# ARCHITECTURE EVOLUTION — FROM TASK FILES TO UNIVERSAL AUTOMATION

> **Purpose:** This document explains why the current one-task-per-file design cannot scale to handle all Ubuntu automation tasks, what the real problems are, and the correct architectural solutions. Read this alongside `MASTER.md`.

---

## THE PROBLEM — WHAT WE REALISED

### Current System Behaviour for Unknown Tasks

```
User: "open Spotify and create a playlist called Morning Vibes"
                    ↓
Agent: No spotify_playlist.py file exists
                    ↓
universal_fallback.py tries:
  • xdg-open → opens Spotify (but can't create playlist)
  • shell binary → fails
  • logs to unknown_commands.txt
                    ↓
❌  FAILED — user gets no result
```

**The system only knows what its .py files tell it. Ubuntu has thousands of possible tasks. We can never write a .py file for every one.**

---

## WHY ONE FILE PER TASK IS WRONG AT SCALE

### The 25-File Trap

```
Current state:
  install_app.py        ← knows how to install apps
  youtube_automation.py ← knows YouTube only
  whatsapp_send.py      ← knows WhatsApp only
  klavaro_automation.py ← knows Klavaro only
  window_management.py  ← knows windows
  ... 25 files total

Reality of Ubuntu:
  Installed apps on a typical system: 200–500
  Total Ubuntu software: 50,000+ packages
  Possible automation tasks: effectively infinite

Conclusion: Writing a .py file per task is impossible to scale.
```

### Why We Built It This Way (The Learning Phase)

The per-task approach was correct **as a learning exercise**:
- ✅ Understand each automation method in depth (AT-SPI, evdev, CDP, subprocess)
- ✅ Make each task's logic explicit and debuggable
- ✅ Build the foundational library (SmartParser, StrategyExecutor, Verifier, VirtualKeyboard)
- ✅ Prove the automation primitives work on real Linux/Wayland

**The 25 task files are the FOUNDATION, not the destination.**

### The Analogy

```
Current approach:
  "To drive to any city, build a separate dedicated road to each city."
  Built 25 roads. What about city 26? Build another road.
  City 1,000? Still building roads.

Correct approach:
  "Build a GPS + universal car that navigates any road."
  Works for city 1, city 25, city 1,000, and cities
  that do not even exist yet.
```

---

## WHERE DOES TASK KNOWLEDGE LIVE?

This is the core question. In the current design, knowledge of HOW to do a task lives inside the .py file. If the file does not exist, the knowledge does not exist.

```
Option A — Current (WRONG at scale):
  install_app.py   → contains knowledge: "use snap install + App Center"
  youtube.py       → contains knowledge: "use CDP + search URL"
  ???_app.py       → knowledge MISSING → task fails

Option B — Correct (SCALABLE):
  The system itself is the knowledge.
  It can READ any app's interface at runtime.
  It does not need to know about the app in advance.
```

---

## THE 4 REAL SOLUTIONS

### Layer 1 — CLI Registry (covers ~60% of Ubuntu tasks)

**Key insight:** Ubuntu has a CLI command for almost everything. You do not need a UI, no AT-SPI, no browser. Just a shell command.

Instead of writing Python code per task, maintain a **data dictionary**: `task description → shell command template`.

```
"install X"             →  snap install {X} || apt install -y {X}
"uninstall X"           →  snap remove {X} || apt remove {X}
"play music file"       →  mpv {file} || vlc {file}
"resize image 50%"      →  convert {input} -resize 50% {output}
"compress folder"       →  zip -r {folder}.zip {folder}/
"extract archive"       →  unzip {file} || tar -xf {file}
"check disk space"      →  df -h
"check memory"          →  free -h
"check wifi"            →  nmcli device status
"show ip address"       →  ip addr show
"set wallpaper"         →  gsettings set org.gnome.desktop.background picture-uri {uri}
"record screen"         →  ffmpeg -f x11grab -r 25 -s 1920x1080 -i :0 output.mp4
"set reminder 10min"    →  echo "notify-send Reminder" | at now + 10 minutes
"translate text"        →  trans en:hi "{text}"
"convert pdf to docx"   →  libreoffice --headless --convert-to docx {file}
"merge pdfs"            →  pdfunite {files} output.pdf
"crop image"            →  convert {input} -crop {W}x{H}+{X}+{Y} {output}
"list files by size"    →  du -sh * | sort -h
"find large files"      →  find / -size +100M -type f
"kill process"          →  pkill -f {process_name}
"check running apps"    →  pgrep -a -l
"set volume"            →  pactl set-sink-volume @DEFAULT_SINK@ {level}%
"set brightness"        →  brightnessctl set {level}%
"connect bluetooth"     →  bluetoothctl connect {mac}
"sync time"             →  timedatectl set-ntp true
"clear trash"           →  gio trash --empty
"empty downloads"       →  rm -rf ~/Downloads/*
"backup folder"         →  rsync -av {src} {dst}
... (200+ more entries)
```

