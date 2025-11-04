from openai import OpenAI
from app.config import Config
from app.vector_db import pinecone_manager
from app.db import get_db_collection
from app.utils import executor
from datetime import datetime, timezone
import uuid
import json
import logging
import traceback
import re
from bs4 import BeautifulSoup

# Initialize OpenAI client
# This will fail if OPENAI_API_KEY is not in your .env file
try:
    openai_client = OpenAI(api_key=Config.OPENAI_API_KEY)
except Exception as e:
    logging.critical(f"❌ FAILED TO INITIALIZE OPENAI CLIENT: {e}")
    logging.critical("   Please ensure OPENAI_API_KEY is set in your .env file.")
    raise

# --- NEW: Text Extraction Feature ---

def extract_data_with_llm(html_content: str, extraction_goal: str, source_url: str) -> dict:
    """
    Extract specific data from HTML content using LLM.
    
    Args:
        html_content: Full HTML content of the page
        extraction_goal: Description of what data to extract
        source_url: URL of the source page
    
    Returns:
        Dictionary containing extracted data in the format:
        {"extracted_data": "content from the website"}
    """
    try:
        # Clean and parse HTML to extract meaningful text
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Get text content
        text_content = soup.get_text(separator='\n', strip=True)
        
        # Clean up whitespace
        lines = (line.strip() for line in text_content.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        cleaned_text = '\n'.join(chunk for chunk in chunks if chunk)
        
        # Limit text size to avoid token limits (keep first 15000 characters)
        if len(cleaned_text) > 15000:
            cleaned_text = cleaned_text[:15000] + "\n...[content truncated]"
        
        logging.info(f"🔍 Extracting data with goal: {extraction_goal}")
        logging.info(f"📄 Cleaned text length: {len(cleaned_text)} characters")
        
        # Create prompt for LLM
        prompt = f"""You are a precise data extraction assistant. Your task is to extract specific information from website content based on the user's request.

SOURCE URL: {source_url}

EXTRACTION GOAL: {extraction_goal}

WEBSITE CONTENT:
{cleaned_text}

INSTRUCTIONS:
1. Carefully read the website content above
2. Extract ONLY the information that directly relates to the extraction goal
3. Use ONLY text that appears in the website content - do not invent, paraphrase, or add information
4. If the requested information is not found, return an empty string for that field
5. Be precise and extract the exact text from the website
6. Return your response as a JSON object

RESPONSE FORMAT:
{{
    "extracted_data": "the exact text content from the website that matches the extraction goal"
}}

If multiple pieces of information are requested in the extraction goal, you may use multiple keys like:
{{
    "field_1": "extracted content 1",
    "field_2": "extracted content 2"
}}

Extract the data now:"""

        # Call OpenAI API
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system", 
                    "content": "You are a precise data extraction assistant. Extract only the exact information requested from the provided website content. Return results in valid JSON format. Never invent or paraphrase - only use text that appears in the source."
                },
                {
                    "role": "user", 
                    "content": prompt
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.1,  # Low temperature for precise extraction
            timeout=60
        )
        
        # Parse the response
        content = response.choices[0].message.content
        extracted_data = json.loads(content)
        
        logging.info(f"✅ Data extracted successfully")
        logging.info(f"📊 Extracted fields: {list(extracted_data.keys())}")
        
        return extracted_data
    
    except json.JSONDecodeError as e:
        logging.error(f"❌ Failed to parse LLM response as JSON: {e}")
        return {"error": "Failed to parse extraction results", "raw_response": content if 'content' in locals() else "No response"}
    
    except Exception as e:
        logging.error(f"❌ Error during data extraction: {e}")
        traceback.print_exc()
        return {"error": str(e)}


# --- AI Enhancement Logic ---

