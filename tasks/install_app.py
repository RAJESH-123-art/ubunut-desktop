"""
Install applications via Ubuntu Software/App Center.
Handles search, installation, and password authentication.
Enhanced with multi-method UI interaction, debug screenshots, and robust error handling.
"""

import time
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from loguru import logger
from pynput.mouse import Controller as MouseController
from pynput.mouse import Button as MouseButton

from core.gui_controller import GUIController, is_wayland
from core.vision_engine import VisionEngine
from core.browser_manager import setup_shared_resources, cleanup_shared_resources, finalize_workflow
from core.logger import start, finish, notify, telegram
from core.system_utils import command, command_output
from tasks.install_verification import InstallationVerificationManager


class DebugScreenshotManager:
    """Manages debug screenshots for visual feedback and troubleshooting."""
    
    def __init__(self, gui):
        self.gui = gui
        self.screenshots = []
    
    def capture(self, name: str, description: str = "") -> str:
        """Capture screenshot with description."""
        try:
            path = self.gui.screenshot(name=f"debug_{name}")
            self.screenshots.append({"name": name, "path": path, "description": description})
            logger.info(f"Debug screenshot captured: {name} - {description}")
            return path
        except Exception as e:
            logger.error(f"Failed to capture debug screenshot {name}: {e}")
            return ""
    
    def get_report(self) -> str:
        """Generate debug report of all screenshots."""
        report = "\n=== Debug Screenshot Report ===\n"
        for i, shot in enumerate(self.screenshots, 1):
            report += f"{i}. {shot['name']}: {shot['path']}\n"
            if shot['description']:
                report += f"   Description: {shot['description']}\n"
        return report


