"""
Utility task to pause automation.
Useful between multi-step workflows where UI may need time to react.
"""

import time
from typing import Dict, Any
from loguru import logger

from core.logger import start, finish, notify

def setup():
    """No special resources needed."""
    return {}

def execute(args: dict, resources: dict):
    """Sleep for specified seconds."""
    task_name = "wait_seconds"
    start(task_name)
    seconds = int(args.get("seconds", 1))
    logger.info(f"Waiting {seconds} second(s)")
    time.sleep(seconds)
    finish("success", task_name)
    return True

def cleanup(resources: dict):
    """Nothing to clean up."""
    pass