**No Python code needed per task. Add new task = add one line to a YAML file.**

**What this covers:**
- File operations (any file type, any operation)
- System settings (network, display, audio, power)
- Media conversion and playback
- Archive management
- Process management
- Text processing
- Network operations
- System information

**What this CANNOT do:**
- Click buttons in a GUI
- Fill forms
- Navigate menus
- Interact with apps that have no CLI

---

### Layer 2 — AT-SPI Universal Navigator (covers ~25% more tasks)

**Key insight:** AT-SPI (Accessibility API) exposes the ENTIRE interactive tree of any GTK/Qt application automatically. The system does not need to know the app in advance. It reads the app's own structure at runtime.

```
User: "open Nautilus and create a new folder named Photos"

WITHOUT a nautilus_task.py file:

Step 1: Launch Nautilus
        open_system_app("nautilus") ← already exists

Step 2: Read its AT-SPI tree automatically
        Desktop → Nautilus → Frame → Toolbar → [New Folder Button]
                                   → Sidebar → [Bookmarks]
                                   → Content → [File Grid]
        AT-SPI reveals ALL interactive elements of Nautilus instantly.

Step 3: Match "create new folder" to tree element
        Search tree for ROLE_PUSH_BUTTON + name contains "folder"
        → Found: button "New Folder" at path Frame→Toolbar→Button[3]

Step 4: Click it
        do_action(button) ← already exists in atspi_utils.py

Step 5: Dialog appears → read its tree
        → Found: ROLE_ENTRY (the folder name input field)
        → set_text(field, "Photos") ← already exists

Step 6: Find and click OK/Create button
        → Found: ROLE_PUSH_BUTTON name="Create"
        → do_action(button)

Step 7: Verify via AT-SPI
        → Check folder "Photos" now exists in tree
        ✅ Done
```

**This works for ANY GTK application because GTK exposes its full widget tree through AT-SPI by default.**

#### How AT-SPI Universal Navigation Works

```
ANY GTK App
    ↓
AT-SPI reads:
  - Every button (name, state, position)
  - Every text field (content, placeholder)
  - Every menu (items, submenus)
  - Every label
  - Every list item
  - Every dialog
    ↓
System logic:
  1. Parse user intent → what action + what target
     ("create folder named Photos" → action=create, target=folder, name=Photos)
  2. Search AT-SPI tree for matching element
     (find button/menu that handles "create" + "folder")
  3. Execute the action on that element
  4. Handle any resulting dialog (read tree, fill inputs, confirm)
```

**What AT-SPI covers:**
- ALL GNOME native apps (Files, Settings, Text Editor, Calendar, Contacts...)
- ALL GTK3/GTK4 applications (GIMP, Inkscape, Audacity, Gedit...)
- MOST Qt apps (have accessibility bridge)
- SOME Electron apps (if accessibility is enabled)

**What AT-SPI CANNOT do:**
- Canvas-drawn UIs (the button exists visually but has no AT-SPI node)
- Games (custom rendering engines)
- Apps with accessibility explicitly disabled
- Web content inside browsers (use CDP instead)

---

### Layer 3 — Browser/CDP Universal Executor (covers ~7% more tasks)

**Key insight:** Anything that runs in a browser (web apps, Electron apps) can be automated via Chrome DevTools Protocol (CDP) regardless of what the website looks like.

