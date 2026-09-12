"""
capabilities/translation.py — Text translation via deep-translator (Google)
with langdetect for local language detection (no API key needed).
"""
from __future__ import annotations

from typing import Any

from capabilities.base import Cap, fail, ok, register_cap


def install(registry: Any, *, approve_all: bool = False) -> None:

    def translate_text(args: dict, state: Any) -> Any:
        text = str(args.get("text", "")).strip()
        target = str(args.get("target", "en")).strip().lower()
        source = str(args.get("source", "auto")).strip().lower() or "auto"
        if not text:
            return fail("'text' is required")
        if not target:
            return fail("'target' language is required (e.g. 'es', 'hi')")
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            return fail("deep-translator not installed: pip install deep-translator")
        try:
            translator = GoogleTranslator(source=source, target=target)
        except Exception as exc:
            return fail(f"invalid source/target language: {str(exc)[:200]}")
        if len(text) > 5000:
            return fail("text too long (max 5000 chars per call)")
        result = None
        last_err: Exception | None = None
        for _attempt in range(3):  # Google endpoint intermittently 500s
            try:
                result = translator.translate(text)
                if result and "That's an error" not in result and "Error 500" not in result:
                    break
                last_err = Exception(f"Google endpoint error: {str(result)[:120]}")
                result = None
            except Exception as exc:
                last_err = exc
        if not result:
            return fail(f"translation failed: {str(last_err)[:300]}")
        return ok({
            "original": text,
            "translated": result,
            "source": source,
            "target": target,
        })

    def detect_language(args: dict, state: Any) -> Any:
        text = str(args.get("text", "")).strip()
        if not text:
            return fail("'text' is required")
        try:
            from langdetect import DetectorFactory, detect, detect_langs

            DetectorFactory.seed = 0  # deterministic results
            lang = detect(text)
            try:
                probs = detect_langs(text)
                confidence = max(p.prob for p in probs)
                alternatives = [
                    {"lang": p.lang, "prob": round(p.prob, 3)} for p in probs
                ]
            except Exception:
                confidence, alternatives = 1.0, []
            return ok({
                "language": lang,
                "confidence": round(confidence, 3),
                "alternatives": alternatives,
            })
        except ImportError:
            return fail("langdetect not installed: pip install langdetect")
        except Exception as exc:
            return fail(f"detection failed: {exc}")

    def list_languages(args: dict, state: Any) -> Any:
        try:
            from deep_translator import GoogleTranslator

            langs = GoogleTranslator().get_supported_languages()
            return ok({"languages": langs, "count": len(langs)})
        except ImportError:
            return fail("deep-translator not installed: pip install deep-translator")
        except Exception as exc:
            return fail(f"could not list languages: {exc}")

    register_cap(registry, Cap(
        name="translate.text",
        description="translate text between languages (Google backend, source auto-detects)",
        side_effect="external", inputs=("text", "target", "source"),
    ), translate_text)

    register_cap(registry, Cap(
        name="translate.detect_language",
        description="detect the language of a text (local langdetect, no API key)",
        side_effect="read", inputs=("text",),
    ), detect_language)

    register_cap(registry, Cap(
        name="translate.list_languages",
        description="list supported translation languages",
        side_effect="read", inputs=(),
    ), list_languages)
