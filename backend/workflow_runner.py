import json
import time
import os
import sys
import argparse
from typing import Dict, List, Any, Optional

class PlaywrightWorkflowExecutor:
    def __init__(self, headless: bool = False, keep_open: bool = False):
        """
        Initialize the workflow executor with Playwright
        
        Args:
            headless: Run browser in headless mode
            keep_open: Keep browser open after workflow completion
        """
        self.playwright = None
        self.browser = None
        self.page = None
        self.headless = headless
        self.keep_open = keep_open
        self.setup_playwright()
    
    def setup_playwright(self):
        """Setup Playwright with Chrome browser in full screen"""
        try:
            from playwright.sync_api import sync_playwright
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
                viewport=None,  # Let it use full window size
                no_viewport=True,  # Don't enforce viewport size
                ignore_https_errors=True
            )
            
            self.page = context.new_page()
            
            # Maximize window to full screen
            self.page.set_viewport_size({"width": 1920, "height": 1080})
            
            print("✅ Playwright initialized successfully")
            print("🖥️  Browser opened in full screen mode")
            
        except ImportError:
            print("❌ Playwright not installed. Please install: pip install playwright && playwright install")
            sys.exit(1)
    
    def maximize_window(self):
        """Maximize the browser window to full screen"""
        try:
            # Try to maximize using keyboard shortcut F11
            self.page.keyboard.press("F11")
            time.sleep(1)
            print("🔲 Browser maximized to full screen")
        except Exception as e:
            print(f"⚠️  Could not maximize window automatically: {e}")
    
    def execute_workflow(self, workflow_file: str):
        """Execute a workflow from JSON file"""
        try:
            with open(workflow_file, 'r', encoding='utf-8') as f:
                workflow = json.load(f)
            
            print(f"\n🎯 Executing workflow: {workflow.get('name', 'Unknown')}")
            print(f"📝 Description: {workflow.get('description', 'No description')}")
            print(f"📁 File: {workflow_file}")
            
            steps = workflow.get('steps', [])
            total_steps = len(steps)
            print(f"📊 Total steps: {total_steps}")
            
            # Maximize window before starting workflow
            self.maximize_window()
            
            for i, step in enumerate(steps, 1):
                print(f"\n  🔄 Step {i}/{total_steps}: {step.get('description', 'No description')}")
                self.execute_step(step)
                
            print(f"\n✅ Workflow '{workflow.get('name', 'Unknown')}' completed successfully!")
            
            if self.keep_open:
                print("🔄 Browser will remain open. Press Ctrl+C to close.")
                self.wait_for_user_close()
            
            return True
            
        except Exception as e:
            print(f"❌ Error executing workflow {workflow_file}: {str(e)}")
            return False
    
    def wait_for_user_close(self):
        """Wait for user to manually close the browser"""
        try:
            while True:
                # Check if browser is still connected
                if not self.browser or not self.browser.is_connected():
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\n⏹️  User requested shutdown. Closing browser...")
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
            else:
                print(f"    ⚠ Unknown step type: {step_type}")
            
            time.sleep(1)  # Brief pause between steps
            
        except Exception as e:
            print(f"    ❌ Error in step: {str(e)}")
    
    def execute_navigation(self, step: Dict[str, Any]):
        """Execute navigation step"""
        url = step.get('url', '')
        if url:
            self.page.goto(url, wait_until="networkidle")
            print(f"    🌐 Navigated to: {url}")
    
    def execute_click(self, step: Dict[str, Any]):
        """Execute click step"""
        element = self.find_element(step)
        if element:
            element.click()
            print(f"    🖱️ Clicked element")
    
    def execute_input(self, step: Dict[str, Any]):
        """Execute input step for text fields"""
        element = self.find_element(step)
        value = step.get('value', '')
        
        # Skip input for radio buttons and checkboxes that are incorrectly recorded as input
        element_tag = step.get('elementTag', '').upper()
        if element_tag in ['INPUT']:
            input_type = self.get_input_type(step)
            if input_type in ['radio', 'checkbox']:
                print(f"    ⚡ Skipping input for {input_type} (handled by click)")
                return
        
        if element and value:
            element.fill(value)
            masked_value = "********" if "password" in step.get('targetText', '').lower() else value
            print(f"    ⌨️ Input text: {masked_value}")
    
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
                    print(f"    ⚠ Could not select option '{value}': {e}")
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
    
    def execute_scroll(self, step: Dict[str, Any]):
        """Execute scroll step"""
        scroll_x = step.get('scrollX', 0)
        scroll_y = step.get('scrollY', 0)
        
        self.page.evaluate(f"window.scrollTo({scroll_x}, {scroll_y})")
        print(f"    📜 Scrolled to position ({scroll_x}, {scroll_y})")
    
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
            print(f"    ⚠ Could not find element with available locators")
        
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
    
    def run_workflows_from_directory(self, directory_path: str = "."):
        """Run all workflow JSON files in a directory"""
        json_files = [f for f in os.listdir(directory_path) if f.endswith('.json')]
        
        if not json_files:
            print("❌ No JSON workflow files found in directory")
            return
        
        print(f"\n📁 Found {len(json_files)} workflow files:")
        for file in json_files:
            print(f"  - {file}")
        
        successful = 0
        for i, workflow_file in enumerate(json_files, 1):
            print(f"\n{'='*80}")
            print(f"📦 Processing workflow {i}/{len(json_files)}")
            if self.execute_workflow(os.path.join(directory_path, workflow_file)):
                successful += 1
            
            # Don't close browser between workflows if keep_open is enabled
            if i < len(json_files) and self.keep_open:
                input(f"\n⏎ Press Enter to continue to next workflow...")
            
            print(f"{'='*80}")
        
        print(f"\n📊 Summary: {successful}/{len(json_files)} workflows executed successfully")
        
        if self.keep_open:
            print("🔄 All workflows completed. Browser will remain open. Press Ctrl+C to close.")
            self.wait_for_user_close()
    
    def run_specific_workflow(self, workflow_file: str):
        """Run a specific workflow file"""
        if os.path.exists(workflow_file):
            return self.execute_workflow(workflow_file)
        else:
            print(f"❌ Workflow file not found: {workflow_file}")
            return False
    
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

