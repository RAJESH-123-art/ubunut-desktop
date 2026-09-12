"""Temp probe: dump the RAW planner completion for the elon-musk goal to see what free-GLM returns."""
import json
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

from core.structured_automation import (
    StructuredPlanner,
    _PLAN_PROMPT,
    _json_payload_from_completion,
    _universal_catalog_compact,
)
from tasks import task_catalog

goal = "open chrome and search elon musk, gather his biodata, store in spreadsheet"

p = StructuredPlanner()
capabilities = json.dumps(task_catalog(), separators=(",", ":"))
universal = _universal_catalog_compact()
request_sections = [f"GOAL: {goal}", f"REGISTERED TASK SCHEMAS: {capabilities}"]
if universal:
    request_sections.append(
        "UNIVERSAL CAPABILITY CATALOG (for cap steps; [c] = requires "
        "confirmation): " + universal
    )
full_request = "\n".join(request_sections)
print(f"[probe] prompt chars: system={len(_PLAN_PROMPT)} user={len(full_request)}", file=sys.stderr)

text = p._completion(_PLAN_PROMPT, full_request)
print("=" * 60)
print("RAW COMPLETION (first 3000 chars):")
print("=" * 60)
print(repr(text[:3000]))
print("=" * 60)
print(f"RAW LEN: {len(text)}")
try:
    payload = _json_payload_from_completion(text, label="probe")
    print("PARSED PAYLOAD KEYS:", list(payload.keys()) if isinstance(payload, dict) else type(payload))
    print("STEPS:", payload.get("steps") if isinstance(payload, dict) else "n/a")
except Exception as e:
    print("PARSE FAILED:", type(e).__name__, e)
