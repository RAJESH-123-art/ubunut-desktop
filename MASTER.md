# AUTONOMOUS DESKTOP AGENT — MASTER DOCUMENT

> **Purpose of this file:** Paste this into any new AI chat session. The AI will instantly understand the entire project — architecture, code, decisions, current state, and roadmap — without you explaining anything.

---

## 1. WHAT IS THIS PROJECT?

An **autonomous desktop automation agent** for Linux (Ubuntu/GNOME, Wayland + X11) that:

- Understands **natural language commands** in English + romanized Hindi/Telugu (e.g. `"yt pe rrr song chalao"`, `"watsap pe darling ko hi bhejo"`)
- Executes **real desktop actions** — installs apps, sends WhatsApp messages, plays YouTube, takes screenshots, controls windows, manages files, types text, controls volume/brightness, runs shell commands
- Uses **zero AI, zero internet, zero API keys** for core operation
- **Self-heals**: tries multiple strategies, verifies results, learns which strategy works best
- Runs on **Wayland** (GNOME 46) with AT-SPI as the primary automation layer

### Entry point
```bash
cd /home/rajesh/Pictures/desktop
python agent.py "install vlc"
python agent.py "yt pe rrr song chalao"
python agent.py "watsap pe darling ko hi bhejo"
python agent.py "take ss"
python agent.py "open klavaro and velocity text typing task do it 100% accuracy and speed"
python agent.py --workflow daily_cleanup
python agent.py --list
```

---

## 2. PROJECT LOCATION & STRUCTURE

```
/home/rajesh/Pictures/desktop/
│
├── agent.py                  ← MAIN ENTRY POINT (NL command → execute)
├── master_agent.py           ← Alternative simpler agent (single-file)
├── automation.py             ← Legacy automation runner
├── MASTER.md                 ← THIS FILE
│
├── core/                     ← Engine layer (no tasks here)
│   ├── smart_parser.py       ← NL → ParsedIntent (fuzzy, multilingual)
│   ├── strategy_executor.py  ← Tries N strategies, learns from history
│   ├── verifier.py           ← Post-action verification (OCR/AT-SPI/CLI)
│   ├── memory.py             ← JSON persistence (~/.config/desktop_automation/)
│   ├── workflow_engine.py    ← Multi-step YAML workflow runner
│   ├── gui_controller.py     ← Mouse/keyboard/window control
│   ├── uinput_keyboard.py    ← Hardware-level keyboard via evdev (Wayland-native)
│   ├── vision_engine.py      ← OCR + template matching (OpenCV + Tesseract)
│   ├── atspi_utils.py        ← AT-SPI accessibility tree helpers
│   ├── app_registry.py       ← App name → package/command lookup table
│   ├── cdp_browser.py        ← Chrome DevTools Protocol browser control
│   ├── browser.py            ← Playwright browser wrapper
│   ├── browser_manager.py    ← Shared browser instance management
│   ├── logger.py             ← Logging, screenshots, notifications
│   ├── system_utils.py       ← Shell commands, clipboard, file utils
│   └── intent_parser.py      ← Legacy intent parser (mostly superseded)
│
├── tasks/                    ← One file per automation task
│   ├── __init__.py           ← Auto-discovers tasks (setup/execute/cleanup)
│   ├── atspi_install.py      ← App Center GUI install (PRIMARY install method)
│   ├── install_app.py        ← App install (AT-SPI + CLI fallback)
│   ├── youtube_automation.py ← YouTube search + play
│   ├── whatsapp_send.py      ← WhatsApp Web via Chrome CDP
│   ├── system_screenshot.py  ← Screenshot (gnome-screenshot/grim/scrot/mss)
│   ├── open_browser_and_visit.py ← Open browser + navigate to URL
│   ├── open_system_app.py    ← Launch any system app with AT-SPI verify
│   ├── window_management.py  ← Minimize/maximize/close via AT-SPI (Wayland-native)
│   ├── klavaro_automation.py ← Klavaro typing test (velocity/adaptability/basic/fluidness)
│   ├── volume_control.py     ← pactl/amixer volume up/down/mute/unmute/set
│   ├── brightness_control.py ← brightnessctl/xrandr brightness up/down/set
│   ├── lock_screen.py        ← loginctl/gnome-screensaver/xdg-screensaver
│   ├── system_power.py       ← shutdown/restart/suspend/hibernate
│   ├── type_text.py          ← Type text via GUIController
│   ├── hotkey.py             ← Keyboard shortcuts via GUIController
│   ├── system_hotkey.py      ← Keyboard shortcuts via evdev/uinput (Wayland-native)
│   ├── create_folder.py      ← Create directory
│   ├── file_operations.py    ← Delete/move/copy/rename files
│   ├── folder_operations.py  ← Open/create/delete folders
│   ├── run_command.py        ← Execute raw shell command
│   ├── organize_downloads.py ← Sort ~/Downloads into subfolders
│   ├── wait_seconds.py       ← Pause N seconds
│   ├── browser_action.py     ← Playwright browser action sequences
│   ├── canva_template.py     ← Canva/Figma design automation
│   └── universal_fallback.py ← Catch-all: URL→xdg-open, binary→run, else log
│
├── config/
│   ├── config.yaml           ← Main config (password, GUI, logging, paths)
│   ├── workflow.yaml         ← Named multi-step workflows
│   └── config_loader.py      ← YAML loader
│
├── ui/
│   ├── assistant.py          ← Chat-style UI (optional)
│   └── cli.py                ← CLI interface
│
└── logs/
    ├── automation.log        ← Structured execution log
    └── screenshots/          ← Auto-saved screenshots per action
```

---

## 3. HOW IT WORKS — THE FULL PIPELINE

```
User types: "yt pe rrr song chalao"
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  1. KAVARO SHORTCUT CHECK (agent.py)            │
│     if "klavaro" in command → skip parser       │
│     extract exercise mode → run directly        │
└─────────────────────────────────────────────────┘
                        │ (not klavaro)
                        ▼
┌─────────────────────────────────────────────────┐
│  2. SMART PARSER (core/smart_parser.py)         │
│     • WORD_MAP: "yt"→"youtube", "pe"→"on",     │
│       "chalao"→"play", "bhejo"→"send" ...       │
│     • INTENT_DEFS: score each intent by         │
│       keyword overlap (fuzzy bigram matching)   │
│     • Extracts params: query, contact, url...   │
│     → ParsedIntent(intent="youtube",            │
│         params={"query": "rrr song"},           │
│         confidence=0.38)                        │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  3. EXECUTOR BUILDER (agent.py _build_executor) │
│     Maps intent → StrategyExecutor with:        │
│     • One or more Strategy functions            │
│     • A VerifySpec (what "success" looks like)  │
│     • Password injected from config.yaml        │
│     • URL re-extracted from raw input           │
│       (normalization strips dots, breaks URLs)  │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  4. STRATEGY EXECUTOR (core/strategy_executor)  │
│     • Reads historical success rates from Memory│
│     • Orders strategies best-first              │
│     • Tries strategy 1... if fails, try 2...   │
│     • Calls task's execute(args, resources)     │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  5. TASK EXECUTION (tasks/*.py)                 │
│     Each task has: setup() / execute() /        │
│     cleanup()                                   │
│     Uses: AT-SPI, VirtualKeyboard, Playwright,  │
│     subprocess, GUIController                   │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  6. VERIFIER (core/verifier.py)                 │
│     Confirms action actually worked via:        │
│     • OCR: text visible on screen               │
│     • AT-SPI: app running in accessibility tree │
│     • CLI: shell command exit code              │
│     • Template: image present on screen         │
│     ANY one method passing = verified ✅        │
└─────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│  7. MEMORY UPDATE (core/memory.py)              │
│     Records ok/fail count per strategy          │
│     → Next run automatically tries the          │
│       historically best strategy first          │
└─────────────────────────────────────────────────┘
```

---

## 4. SMART PARSER — HOW LANGUAGE UNDERSTANDING WORKS

File: `core/smart_parser.py`

### Step 1: WORD_MAP normalization
Maps every token to canonical English. Unknown tokens pass through unchanged.

```python
WORD_MAP = {
    # Hindi/Telugu romanized → English
    "chalao": "play",   "bajao": "play",    "kholo": "open",
    "bhejo":  "send",   "hatao": "delete",  "lagao": "install",
    "band":   "close",  "khoj":  "search",
    # Abbreviations
    "yt":  "youtube",   "ss": "screenshot", "wa": "whatsapp",
    "vol": "volume",    "aawaz": "volume",
    # Volume Hindi
    "kam": "down",      "zyada": "up",
    # Power
    "reboot": "restart",
    # Lock
    "taala": "lock",
    # Typing
    "likhna": "type",
    # Create
    "banao": "create",  "bana": "create",
    # Speed terms
    "tez": "speed",     "shuddh": "accuracy",
    ...
}
```

### Step 2: Intent scoring
Each intent has a keyword set. Score = sum(best token match per keyword) / total keywords.

```
For "yt pe rrr song chalao" → normalized "youtube on rrr song play"
  youtube intent keywords: {"youtube","yt","play","video","song","music","watch","bajao"}
    "youtube" → 1.0,  "play" → 1.0,  "song" → 1.0  → score = 3/8 = 0.375 ✅
```

