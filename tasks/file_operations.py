"""
File operations — delete, move, copy, rename.

Decision flow (Klavaro pattern):
  1. Sanity-check all required args (non-empty, valid operation)
  2. Verify source EXISTS before acting (abort if not)
  3. For move/copy: verify destination parent is writable
  4. Perform the operation
  5. Verify the operation succeeded (file gone / file present at dest)
  6. Abort with clear message if post-verify fails

Args:
    operation (str):    delete | remove | move | copy | rename
    file_name (str):    Source file path (absolute or ~-relative).
    destination (str):  Destination path (required for move / copy).
    new_name (str):     New filename in same directory (required for rename).
"""
import shutil
from pathlib import Path

from loguru import logger

from core.logger import finish, notify, start
from core.safety_guard import assert_safe_to_delete


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "file_operations"
    start(task_name)
    try:
        file_name   = str(args.get("file_name",   "")).strip()
        operation   = str(args.get("operation",   "")).strip().lower()
        destination = str(args.get("destination", "")).strip()
        new_name    = str(args.get("new_name",    "")).strip()

        # ── Sanity check ──────────────────────────────────────────────────────
        if not file_name:
            raise ValueError("'file_name' is required")
        if not operation:
            raise ValueError("'operation' is required: delete | remove | move | copy | rename")

        valid_ops = {"delete", "remove", "move", "copy", "rename"}
        if operation not in valid_ops:
            raise ValueError(f"Unknown operation {operation!r}. Valid: {sorted(valid_ops)}")

        src = Path(file_name).expanduser().resolve()

        # ── Pre-act verify: source must exist ─────────────────────────────────
        if not src.exists():
            raise FileNotFoundError(
                f"Source not found: {src}\n"
                f"Check the path and try again."
            )

        # ── delete / remove ───────────────────────────────────────────────────
        if operation in ("delete", "remove"):
            # SAFETY: defense-in-depth even though unlink() can only ever
            # remove a single file (it raises on directories, so it can't
            # recurse into a whole tree the way rmtree can) — still refuse
            # anything resolving to a protected system path.
            assert_safe_to_delete(src)
            src.unlink()
            # Post-verify
            if src.exists():
                raise RuntimeError(f"Delete appeared to succeed but file still exists: {src}")
            logger.info(f"✅ Deleted: {src}")
            notify(f"Deleted: {src.name}")

        # ── move ──────────────────────────────────────────────────────────────
        elif operation == "move":
            if not destination:
                raise ValueError("'destination' is required for 'move'")
            dst = Path(destination).expanduser()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            # Post-verify
            dst_final = dst if dst.is_file() else dst / src.name
            if not dst_final.exists():
                raise RuntimeError(f"Move appeared to succeed but destination not found: {dst_final}")
            if src.exists():
                logger.warning(f"Source still exists after move (may be a copy): {src}")
            logger.info(f"✅ Moved: {src.name} → {dst}")
            notify(f"Moved: {src.name} → {dst}")

        # ── copy ──────────────────────────────────────────────────────────────
        elif operation == "copy":
            if not destination:
                raise ValueError("'destination' is required for 'copy'")
            dst = Path(destination).expanduser()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            # Post-verify
            dst_final = dst if dst.is_file() else dst / src.name
            if not dst_final.exists():
                raise RuntimeError(f"Copy appeared to succeed but destination not found: {dst_final}")
            logger.info(f"✅ Copied: {src.name} → {dst}")
            notify(f"Copied: {src.name} → {dst}")

        # ── rename ────────────────────────────────────────────────────────────
        elif operation == "rename":
            if not new_name:
                raise ValueError("'new_name' is required for 'rename'")
            dst = src.parent / new_name
            if dst.exists():
                raise FileExistsError(f"Cannot rename — target already exists: {dst}")
            src.rename(dst)
            # Post-verify
            if not dst.exists():
                raise RuntimeError(f"Rename appeared to succeed but new file not found: {dst}")
            if src.exists():
                raise RuntimeError(f"Rename appeared to succeed but old file still exists: {src}")
            logger.info(f"✅ Renamed: {src.name} → {dst.name}")
            notify(f"Renamed: {src.name} → {dst.name}")

        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        tmp.write(b"test")
        tmp_name = tmp.name
    r = setup()
    execute({"file_name": tmp_name, "operation": "delete"}, r)
    cleanup(r)
