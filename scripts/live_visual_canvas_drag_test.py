"""Live proof for a non-accessible canvas drag using the structured executor.

The canvas exposes no DOM control for either shape.  The executor must locate
two visual regions, drag in page-viewport coordinates, and verify the canvas
changed.  It is intentionally local and non-destructive.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser import brave
from core.runtime_adapters import execute_structured_plan_runtime
from core.runtime_observer import RuntimeObserver
from core.structured_automation import StructuredExecutor, validate_plan


def _load_local_secrets() -> None:
    """Load local test credentials without printing their values."""
    path = Path(__file__).resolve().parents[1] / "config" / "secrets.env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    _load_local_secrets()
    browser = brave(headless=True).start()
    try:
        browser.page.set_content("""<!doctype html>
          <title>Canvas Drag Proof</title>
          <style>body{margin:0;background:#f8fafc;font:18px sans-serif}canvas{display:block}</style>
          <canvas id="board" width="900" height="520" aria-label="drawing board"></canvas>
          <script>
          const canvas = document.querySelector('#board');
          const ctx = canvas.getContext('2d');
          const source = {x:100,y:140,w:150,h:110};
          const target = {x:620,y:150,w:190,h:180};
          let card = {...source}, dragging = false, dropped = false;
          function inside(p, box) { return p.x >= box.x && p.x <= box.x + box.w && p.y >= box.y && p.y <= box.y + box.h; }
          function point(event) { const r=canvas.getBoundingClientRect(); return {x:(event.clientX-r.left)*canvas.width/r.width, y:(event.clientY-r.top)*canvas.height/r.height}; }
          function draw() {
            ctx.clearRect(0,0,900,520);
            ctx.fillStyle='#172554'; ctx.font='bold 24px sans-serif'; ctx.fillText('Move the blue card into the amber target', 55, 62);
            ctx.fillStyle='#b91c1c'; ctx.fillRect(335,160,120,120); ctx.fillStyle='white'; ctx.fillText('Decoy',350,225);
            ctx.fillStyle='#d97706'; ctx.fillRect(target.x,target.y,target.w,target.h);
            ctx.fillStyle='#fff7ed'; ctx.fillRect(target.x+10,target.y+10,target.w-20,target.h-20);
            ctx.fillStyle='#92400e'; ctx.font='bold 20px sans-serif'; ctx.fillText('DROP HERE',650,245);
            ctx.fillStyle='#0e65b7'; ctx.fillRect(card.x,card.y,card.w,card.h);
            ctx.fillStyle='white'; ctx.font='bold 20px sans-serif'; ctx.fillText('BLUE CARD',120 + (dropped ? 525 : 0),205 + (dropped ? 60 : 0));
            ctx.fillStyle='#1e293b'; ctx.font='18px sans-serif'; ctx.fillText(dropped ? 'Success: blue card dropped' : 'Status: waiting for drag',55,455);
          }
          canvas.addEventListener('pointerdown', event => { const p=point(event); dragging=inside(p,card); if(dragging) canvas.setPointerCapture(event.pointerId); });
          canvas.addEventListener('pointermove', event => { if(!dragging) return; const p=point(event); card.x=p.x-card.w/2; card.y=p.y-card.h/2; draw(); });
          canvas.addEventListener('pointerup', event => { if(!dragging) return; const p=point(event); dragging=false; if(inside(p,target)){ card={x:640,y:190,w:150,h:110}; dropped=true; window.__canvasDropped=true; } draw(); });
          draw();
          </script>""")
        plan = validate_plan({
            "summary": "Move the non-accessible canvas card",
            "steps": [{
                "action": "visual_drag",
                "args": {
                    "app": "Canvas Drag Proof",
                    "source": "solid blue draggable card in the left side of the drawing canvas",
                    "target": "large amber orange drop zone on the right side of the drawing canvas",
                    "minimum_confidence": 0.9,
                    "duration_seconds": 0.8,
                },
                "expect": {"minimum_change_ratio": 0.002},
            }],
        })
        executor = StructuredExecutor(
            approve_all=True,
            execution_id="live-visual-canvas-drag",
            step_timeout=15.0,
            total_timeout=25.0,
        )
        executor.attach_page(browser.page)
        observer = RuntimeObserver(page=browser.page)
        result = execute_structured_plan_runtime(
            plan,
            "drag a visual canvas card into its target",
            executor=executor,
            page=browser.page,
            observe=lambda _runtime: observer.observe(),
        )
        dropped = browser.page.evaluate("Boolean(window.__canvasDropped)")
        evidence_items = result.state.verified_facts.get("structured_evidence", [])
        evidence = evidence_items[0] if evidence_items else {}
        print(f"SUCCESS={result.success}")
        print(f"MESSAGE={result.state.message!r}")
        print(f"METHOD={evidence.get('method')}")
        print(f"CHANGE_RATIO={evidence.get('visual_change_ratio')}")
        print(f"DROPPED={dropped}")
        if not result.success or not dropped:
            return 1
        print("RESULT=PASS")
        return 0
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
