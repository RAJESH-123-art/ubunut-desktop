"""
Task: file_write
Write text content to a file (overwrite or append).

Decision flow:
  1. Sanity-check: file_path and content must be present
  2. Resolve path: expanduser; bare/relative names resolve under ~/Desktop
     (falls back to ~), absolute paths are used as-is
  3. Write-root guard: resolved path must live inside ~, /tmp, or /var/tmp
     — refuse anything else (no /etc, /usr, /boot, ... writes from automation)
  4. Overwrite: atomic write (mkstemp → fsync → os.replace), then read back
     and compare byte-for-byte with the requested content
     Append: append + flush + fsync, then read back and confirm the chunk
     is present
  5. Post-verify failure ⇒ TaskResult(False) — never claim success without
     reading the file back

Args:
    file_path (str): Destination path. Required.
    content (str):   Text to write. Required (may be empty to truncate/create).
    append (bool):   Append instead of overwrite. Default False.
"""
from pathlib import Path

from loguru import logger

from core.atomic_write import atomic_write_text
from core.logger import finish, notify, start
from core.task_contract import TaskResult

_ALLOWED_WRITE_ROOTS = (Path.home().resolve(), Path("/tmp"), Path("/var/tmp"))
_MAX_CONTENT_BYTES = 10 * 1024 * 1024  # 10 MB sanity cap


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_dest(file_path: str) -> Path:
    """
    Resolve the destination path.
    Bare/relative names go to ~/Desktop (fallback ~); absolute paths as-is.
    """
    raw = Path(file_path.strip()).expanduser()
    if not raw.is_absolute():
        desktop = Path.home() / "Desktop"
        base = desktop if desktop.is_dir() else Path.home()
        raw = base / raw
    return raw.resolve()


def _assert_writable_location(path: Path) -> None:
    """Refuse writes outside the user's home, /tmp and /var/tmp."""
    if not any(_is_within(path, root) for root in _ALLOWED_WRITE_ROOTS):
        raise PermissionError(
            f"Refusing to write outside home, /tmp and /var/tmp: {path}"
        )


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> TaskResult:
    task_name = "file_write"
    start(task_name)
    try:
        file_path_arg = str(args.get("file_path", "")).strip()
        content = args.get("content", "")
        append = bool(args.get("append", False))

        # ── Sanity check ──────────────────────────────────────────────────────
        if not file_path_arg:
            logger.warning("'file_path' is empty — nothing to write")
            finish("error", task_name)
            return TaskResult(False, error="file_path is required")
        if not isinstance(content, str):
            content = str(content)
        if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            finish("error", task_name)
            return TaskResult(
                False,
                error=f"content exceeds {_MAX_CONTENT_BYTES} byte safety cap",
            )

        dest = _resolve_dest(file_path_arg)
        _assert_writable_location(dest)

        logger.info(f"{'Appending to' if append else 'Writing'} file: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)

        if append:
            with open(dest, "a", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                import os as _os
                _os.fsync(handle.fileno())
        else:
            atomic_write_text(dest, content)

        # ── Post-verify: read back ─────────────────────────────────────────────
        written = dest.read_text(encoding="utf-8")
        if append:
            if content not in written:
                raise RuntimeError(
                    f"Append appeared to succeed but content missing from {dest}"
                )
        elif written != content:
            raise RuntimeError(
                f"Write appeared to succeed but read-back differs for {dest}"
            )

        size = dest.stat().st_size
        print(f"💾 {'Appended to' if append else 'Wrote'} {dest} ({size} bytes)")
        logger.info(f"✅ File {'appended' if append else 'written'} and verified: {dest}")
        notify(f"{'Appended to' if append else 'Saved'}: {dest.name}")
        finish("success", task_name)
        return TaskResult(
            True,
            data={"path": str(dest), "bytes": size, "append": append},
            evidence=[{"kind": "file", "path": str(dest), "bytes": size,
                       "mode": "append" if append else "overwrite"}],
        )

    except PermissionError as exc:
        logger.warning(f"file_write refused: {exc}")
        finish("error", task_name)
        return TaskResult(False, error=str(exc))
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"file_path": "/tmp/nikki_write_test.txt", "content": "hello world"}, r)
    cleanup(r)