def enhance_workflow_with_ai(workflow_data):
    """
    Enhance entire workflow with AI in a single call.
    Generates analysis, description, step descriptions, and checks for passwords.
    """
    steps = workflow_data.get('steps', [])
    
    if not steps:
        return generate_fallback_descriptions(steps)
    
    workflow_summary = {
        # --- MODIFIED: Pass the original name (e.g., "Recorded Workflow") ---
        'name': workflow_data.get('name', 'Recorded Workflow'), 
        'steps': []
    }
    
    for i, step in enumerate(steps, 1):
        step_info = { 'step_number': i, 'type': step.get('type') }
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
    
    # --- PROMPT UPDATED TO GENERATE A NAME ---
    prompt = f"""Analyze this browser automation workflow and provide comprehensive descriptions.

    Workflow Data:
    {json.dumps(workflow_summary, indent=2)}

    Please provide your response in the following JSON format:
    {{
      "name": "Generate a clear, concise workflow name in Title Case that includes the main action, platform (if mentioned), and the specific product, search term, or noun from the description (e.g., 'Amazon Chair Search Workflow', 'LinkedIn Job Application Automation in Company Name', 'Wikipedia search for RPA').",
      "workflow_analysis": "6-7 lines analyzing what this workflow does, its purpose, any dynamic elements, potential input parameters needed, and key observations about the workflow structure",
      "description": "3-4 lines describing the overall workflow in a user-friendly way - what task it accomplishes and the main steps involved",
      "step_descriptions": [
        "Description for step 1 explaining what this step does",
        "Description for step 2 explaining what this step does",
        ...
      ],
      "requires_password": true_or_false
    }}

    Guidelines:
    - name: Based on the steps, create a concise, human-readable name for the workflow. Use 5-10 words. This is the primary title.
    - workflow_analysis: Provide technical analysis (6-7 lines) covering the workflow's purpose, dynamic vs static elements, required inputs, and structure
    - description: Write a clear, concise overview (3-4 lines) that explains what the workflow accomplishes
    - step_descriptions: For each step, write a clear one-sentence description of what that step does
    - requires_password: Analyze the steps. If any step involves an input into a field with 'password' in its name, id, or description, set this to true. Otherwise, set it to false.

    Return ONLY valid JSON, no additional text."""

    try:
        logging.info("🤖 Sending workflow to OpenAI for complete enhancement...")
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert at analyzing browser automation workflows. Provide detailed, accurate descriptions in JSON format."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            timeout=30
        )
        
        content = response.choices[0].message.content
        ai_response = json.loads(content)
        
        logging.info(f"✅ AI enhancement successful")
        
        # --- MODIFIED: Return all AI-generated fields, including the new name ---
        return {
            'name': ai_response.get('name', f"AI Workflow {datetime.now(timezone.utc).strftime('%Y-%m-%d')}") , # <-- NEW
            'workflow_analysis': ai_response.get('workflow_analysis', f"Automated workflow with {len(steps)} steps"),
            'description': ai_response.get('description', f"Recorded on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}") ,
            'step_descriptions': ai_response.get('step_descriptions', []),
            'requires_password': ai_response.get('requires_password', False)
        }
    
    except json.JSONDecodeError as e:
        logging.warning(f"⚠️ Failed to parse AI response as JSON: {e}")
        return generate_fallback_descriptions(steps)
    except Exception as e:
        logging.warning(f"⚠️ OpenAI error: {e}")
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
        elif step_type == 'extract':
            desc = f"Extract data: {step.get('extractionGoal', 'information from page')}"
        else:
            desc = f"Perform {step_type} action"
        step_descriptions.append(desc)
    
    return {
        'name': 'Recorded Workflow (Fallback)', # <-- NEW
        'workflow_analysis': f"Automated browser workflow with {len(steps)} steps.",
        'description': f"Recorded workflow containing {len(steps)} automated steps.",
        'step_descriptions': step_descriptions,
        'requires_password': False # Default to false on fallback
    }

# --- Vectorization & Storage Logic ---

