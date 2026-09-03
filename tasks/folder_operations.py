"""
Folder operations — open, create, delete.

Decision flow (Klavaro pattern):
  1. Sanity-check all args (non-empty name + valid operation)
  2. For open/delete: verify folder EXISTS first (abort if not)
  3. Perform the operation
  4. Verify the operation succeeded (folder gone / folder present)
  5. Abort with clear message if post-verify fails

Args:
    operation (str): open | create | delete
    name (str):      Folder path or well-known alias:
                     Downloads, Documents, Pictures, Music, Videos, Desktop
"""
import shutil
import subprocess
from pathlib import Path

from loguru import logger

from core.logger import finish, notify, start
from core.safety_guard import assert_safe_to_delete

_SPECIAL = {"downloads", "documents", "pictures", "music", "videos", "desktop"}


def _resolve(name: str) -> Path:
    if name.lower() in _SPECIAL:
        return Path.home() / name.capitalize()
    p = Path(name).expanduser()
    return p if p.is_absolute() else Path.home() / p


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "folder_operations"
    start(task_name)
    try:
        operation   = str(args.get("operation", "")).strip().lower()
        folder_name = str(args.get("name",      "")).strip()

        # ── Sanity check ──────────────────────────────────────────────────────
        if not folder_name:
            raise ValueError("'name' (folder path or alias) is required")
        if not operation:
            raise ValueError("'operation' is required: open | create | delete")
        valid_ops = {"open", "create", "delete"}
        if operation not in valid_ops:
            raise ValueError(f"Unknown operation {operation!r}. Valid: {sorted(valid_ops)}")

        folder_path = _resolve(folder_name)

        # ── open ──────────────────────────────────────────────────────────────
        if operation == "open":
            if not folder_path.exists():
                raise FileNotFoundError(f"Folder not found: {folder_path}")
            if not folder_path.is_dir():
                raise NotADirectoryError(f"Path is not a folder: {folder_path}")
            subprocess.Popen(["xdg-open", str(folder_path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"✅ Opened: {folder_path}")
            notify(f"Opened folder: {folder_path.name}")

        # ── create ────────────────────────────────────────────────────────────
        elif operation == "create":
            folder_path.mkdir(parents=True, exist_ok=True)
            # Post-verify
            if not folder_path.is_dir():
                raise RuntimeError(f"Create appeared to succeed but folder not found: {folder_path}")
            logger.info(f"✅ Created: {folder_path}")
            notify(f"Created folder: {folder_path.name}")

        # ── delete ────────────────────────────────────────────────────────────
        # ── delete ──────────────────────────────────────────
        elif operation == "delete":
            if not folder_path.exists():
                raise FileNotFoundError(f"Folder not found: {folder_path}")
            if not folder_path.is_dir():
                raise NotADirectoryError(f"Path is not a folder: {folder_path}")
            # SAFETY: refuse to recursively delete a protected system path,
            # the home directory itself, or anything suspiciously shallow —
            # a misresolved `name` (e.g. "~", "/", empty-alias fallback)
            # must never reach shutil.rmtree() unchecked. This is the most
            # dangerous single operation in the whole task suite.
            assert_safe_to_delete(folder_path)
            shutil.rmtree(folder_path)
            # Post-verify
            if folder_path.exists():
                raise RuntimeError(f"Delete appeared to succeed but folder still exists: {folder_path}")
            logger.info(f"✅ Deleted: {folder_path}")
            notify(f"Deleted folder: {folder_path.name}")

        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"operation": "open", "name": "Downloads"}, r)
    cleanup(r)
