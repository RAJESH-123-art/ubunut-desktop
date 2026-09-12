from __future__ import annotations

from pathlib import Path

from core.download_watcher import watch_download


def test_existing_completed_file_requires_stability_before_completion(tmp_path: Path) -> None:
    before: set[Path] = set()
    target = tmp_path / "report.iso"
    target.write_bytes(b"x" * 32)

    result = watch_download(
        tmp_path,
        before=before,
        timeout=1,
        poll_interval=0.01,
        min_size=1,
        stable_checks=1,
    )

    assert result.status == "completed"
    assert result.path == target
    assert result.bytes == 32


def test_partial_file_stalls_instead_of_reporting_completion(tmp_path: Path) -> None:
    partial = tmp_path / "report.iso.crdownload"
    partial.write_bytes(b"x" * 32)

    result = watch_download(
        tmp_path,
        before=set(),
        timeout=0.05,
        stall_timeout=0.02,
        poll_interval=0.01,
        min_size=1,
    )

    assert result.status == "stalled"
    assert result.path is None
    assert result.partial_files == ("report.iso.crdownload",)
