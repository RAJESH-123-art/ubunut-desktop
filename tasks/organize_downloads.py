"""
Organise the Downloads folder by file type.

Decision flow (Klavaro pattern):
  1. Sanity-check: Downloads folder must exist
  2. Sanity-check: at least one subfolder config OR move_videos must be set
  3. Process files, collecting moved/removed counts
  4. Verify: confirm moved files now exist at destination
  5. Report final summary with counts

Args:
    create_subfolders (dict):          {folder_name: [extension, ...]}
    move_videos (bool):                Move videos to Videos/ (default True)
    delete_junk (bool):                Delete junk patterns (default False)
    junk_patterns (list[str]):         Glob patterns (default ["*.tmp","*.crdownload"])
    delete_older_than_days (int|None): Remove sub-dirs older than N days (default None)
"""
import shutil
from datetime import datetime, timedelta, timezone
from typing import List

from loguru import logger

from core.logger import finish, notify, start, telegram
from core.safety_guard import assert_safe_to_delete
from core.system_utils import ensure_dir, user_downloads

VIDEO_EXTS = {"mp4", "webm", "mkv", "avi", "mov", "flv", "m4v", "wmv"}


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "organize_downloads"
    start(task_name)
    try:
        config:        dict      = args.get("create_subfolders", {})
        move_videos:   bool      = bool(args.get("move_videos", True))
        delete_junk:   bool      = bool(args.get("delete_junk", False))
        junk_patterns: List[str] = args.get("junk_patterns", ["*.tmp", "*.crdownload"])
        older_than_days          = args.get("delete_older_than_days", None)

        # ── Sanity check ──────────────────────────────────────────────────────
        down = user_downloads()
        if not down.is_dir():
            raise FileNotFoundError(
                f"Downloads folder not found: {down}\n"
                f"Create it first or check your system configuration."
            )

        if not config and not move_videos and not delete_junk:
            raise ValueError(
                "Nothing to do — set at least one of: "
                "create_subfolders, move_videos=True, delete_junk=True"
            )

        # ── Build extension → folder map ──────────────────────────────────────
        ext_to_folder = {
            ext.lower(): folder
            for folder, exts in config.items()
            for ext in exts
        }

        # Create configured subfolders upfront
        for folder in config:
            ensure_dir(down / folder)
        if move_videos:
            ensure_dir(down / "Videos")

        moved = removed = errors = 0

        # ── Process files ─────────────────────────────────────────────────────
        for path in list(down.iterdir()):
            if path.is_file():
                ext = path.suffix.lstrip(".").lower()

                # Move to typed subfolder
                dest_folder = ext_to_folder.get(ext)
                if dest_folder:
                    dst = down / dest_folder / path.name
                    try:
                        shutil.move(str(path), str(dst))
                        # Post-verify each move
                        if dst.exists():
                            moved += 1
                            logger.debug(f"Moved: {path.name} → {dest_folder}/")
                        else:
                            logger.warning(f"Move reported OK but {dst} not found")
                            errors += 1
                    except Exception as exc:
                        logger.warning(f"Could not move {path.name}: {exc}")
                        errors += 1

                # Move videos to Videos/
                elif move_videos and ext in VIDEO_EXTS:
                    dst = down / "Videos" / path.name
                    try:
                        shutil.move(str(path), str(dst))
                        if dst.exists():
                            moved += 1
                            logger.debug(f"Video moved: {path.name} → Videos/")
                        else:
                            errors += 1
                    except Exception as exc:
                        logger.warning(f"Could not move video {path.name}: {exc}")
                        errors += 1

            # Remove old subdirectories
            elif path.is_dir() and older_than_days:
                try:
                    age = datetime.now(tz=timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                    if age > timedelta(days=int(older_than_days)):
                        # SAFETY: restrict_to_home=True since this loop only
                        # ever iterates direct children of the resolved
                        # Downloads folder, but the extra check costs
                        # nothing and guards against a future refactor
                        # accidentally widening the iteration scope.
                        assert_safe_to_delete(path, restrict_to_home=True)
                        shutil.rmtree(path)
                        if not path.exists():
                            removed += 1
                            logger.info(f"Removed old dir: {path.name}")
                        else:
                            errors += 1
                except Exception as exc:
                    logger.warning(f"Could not remove dir {path.name}: {exc}")
                    errors += 1

        # ── Delete junk files ─────────────────────────────────────────────────
        if delete_junk:
            for pattern in junk_patterns:
                for path in down.glob(pattern):
                    if path.is_file():
                        try:
                            path.unlink()
                            if not path.exists():
                                removed += 1
                            else:
                                errors += 1
                        except Exception as exc:
                            logger.warning(f"Could not delete {path.name}: {exc}")
                            errors += 1

        # ── Summary ───────────────────────────────────────────────────────────
        status = "success" if errors == 0 else "partial"
        msg = (
            f"Downloads organised — "
            f"moved: {moved}, removed: {removed}"
            + (f", errors: {errors}" if errors else "")
        )
        logger.info(f"{'✅' if errors == 0 else '⚠️'} {msg}")
        notify(msg)
        telegram(f"🗂️ {msg}")

        finish(status, task_name)
        # Report success only when nothing actually failed — previously this
        # returned True whenever ANY file moved, even with errors present,
        # masking real partial failures from callers checking the bool
        # (see TASK_KNOWLEDGE_BASE.md Part 5, fix #7).
        return errors == 0

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({
        "create_subfolders": {
            "Images":    ["jpg", "jpeg", "png", "gif", "webp", "bmp"],
            "Documents": ["pdf", "doc", "docx", "txt", "rtf", "odt"],
            "Archives":  ["zip", "tar", "gz", "7z", "rar"],
            "Code":      ["py", "js", "ts", "sh", "yaml", "json"],
        },
        "move_videos": True,
        "delete_junk": True,
        "junk_patterns": ["*.tmp", "*.crdownload", "*.part"],
        "delete_older_than_days": 30,
    }, r)
    cleanup(r)
