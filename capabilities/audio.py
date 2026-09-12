from capabilities.base import Cap, fail, ok, register_cap, run_shell


def _exec_volume_cmd(args, context, action, level=None):
    if action == "get":
        code, out, err = run_shell(["amixer", "sget", "Master"])
        if code != 0: return fail("Failed to get volume: " + err)
        return ok({"output": out})
    elif action == "set":
        code, out, err = run_shell(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
        if code != 0:
            code, out, err = run_shell(["amixer", "set", "Master", f"{level}%"])
            if code != 0: return fail("Failed to set volume")
        return ok()
    elif action == "increase":
        amount = args.get("amount", 10)
        code, out, err = run_shell(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{amount}%"])
        if code != 0:
            code, out, err = run_shell(["amixer", "set", "Master", f"{amount}%+"])
            if code != 0: return fail("Failed to increase volume")
        return ok()
    elif action == "decrease":
        amount = args.get("amount", 10)
        code, out, err = run_shell(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{amount}%"])
        if code != 0:
            code, out, err = run_shell(["amixer", "set", "Master", f"{amount}%-"])
            if code != 0: return fail("Failed to decrease volume")
        return ok()
    elif action == "mute":
        code, out, err = run_shell(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"])
        if code != 0:
            code, out, err = run_shell(["amixer", "-q", "set", "Master", "mute"])
        return ok() if code == 0 else fail("Failed to mute: " + err)
    elif action == "unmute":
        code, out, err = run_shell(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"])
        if code != 0:
            code, out, err = run_shell(["amixer", "-q", "set", "Master", "unmute"])
        return ok() if code == 0 else fail("Failed to unmute: " + err)
    elif action == "toggle_mute":
        code, out, err = run_shell(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        if code != 0:
            code, out, err = run_shell(["amixer", "-q", "set", "Master", "toggle"])
        return ok() if code == 0 else fail("Failed to toggle mute: " + err)
    return fail("Unknown action")

def _exec_list_outputs(args, context):
    _code, out, _err = run_shell(["pactl", "list", "short", "sinks"])
    return ok({"outputs": out})

def _exec_list_inputs(args, context):
    _code, out, _err = run_shell(["pactl", "list", "short", "sources"])
    return ok({"inputs": out})

def _exec_set_output(args, context):
    device = args.get("device")
    if not device: return fail("Missing device")
    code, _out, err = run_shell(["pactl", "set-default-sink", device])
    return ok() if code == 0 else fail(err)

def _exec_set_input(args, context):
    device = args.get("device")
    if not device: return fail("Missing device")
    code, _out, err = run_shell(["pactl", "set-default-source", device])
    return ok() if code == 0 else fail(err)

def _exec_play(args, context):
    path = args.get("path")
    if not path: return fail("Missing path")
    code, _out, err = run_shell(["ffplay", "-nodisp", "-autoexit", path]) # simple player fallback
    return ok() if code == 0 else fail(err)

def _exec_playerctl(args, context, cmd):
    code, _out, err = run_shell(["playerctl", cmd])
    return ok() if code == 0 else fail(err)

def install(registry, *, approve_all=False):
    """Installs audio controls with risk local_write."""
    
    register_cap(registry, Cap("audio.get_volume", "get current output volume", "read"), lambda a, c: _exec_volume_cmd(a, c, "get"))
    register_cap(registry, Cap("audio.set_volume", "set volume", "local_write", ("level",)), lambda a, c: _exec_volume_cmd(a, c, "set", a.get("level", 50)))
    register_cap(registry, Cap("audio.increase_volume", "increase by N%", "local_write", ("amount",)), lambda a, c: _exec_volume_cmd(a, c, "increase"))
    register_cap(registry, Cap("audio.decrease_volume", "decrease by N%", "local_write", ("amount",)), lambda a, c: _exec_volume_cmd(a, c, "decrease"))
    register_cap(registry, Cap("audio.mute", "mute audio", "local_write"), lambda a, c: _exec_volume_cmd(a, c, "mute"))
    register_cap(registry, Cap("audio.unmute", "unmute audio", "local_write"), lambda a, c: _exec_volume_cmd(a, c, "unmute"))
    register_cap(registry, Cap("audio.toggle_mute", "toggle mute", "local_write"), lambda a, c: _exec_volume_cmd(a, c, "toggle_mute"))
    
    register_cap(registry, Cap("audio.list_outputs", "list audio outputs", "read"), _exec_list_outputs)
    register_cap(registry, Cap("audio.list_inputs", "list audio inputs", "read"), _exec_list_inputs)
    register_cap(registry, Cap("audio.set_output", "set default output", "system", ("device",)), _exec_set_output)
    register_cap(registry, Cap("audio.set_input", "set default input", "system", ("device",)), _exec_set_input)
    
    register_cap(registry, Cap("audio.play", "play audio file", "local_write", ("path",)), _exec_play)
    register_cap(registry, Cap("audio.stop", "stop playback", "local_write"), lambda a, c: _exec_playerctl(a, c, "stop"))
    register_cap(registry, Cap("audio.pause", "pause playback", "local_write"), lambda a, c: _exec_playerctl(a, c, "pause"))
    register_cap(registry, Cap("audio.resume", "resume playback", "local_write"), lambda a, c: _exec_playerctl(a, c, "play"))
    register_cap(registry, Cap("audio.next_track", "next track", "local_write"), lambda a, c: _exec_playerctl(a, c, "next"))
    register_cap(registry, Cap("audio.prev_track", "previous track", "local_write"), lambda a, c: _exec_playerctl(a, c, "previous"))
