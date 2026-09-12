"""Temp probe: test OpenRouter GLM 5.3 flash with the planner's exact request shape."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
_env = Path(__file__).resolve().parents[1] / "config" / "secrets.env"
for _line in _env.read_text().splitlines():
    _line = _line.strip()
    if not _line or _line.startswith("#") or "=" not in _line:
        continue
    _k, _, _v = _line.partition("=")
    os.environ.setdefault(_k.strip(), _v.strip())

from openai import OpenAI

KEY = os.environ.get("OPENROUTER_API_KEY") or "YOUR_OPENROUTER_API_KEY_HERE"
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=KEY)

variants = [
    ("json_object", {"response_format": {"type": "json_object"}}),
    ("plain", {}),
]

for name, opts in variants:
    try:
        resp = client.chat.completions.create(
            model="z-ai/glm-5.3-flash",
            messages=[
                {"role": "system", "content": "Reply with strict JSON only."},
                {"role": "user", "content": 'Plan format: return exactly {"summary": "...", "steps": [{"action": "navigate", "args": {"url": "https://example.com"}}]}. Make a 2-step plan to open a browser and navigate to example.com.'},
            ],
            temperature=0.0,
            max_tokens=2000,
            timeout=60,
            **opts,
        )
        msg = resp.choices[0].message
        content = msg.content or ""
        reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)
        print(f"[{name}] OK")
        print(f"[{name}] content_len={len(content)} reasoning_present={bool(reasoning)}")
        print(f"[{name}] content[:600]={content[:600]!r}")
    except Exception as e:
        print(f"[{name}] FAILED: {type(e).__name__}: {str(e)[:400]}")
    print("-" * 60)