```
User: "go to Flipkart, search for headphones, filter by price under 2000"

Without a flipkart_task.py file:

System:
  1. Open browser → go to flipkart.com (browser_action.py already handles this)
  2. Find search input: page.locator('input[type="search"]')
  3. Type "headphones" → press Enter
  4. Find filter: page.locator('text=Price')
  5. Set range: max 2000
  6. Screenshot result

This works for ANY website because Playwright/CDP can:
  - Find ANY element by CSS selector, text, role, placeholder
  - Click ANY element
  - Type into ANY input
  - Read ANY page content
  - Wait for ANY condition
  - Take screenshots for verification
```

**What Browser/CDP covers:**
- All web apps (Gmail, Drive, GitHub, YouTube, WhatsApp Web...)
- All Electron apps (VS Code, Slack, Discord, Notion...)
- Any website's forms, menus, buttons, search boxes

---

### Layer 4 — Vision Model (covers the remaining ~8%)

**Key insight:** A vision model can SEE the screen like a human, understand what it sees, and generate the right action — for ANY app, ANY UI, ANY state.

```
User: "open that drawing app and draw a red circle"

Apps like Pinta/Krita have:
  - Custom canvas (AT-SPI sees nothing useful)
  - No CLI for drawing
  - Not browser-based

Vision Model approach:
  1. Take screenshot
  2. Send to vision model: "I need to draw a circle.
     What tool should I click and where?"
  3. Model responds: "Click the Ellipse tool at position (145, 67),
     then drag from (400,300) to (500,400)"
  4. System executes those clicks/drags
  5. Screenshot to verify
  ✅ Done — without ANY pre-built task file for this app
```

**What Vision covers:** Literally everything that can be seen on a screen.

---

## THE CORRECT LAYERED ARCHITECTURE

```
User gives ANY task
        ↓
┌─────────────────────────────────────────────────┐
│  SMART PARSER  (already built)                   │
│  Understands what the user wants                 │
│  Works in English + Hindi/Telugu                 │
└─────────────────┬───────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────┐
│  LAYER 1: CLI REGISTRY  (~60% coverage)          │
│  Check: does this task have a shell command?     │
│  Yes → execute it → done                        │
│  No  → continue to Layer 2                      │
│                                                  │
│  Source: config/cli_registry.yaml               │
│  Format: "task_pattern → shell command template"│
│  Size:   200+ entries                           │
│  Add new: edit YAML, no Python needed           │
└─────────────────┬───────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────┐
│  LAYER 2: AT-SPI AUTO-NAVIGATOR  (+25% coverage) │
│  Check: is the target app accessible via AT-SPI? │
│  Yes → read its tree → find matching element     │
│      → click/type/select → verify               │
│  No  → continue to Layer 3                      │
│                                                  │
│  Key modules: atspi_utils.py (already built)    │
│  New module:  core/atspi_navigator.py           │
│  No per-app files needed                        │
└─────────────────┬───────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────┐
│  LAYER 3: BROWSER/CDP  (+7% coverage)            │
│  Check: is this a web app or Electron app?      │
│  Yes → Playwright CDP → DOM traversal → action  │
│  No  → continue to Layer 4                      │
│                                                  │
│  Key module: browser_action.py (already built)  │
│  No per-website files needed                    │
└─────────────────┬───────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────┐
│  LAYER 4: VISION MODEL  (+8% coverage)           │
│  Screenshot → send to vision model               │
│  Model identifies what to click/type            │
│  Execute the visual action                      │
│  Verify via screenshot comparison               │
│                                                  │
│  New module: core/semantic_vision.py            │
│  Requires: Ollama + llava (4GB, runs locally)  │
│  No per-app files needed                        │
└─────────────────────────────────────────────────┘
                  ↓
              ✅ ~99% of tasks handled
              (remaining 1% = truly impossible
               e.g. hardware not present)
```

---

## DATA-DRIVEN SINGLE FILE — REPLACING 25 TASK FILES

### The Problem With 25 Files

```
Current structure (BAD for maintenance):
  tasks/install_app.py       → 190 lines
  tasks/youtube_automation.py → 150 lines
  tasks/whatsapp_send.py     → 250 lines
  tasks/brightness_control.py → 140 lines
  ... 25 files, ~3,800 lines total

To add a new task: write a new .py file (150-250 lines)
To fix a bug: find the right file, understand its code
To audit all tasks: read 25 different files
```

### The Better Design: Data-Driven Registry

