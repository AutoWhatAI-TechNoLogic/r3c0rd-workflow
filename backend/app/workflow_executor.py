import json
import time
import os
import sys
from typing import Dict, List, Any, Optional
import re
from playwright.sync_api import sync_playwright, Page, Browser, Playwright, TimeoutError as PlaywrightTimeoutError
import logging
import traceback

# --- IMPORTS FOR SELF-HEALING ---
# We use try/except to make this file runnable standalone
try:
    from app.config import Config
    from app.db import get_db_collection
    OPENAI_API_KEY = Config.OPENAI_API_KEY
    MONGODB_AVAILABLE = True
except ImportError:
    # Fallback if running standalone
    MONGODB_AVAILABLE = False
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") # Try to get from env
    if not OPENAI_API_KEY:
        print("⚠️  Warning: app.config not found and OPENAI_API_KEY not in env. Self-healing is disabled.")

try:
    from openai import OpenAI
    if not OPENAI_API_KEY:
        raise ImportError("OpenAI API key not found")
    SELF_HEALING_AVAILABLE = MONGODB_AVAILABLE
except ImportError:
    SELF_HEALING_AVAILABLE = False


class PlaywrightWorkflowExecutor:
    
    def __init__(self, headless: bool = False, keep_open: bool = False, mongodb_id: str = None, password: Optional[str] = None):
        """
        Initialize the workflow executor with Playwright
        
        Args:
            headless: Run browser in headless mode
            keep_open: Keep browser open after workflow completion
            mongodb_id: The ID of the workflow in MongoDB (for saving fixes)
            password: The real password provided by the user (if any)
        """
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.headless = headless
        self.keep_open = keep_open
        
        # For Self-Healing
        self.mongodb_id = mongodb_id
        self.can_self_heal = SELF_HEALING_AVAILABLE and bool(self.mongodb_id) and bool(OPENAI_API_KEY)
        self.MAX_HEAL_ATTEMPTS = 5 if self.can_self_heal else 1
        
        if self.can_self_heal:
            try:
                self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
                print("✅ Self-healing enabled.")
            except Exception as e:
                print(f"⚠️  Could not initialize OpenAI client, self-healing disabled: {e}")
                self.openai_client = None
                self.can_self_heal = False
        else:
            self.openai_client = None
            if not SELF_HEALING_AVAILABLE:
                print("⚠️  Self-healing disabled (MongoDB or OpenAI lib not found).")

        
        # Store user-supplied password
        self.user_password = password
        
        self.setup_playwright()
    
    def setup_playwright(self):
        """Setup Playwright with Chrome browser"""
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--no-default-browser-check",
                    "--disable-extensions"
                ]
            )
            context = self.browser.new_context(
                viewport=None,
                no_viewport=True,
                ignore_https_errors=True
            )
            self.page = context.new_page()
            if not self.headless:
                try:
                    self.page.set_viewport_size({"width": 1920, "height": 1080})
                    print("🖥️  Browser opened in full screen mode")
                except Exception as e:
                    print(f"⚠️  Could not set viewport: {e}")
            print("✅ Playwright initialized successfully")
            
        except ImportError:
            print("❌ Playwright not installed. Please install: pip install playwright && playwright install")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error setting up Playwright: {e}")
            sys.exit(1)

    def maximize_window(self):
        """Maximize the browser window to full screen (if not headless)"""
        if self.headless:
            return
        try:
            self.page.keyboard.press("F11")
            time.sleep(1)
            print("🔲 Browser maximized to full screen")
        except Exception as e:
            print(f"⚠️  Could not maximize window automatically: {e}")

    def execute_workflow(self, workflow_data: Dict[str, Any], workflow_name: str = "Unknown"):
        """
        Execute a workflow from a dictionary object with a self-healing loop.
        """
        
        # We create a deep copy so our changes don't affect the original dict
        current_workflow_json = json.loads(json.dumps(workflow_data))
        
        for attempt in range(1, self.MAX_HEAL_ATTEMPTS + 1):
            if self.MAX_HEAL_ATTEMPTS > 1:
                print(f"\n{'='*20} 🚀 ATTEMPT {attempt}/{self.MAX_HEAL_ATTEMPTS} {'='*20}")
            print(f"🎯 Executing workflow: {current_workflow_json.get('name', workflow_name)}")
            
            # Try to run the entire workflow
            success, failed_step_index, error_message = self._run_workflow_steps(current_workflow_json)
            
            if success:
                print(f"\n✅ Workflow '{current_workflow_json.get('name', workflow_name)}' completed successfully!")
                
                # If this was a healed attempt, save it back to MongoDB
                if attempt > 1 and self.mongodb_id:
                    print(f"✨ Self-healing successful. Saving updated workflow to MongoDB: {self.mongodb_id}")
                    self._save_healed_workflow(current_workflow_json)
                
                if self.keep_open:
                    print("🔄 Browser will remain open. Press Ctrl+C in server terminal to close.")
                    self.wait_for_user_close()
                
                self.close()
                return True
            
            # If failed and more attempts left, try to heal
            if attempt < self.MAX_HEAL_ATTEMPTS:
                print(f"\n🩹 Workflow failed. Attempting self-healing (Attempt {attempt})...")
                
                # Get the specific step that failed
                failed_step_json = current_workflow_json['steps'][failed_step_index]
                
                print(f"   Failed Step: {failed_step_json.get('description', 'N/A')}")
                print(f"   Error: {error_message}")
                
                # Send *only* the failed step to the AI
                healed_step_json = self._request_ai_heal(failed_step_json, error_message)
                
                if healed_step_json:
                    print("🤖 AI provided a potential fix. Retrying...")
                    # Surgically replace *only* the broken step
                    current_workflow_json['steps'][failed_step_index] = healed_step_json
                else:
                    print("❌ AI could not provide a fix. Aborting.")
                    break
            else:
                if self.can_self_heal:
                    print(f"❌ Max self-healing attempts ({self.MAX_HEAL_ATTEMPTS}) reached. Aborting.")
        
        # If loop finishes without success
        print(f"❌ Workflow '{workflow_data.get('name', workflow_name)}' failed.")
        self.close()
        return False

    def _run_workflow_steps(self, workflow_data: Dict[str, Any]):
        """
        Internal function to run workflow steps.
        Returns (success, failed_step_index, error_message)
        """
        steps = workflow_data.get('steps', [])
        total_steps = len(steps)
        print(f"📊 Total steps: {total_steps}")
        
        if not self.headless:
            self.maximize_window()
        
        for i, step in enumerate(steps, 1):
            print(f"\n  🔄 Step {i}/{total_steps}: {step.get('description', 'No description')}")
            try:
                self.execute_step(step)
            except Exception as e:
                print(f"    ❌ ERROR on step {i}: {str(e)}")
                traceback.print_exc(limit=2)
                # Return failure status and the data needed for healing
                # We return i-1 because step index is 1-based, list index is 0-based
                return False, i-1, str(e) 
                
        return True, None, None
    
    def _request_ai_heal(self, failed_step: Dict[str, Any], error_message: str):
        """
        Send *only* the failed step to OpenAI and ask for a fix.
        """
        if not self.openai_client:
            return None
            
        try:
            # --- THIS IS THE SMARTER, SURGICAL AI PROMPT ---
            prompt = f"""You are an expert Playwright automation engineer. A single workflow step has failed. Your task is to fix it.

Analyze the failed step JSON and the error message.
Return a *complete, new JSON* for *only this single step* with the fix applied.
The fix will likely involve correcting the 'xpath' or 'cssSelector' for the failed step.

ERROR ANALYSIS:
1.  **Try to find a better selector:** Look at the 'targetText', 'elementText', or 'placeholder' in the failed step. Suggest a robust text-based or role-based selector. For example, change a brittle XPath to:
    `"xpath": "//*[text()='Submit Form']"` or
    `"cssSelector": "button:has-text('Submit Form')"`

2.  **Handle missing text:** If the failed step is for an input field with NO 'targetText' (like the user's test case), the old selector (e.g., `id("old_id")`) failed. Your *only* option is to create an ordinal selector.
    For example:
    - If it's the *first* input on the page, a good fix is: `"cssSelector": "input[type='text']:nth-of-type(1)"`
    - If it's the *second* password input, a good fix is: `"cssSelector": "input[type='password']:nth-of-type(2)"`
    Analyze the *context* of the step (e.g., its original `xpath`) to make an intelligent guess.

RULES:
- Return ONLY the complete, valid JSON for *this single step*.
- Do not add any new, extra keys.
- Do not explain your changes. Just return the fixed JSON.

---
THE FAILED STEP JSON:
{json.dumps(failed_step, indent=2)}

---
THE ERROR MESSAGE:
{error_message}

---
Return the complete, corrected JSON for the *single step* now:
"""
            print("   -> Sending failed step to AI for analysis...")
            response = self.openai_client.chat.completions.create(
                model="gpt-4o", # Use a smart model
                messages=[
                    {"role": "system", "content": "You are an expert Playwright automation engineer. Your task is to fix a broken workflow step. You will return only the complete, corrected JSON for that single step."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2 # Be precise
            )
            
            healed_json_str = response.choices[0].message.content
            
            # Find the JSON block in the response, in case it adds text
            match = re.search(r'\{.*\}', healed_json_str, re.DOTALL)
            if not match:
                print("   -> AI returned a non-JSON response.")
                return None
                
            healed_json = json.loads(match.group(0))
            
            # Simple validation: check if it's a dict and has a 'type'
            if isinstance(healed_json, dict) and 'type' in healed_json:
                print("   -> AI returned a valid JSON fix for the step.")
                return healed_json
            else:
                print("   -> AI returned invalid JSON structure for the step.")
                return None
                
        except Exception as e:
            print(f"   -> ❌ Error during AI self-healing request: {e}")
            return None

    def _save_healed_workflow(self, healed_workflow_json: Dict[str, Any]):
        """
        Save the successfully healed workflow back to MongoDB.
        """
        if not self.mongodb_id or not MONGODB_AVAILABLE:
            print("   -> (Warning) No MongoDB ID provided or DB not available. Cannot save healed workflow.")
            return
            
        try:
            # This function must be defined if MONGODB_AVAILABLE is True
            with get_db_collection() as collection:
                if collection is None:
                    print("   -> (Error) Could not connect to MongoDB to save heal.")
                    return

                # Update the original document, replacing 'steps' and 'metadata'
                # and other top-level AI fields
                collection.update_one(
                    {'_id': self.mongodb_id},
                    {
                        '$set': {
                            'steps': healed_workflow_json.get('steps', []),
                            'name': healed_workflow_json.get('name', 'Healed Workflow'),
                            'description': healed_workflow_json.get('description', ''),
                            'workflow_analysis': healed_workflow_json.get('workflow_analysis', ''),
                            'requires_password': healed_workflow_json.get('requires_password', False),
                            'metadata.version': '1.1 (healed)',
                            'metadata.healed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ')
                        }
                    }
                )
                print(f"   -> ✅ Successfully saved healed workflow {self.mongodb_id} to DB.")
        except Exception as e:
            print(f"   -> ❌ FAILED to save healed workflow to DB: {e}")

    def wait_for_user_close(self):
        """Wait for user to manually close the browser"""
        try:
            while True:
                if not self.browser or not self.browser.is_connected():
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\n⏹️  User requested shutdown. Closing browser...")
        except Exception as e:
            print(f"⚠️  Error while waiting: {e}")
    
    def execute_step(self, step: Dict[str, Any]):
        """
        Execute a single workflow step.
        Raises an exception on failure, which is caught by the healing loop.
        """
        step_type = step.get('type', '')
        element_tag = step.get('elementTag', '').upper()
        
        # Increased wait time for dynamic pages
        self.page.wait_for_load_state('networkidle', timeout=10000)
        
        if (step_type == 'input' and element_tag == 'SELECT') or \
           ('dropdown' in step.get('description', '').lower() and 'select' in step.get('description', '').lower()):
            self.execute_dropdown_selection(step)
        elif step_type == 'navigation':
            self.execute_navigation(step)
        elif step_type == 'click':
            self.execute_click(step)
        elif step_type == 'input':
            self.execute_input(step)
        elif step_type == 'key_press':
            self.execute_key_press(step)
        elif step_type == 'scroll':
            self.execute_scroll(step)
        # --- START OF ADDED CODE ---
        elif step_type == 'extract':
            self.execute_extraction(step)
        # --- END OF ADDED CODE ---
        else:
            print(f"    ⚠ Unknown step type: {step_type}")
        
        time.sleep(0.5) # Brief pause between steps
    
    def execute_navigation(self, step: Dict[str, Any]):
        """Execute navigation step"""
        url = step.get('url', '')
        if url:
            self.page.goto(url, wait_until="networkidle", timeout=30000)
            print(f"    🌐 Navigated to: {url}")
        else:
            raise Exception(f"No URL provided for navigation step: {step.get('description')}")
    
    def execute_click(self, step: Dict[str, Any]):
        """Execute click step"""
        # Pass "click" as the step_type
        element = self.find_element(step, step_type="click") 
        if element:
            element.click(timeout=10000)
            print(f"    🖱️ Clicked element")
        else:
            raise Exception(f"Element not found for click: {step.get('description')}")
    
    def execute_input(self, step: Dict[str, Any]):
        """Execute input step for text fields"""
        # Pass "input" as the step_type
        element = self.find_element(step, step_type="input") 
        
        # Default value from JSON (likely "********")
        value = step.get('value', '')
        
        # Check if this is a password field
        is_password_field = False
        if "password" in step.get('description', '').lower() or \
           "password" in step.get('targetText', '').lower() or \
           "password" in step.get('cssSelector', '').lower() or \
           "password" in step.get('xpath', '').lower():
            is_password_field = True

        # If it's a password field AND we have a real password, override the value
        if is_password_field and self.user_password is not None:
            print("    ⚡ Detected password field. Using user-supplied password.")
            value = self.user_password
            masked_value = "[USER-SUPPLIED PASSWORD]"
        else:
            masked_value = "********" if is_password_field else value

        element_tag = step.get('elementTag', '').upper()
        if element_tag == 'INPUT':
            input_type = self.get_input_type(step)
            if input_type in ['radio', 'checkbox']:
                print(f"    ⚡ Skipping input for {input_type} (handled by click)")
                return
        
        if element:
            element.fill(value, timeout=10000)
            print(f"    ⌨️ Input text: {masked_value}")
        else:
            raise Exception(f"Element not found for input: {step.get('description')}")
    
    def execute_dropdown_selection(self, step: Dict[str, Any]):
        """Execute dropdown selection step"""
        # Pass "input" as the step_type, as dropdowns are a form of input
        element = self.find_element(step, step_type="input") 
        value = step.get('value', '')
        
        if element and value:
            element_tag = step.get('elementTag', '').upper()
            if element_tag == 'SELECT':
                try:
                    element.select_option(value, timeout=10000)
                    print(f"    📋 Selected dropdown option: {value}")
                except Exception:
                    # Fallback by text
                    element.select_option(label=value, timeout=10000)
                    print(f"    📋 Selected dropdown option (by label): {value}")
            else:
                element.click(timeout=10000)
                option_locator = self.page.get_by_text(value).first
                option_locator.wait_for(state="visible", timeout=5000)
                option_locator.click()
                print(f"    📋 Selected option: {value}")
        else:
            raise Exception(f"Element/Value not found for dropdown: {step.get('description')}")
    
    def execute_key_press(self, step: Dict[str, Any]):
        """Execute key press step"""
        # Pass "input" as the step_type, as key presses are often on inputs
        element = self.find_element(step, step_type="input") 
        key = step.get('key', '')
        if element and key:
            element.press(key, timeout=10000)
            print(f"    ⌨️ Pressed {key} key")
        else:
            raise Exception(f"Element not found for key press: {step.get('description')}")
    
    def execute_scroll(self, step: Dict[str, Any]):
        """Execute scroll step"""
        scroll_x = step.get('scrollX', 0)
        scroll_y = step.get('scrollY', 0)
        self.page.evaluate(f"window.scrollTo({scroll_x}, {scroll_y})")
        print(f"    📜 Scrolled to position ({scroll_x}, {scroll_y})")

    # --- START OF ADDED CODE ---

    def execute_extraction(self, step: Dict[str, Any]):
        """Execute extraction step using LLM"""
        try:
            # Import here to avoid circular dependency
            from app.services import extract_data_with_llm
            
            extraction_goal = step.get('extractionGoal', step.get('description', ''))
            
            if not extraction_goal:
                print(f"    ⚠️ No extraction goal specified")
                return
            
            print(f"    🔍 Extracting data: {extraction_goal}")
            
            # Get the current page HTML content
            html_content = self.page.content()
            current_url = self.page.url
            
            print(f"    📄 Retrieved page content ({len(html_content)} characters)")
            
            # Call the LLM extraction service
            extracted_data = extract_data_with_llm(html_content, extraction_goal, current_url)
            
            if extracted_data:
                print(f"    ✅ Data extracted successfully")
                
                # Display the extracted data in a new tab
                self.display_extraction_results(extracted_data, extraction_goal, current_url)
            else:
                print(f"    ❌ Failed to extract data")
                
        except Exception as e:
            print(f"    ❌ Error during extraction: {str(e)}")
            import traceback
            traceback.print_exc()

    def display_extraction_results(self, extracted_data: Dict[str, Any], extraction_goal: str, source_url: str):
        """Display extraction results in a new browser tab"""
        try:
            # Create HTML content for the results page
            html_content = self.generate_results_html(extracted_data, extraction_goal, source_url)
            
            # Open a new page/tab
            new_page = self.browser.new_page()
            
            # Set the content
            new_page.set_content(html_content)
            
            print(f"    🌐 Extraction results opened in new tab")
            
        except Exception as e:
            print(f"    ⚠️ Could not open results in new tab: {e}")
            # Fallback: print to console
            print(f"    📊 Extracted Data:")
            print(json.dumps(extracted_data, indent=2))

    def generate_results_html(self, extracted_data: Dict[str, Any], extraction_goal: str, source_url: str) -> str:
        """Generate HTML for displaying extraction results"""
        # Convert the extracted data to formatted JSON
        json_data = json.dumps(extracted_data, indent=2)
        
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Extraction Results</title>
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }}
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    padding: 40px 20px;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 16px;
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    overflow: hidden;
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px 40px;
                }}
                .header h1 {{
                    font-size: 28px;
                    margin-bottom: 10px;
                    font-weight: 600;
                }}
                .header p {{
                    opacity: 0.9;
                    font-size: 16px;
                }}
                .content {{
                    padding: 40px;
                }}
                .info-section {{
                    background: #f8f9fa;
                    border-radius: 8px;
                    padding: 20px;
                    margin-bottom: 30px;
                }}
                .info-label {{
                    font-weight: 600;
                    color: #495057;
                    margin-bottom: 8px;
                    font-size: 14px;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                }}
                .info-value {{
                    color: #212529;
                    font-size: 16px;
                    word-break: break-word;
                }}
                .data-section {{
                    margin-top: 30px;
                }}
                .section-title {{
                    font-size: 20px;
                    font-weight: 600;
                    color: #212529;
                    margin-bottom: 20px;
                    padding-bottom: 10px;
                    border-bottom: 2px solid #667eea;
                }}
                .json-container {{
                    background: #2d3748;
                    border-radius: 8px;
                    padding: 24px;
                    overflow-x: auto;
                }}
                pre {{
                    color: #e2e8f0;
                    font-family: 'Monaco', 'Menlo', 'Courier New', monospace;
                    font-size: 14px;
                    line-height: 1.6;
                    margin: 0;
                }}
                .copy-button {{
                    background: #667eea;
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    border-radius: 6px;
                    cursor: pointer;
                    font-size: 14px;
                    font-weight: 600;
                    margin-top: 20px;
                    transition: background 0.2s;
                }}
                .copy-button:hover {{
                    background: #5568d3;
                }}
                .copy-button:active {{
                    transform: scale(0.98);
                }}
                .data-items {{
                    display: grid;
                    gap: 16px;
                }}
                .data-item {{
                    background: #f8f9fa;
                    border-left: 4px solid #667eea;
                    padding: 16px 20px;
                    border-radius: 6px;
                }}
                .data-item-key {{
                    font-weight: 600;
                    color: #667eea;
                    margin-bottom: 8px;
                    font-size: 15px;
                }}
                .data-item-value {{
                    color: #495057;
                    line-height: 1.6;
                    white-space: pre-wrap;
                    word-break: break-word;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎯 Extraction Results</h1>
                    <p>Data successfully extracted from the webpage</p>
                </div>
                
                <div class="content">
                    <div class="info-section">
                        <div class="info-label">📝 Extraction Goal</div>
                        <div class="info-value">{extraction_goal}</div>
                    </div>
                    
                    <div class="info-section">
                        <div class="info-label">🌐 Source URL</div>
                        <div class="info-value"><a href="{source_url}" target="_blank" style="color: #667eea; text-decoration: none;">{source_url}</a></div>
                    </div>
                    
                    <div class="data-section">
                        <h2 class="section-title">📊 Extracted Data</h2>
                        
                        <div class="data-items">
        """
        
        # Add each extracted field as a card
        for key, value in extracted_data.items():
            html += f"""
                            <div class="data-item">
                                <div class="data-item-key">{key}</div>
                                <div class="data-item-value">{str(value)}</div>
                            </div>
            """
        
        html += """
                        </div>
                        
                        <div class="json-container">
                            <pre id="jsonData">""" + json_data + """</pre>
                        </div>
                        
                        <button class="copy-button" onclick="copyToClipboard()">📋 Copy JSON to Clipboard</button>
                    </div>
                </div>
            </div>
            
            <script>
                function copyToClipboard() {
                    const jsonData = document.getElementById('jsonData').textContent;
                    navigator.clipboard.writeText(jsonData).then(() => {
                        const button = document.querySelector('.copy-button');
                        const originalText = button.textContent;
                        button.textContent = '✅ Copied!';
                        button.style.background = '#48bb78';
                        setTimeout(() => {
                            button.textContent = originalText;
                            button.style.background = '#667eea';
                        }, 2000);
                    }).catch(err => {
                        alert('Failed to copy to clipboard');
                    });
                }
            </script>
        </body>
        </html>
        """
        
        return html
    
    # --- END OF ADDED CODE ---

    def find_element(self, step: Dict[str, Any], step_type: str, timeout_ms: int = 10000):
        """
        Find element using a robust, multi-strategy approach.
        Uses different fallbacks based on the step_type ("input" or "click").
        """
        element = None
        text_to_find = step.get('targetText') or step.get('elementText')
        placeholder_to_find = step.get('placeholder')
        
        locators = []

        # --- STRATEGY 1: ORIGINAL BRITTLE LOCATORS (ALWAYS TRY FIRST) ---
        locators.append(('xpath', step.get('xpath')))
        locators.append(('css', step.get('cssSelector')))
        locators.append(('id', self.extract_id_from_xpath(step.get('xpath'))))

        # --- STRATEGY 2: CONTEXT-AWARE FALLBACKS ---
        
        # If it's an INPUT step, look for placeholders and labels
        if step_type == 'input':
            if placeholder_to_find:
                locators.append(('placeholder', placeholder_to_find))
            if text_to_find:
                # Use get_by_label for associated <label> tags
                locators.append(('label', text_to_find))
                
        # If it's a CLICK step, look for buttons and links
        elif step_type == 'click':
            if text_to_find:
                locators.append(('role_button', text_to_find))
                locators.append(('role_link', text_to_find))

        # --- STRATEGY 3: GENERAL TEXT FALLBACK (LAST RESORT) ---
        if text_to_find:
            locators.append(('text_exact', text_to_find))
            locators.append(('text_contains', text_to_find))


        for locator_type, locator_value in locators:
            if not locator_value:
                continue
                
            try:
                print(f"    ... Trying strategy: {locator_type} = '{str(locator_value)[:50]}...'")
                element = self.find_element_playwright(locator_type, locator_value)
                
                if element:
                    element.wait_for(state="visible", timeout=timeout_ms)
                    print(f"    ✅ Found element using strategy: {locator_type}")
                    return element # Success!
                
            except PlaywrightTimeoutError:
                print(f"    ... Strategy timed out (not visible): {locator_type}")
                continue # Try next locator
            except Exception as e:
                print(f"    ... Strategy failed: {locator_type} ({e})")
                continue # Try next locator
        
        print(f"    ❌ FAILED: Could not find element for step: {step.get('description')}")
        return None

    # --- THIS IS THE UPGRADED FUNCTION ---
    def find_element_playwright(self, locator_type: str, locator_value: str):
        """Find element using Playwright locator methods"""
        if locator_type == 'xpath':
            return self.page.locator(f"xpath={locator_value}")
        elif locator_type == 'css':
            return self.page.locator(locator_value)
        elif locator_type == 'id':
            return self.page.locator(f"#{locator_value}")
        # Text-based locators
        elif locator_type == 'role_link':
            return self.page.get_by_role("link", name=re.compile(locator_value, re.IGNORECASE)).first
        elif locator_type == 'role_button':
            return self.page.get_by_role("button", name=re.compile(locator_value, re.IGNORECASE)).first
        elif locator_type == 'text_exact':
            return self.page.get_by_text(locator_value, exact=True).first
        elif locator_type == 'text_contains':
            return self.page.get_by_text(re.compile(locator_value, re.IGNORECASE)).first
        elif locator_type == 'placeholder':
            return self.page.get_by_placeholder(re.compile(locator_value, re.IGNORECASE)).first
        elif locator_type == 'label':
            return self.page.get_by_label(re.compile(locator_value, re.IGNORECASE)).first
        return None
    
    def extract_id_from_xpath(self, xpath: Optional[str]) -> Optional[str]:
        """Extract ID from XPath if present"""
        if not xpath:
            return None
        if 'id("' in xpath:
            match = re.search(r'id\("([^"]+)"\)', xpath)
            if match:
                return match.group(1)
        return None
    
    def get_input_type(self, step: Dict[str, Any]) -> str:
        """Get the input type from CSS selector or other attributes"""
        css_selector = step.get('cssSelector', '')
        if 'type=\"radio\"' in css_selector:
            return 'radio'
        elif 'type=\"checkbox\"' in css_selector:
            return 'checkbox'
        # Check for password type
        elif 'type=\"password\"' in css_selector:
            return 'password'
        return 'text'

    def close(self):
        """Close the browser and cleanup"""
        try:
            if self.browser and self.browser.is_connected():
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            print("🔚 Browser closed.")
        except Exception as e:
            print(f"⚠️ Error during cleanup: {e}")

# --- COMMAND-LINE EXECUTION (for standalone use) ---

def print_banner():
    """Print a nice banner"""
    banner = """
    🚀 PLAYWRIGHT WORKFLOW EXECUTOR
    ===============================
    (Standalone Runner Mode)
    """
    print(banner)

def parse_arguments():
    """Parse command line arguments"""
    # Import argparse only if needed
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Execute workflow JSON files using Playwright',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Modified to accept a file path *or* run in server mode
    parser.add_argument(
        'workflow_file', 
        nargs='?', 
        default=None, 
        help='Specific workflow JSON file to execute (optional)'
    )
    parser.add_argument(
        '--server', 
        action='store_true', 
        help='Run in server mode (not implemented in this file)'
    )
    parser.add_argument('--headless', action='store_true', help='Run browser in headless mode')
    parser.add_argument('--keep-open', action='store_true', help='Keep browser open after workflow completion')
    return parser.parse_args()

def main():
    # This main block is for *standalone testing*
    # The server uses this class by importing it in `app/routes.py`
    
    print_banner()
    args = parse_arguments()
    
    if args.server:
        print("Server mode not implemented in standalone runner. Run `python run.py`")
        sys.exit(1)
        
    if not args.workflow_file:
        print("❌ Error: No workflow_file specified.")
        print("Usage: python app/workflow_executor.py <path_to_workflow.json>")
        sys.exit(1)

    executor = None
    try:
        executor = PlaywrightWorkflowExecutor(
            headless=args.headless, 
            keep_open=args.keep_open
            # Note: mongodb_id is not passed, so self-healing is disabled
        )
        
        if os.path.exists(args.workflow_file):
            # execute_workflow handles loading the file path
            with open(args.workflow_file, 'r') as f:
                workflow_json = json.load(f)
            
            success = executor.execute_workflow(workflow_json, os.path.basename(args.workflow_file))
            if success:
                sys.exit(0)
            else:
                sys.exit(1)
        else:
            print(f"❌ Workflow file not found: {args.workflow_file}")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⏹️ Execution interrupted by user")
        if executor:
            executor.close()
        sys.exit(1)
    except Exception as e:
        print(f"❌ An error occurred: {str(e)}")
        if executor:
            executor.close()
        sys.exit(1)

if __name__ == "__main__":
    main()