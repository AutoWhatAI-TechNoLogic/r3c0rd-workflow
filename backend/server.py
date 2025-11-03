from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
from pymongo import MongoClient
from datetime import datetime, timezone
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
import json
from pinecone import Pinecone, ServerlessSpec

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Configuration
class Config:
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    PINECONE_API_KEY = os.getenv('PINECONE_API_KEY')
    PINECONE_ENVIRONMENT = os.getenv('PINECONE_ENVIRONMENT', 'us-east-1')
    PINECONE_INDEX_NAME = os.getenv('PINECONE_INDEX_NAME', 'workflow-embeddings')
    MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
    DB_NAME = os.getenv('MONGODB_DB_NAME', 'workflow_db')
    COLLECTION_NAME = os.getenv('MONGODB_COLLECTION', 'enhanced_workflows')
    MAX_WORKERS = 4
    MONGODB_TIMEOUT = 5000
    EMBEDDING_MODEL = "text-embedding-3-small"
    EMBEDDING_DIMENSION = 1536

# Initialize clients
openai_client = OpenAI(api_key=Config.OPENAI_API_KEY)
executor = ThreadPoolExecutor(max_workers=Config.MAX_WORKERS)

# MongoDB connection pool (singleton pattern)
class MongoDBManager:
    _instance = None
    _client = None
    _connected = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize MongoDB connection with connection pooling"""
        if self._client is not None:
            return
        
        try:
            print("🔄 Initializing MongoDB connection...")
            self._client = MongoClient(
                Config.MONGODB_URI,
                serverSelectionTimeoutMS=Config.MONGODB_TIMEOUT,
                connectTimeoutMS=30000,
                socketTimeoutMS=30000,
                maxPoolSize=50,
                minPoolSize=10,
                maxIdleTimeMS=45000
            )
            # Verify connection
            self._client.admin.command('ping')
            self._connected = True
            print("✅ MongoDB connection successful")
        except Exception as e:
            print(f"⚠️ MongoDB connection failed: {e}")
            self._connected = False
            self._client = None
    
    def get_collection(self):
        """Get MongoDB collection"""
        if not self._connected or self._client is None:
            self.initialize()
        
        if self._connected and self._client is not None:
            try:
                return self._client[Config.DB_NAME][Config.COLLECTION_NAME]
            except Exception as e:
                print(f"❌ MongoDB collection error: {e}")
                self._connected = False
        
        return None
    
    @property
    def is_connected(self):
        return self._connected

# Pinecone Manager
class PineconeManager:
    _instance = None
    _pc = None
    _index = None
    _connected = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize Pinecone connection"""
        if self._pc is not None:
            return
        
        try:
            print("🔄 Initializing Pinecone connection...")
            self._pc = Pinecone(api_key=Config.PINECONE_API_KEY)
            
            # Check if index exists, create if not
            existing_indexes = [index.name for index in self._pc.list_indexes()]
            
            if Config.PINECONE_INDEX_NAME not in existing_indexes:
                print(f"📝 Creating Pinecone index: {Config.PINECONE_INDEX_NAME}")
                self._pc.create_index(
                    name=Config.PINECONE_INDEX_NAME,
                    dimension=Config.EMBEDDING_DIMENSION,
                    metric='cosine',
                    spec=ServerlessSpec(
                        cloud='aws',
                        region=Config.PINECONE_ENVIRONMENT
                    )
                )
            
            self._index = self._pc.Index(Config.PINECONE_INDEX_NAME)
            self._connected = True
            print("✅ Pinecone connection successful")
        except Exception as e:
            print(f"⚠️ Pinecone connection failed: {e}")
            self._connected = False
            self._index = None
    
    def get_index(self):
        """Get Pinecone index"""
        if not self._connected or self._index is None:
            self.initialize()
        return self._index
    
    @property
    def is_connected(self):
        return self._connected

# Initialize managers
db_manager = MongoDBManager()
pinecone_manager = PineconeManager()

