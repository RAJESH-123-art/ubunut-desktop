"""
Simple intent parser for natural language automation commands.
Parses commands like:
- open brave browser
- open canva design
- click login button
- type username in input field

Supports future LLM integration for higher accuracy.
"""

import re
from typing import Dict, Any, Optional
from pathlib import Path
from loguru import logger


class Intent:
    def __init__(self, action: str, targets: list, extra: Optional[Dict[str, Any]] = None):
        self.action = action
        self.targets = targets
        self.extra = extra or {}
    
    def __repr__(self):
        return f"Intent(action={self.action!r}, targets={self.targets!r}, extra={self.extra})"


# Enhanced patterns to handle more complex multi-action commands.
# For production, replace with an LLM-based parser.
PATTERNS = {
    "open_browser": re.compile(r"\b(open|launch|start)\b\s+(firefox|chrome|brave|chromium|edge)\s+(browser)?\b", re.I),
    "navigate_to": re.compile(r"\b(go to|navigate to|visit)\s+(https?://[^\s]+)", re.I),
    "navigate_site": re.compile(r"\b(go to|navigate to|visit)\s+([a-z0-9.-]+\.[a-z]{2,})", re.I),
    "open_site_template": re.compile(r"\b(go to|navigate to|visit)\s+([^\s]+)", re.I),
    "create_template": re.compile(r"\b(make|create|design)\s+(.*)\s+(template|design)\s+(?:with|of)\s+(\d+[:x]\d+|4:5|16:9)", re.I),
    "open_app_multiword": re.compile(r"\b(open|launch|start)\b\s+(file manager|text editor|system settings|music player|photo viewer|image viewer)\b", re.I),
    "open_folder_app": re.compile(r"\b(open|launch|start)\s+(?:the )?(file|folder) (?:manager|explorer)\b", re.I),
    "click": re.compile(r"\b(click)\s+(.+?)\s*\b(button|link|image)?\b", re.I),
    "type": re.compile(r"\b(type|enter)\b\s+(.+?)(\s+in|\s+into)\s+(.+)", re.I),
    "wait": re.compile(r"\b(wait|pause|sleep)\b.*?(\d+(?:\.\d+)?)?\s*(seconds?|secs?|minutes?|mins?)?", re.I),
    "scroll": re.compile(r"\b(scroll)\s+(up|down)\b.*?(\d+)?\s*(times?|page)?", re.I),
    "screenshot": re.compile(r"\b(take|capture)\b\s+(?:my )?(?:a )?screenshot", re.I),
    "file_ops": re.compile(r"\b(delete|remove|move|copy|rename)\s+(?:the )?file\b\s+(.+)", re.I),
    "folder_ops": re.compile(r"\b(create|open|go\s+to)\s+(?:the )?folder\b\s+(.+)", re.I),
    "window_ops": re.compile(r"\b(minimize|maximize|close|focus)\b(?:\s+the)?\s+(?:window|app)\s+(.+)", re.I),
    "search": re.compile(r"\b(search|find|look for)\s+(.+?)\s+(?:on|in|at)\s+(.+)", re.I),
    "hotkey": re.compile(r"\b(press)\s+(\w+)(?:\s+(\w+))?(?:\s+(\w+))?", re.I),
    "drag_drop": re.compile(r"\b(drag)\s+(.+?)\s+to\s+(.+)", re.I),
    "system_ops": re.compile(r"\b(shutdown|reboot|logout|restart)\b(?:\s+(?:the )?\s*(computer|system|pc))?", re.I),
    "install_app": re.compile(r"\b(install)\b\s+(?:app )?(.+)", re.I),
}

