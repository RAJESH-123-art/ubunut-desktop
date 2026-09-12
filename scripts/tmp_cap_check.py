import json
import warnings

warnings.filterwarnings("ignore")

from capabilities import build_registry  # noqa: E402

r = build_registry()
contracts = r.contracts()

# families relevant to the elon-musk E2E flow
for prefix in ("sheet.", "fs.", "browser.", "research.", "calc.", "system.", "data.", "verify."):
    names = [c.name for c in contracts if c.name.startswith(prefix)]
    print(prefix, "->", names)

# check compact catalog size with conditional keys
cat = []
for c in contracts:
    entry = {"n": c.name}
    if c.inputs:
        entry["a"] = list(c.inputs)
    if c.requires_confirmation:
        entry["c"] = 1
    cat.append(entry)
s = json.dumps(cat, separators=(",", ":"))
print("conditional catalog bytes:", len(s))
open("/tmp/cap_catalog.json", "w").write(s)

# execute a few harmless read caps to learn their data fields
from core.task_runtime import RuntimeAction, RuntimeState  # noqa: E402

state = RuntimeState(goal="probe")
for name, args in [
    ("calc.evaluate", {"expression": "2+3*4"}),
    ("system.get_username", {}),
    ("system.get_uptime", {}),
]:
    out = r.execute(RuntimeAction(name, dict(args)), state)
    print(name, "->", out.state, "data=", out.data, "evidence=", out.evidence)
