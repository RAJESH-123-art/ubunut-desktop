from capabilities.base import Cap, fail, ok, register_cap, run_shell


def _display_get_info(args, context):
    code, out, err = run_shell(["xrandr", "-q"])
    return ok({"info": out}) if code == 0 else fail(err)

def _display_list_monitors(args, context):
    code, out, err = run_shell(["xrandr", "--listmonitors"])
    return ok({"monitors": out}) if code == 0 else fail(err)

def _display_get_resolution(args, context):
    code, out, err = run_shell("xrandr | grep '\\*' | awk '{print $1}'", shell=True)
    return ok({"resolution": out}) if code == 0 else fail(err)

def _display_set_resolution(args, context):
    monitor = args.get("monitor")
    resolution = args.get("resolution")
    if not monitor or not resolution: return fail("Missing monitor or resolution")
    code, _out, err = run_shell(["xrandr", "--output", monitor, "--mode", resolution])
    return ok() if code == 0 else fail(err)

def _display_get_brightness(args, context):
    code, out, err = run_shell(["brightnessctl", "get"])
    return ok({"brightness": out}) if code == 0 else fail(err)

def _display_set_brightness(args, context):
    level = args.get("level", 50)
    code, _out, err = run_shell(["brightnessctl", "set", f"{level}%"])
    return ok() if code == 0 else fail(err)

def _display_increase_brightness(args, context):
    amount = args.get("amount", 10)
    code, _out, err = run_shell(["brightnessctl", "set", f"+{amount}%"])
    return ok() if code == 0 else fail(err)

def _display_decrease_brightness(args, context):
    amount = args.get("amount", 10)
    code, _out, err = run_shell(["brightnessctl", "set", f"{amount}%-"])
    return ok() if code == 0 else fail(err)

def _display_enable_monitor(args, context):
    monitor = args.get("monitor")
    if not monitor: return fail("Missing monitor")
    code, _out, err = run_shell(["xrandr", "--output", monitor, "--auto"])
    return ok() if code == 0 else fail(err)

def _display_disable_monitor(args, context):
    monitor = args.get("monitor")
    if not monitor: return fail("Missing monitor")
    code, _out, err = run_shell(["xrandr", "--output", monitor, "--off"])
    return ok() if code == 0 else fail(err)

def _display_rotate(args, context):
    monitor = args.get("monitor")
    rotation = args.get("rotation", "normal")
    if not monitor: return fail("Missing monitor")
    code, _out, err = run_shell(["xrandr", "--output", monitor, "--rotate", rotation])
    return ok() if code == 0 else fail(err)

def _display_night_mode(args, context):
    code, _out, err = run_shell(["gsettings", "set", "org.gnome.settings-daemon.plugins.color", "night-light-enabled", "true"])
    return ok() if code == 0 else fail(err)

def _display_night_mode_off(args, context):
    code, _out, err = run_shell(["gsettings", "set", "org.gnome.settings-daemon.plugins.color", "night-light-enabled", "false"])
    return ok() if code == 0 else fail(err)


def install(registry, *, approve_all=False):
    """Installs display controls."""
    
    register_cap(registry, Cap("display.get_info", "get display info", "read"), _display_get_info)
    register_cap(registry, Cap("display.list_monitors", "list monitors", "read"), _display_list_monitors)
    register_cap(registry, Cap("display.get_resolution", "current resolution", "read"), _display_get_resolution)
    
    req = not approve_all
    register_cap(registry, Cap("display.set_resolution", "change resolution", "system", ("monitor", "resolution"), requires_confirmation=req), _display_set_resolution)
    
    register_cap(registry, Cap("display.get_brightness", "get backlight brightness", "read"), _display_get_brightness)
    register_cap(registry, Cap("display.set_brightness", "set brightness", "local_write", ("level",)), _display_set_brightness)
    register_cap(registry, Cap("display.increase_brightness", "increase brightness", "local_write", ("amount",)), _display_increase_brightness)
    register_cap(registry, Cap("display.decrease_brightness", "decrease brightness", "local_write", ("amount",)), _display_decrease_brightness)
    
    register_cap(registry, Cap("display.enable_monitor", "enable a monitor", "system", ("monitor",)), _display_enable_monitor)
    register_cap(registry, Cap("display.disable_monitor", "disable a monitor", "system", ("monitor",)), _display_disable_monitor)
    register_cap(registry, Cap("display.rotate", "rotate display", "system", ("monitor", "rotation")), _display_rotate)
    
    register_cap(registry, Cap("display.night_mode", "enable night mode", "local_write"), _display_night_mode)
    register_cap(registry, Cap("display.night_mode_off", "disable night mode", "local_write"), _display_night_mode_off)