### Step 3: Param extraction rules
- `after:trigger` → single token after first trigger match
- `all_after:trigger` → everything after trigger to end
- `before:trigger` → everything before trigger
- `word_before:trigger` → single token immediately before trigger ← NEW (WhatsApp contact)
- `regex:pattern` → first regex match in normalized text

### Key fix: `_token_matches_keyword`
```python
# Both token AND keyword must be ≥3 chars for substring match.
# This prevents 2-char keyword "sc" from matching inside "fullscreen"
if len(token) >= 3 and len(keyword) >= 3 and (token in keyword or keyword in token):
    return 0.8
```

### All intents defined (in priority order of INTENT_DEFS):

| Intent | Keywords | min_score | Example |
|---|---|---|---|
| `youtube` | youtube,yt,play,video,song,music,watch,bajao | 0.10 | "yt pe rrr chalao" |
| `install_app` | install,setup,get,download,add | 0.10 | "install vlc" |
| `whatsapp_send` | whatsapp,send,message,msg,chat,bhejo,bhej,darling | 0.12 | "watsap darling hi bhejo" |
| `screenshot` | screenshot,capture,screen,snap,ss,sc | 0.12 | "take ss" |
| `search_web` | search,google,find,lookup,look,dhundho | 0.15 | "search python tutorials" |
| `open_browser` | browser,chrome,brave,firefox,internet | 0.18 | "open browser" |
| `visit_url` | visit,go,navigate,open,github,google,youtube | 0.12 | "go to github.com" |
| `open_app` | open,launch,start,run,kholo,khol | 0.15 | "open calculator" |
| `window_minimize` | minimize,chhota | 0.20 | "minimize chrome" |
| `window_maximize` | maximize,fullscreen,expand | 0.30 | "fullscreen" |
| `window_close` | close,quit,exit,kill | 0.18 | "close chrome" |
| `file_delete` | delete,remove,trash,hatao,mita | 0.20 | "delete file test.txt" |
| `organize_downloads` | organize,clean,sort,downloads,files,folder | 0.15 | "organize downloads" |
| `type_text` | type,write,input,keyboard | 0.20 | "type hello world" |
| `volume_control` | volume,sound,audio,mute | 0.20 | "mute" / "aawaz kam karo" |
| `brightness_control` | brightness,bright,dim | 0.30 | "brightness up" |
| `lock_screen` | lock | 0.80 | "lock screen" / "taala lagao" |
| `system_power` | shutdown,restart,suspend,hibernate | 0.18 | "shutdown" / "reboot" |
| `create_folder` | create,new,make,folder,directory | 0.15 | "banao folder test" |
| `run_command` | execute,shell,command | 0.30 | "execute ls -la" |
| `hotkey` | ctrl,alt,shift,press,shortcut,hotkey | 0.15 | "press ctrl c" |
| `klavaro_exercise` | klavaro,velocity,adaptability,typing,exercise | 0.12 | "klavaro velocity" |
| `wait_seconds` | wait,pause,sleep,ruko,ruk | 0.20 | "wait 3 seconds" |

### Compound commands
```python
# "and"/"then"/"aur"/"phir" splits into multiple intents
"install vlc and then open it" → [install_app, open_app]
```

### Special: Klavaro shortcut (bypasses parser entirely)
```python
# In run_command(), BEFORE parse_multi():
if re.search(r'\bklavaro\b', raw):
    exercise = re.search(r'\b(velocity|adaptability|basic|fluidness)\b', raw)
    → runs klavaro_automation.execute({"exercise": mode})
```

---

## 5. ALL TASKS — WHAT / HOW / WHY

### `atspi_install` — App Center GUI Install
**What:** Installs applications through the graphical Ubuntu App Center (snap-store).
**How:**
1. Check if already installed (`snap list` + `which`)
2. Reuse running App Center OR launch fresh (saves ~20s)
3. Find search bar via AT-SPI → type app name → press Enter
4. Event-driven wait (`_await_node()`) until results appear (no fixed sleep)
5. Click first matching result via AT-SPI push_button action
6. Event-driven wait until Install button appears
7. Click Install
8. Event-driven wait for polkit auth dialog (8s max)
9. Type password via AT-SPI or keyboard blind-type
10. Poll `snap list` every 2s until package appears (max 5 min)

**Why GUI not CLI:** User explicitly requested App Center GUI flow. Password is `rgukt` stored in `config/config.yaml → install.sudo_password`.

**Key function:** `_await_node(app_frags, role, name_contains, timeout)` — polls AT-SPI every 0.25s, returns the instant element appears. This is the core speed optimization replacing all `time.sleep(N)` calls.

**Password config:**
```yaml
# config/config.yaml
install:
  sudo_password: "rgukt"
```

---

### `klavaro_automation` — Typing Test Automation
**What:** Automates Klavaro typing tutor to complete exercises with maximum speed and accuracy.
**How:**
1. Set exercise mode in `~/.config/klavaro/preferences.ini`
2. Launch Klavaro detached
3. Navigate to chosen exercise using number keys (1=Basic, 2=Adaptability, 3=Velocity, 4=Fluidness)
4. Read exercise text via AT-SPI from the GtkTextView widget (not OCR — direct memory read)
5. Sanity-check text (≥5 words, ≥60% real English tokens)
6. Focus input field via AT-SPI `grabFocus()`
7. Type at 1800 CPM using `VirtualKeyboard` (evdev/uinput)
8. Press Enter after each paragraph
9. Screenshot results

**Exercise modes:**
```python
EXERCISE_MAP = {
    "basic":        (0, KEY_1, "Basic Course",  False),
    "adaptability": (1, KEY_2, "Adaptability",  True),   # needs Enter to start
    "velocity":     (2, KEY_3, "Velocity",      False),  # DEFAULT when just "klavaro"
    "fluidness":    (3, KEY_4, "Fluidness",     True),
}
```

**Result achieved:** 100% accuracy, 0/460 errors, 274.4 WPM on Velocity exercise.

**Why AT-SPI not OCR:** AT-SPI reads exact GtkTextBuffer contents — no OCR errors, no coordinate guessing. Wayland-safe, coordinate-free.

---

### `whatsapp_send` — WhatsApp Web Automation
**What:** Sends WhatsApp messages using your real logged-in Chrome browser via CDP.
**How:**
1. Launch Chrome with `--remote-debugging-port=9222` using real profile (`~/.config/google-chrome`)
2. Connect via Playwright CDP — reuses your existing WhatsApp Web login session
3. Navigate to `https://web.whatsapp.com`
4. Search for contact or use direct phone URL (`?phone=&text=`)
5. Find message input box, type message, press Enter
6. Screenshot confirmation

**Contact/message extraction (smart recovery in agent.py):**
```python
# Greeting-first strategy — always scans raw input for known phrases FIRST
_greetings = ["good morning", "good night", "hi", "hello", "how are you", ...]
# Known contact aliases scanned in raw input
_known_contacts = ["darling", "mom", "dad", "bhai", "friend", "yaar", ...]
# Words that are NOT contact names
_not_contact = {"to", "send", "in", "you", "are", "on", "good", "morning", ...}
```

**URL-encodes messages** (`urllib.parse.quote`) so special chars don't break the URL.

**Examples that all work correctly:**
- `"open browser darling to send hi msg"` → contact=darling, message=hi
- `"watsap pe darling ko hi bhejo"` → contact=darling, message=hi
- `"watsap me bhai ko good morning bhejo"` → contact=bhai, message=good morning
- `"send how are you to yaar on whatsapp"` → contact=yaar, message=how are you

---

### `youtube_automation` — YouTube Search & Play
**What:** Searches YouTube and plays the first result.
**How (3 strategies):**
1. **CDP path**: Navigate existing Chrome to YouTube search URL
2. **Playwright path**: Open browser → navigate → click first video
3. **GUI fallback**: Open browser via subprocess → click estimated video position → Tab+Enter

---

### `window_management` — Wayland-Native Window Control
**What:** Minimize, maximize, unmaximize, close, focus any window.
**Why rewritten:** `wmctrl` returns zero windows on Wayland (X11 only). Replaced with:
- **AT-SPI** to find window frames by title
- **AT-SPI frame actions** (`win.close`, `window.minimize`, `window.toggle-maximized`) for GTK apps
- **evdev keyboard shortcuts** as universal fallback (Super+Up/Down, Alt+F4, Super+H)

**Empty window name** = acts on currently active/focused window (detected via `STATE_ACTIVE`).

---

### `volume_control` — System Volume
**What:** Volume up/down/mute/unmute/set via pactl or amixer.
**Bug fixed:** Previously mute AND unmute both used `toggle` (calling "unmute" when muted would re-mute). Now uses explicit `1`/`0` for pactl, `mute`/`unmute` for amixer.

---

### `brightness_control` — Screen Brightness
**What:** Brightness up/down/set via brightnessctl (primary) or xrandr (fallback).
**Bug fixed:** xrandr fallback used hardcoded 0.9/0.7. Now reads current brightness from `xrandr --verbose` and applies relative ±0.10 delta.

