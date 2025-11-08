#!/usr/bin/env python3
"""
Desktop Automation System - Main entry point.
Run single tasks or full workflows.
"""

import sys
import argparse
import json
import signal
import time
from pathlib import Path

from config.config_loader import load_config, get_section
from tasks import discover_tasks, get_task, list_tasks, task_info
from core.gui_controller import GUIController
from core.vision_engine import VisionEngine
from core.system_utils import env_check
from core.logger import notify, start as log_start, finish as log_finish, get_system_info

def signal_handler(signum, frame):
    """Graceful shutdown on SIGINT/SIGTERM."""
    print("\nReceived interrupt, shutting down...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def run_task(task_name: str, args: dict) -> bool:
    """Prepare resources, run a single task, and clean up."""
    # Load config for GUI and vision
    cfg = load_config()
    gui_cfg = get_section("gui", cfg)
    vision_cfg = get_section("vision", cfg)
    
    # Set up resources
    resources = {
        'gui': GUIController(safe_mode=gui_cfg.get('safe_mode', True)),
        'vision': VisionEngine(),
        'config': cfg
    }
    # Call task setup
    mod = get_task(task_name)
    if not mod:
        print(f"Task '{task_name}' not found.")
        return False
    task_resources = mod.setup()
    resources.update(task_resources)

    try:
        result = mod.execute(args, resources)
    finally:
        mod.cleanup(resources)
    return result

def run_workflow(workflow_name: str, workflow_file: str) -> bool:
    """Load workflow YAML and run each task in order."""
    import yaml
    wf_path = Path(workflow_file)
    if not wf_path.is_file():
        print(f"Workflow file not found: {workflow_file}")
        return False
    with open(wf_path, 'r') as f:
        workflows = yaml.safe_load(f)
    if workflow_name not in workflows:
        print(f"Workflow '{workflow_name}' not defined in {workflow_file}")
        return False
    wf = workflows[workflow_name]
    tasks_seq = wf.get('tasks', [])
    on_error = wf.get('on_error', 'ignore')
    workflow_timeout = wf.get('timeout', None)
    start_time = time.time()
    # Prepare shared resources across tasks
    cfg = load_config()
    resources = {
        'gui': GUIController(safe_mode=cfg.get('gui', {}).get('safe_mode', True)),
        'vision': VisionEngine(),
        'config': cfg
    }
    try:
        for step in tasks_seq:
            task_name = step.get('task')
            args = step.get('args', {})
            retries = step.get('retries', 0)
            print(f"-> Running task: {task_name}")
            success = False
            while retries >= 0 and not success:
                try:
                    mod = get_task(task_name)
                    if not mod:
                        raise RuntimeError(f"Task not found: {task_name}")
                    # Setup/execute/cleanup per task
                    task_res = mod.setup()
                    all_res = dict(resources)
                    all_res.update(task_res)
                    mod.execute(args, all_res)
                    mod.cleanup(all_res)
                    success = True
                except Exception as exc:
                    retries -= 1
                    if retries < 0:
                        print(f"Task {task_name} failed: {exc}")
                        if on_error == 'abort':
                            raise
                    else:
                        print(f"Retrying {task_name} ({retries} left) after {exc}")
                        time.sleep(2)
            if workflow_timeout and (time.time() - start_time) > workflow_timeout:
                print("Workflow timeout reached")
                break
        return True
    except Exception as exc:
        print(f"Workflow failed: {exc}")
        return False

def list_all_tasks():
    """Print discovered tasks with short description."""
    for name in sorted(list_tasks()):
        info = task_info(name)
        desc = info.get('docstring', '')
        print(f"{name}: {desc}")

def main():
    parser = argparse.ArgumentParser(description="Linux Desktop Automation")
    subparsers = parser.add_subparsers(dest="cmd", required=True, help="commands")
    
    run_parser = subparsers.add_parser("run", help="Run a single task")
    run_parser.add_argument("task", help="Task name")
    run_parser.add_argument("--args", help="JSON args dict")
    
    workflow_parser = subparsers.add_parser("workflow", help="Run defined workflow")
    workflow_parser.add_argument("name", help="Workflow name")
    workflow_parser.add_argument("--file", default="config/workflow.yaml", help="Workflow file")
    
    subparsers.add_parser("list", help="List discovered tasks")
    subparsers.add_parser("env", help="Print environment / capability detection")
    
    args = parser.parse_args()
    
    if args.cmd == "list":
        list_all_tasks()
        return
    elif args.cmd == "env":
        print(json.dumps(env_check(), indent=2))
        return

    if args.cmd == "run":
        cli_args = {}
        if args.args:
            cli_args = json.loads(args.args)
        result = run_task(args.task, cli_args)
        sys.exit(0 if result else 1)
    elif args.cmd == "workflow":
        result = run_workflow(args.name, args.file)
        sys.exit(0 if result else 1)

if __name__ == "__main__":
    main()