EXTRACTORS = {
    "open_browser": {
        "browser_type": lambda m: m.group(2).lower().replace(" ", ""),
    },
    "navigate_to": {
        "url": lambda m: m.group(2).strip().rstrip(",.!?"),
    },
    "navigate_site": {
        "site": lambda m: m.group(2).strip().rstrip(",.!?"),
    },
    "open_site_template": {
        "site": lambda m: m.group(2).strip().rstrip(",.!?"),
    },
    "create_template": {
        "template_type": lambda m: m.group(2).strip(),
        "ratio": lambda m: m.group(4).strip(),
    },
    "open_app_multiword": {
        "app_name": lambda m: m.group(2).strip().lower(),
    },
    "open_folder_app": {},
    "click": {
        "target": lambda m: m.group(2).strip(),
    },
    "type": {
        "text": lambda m: m.group(2).strip('\'"'),
        "where": lambda m: m.group(4).strip(),
    },
    "wait": {
        "duration": lambda m: float(m.group(2)) if m and m.lastindex >= 2 and m.group(2) else 2.0,
        "unit": lambda m: m.group(3) if m and m.lastindex >= 3 else "seconds",
    },
    "scroll": {
        "direction": lambda m: m.group(2),
        "count": lambda m: int(m.group(3)) if m and m.lastindex >= 3 else 1,
    },
    "screenshot": {},
    "file_ops": {
        "operation": lambda m: m.group(1),
        "file_name": lambda m: m.group(2).strip(),
    },
    "folder_ops": {
        "operation": lambda m: m.group(1),
        "folder_name": lambda m: m.group(2).strip(),
    },
    "window_ops": {
        "operation": lambda m: m.group(1),
        "window_name": lambda m: m.group(2).strip(),
    },
    "search": {
        "query": lambda m: m.group(2).strip(),
        "location": lambda m: m.group(3).strip(),
    },
    "hotkey": {
        "key1": lambda m: m.group(2),
        "key2": lambda m: m.group(3) if m and m.lastindex >= 3 else None,
        "key3": lambda m: m.group(4) if m and m.lastindex >= 4 else None,
    },
    "drag_drop": {
        "source": lambda m: m.group(2).strip(),
        "target": lambda m: m.group(3).strip(),
    },
    "system_ops": {
        "operation": lambda m: m.group(1),
        "target": lambda m: m.group(2) if m and m.lastindex >= 2 else "system",
    },
    "install_app": {
        "app_name": lambda m: m.group(2).strip(),
    },
}


def parse(text: str) -> Optional[Intent]:
    """
    Very simple rule based parser updated for multi-action commands.
    In future, this can call a local LLM for higher accuracy:
        - llama.cpp with a small model
        - Ollama+llama3:8b
        - OpenAI GPT4
    """
    text = text.strip()
    if not text:
        return None

    # Check for multiple connected actions (using 'and', 'then')
    # Use capture groups to preserve the connector and skip them
    parts = re.split(r"\s+(?:and|then)\s+", text, flags=re.I)
    multi_actions = []
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        matched = False
        for intent_name, pattern in PATTERNS.items():
            m = pattern.search(part)
            if m:
                extracted: Dict[str, Any] = {}
                for key, fn in EXTRACTORS.get(intent_name, {}).items():
                    try:
                        extracted[key] = fn(m)
                    except Exception as e:
                        logger.debug(f"Error extracting {key}: {e}")
                        continue
                logger.info(f"Parsed intent: {intent_name} from {part!r}")
                multi_actions.append(Intent(action=intent_name, targets=[m.group(0)], extra=extracted))
                matched = True
                break
        
        # Special case for multi-word apps like "file manager"
        if not matched and re.search(r"\b(open|launch|start)\b.*\b(file manager)\b", part, re.I):
            logger.info(f"Using special case for file manager from {part!r}")
            multi_actions.append(Intent(action="open_app_multiword", targets=[part], extra={"app_name": "file manager"}))
            matched = True
        if not matched:
            logger.warning(f"No intent matched for part: {part!r}")
    
    if len(multi_actions) == 1:
        return multi_actions[0]
    elif len(multi_actions) > 1:
        # For simplicity, just return the first action but with all actions stored
        intent = multi_actions[0]
        intent.extra["additional_actions"] = multi_actions[1:]
        return intent
    
    logger.warning(f"No intent matched for: {text!r}")
    return None


def generate_from_intent(intent: Intent) -> Optional[str]:
    """
    Translate simple intent to existing task call or small script.
    Example:
        open brave browser -> Browser automation + Playwright
        create template with ratio -> Template automation task
    """
    # Handle create_template intent with aspect ratio
    if intent.action == "create_template":
        template_type = intent.extra.get("template_type", "Instagram Story")
        ratio = intent.extra.get("ratio", "4:5")
        
        import json
        return json.dumps({
            "task": "canva_template", 
            "args": {
                "template": template_type,
                "ratio": ratio
            }
        })
    