---

### `universal_fallback` — Unknown Command Handler
**What:** Last-resort handler for commands the parser can't understand.
**Strategy (in order):**
1. URL detected → `xdg-open <url>`
2. First token is known binary → run as shell command
3. Any token is known binary → launch it
4. Log to `~/.config/desktop_automation/unknown_commands.txt` + print tip

---

### Other Tasks (brief)

| Task | What it does |
|---|---|
| `system_screenshot` | Saves screenshot (gnome-screenshot → grim → scrot → mss) |
| `lock_screen` | Locks screen (loginctl → gnome-screensaver → xdg-screensaver → dbus) |
| `system_power` | shutdown/restart/suspend/hibernate via systemctl |
| `open_system_app` | Launches any app by name, verifies via AT-SPI |
| `open_browser_and_visit` | Opens browser + navigates (CDP → Playwright → subprocess) |
| `run_command` | Runs raw shell command. Re-extracts from RAW input to preserve flags like `-la` |
| `hotkey` | Keyboard shortcuts via GUIController (X11/XWayland) |
| `system_hotkey` | Keyboard shortcuts via evdev/uinput (Wayland-native, any app) |
| `type_text` | Types text via GUIController |
| `create_folder` | Creates directory (default: ~/Desktop) |
| `file_operations` | delete/move/copy/rename files with pre/post verification |
| `folder_operations` | open/create/delete folders. Aliases: Downloads, Desktop, Pictures... |
| `organize_downloads` | Sorts ~/Downloads into Images/Documents/Archives/Code/Videos |
| `wait_seconds` | Pauses N seconds (max 300s safety cap) |
| `browser_action` | Runs Playwright action sequences (goto/click/type/wait/screenshot) |
| `canva_template` | Opens Canva/Figma, searches template, fills placeholders |

---

## 6. CORE COMPONENTS — DEEP DIVE

### `core/strategy_executor.py` — Self-Healing Engine

```python
# Every task has N strategies ordered by historical success rate
ex = StrategyExecutor("install_app")
ex.add(Strategy("gui_appcenter", gui_fn,  verify_spec=spec, max_retries=1))
# Memory records ok/fail count per strategy
# Next run: best-performing strategy runs first (learning without AI)
```

Memory keys: `strategy:install_app.gui_appcenter:ok` / `:err`

### `core/verifier.py` — Post-Action Verification

```python
# VerifySpec — what "success" looks like
VerifySpec(
    expect_text=["youtube"],       # OCR: these strings must appear on screen
    expect_app="chrome",           # AT-SPI: this app must be running
    expect_cmd="snap list vlc",    # CLI: this command must exit 0
    expect_image="template.png",   # OpenCV: this image must appear on screen
    settle_wait=1.5,               # Seconds to wait before checking
)
# ANY one method passing = VERIFIED
```

**browser_open_spec bug fixed:** Previously required ALL of "browser","chrome","brave","firefox" to appear simultaneously in OCR (impossible). Fixed to use `pgrep -f 'chromium|brave|firefox|chrome'` CLI check instead.

### `core/memory.py` — Persistent Learning Store

```python
memory.set("strategy:youtube", "youtube:ok", 5)   # 5 successes
memory.get("strategy:youtube", "youtube:err")      # failure count
# File: ~/.config/desktop_automation/task_memory.json
```

### `core/uinput_keyboard.py` — Wayland-Native Keyboard

```python
# Works on Wayland, X11, any native app
# Does NOT use xdotool (X11 only)
vk = VirtualKeyboard()
vk.type_text("hello world", cpm=1800)   # 1800 chars/minute
vk.press_key(evdev.ecodes.KEY_ENTER)
vk.hotkey(KEY_LEFTCTRL, KEY_C)
vk.close()
```

### `core/atspi_utils.py` — AT-SPI Accessibility Helpers

```python
# Find any widget in any app's accessibility tree
find_node(root, role=pyatspi.ROLE_PUSH_BUTTON, name_contains="install")
do_action(node)              # click, press, activate
set_text(node, "my text")   # type into text field
wait_for_app(["snap-store"], timeout=15)  # poll until app appears
```

### `core/workflow_engine.py` — Multi-Step Orchestrator

```python
# YAML supports both 'tasks:' and 'steps:' as key (auto-detected)
# Supports parallel:true for concurrent step execution
# Adaptive retries: steps that fail often automatically get more retries
engine = WorkflowEngine()
engine.run("daily_cleanup", "config/workflow.yaml")
```

---

## 7. CONFIGURATION

### `config/config.yaml`
```yaml
install:
  sudo_password: "rgukt"     # ← PASSWORD for App Center auth dialog

gui:
  safe_mode: true
  move_duration: 0.3

logging:
  level: "INFO"
  file: "logs/automation.log"
```

### `config/workflow.yaml`
Available named workflows:
- `daily_cleanup` — sort ~/Downloads by file type
- `browser_evening` — open bookmarks + screenshots
- `batch_process` — organize + move videos
- `install_essentials` — install VLC, GIMP, Inkscape
- `tile_windows` — window layout

```bash
python agent.py --workflow daily_cleanup
python agent.py --workflow browser_evening
```

---

## 8. SYSTEM REQUIREMENTS & ENVIRONMENT

| Item | Value |
|---|---|
| OS | Ubuntu Linux (GNOME desktop) |
| Session type | Wayland (GNOME 46.0) |
| Python | 3.12+ |
| Key system packages | `wmctrl`, `tesseract-ocr`, `xdotool` (optional), `evdev`, `pyatspi` |
| pyatspi path | `/usr/lib/python3/dist-packages` (system package, not pip) |
| AT-SPI bridge | Must be enabled for automation to work |
| Chrome CDP port | 9222 (WhatsApp/browser automation) |
| venv | `.venv/` in project root |

```bash
# Run with venv
source .venv/bin/activate
python agent.py "your command"
```

---

## 9. KNOWN BUGS FIXED IN THIS SESSION

| File | Bug | Fix |
|---|---|---|
| `install_app.py` | Missing `f` prefix in error string | Added `f` prefix |
| `volume_control.py` | mute/unmute both used `toggle` | Explicit `1`/`0` for pactl, `mute`/`unmute` for amixer |
| `whatsapp_send.py` | Raw message in URL (breaks special chars) | `urllib.parse.quote(message)` |
| `brightness_control.py` | xrandr fallback hardcoded 0.9/0.7 | Read current level, apply ±0.10 delta |
| `wait_seconds.py` | Missing try/except → `finish("error")` never called | Wrapped in try/except |
| `youtube_automation.py` | Missing `start()`/`finish()` logging | Added wrapper |
| `window_management.py` | Used wmctrl (X11 only) — zero windows on Wayland | Complete rewrite using AT-SPI + evdev |
| `verifier.py` `browser_open_spec` | Required ALL browser names in OCR simultaneously (impossible) | Changed to `pgrep` CLI check |
| `agent.py` open_browser/visit_url | URL dots stripped by normalizer ("github.com" → "github com") | Re-extract URL from raw input |
| `smart_parser.py` | 2-char keyword "sc" substring-matched inside "fullscreen" | Added `len(keyword) >= 3` guard |
| `smart_parser.py` | "minimize" fuzzy-matched "maximize" and triggered wrong intent | `window_maximize` min_score 0.20→0.30; `window_minimize` keywords reduced to 2 |
| `smart_parser.py` | `whatsapp_send` extraction got filler words as contact | Added `word_before:` rule + smart recovery in agent.py |
| `workflow_engine.py` | Read `steps:` but YAML had `tasks:` → Steps: 0 | Now accepts both `tasks:` and `steps:` |
| `atspi_install.py` | Fixed `time.sleep()` calls → slow | Replaced with event-driven `_await_node()` polling |
| `atspi_install.py` | Always killed and restarted App Center | Reuses if already running (saves ~20s) |

---

## 10. COMMAND EXAMPLES — FULL LIST

