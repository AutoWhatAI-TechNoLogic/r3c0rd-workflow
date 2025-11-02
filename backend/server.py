from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
import os

app = Flask(__name__)
CORS(app)

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

@app.route('/enhance-workflow', methods=['POST'])
def enhance_workflow():
    try:
        workflow_data = request.json
        
        # Generate overall workflow description
        overall_description = generate_workflow_description(workflow_data)
        
        # Enhance each step with AI description
        enhanced_steps = []
        for step in workflow_data.get('steps', []):
            step_description = generate_step_description(step)
            enhanced_step = {
                **step,
                'description': step_description
            }
            enhanced_steps.append(enhanced_step)
        
        result = {
            **workflow_data,
            'description': overall_description,
            'steps': enhanced_steps
        }
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def generate_workflow_description(workflow_data):
    """Generate overall workflow description"""
    steps = workflow_data.get('steps', [])
    
    # Create a summary of the workflow
    steps_summary = []
    for i, step in enumerate(steps, 1):
        step_type = step.get('type', 'unknown')
        if step_type == 'navigation':
            steps_summary.append(f"Navigate to {step.get('url', 'page')}")
        elif step_type == 'click':
            steps_summary.append(f"Click on '{step.get('targetText', step.get('elementText', 'element'))}'")
        elif step_type == 'input':
            steps_summary.append(f"Enter text: '{step.get('value', '')}'")
        elif step_type == 'key_press':
            steps_summary.append(f"Press {step.get('key', 'key')}")
    
    prompt = f"""Analyze this browser workflow and provide a clear, concise overall description (2-3 sentences max):

Workflow has {len(steps)} steps:
{chr(10).join(steps_summary[:10])}  

Provide a natural language description of what this workflow accomplishes."""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that describes browser workflows clearly and concisely. Focus on the user's goal and what they're trying to accomplish."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )
        
        return response.choices[0].message.content.strip()
    
    except Exception as e:
        return f"Workflow description generation failed: {str(e)}"

def generate_step_description(step):
    """Generate AI description for a single workflow step"""
    step_type = step.get('type', 'unknown')
    
    # Build context-aware prompt based on step type
    if step_type == 'navigation':
        context = f"Navigating to: {step.get('url', 'unknown URL')}"
    elif step_type == 'click':
        target = step.get('targetText') or step.get('elementText', 'element')
        context = f"Clicking on: {target}\nElement: {step.get('elementTag', 'N/A')}\nURL: {step.get('url', 'N/A')}"
    elif step_type == 'input':
        context = f"Entering text: '{step.get('value', '')}'\nInto element: {step.get('elementTag', 'N/A')}\nTarget: {step.get('targetText', 'N/A')}"
    elif step_type == 'key_press':
        context = f"Pressing key: {step.get('key', 'N/A')}\nOn element: {step.get('elementTag', 'N/A')}"
    else:
        context = f"Action type: {step_type}\nElement: {step.get('elementTag', 'N/A')}"
    
    prompt = f"""Describe this browser workflow step in one clear sentence (user-friendly, no technical jargon):

{context}

Provide a simple description of what the user is doing."""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that describes browser actions clearly and simply. Write from the user's perspective (e.g., 'Search for products' not 'User searches')."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=80,
            temperature=0.7
        )
        
        return response.choices[0].message.content.strip()
    
    except Exception as e:
        return f"Step description unavailable"

if __name__ == '__main__':
    app.run(port=5000, debug=True)