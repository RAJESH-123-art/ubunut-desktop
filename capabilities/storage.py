import json

from capabilities.base import Cap, fail, ok, register_cap, run_shell


def _storage_list_disks(args, context):
    code, out, err = run_shell(["lsblk", "-J", "-d"])
    if code != 0: return fail(err)
    try:
        return ok({"disks": json.loads(out)})
    except (TypeError, ValueError):
        return ok({"disks": out})

def _storage_list_partitions(args, context):
    code, out, err = run_shell(["lsblk", "-J"])
    if code != 0: return fail(err)
    try:
        return ok({"partitions": json.loads(out)})
    except (TypeError, ValueError):
        return ok({"partitions": out})

def _storage_get_disk_info(args, context):
    device = args.get("device")
    if not device: return fail("Missing device")
    code, out, err = run_shell(["lsblk", device, "-J"])
    if code != 0: return fail(err)
    return ok({"info": out})

def _storage_get_usage(args, context):
    path = args.get("path", "/")
    code, out, err = run_shell(["df", "-h", path])
    if code != 0: return fail(err)
    return ok({"usage": out})

def _storage_find_large_files(args, context):
    path = args.get("path", "/")
    min_size_mb = args.get("min_size_mb", 100)
    _code, out, _err = run_shell(["find", path, "-type", "f", "-size", f"+{min_size_mb}M"])
    # find might exit with 1 if permission denied in some dirs, but still return output
    return ok({"files": out.splitlines()})

def _storage_check_health(args, context):
    device = args.get("device")
    if not device: return fail("Missing device")
    code, out, _err = run_shell(["smartctl", "-H", device])
    status = "PASS" if code == 0 else "FAIL"
    return ok({"status": status, "output": out})


def install(registry, *, approve_all=False):
    """Installs storage controls."""
    register_cap(registry, Cap("storage.list_disks", "list physical disks", "read"), _storage_list_disks)
    register_cap(registry, Cap("storage.list_partitions", "list partitions", "read"), _storage_list_partitions)
    register_cap(registry, Cap("storage.get_disk_info", "disk info", "read", ("device",)), _storage_get_disk_info)
    register_cap(registry, Cap("storage.get_usage", "disk usage", "read", ("path",)), _storage_get_usage)
    register_cap(registry, Cap("storage.find_large_files", "find large files", "read", ("path", "min_size_mb")), _storage_find_large_files)
    register_cap(registry, Cap("storage.check_health", "check disk health", "read", ("device",)), _storage_check_health)
