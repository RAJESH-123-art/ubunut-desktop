"""
Full Ubuntu application discovery.

core/app_registry.py's APP_COMMANDS is a small, hand-curated table of the
most common apps with already-known-good launch commands -- fast, but it
only covers ~15 apps. This module makes EVERY installed application openable
by scanning the standard .desktop file locations Linux apps register
themselves in (system packages, user-installed, snap, flatpak), parsing
each one, and building a searchable name -> launch-command index.

Usage:
    from core.app_finder import find_app, list_app_names

    app = find_app("gimp")          # fuzzy/substring match on Name/GenericName/id
    if app:
        subprocess.Popen(shlex.split(app.launch_command()))

    list_app_names()                # every discovered app's display name
"""
from __future__ import annotations

import configparser
import difflib
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# Every place Linux desktop apps register a *.desktop launcher.
_DESKTOP_DIRS: list[Path] = [
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/applications",
    Path.home() / ".local/share/flatpak/exports/share/applications",
]

# Desktop-entry field codes (%f %F %u %U %i %c %k %%) that must be stripped
# from Exec= before the command can be run directly via subprocess.
_FIELD_CODE_RE = re.compile(r"%[fFuUick%]")

_CACHE_TTL = 300.0  # rescan at most every 5 min -- new app installs are rare


@dataclass
class DesktopApp:
    id: str             # .desktop file stem, e.g. "org.gnome.Calculator"
    name: str           # Name=
    generic_name: str   # GenericName=
    exec_cmd: str       # cleaned Exec= (field codes stripped)
    path: str           # full path to the .desktop file
    wm_class: str = ""  # StartupWMClass= -- useful for AT-SPI/window matching

    def launch_command(self) -> str:
        if self.exec_cmd:
            return self.exec_cmd
        if shutil.which("gtk-launch"):
            return f"gtk-launch {self.id}"
        return ""


def _clean_exec(exec_line: str) -> str:
    cmd = _FIELD_CODE_RE.sub("", exec_line).strip()
    return re.sub(r"\s+", " ", cmd)


def _parse_desktop_file(path: Path) -> DesktopApp | None:
    try:
        parser = configparser.RawConfigParser(strict=False)
        parser.read(path, encoding="utf-8")
        if not parser.has_section("Desktop Entry"):
            return None
        section = parser["Desktop Entry"]
        if section.get("Type", "Application") != "Application":
            return None
        if section.get("NoDisplay", "false").strip().lower() == "true":
            return None
        if section.get("Hidden", "false").strip().lower() == "true":
            return None
        name = section.get("Name", "").strip()
        exec_line = section.get("Exec", "").strip()
        if not name or not exec_line:
            return None
        return DesktopApp(
            id=path.stem,
            name=name,
            generic_name=section.get("GenericName", "").strip(),
            exec_cmd=_clean_exec(exec_line),
            path=str(path),
            wm_class=section.get("StartupWMClass", "").strip(),
        )
    except Exception as exc:
        logger.debug(f"app_finder: failed to parse {path}: {exc}")
        return None


def _scan() -> dict[str, DesktopApp]:
    apps: dict[str, DesktopApp] = {}
    for directory in _DESKTOP_DIRS:
        if not directory.is_dir():
            continue
        for entry in directory.glob("*.desktop"):
            app = _parse_desktop_file(entry)
            if app is None:
                continue
            apps[app.id.lower()] = app
    logger.debug(f"app_finder: discovered {len(apps)} installed applications")
    return apps


_cache: dict[str, DesktopApp] | None = None
_cache_time: float = 0.0


def all_apps(force_refresh: bool = False) -> dict[str, DesktopApp]:
    """Return {desktop_id_lower: DesktopApp} for every installed app found."""
    global _cache, _cache_time
    now = time.time()
    if force_refresh or _cache is None or (now - _cache_time) > _CACHE_TTL:
        _cache = _scan()
        _cache_time = now
    return _cache


def find_app(query: str) -> DesktopApp | None:
    """
    Best-effort lookup of an installed app by human name -- e.g. "gimp",
    "vs code", "text editor", "calc" -- matching against Name=, GenericName=,
    and the .desktop file id, with fuzzy fallback so typos and partial names
    still resolve to a real installed application.

    Match priority matters a lot here: GenericName= is frequently shared by
    many unrelated apps (Vim, VS Code, Zed, Antigravity, and the actual GNOME
    "Text Editor" app all declare GenericName=Text Editor). An exact match on
    the real Name= (what a user means by "open text editor") must always beat
    an incidental GenericName= match on some other app, or resolution becomes
    a coin-flip based on filesystem scan order (this previously caused "open
    text editor" to launch Vim instead of GNOME Text Editor).
    """
    apps = list(all_apps().values())
    q = query.lower().strip()
    if not q:
        return None

    # 1a. Exact match on the real Name= -- highest priority, always wins
    for app in apps:
        if q == app.name.lower():
            return app

    # 1b. Exact match on the .desktop file id
    for app in apps:
        if q == app.id.lower():
            return app

    # 1c. Exact match on GenericName= -- lowest-priority exact match, since
    #     many unrelated apps share the same GenericName
    for app in apps:
        if q == app.generic_name.lower():
            return app

    # 2a. Substring match on Name= (either direction) -- prefer the shortest
    #     matching name (most specific match)
    name_candidates = [
        app for app in apps
        if q in app.name.lower() or app.name.lower() in q
    ]
    if name_candidates:
        name_candidates.sort(key=lambda a: len(a.name))
        return name_candidates[0]

    # 2b. Substring match on id or GenericName as a weaker fallback
    other_candidates = [
        app for app in apps
        if q in app.id.lower() or q in app.generic_name.lower()
    ]
    if other_candidates:
        other_candidates.sort(key=lambda a: len(a.name))
        return other_candidates[0]

    # 3. Fuzzy match against all display names as a last resort (handles typos)
    names = {app.name.lower(): app for app in apps}
    close = difflib.get_close_matches(q, names.keys(), n=1, cutoff=0.6)
    if close:
        return names[close[0]]

    return None


def list_app_names() -> list[str]:
    """Human-readable names of every discovered application."""
    return sorted({app.name for app in all_apps().values()})