def generate_contextual_content(workflow_data, mongodb_id):
    """
    Generate a semantically dense document (for vector embedding) 
    about the workflow.
    """
    steps = workflow_data.get('steps', [])
    
    workflow_info = {
        # --- MODIFIED: Use the new AI-generated name ---
        'name': workflow_data.get('name', 'Recorded Workflow'), 
        'description': workflow_data.get('description', ''),
        'workflow_analysis': workflow_data.get('workflow_analysis', ''),
        'step_count': len(steps),
        'step_details': []
    }
    
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
            step_info['extractionGoal'] = step.get('extractionGoal')
        workflow_info['step_details'].append(step_info)
    
    step_summary_for_prompt = json.dumps(workflow_info['step_details'], indent=2)

    prompt = f"""You are an expert technical writer creating a semantic document for a vector search index (like Pinecone). Your task is to generate a dense, plain-text summary of the workflow provided. This document will be embedded and used to find the workflow via semantic search.

Workflow Information:
Name: {workflow_info['name']}
AI-Generated Description: {workflow_info['description']}
AI-Generated Analysis: {workflow_info['workflow_analysis']}

Workflow Steps (JSON Summary):
{step_summary_for_prompt}

INSTRUCTIONS:
1.  **Synthesize, Don't List:** Do not just list the steps. Synthesize the workflow's overall *purpose*, *goal*, and *actions* into a coherent text.
2.  **Focus on Concepts & Keywords:** Extract and embed key concepts, actions, and entities. For example, if it's logging in, use terms like "login," "sign-in," "authentication," "username," "password," "access account," "credentials."
3.  **Anticipate Search Queries:** Think about what a user would type to find this. Include alternative phrasings and related terms. (e.g., "scrape data" and "extract information", "buy item" and "checkout process", "sign up" and "create account").
4.  **Mention Key Entities:** Identify and name important websites, applications, or services involved (e.g., "Google," "Amazon," "a checkout page," "user profile dashboard").
5.  **Describe the Process:** Briefly summarize the sequence (e.g., "This workflow navigates to a login page, fills in user credentials, and then scrapes the user's profile data from the dashboard.").
6.  **Extract Key Entities:** Identify and repeat the primary subjects, company names, or proper nouns. If the workflow is about "BTS" on "Wikipedia," those exact words should be prominent in the text.

OUTPUT REQUIREMENTS:
-   **Format:** A single, continuous block of plain text.
-   **Length:** 150-300 words. This should be a dense, keyword-rich summary.
-   **CRITICAL: DO NOT USE** any markdown (**, ##), bullet points, numbered lists, headers, or special formatting.
-   **Tone:** Professional and descriptive.

Generate the semantic document now:"""

    try:
        logging.info(f"🤖 Generating semantic content for workflow {mongodb_id}...")
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert technical writer optimizing content for a vector search database. Your output must be a single, dense, plain-text block with NO markdown or formatting."},
                {"role": "user", "content": prompt}
            ],
            timeout=90
        )
        
        raw_content = response.choices[0].message.content
        
        # Programmatically remove any markdown-like characters
        plain_text = re.sub(r'[\*_`#]', '', raw_content)
        plain_text = re.sub(r'^\s*[-*]\s+', '', plain_text, flags=re.MULTILINE)
        plain_text = re.sub(r'^\s*\d+\.\s+', '', plain_text, flags=re.MULTILINE)
        contextual_content = re.sub(r'\n{2,}', '\n', plain_text).strip()
        
        word_count = len(contextual_content.split())
        logging.info(f"✅ Generated semantic content: {word_count} words (markdown stripped)")
        
        return contextual_content
    
    except Exception as e:
        logging.warning(f"⚠️ Error generating semantic content: {e}")
        # --- MODIFIED: Use the AI-generated name in the fallback ---
        return f"This is an automated browser workflow named {workflow_info['name']} containing {len(steps)} sequential steps."