```bash
# ── App installation (GUI App Center) ──────────────────────────────────────────
python agent.py "install vlc"
python agent.py "install inkscape"
python agent.py "install gimp"
python agent.py "get discord"
python agent.py "download spotify"

# ── YouTube ───────────────────────────────────────────────────────────────────
python agent.py "play rrr naatu naatu on youtube"
python agent.py "yt pe rrr song chalao"          # Hindi
python agent.py "youtube pe trending music lagao" # Hindi mix

# ── WhatsApp ──────────────────────────────────────────────────────────────────
python agent.py "open browser darling to send hi msg"
python agent.py "watsap pe darling ko hi bhejo"   # Hindi classic
python agent.py "send hi to darling on whatsapp"
python agent.py "whatsapp darling hi"
python agent.py "watsap me bhai ko good morning bhejo"
python agent.py "send how are you to yaar on whatsapp"

# ── Screenshots ───────────────────────────────────────────────────────────────
python agent.py "take ss"
python agent.py "take screenshot"
python agent.py "screenshot lelo"                  # Hindi

# ── Browser ───────────────────────────────────────────────────────────────────
python agent.py "open browser"
python agent.py "go to github.com"
python agent.py "open browser and go to youtube.com"
python agent.py "search google for python tutorials"

# ── App control ───────────────────────────────────────────────────────────────
python agent.py "open calculator"
python agent.py "open file manager"
python agent.py "open vlc"
python agent.py "open terminal"

# ── Window management ─────────────────────────────────────────────────────────
python agent.py "minimize chrome"
python agent.py "maximize calculator"
python agent.py "fullscreen"                        # acts on active window
python agent.py "close calculator"
python agent.py "chhota karo window"               # Hindi: minimize

# ── Volume ────────────────────────────────────────────────────────────────────
python agent.py "volume up"
python agent.py "volume down"
python agent.py "mute"
python agent.py "unmute"
python agent.py "set volume to 60"
python agent.py "aawaz kam karo"                   # Hindi: lower volume
python agent.py "aawaz zyada karo"                 # Hindi: raise volume

# ── Brightness ────────────────────────────────────────────────────────────────
python agent.py "brightness up"
python agent.py "brightness down"
python agent.py "dim"
python agent.py "ujala kam karo"                   # Hindi: reduce brightness

# ── System ────────────────────────────────────────────────────────────────────
python agent.py "lock screen"
python agent.py "taala lagao"                      # Hindi: lock
python agent.py "shutdown"
python agent.py "restart"
python agent.py "reboot"                           # same as restart
python agent.py "suspend"

# ── Typing & keyboard ─────────────────────────────────────────────────────────
python agent.py "type hello world"
python agent.py "likhna test message"              # Hindi: type
python agent.py "press ctrl c"
python agent.py "ctrl alt delete"
python agent.py "alt tab"

# ── Files & folders ───────────────────────────────────────────────────────────
python agent.py "create folder MyPhotos"
python agent.py "banao folder test"                # Hindi: create
python agent.py "new folder Videos"
python agent.py "organize downloads"

# ── Shell commands ────────────────────────────────────────────────────────────
python agent.py "execute ls -la"
python agent.py "execute git status"
python agent.py "shell command mkdir test"

# ── Klavaro typing test ───────────────────────────────────────────────────────
python agent.py "open klavaro and velocity text typing task do it 100% accuracy and speed"
python agent.py "klavaro velocity"
python agent.py "klavaro adaptability"
python agent.py "klavaro basic"
python agent.py "klavaro fluidness"

# ── Compound commands ─────────────────────────────────────────────────────────
python agent.py "install vlc and then open it"
python agent.py "take ss and open browser"

# ── Workflows ─────────────────────────────────────────────────────────────────
python agent.py --workflow daily_cleanup
python agent.py --workflow browser_evening
python agent.py --workflow install_essentials

# ── Meta ──────────────────────────────────────────────────────────────────────
python agent.py --list                             # show all 25 tasks
```

---

## 11. ADDING A NEW TASK

**Step 1:** Create `tasks/my_task.py` with required API:
```python
"""Task description. Args: arg1 (str): ..."""
from loguru import logger
from core.logger import finish, start, notify

def setup() -> dict:
    return {}                     # return shared resources

def execute(args: dict, resources: dict) -> bool:
    task_name = "my_task"
    start(task_name)
    try:
        value = args.get("my_param", "default")
        # ... do the thing ...
        logger.info(f"✅ Done: {value}")
        notify(f"My task: {value}")
        finish("success", task_name)
        return True
    except Exception as exc:
        finish("error", task_name, err=exc)
        raise

def cleanup(resources: dict) -> None:
    pass
```

**Step 2:** Add intent to `core/smart_parser.py` INTENT_DEFS:
```python
"my_task": {
    "keywords": {"my", "task", "keyword"},
    "min_score": 0.20,
    "extract": {"my_param": "all_after:keyword"},
},
```

**Step 3:** Add executor to `agent.py` `_build_executor()`:
```python
if name == "my_task":
    val = params.get("my_param", "default")
    args = {"my_param": val}
    def fn(a, r):
        from tasks.my_task import execute
        return execute(a, r)
    ex = StrategyExecutor("my_task")
    ex.add(Strategy("my_strategy", fn, retry_wait=1))
    return ex, args, res
```

**Step 4:** Task auto-discovered on next run (no registration needed).

---

## 12. ADDING A NEW WORKFLOW

Edit `config/workflow.yaml`:
```yaml
my_workflow:
  description: "What this workflow does"
  on_error: notify     # ignore | notify | abort
  tasks:               # OR 'steps:' — both work
    - task: open_system_app
      args:
        app_name: calculator
    - task: wait_seconds
      args:
        seconds: 2
    - task: system_screenshot
      parallel: false  # set true for concurrent execution
      retries: 1
```

Run: `python agent.py --workflow my_workflow`

---

## 13. ARCHITECTURE DECISIONS & WHY

| Decision | Reason |
|---|---|
| **AT-SPI over xdotool/wmctrl** | Wayland-native. xdotool/wmctrl only work on X11 and return empty results on Wayland |
| **evdev/uinput over xdotool for keyboard** | Works on Wayland, native apps, GTK, Qt, Flutter — everywhere |
| **CDP (Chrome DevTools) for browser** | Reuses real logged-in Chrome session — no re-login, no QR codes for WhatsApp |
| **Rule-based parser (no LLM)** | Zero cost, zero internet, instant, deterministic, multilingual via WORD_MAP |
| **Event-driven waiting (_await_node)** | Fixed `time.sleep()` wastes seconds even when UI is ready in 0.3s |
| **GUI App Center for install** | User explicitly requested: open App Center → search → click → Install → password |
| **StrategyExecutor + Memory** | Learning without AI: history tells which strategy works best, auto-reorders |
| **`word_before:` extraction rule** | "darling to send hi" → contact is BEFORE "to", not after it |
| **Greeting-first message extraction** | Parser extraction of WhatsApp messages is unreliable; known greetings are unambiguous |
| **`len(keyword) >= 3` in fuzzy match** | Short keywords like "sc", "ss" were substring-matching inside long words ("fullscreen") |
| **window_maximize min_score = 0.30** | "minimize" fuzzy-scores 0.75 against "maximize" → scored 0.25 which beat window_minimize's 0.20. Raising to 0.30 blocks this. |

---

## 14. CURRENT LIMITATIONS (HONEST)

| Limitation | Impact |
|---|---|
| **No goal planner** | Must tell the agent exactly what tasks. Can't say "plan my morning" and have it figure out the steps |
| **No semantic screen understanding** | Cannot understand an unfamiliar UI. Must use AT-SPI (known apps) or OCR (text only) |
| **No background daemon** | Agent only runs when user gives a command. Cannot react to emails, notifications, time |
| **No inter-task data flow** | Task A's output cannot automatically feed into Task B |
| **Wayland: no pixel coordinates** | Cannot click at X,Y pixel position in native Wayland apps without xdotool |
| **GNOME Shell.Eval disabled in GNOME 46** | gdbus/Shell.Eval returns (false,'') — can't run arbitrary JS in Shell |
| **WhatsApp requires logged-in Chrome** | Chrome must have WhatsApp Web session active. Cannot log in automatically |
| **Klavaro: AT-SPI text must be available** | If AT-SPI returns empty, falls back to fail (no OCR fallback for exercise text) |
| **No vision model integration** | Cannot click "the blue button with the shopping cart icon" on an unknown webpage |

---

## 15. ROADMAP — FULL IMPLEMENTATION PLAN

### Level Map

```
Level 1  DONE ✅   Single command → single task
Level 2  NEXT     Goal → auto-plan → parallel tasks → result sharing
Level 3  FUTURE   24/7 daemon → event triggers → reactive autonomy
Level 4  ADVANCED Semantic vision → any screen → full self-direction
```

---

## PHASE 1 — LEVEL 1.5: Stabilisation (COMPLETE)

**Status: Done ✅**
- 25 tasks working, all bugs fixed, all tested
- SmartParser with 23 intents + Hindi/Telugu WORD_MAP
- StrategyExecutor with memory-based learning
- AT-SPI + evdev Wayland-native automation
- App Center GUI install (password: rgukt from config.yaml)
- Window management via AT-SPI (no wmctrl)
- WhatsApp smart contact/message extraction

---

## PHASE 2 — LEVEL 2: Goal Planner + Parallel Execution

### What becomes possible after Phase 2

```bash
# User says ONE goal — agent plans and executes everything
python agent.py "prepare my morning routine"
# → Agent plans: [open news tab] [open email] [play music] [take screenshot]
# → Runs news + email + music ALL IN PARALLEL
# → Waits for all, reports summary

python agent.py "research python and open vscode"
# → [search_web: python tutorials] → result → [open_browser: result_url]
# → [open_system_app: vscode]  (in parallel with browser)

python agent.py --parallel "install vlc, install gimp, install inkscape"
# → 3 App Center installs running simultaneously
```

### New files to create

```
core/
  task_dag.py        ← Task node + dependency graph
  goal_planner.py    ← NL goal → TaskDAG
  parallel_runner.py ← ThreadPool DAG executor
  result_bus.py      ← Inter-task result sharing
  world_model.py     ← Desktop state snapshot
```

---

### FILE: `core/task_dag.py`

