from capabilities.base import Cap, fail, ok, register_cap, run_shell


def _camera_list(args, context):
    code, out, _err = run_shell(["v4l2-ctl", "--list-devices"])
    if code != 0:
        # fallback
        code, out, _err = run_shell("ls /dev/video*", shell=True)
    return ok({"devices": out.splitlines() if out else []})

def _camera_capture_photo(args, context):
    device = args.get("device", "/dev/video0")
    path = args.get("path")
    if not path: return fail("Missing path")
    try:
        code, _out, err = run_shell(["fswebcam", "-d", device, path])
        if code == 0: return ok()
        code, _out, err = run_shell(["ffmpeg", "-y", "-f", "video4linux2", "-i", device, "-vframes", "1", path])
        return ok() if code == 0 else fail(err)
    except Exception as e:
        return fail(str(e))

def _camera_record_video(args, context):
    device = args.get("device", "/dev/video0")
    path = args.get("path")
    duration = args.get("duration", 5)
    if not path: return fail("Missing path")
    try:
        code, _out, err = run_shell(["ffmpeg", "-y", "-f", "video4linux2", "-i", device, "-t", str(duration), path])
        return ok() if code == 0 else fail(err)
    except Exception as e:
        return fail(str(e))

def _camera_preview(args, context):
    player = _shutil_which_any(["cheese", "gnome-camera", "mpv"])
    if not player:
        return fail("No camera preview app found (cheese/gnome-camera/mpv)")
    try:
        device = args.get("device", "/dev/video0")
        if player == "mpv":
            code, _out, err = run_shell(["mpv", "--demuxer-lavf-format=video4linux2", f"avdevice:v4l2:{device}", "--really-quiet", "--frames=60"], timeout=20)
        else:
            code, _out, err = run_shell([player], timeout=20)
        return ok() if code in (0, 124) else fail(err)  # 124: closed by user
    except Exception as e:
        return fail(str(e))

def _shutil_which_any(candidates):
    import shutil
    for c in candidates:
        if shutil.which(c):
            return c
    return None

def install(registry, *, approve_all=False):
    """Installs camera controls."""
    register_cap(registry, Cap("camera.list", "list cameras", "read"), _camera_list)
    register_cap(registry, Cap("camera.capture_photo", "capture photo", "local_write", ("device", "path")), _camera_capture_photo)
    register_cap(registry, Cap("camera.record_video", "record video", "local_write", ("device", "path", "duration")), _camera_record_video)
    register_cap(registry, Cap("camera.preview", "open camera preview", "read"), _camera_preview)
