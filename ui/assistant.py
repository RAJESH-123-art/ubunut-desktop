#!/usr/bin/env python3
"""
Assistant interface: parse natural language commands and execute automations.
Examples:
- open brave browser
- open canva design with Instagram Story template
- click login button then type admin in username field
"""

import json
import argparse
import sys
import os
from typing import Optional

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config_loader import load_config
from tasks import get_task
from core.intent_parser import parse, generate_from_intent
from core.logger import notify
from core.browser_manager import finalize_workflow

def main():
    parser = argparse.ArgumentParser(description="Automation Assistant")
    parser.add_argument("command", nargs="?", help="Natural language command")
    parser.add_argument("--file", help="Read commands from a file (one per line)")
    args = parser.parse_args()

    commands = []
    if args.file:
        try:
            with open(args.file, "r") as f:
                commands = [line.strip() for line in f if line.strip()]
        except Exception as e:
            print(f"Failed to read file: {e}")
            return

    if args.command:
        commands.insert(0, args.command)

    if not commands:
        parser.print_help()
        return

    for txt in commands:
        intent = parse(txt)
        if not intent:
            print(f"Could not understand: {txt}")
            continue
        print(f"Intent: {intent}")
        if intent.action == "open_app":
            # Special handling: platform-specific template handler
            app_name = intent.targets[0].split()[1]  # crude
            if app_name.lower() in {"canva", "figma", "vistacreate"}:
                # Build simple args for template_action.py
                tmpl_args = {"platform": app_name.lower()}
                # extract template name if present
                template_query = intent.extra  # for now, empty
                task_mod = get_task("template_action")
                if task_mod:
                    res = task_mod.setup()
                    try:
                        task_mod.execute(tmpl_args, res)
                    finally:
                        task_mod.cleanup(res)
                else:
                    print(f"Template task not available for {app_name}")
                continue
        # Regular path: JSON string -> our task runner
        # Check for multi-action intent
        if "additional_actions" in intent.extra:
            # Execute sequential actions
            print(f"Executing multi-action command: {len(intent.extra['additional_actions']) + 1} actions")
            
            # First action
            json_str = generate_from_intent(intent)
            if json_str:
                print(f"  1) {json_str}")
                try:
                    spec = json.loads(json_str)
                    task_name = spec.get("task")
                    if task_name:
                        mod = get_task(task_name)
                        if mod:
                            resources = mod.setup()
                            try:
                                mod.execute(spec.get("args", {}), resources)
                                # Keep browser resources for potential reuse
                            except Exception as e:
                                print(f"  Error: {e}")
                            finally:
                                mod.cleanup(resources)
                except Exception as e:
                    print(f"  JSON parsing error: {e}")
            
            # Additional actions
            for idx, add_intent in enumerate(intent.extra["additional_actions"], start=2):
                add_str = generate_from_intent(add_intent)
                if add_str:
                    print(f"  {idx}) {add_str}")
                    try:
                        spec = json.loads(add_str)
                        task_name = spec.get("task")
                        if task_name:
                            mod = get_task(task_name)
                            if mod:
                                resources = mod.setup()
                                try:
                                    mod.execute(spec.get("args", {}), resources)
                                except Exception as e:
                                    print(f"  Error: {e}")
                                finally:
                                    mod.cleanup(resources)
                    except Exception as e:
                        print(f"  JSON parsing error: {e}")
            # Close shared browser at workflow end
            finalize_workflow()
            notify(f"Completed multi-action command: {txt}")
        else:
            # Single action path
            json_str = generate_from_intent(intent)
            if not json_str:
                print(f"Cannot auto-generate actions for: {txt}")
                continue
            print(f"Executing: {json_str}")
            # Parse JSON structure for simple task+args format
            try:
                spec = json.loads(json_str)
                task_name = spec.get("task")
                if not task_name:
                    print(f"No task found in generated spec: {spec}")
                    continue
                mod = get_task(task_name)
                if not mod:
                    print(f"Task '{task_name}' not found")
                    continue
                args_json = spec.get("args", {})
                # Run the task
                resources = mod.setup()
                try:
                    mod.execute(args_json, resources)
                    notify(f"Completed: {txt}")
                finally:
                    mod.cleanup(resources)
            except Exception as e:
                print(f"Failed executing: {e}")

if __name__ == "__main__":
    main()