```python
"""
TaskDAG — Directed Acyclic Graph of automation tasks.

Each node = one task to execute.
Edges = dependencies (node B waits for node A to complete first).
Nodes with no pending deps = can run immediately (in parallel).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import threading

@dataclass
class TaskNode:
    id: str                        # unique identifier e.g. "open_browser_0"
    intent: str                    # intent name e.g. "open_browser"
    args: dict                     # task arguments
    deps: list[str] = field(default_factory=list)  # IDs of tasks this waits for
    status: str = "pending"        # pending | running | done | failed
    result: Any = None             # output value (can be input to another task)
    error: str = ""

class TaskDAG:
    def __init__(self) -> None:
        self.nodes: dict[str, TaskNode] = {}
        self._lock = threading.Lock()

    def add(self, node: TaskNode) -> None:
        with self._lock:
            self.nodes[node.id] = node

    def ready(self) -> list[TaskNode]:
        """Return nodes whose all dependencies are done."""
        with self._lock:
            return [
                n for n in self.nodes.values()
                if n.status == "pending"
                and all(self.nodes[d].status == "done" for d in n.deps if d in self.nodes)
            ]

    def mark_running(self, node_id: str) -> None:
        with self._lock:
            self.nodes[node_id].status = "running"

    def mark_done(self, node_id: str, result: Any = None) -> None:
        with self._lock:
            self.nodes[node_id].status = "done"
            self.nodes[node_id].result = result

    def mark_failed(self, node_id: str, error: str = "") -> None:
        with self._lock:
            self.nodes[node_id].status = "failed"
            self.nodes[node_id].error = error

    def is_complete(self) -> bool:
        with self._lock:
            return all(n.status in ("done", "failed") for n in self.nodes.values())

    def has_failures(self) -> bool:
        with self._lock:
            return any(n.status == "failed" for n in self.nodes.values())

    def summary(self) -> dict:
        with self._lock:
            return {
                "total": len(self.nodes),
                "done": sum(1 for n in self.nodes.values() if n.status == "done"),
                "failed": sum(1 for n in self.nodes.values() if n.status == "failed"),
                "pending": sum(1 for n in self.nodes.values() if n.status == "pending"),
            }
```

---

### FILE: `core/result_bus.py`

```python
"""
ResultBus — Thread-safe shared result store.

Task A publishes its output. Task B (which depends on A) reads it.
This enables data flow between tasks:
  search_web → results → open_browser(url=results[0])
"""
import threading
import time
from typing import Any

class ResultBus:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._lock  = threading.Lock()
        self._event = threading.Event()

    def publish(self, task_id: str, result: Any) -> None:
        with self._lock:
            self._store[task_id] = result
        self._event.set()
        self._event.clear()

    def get(self, task_id: str, default: Any = None) -> Any:
        with self._lock:
            return self._store.get(task_id, default)

    def wait_for(self, task_id: str, timeout: float = 30.0) -> Any:
        """Block until task_id result is published or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            val = self.get(task_id)
            if val is not None:
                return val
            time.sleep(0.2)
        return None

# Global singleton — imported by tasks that need to share data
result_bus = ResultBus()
```

---

### FILE: `core/parallel_runner.py`

```python
"""
ParallelRunner — Executes a TaskDAG using a thread pool.

Algorithm:
  1. Find all nodes with no pending deps → submit to pool
  2. As each completes → publish result → unlock dependents
  3. Repeat until DAG is complete or timeout
"""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor, Future
from loguru import logger
from core.task_dag import TaskDAG, TaskNode
from core.result_bus import result_bus
from agent import _build_executor   # reuse existing executor builder

class ParallelRunner:
    def __init__(self, max_workers: int = 4, timeout: float = 300.0) -> None:
        self.max_workers = max_workers
        self.timeout     = timeout

    def run(self, dag: TaskDAG) -> dict:
        futures: dict[str, Future] = {}
        t0 = time.time()

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not dag.is_complete():
                # Submit all ready nodes
                for node in dag.ready():
                    if node.id not in futures:
                        dag.mark_running(node.id)
                        logger.info(f"[ParallelRunner] Submitting: {node.id} ({node.intent})")
                        fut = pool.submit(self._run_node, node, dag)
                        futures[node.id] = fut

                # Timeout guard
                if time.time() - t0 > self.timeout:
                    logger.error("ParallelRunner: timeout reached")
                    break

                time.sleep(0.1)   # tight poll loop

        return dag.summary()

    def _run_node(self, node: TaskNode, dag: TaskDAG) -> bool:
        """Execute one task node inside a thread."""
        try:
            from core.smart_parser import ParsedIntent
            # Reconstruct a minimal ParsedIntent for _build_executor
            intent = ParsedIntent(
                intent=node.intent,
                params=node.args,
                confidence=1.0,
                raw_input=str(node.args),
                normalized_input=str(node.args),
            )
            executor, args, resources = _build_executor(intent)
            ok = executor.run(args, resources)
            result_bus.publish(node.id, {"ok": ok, "args": args})
            if ok:
                dag.mark_done(node.id, result={"ok": True})
            else:
                dag.mark_failed(node.id, error="Task returned False")
            return ok
        except Exception as exc:
            dag.mark_failed(node.id, error=str(exc))
            result_bus.publish(node.id, {"ok": False, "error": str(exc)})
            logger.error(f"[ParallelRunner] Node {node.id} failed: {exc}")
            return False
```

---

### FILE: `core/world_model.py`

```python
"""
WorldModel — Real-time snapshot of desktop state.

Used by: GoalPlanner ("what's currently running?"),
         Replanner ("what changed after failure?"),
         Daemon ("is my goal already achieved?")
"""
from __future__ import annotations
import subprocess
import sys
import time
from loguru import logger

sys.path.insert(0, "/usr/lib/python3/dist-packages")

class WorldModel:
    def snapshot(self) -> dict:
        """Take a full snapshot of current desktop state."""
        return {
            "open_apps":       self.open_apps(),
            "active_window":   self.active_window_title(),
            "running_procs":   self.running_processes(),
            "timestamp":       time.time(),
        }

    def open_apps(self) -> list[str]:
        """All apps visible in AT-SPI accessibility tree."""
        apps = []
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app and app.name:
                    apps.append(app.name)
        except Exception as exc:
            logger.debug(f"WorldModel.open_apps: {exc}")
        return apps

    def active_window_title(self) -> str:
        """Title of the currently focused window."""
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None: continue
                for i in range(app.childCount):
                    try:
                        frame = app.getChildAtIndex(i)
                        if frame and frame.getState().contains(pyatspi.STATE_ACTIVE):
                            return frame.name or ""
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug(f"WorldModel.active_window: {exc}")
        return ""

    def is_app_running(self, name: str) -> bool:
        r = subprocess.run(["pgrep", "-fi", name],
                          capture_output=True, check=False)
        return r.returncode == 0

    def running_processes(self) -> list[str]:
        r = subprocess.run(["pgrep", "-a", "-l", "-u", str(__import__('os').getuid())],
                          capture_output=True, text=True, check=False)
        return [line.split(None, 1)[-1] for line in r.stdout.strip().splitlines() if line]

world = WorldModel()   # singleton
```

---

### FILE: `core/goal_planner.py`

```python
"""
GoalPlanner — Converts a high-level natural language goal into a TaskDAG.

Strategy:
  1. Parse multi-intent (existing parse_multi)
  2. Detect dependency patterns (search→open, install→launch, etc.)
  3. Mark independent tasks as parallel
  4. Return executable TaskDAG
"""
from __future__ import annotations
import re
from loguru import logger
from core.smart_parser import smart_parser, ParsedIntent
from core.task_dag import TaskDAG, TaskNode

# Rules: if intent_A result feeds intent_B → B depends on A
DEPENDENCY_RULES: list[tuple[str, str]] = [
    ("search_web",    "visit_url"),      # search → open result URL
    ("search_web",    "open_browser"),   # search → open in browser
    ("install_app",   "open_app"),       # install → then open
    ("open_browser",  "whatsapp_send"),  # open browser → then WhatsApp
    ("screenshot",    "organize_downloads"),  # screenshot → organize
]

# Goals that map directly to multi-task plans (without needing parser)
GOAL_TEMPLATES: dict[str, list[dict]] = {
    "morning routine": [
        {"intent": "open_browser",    "args": {"url": "https://news.google.com"}},
        {"intent": "open_browser",    "args": {"url": "https://mail.google.com"}},
        {"intent": "youtube",         "args": {"search_query": "morning motivation"}},
        {"intent": "screenshot",      "args": {}},
    ],
    "work setup": [
        {"intent": "open_app",        "args": {"app_name": "vscode"}},
        {"intent": "open_browser",    "args": {"url": "https://github.com"}},
        {"intent": "open_app",        "args": {"app_name": "terminal"}},
    ],
    "cleanup": [
        {"intent": "organize_downloads", "args": {}},
        {"intent": "screenshot",         "args": {}},
    ],
}

class GoalPlanner:
    def plan(self, raw_goal: str) -> TaskDAG:
        dag = TaskDAG()

        # ── 1. Check goal templates first ────────────────────────────────────
        goal_lo = raw_goal.lower()
        for template_key, task_list in GOAL_TEMPLATES.items():
            if template_key in goal_lo:
                logger.info(f"GoalPlanner: matched template '{template_key}'")
                for i, t in enumerate(task_list):
                    node = TaskNode(
                        id=f"{t['intent']}_{i}",
                        intent=t["intent"],
                        args=t["args"],
                        deps=[],   # all parallel (no deps in templates)
                    )
                    dag.add(node)
                return dag

        # ── 2. Use SmartParser to split into intents ──────────────────────────
        intents: list[ParsedIntent] = smart_parser.parse_multi(raw_goal)
        if not intents:
            logger.warning(f"GoalPlanner: no intents found for {raw_goal!r}")
            return dag

        nodes: list[TaskNode] = []
        for i, intent in enumerate(intents):
            node = TaskNode(
                id=f"{intent.intent}_{i}",
                intent=intent.intent,
                args=intent.params,
                deps=[],
            )
            nodes.append(node)

        # ── 3. Apply dependency rules ─────────────────────────────────────────
        for i, node_a in enumerate(nodes):
            for j, node_b in enumerate(nodes):
                if i >= j: continue
                for rule_a, rule_b in DEPENDENCY_RULES:
                    if node_a.intent == rule_a and node_b.intent == rule_b:
                        node_b.deps.append(node_a.id)
                        logger.info(f"  Dep: {node_b.id} waits for {node_a.id}")

        # ── 4. Add all nodes to DAG ───────────────────────────────────────────
        for node in nodes:
            dag.add(node)

        logger.info(f"GoalPlanner: DAG has {len(nodes)} nodes")
        return dag

goal_planner = GoalPlanner()   # singleton
```