def generate_embedding(text):
    """
    Generate embedding for text using OpenAI's embedding model
    """
    try:
        logging.info(f"🔢 Generating embedding for text ({len(text)} chars)...")
        response = openai_client.embeddings.create(
            model=Config.EMBEDDING_MODEL,
            input=text
        )
        embedding = response.data[0].embedding
        logging.info(f"✅ Embedding generated: {len(embedding)} dimensions")
        return embedding
    except Exception as e:
        logging.error(f"⚠️ Error generating embedding: {e}")
        return None

def store_in_pinecone(mongodb_id, contextual_content):
    """
    Store workflow embedding in Pinecone with minimal metadata
    """
    try:
        index = pinecone_manager.get_index()
        if index is None:
            logging.warning("⚠️ Pinecone not available - skipping vector storage")
            return False
        
        embedding = generate_embedding(contextual_content)
        if embedding is None:
            return False

        metadata = {
            'mongodb_id': mongodb_id,
            'vectorized_at': datetime.now(timezone.utc).isoformat(),
            'contextual_content': contextual_content
        }
        
        logging.info(f"📤 Storing vector in Pinecone with ID: {mongodb_id}")
        index.upsert(
            vectors=[{'id': mongodb_id, 'values': embedding, 'metadata': metadata}]
        )
        logging.info(f"✅ Successfully stored in Pinecone: {mongodb_id}")
        return True
    
    except Exception as e:
        logging.error(f"❌ Error storing in Pinecone: {e}")
        traceback.print_exc()
        return False

def process_workflow_vectorization(workflow_data, mongodb_id):
    """
    Async task: Generate content, embed, and store in Pinecone
    """
    try:
        logging.info(f"🔄 [Async] Starting vectorization for workflow: {mongodb_id}")
        contextual_content = generate_contextual_content(workflow_data, mongodb_id)
        store_in_pinecone(mongodb_id, contextual_content)
    except Exception as e:
        logging.error(f"❌ [Async] Error in vectorization process: {e}")
        traceback.print_exc()

# --- Database Operations ---

def save_workflow_to_db(workflow_data):
    """
    Saves the enhanced workflow to MongoDB.
    This function is synchronous and uses the DB context manager.
    It now also applies the step descriptions.
    """
    try:
        with get_db_collection() as collection:
            if collection is None:
                logging.error("Failed to get DB collection. Workflow not saved.")
                return None, None
            
            # Apply step descriptions to steps
            enhanced_steps = []
            steps = workflow_data.get('steps', [])
            step_descriptions = workflow_data.get('step_descriptions', [])
            
            for i, step in enumerate(steps):
                enhanced_step = {**step}
                if i < len(step_descriptions):
                    enhanced_step['description'] = step_descriptions[i]
                else:
                    enhanced_step['description'] = f"Step {i+1}: {step.get('type')}"
                enhanced_steps.append(enhanced_step)
            
            mongodb_id = str(uuid.uuid4())
            
            # This is the final, complete document to be saved
            enhanced_workflow = {
                '_id': mongodb_id,
                # --- MODIFIED: Use the AI-generated name ---
                'name': workflow_data.get('name', 'Recorded Workflow'), # from AI
                'description': workflow_data.get('description'), # from AI
                'workflow_analysis': workflow_data.get('workflow_analysis'), # from AI
                'requires_password': workflow_data.get('requires_password'), # from AI
                'steps': enhanced_steps, # new steps with descriptions
                'metadata': {
                    'enhanced_at': datetime.now(timezone.utc).isoformat(),
                    'step_count': len(enhanced_steps),
                    'version': '1.0'
                }
            }
            
            # Add any other original keys from the recorder (e.g., 'startUrl')
            for key, value in workflow_data.items():
                if key not in enhanced_workflow and key not in ['step_descriptions']:
                    enhanced_workflow[key] = value
            
            collection.insert_one(enhanced_workflow)
            logging.info(f"✅ Workflow saved to MongoDB: {mongodb_id}")
            
            # Return the fully constructed document
            return enhanced_workflow, mongodb_id
    except Exception as e:
        logging.error(f"❌ Error saving to MongoDB: {e}")
        traceback.print_exc()
        return None, None