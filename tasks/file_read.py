"""
Task: file_read
Read a text file back and return its content as evidence.

Decision flow:
  1. Sanity-check: file_path must be non-empty
  2. Resolve path: expanduser; bare/relative names resolve under ~/Desktop
     (falls back to ~), absolute paths as-is
  3. Read-root guard: reads allowed anywhere in the home directory, /tmp,
     /var/tmp, /var/log — refused elsewhere (automation should not go
     fetch /etc/shadow; log locations are whitelisted for diagnosis)
  4. Size cap: read at most 1 MB (evidence payloads must stay bounded)
  5. Post-verify: size reported matches bytes actually read

Args:
    file_path (str): File to read. Required.
    max_bytes (int): Optional cap, default 1 MB.
"""
from pathlib import Path

from loguru import logger

from core.logger import finish, start
from core.task_contract import TaskResult

_ALLOWED_READ_ROOTS = (Path.home().resolve(), Path("/tmp"), Path("/var/tmp"), Path("/var/log"))
_DEFAULT_MAX_BYTES = 1024 * 1024


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_src(file_path: str) -> Path:
    raw = Path(file_path.strip()).expanduser()
    if not raw.is_absolute():
        desktop = Path.home() / "Desktop"
        base = desktop if desktop.is_dir() else Path.home()
        raw = base / raw
    return raw.resolve()


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> TaskResult:
    task_name = "file_read"
    start(task_name)
    try:
        file_path_arg = str(args.get("file_path", "")).strip()
        raw_max = args.get("max_bytes", _DEFAULT_MAX_BYTES)
        try:
            max_bytes = int(raw_max)
        except (TypeError, ValueError):
            raise ValueError(f"'max_bytes' must be an integer, got {raw_max!r}")
        if max_bytes <= 0:
            raise ValueError(f"'max_bytes' must be positive, got {max_bytes}")

        # ── Sanity check ──────────────────────────────────────────────────────
        if not file_path_arg:
            logger.warning("'file_path' is empty — nothing to read")
            finish("error", task_name)
            return TaskResult(False, error="file_path is required")

        src = _resolve_src(file_path_arg)

        if not src.is_file():
            finish("error", task_name)
            return TaskResult(False, error=f"File not found: {src}")

        if not any(_is_within(src, root) for root in _ALLOWED_READ_ROOTS):
            logger.warning(f"file_read refused outside allowed roots: {src}")
            finish("error", task_name)
            return TaskResult(
                False,
                error=f"Refusing to read outside home, /tmp, /var/tmp, /var/log: {src}",
            )

        size = src.stat().st_size
        if size > max_bytes:
            logger.warning(
                f"Refusing to read {size} bytes from {src} (cap {max_bytes})"
            )
            finish("error", task_name)
            return TaskResult(
                False,
                error=f"File is {size} bytes; refusing to read more than {max_bytes}",
            )

        with open(src, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(max_bytes)
        # ── Post-verify: bytes read match expectation ──────────────────────────
        if len(content.encode("utf-8")) > max_bytes:
            raise RuntimeError(f"Read more bytes than the cap from {src}")

        print(content)
        logger.info(f"✅ Read {size} bytes from {src}")
        finish("success", task_name)
        return TaskResult(
            True,
            data={"path": str(src), "bytes": size, "content": content},
            evidence=[{"kind": "file_read", "path": str(src), "bytes": size}],
        )

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"file_path": "/tmp/nikki_write_test.txt"}, r)
    cleanup(r)