---

### How to wire Phase 2 into `agent.py`

Add `--goal` flag to CLI:

```python
# In main() in agent.py:
parser.add_argument("--goal", metavar="GOAL",
                    help="High-level goal — auto-planned and parallel-executed")
parser.add_argument("--parallel", action="store_true",
                    help="Run compound command tasks in parallel")

# New handler:
if args.goal:
    from core.goal_planner import goal_planner
    from core.parallel_runner import ParallelRunner
    dag = goal_planner.plan(args.goal)
    runner = ParallelRunner(max_workers=4)
    summary = runner.run(dag)
    print(f"\n✅ Goal complete: {summary}")
    sys.exit(0 if summary['failed'] == 0 else 1)
```

### New commands after Phase 2

```bash
python agent.py --goal "prepare morning routine"
python agent.py --goal "work setup"
python agent.py --goal "search python tutorials and open first result"
python agent.py --goal "install vlc and open it and play a song"
python agent.py --goal "cleanup"

# Parallel flag for explicit multi-task:
python agent.py --parallel "install vlc, install gimp, install inkscape"
```

### Install needed for Phase 2
```bash
# Nothing new — uses stdlib threading + concurrent.futures
# Already available: threading, queue, dataclasses
```

---

## PHASE 3 — LEVEL 3: Daemon + Event Triggers

### What becomes possible after Phase 3

```bash
# Start the daemon
python agent.py --daemon

# Register triggers (persisted to config/triggers.yaml)
python agent.py --watch "time:09:00 → morning routine"
python agent.py --watch "file:~/Downloads/*.pdf → organize downloads"
python agent.py --watch "notify:boss → reply hi received"
python agent.py --watch "battery:20 → suspend"
python agent.py --watch "app:klavaro_closes → take screenshot"

# List active triggers
python agent.py --triggers

# Stop daemon
python agent.py --daemon-stop
```

### New files to create

```
core/
  daemon.py          ← Background event loop (runs 24/7)
  trigger_engine.py  ← Trigger types + condition checkers
  replanner.py       ← Adaptive replanning on task failure

config/
  triggers.yaml      ← Persisted trigger definitions
```

---

### FILE: `core/trigger_engine.py`

```python
"""
Trigger Engine — defines condition → goal pairs.
Each trigger is checked every N seconds by the daemon.
"""
from __future__ import annotations
import re
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from loguru import logger

@dataclass
class TriggerResult:
    fired: bool
    context: dict   # extra data to pass to the goal (e.g. email content)

class Trigger(ABC):
    name: str
    goal: str       # what to do when this fires, e.g. "take screenshot"
    cooldown: float = 60.0  # minimum seconds between firings
    _last_fired: float = 0.0

    @abstractmethod
    def check(self) -> TriggerResult:
        """Return TriggerResult. Called every poll cycle by daemon."""
        ...

    def ready(self) -> bool:
        return time.time() - self._last_fired > self.cooldown

    def fired(self) -> None:
        self._last_fired = time.time()


class TimeTrigger(Trigger):
    """
    Fires at a specific clock time (HH:MM) or interval (every Nm).
    Examples:
      time="09:00"  fires daily at 9am
      time="every 30m" fires every 30 minutes
    """
    def __init__(self, name: str, goal: str, time_spec: str):
        self.name = name; self.goal = goal; self.time_spec = time_spec
        self._last_hhmm = ""

    def check(self) -> TriggerResult:
        now = datetime.now()
        hhmm = now.strftime("%H:%M")
        if self.time_spec == hhmm and hhmm != self._last_hhmm:
            self._last_hhmm = hhmm
            return TriggerResult(fired=True, context={"time": hhmm})
        if self.time_spec.startswith("every "):
            mins = int(re.search(r'(\d+)', self.time_spec).group(1))
            if now.minute % mins == 0 and now.second < 5:
                return TriggerResult(fired=True, context={"interval": mins})
        return TriggerResult(fired=False, context={})


class FileTrigger(Trigger):
    """
    Fires when a new file appears in a watched directory.
    Uses polling (no inotifywait required).
    """
    def __init__(self, name: str, goal: str, watch_path: str, pattern: str = "*"):
        self.name = name; self.goal = goal
        self.watch_path = watch_path; self.pattern = pattern
        self._seen: set = set()

    def check(self) -> TriggerResult:
        import glob
        from pathlib import Path
        current = set(glob.glob(f"{self.watch_path}/{self.pattern}"))
        new_files = current - self._seen
        self._seen = current
        if new_files:
            return TriggerResult(fired=True, context={"new_files": list(new_files)})
        return TriggerResult(fired=False, context={})


class NotificationTrigger(Trigger):
    """
    Fires when a desktop notification matches a pattern.
    Listens to D-Bus org.freedesktop.Notifications.
    """
    def __init__(self, name: str, goal: str, pattern: str):
        self.name = name; self.goal = goal; self.pattern = re.compile(pattern, re.I)
        self._queue: list[str] = []
        self._start_dbus_listener()

    def _start_dbus_listener(self):
        import threading
        def listen():
            try:
                proc = subprocess.Popen(
                    ["dbus-monitor", "interface=org.freedesktop.Notifications"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
                )
                for line in proc.stdout:
                    if 'string' in line:
                        self._queue.append(line)
            except Exception as exc:
                logger.debug(f"NotificationTrigger listener: {exc}")
        threading.Thread(target=listen, daemon=True).start()

    def check(self) -> TriggerResult:
        while self._queue:
            line = self._queue.pop(0)
            if self.pattern.search(line):
                return TriggerResult(fired=True, context={"notification": line.strip()})
        return TriggerResult(fired=False, context={})


class AppClosedTrigger(Trigger):
    """Fires when a specific app stops running."""
    def __init__(self, name: str, goal: str, app_name: str):
        self.name = name; self.goal = goal; self.app_name = app_name
        self._was_running = False

    def check(self) -> TriggerResult:
        r = subprocess.run(["pgrep", "-fi", self.app_name],
                          capture_output=True, check=False)
        running = r.returncode == 0
        fired = self._was_running and not running
        self._was_running = running
        return TriggerResult(fired=fired, context={"app": self.app_name})


class BatteryTrigger(Trigger):
    """Fires when battery drops below threshold %."""
    def __init__(self, name: str, goal: str, threshold: int = 20):
        self.name = name; self.goal = goal; self.threshold = threshold

    def check(self) -> TriggerResult:
        try:
            r = subprocess.run(["cat", "/sys/class/power_supply/BAT0/capacity"],
                              capture_output=True, text=True, check=False)
            level = int(r.stdout.strip())
            if level <= self.threshold:
                return TriggerResult(fired=True, context={"battery": level})
        except Exception:
            pass
        return TriggerResult(fired=False, context={})
```

---

### FILE: `core/daemon.py`

