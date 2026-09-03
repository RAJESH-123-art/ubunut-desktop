"""
Single source of truth for app names, package aliases, browser paths, and commands.

All task files and agents import from here — no more five separate ALIAS dicts
scattered across the codebase.
"""
from __future__ import annotations

import shutil

# ── Snap/apt package name aliases ────────────────────────────────────────────
# Maps human-readable / alternative spellings → canonical snap package name.
PACKAGE_ALIASES: dict[str, str] = {
    "proton vpn":          "proton-vpn",
    "protonvpn":           "proton-vpn",
    "vlc":                 "vlc",
    "gimp":                "gimp",
    "obs":                 "obs-studio",
    "obs studio":          "obs-studio",
    "discord":             "discord",
    "telegram":            "telegram-desktop",
    "slack":               "slack",
    "zoom":                "zoom-client",
    "spotify":             "spotify",
    "vscode":              "code",
    "vs code":             "code",
    "visual studio code":  "code",
    "inkscape":            "inkscape",
    "blender":             "blender",
    "kdenlive":            "kdenlive",
    "audacity":            "audacity",
    "mumble":              "mumble",
    "firefox":             "firefox",
    "brave":               "brave",
    "chromium":            "chromium",
}

# ── App name → launch command ─────────────────────────────────────────────────
APP_COMMANDS: dict[str, str] = {
    "file manager":    "nautilus",
    "files":           "nautilus",
    "nautilus":        "nautilus",
    "terminal":        "gnome-terminal",
    "calculator":      "gnome-calculator",
    "text editor":     "gedit",
    "gedit":           "gedit",
    "firefox":         "firefox",
    "brave":           "brave-browser",
    "chrome":          "google-chrome",
    "chromium":        "chromium-browser",
    "vlc":             "vlc",
    "music player":    "rhythmbox",
    "rhythmbox":       "rhythmbox",
    "photo viewer":    "eog",
    "image viewer":    "eog",
    "eog":             "eog",
    "system settings": "gnome-control-center",
    "settings":        "gnome-control-center",
    "screenshot tool": "gnome-screenshot",
    "gimp":            "gimp",
    "inkscape":        "inkscape",
}

# ── Browser name → binary path ────────────────────────────────────────────────
BROWSER_PATHS: dict[str, str] = {
    "brave":    "/snap/bin/brave",
    "browser":  "/snap/bin/brave",
    "chrome":   "google-chrome",
    "chromium": "chromium",
    "firefox":  "firefox",
}

# ── Ordered preference list for browser auto-detection ───────────────────────
BROWSER_EXECUTABLES: list[str] = [
    "google-chrome",
    "google-chrome-stable",
    "brave-browser",
    "chromium-browser",
    "chromium",
    "firefox",
]

# ── Design platform URLs ──────────────────────────────────────────────────────
DESIGN_URLS: dict[str, str] = {
    "canva":       "https://www.canva.com/",
    "figma":       "https://www.figma.com/files/recent",
    "vistacreate": "https://www.vistacreate.com/templates/",
}


# ── Helper functions ──────────────────────────────────────────────────────────

def normalize_package(app_name: str, override: str = "") -> str:
    """Resolve a user-facing app name to its canonical snap/apt package name."""
    key = (override or app_name).lower().strip()
    return PACKAGE_ALIASES.get(key, PACKAGE_ALIASES.get(app_name.lower().strip(), key.replace(" ", "-")))


def find_browser() -> str:
    """Return the path of the first available browser on this system."""
    for browser in BROWSER_EXECUTABLES:
        if shutil.which(browser):
            return browser
    return "firefox"  # last-resort fallback


def resolve_app_command(app_name: str) -> str:
    """Return the launch command for a given app name (falls back to app_name itself)."""
    return APP_COMMANDS.get(app_name.lower().strip(), app_name)
