"""
capabilities/qrcode.py — QR code generation (qrcode lib) and scanning
(PIL + pyzbar, with zbarimg CLI fallback).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell


def install(registry: Any, *, approve_all: bool = False) -> None:

    def generate(args: dict, state: Any) -> Any:
        data = str(args.get("data", "")).strip()
        path = str(args.get("path", "")).strip()
        size = int(args.get("size", 10))  # pixels per module
        if not data:
            return fail("'data' is required")
        if not path:
            return fail("'path' is required (e.g. /tmp/qr.png)")
        if not path.lower().endswith(".png"):
            return fail("'path' must end with .png")
        try:
            import qrcode

            img = qrcode.make(data, box_size=size)
            out = Path(path).expanduser()
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(out))
        except ImportError:
            return fail("qrcode not installed: pip install qrcode")
        except Exception as exc:
            return fail(f"QR generation failed: {exc}")
        if not out.is_file() or out.stat().st_size == 0:
            return fail(f"QR image not verified at {path}")
        return ok({
            "path": str(out),
            "data": data,
            "size_bytes": out.stat().st_size,
            "verified": True,
        })

    def scan(args: dict, state: Any) -> Any:
        path = str(args.get("path", "")).strip()
        if not path:
            return fail("'path' is required")
        p = Path(path).expanduser()
        if not p.is_file():
            return fail(f"file not found: {path}")
        # Primary: pyzbar
        try:
            from PIL import Image
            from pyzbar import pyzbar

            results = pyzbar.decode(Image.open(str(p)))
            if results:
                codes = [
                    {"type": r.type, "data": r.data.decode(errors="replace")}
                    for r in results
                ]
                return ok({"codes": codes, "count": len(codes), "engine": "pyzbar"})
            return ok({"codes": [], "count": 0, "engine": "pyzbar",
                        "note": "no codes found in image"})
        except ImportError:
            pass
        except Exception as exc:
            return fail(f"pyzbar scan failed: {exc}")
        # Fallback: zbarimg CLI
        rc, out, err = run_shell(["zbarimg", "--quiet", "--raw", str(p)])
        if rc == 0 and out:
            lines = [ln for ln in out.splitlines() if ln.strip()]
            return ok({"codes": [{"type": "unknown", "data": ln} for ln in lines],
                        "count": len(lines), "engine": "zbarimg"})
        if rc == 4:  # zbarimg: no symbols found
            return ok({"codes": [], "count": 0, "engine": "zbarimg"})
        return fail(err or f"zbarimg exited {rc} (install: sudo apt install zbar-tools)")

    register_cap(registry, Cap(
        name="qrcode.generate",
        description="generate a QR code PNG from text/URL data",
        side_effect="local_write", inputs=("data", "path", "size"),
    ), generate)

    register_cap(registry, Cap(
        name="qrcode.scan",
        description="scan a QR/barcode from an image file (pyzbar, zbarimg fallback)",
        side_effect="read", inputs=("path",),
    ), scan)