# Handle navigation to a URL or domain (default to Brave path)
    if intent.action in ("navigate_to", "navigate_site"):
        import json
        url = intent.extra.get("url")
        if not url:
            site = intent.extra.get("site", "")
            if site and not site.startswith("http"):
                url = f"https://{site}"
            else:
                url = site
        if not url:
            logger.error("No URL or site extracted for navigation intent")
            return None
        return json.dumps({
            "task": "open_browser_and_visit",
            "args": {
                "browser": "brave",
                "url": url
            }
        })
    
    # Handle folder navigation (open_site_template pattern)
    if intent.action == "open_site_template":
        folder_name = intent.extra.get("site", "")
        if not folder_name:
            logger.error("No folder name extracted")
            return None
        
        import json
        return json.dumps({
            "task": "folder_operations",
            "args": {
                "operation": "go to",
                "name": folder_name
            }
        })
    
     # Handle file manager opening
    if intent.action == "open_folder_app":
        import json
        return json.dumps({
            "task": "folder_operations",
            "args": {"operation": "open", "name": str(Path.home())}
        })
    
    # Handle opening multi-word system apps
    if intent.action == "open_app_multiword":
        app_name = intent.extra.get("app_name", "")
        
        # Special cases for multi-word apps
        app_mappings = {
            "file manager": "folder_operations",
            "text editor": "system_text_editor",
            "system settings": "system_settings",
            "music player": "music_player",
            "photo viewer": "photo_viewer",
            "image viewer": "photo_viewer",
        }
        
        if app_name in app_mappings:
            task_name = app_mappings[app_name]
            import json
            
            if task_name == "folder_operations":
                return json.dumps({
                    "task": task_name,
                    "args": {"operation": "open", "name": str(Path.home())}
                })
            else:
                # For multi-word apps, use the generic system app launcher
                return json.dumps({
                    "task": "open_system_app",
                    "args": {"app_name": app_name}
                })
        
        # If not mapped, use generic launcher
        import json
        return json.dumps({
            "task": "open_system_app",
            "args": {"app_name": app_name}
        })
    
    # Handle opening single-word apps
    if intent.action.startswith("open_app"):
        app_name = intent.extra.get("app_name", "")
        
        # Special cases for single-word apps
        app_mappings = {
            "nautilus": "folder_operations",
            "terminal": "system_terminal",
            "calculator": "system_calculator",
            "gedit": "system_text_editor",
            "firefox": " Firefox should use browser automation",
            "brave": " Brave should use browser automation",
            "chrome": "Chrome should use browser automation",
        }
        
        # Skip browser apps since they have their own handlers
        if app_name in ("firefox", "brave", "chrome"):
            return None
            
        if app_name in ("nautilus",):
            import json
            return json.dumps({
                "task": "folder_operations",
                "args": {"operation": "open", "name": str(Path.home())}
            })
        
        # If not mapped, use generic launcher
        import json
        return json.dumps({
            "task": "open_system_app",
            "args": {"app_name": app_name}
        })

    # Handle Firefox/Chromium browser open
    if intent.action == "open_browser":
        browser_type = intent.extra.get("browser_type", "")
        # Map user-friendly names to our task file names or browser IDs
        mapping = {
            "brave": "brave",
            "chrome": "chromium",
            "chromium": "chromium",
            "firefox": "firefox",
            "edge": "msedge",
        }
        internal = mapping.get(browser_type)
        if internal is None:
            logger.error(f"Unsupported browser: {browser_type}")
            return None
        
        # Return a call to our browser visit task; will be run by our executor
        url = "https://www.google.com"  # default page
        import json
        return json.dumps({"task": "open_browser_and_visit", "args": {"browser": internal, "url": url}})
    
    if intent.action == "click":
        target = intent.extra.get("target")
        if not target:
            return None
        # Simple selector mapping for common elements
        sel_map = {
            "login": 'button:has-text("Login")',
            "submit": 'button[type="submit"]',
            "search": 'input[placeholder*="search" i]',
        }
        selector = sel_map.get(target.lower(), f'text={target}')
        import json
        return json.dumps({"task": "browser_action", "args": {"actions": [{"type": "click", "selector": selector}]}})

    if intent.action == "type":
        text = intent.extra.get("text")
        where = intent.extra.get("where")
        if not text or not where:
            return None
        # Map generic descriptor to selector
        where_map = {
            "username": 'input[type="text"], input[name="username"]',
            "password": 'input[type="password"]',
            "email": 'input[type="email"], input[name="email"]',
        }
        selector = where_map.get(where.lower(), f'placeholder={where}')
        import json
        return json.dumps({"task": "browser_action", "args": {"actions": [{"type": "type", "selector": selector, "text": text}]}})
    
    if intent.action == "wait":
        # Extract duration and convert to seconds
        duration = intent.extra.get("duration", 2.0)
        unit = intent.extra.get("unit", "seconds")
        if unit.startswith("min"):
            duration *= 60
        
        import json
        return json.dumps({
            "task": "wait_seconds", 
            "args": {"seconds": duration}
        })
    
    if intent.action == "scroll":
        direction = intent.extra.get("direction", "down")
        count = intent.extra.get("count", 1)
        
        import json
        return json.dumps({
            "task": "browser_action", 
            "args": {
                "actions": [{
                    "type": "scroll",
                    "direction": direction,
                    "count": count
                }]
            }
        })
    
    if intent.action == "screenshot":
        import json
        return json.dumps({
            "task": "system_screenshot", 
            "args": {}
        })
    
    if intent.action == "file_ops":
        operation = intent.extra.get("operation")
        file_name = intent.extra.get("file_name")
        
        # Map operation to task
        operation_map = {
            "delete": "delete_file",
            "remove": "delete_file",
            "move": "move_file",
            "copy": "copy_file",
            "rename": "rename_file"
        }
        
        task = operation_map.get(operation, "file_ops")
        import json
        return json.dumps({
            "task": task,
            "args": {"file_name": file_name, "operation": operation}
        })
    
    if intent.action == "folder_ops":
        operation = intent.extra.get("operation")
        folder_name = intent.extra.get("folder_name")
        
        import json
        return json.dumps({
            "task": "folder_operations", 
            "args": {"name": folder_name, "operation": operation}
        })
    
    if intent.action == "window_ops":
        operation = intent.extra.get("operation")
        window_name = intent.extra.get("window_name")
        
        import json
        return json.dumps({
            "task": "window_management", 
            "args": {"operation": operation, "window": window_name}
        })
    
    if intent.action == "search":
        query = intent.extra.get("query")
        location = intent.extra.get("location")
        
        import json
        return json.dumps({
            "task": "search_content", 
            "args": {"query": query, "location": location}
        })
    
    if intent.action == "hotkey":
        key1 = intent.extra.get("key1")
        key2 = intent.extra.get("key2")
        key3 = intent.extra.get("key3")
        
        # Build hotkey list
        hotkey = [k for k in [key1, key2, key3] if k]
        
        import json
        return json.dumps({
            "task": "system_hotkey", 
            "args": {"keys": hotkey}
        })
    
    if intent.action == "drag_drop":
        source = intent.extra.get("source")
        target = intent.extra.get("target")
        
        import json
        return json.dumps({
            "task": "drag_and_drop", 
            "args": {"source": source, "target": target}
        })
    
    if intent.action == "system_ops":
        operation = intent.extra.get("operation")
        
        import json
        return json.dumps({
            "task": "system_operation",
            "args": {"operation": operation}
        })
    
    if intent.action == "install_app":
        app_name = intent.extra.get("app_name")
        
        import json
        return json.dumps({
            "task": "install_app",
            "args": {"app_name": app_name, "password": "rgukt"}
        })
    
    logger.error(f"Unhandled intent action: {intent.action}")
    return None


if __name__ == "__main__":
    # Simple test harness
    tests = [
        "open brave browser",
        "open canva design",
        "click login button",
        "type admin in username field",
        "wait 2 seconds",
    ]
    for t in tests:
        intent = parse(t)
        print(f"Input: {t}\nIntent: {intent}\nGenerated: {generate_from_intent(intent)}\n")