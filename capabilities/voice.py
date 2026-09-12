"""
capabilities/voice.py — Text-to-Speech via espeak (offline) + gTTS (quality).

Two engines:
  - espeak: instant, offline, robotic. Good for confirmations.
  - gtts:   natural Google voice, needs network, saves mp3, plays via ffplay.

Playback processes (ffplay/espeak with audible output) hold inherited pipes
while running — stdout/stderr are redirected to devnull so we never hang.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap, run_shell

# Module-level voice settings, persisted across calls within a session.
_voice_settings: dict[str, Any] = {"rate": 150, "volume": 100, "voice": "en"}


def _play_wav(path: str) -> bool:
    """Play a wav file without hanging on inherited pipes."""
    try:
        with open("/dev/null", "w") as devnull:
            rc = subprocess.run(
                ["paplay", path],
                stdout=devnull, stderr=devnull,
                timeout=120, check=False,  # callers branch on returncode
            ).returncode
        return rc == 0
    except Exception:
        return False


def _play_any(path: str) -> bool:
    """Play wav/mp3 via ffplay (video-capable player, audio-only file)."""
    try:
        with open("/dev/null", "w") as devnull:
            rc = subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                stdout=devnull, stderr=devnull,
                timeout=180, check=False,  # callers branch on returncode
            ).returncode
        return rc == 0
    except Exception:
        return False


def install(registry: Any, *, approve_all: bool = False) -> None:

    def speak(args: dict, state: Any) -> Any:
        text = str(args.get("text", "")).strip()
        if not text:
            return fail("'text' is required")
        engine = str(args.get("engine", "espeak")).lower()
        rate = int(args.get("rate", _voice_settings["rate"]))
        volume = int(args.get("volume", _voice_settings["volume"]))
        voice = str(args.get("voice", _voice_settings["voice"]))

        if engine in ("gtts", "google"):
            try:
                from gtts import gTTS

                lang = voice[:2] if len(voice) >= 2 else "en"
                tts = gTTS(text=text, lang=lang)
                out = Path("/tmp/nikki_gtts.mp3")
                tts.save(str(out))
                if not out.is_file() or out.stat().st_size == 0:
                    return fail("gtts produced no audio")
                if not _play_any(str(out)):
                    return fail("gtts audio saved to /tmp/nikki_gtts.mp3 but playback failed (need ffplay)")
                return ok({"spoken": text, "engine": "gtts",
                           "audio": str(out), "rate": rate, "volume": volume})
            except ImportError:
                return fail("gtts not installed: pip install gtts")
            except Exception as exc:
                return fail(f"gtts failed: {exc}")

        # Default: espeak (offline, instant)
        rc, out, _err = run_shell(["which", "espeak"])
        if rc != 0:
            return fail("espeak not installed (sudo apt install espeak)")
        try:
            with open("/dev/null", "w") as devnull:
                proc = subprocess.run(
                    ["espeak", "-v", voice, "-s", str(rate), "-a", str(volume), text],
                    stdout=devnull, stderr=devnull,
                    timeout=120, check=False,  # callers branch on returncode
                )
            if proc.returncode != 0:
                return fail(f"espeak exited {proc.returncode}")
            return ok({"spoken": text, "engine": "espeak", "voice": voice,
                       "rate": rate, "volume": volume})
        except subprocess.TimeoutExpired:
            return fail("espeak timed out")
        except Exception as exc:
            return fail(f"espeak failed: {exc}")

    def save_speech(args: dict, state: Any) -> Any:
        """TTS to a file (no playback) — wav via espeak, mp3 via gtts."""
        text = str(args.get("text", "")).strip()
        path = str(args.get("path", "")).strip()
        engine = str(args.get("engine", "espeak")).lower()
        if not text:
            return fail("'text' is required")
        if not path:
            return fail("'path' is required (e.g. /tmp/out.wav)")
        if engine in ("gtts", "google"):
            try:
                from gtts import gTTS

                tts = gTTS(text=text, lang=str(args.get("voice", "en"))[:2])
                tts.save(path)
            except Exception as exc:
                return fail(f"gtts failed: {exc}")
        else:
            rc, _, err = run_shell(
                ["espeak", "-v", str(args.get("voice", "en")),
                 "-s", str(args.get("rate", _voice_settings["rate"])), text, "-w", path]
            )
            if rc != 0:
                return fail(err or f"espeak exited {rc}")
        saved = Path(path)
        if not saved.is_file() or saved.stat().st_size == 0:
            return fail(f"audio not verified at {path}")
        return ok({"path": path, "size_bytes": saved.stat().st_size, "verified": True})

    def list_voices(args: dict, state: Any) -> Any:
        lang = str(args.get("language", "")).strip()
        rc, out, _err = run_shell(["espeak", "--voices"])
        if rc != 0:
            return fail("espeak not installed")
        voices = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                voices.append({
                    "pty": parts[0], "language": parts[1],
                    "gender": parts[2] if not parts[2].startswith("-") else "",
                    "name": parts[3],
                })
        if lang:
            voices = [v for v in voices if v["language"].lower().startswith(lang.lower())]
        return ok({"voices": voices, "count": len(voices)})

    def set_voice(args: dict, state: Any) -> Any:
        for key in ("rate", "volume"):
            if key in args:
                try:
                    _voice_settings[key] = int(args[key])
                except (TypeError, ValueError):
                    return fail(f"{key} must be an integer")
        if "voice" in args:
            _voice_settings["voice"] = str(args["voice"])
        return ok({"settings": dict(_voice_settings)})

    register_cap(registry, Cap(
        name="voice.speak",
        description="speak text aloud (espeak offline, or engine='gtts' for natural voice)",
        side_effect="external", inputs=("text", "engine", "rate", "volume", "voice"),
    ), speak)

    register_cap(registry, Cap(
        name="voice.save_speech",
        description="save speech to an audio file (wav=espeak, mp3=gtts) without playing",
        side_effect="local_write", inputs=("text", "path", "engine", "voice", "rate"),
    ), save_speech)

    register_cap(registry, Cap(
        name="voice.list_voices",
        description="list available espeak voices, optionally filtered by language",
        side_effect="read", inputs=("language",),
    ), list_voices)

    register_cap(registry, Cap(
        name="voice.set_voice",
        description="set default voice settings (rate, volume, voice) for subsequent speak calls",
        side_effect="local_write", inputs=("rate", "volume", "voice"),
    ), set_voice)
