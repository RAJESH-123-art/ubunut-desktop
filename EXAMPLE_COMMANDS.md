# Desktop Automation - Natural Language Command Examples

The system now supports complex multi-action commands using natural language!

## Single Action Examples

### Open Browser
```bash
python ui/assistant.py "open brave browser"
python ui/assistant.py "open firefox browser"
```

### Wait Command
```bash
python ui/assistant.py "wait 5 seconds"
```

## Multi-Action Examples (Connected with "and")

### Complex Canva Workflow
```bash
python ui/assistant.py "open firefox browser and go to canva.com and make a template of 4:5"
```

**What this does:**
1. Detects: "open firefox browser" → Opens Brave (optimized for Wayland)
2. Detects: "go to canva.com" → Site detection
3. Detects: "make a template of 4:5" → Template with 4:5 aspect ratio

The system then:
- Opens Brave browser using Playwright
- Navigates to Canva
- Opens a 4:5 aspect ratio template
- Takes a screenshot
- Logs all actions

### Instagram Story Creation
```bash
python ui/assistant.py "open brave browser and go to canva and create an Instagram Story template with 9:16 ratio"
```

### Batch Commands (From File)
Create a file `commands.txt`:
```
open brave browser and go to canva.com and make a template of 4:5
wait 2 seconds
open brave browser and go to figma.com
```

Then run:
```bash
python ui/assistant.py --file commands.txt
```

## Supported Aspect Ratios

- `4:5` - Instagram Story (vertical)
- `9:16` - TikTok/Reels format
- `1:1` - Square (Instagram Post)
- `16:9` - Landscape/YouTube thumbnail
- `3:2` - Standard photo ratio

## Templates Recognized

- Instagram Story
- Instagram Post
- Facebook Cover
- YouTube Thumbnail
- Twitter Header
- LinkedIn Banner
- TikTok Video

## How It Works

1. **Natural Language Parsing**: Breaks down your command into intents
2. **Intent Recognition**: Identifies actions like "open browser", "navigate to", "create template"
3. **Multi-Action Chaining**: Connects actions with "and" or "then"
4. **Task Execution**: Routes to appropriate automation task
5. **Result Logging**: Captures screenshots and logs all steps

## Under the Hood

Each command is:
1. **Parsed** by `core/intent_parser.py`
2. **Converted** to task JSON specifications
3. **Executed** by discovered task modules in `tasks/`
4. **Logged** with screenshots and metrics

## Adding Custom Commands

Edit `core/intent_parser.py` to add new patterns:

```python
PATTERNS = {
    "my_action": re.compile(r"your pattern here", re.I),
}

EXTRACTORS = {
    "my_action": {
        "parameter": lambda m: m.group(2).strip(),
    },
}
```

Then handle in `generate_from_intent()`:

```python
if intent.action == "my_action":
    param = intent.extra.get("parameter")
    return json.dumps({"task": "my_task", "args": {"param": param}})
```

## Troubleshooting

### "Firefox window not found" Error
- This is expected on Wayland. The system automatically uses Brave/Chromium which works better with Playwright
- If you specifically need Firefox, use X11 or enable XWayland

### "No intent matched" Warning
- Your command doesn't match any recognized patterns
- Try simpler phrasing or check available patterns above

### Playwright Errors
- Make sure Chromium is installed: `python -m playwright install chromium`
- Check you're using the venv: `source .venv/bin/activate`

## Performance Tips

- **Wayland**: System uses grim for screenshots (faster than mss)
- **Browser Automation**: Playwright is much faster than X11 automation
- **Async-Safe**: Threading prevents asyncio loop conflicts

## Future Enhancements

- [ ] LLM-based intent parsing (using local Ollama or OpenAI)
- [ ] Screenshot-based validation (verify actions completed)
- [ ] Error recovery and retry logic
- [ ] Voice command support
- [ ] Browser tab detection and reuse