```python
# ONE file: core/task_runner.py  (~300 lines total)
# Replaces all 25 task files

TASK_REGISTRY = {

    # ── CLI-based tasks (no UI needed) ──────────────────────────────────
    "screenshot":       CLITask("gnome-screenshot || scrot || grim"),
    "volume_up":        CLITask("pactl set-sink-volume @DEFAULT_SINK@ +10%"),
    "volume_down":      CLITask("pactl set-sink-volume @DEFAULT_SINK@ -10%"),
    "mute":             CLITask("pactl set-sink-mute @DEFAULT_SINK@ 1"),
    "unmute":           CLITask("pactl set-sink-mute @DEFAULT_SINK@ 0"),
    "brightness_up":    CLITask("brightnessctl set +10%"),
    "brightness_down":  CLITask("brightnessctl set 10%-"),
    "lock_screen":      CLITask("loginctl lock-session"),
    "shutdown":         CLITask("systemctl poweroff"),
    "restart":          CLITask("systemctl reboot"),
    "suspend":          CLITask("systemctl suspend"),
    "organize_downloads": CLITask("python3 -c 'from tasks.organize_downloads import execute; execute({},{})'"),

    # ── App launch tasks (AT-SPI verify) ───────────────────────────────
    "open_calculator":  AppTask("gnome-calculator"),
    "open_terminal":    AppTask("gnome-terminal"),
    "open_files":       AppTask("nautilus"),
    "open_vscode":      AppTask("code"),
    "open_vlc":         AppTask("vlc"),
    # ADD ANY NEW APP: just add one line here

    # ── Browser tasks (CDP/Playwright) ─────────────────────────────────
    "youtube":          BrowserTask("https://youtube.com/results?search_query={query}"),
    "whatsapp":         BrowserTask("https://web.whatsapp.com"),
    "open_url":         BrowserTask("{url}"),
    "search_web":       BrowserTask("https://google.com/search?q={query}"),
    # ADD ANY NEW WEBSITE: just add one line here

    # ── AT-SPI auto tasks (any GTK app, no code needed) ────────────────
    "click_button":     ATSPITask(action="click",   find_by="name"),
    "type_in_field":    ATSPITask(action="type",    find_by="role"),
    "select_menu":      ATSPITask(action="click",   find_by="menu"),
    "close_dialog":     ATSPITask(action="click",   find_by="name", target="Close|Cancel|OK"),

    # ── Universal fallback ─────────────────────────────────────────────
    "unknown":          UniversalTask(),  # tries CLI → AT-SPI → vision model
}
```

**To add a new task:** One line in the registry. No new .py file. No 150 lines of code.

### Comparison

| | Current (25 files) | Data-Driven (1 file) |
|---|---|---|
| Add new task | Write new .py file (~150 lines) | Add 1 line to registry |
| Fix a bug | Find correct file, understand its code | Fix in one place |
| Add new app support | New file per app | One line: `AppTask("appname")` |
| Add new website | New file | One line: `BrowserTask("url_template")` |
| Total code to maintain | ~3,800 lines across 25 files | ~300 lines in 1 file |
| Coverage | 25 specific tasks | Unlimited via registry + layers |

---

## WHY THE CURRENT APPROACH IS STILL VALUABLE

The 25 task files we built are NOT wasted. They are:

**1. The primitive library**
Every automation method we discovered (AT-SPI, evdev, CDP, pyatspi, VirtualKeyboard, Verifier) is battle-tested and working. These become the BUILDING BLOCKS of the universal system.

**2. The reference implementations**
Each .py file shows HOW a certain type of automation works. `klavaro_automation.py` shows how to use AT-SPI for reading text + evdev for typing. `whatsapp_send.py` shows how CDP works. These are blueprints.

**3. The performance examples**
Some tasks are complex enough to deserve their own file for maximum reliability:
- `klavaro_automation.py` — extremely precise, timing-critical
- `atspi_install.py` — complex multi-step GUI flow with error recovery

**The right model:** Keep the 25 files for complex tasks that need custom logic. Use the universal registry for everything else (which is 95% of tasks).

---

## THE UBUNTU TASK CATEGORIES — HOW EACH GETS HANDLED

