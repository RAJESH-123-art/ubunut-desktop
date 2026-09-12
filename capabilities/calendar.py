"""
capabilities/calendar.py — GNOME/Evolution calendar via local ics file.

The "On This Computer" calendar lives at
~/.local/share/evolution/calendar/system/calendar.ics — the evolution-calendar-
factory monitors and reloads that file, so direct atomic writes are reflected
in GNOME Clocks/Calendar immediately.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap

ICS_PATH = Path(
    os.path.expanduser("~/.local/share/evolution/calendar/system/calendar.ics")
)


def _parse_dt(value: str) -> datetime | None:
    """Parse ISO-ish datetime: 2026-09-11, 2026-09-11T15:00, 15:00 today."""
    value = str(value).strip()
    if not value:
        return None
    # Time-only → today
    try:
        t = datetime.strptime(value, "%H:%M")  # noqa: DTZ007 — made tz-aware below
        return datetime.combine(
            datetime.now(tz=None).astimezone().date(), t.time()
        ).astimezone()
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.astimezone()
    except ValueError:
        return None


def _dt_out(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _load_events() -> list[tuple[str, dict]]:
    """Return [(raw_vevent_block, {uid, summary, dtstart, dtend, ...}), ...]."""
    import icalendar

    if not ICS_PATH.is_file():
        return []
    try:
        cal = icalendar.Calendar.from_ical(ICS_PATH.read_bytes())
    except Exception:
        return []
    events: list[tuple[str, dict]] = []
    for component in cal.walk("VEVENT"):
        uid = str(component.get("UID", ""))
        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND")
        events.append((
            component.to_ical().decode(),
            {
                "uid": uid,
                "summary": str(component.get("SUMMARY", "")),
                "dtstart": str(dtstart.dt) if dtstart else None,
                "dtend": str(dtend.dt) if dtend else None,
                "location": str(component.get("LOCATION", "")) or None,
                "description": str(component.get("DESCRIPTION", "")) or None,
            },
        ))
    return events


def _write_calendar(vevent_blocks: list[str]) -> None:
    """Atomically rewrite the ics file keeping only given raw VEVENT blocks."""
    lines = [
        "BEGIN:VCALENDAR",
        "CALSCALE:GREGORIAN",
        "PRODID:-//NIKKI Agent//NONSGML Calendar Capability//EN",
        "VERSION:2.0",
    ]
    for block in vevent_blocks:
        lines.extend(block.strip().splitlines())
    lines.append("END:VCALENDAR")
    ICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ICS_PATH.with_suffix(".ics.tmp")
    tmp.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    os.replace(tmp, ICS_PATH)


def install(registry: Any, *, approve_all: bool = False) -> None:

    def list_events(args: dict, state: Any) -> Any:
        from_days = int(args.get("days", 30))
        events = _load_events()
        now = datetime.now(tz=None).astimezone()
        horizon = now + timedelta(days=from_days)
        upcoming = []
        for _, info in events:
            ds = info["dtstart"]
            if not ds:
                continue
            start = _parse_dt(str(ds))
            if start is None:
                continue
            if now - timedelta(days=1) <= start <= horizon:
                upcoming.append(info)
        upcoming.sort(key=lambda e: str(e["dtstart"]))
        return ok({"events": upcoming, "count": len(upcoming)})

    def create_event(args: dict, state: Any) -> Any:
        title = str(args.get("title") or args.get("summary") or "").strip()
        if not title:
            return fail("'title' is required")
        start_raw = args.get("start")
        end_raw = args.get("end")
        if not start_raw:
            return fail("'start' is required (e.g. 2026-09-12 15:00)")
        start = _parse_dt(str(start_raw))
        if start is None:
            return fail(f"cannot parse start {start_raw!r} (use 2026-09-12T15:00)")
        end = _parse_dt(str(end_raw)) if end_raw else None
        if end is None:
            minutes = int(args.get("duration_minutes", 60))
            end = start + timedelta(minutes=minutes)
        if end < start:
            return fail(f"end {end} before start {start}")
        description = str(args.get("description", "") or "")
        location = str(args.get("location", "") or "")
        uid = f"nikki-{uuid.uuid4().hex[:12]}@local"
        lines = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{_dt_out(datetime.now(tz=None).astimezone())}",
            f"DTSTART:{_dt_out(start)}",
            f"DTEND:{_dt_out(end)}",
            f"SUMMARY:{title}",
        ]
        if description:
            lines.append(f"DESCRIPTION:{description}")
        if location:
            lines.append(f"LOCATION:{location}")
        lines.append("END:VEVENT")
        existing = [raw for raw, _ in _load_events()]
        _write_calendar(existing + ["\n".join(lines)])
        # verify
        if any(info["uid"] == uid for _, info in _load_events()):
            return ok({"created": title, "uid": uid,
                        "start": str(start), "end": str(end), "verified": True})
        return fail(f"creation of {title!r} not verified on disk")

    def delete_event(args: dict, state: Any) -> Any:
        uid = str(args.get("uid", "") or "").strip()
        title = str(args.get("title", "") or "").strip()
        if not uid and not title:
            return fail("'uid' or 'title' is required")
        events = _load_events()
        kept, removed = [], []
        for raw, info in events:
            match = (uid and info["uid"] == uid) or (
                title and title.lower() in info["summary"].lower()
            )
            if match:
                removed.append(info)
            else:
                kept.append(raw)
        if not removed:
            return fail(f"no event matches uid={uid!r} title={title!r}")
        _write_calendar(kept)
        if not any(
            (uid and info["uid"] == uid) or (title and title.lower() in info["summary"].lower())
            for _, info in _load_events()
        ):
            return ok({"deleted": [r["summary"] for r in removed],
                        "count": len(removed), "verified": True})
        return fail("delete not verified — event still on disk")

    def update_event(args: dict, state: Any) -> Any:
        uid = str(args.get("uid", "") or "").strip()
        title = str(args.get("title", "") or "").strip()
        if not uid and not title:
            return fail("'uid' or 'title' is required to find the event")
        import icalendar

        if not ICS_PATH.is_file():
            return fail("calendar file not found")
        try:
            cal = icalendar.Calendar.from_ical(ICS_PATH.read_bytes())
        except Exception as exc:
            return fail(f"cannot parse calendar: {exc}")
        updated = False
        for comp in cal.walk("VEVENT"):
            c_uid = str(comp.get("UID", ""))
            c_summary = str(comp.get("SUMMARY", ""))
            if (uid and c_uid == uid) or (title and title.lower() in c_summary.lower()):
                if args.get("new_title"):
                    comp["SUMMARY"] = str(args["new_title"])
                if args.get("new_start"):
                    start = _parse_dt(str(args["new_start"]))
                    if start is None:
                        return fail(f"cannot parse new_start {args['new_start']!r}")
                    comp["DTSTART"] = icalendar.vDatetime(start)
                if args.get("new_end"):
                    end = _parse_dt(str(args["new_end"]))
                    if end is None:
                        return fail(f"cannot parse new_end {args['new_end']!r}")
                    comp["DTEND"] = icalendar.vDatetime(end)
                updated = True
                break
        if not updated:
            return fail(f"no event matches uid={uid!r} title={title!r}")
        tmp = ICS_PATH.with_suffix(".ics.tmp")
        tmp.write_bytes(cal.to_ical())
        os.replace(tmp, ICS_PATH)
        return ok({"updated": uid or title, "verified": True})

    register_cap(registry, Cap(
        name="calendar.list_events",
        description="list upcoming calendar events from the local Evolution calendar",
        side_effect="read", inputs=("days",),
    ), list_events)

    register_cap(registry, Cap(
        name="calendar.create_event",
        description="create a calendar event (start ISO, optional end or duration_minutes)",
        side_effect="local_write", inputs=("title", "start", "end", "duration_minutes"),
    ), create_event)

    register_cap(registry, Cap(
        name="calendar.delete_event",
        description="delete a calendar event by uid or title substring",
        side_effect="local_write", inputs=("uid", "title"),
    ), delete_event)

    register_cap(registry, Cap(
        name="calendar.update_event",
        description="update title/start/end of an existing event found by uid or title",
        side_effect="local_write", inputs=("uid", "title", "new_title", "new_start", "new_end"),
    ), update_event)
