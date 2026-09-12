from capabilities.base import Cap, fail, ok, register_cap, run_shell


def _printer_list(args, context):
    code, out, err = run_shell(["lpstat", "-p"])
    return ok({"printers": out}) if code == 0 else fail(err)

def _printer_status(args, context):
    name = args.get("name")
    if not name: return fail("Missing printer name")
    code, out, err = run_shell(["lpstat", "-p", name])
    return ok({"status": out}) if code == 0 else fail(err)

def _printer_print_file(args, context):
    file_path = args.get("file_path")
    printer = args.get("printer")
    copies = args.get("copies", 1)
    if not file_path: return fail("Missing file_path")
    
    cmd = ["lp", "-n", str(copies)]
    if printer: cmd.extend(["-d", printer])
    cmd.append(file_path)
    
    code, out, err = run_shell(cmd)
    return ok({"job": out}) if code == 0 else fail(err)

def _printer_cancel_job(args, context):
    job_id = args.get("job_id")
    if not job_id: return fail("Missing job_id")
    code, _out, err = run_shell(["cancel", str(job_id)])
    return ok() if code == 0 else fail(err)

def _printer_list_jobs(args, context):
    printer = args.get("printer")
    cmd = ["lpstat", "-o"]
    if printer: cmd.append(printer)
    code, out, err = run_shell(cmd)
    return ok({"jobs": out}) if code == 0 else fail(err)


def install(registry, *, approve_all=False):
    """Installs printer controls."""
    register_cap(registry, Cap("printer.list", "list printers", "read"), _printer_list)
    register_cap(registry, Cap("printer.status", "get printer status", "read", ("name",)), _printer_status)
    register_cap(registry, Cap("printer.print_file", "print a file", "local_write", ("file_path", "printer", "copies")), _printer_print_file)
    register_cap(registry, Cap("printer.cancel_job", "cancel print job", "local_write", ("job_id",)), _printer_cancel_job)
    register_cap(registry, Cap("printer.list_jobs", "list print jobs", "read", ("printer",)), _printer_list_jobs)
