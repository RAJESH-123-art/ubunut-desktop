#!/usr/bin/env python3
"""
CLI dashboard for Linux Desktop Automation.
List, run, inspect tasks, and view logs.
"""

import json
import time
import argparse
import sys
import os
from pathlib import Path
from typing import List

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config_loader import load_config, get_section
from tasks import discover_tasks, list_tasks, task_info, get_task
from core.system_utils import env_check

def console_menu():
    """Interactive menu for picking actions."""
    while True:
        print("\n--- Desktop Automation CLI ---")
        print("1) List tasks")
        print("2) Run a task")
        print("3) Run workflow")
        print("4) Show environment")
        print("5) Show recent logs")
        print("6) Exit")
        try:
            choice = input("Select [1-6]: ").strip()
            if choice == "1":
                print("\nDiscovered tasks:")
                for name in sorted(list_tasks()):
                    info = task_info(name)
                    doc = info.get("docstring", "").splitlines()[0] if info.get("docstring") else ""
                    print(f"- {name}: {doc}")
            elif choice == "2":
                name = input("Task name: ").strip()
                args_input = input("Args (JSON or empty): ").strip()
                args = json.loads(args_input) if args_input else {}
                print(f"Running {name}...")
                from automation import run_task
                result = run_task(name, args)
                print("Success" if result else "Failed")
            elif choice == "3":
                wf_name = input("Workflow name: ").strip()
                wf_file = input("Workflow file (defaults config/workflow.yaml) or empty: ").strip() or "config/workflow.yaml"
                print(f"Running workflow {wf_name} from {wf_file}...")
                from automation import run_workflow
                result = run_workflow(wf_name, wf_file)
                print("Workflow finished" if result else "Workflow failed")
            elif choice == "4":
                print("\nEnvironment / capabilities:")
                print(json.dumps(env_check(), indent=2))
            elif choice == "5":
                logs_path = Path("logs/automation.log")
                if logs_path.is_file():
                    print("\nLast 10 lines of automation.log:")
                    with open(logs_path, 'r') as f:
                        lines = f.readlines()
                        for line in lines[-10:]:
                            print(line.rstrip())
                else:
                    print("No log file found.")
            elif choice == "6":
                break
            else:
                print("Invalid choice.")
        except Exception as exc:
            print(f"Error: {exc}")

def list_tasks_cmd():
    """CLI: list tasks with docstring."""
    print("Discovered tasks:")
    for name in sorted(list_tasks()):
        info = task_info(name)
        doc = (info.get("docstring") or "").splitlines()[0] if info.get("docstring") else ""
        print(f"- {name}: {doc}")

def log_viewer(lines: int = 20):
    """Pretty print log lines, optionally number and paginate."""
    logs_path = Path("logs/automation.log")
    if not logs_path.is_file():
        print("log file not found")
        return
    with open(logs_path, 'r') as f:
        tail = f.readlines()[-lines:]
        for i, line in enumerate(tail, start=len(tail)-lines+1):
            print(f"{i}: {line.rstrip()}")

def main():
    parser = argparse.ArgumentParser(description="Desktop Automation CLI Dashboard")
    parser.add_argument("--menu", action="store_true", help="Interactive menu")
    parser.add_argument("--list", action="store_true", help="List tasks")
    parser.add_argument("--logs", type=int, default=None, metavar="N", help="Show last N log lines")
    args = parser.parse_args()

    if args.menu:
        console_menu()
    elif args.list:
        list_tasks_cmd()
    elif args.logs is not None:
        log_viewer(args.logs)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()