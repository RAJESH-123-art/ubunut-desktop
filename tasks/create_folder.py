"""
Create a new folder / directory.

Decision flow:
  1. Sanity-check: folder_name must be non-empty
  2. Resolve location: use args["location"] if provided, else ~/Desktop,
     falling back to ~ if the Desktop directory does not exist
  3. Build full path = location / folder_name
  4. mkdir(parents=True, exist_ok=True)
  5. Post-verify: confirm directory exists
  6. Print and log the created path

Args:
    folder_name (str): Name of the folder to create. Required.
    location (str):    Optional parent directory path.
                       Defaults to ~/Desktop (falls back to ~ if Desktop absent).
"""
from pathlib import Path

from loguru import logger

from core.logger import finish, notify, start
from core.task_contract import TaskResult


def _resolve_location(location_arg: str) -> Path:
    """
    Return the parent directory to create the folder in.

    Priority:
      1. args["location"] if provided and non-empty
      2. ~/Desktop if it exists
      3. ~ (home directory)
    """
    if location_arg:
        return Path(location_arg).expanduser()

    desktop = Path.home() / "Desktop"
    if desktop.exists() and desktop.is_dir():
        return desktop

    logger.debug("~/Desktop not found — defaulting location to home directory")
    return Path.home()


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> TaskResult:
    task_name = "create_folder"
    start(task_name)
    try:
        folder_name  = str(args.get("folder_name", "")).strip()
        location_arg = str(args.get("location",    "")).strip()

        # ── Sanity check ──────────────────────────────────────────────────────
        if not folder_name:
            logger.warning("'folder_name' is empty — nothing to create")
            finish("error", task_name)
            return TaskResult(False, error="folder_name is required")

        name_path = Path(folder_name)
        if name_path.is_absolute() or len(name_path.parts) != 1 or folder_name in (".", ".."):
            raise ValueError("'folder_name' must be one plain directory name, not a path")

        location = _resolve_location(location_arg).resolve()
        full_path = location / folder_name

        logger.info(f"Creating folder: {full_path}")
        full_path.mkdir(parents=True, exist_ok=True)

        # ── Post-verify ───────────────────────────────────────────────────────
        if not full_path.is_dir():
            raise RuntimeError(
                f"mkdir appeared to succeed but directory not found: {full_path}"
            )

        print(f"📁 Created folder: {full_path}")
        logger.info(f"✅ Folder created: {full_path}")
        notify(f"Created folder: {folder_name}")
        finish("success", task_name)
        return TaskResult(
            True,
            data={"path": str(full_path)},
            evidence=[{"kind": "directory", "path": str(full_path)}],
        )

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"folder_name": "TestFolder"}, r)
    cleanup(r)