def generate_contextual_content(workflow_data, mongodb_id):
    """
    Generate 500-1000 word contextual content about the workflow using LLM
    """
    steps = workflow_data.get('steps', [])
    
    # Prepare comprehensive workflow information
    workflow_info = {
        'name': workflow_data.get('name', 'Recorded Workflow'),
        'description': workflow_data.get('description', ''),
        'workflow_analysis': workflow_data.get('workflow_analysis', ''),
        'step_count': len(steps),
        'steps': []
    }
    
    # Include detailed step information
    for i, step in enumerate(steps, 1):
        step_info = {
            'step_number': i,
            'type': step.get('type'),
            'description': step.get('description', '')
        }
        
        if step.get('type') == 'navigation':
            step_info['url'] = step.get('url')
        elif step.get('type') == 'click':
            step_info['target'] = step.get('targetText') or step.get('elementText')
        elif step.get('type') == 'input':
            step_info['value'] = step.get('value')
            step_info['field'] = step.get('targetText')
        elif step.get('type') == 'extract':
            step_info['extraction_goal'] = step.get('extractionGoal')
        
        workflow_info['steps'].append(step_info)
    
        prompt = f"""You are analyzing a browser automation workflow. Write a comprehensive 50-100 word plain text document about this workflow that would help someone understand and search for it.

        Workflow Information:
        Name: {workflow_info['name']}
        Description: {workflow_info['description']}
        Analysis: {workflow_info['workflow_analysis']}
        Total Steps: {workflow_info['step_count']}

        Steps:
        {chr(10).join([f"Step {s['step_number']}: {s.get('description', '')} (Type: {s['type']})" for s in workflow_info['steps']])}

        Write a detailed, flowing narrative (500-1000 words) covering:
        - What this workflow does and its purpose
        - The sequence of actions and why each step matters
        - Common use cases and scenarios where this would be useful
        - Technical aspects and patterns used
        - Potential variations or extensions
        - Key concepts and terminology

        CRITICAL: Write ONLY in plain text paragraphs. DO NOT use:
        - Markdown formatting (**, __, ##, etc.)
        - Bullet points or numbered lists
        - Special characters or symbols
        - Section headers
        - Any formatting whatsoever

        Output should be continuous flowing text in natural paragraphs only."""

    try:
        print(f"🤖 Generating contextual content for workflow {mongodb_id}...")
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",  # gpt-5-nano doesn't exist, use gpt-4o-mini or gpt-3.5-turbo
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert technical writer specializing in browser automation. Write comprehensive, natural-flowing plain text content. Output ONLY plain text paragraphs with NO formatting, NO markdown, NO bullet points, NO numbered lists, and NO special characters."
                },
                {"role": "user", "content": prompt}
            ],
            timeout=60
        )
        
        contextual_content = response.choices[0].message.content
        word_count = len(contextual_content.split())
        print(f"✅ Generated contextual content: {word_count} words")
        
        return contextual_content
    
    except Exception as e:
        print(f"⚠️ Error generating contextual content: {e}")
        # Fallback content
        return f"""This is an automated browser workflow named {workflow_info['name']} containing {len(steps)} sequential steps. The workflow demonstrates browser automation patterns for accomplishing specific tasks through programmatic web interaction. It includes navigation between pages, user input simulation, element interactions, and data extraction operations. This type of workflow is commonly used for task automation, testing, data collection, and repetitive process optimization. The sequence of steps has been carefully designed to replicate human interactions while maintaining reliability and efficiency. Each step plays a crucial role in achieving the overall objective of the automation."""

def generate_embedding(text):
    """
    Generate embedding for text using OpenAI's embedding model
    """
    try:
        print(f"🔢 Generating embedding for text ({len(text)} chars)...")
        response = openai_client.embeddings.create(
            model=Config.EMBEDDING_MODEL,
            input=text
        )
        embedding = response.data[0].embedding
        print(f"✅ Embedding generated: {len(embedding)} dimensions")
        return embedding
    except Exception as e:
        print(f"⚠️ Error generating embedding: {e}")
        return None

def store_in_pinecone(mongodb_id, contextual_content):
    """
    Store workflow embedding in Pinecone with minimal metadata
    """
    try:
        index = pinecone_manager.get_index()
        if index is None:
            print("⚠️ Pinecone not available - skipping vector storage")
            return False
        
        # Generate embedding
        embedding = generate_embedding(contextual_content)
        if embedding is None:
            return False

        # Minimal metadata - only MongoDB ID
        metadata = {
            'mongodb_id': mongodb_id,
            'vectorized_at': datetime.now(timezone.utc).isoformat(),
            'contextual_content': contextual_content
        }
        
        # Upsert to Pinecone
        print(f"📤 Storing vector in Pinecone with ID: {mongodb_id}")
        index.upsert(
            vectors=[
                {
                    'id': mongodb_id,
                    'values': embedding,
                    'metadata': metadata
                }
            ]
        )
        print(f"✅ Successfully stored in Pinecone: {mongodb_id}")
        return True
    
    except Exception as e:
        print(f"❌ Error storing in Pinecone: {e}")
        import traceback
        traceback.print_exc()
        return False