```
Ubuntu Task Categories            Best Handler
────────────────────────────────────────────────────────────
File operations                   CLI (cp, mv, rm, find, ls)
Archive operations                CLI (zip, tar, unzip)
Media playback                    CLI (mpv, vlc --intf)
Media conversion                  CLI (ffmpeg, convert, ffprobe)
System settings                   CLI (gsettings, systemctl, nmcli)
Package management                CLI (snap, apt, flatpak)
Text processing                   CLI (grep, sed, awk, cut)
Network operations                CLI (curl, wget, ssh, ping)
Screenshot/recording              CLI (gnome-screenshot, ffmpeg)
Process management                CLI (kill, pkill, pgrep)
Cron/scheduling                   CLI (crontab, at, systemd timer)
GNOME native apps                 AT-SPI auto-navigation
GTK3/4 apps (GIMP, Inkscape...)   AT-SPI auto-navigation
Qt apps (VLC, Kdenlive...)        AT-SPI auto-navigation
Web browsing                      Browser/CDP (Playwright)
Web forms/automation              Browser/CDP (Playwright)
Electron apps (VSCode, Slack...)  Browser/CDP (Playwright)
Games                             Vision Model (screen reading)
Custom-rendered apps              Vision Model (screen reading)
Physical hardware                 Vision Model + system calls
────────────────────────────────────────────────────────────
TOTAL COVERAGE with 4 layers:     ~99% of Ubuntu tasks
TOTAL COVERAGE with current .py:  25 specific tasks only
```

---

## THE MISSING PIECE — `core/atspi_navigator.py`

This is the single most important file to build next. It is what allows the system to handle ANY GTK app without pre-built task files.

### What it does

```
Input:  User intent as plain language
        Target app (already running or to be launched)

Output: ✅ Action completed  OR  ❌ Element not found (→ next layer)

How:
  1. Walk entire AT-SPI tree of the app
  2. Score every node against the user intent
  3. Select highest-scoring interactive node
  4. Execute appropriate action (click/type/select/check)
  5. Handle any resulting dialog automatically
  6. Return success/failure
```

### Why this enables unlimited tasks

```
Today:
  "open GIMP and create new image 800x600"
  → No gimp_task.py → FAILS

With atspi_navigator.py:
  "open GIMP and create new image 800x600"
  → Launch GIMP (open_system_app ← already works)
  → Read GIMP's AT-SPI tree
  → Find: File menu → New option
  → Click File → New
  → Dialog opens → read its tree
  → Find: Width field → type "800"
  → Find: Height field → type "600"
  → Find: OK button → click
  → Verify: new canvas appeared in tree
  → ✅ Done — without any GIMP-specific code
```

---

## THE MISSING PIECE — `config/cli_registry.yaml`

This file converts the CLI-layer from "try random shell commands" into a proper knowledge base.

```yaml
# config/cli_registry.yaml
# Format: pattern → command template
# {varname} = extracted parameter from user command

file_operations:
  compress_folder:   "zip -r {name}.zip {folder}"
  extract_zip:       "unzip {file} -d {destination}"
  extract_tar:       "tar -xf {file} -C {destination}"
  find_large_files:  "find {path} -size +{size}M -type f"
  count_files:       "find {path} -type f | wc -l"
  rename_bulk:       "rename 's/{old}/{new}/g' {pattern}"

media:
  play_video:        "mpv {file}"
  play_audio:        "mpv --no-video {file}"
  convert_video:     "ffmpeg -i {input} {output}"
  compress_video:    "ffmpeg -i {input} -crf 28 {output}"
  extract_audio:     "ffmpeg -i {video} -vn -acodec copy {audio}"
  resize_image:      "convert {input} -resize {size} {output}"
  compress_image:    "convert {input} -quality {quality} {output}"
  merge_pdfs:        "pdfunite {files} {output}"
  split_pdf:         "pdfseparate -f {start} -l {end} {input} {output}"

system:
  check_disk:        "df -h"
  check_memory:      "free -h"
  check_cpu:         "top -bn1 | head -20"
  check_temp:        "sensors"
  list_usb:          "lsusb"
  list_bluetooth:    "bluetoothctl devices"
  connect_wifi:      "nmcli device wifi connect {ssid} password {pwd}"
  flush_dns:         "sudo systemd-resolve --flush-caches"

text:
  count_words:       "wc -w {file}"
  count_lines:       "wc -l {file}"
  search_in_file:    "grep -n '{pattern}' {file}"
  replace_in_file:   "sed -i 's/{old}/{new}/g' {file}"
  sort_file:         "sort {file} -o {file}"
  remove_duplicates: "sort -u {file} -o {file}"

network:
  check_port:        "nmap -p {port} {host}"
  download_file:     "wget -P {destination} {url}"
  upload_file:       "curl -T {file} {url}"
  check_ssl:         "echo | openssl s_client -connect {host}:443 2>/dev/null | openssl x509 -noout -dates"
```

