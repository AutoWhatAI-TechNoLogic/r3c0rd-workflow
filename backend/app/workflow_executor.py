import json
import time
import os
import sys
from typing import Dict, List, Any, Optional
from playwright.sync_api import sync_playwright, Page, Browser, Playwright

# MongoDB imports
try:
    from app.config import Config
    from app.db import get_db_collection
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    print("⚠️  MongoDB dependencies not available. Running in basic mode.")


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
        self.mongodb_id = mongodb_id
        self.user_password = password
        self.setup_playwright()
    
    def setup_playwright(self):
        """Setup Playwright with Chrome browser in full screen"""
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
            
            # Create context with viewport set to maximum screen size
            context = self.browser.new_context(
                viewport=None,
                no_viewport=True,
                ignore_https_errors=True
            )
            
            self.page = context.new_page()
            
            # Set viewport size if not headless
            if not self.headless:
                try:
                    self.page.set_viewport_size({"width": 1920, "height": 1080})
                except Exception as e:
                    print(f"⚠️  Could not set viewport: {e}")
            
            print("✅ Playwright initialized successfully")
            if not self.headless:
                print("🖥️  Browser opened in full screen mode")
            
        except ImportError:
            print("❌ Playwright not installed. Please install: pip install playwright && playwright install")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error setting up Playwright: {e}")
            sys.exit(1)
    
    def maximize_window(self):
        """Maximize the browser window to full screen"""
        if self.headless:
            return
        try:
            # Try to maximize using keyboard shortcut F11
            self.page.keyboard.press("F11")
            time.sleep(1)
            print("🔲 Browser maximized to full screen")
        except Exception as e:
            print(f"⚠️  Could not maximize window automatically: {e}")
    
    def execute_workflow(self, workflow_data: Dict[str, Any], workflow_name: str = "Unknown"):
        """
        Execute a workflow from a dictionary object or file path.
        """
        # Handle both file path and dictionary input
        if isinstance(workflow_data, str):
            # It's a file path
            try:
                with open(workflow_data, 'r', encoding='utf-8') as f:
                    workflow_data = json.load(f)
                workflow_name = workflow_data.get('name', os.path.basename(workflow_data))
            except Exception as e:
                print(f"❌ Error loading workflow file: {e}")
                return False
        
        print(f"\n🎯 Executing workflow: {workflow_data.get('name', workflow_name)}")
        print(f"📝 Description: {workflow_data.get('description', 'No description')}")
        
        steps = workflow_data.get('steps', [])
        total_steps = len(steps)
        print(f"📊 Total steps: {total_steps}")
        
        # Maximize window before starting workflow
        if not self.headless:
            self.maximize_window()
        
        for i, step in enumerate(steps, 1):
            print(f"\n  🔄 Step {i}/{total_steps}: {step.get('description', 'No description')}")
            try:
                self.execute_step(step)
            except Exception as e:
                print(f"    ❌ Error in step: {str(e)}")
                return False
        
        print(f"\n✅ Workflow '{workflow_data.get('name', workflow_name)}' completed successfully!")
        
        if self.keep_open:
            print("🔄 Browser will remain open. Press Ctrl+C to close.")
            self.wait_for_user_close()
        
        return True
    
    def wait_for_user_close(self):
        """Wait for user to manually close the browser"""
        try:
            while True:
                # Check if browser is still connected
                if not self.browser or not self.browser.is_connected():
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\nℹ️  User requested shutdown. Closing browser...")
        except Exception as e:
            print(f"⚠️  Error while waiting: {e}")
    
    def execute_step(self, step: Dict[str, Any]):
        """Execute a single workflow step"""
        step_type = step.get('type', '')
        element_tag = step.get('elementTag', '').upper()
        
        try:
            # Special handling for dropdown SELECT elements with input type
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
            elif step_type == 'extract':
                self.execute_extraction(step)
            else:
                print(f"    ⚠  Unknown step type: {step_type}")
            
            time.sleep(1)  # Brief pause between steps
            
        except Exception as e:
            print(f"    ❌ Error in step: {str(e)}")
    
    def execute_navigation(self, step: Dict[str, Any]):
        """Execute navigation step"""
        url = step.get('url', '')
        if url:
            self.page.goto(url)
            print(f"    🌐 Navigated to: {url}")
    
    def execute_click(self, step: Dict[str, Any]):
        """Execute click step"""
        element = self.find_element(step)
        if element:
            element.click()
            print(f"    🖱️ Clicked element")
        else:
            print(f"    ⚠️ Could not find element to click")
    
    def execute_input(self, step: Dict[str, Any]):
        """Execute input step for text fields"""
        element = self.find_element(step)
        
        # Default value from JSON
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
        
        # Skip input for radio buttons and checkboxes that are incorrectly recorded as input
        element_tag = step.get('elementTag', '').upper()
        if element_tag in ['INPUT']:
            input_type = self.get_input_type(step)
            if input_type in ['radio', 'checkbox']:
                print(f"    ⚡ Skipping input for {input_type} (handled by click)")
                return
        
        if element and value:
            element.fill(value)
            print(f"    ⌨️ Input text: {masked_value}")
        else:
            if not element:
                print(f"    ⚠️ Could not find element for input")
            if not value:
                print(f"    ⚠️ No value provided for input")
    
    def execute_dropdown_selection(self, step: Dict[str, Any]):
        """Execute dropdown selection step"""
        element = self.find_element(step)
        value = step.get('value', '')
        
        if element and value:
            # For SELECT elements, use select_option
            element_tag = step.get('elementTag', '').upper()
            if element_tag == 'SELECT':
                try:
                    element.select_option(value)
                    print(f"    📋 Selected dropdown option: {value}")
                except Exception as e:
                    print(f"    ⚠  Could not select option '{value}': {e}")
                    # Fallback: try clicking and then selecting
                    try:
                        element.click()
                        self.page.locator(f"option[value='{value}'], text={value}").first.click()
                        print(f"    📋 Selected dropdown option (fallback): {value}")
                    except Exception as e2:
                        print(f"    ❌ Failed to select dropdown option: {e2}")
            else:
                # For other elements that behave like dropdowns
                element.click()
                option_locator = self.page.locator(f"text={value}").first
                if option_locator.is_visible():
                    option_locator.click()
                    print(f"    📋 Selected option: {value}")
                else:
                    print(f"    ❌ Option '{value}' not found")
    
    def execute_key_press(self, step: Dict[str, Any]):
        """Execute key press step"""
        element = self.find_element(step)
        key = step.get('key', '')
        if element and key:
            element.press(key)
            print(f"    ⌨️ Pressed {key} key")
        else:
            if not element:
                print(f"    ⚠️ Could not find element for key press")
            if not key:
                print(f"    ⚠️ No key specified")
    
    def execute_scroll(self, step: Dict[str, Any]):
        """Execute scroll step"""
        scroll_x = step.get('scrollX', 0)
        scroll_y = step.get('scrollY', 0)
        
        self.page.evaluate(f"window.scrollTo({scroll_x}, {scroll_y})")
        print(f"    📜 Scrolled to position ({scroll_x}, {scroll_y})")
    
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
    
    def find_element(self, step: Dict[str, Any]):
        """Find element using available locators"""
        element = None
        
        # Try different locator strategies
        locators = [
            ('xpath', step.get('xpath')),
            ('css', step.get('cssSelector')),
            ('id', self.extract_id_from_xpath(step.get('xpath'))),
        ]
        
        for locator_type, locator_value in locators:
            if locator_value:
                try:
                    element = self.find_element_playwright(locator_type, locator_value)
                    if element:
                        break
                except Exception as e:
                    continue
        
        if not element:
            print(f"    ⚠  Could not find element with available locators")
        
        return element
    
    def find_element_playwright(self, locator_type: str, locator_value: str):
        """Find element using Playwright"""
        if locator_type == 'xpath':
            return self.page.locator(f"xpath={locator_value}")
        elif locator_type == 'css':
            return self.page.locator(locator_value)
        elif locator_type == 'id':
            return self.page.locator(f"#{locator_value}")
        return None
    
    def extract_id_from_xpath(self, xpath: Optional[str]) -> Optional[str]:
        """Extract ID from XPath if present"""
        if not xpath:
            return None
        if 'id("' in xpath:
            import re
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
        return 'text'
    
    def close(self):
        """Close the browser and cleanup"""
        try:
            if self.browser and self.browser.is_connected():
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            print("🔒 Browser closed.")
        except Exception as e:
            print(f"⚠️ Error during cleanup: {e}")