"""
capabilities/scheduling.py — Cron job scheduling via user crontab.

Jobs are tagged with `# nikki-id:<id>` comment lines so NIKKI-added jobs can
be listed/removed precisely without touching user's manual cron entries.
Write path pipes text into `crontab -` (no temp files, atomic enough for cron).
"""
from __future__ import annotations

import uuid
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell

ID_PREFIX = "nikki-id:"


def _read_crontab() -> tuple[bool, str]:
    rc, out, err = run_shell(["crontab", "-l"])
    if rc != 0:
        # "no crontab for user" is rc 1 — treat as empty, anything else is real
        if "no crontab" in (err or "").lower() or "no crontab" in out.lower():
            return True, ""
        return False, err or f"crontab -l exited {rc}"
    return True, out


def _write_crontab(content: str) -> tuple[bool, str]:
    import subprocess

    try:
        proc = subprocess.run(
            ["crontab", "-"], input=content, text=True,
            capture_output=True, timeout=15,
            check=False,  # we branch on returncode below
        )
    except Exception as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or f"crontab - exited {proc.returncode}").strip()
    return True, ""


def _cron_expr(schedule: str) -> str | None:
    """Normalize schedule: cron expr, hourly/daily/weekly/monthly, daily:HH:MM."""
    s = schedule.strip().lower()
    if s == "hourly":
        return "0 * * * *"
    if s == "daily":
        return "0 9 * * *"
    if s == "midnight":
        return "0 0 * * *"
    if s == "weekly":
        return "0 9 * * 1"
    if s == "monthly":
        return "0 9 1 * *"
    if s == "boot" or s == "reboot":
        return "@reboot"
    if s.startswith("daily:"):
        hhmm = s.split(":", 1)[1]
        parts = hhmm.split(":")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{m} {h} * * *"
        return None
    # Raw cron expr: 5 fields or @reboot/@daily/...
    if s.startswith("@"):
        return s if s in ("@reboot", "@daily", "@hourly", "@weekly", "@monthly", "@midnight") else None
    fields = schedule.strip().split()
    if len(fields) == 5:
        return schedule.strip()
    return None


def install(registry: Any, *, approve_all: bool = False) -> None:

    def add_job(args: dict, state: Any) -> Any:
        command = str(args.get("command", "")).strip()
        schedule = str(args.get("schedule", "")).strip()
        job_id = str(args.get("id", "")).strip() or f"nikki-{uuid.uuid4().hex[:8]}"
        if not command:
            return fail("'command' is required")
        if not schedule:
            return fail("'schedule' is required (cron expr, 'hourly', 'daily', 'daily:09:30', 'boot')")
        expr = _cron_expr(schedule)
        if expr is None:
            return fail(f"invalid schedule {schedule!r} (use 'm h dom mon dow' or 'daily'/'hourly'/'boot')")
        ok_read, current = _read_crontab()
        if not ok_read:
            return fail(f"cannot read crontab: {current}")
        if ID_PREFIX in current:
            for line in current.splitlines():
                if line.startswith("#") and ID_PREFIX in line and line.split(ID_PREFIX, 1)[1].strip() == job_id:
                    return fail(f"job id {job_id!r} already exists — remove it first or pass a new id")
        entry = f"{expr} {command}"
        new_content = f"{current.rstrip()}\n" if current.strip() else ""
        new_content += f"# {ID_PREFIX}{job_id}\n{entry}\n"
        ok_write, err = _write_crontab(new_content)
        if not ok_write:
            return fail(f"crontab write failed: {err}")
        # verify
        ok_read2, check = _read_crontab()
        if ok_read2 and entry in check and f"{ID_PREFIX}{job_id}" in check:
            return ok({"id": job_id, "schedule": expr, "command": command, "verified": True})
        return fail("crontab write not verified — read-back mismatch")

    def list_jobs(args: dict, state: Any) -> Any:
        all_jobs = bool(args.get("all", False))
        ok_read, content = _read_crontab()
        if not ok_read:
            return fail(f"cannot read crontab: {content}")
        jobs: list[dict[str, Any]] = []
        lines = content.splitlines()
        pending_id: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") and ID_PREFIX in stripped:
                pending_id = stripped.split(ID_PREFIX, 1)[1].strip()
                continue
            if stripped and not stripped.startswith("#"):
                is_nikki = pending_id is not None
                if is_nikki or all_jobs:
                    parts = stripped.split()
                    if stripped.startswith("@"):
                        sched = parts[0]
                        cmd = " ".join(parts[1:])
                    else:
                        sched = " ".join(parts[:5])
                        cmd = " ".join(parts[5:])
                    jobs.append({
                        "id": pending_id or "(user)",
                        "schedule": sched,
                        "command": cmd,
                        "raw": stripped,
                    })
                pending_id = None
        return ok({"jobs": jobs, "count": len(jobs)})

    def remove_job(args: dict, state: Any) -> Any:
        job_id = str(args.get("id", "")).strip()
        if not job_id:
            return fail("'id' is required (as shown by schedule.list_jobs)")
        ok_read, content = _read_crontab()
        if not ok_read:
            return fail(f"cannot read crontab: {content}")
        lines = content.splitlines()
        out_lines: list[str] = []
        removed = False
        skip_next = False
        for line in lines:
            stripped = line.strip()
            if skip_next:
                skip_next = False
                continue
            if stripped.startswith("#") and ID_PREFIX in stripped:
                this_id = stripped.split(ID_PREFIX, 1)[1].strip()
                if this_id == job_id:
                    removed = True
                    skip_next = True  # drop the command line after this marker
                    continue
            out_lines.append(line)
        if not removed:
            return fail(f"no scheduled job with id {job_id!r}")
        ok_write, err = _write_crontab("\n".join(out_lines) + ("\n" if out_lines else ""))
        if not ok_write:
            return fail(f"crontab write failed: {err}")
        ok_read2, check = _read_crontab()
        if ok_read2 and job_id not in check:
            return ok({"removed": job_id, "verified": True})
        return fail("remove not verified — id still present")

    register_cap(registry, Cap(
        name="schedule.add_job",
        description="add a cron job (schedule: cron expr, 'hourly', 'daily', 'daily:09:30', 'boot')",
        side_effect="local_write", inputs=("command", "schedule", "id"),
    ), add_job)

    register_cap(registry, Cap(
        name="schedule.list_jobs",
        description="list scheduled jobs (nikki-tagged by default, all=true includes user jobs)",
        side_effect="read", inputs=("all",),
    ), list_jobs)

    register_cap(registry, Cap(
        name="schedule.remove_job",
        description="remove a scheduled job by its nikki id",
        side_effect="local_write", inputs=("id",),
    ), remove_job)
