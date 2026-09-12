import json
import warnings

warnings.filterwarnings("ignore")

from capabilities import build_registry  # noqa: E402

r = build_registry()
contracts = r.contracts()
print("total caps:", len(contracts))
cat = [
    {"n": c.name, "a": list(c.inputs), "c": 1 if c.requires_confirmation else 0}
    for c in contracts
]
s = json.dumps(cat, separators=(",", ":"))
print("compact catalog bytes:", len(s))
lines = [f"{c.name}({','.join(c.inputs)})" for c in contracts]
print("name-only bytes:", len(";".join(lines)))
