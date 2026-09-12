"""
download_watcher.py — Vercept-level download completion detection.

Polls a directory (default ~/Downloads) for a browser download to finish.
Detects when a .crdownload / .part / .opdownload temp file disappears and
the real file appears. Used before any move/open step that needs the file.

Usage:
    from core.download_watcher import wait_for_download

    path = wait_for_download(timeout=300)   # returns Path or None
    if path:
        shutil.move(str(path), destination)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from core.progress_wait import ProgressWaitResult, wait_for_condition

# Temp suffixes that browsers use while downloading
_PARTIAL_SUFFIXES = {".crdownload", ".part", ".opdownload", ".download"}


@dataclass(frozen=True)
class DownloadWatchResult:
    """Observed download state; completion is only true for a stable real file."""

    status: str
    path: Path | None = None
    bytes: int = 0
    partial_files: tuple[str, ...] = ()
    samples: tuple[object, ...] = field(default_factory=tuple)


def _partial_files(directory: Path) -> list[Path]:
    """All in-progress download temp files in directory."""
    return [p for p in directory.iterdir() if p.suffix in _PARTIAL_SUFFIXES and p.is_file()]


def _completed_files(directory: Path, after_ts: float, min_size: int = 1024) -> list[Path]:
    """Files that appeared or grew after after_ts, excluding partial files."""
    result = []
    for p in directory.iterdir():
        if p.suffix in _PARTIAL_SUFFIXES:
            continue
        if not p.is_file():
            continue
        try:
            st = p.stat()
            if st.st_mtime >= after_ts and st.st_size >= min_size:
                result.append(p)
        except OSError:
            continue
    return result


def watch_download(
    directory: str | Path | None = None,
    *,
    before: set[Path] | None = None,
    timeout: float = 300.0,
    stall_timeout: float | None = None,
    poll_interval: float = 1.5,
    min_size: int = 1024,
    filename_hint: str = "",
    stable_checks: int = 2,
) -> DownloadWatchResult:
    """Observe a download through real file growth and stable completion.

    A file appearing is not completion.  The file must be non-partial and
    retain its size over repeated observations.  Changing sizes renew the
    progress window; unchanged partial files become ``stalled`` rather than
    falsely succeeding.
    """
    watch_dir = Path(directory) if directory else Path.home() / "Downloads"
    watch_dir.mkdir(parents=True, exist_ok=True)
    baseline = before if before is not None else {
        path for path in watch_dir.iterdir()
        if path.is_file() and path.suffix not in _PARTIAL_SUFFIXES
    }
    previous_signature: tuple[str, int] | None = None
    stable = 0

    def observe() -> dict[str, object]:
        nonlocal previous_signature, stable
        partial = _partial_files(watch_dir)
        candidates = [
            path for path in watch_dir.iterdir()
            if path.is_file()
            and path.suffix not in _PARTIAL_SUFFIXES
            and path not in baseline
            and path.stat().st_size >= min_size
        ]
        if filename_hint:
            hinted = [path for path in candidates if filename_hint.lower() in path.name.lower()]
            if hinted:
                candidates = hinted
        selected = max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None
        signature = (selected.name, selected.stat().st_size) if selected else None
        stable = stable + 1 if signature is not None and signature == previous_signature else 0
        previous_signature = signature
        return {
            "partial": tuple(path.name for path in partial),
            "selected": selected,
            "bytes": selected.stat().st_size if selected else 0,
            "stable": stable,
            "complete": bool(selected and not partial and stable >= stable_checks),
            "progress": (tuple((path.name, path.stat().st_size) for path in partial), signature),
        }

    wait: ProgressWaitResult = wait_for_condition(
        observe,
        lambda item: bool(item["complete"]),
        progress=lambda item: item["progress"],
        timeout=timeout,
        stall_timeout=stall_timeout,
        interval=poll_interval,
    )
    item = wait.observation
    selected = item["selected"]
    if wait.satisfied and isinstance(selected, Path):
        return DownloadWatchResult("completed", selected, int(item["bytes"]), tuple(item["partial"]), wait.samples)
    return DownloadWatchResult(wait.status, None, int(item["bytes"]), tuple(item["partial"]), wait.samples)


def wait_for_download(
    directory: str | Path | None = None,
    timeout: float = 300.0,
    poll_interval: float = 1.5,
    min_size: int = 1024,
    filename_hint: str = "",
) -> Path | None:
    """
    Block until a browser download completes in ``directory`` (default ~/Downloads).

    Strategy:
      1. Record the start time.
      2. Poll every ``poll_interval`` seconds.
      3. When we see a .crdownload/.part file appear → a download started.
      4. When all such temp files are gone and a new real file appeared → done.

    Args:
        directory:      Folder to watch (default: ~/Downloads).
        timeout:        Max seconds to wait before giving up.
        poll_interval:  Seconds between polls.
        min_size:       Minimum file size (bytes) to count as a real download.
        filename_hint:  Optional substring to filter the expected filename.

    Returns:
        Path to the completed download, or None if timed out.
    """
    result = watch_download(
        directory,
        timeout=timeout,
        poll_interval=poll_interval,
        min_size=min_size,
        filename_hint=filename_hint,
    )
    if result.path is not None:
        logger.info(f"DownloadWatcher: complete → {result.path.name} ({result.bytes:,} bytes)")
        return result.path
    logger.warning(f"DownloadWatcher: {result.status} without a completed file")
    return None


def wait_for_download_then_move(
    destination: str | Path,
    directory: str | Path | None = None,
    timeout: float = 300.0,
    filename_hint: str = "",
) -> Path | None:
    """
    Wait for a download to complete, then move it to ``destination``.

    Returns the final Path or None if download timed out.
    """
    import shutil

    completed = wait_for_download(directory=directory, timeout=timeout, filename_hint=filename_hint)
    if completed is None:
        return None
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / completed.name
    shutil.move(str(completed), str(final))
    logger.info(f"DownloadWatcher: moved → {final}")
    return final