---

## WHAT CHANGES IN `agent.py` WITH THIS ARCHITECTURE

```
CURRENT flow:
  parse command → find matching executor in agent.py → run task .py file

EVOLVED flow:
  parse command
       ↓
  Layer 1: CLI Registry
    check cli_registry.yaml for matching pattern
    if found → run shell command → done
       ↓ (not found)
  Layer 2: AT-SPI Navigator
    does target app have AT-SPI tree?
    if yes → auto-navigate to element → done
       ↓ (not found)
  Layer 3: Browser/CDP
    is target a web app or Electron?
    if yes → Playwright automation → done
       ↓ (not found)
  Layer 4: Vision Model
    take screenshot → ask model → execute clicks → done
       ↓ (model unavailable or fails)
  Log to unknown_commands.txt with full context
  Suggest closest known command
```

---

## KEY INSIGHT — THE AUTOMATION TRIANGLE

```
         RELIABILITY
              ▲
              │
         CLI  │  (most reliable, predictable, scriptable)
              │
     AT-SPI  │  (reliable for GTK, but depends on app exposing accessibility)
              │
    Browser   │  (reliable for web, depends on DOM structure)
              │
      Vision  │  (least reliable, AI-dependent, but widest coverage)
              ▼
           COVERAGE
```

**You always want to be as high up the triangle as possible.**
- If a task CAN be done via CLI → use CLI (most reliable)
- If NOT → try AT-SPI (still very reliable for GTK)
- If NOT → try Browser/CDP (reliable for web)
- If NOT → Vision model (wide coverage, less predictable)

---

## SUMMARY — THREE THINGS TO BUILD

| Priority | What to Build | File | Unlocks |
|---|---|---|---|
| 1 (HIGHEST) | CLI Registry | `config/cli_registry.yaml` | 200+ tasks with zero code |
| 2 (HIGH) | AT-SPI Universal Navigator | `core/atspi_navigator.py` | Any GTK app without task files |
| 3 (MEDIUM) | Data-driven task runner | Refactor `tasks/` into registry | One file replaces 25 |
| 4 (LOW) | Vision model tier | `core/semantic_vision.py` | 100% task coverage |

---

## THE HONEST TRUTH

```
What we have now:
  A very solid Level 1 system.
  25 specific things it can do extremely well.
  Fails gracefully for anything outside those 25.

What we need:
  A system that never truly fails.
  Always finds SOME way to accomplish the task.
  Uses the simplest method possible (CLI first, vision last).
  Learns from every new task it encounters.

The gap:
  CLI Registry: ~2 hours to build (write YAML file)
  AT-SPI Navigator: ~1 week to build (core Python module)
  Vision model: ~2 days to set up (install Ollama + llava)

Result after all three:
  ~99% of Ubuntu automation tasks handled automatically.
  User never sees "task failed" again.
  System gets smarter with every command it learns.
```

---

## FOR AI ASSISTANTS READING THIS

If you are helping build this system, the priority order is:

1. **Build `config/cli_registry.yaml` first** — highest ROI, easiest to build, zero risk
2. **Build `core/atspi_navigator.py` second** — high impact, uses existing atspi_utils.py as foundation
3. **Refactor task registry last** — lowest urgency, current 25 files still work fine

The existing task files in `tasks/` are NOT to be deleted. They are kept as:
- Complex task implementations (Klavaro, WhatsApp, App Center install)
- Reference implementations for automation patterns
- Fallback for tasks that need precise timing or custom error handling

Everything else gradually moves to the registry + navigator pattern.

---

*Generated: 2026-09-01 | Companion to MASTER.md | Project: Autonomous Desktop Agent*