class UIInteractionManager:
    """Manages multi-method UI interactions with fallbacks."""
    
    def __init__(self, gui: GUIController, vision: VisionEngine, debug_manager: DebugScreenshotManager):
        self.gui = gui
        self.vision = vision
        self.debug = debug_manager
    
    def try_keyboard_shortcut(self, *keys, delay: float = 0.5) -> bool:
        """Try keyboard shortcut method."""
        try:
            self.gui.hotkey(*keys)
            time.sleep(delay)
            self.debug.capture(f"after_hotkey_{'_'.join(keys)}", f"After pressing {' + '.join(keys)}")
            return True
        except Exception as e:
            logger.warning(f"Keyboard shortcut {keys} failed: {e}")
            return False
    
    def try_mouse_click(self, x: int, y: int, description: str = "", delay: float = 0.5) -> bool:
        """Try mouse click method."""
        try:
            self.gui.click(x, y)
            time.sleep(delay)
            self.debug.capture(f"after_click_{x}_{y}", description or f"After clicking at ({x}, {y})")
            return True
        except Exception as e:
            logger.warning(f"Mouse click at ({x}, {y}) failed: {e}")
            return False
    
    def try_vision_based_click(self, description: str = "", delay: float = 0.5) -> bool:
        """Try vision-based detection and click."""
        try:
            screenshot_path = self.gui.screenshot(name="vision_detection_attempt")
            text = self.vision.ocr_text(image_path=screenshot_path)
            logger.info(f"OCR text detected: {text[:100]}...")
            
            if "search" in text.lower():
                screen_width, screen_height = self.gui.get_screen_size()
                self.gui.click(x=screen_width//2, y=80)
                time.sleep(delay)
                self.debug.capture("after_vision_click_search", "Clicked on search area detected by vision")
                return True
            return False
        except Exception as e:
            logger.warning(f"Vision-based detection failed: {e}")
            return False
    
    def try_tab_navigation(self, num_tabs: int = 10, delay: float = 0.2) -> bool:
        """Try tab navigation method."""
        try:
            self.gui.click(10, 10)
            time.sleep(0.5)
            for _ in range(num_tabs):
                self.gui.press("tab")
                time.sleep(delay)
            self.debug.capture("after_tab_navigation", f"After {num_tabs} tab presses")
            return True
        except Exception as e:
            logger.warning(f"Tab navigation failed: {e}")
            return False
    
    def execute_with_fallbacks(self, methods: List[tuple], description: str = "") -> bool:
        """Execute methods with fallbacks until one succeeds."""
        logger.info(f"Executing {description} with {len(methods)} fallback methods")
        self.debug.capture(f"before_{description}", f"Before attempting {description}")
        
        for i, (method_name, method_callable) in enumerate(methods, 1):
            logger.info(f"Attempt {i}/{len(methods)}: {method_name}")
            try:
                if method_callable():
                    logger.info(f"Success with method: {method_name}")
                    return True
            except Exception as e:
                logger.warning(f"Method {method_name} failed: {e}")
                self.debug.capture(f"failed_{method_name}", f"Failed attempt with {method_name}")
            time.sleep(0.5)
        
        logger.error(f"All fallback methods failed for {description}")
        self.debug.capture(f"all_failed_{description}", f"All methods failed for {description}")
        return False


def try_tab_navigation(gui):
    """Helper function to try tab navigation"""
    gui.click(10, 10)
    time.sleep(0.5)
    for _ in range(10):
        gui.press("tab")
        time.sleep(0.2)

def find_and_click_search_input(gui, vision):
    """Try to find search input using OCR or template matching"""
    try:
        # First try OCR to find text like "Search"
        screenshot_path = gui.screenshot(name="search_detection")
        text = vision.ocr_text(image_path=screenshot_path)
        
        if "search" in text.lower():
            # Take coordinates (approximate for now)
            screen_width, screen_height = gui.get_screen_size()
            # Click in top area where search should be
            gui.click(x=screen_width//2, y=80)
            time.sleep(1)
            return
            
    except Exception as e:
        logger.warning(f"Vision-based search detection failed: {e}")
        # As fallback, click where search likely is
        screen_width, _ = gui.get_screen_size()
        gui.click(x=screen_width//2, y=100)
        time.sleep(1)

def setup():
    """Initialize shared resources for app installation."""
    # No browser needed for app installation
    return setup_shared_resources(create_browser=False)

def execute(args: dict, resources: dict):
    """Open app center, search for app, and install it with enhanced error handling."""
    task_name = "install_app"
    start(task_name)
    debug_manager = None
    ui_manager = None
    verification_manager = None
    
    try:
        gui = resources["gui"]
        vision = resources["vision"]
        app_name = args.get("app_name", "")
        password = args.get("password", "")
        package_name = args.get("package_name", app_name.lower())
        
        if not app_name:
            raise ValueError("No app name provided")
        if not password:
            logger.warning("No password provided, installation may fail")
        
        # Initialize managers
        debug_manager = DebugScreenshotManager(gui)
        ui_manager = UIInteractionManager(gui, vision, debug_manager)
        verification_manager = InstallationVerificationManager()
        # Open Ubuntu App Center (snap store)
        logger.info("Opening Ubuntu App Center")
        app_opened = False
        
        for app_cmd, app_label in [("snap-store", "Snap Store"), ("gnome-software", "GNOME Software")]:
            try:
                logger.info(f"Attempting to open {app_label}")
                command(f"{app_cmd} &")
                logger.info(f"Waiting for {app_label} to fully load...")
                time.sleep(5)  # Increased wait time for app to fully load
                app_opened = True
                debug_manager.capture(f"opened_{app_cmd}", f"Successfully opened {app_label}")
                break
            except Exception as e:
                logger.warning(f"Failed to open {app_label}: {e}")
                debug_manager.capture(f"failed_{app_cmd}", f"Failed to open {app_label}")
                continue
        
        if not app_opened:
            raise RuntimeError("Failed to open any app center application")
        
        # Focus window with multiple attempts
        window_focused = False
        for window_name in ["Software", "Ubuntu Software", "GNOME Software", "Snap Store"]:
            if gui.focus_window(window_name):
                logger.info(f"Successfully focused window: {window_name}")
                debug_manager.capture(f"focused_{window_name}", f"Window focused: {window_name}")
                window_focused = True
                break
        
        if not window_focused:
            logger.warning("Could not focus any known software center window using wmctrl")
            logger.info("Attempting to focus by clicking on the window")
            # Click in the center of the screen to focus the app center window
            screen_width, screen_height = gui.get_screen_size()
            gui.click(screen_width // 2, screen_height // 2)
            time.sleep(1)
            debug_manager.capture("window_focus_failed", "Failed wmctrl focus, tried click fallback")
        
        time.sleep(2)  # Extra wait to ensure window is ready
        
        # Search for app with fallback methods
        search_methods = [
            ("Ctrl+F Keyboard Shortcut", lambda: ui_manager.try_keyboard_shortcut("ctrl", "f")),
            ("Tab Navigation", lambda: ui_manager.try_tab_navigation(num_tabs=10)),
            ("Vision-based Search Detection", lambda: ui_manager.try_vision_based_click("Vision-based search detection")),
            ("Direct Center Click", lambda: ui_manager.try_mouse_click(int(gui.get_screen_size()[0]*0.5), 80, "Clicked center top area")),
        ]
        
        if ui_manager.execute_with_fallbacks(search_methods, "search_bar_focus"):
            # Wait a bit for search bar to be ready
            time.sleep(1)
            
            # Clear any existing text and type app name
            logger.info("Clearing existing text in search bar")
            gui.hotkey("ctrl", "a")
            time.sleep(0.5)
            
            # Type the app name slowly to ensure it registers
            logger.info(f"Typing app name: {app_name}")
            for char in app_name:
                gui.press(char.lower() if char.isalpha() else char)
                time.sleep(0.1)
            
            time.sleep(1)
            logger.info(f"Successfully typed: {app_name}")
            debug_manager.capture(f"typed_{app_name}", f"Typed {app_name} in search")
        else:
            logger.error("Could not focus search bar with any method")
            debug_manager.capture("search_bar_focus_failed", "All search bar focus methods failed")
            raise RuntimeError("Failed to focus search bar")
        
        # Press Enter to trigger search
        gui.press("enter")
        time.sleep(3)
        debug_manager.capture("after_search_enter", "After pressing Enter to search")
        
        # Navigate to app result and open details
        select_methods = [
            ("Arrow Down Navigation", lambda: (gui.press("down"), gui.press("down"), gui.press("down"), time.sleep(0.5), True)),
            ("Tab to Result", lambda: (gui.press("tab"), time.sleep(0.5), True)),
        ]
        
        if ui_manager.execute_with_fallbacks(select_methods, "app_result_selection"):
            gui.press("enter")
            time.sleep(3)
            debug_manager.capture("app_details_opened", f"Opened details for {app_name}")
        else:
            logger.warning("Could not navigate to app result")
        
        # Install the app with multiple attempts
        install_methods = [
            ("Tab to Install Button", lambda: (any(gui.press("tab") or time.sleep(0.4) for _ in range(5)), gui.press("enter"), time.sleep(2), True)),
            ("Direct Space Press", lambda: (gui.press("space"), time.sleep(2), True)),
            ("Direct Enter Press", lambda: (gui.press("enter"), time.sleep(2), True)),
        ]
        
        if ui_manager.execute_with_fallbacks(install_methods, "install_button_click"):
            logger.info("Install button activated, waiting for authentication")
            time.sleep(2)
            
            # Handle password authentication
            if password:
                logger.info("Attempting password entry")
                try:
                    gui.type_text(password)
                    time.sleep(0.5)
                    gui.press("enter")
                    time.sleep(1)
                    debug_manager.capture("password_entered", "Password entered for authentication")
                except Exception as e:
                    logger.error(f"Failed to enter password: {e}")
                    debug_manager.capture("password_entry_failed", f"Password entry failed: {e}")
            else:
                logger.warning("No password provided, authentication may fail")
        else:
            logger.warning("Could not click install button with any method")
        
        # Wait for installation to complete
        logger.info(f"Waiting for {app_name} installation to complete")
        for i in range(5):
            time.sleep(2)
            debug_manager.capture(f"installation_progress_{i}", f"Installation progress checkpoint {i+1}/5")
        
        # Take final screenshot
        final_screenshot = gui.screenshot(name="app_installation_final")
        
        # Verify installation actually succeeded
        logger.info("Verifying installation completed successfully")
        debug_manager.capture("before_verification", "Before verification attempt")
        verification_results = verification_manager.verify_app_installed(app_name, package_name)
        
        # Check if app is actually installed
        if verification_results['installed']:
            verification_report = verification_manager.get_verification_report()
            debug_report = debug_manager.get_report()
            logger.info("=== VERIFICATION REPORT ===")
            logger.info(verification_report)
            logger.info("=== DEBUG SCREENSHOT REPORT ===")
            logger.info(debug_report)
            
            notify(f"✅ Verification Confirmed: {app_name} is installed successfully")
            telegram(f"✅ Verified installation of {app_name}")
            finish("success", task_name)
            return True
        else:
            # Installation reported success but verification failed
            logger.error(f"INSTALLATION FAILED TO VERIFY: {app_name} is NOT installed")
            debug_manager.capture("verification_failed", f"Verification failed for {app_name}")
            
            # Try to check running processes if verification failed
            process_check = verification_manager.check_running_processes(app_name, package_name)
            logger.info(f"Process check result: {process_check}")
            
            error_msg = f"{app_name} installation appeared to succeed but verification failed. Details: {verification_results['details']}"
            notify(f"❌ VERIFICATION FAILED for {app_name}. App is NOT installed.")
            telegram(f"❌ {error_msg}")
            
            raise RuntimeError(error_msg)
        
    except Exception as exc:
        if debug_manager:
            debug_manager.capture("error_occurred", f"Error: {str(exc)}")
            logger.error(debug_manager.get_report())
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """Clean up resources."""
    gui = resources.get("gui")
    if gui:
        gui.screenshot("post_install_task")
    
    cleanup_shared_resources(resources)
    notify("App installation task completed")

if __name__ == "__main__":
    args = {"app_name": "Mumble", "password": "rgukt"}
    r = setup()
    execute(args, r)
    cleanup(r)