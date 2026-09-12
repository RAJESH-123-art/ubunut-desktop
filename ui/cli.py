#!/usr/bin/env python3
"""
CLI dashboard for Linux Desktop Automation.
List, run, inspect tasks, and view logs.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.action_policy import describe_action, requires_approval
from core.system_utils import env_check
from tasks import get_task, list_tasks, task_info


def _confirm_action(name: str, args: dict) -> bool:
    """Require an exact interactive confirmation for consequential tasks."""
    if not requires_approval(name, args):
        return True
    print(f"Approval required: {describe_action(name, args)}")
    print(f"Exact task: {name}  args: {json.dumps(args, sort_keys=True, default=str)}")
    return input("Execute this exact action? Type 'yes': ").strip().lower() == "yes"


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
                if not isinstance(args, dict):
                    raise ValueError("Task args must be a JSON object")
                if not _confirm_action(name, args):
                    print("Cancelled.")
                    continue
                if name == "run_command":
                    args["authorized"] = True
                elif name == "system_power":
                    args["confirm"] = True
                print(f"Running {name}...")
                mod = get_task(name)
                if not mod:
                    print(f"Task '{name}' not found. Use option 1 to list tasks.")
                else:
                    resources = mod.setup()
                    try:
                        result = mod.execute(args, resources)
                    finally:
                        mod.cleanup(resources)
                    print("Success" if result else "Failed")
            elif choice == "3":
                wf_name = input("Workflow name: ").strip()
                wf_file = input("Workflow file (defaults config/workflow.yaml) or empty: ").strip() or "config/workflow.yaml"
                approve_all = input(
                    "Allow consequential steps defined by this workflow? Type 'yes' to allow, or press Enter to refuse them: "
                ).strip().lower() == "yes"
                print(f"Running workflow {wf_name} from {wf_file}...")
                from core.workflow_engine import WorkflowEngine
                result = WorkflowEngine(approve_all=approve_all).run(wf_name, wf_file)
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