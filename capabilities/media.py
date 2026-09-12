from __future__ import annotations

import shutil as _shutil
from typing import Any

from capabilities.base import Cap, fail, register_cap, run_shell


def install(registry, *, approve_all=False):

    @register_cap(registry, "video.open", Cap.LOW, "Open video in default player", approve_all)
    def open_video(path: str):
        return run_shell(["xdg-open", path])

    @register_cap(registry, "video.info", Cap.READ, "Get video info", approve_all)
    def info(path: str):
        return run_shell(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path])

    @register_cap(registry, "video.trim", Cap.WRITE, "Trim video", approve_all)
    def trim(path: str, output: str, start: str, end: str):
        return run_shell(["ffmpeg", "-y", "-i", path, "-ss", start, "-to", end, "-c", "copy", output])

    @register_cap(registry, "video.extract_frame", Cap.READ, "Extract frame at timestamp", approve_all)
    def extract_frame(path: str, output: str, timestamp: str):
        return run_shell(["ffmpeg", "-y", "-ss", timestamp, "-i", path, "-frames:v", "1", "-q:v", "2", output])

    @register_cap(registry, "video.extract_audio", Cap.WRITE, "Extract audio track", approve_all)
    def extract_audio(path: str, output: str):
        return run_shell(["ffmpeg", "-y", "-i", path, "-vn", "-acodec", "copy", output])

    @register_cap(registry, "video.merge", Cap.WRITE, "Concatenate videos", approve_all)
    def merge(paths: list, output: str):
        with open("/tmp/ffmpeg_concat.txt", "w") as f:
            f.writelines(f"file '{p}'\n" for p in paths)
        res = run_shell(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "/tmp/ffmpeg_concat.txt", "-c", "copy", output])
        return res

    @register_cap(registry, "video.convert", Cap.WRITE, "Convert format", approve_all)
    def convert(path: str, output: str, format: str):
        return run_shell(["ffmpeg", "-y", "-i", path, output])

    @register_cap(registry, "video.compress", Cap.WRITE, "Compress video", approve_all)
    def compress(path: str, output: str, crf: int = 23):
        return run_shell(["ffmpeg", "-y", "-i", path, "-vcodec", "libx264", "-crf", str(crf), output])

    @register_cap(registry, "video.add_audio", Cap.WRITE, "Replace/add audio track", approve_all)
    def add_audio(video_path: str, audio_path: str, output: str):
        return run_shell(["ffmpeg", "-y", "-i", video_path, "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", output])

    @register_cap(registry, "video.remove_audio", Cap.WRITE, "Strip audio", approve_all)
    def remove_audio(path: str, output: str):
        return run_shell(["ffmpeg", "-y", "-i", path, "-c", "copy", "-an", output])

    @register_cap(registry, "audio.convert", Cap.WRITE, "Convert audio format", approve_all)
    def convert_audio(path: str, output: str, format: str):
        return run_shell(["ffmpeg", "-y", "-i", path, output])

    @register_cap(registry, "audio.trim", Cap.WRITE, "Trim audio", approve_all)
    def trim_audio(path: str, output: str, start: str, end: str):
        return run_shell(["ffmpeg", "-y", "-i", path, "-ss", start, "-to", end, "-c", "copy", output])

    @register_cap(registry, "audio.merge", Cap.WRITE, "Merge audio files", approve_all)
    def merge_audio(paths: list, output: str):
        with open("/tmp/ffmpeg_audio_concat.txt", "w") as f:
            f.writelines(f"file '{p}'\n" for p in paths)
        res = run_shell(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "/tmp/ffmpeg_audio_concat.txt", "-c", "copy", output])
        return res

    # ── media.* namespace ─────────────────────────────────────────────────
    # Generic media player control using playerctl (MPRIS2 DBus interface)

    def _media_playerctl(sub_args: list) -> Any:

        if not _shutil.which("playerctl"):
            return fail("playerctl not installed (install with: sudo apt install playerctl)")
        return run_shell(["playerctl"] + sub_args)

    @register_cap(registry, "media.play", Cap.LOW, "Play media in active player", approve_all)
    def media_play():
        return _media_playerctl(["play"])

    @register_cap(registry, "media.pause", Cap.LOW, "Pause media player", approve_all)
    def media_pause():
        return _media_playerctl(["pause"])

    @register_cap(registry, "media.stop", Cap.LOW, "Stop media player", approve_all)
    def media_stop():
        return _media_playerctl(["stop"])

    @register_cap(registry, "media.resume", Cap.LOW, "Resume paused media", approve_all)
    def media_resume():
        return _media_playerctl(["play"])

    @register_cap(registry, "media.next_track", Cap.LOW, "Skip to next track", approve_all)
    def media_next():
        return _media_playerctl(["next"])

    @register_cap(registry, "media.prev_track", Cap.LOW, "Go to previous track", approve_all)
    def media_prev():
        return _media_playerctl(["previous"])

    @register_cap(registry, "media.get_volume", Cap.READ, "Get media player volume (0-100)", approve_all)
    def media_get_vol():
        return _media_playerctl(["volume"])

    @register_cap(registry, "media.set_volume", Cap.LOW, "Set media player volume (0.0-1.0)", approve_all)
    def media_set_vol(volume: float = 0.5):
        return _media_playerctl(["volume", str(volume)])

    @register_cap(registry, "media.mute", Cap.LOW, "Mute active media player", approve_all)
    def media_mute():
        return _media_playerctl(["volume", "0"])

    @register_cap(registry, "media.unmute", Cap.LOW, "Unmute media player to 50%", approve_all)
    def media_unmute():
        return _media_playerctl(["volume", "0.5"])

    @register_cap(registry, "media.convert", Cap.WRITE, "Convert media file format via ffmpeg", approve_all)
    def media_convert(path: str, output: str):
        return run_shell(["ffmpeg", "-y", "-i", path, output])

    @register_cap(registry, "media.merge", Cap.WRITE, "Merge multiple media files via ffmpeg", approve_all)
    def media_merge(paths: list, output: str):
        with open("/tmp/ffmpeg_media_concat.txt", "w") as f:
            f.writelines(f"file '{p}'\n" for p in paths)
        return run_shell(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "/tmp/ffmpeg_media_concat.txt", "-c", "copy", output])

    @register_cap(registry, "media.trim", Cap.WRITE, "Trim media file to time range", approve_all)
    def media_trim(path: str, output: str, start: str, end: str):
        return run_shell(["ffmpeg", "-y", "-i", path, "-ss", start, "-to", end, "-c", "copy", output])