def print_banner():
    """Print a nice banner"""
    banner = """
    🚀 PLAYWRIGHT WORKFLOW EXECUTOR
    ===============================
    Fast, reliable workflow automation
    Compatible with all your recorded workflows
    """
    print(banner)

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Execute workflow JSON files using Playwright',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python workflow_runner.py workflow.json
  python workflow_runner.py workflow.json --keep-open
  python workflow_runner.py --dir ./workflows --keep-open
  python workflow_runner.py workflow.json --headless
        """
    )
    
    parser.add_argument(
        'workflow_file', 
        nargs='?',
        help='Specific workflow JSON file to execute'
    )
    
    parser.add_argument(
        '--dir', 
        dest='directory',
        default=None,
        help='Directory containing workflow JSON files (executes all)'
    )
    
    parser.add_argument(
        '--headless', 
        action='store_true',
        help='Run browser in headless mode'
    )
    
    parser.add_argument(
        '--keep-open', 
        action='store_true',
        help='Keep browser open after workflow completion'
    )
    
    parser.add_argument(
        '--delay', 
        type=float,
        default=1.0,
        help='Delay between steps in seconds (default: 1.0)'
    )
    
    return parser.parse_args()

def main():
    print_banner()
    args = parse_arguments()
    
    executor = None
    try:
        # Initialize executor
        executor = PlaywrightWorkflowExecutor(
            headless=args.headless, 
            keep_open=args.keep_open
        )
        
        if args.directory:
            # Run all workflows in directory
            print(f"📂 Executing all workflows in directory: {args.directory}")
            executor.run_workflows_from_directory(args.directory)
        elif args.workflow_file:
            # Run specific workflow file
            success = executor.run_specific_workflow(args.workflow_file)
            if success:
                sys.exit(0)
            else:
                sys.exit(1)
        else:
            print("❌ Please specify either a workflow file or directory")
            print("💡 Usage: python workflow_runner.py <workflow.json> or --dir <directory>")
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