```python
"""
AgentDaemon — Background event loop.

Runs as a thread (or systemd service). Checks all registered triggers
every POLL_INTERVAL seconds. When a trigger fires, executes its goal
via GoalPlanner → ParallelRunner.
"""
from __future__ import annotations
import signal
import sys
import threading
import time
import yaml
from pathlib import Path
from loguru import logger
from core.trigger_engine import Trigger
from core.goal_planner import goal_planner
from core.parallel_runner import ParallelRunner

POLL_INTERVAL = 5.0           # check triggers every 5 seconds
TRIGGERS_FILE = Path("config/triggers.yaml")

class AgentDaemon:
    def __init__(self) -> None:
        self._triggers: list[Trigger] = []
        self._running  = False
        self._thread: threading.Thread | None = None
        self._runner   = ParallelRunner(max_workers=4)

    def register(self, trigger: Trigger) -> None:
        self._triggers.append(trigger)
        logger.info(f"Daemon: registered trigger '{trigger.name}' → '{trigger.goal}'")

    def load_triggers_from_yaml(self) -> None:
        """Load triggers.yaml and register all triggers."""
        if not TRIGGERS_FILE.exists():
            return
        with open(TRIGGERS_FILE) as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("triggers", []):
            self._register_from_dict(entry)

    def _register_from_dict(self, entry: dict) -> None:
        kind = entry.get("type", "")
        name = entry.get("name", kind)
        goal = entry.get("goal", "take screenshot")
        if kind == "time":
            from core.trigger_engine import TimeTrigger
            self.register(TimeTrigger(name, goal, entry["time"]))
        elif kind == "file":
            from core.trigger_engine import FileTrigger
            self.register(FileTrigger(name, goal, entry["path"], entry.get("pattern","*")))
        elif kind == "notification":
            from core.trigger_engine import NotificationTrigger
            self.register(NotificationTrigger(name, goal, entry["pattern"]))
        elif kind == "app_closed":
            from core.trigger_engine import AppClosedTrigger
            self.register(AppClosedTrigger(name, goal, entry["app"]))
        elif kind == "battery":
            from core.trigger_engine import BatteryTrigger
            self.register(BatteryTrigger(name, goal, int(entry.get("threshold", 20))))

    def start(self) -> None:
        """Start event loop in background thread."""
        self.load_triggers_from_yaml()
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"Daemon started ({len(self._triggers)} triggers)")

        # Keep alive until SIGINT/SIGTERM
        signal.signal(signal.SIGINT,  lambda *_: self.stop())
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        while self._running:
            time.sleep(1)

    def stop(self) -> None:
        self._running = False
        logger.info("Daemon stopped")
        sys.exit(0)

    def _loop(self) -> None:
        """Inner event loop — checks triggers every POLL_INTERVAL seconds."""
        while self._running:
            for trigger in self._triggers:
                try:
                    if not trigger.ready():
                        continue
                    result = trigger.check()
                    if result.fired:
                        logger.info(f"Trigger fired: '{trigger.name}' → '{trigger.goal}'")
                        trigger.fired()
                        self._execute_goal(trigger.goal, result.context)
                except Exception as exc:
                    logger.error(f"Trigger '{trigger.name}' check failed: {exc}")
            time.sleep(POLL_INTERVAL)

    def _execute_goal(self, goal: str, context: dict) -> None:
        """Plan and execute goal triggered by an event."""
        try:
            dag = goal_planner.plan(goal)
            self._runner.run(dag)
        except Exception as exc:
            logger.error(f"Daemon goal execution failed: {exc}")

daemon = AgentDaemon()   # singleton
```

---

### FILE: `core/replanner.py`

```python
"""
Replanner — Adaptive recovery when a task fails.

When a task node fails:
  1. Examine what failed and why (from error message + world model)
  2. Generate an alternative path to achieve same sub-goal
  3. Insert new nodes into the running DAG
  4. Continue execution
"""
from __future__ import annotations
from loguru import logger
from core.task_dag import TaskDAG, TaskNode
from core.world_model import world

# Alternative strategies for common failures
ALTERNATIVE_STRATEGIES: dict[str, list[dict]] = {
    "open_browser": [
        {"intent": "run_command", "args": {"command": "google-chrome"}},
        {"intent": "run_command", "args": {"command": "brave-browser"}},
        {"intent": "run_command", "args": {"command": "firefox"}},
    ],
    "install_app": [
        # If App Center fails, try CLI
        {"intent": "run_command", "args_template": "sudo snap install {app_name}"},
        {"intent": "run_command", "args_template": "sudo apt install -y {app_name}"},
    ],
    "youtube": [
        # If YouTube via CDP fails, try plain browser
        {"intent": "open_browser", "args_template": "https://www.youtube.com/results?search_query={query}"},
    ],
}

class Replanner:
    def on_failure(self, failed_node: TaskNode, dag: TaskDAG) -> bool:
        """
        Try to recover from a failed node.
        Returns True if recovery nodes were injected into the DAG.
        """
        alternatives = ALTERNATIVE_STRATEGIES.get(failed_node.intent, [])
        if not alternatives:
            logger.warning(f"Replanner: no alternatives for {failed_node.intent}")
            return False

        snap = world.snapshot()
        logger.info(f"Replanner: injecting {len(alternatives)} alternatives for '{failed_node.id}'")

        # Chain alternatives as sequential fallbacks
        prev_dep = []  # first alternative has no deps (run immediately)
        for i, alt in enumerate(alternatives):
            # Fill args template
            args = alt.get("args", {})
            if "args_template" in alt:
                template = alt["args_template"]
                for k, v in failed_node.args.items():
                    template = template.replace(f"{{{k}}}", str(v))
                args = {"command": template} if failed_node.intent == "run_command" else {"url": template}

            new_node = TaskNode(
                id=f"{failed_node.id}_alt{i}",
                intent=alt["intent"],
                args=args,
                deps=prev_dep,
            )
            dag.add(new_node)
            # Each subsequent alternative depends on the previous one failing
            # (simplified: just chain them, stop when one succeeds)
            prev_dep = [new_node.id]

        return True

replanner = Replanner()   # singleton
```

---

### FILE: `config/triggers.yaml`

```yaml
# Agent daemon trigger definitions
# Run: python agent.py --daemon

triggers:

  # Take screenshot every morning at 9am
  - name: morning_screenshot
    type: time
    time: "09:00"
    goal: "take screenshot"

  # Organize downloads when new PDF appears
  - name: pdf_organizer
    type: file
    path: /home/rajesh/Downloads
    pattern: "*.pdf"
    goal: "organize downloads"

  # Auto-suspend when battery low
  - name: low_battery
    type: battery
    threshold: 15
    goal: "suspend"

  # Screenshot when Klavaro closes (captures results)
  - name: klavaro_results
    type: app_closed
    app: klavaro
    goal: "take screenshot"

  # When boss notification appears: screenshot + reply
  - name: boss_notification
    type: notification
    pattern: "boss|urgent|meeting"
    goal: "take screenshot"
```

---

### Wire Phase 3 into `agent.py`

```python
# In main():
parser.add_argument("--daemon",       action="store_true", help="Start background daemon")
parser.add_argument("--daemon-stop",  action="store_true", help="Stop running daemon")
parser.add_argument("--watch",        metavar="SPEC",       help="Add trigger: 'time:09:00 → take screenshot'")
parser.add_argument("--triggers",     action="store_true", help="List active triggers")

if args.daemon:
    from core.daemon import daemon
    daemon.start()   # blocks until SIGINT

if args.watch:
    # Parse spec: "time:09:00 → morning routine"
    import re, yaml
    m = re.match(r'(\w+):(.+?)\s*→\s*(.+)', args.watch)
    if m:
        kind, spec, goal = m.groups()
        entry = {"type": kind, "name": f"{kind}_{spec}", "goal": goal, kind: spec}
        # Append to triggers.yaml
        from pathlib import Path
        tf = Path("config/triggers.yaml")
        data = yaml.safe_load(tf.read_text()) if tf.exists() else {"triggers": []}
        data["triggers"].append(entry)
        tf.write_text(yaml.dump(data))
        print(f"Trigger added: {kind}:{spec} → {goal}")
```

### Install needed for Phase 3
```bash
pip install schedule                     # already available
pip install pyyaml                       # likely already available
# inotifywait (optional, we use polling instead):
sudo apt install inotify-tools
```

### New commands after Phase 3
```bash
python agent.py --daemon                           # start 24/7 background agent
python agent.py --watch "time:09:00 → morning routine"
python agent.py --watch "file:~/Downloads → organize downloads"
python agent.py --watch "notify:boss → take screenshot"
python agent.py --watch "battery:15 → suspend"
python agent.py --triggers                         # list all triggers
python agent.py --daemon-stop                      # stop daemon
```

---

## PHASE 4 — LEVEL 4: Semantic Vision + Full Autonomy

### What becomes possible after Phase 4

```bash
# Agent can understand ANY screen — no pre-configured UI
python agent.py "click the blue Submit button on the form"
python agent.py "fill in the registration form with my name Rajesh"
python agent.py "find the best offer on Flipkart for headphones"
python agent.py "what is open on my screen right now?"
```

### New files to create

```
core/
  semantic_vision.py   ← Vision model interface (Ollama/API)
  screen_reader.py     ← Describe screen in natural language
  ui_navigator.py      ← Click any element by description
```

---

### FILE: `core/semantic_vision.py`

