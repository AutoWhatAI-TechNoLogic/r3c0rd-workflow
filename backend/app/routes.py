from flask import Blueprint, request, jsonify
from app.services import (
    enhance_workflow_with_ai, 
    save_workflow_to_db, 
    process_workflow_vectorization,
    generate_embedding
)
from app.db import get_db_collection
from app.vector_db import pinecone_manager
from app.utils import executor
from app.config import Config
from datetime import datetime, timezone
import logging
import traceback
from typing import Optional

# --- NEW: Import the self-healing executor ---
from app.workflow_executor import PlaywrightWorkflowExecutor

main_bp = Blueprint('main', __name__)

@main_bp.route('/enhance-workflow', methods=['POST'])
def enhance_workflow():
    """Enhance workflow, save to DB, and queue vectorization"""
    try:
        workflow_data = request.get_json()
        if not workflow_data:
            return jsonify({'error': 'No workflow data provided'}), 400
        
        steps = workflow_data.get('steps', [])
        logging.info(f"📥 Received workflow with {len(steps)} steps")
        
        # 1. Enhance with AI (now returns 'requires_password')
        ai_enhancements = enhance_workflow_with_ai(workflow_data)
        
        # 2. Save to MongoDB (this function now handles applying descriptions)
        # We pass the original data + AI enhancements
        all_workflow_data = {**workflow_data, **ai_enhancements}
        saved_workflow, mongodb_id = save_workflow_to_db(all_workflow_data)
        
        if not mongodb_id:
            return jsonify({'error': 'Failed to save workflow to database'}), 500
        
        # 3. Queue vectorization (Asynchronously)
        # 'saved_workflow' is the full document from the DB
        executor.submit(process_workflow_vectorization, saved_workflow, mongodb_id)
        
        logging.info(f"✅ Successfully enhanced workflow. Queued for vectorization: {mongodb_id}")
        
        return jsonify({
            **saved_workflow,
            'mongodb_status': 'saved',
            'vectorization_status': 'queued'
        })
    
    except Exception as e:
        logging.error(f"❌ Error in enhance_workflow: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# --- THIS IS THE FIX ---
# Your server is likely missing methods=['POST']
@main_bp.route('/search-workflows', methods=['POST'])
def search_workflows():
    """
    Search workflows using HYBRID search (semantic + keyword).
    """
    try:
        data = request.get_json()
        query = data.get('query', '')
        top_k = min(data.get('top_k', 10), 50)
        
        if not query:
            return jsonify({'error': 'No search query provided'}), 400

        # --- Parallel Search Functions ---

        def task_semantic_search(query, top_k):
            """Task for semantic search in Pinecone."""
            try:
                MIN_SEMANTIC_SCORE = 0.3  # Filters out weak "concept" matches
                
                query_embedding = generate_embedding(query)
                if query_embedding is None:
                    logging.warning("Failed to generate query embedding for semantic search.")
                    return []
                
                index = pinecone_manager.get_index()
                if index is None:
                    logging.warning("Pinecone not available for semantic search.")
                    return []
                
                results = index.query(
                    vector=query_embedding,
                    top_k=top_k,
                    include_metadata=False
                )
                
                filtered_matches = [
                    m for m in results.matches if m.score >= MIN_SEMANTIC_SCORE
                ]
                logging.info(f"DEBUG: Semantic search found {len(results.matches)} matches, returning {len(filtered_matches)} after threshold.")
                return filtered_matches

            except Exception as e:
                logging.error(f"Error in semantic search task: {e}")
                return []

        def task_keyword_search(query, top_k):
            """Task for keyword text search in MongoDB."""
            try:
                with get_db_collection() as collection:
                    if collection is None:
                        logging.warning("MongoDB not available for keyword search.")
                        return []
                    
                    logging.info(f"DEBUG: Running keyword search for query: '{query}'")
                    
                    cursor = collection.find(
                        { "$text": { "$search": query } },
                        { "score": { "$meta": "textScore" } }
                    ).limit(top_k)
                    
                    results = list(cursor)
                    logging.info(f"DEBUG: Keyword search found {len(results)} results.")
                    return results
            except Exception as e:
                logging.error(f"❌ CRITICAL ERROR in keyword search task: {e}")
                import traceback
                traceback.print_exc() 
                return []

        # --- Execute searches in parallel ---
        future_semantic = executor.submit(task_semantic_search, query, top_k)
        future_keyword = executor.submit(task_keyword_search, query, top_k)

        pinecone_matches = future_semantic.result()
        mongo_docs = future_keyword.result()

        # --- Merge & Re-rank Results ---
        final_results = {}
        
        for doc in mongo_docs:
            doc_id = str(doc['_id'])
            doc['keyword_score'] = doc.get('score', 0)
            doc['semantic_score'] = 0.0
            final_results[doc_id] = doc

        pinecone_ids = [match.id for match in pinecone_matches]
        pinecone_scores = {match.id: match.score for match in pinecone_matches}
        
        ids_to_fetch = [pid for pid in pinecone_ids if pid not in final_results]
        
        if ids_to_fetch:
            with get_db_collection() as collection:
                if collection is not None:
                    semantic_docs = list(collection.find({ "_id": { "$in": ids_to_fetch } }))
                    for doc in semantic_docs:
                        doc_id = str(doc['_id'])
                        doc['keyword_score'] = 0.0
                        final_results[doc_id] = doc
        
        for doc_id, doc in final_results.items():
            if doc_id in pinecone_scores:
                doc['semantic_score'] = pinecone_scores[doc_id]

        # --- Calculate hybrid score ---
        ranked_list = []
        for _id, doc in final_results.items():
            
            semantic_weight = 0.8
            keyword_weight = 5.0 # Keywords are very important
            
            hybrid_score = (doc['semantic_score'] * semantic_weight) + (doc['keyword_score'] * keyword_weight)
            
            doc['_id'] = str(doc['_id'])
            doc['similarity_score'] = doc['semantic_score']
            doc['hybrid_score'] = hybrid_score
            ranked_list.append(doc)

        ranked_list.sort(key=lambda x: x['hybrid_score'], reverse=True)

        return jsonify({
            'query': query,
            'results': ranked_list[:top_k],
            'count': len(ranked_list[:top_k])
        })
    
    except Exception as e:
        logging.error(f"❌ Error in hybrid search_workflows: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@main_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    mongo_status = 'disconnected'
    try:
        with get_db_collection() as collection:
            if collection is not None:
                mongo_status = 'connected'
    except Exception:
        pass

    return jsonify({
        'status': 'healthy',
        'mongodb': mongo_status,
        'pinecone': 'connected' if pinecone_manager.is_connected else 'disconnected',
        'openai': 'configured' if Config.OPENAI_API_KEY else 'missing',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

@main_bp.route('/workflows', methods=['GET'])
def get_workflows():
    """Get all workflows with pagination"""
    try:
        page = request.args.get('page', 1, type=int)
        limit = min(request.args.get('limit', 50, type=int), 100)
        skip = (page - 1) * limit

        with get_db_collection() as collection:
            if collection is None:
                return jsonify({'error': 'MongoDB not available'}), 503
            
            workflows = list(collection.find(
                {},
                # Project only the fields needed for the list view
                {
                    '_id': 1, 
                    'name': 1, 
                    'description': 1, 
                    'metadata': 1, 
                    'requires_password': 1 
                }
            ).skip(skip).limit(limit))
            
            total = collection.count_documents({})

        for workflow in workflows:
            workflow['_id'] = str(workflow['_id'])
        
        return jsonify({
            'workflows': workflows,
            'count': len(workflows),
            'total': total,
            'page': page,
            'pages': (total + limit - 1) // limit
        })
    
    except Exception as e:
        logging.error(f"❌ Error in get_workflows: {e}")
        return jsonify({'error': str(e)}), 500

@main_bp.route('/workflows/<workflow_id>', methods=['GET'])
def get_workflow(workflow_id):
    """Get specific workflow by ID"""
    try:
        with get_db_collection() as collection:
            if collection is None:
                return jsonify({'error': 'MongoDB not available'}), 503
            
            workflow = collection.find_one({'_id': workflow_id})
        
        if not workflow:
            return jsonify({'error': 'Workflow not found'}), 404
        
        workflow['_id'] = str(workflow['_id'])
        return jsonify(workflow)
    
    except Exception as e:
        logging.error(f"❌ Error in get_workflow: {e}")
        return jsonify({'error': str(e)}), 500

# --- UPDATED: Background task to run workflow ---
def task_run_workflow(workflow_id: str, password: Optional[str] = None):
    """
    Background task to run a workflow.
    Now accepts a password to pass to the executor.
    """
    executor = None
    try:
        logging.info(f"🔄 [Async] Task started for workflow: {workflow_id}")
        
        # 1. Fetch workflow from MongoDB
        workflow_data = None
        with get_db_collection() as collection:
            if collection is not None:
                workflow_data = collection.find_one({'_id': workflow_id})
        
        if not workflow_data:
            logging.error(f"❌ [Async] Workflow {workflow_id} not found in DB.")
            return

        # 2. Initialize executor with all info
        executor = PlaywrightWorkflowExecutor(
            headless=False, 
            keep_open=True,
            mongodb_id=workflow_id,
            password=password  # <-- Pass the real password here
        )
        
        # 3. Run the workflow
        # The executor's own logic will handle healing and retries
        executor.execute_workflow(workflow_data, workflow_name=workflow_data.get('name', 'Unknown'))
        
        logging.info(f"✅ [Async] Task finished for workflow: {workflow_id}")
        
    except Exception as e:
        logging.error(f"❌ [Async] Task failed for workflow {workflow_id}: {e}")
        traceback.print_exc()
        if executor:
            executor.close()

# --- UPDATED: API endpoint to run workflow ---
@main_bp.route('/run-workflow', methods=['POST'])
def run_workflow():
    """API endpoint to trigger a workflow run"""
    try:
        data = request.get_json()
        workflow_id = data.get('workflow_id')
        password = data.get('password') # <-- Get the password from the request
        
        if not workflow_id:
            return jsonify({'error': 'No workflow_id provided'}), 400
        
        # Submit the task to the thread pool with the password
        executor.submit(task_run_workflow, workflow_id, password)
        
        return jsonify({
            'status': 'queued',
            'workflow_id': workflow_id,
            'message': 'Workflow execution has been started.'
        })
    except Exception as e:
        logging.error(f"❌ Error in /run-workflow: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

