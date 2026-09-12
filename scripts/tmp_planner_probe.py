"""Temp probe: verify StructuredPlanner is wired to TokenRouter GLM 5.3 and the key works."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mirror agent.py's _load_secrets_env (config/secrets.env, setdefault semantics)
from pathlib import Path
_env = Path(__file__).resolve().parents[1] / "config" / "secrets.env"
for _line in _env.read_text().splitlines():
    _line = _line.strip()
    if not _line or _line.startswith("#") or "=" not in _line:
        continue
    _k, _, _v = _line.partition("=")
    os.environ.setdefault(_k.strip(), _v.strip())

from core.structured_automation import StructuredPlanner

p = StructuredPlanner()
print("available:", p.available())
print("base_url :", p.base_url)
print("model    :", p.model)
print("key_tail :", (p.api_key or "")[-8:])

# Live minimal call through the planner's own client path
if p.available():
    try:
        client = p._get_client()
        resp = client.chat.completions.create(
            model=p.model,
            messages=[
                {"role": "system", "content": "Reply with strict JSON only."},
                {"role": "user", "content": 'Return exactly {"ok": true, "n": 14}'},
            ],
            timeout=30,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        content = resp.choices[0].message.content or ""
        print("live_call: rc=OK content=", content.strip()[:120])
    except Exception as e:
        print("live_call: FAILED", type(e).__name__, str(e)[:300])