```python
"""
SemanticVision — Understand any UI element by natural language description.

Three tiers (tried in order):
  Tier 1: AT-SPI (fast, exact, works for GTK apps)
  Tier 2: OCR text matching (find text on screen)
  Tier 3: Vision model (Ollama llava / API) — any app, any UI
"""
from __future__ import annotations
import base64
import json
import subprocess
from loguru import logger
from core.logger import take_screenshot
from core.atspi_utils import find_node, do_action, wait_for_app

class SemanticVision:

    # ── Tier 1: AT-SPI direct ─────────────────────────────────────────────────
    def find_by_atspi(self, description: str, app_name: str = "") -> object | None:
        """
        Find any UI element by natural language description.
        Tries: exact name match → partial match → role match.
        """
        import sys, pyatspi
        sys.path.insert(0, "/usr/lib/python3/dist-packages")
        words = description.lower().split()
        desktop = pyatspi.Registry.getDesktop(0)
        for app in desktop:
            if app is None: continue
            if app_name and app_name.lower() not in (app.name or "").lower(): continue
            # Walk all nodes, score by word overlap
            best_score, best_node = 0, None
            from core.klavaro_automation import _walk
            for _, node in _walk(app):
                node_name = (node.name or "").lower()
                score = sum(1 for w in words if w in node_name)
                if score > best_score:
                    best_score, best_node = score, node
            if best_node and best_score > 0:
                return best_node
        return None

    # ── Tier 2: OCR text matching ─────────────────────────────────────────────
    def find_by_ocr(self, description: str) -> tuple[int, int] | None:
        """
        Take screenshot, OCR it, find bounding box of matching text.
        Returns (x, y) center coordinates.
        """
        try:
            import pytesseract
            from PIL import Image
            import cv2
            ss = take_screenshot(name="semantic_vision")
            img = cv2.imread(ss)
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            words = description.lower().split()
            for i, text in enumerate(data['text']):
                if any(w in text.lower() for w in words) and int(data['conf'][i]) > 50:
                    x = data['left'][i] + data['width'][i] // 2
                    y = data['top'][i] + data['height'][i] // 2
                    return (x, y)
        except Exception as exc:
            logger.debug(f"OCR find failed: {exc}")
        return None

    # ── Tier 3: Vision model (Ollama) ─────────────────────────────────────────
    def find_by_vision_model(self, description: str, model: str = "llava") -> tuple[int, int] | None:
        """
        Use a local vision model (Ollama) to locate UI elements.
        Returns (x, y) pixel coordinates.
        Requires: ollama pull llava
        """
        try:
            ss = take_screenshot(name="vision_model")
            with open(ss, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()

            prompt = (
                f"Look at this desktop screenshot. "
                f"Find the UI element described as: '{description}'. "
                f"Return ONLY a JSON object with the pixel coordinates: "
                f'{{"x": <int>, "y": <int>}}. '
                f"If not found, return {{\"x\": -1, \"y\": -1}}"
            )
            payload = json.dumps({
                "model": model,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
            })
            r = subprocess.run(
                ["curl", "-s", "-X", "POST",
                 "http://localhost:11434/api/generate",
                 "-d", payload],
                capture_output=True, text=True, timeout=30
            )
            resp = json.loads(r.stdout)
            result = json.loads(resp.get("response", "{}"))
            x, y = int(result.get("x", -1)), int(result.get("y", -1))
            if x > 0 and y > 0:
                logger.info(f"Vision model found '{description}' at ({x}, {y})")
                return (x, y)
        except Exception as exc:
            logger.debug(f"Vision model failed: {exc}")
        return None

    # ── Unified interface ──────────────────────────────────────────────────────
    def find_and_click(self, description: str, app_name: str = "") -> bool:
        """
        Try all three tiers to find and click the described element.
        Returns True if clicked.
        """
        from core.gui_controller import GUIController
        gui = GUIController()

        # Tier 1: AT-SPI
        node = self.find_by_atspi(description, app_name)
        if node:
            do_action(node)
            logger.info(f"✅ Clicked '{description}' via AT-SPI")
            return True

        # Tier 2: OCR
        coords = self.find_by_ocr(description)
        if coords:
            gui.click(*coords)
            logger.info(f"✅ Clicked '{description}' via OCR at {coords}")
            return True

        # Tier 3: Vision model
        coords = self.find_by_vision_model(description)
        if coords:
            gui.click(*coords)
            logger.info(f"✅ Clicked '{description}' via vision model at {coords}")
            return True

        logger.error(f"❌ Could not find '{description}' via any method")
        return False

semantic_vision = SemanticVision()   # singleton
```

### Install needed for Phase 4
```bash
# Local vision model (free, private, no internet):
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull llava          # 4GB model, works offline
# OR smaller:
ollama pull moondream      # 1.7GB, faster

# Python binding:
pip install ollama
```

---

## IMPLEMENTATION SEQUENCE — EXACT ORDER TO BUILD

```
Week 1:  core/task_dag.py         (data structure, no deps)
         core/result_bus.py       (thread-safe store, no deps)
         core/world_model.py      (AT-SPI snapshot, no deps)

Week 2:  core/goal_planner.py     (uses task_dag + smart_parser)
         core/parallel_runner.py  (uses task_dag + result_bus + agent)
         Test: python agent.py --goal "morning routine"

Week 3:  core/trigger_engine.py   (trigger types, standalone)
         config/triggers.yaml     (trigger definitions)

Week 4:  core/daemon.py           (uses trigger_engine + goal_planner)
         core/replanner.py        (uses task_dag + world_model)
         Wire --daemon into agent.py
         Test: python agent.py --daemon

Week 5:  core/semantic_vision.py  (AT-SPI + OCR tiers, no Ollama needed)
         Test AT-SPI + OCR tiers

Week 6:  Install Ollama + llava
         Wire vision model tier
         Test: python agent.py "click the Submit button"

Week 7+: Multi-agent coordination
         Self-improvement loop
         Advanced goal decomposition
```

---

## DEPENDENCIES SUMMARY

| Phase | apt install | pip install | Already available |
|---|---|---|---|
| Phase 2 (Goal+DAG) | nothing | nothing | threading, concurrent.futures, queue |
| Phase 3 (Daemon) | `inotify-tools` (optional) | `pyyaml` | schedule, systemctl, dbus-monitor |
| Phase 4 (Vision) | nothing | `ollama` | OpenCV, Tesseract, AT-SPI |

---

## HOW LEVELS COMPARE — CAPABILITY TABLE

| Capability | Level 1 ✅ | Level 2 | Level 3 | Level 4 |
|---|---|---|---|---|
| Single NL command | ✅ | ✅ | ✅ | ✅ |
| Hindi/Telugu commands | ✅ | ✅ | ✅ | ✅ |
| Multi-task (sequential) | ✅ | ✅ | ✅ | ✅ |
| Multi-task (parallel) | ❌ | ✅ | ✅ | ✅ |
| Auto-plan from goal | ❌ | ✅ | ✅ | ✅ |
| Task A result → Task B | ❌ | ✅ | ✅ | ✅ |
| Runs without user | ❌ | ❌ | ✅ | ✅ |
| React to email/notify | ❌ | ❌ | ✅ | ✅ |
| Time-based triggers | ❌ | ❌ | ✅ | ✅ |
| Adaptive replanning | ❌ | Partial | ✅ | ✅ |
| Understand any UI | ❌ | ❌ | ❌ | ✅ |
| Click unknown buttons | ❌ | ❌ | ❌ | ✅ |
| Navigate unknown sites | ❌ | ❌ | ❌ | ✅ |
| Self-improving | Partial | Partial | Partial | ✅ |

---

## 16. FILE: UNKNOWN COMMANDS LOG

All commands the agent couldn't handle are saved here:
```
~/.config/desktop_automation/unknown_commands.txt
```
Format: `[2026-09-01 17:33:52 UTC] your unknown command here`

Review this file to find gaps and add new intents.

---

## 17. QUICK REFERENCE FOR AI ASSISTANTS

If you are an AI reading this file to help with this project, here are the key things to know:

1. **Language**: Python 3.12, no external AI/LLM, no cloud APIs for core function
2. **Platform**: Linux Ubuntu, GNOME 46, Wayland session
3. **Automation layer**: AT-SPI (accessibility) is PRIMARY. evdev for keyboard. Chrome CDP for browser.
4. **Parser**: Rule-based, not ML. WORD_MAP + INTENT_DEFS + fuzzy scoring in `core/smart_parser.py`
5. **Every task** must have `setup()`, `execute(args, resources)`, `cleanup(resources)` 
6. **Password** for App Center install: `rgukt` (stored in `config/config.yaml`)
7. **No wmctrl on Wayland** — it sees zero windows. Use AT-SPI instead.
8. **No Shell.Eval on GNOME 46** — gdbus returns (false,'') for Shell.Eval. Use AT-SPI.
9. **URL extraction**: Always re-extract URLs from `intent.raw_input` (normalization strips dots)
10. **WhatsApp contact**: Use greeting-scan + known-contact-list recovery, not raw parser output
11. **Klavaro**: Intercepted BEFORE parse_multi() in run_command() via regex on "klavaro"
12. **install_app**: ONLY uses `atspi_install.py` (GUI App Center). CLI fallback REMOVED by user request.
13. **Tasks path**: `/home/rajesh/Pictures/desktop/tasks/`
14. **Project root**: `/home/rajesh/Pictures/desktop/`
15. **Total codebase**: ~9,800 lines across 45+ Python files

---

*Generated: 2026-09-01 | Version: Post full-audit | All 25 tasks verified working ✅*