def process_workflow_vectorization(workflow_data, mongodb_id):
    """
    Process workflow for vectorization: generate content, embed, and store in Pinecone
    """
    def process():
        try:
            print(f"🔄 Starting vectorization for workflow: {mongodb_id}")
            
            # Generate contextual content
            contextual_content = generate_contextual_content(workflow_data, mongodb_id)
            
            # Store in Pinecone (only mongodb_id and contextual content)
            success = store_in_pinecone(mongodb_id, contextual_content)
            
            return success
        except Exception as e:
            print(f"❌ Error in vectorization process: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Submit to thread pool
    executor.submit(process)

def save_workflow_async(workflow_data):
    """Save workflow to MongoDB asynchronously using thread pool"""
    def save():
        try:
            collection = db_manager.get_collection()
            if collection is None:
                print("⚠️ MongoDB not available - skipping save")
                return None
            
            mongodb_id = str(uuid.uuid4())
            enhanced_workflow = {
                '_id': mongodb_id,
                **workflow_data,
                'metadata': {
                    'enhanced_at': datetime.now(timezone.utc).isoformat(),
                    'step_count': len(workflow_data.get('steps', [])),
                    'version': '1.0'
                }
            }
            
            collection.insert_one(enhanced_workflow)
            print(f"✅ Workflow saved to MongoDB: {mongodb_id}")
            
            # Trigger vectorization process
            process_workflow_vectorization(workflow_data, mongodb_id)
            
            return mongodb_id
        except Exception as e:
            print(f"❌ Error saving to MongoDB: {e}")
            return None
    
    return executor.submit(save)

def enhance_workflow_with_ai(workflow_data):
    """
    Enhance entire workflow with AI in a single call.
    Generates workflow_analysis, description, and step descriptions.
    """
    steps = workflow_data.get('steps', [])
    
    if not steps:
        return {
            'workflow_analysis': 'Empty workflow',
            'description': 'No steps recorded',
            'step_descriptions': []
        }
    
    # Prepare workflow summary for the prompt
    workflow_summary = {
        'name': workflow_data.get('name', 'Recorded Workflow'),
        'steps': []
    }
    
    # Create a simplified view of steps for the LLM
    for i, step in enumerate(steps, 1):
        step_info = {
            'step_number': i,
            'type': step.get('type'),
        }
        
        if step.get('type') == 'navigation':
            step_info['url'] = step.get('url')
        elif step.get('type') == 'click':
            step_info['target'] = step.get('targetText') or step.get('elementText') or step.get('cssSelector', 'element')
        elif step.get('type') == 'input':
            step_info['value'] = step.get('value')
            step_info['target'] = step.get('targetText') or step.get('cssSelector', 'field')
        elif step.get('type') == 'key_press':
            step_info['key'] = step.get('key')
        elif step.get('type') == 'scroll':
            step_info['scrollX'] = step.get('scrollX')
            step_info['scrollY'] = step.get('scrollY')
        elif step.get('type') == 'extract':
            step_info['extractionGoal'] = step.get('extractionGoal')
        
        workflow_summary['steps'].append(step_info)
    
    # Create the prompt
    prompt = f"""Analyze this browser automation workflow and provide comprehensive descriptions.

Workflow Data:
{json.dumps(workflow_summary, indent=2)}

Please provide your response in the following JSON format:
{{
  "workflow_analysis": "6-7 lines analyzing what this workflow does, its purpose, any dynamic elements, potential input parameters needed, and key observations about the workflow structure",
  "description": "3-4 lines describing the overall workflow in a user-friendly way - what task it accomplishes and the main steps involved",
  "step_descriptions": [
    "Description for step 1 explaining what this step does",
    "Description for step 2 explaining what this step does",
    ...
  ]
}}

Guidelines:
- workflow_analysis: Provide technical analysis (6-7 lines) covering the workflow's purpose, dynamic vs static elements, required inputs, and structure
- description: Write a clear, concise overview (3-4 lines) that explains what the workflow accomplishes
- step_descriptions: For each step, write a clear one-sentence description of what that step does (e.g., "Navigate to Google homepage", "Enter search term into Google's search box", "Click on the specified article link")

Return ONLY valid JSON, no additional text."""

    try:
        print("🤖 Sending workflow to OpenAI for complete enhancement...")
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system", 
                    "content": "You are an expert at analyzing browser automation workflows. Provide detailed, accurate descriptions in JSON format."
                },
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            timeout=30
        )
        
        # Extract content from the response object
        content = response.choices[0].message.content
        print(f"📝 Raw AI response content length: {len(content)} chars")
        
        # Parse the AI response
        ai_response = json.loads(content)
        
        workflow_analysis = ai_response.get('workflow_analysis', f"Automated workflow with {len(steps)} steps")
        description = ai_response.get('description', f"Recorded on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
        step_descriptions = ai_response.get('step_descriptions', [])
        
        print(f"✅ AI enhancement successful")
        print(f"   - Workflow Analysis: {len(workflow_analysis)} chars")
        print(f"   - Description: {len(description)} chars")
        print(f"   - Step Descriptions: {len(step_descriptions)} items")
        
        return {
            'workflow_analysis': workflow_analysis,
            'description': description,
            'step_descriptions': step_descriptions
        }
    
    except json.JSONDecodeError as e:
        print(f"⚠️ Failed to parse AI response as JSON: {e}")
        print(f"   Response content was: {content if 'content' in locals() else 'N/A'}")
        return generate_fallback_descriptions(steps)
    except Exception as e:
        print(f"⚠️ OpenAI error: {e}")
        import traceback
        traceback.print_exc()
        return generate_fallback_descriptions(steps)

def generate_fallback_descriptions(steps):
    """Generate basic descriptions as fallback when AI fails"""
    step_descriptions = []
    for step in steps:
        step_type = step.get('type', 'unknown')
        
        if step_type == 'navigation':
            desc = f"Navigate to {step.get('url', 'URL')}"
        elif step_type == 'click':
            target = step.get('targetText') or step.get('elementText') or 'element'
            desc = f"Click on {target}"
        elif step_type == 'input':
            value = step.get('value', '')
            target = step.get('targetText') or 'field'
            desc = f"Enter '{value}' in {target}"
        elif step_type == 'key_press':
            desc = f"Press {step.get('key', 'key')} key"
        elif step_type == 'scroll':
            desc = "Scroll page"
        elif step_type == 'extract':
            desc = f"Extract: {step.get('extractionGoal', 'information')}"
        else:
            desc = f"Perform {step_type} action"
        
        step_descriptions.append(desc)
    
    return {
        'workflow_analysis': f"Automated browser workflow with {len(steps)} steps. The workflow includes navigation, user interactions, and data operations.",
        'description': f"Recorded workflow containing {len(steps)} automated steps. Recorded on {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.",
        'step_descriptions': step_descriptions
    }

@app.route('/enhance-workflow', methods=['POST'])
def enhance_workflow():
    """Enhance workflow with AI descriptions"""
    try:
        workflow_data = request.get_json()
        
        if not workflow_data:
            return jsonify({'error': 'No workflow data provided'}), 400
        
        steps = workflow_data.get('steps', [])
        print(f"📥 Received workflow with {len(steps)} steps")
        
        # Get AI enhancements in a single call
        ai_enhancements = enhance_workflow_with_ai(workflow_data)
        
        # Apply step descriptions to steps
        enhanced_steps = []
        step_descriptions = ai_enhancements.get('step_descriptions', [])
        
        for i, step in enumerate(steps):
            enhanced_step = {**step}
            if i < len(step_descriptions):
                enhanced_step['description'] = step_descriptions[i]
            else:
                # Fallback if AI didn't provide enough descriptions
                enhanced_step['description'] = f"Step {i+1}"
            enhanced_steps.append(enhanced_step)
        
        # Create enhanced workflow with all AI-generated content
        enhanced_workflow = {
            **workflow_data,
            'workflow_analysis': ai_enhancements.get('workflow_analysis'),
            'description': ai_enhancements.get('description'),
            'steps': enhanced_steps
        }
        
        # Save asynchronously (non-blocking) - this will also trigger vectorization
        future = save_workflow_async(enhanced_workflow)
        
        print(f"✅ Successfully enhanced workflow with {len(enhanced_steps)} steps")
        print(f"🔄 Vectorization process queued")
        
        return jsonify({
            **enhanced_workflow,
            'mongodb_status': 'queued',
            'vectorization_status': 'queued'
        })
    
    except Exception as e:
        print(f"❌ Error in enhance_workflow: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/search-workflows', methods=['POST'])
def search_workflows():
    """Search workflows using semantic similarity"""
    try:
        data = request.get_json()
        query = data.get('query', '')
        top_k = min(data.get('top_k', 10), 50)
        
        if not query:
            return jsonify({'error': 'No search query provided'}), 400
        
        # Generate embedding for search query
        query_embedding = generate_embedding(query)
        if query_embedding is None:
            return jsonify({'error': 'Failed to generate query embedding'}), 500
        
        # Search in Pinecone
        index = pinecone_manager.get_index()
        if index is None:
            return jsonify({'error': 'Pinecone not available'}), 503
        
        results = index.query(
            vector=query_embedding,
            top_k=top_k,
            include_metadata=True
        )
        
        # Fetch full workflow data from MongoDB
        collection = db_manager.get_collection()
        workflows = []
        
        for match in results.matches:
            mongodb_id = match.metadata.get('mongodb_id')
            if mongodb_id and collection:
                workflow = collection.find_one({'_id': mongodb_id})
                if workflow:
                    workflow['_id'] = str(workflow['_id'])
                    workflow['similarity_score'] = match.score
                    workflows.append(workflow)
        
        return jsonify({
            'query': query,
            'results': workflows,
            'count': len(workflows)
        })
    
    except Exception as e:
        print(f"❌ Error in search_workflows: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'mongodb': 'connected' if db_manager.is_connected else 'disconnected',
        'pinecone': 'connected' if pinecone_manager.is_connected else 'disconnected',
        'openai': 'configured' if Config.OPENAI_API_KEY else 'missing',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

@app.route('/workflows', methods=['GET'])
def get_workflows():
    """Get all workflows with pagination"""
    try:
        collection = db_manager.get_collection()
        if collection is None:
            return jsonify({'error': 'MongoDB not available'}), 503
        
        # Pagination parameters
        page = request.args.get('page', 1, type=int)
        limit = min(request.args.get('limit', 50, type=int), 100)
        skip = (page - 1) * limit
        
        # Efficient query with projection
        workflows = list(collection.find(
            {},
            {'_id': 1, 'name': 1, 'description': 1, 'metadata': 1, 'vectorized': 1}
        ).skip(skip).limit(limit))
        
        # Convert ObjectId to string
        for workflow in workflows:
            workflow['_id'] = str(workflow['_id'])
        
        total = collection.count_documents({})
        
        return jsonify({
            'workflows': workflows,
            'count': len(workflows),
            'total': total,
            'page': page,
            'pages': (total + limit - 1) // limit
        })
    
    except Exception as e:
        print(f"❌ Error in get_workflows: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/workflows/<workflow_id>', methods=['GET'])
def get_workflow(workflow_id):
    """Get specific workflow by ID"""
    try:
        collection = db_manager.get_collection()
        if collection is None:
            return jsonify({'error': 'MongoDB not available'}), 503
        
        workflow = collection.find_one({'_id': workflow_id})
        
        if not workflow:
            return jsonify({'error': 'Workflow not found'}), 404
        
        workflow['_id'] = str(workflow['_id'])
        return jsonify(workflow)
    
    except Exception as e:
        print(f"❌ Error in get_workflow: {e}")
        return jsonify({'error': str(e)}), 500

@app.before_request
def initialize_connections():
    """Initialize connections on first request"""
    executor.submit(db_manager.initialize)
    executor.submit(pinecone_manager.initialize)

@app.teardown_appcontext
def cleanup(error=None):
    """Cleanup resources"""
    pass  # Connection pool handles cleanup

if __name__ == '__main__':
    print("🚀 Starting Flask server...")
    print(f"📁 Database: {Config.DB_NAME}")
    print(f"📊 Collection: {Config.COLLECTION_NAME}")
    print(f"✅ OpenAI: {'Configured' if Config.OPENAI_API_KEY else 'Missing'}")
    print(f"🌲 Pinecone: {'Configured' if Config.PINECONE_API_KEY else 'Missing'}")
    
    # Initialize connections in background
    executor.submit(db_manager.initialize)
    executor.submit(pinecone_manager.initialize)

    app.run(host='127.0.0.1', debug=True, port=5000, threaded=True)