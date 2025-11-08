"""
Sample automation task: organize Downloads folder by file type, archiving, and cleanup.
Demonstrates file operations and notification.
"""

import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from core.system_utils import move_file, ensure_dir, user_downloads, command, command_output
from core.logger import start, finish, notify, telegram

def setup():
    """Create resources if required."""
    print("Setting up: organize_downloads")
    return {}

def execute(args: dict, resources: dict):
    """Execute folder organization based on config file or CLI args."""
    task_name = "organize_downloads"
    start(task_name)
    try:
        # Load config
        config = args.get("create_subfolders", {})
        move_videos = args.get("move_videos", True)
        delete_junk = args.get("delete_junk", False)
        junk_patterns: List[str] = args.get("junk_patterns", ["*.tmp", "*.crdownload"])
        delete_older_than_days = args.get("delete_older_than_days", None)

        down = user_downloads()
        if not down.is_dir():
            raise FileNotFoundError(f"Downloads folder not found: {down}")

        # Create type subfolders
        for folder, extensions in config.items():
            target_dir = down / folder
            ensure_dir(target_dir)

        mapping = {ext.lower(): folder for folder, exts in config.items() for ext in exts}

        # Iterate and move files
        moved_archived = 0
        removed = 0
        for path in down.iterdir():
            if path.is_file():
                ext = path.suffix.lstrip('.').lower()
                if ext in mapping:
                    dest = down / mapping[ext] / path.name
                    try:
                        move_file(path, dest)
                        moved_archived += 1
                    except Exception as exc:
                        print(f"Failed to move {path.name}: {exc}")
                elif move_videos and ext in {"mp4", "webm", "mkv", "avi", "mov"}:
                    target_dir = down / "Videos"
                    ensure_dir(target_dir)
                    dest = target_dir / path.name
                    try:
                        move_file(path, dest)
                        moved_archived += 1
                    except Exception as exc:
                        print(f"Failed to move video {path.name}: {exc}")
            else:
                # Directories - check old age
                if delete_older_than_days:
                    try:
                        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
                        if age > timedelta(days=delete_older_than_days):
                            command(f"rm -rf {path}", shell=True)
                            removed += 1
                    except Exception as exc:
                        print(f"Failed to check/remove dir {path.name}: {exc}")

        # Remove junk matches
        if delete_junk:
            for pattern in junk_patterns:
                for path in down.glob(pattern):
                    try:
                        if path.is_file():
                            path.unlink()
                            removed += 1
                    except Exception as exc:
                        print(f"Failed to delete junk file {path.name}: {exc}")

        # Summary notification
        msg = f"Organize complete: moved {moved_archived}, removed {removed}"
        notify(msg)
        telegram(f"🗂️ {msg}")
        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict):
    """Optional post-task cleanup (e.g., archiving old logs)."""
    pass

if __name__ == "__main__":
    # Example usage demo
    args = {
        "create_subfolders": {
            "Images": ["jpg", "png", "gif", "webp", "bmp"],
            "Documents": ["pdf", "doc", "docx", "txt", "rtf"],
            "Archives": ["zip", "tar", "gz", "7z", "rar"],
            "Code": ["py", "js", "sh", "yaml", "json"],
        },
        "move_videos": True,
        "delete_junk": True,
        "junk_patterns": ["*.tmp", "*.crdownload", "*.part"],
        "delete_older_than_days": 30
    }
    r = setup()
    execute(args, r)
    cleanup(r)