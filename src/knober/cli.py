"""The `knober` command.

    knober                      run the menu program (what Hyprland's autostart calls)
    knober start|stop|restart   manage it in the background
    knober status               is it running? is the config OK?
    knober up|down|press|close|reload
                                send a knob event (same as knober-ctl)
    knober logs [-f]            show / follow its log
    knober config path|init|edit|check

Only imports what each subcommand needs, so quick commands stay quick.
"""
import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

from . import LOG_PATH, SOCKET_PATH, __version__
from .ctl import COMMANDS as EVENTS
from .proc import alive, describe, find_daemons

STOP_TIMEOUT = 3.0   # seconds to wait for a clean exit before SIGKILL
START_TIMEOUT = 5.0  # seconds to wait for a new daemon to open its socket


# ---------------------------------------------------------------- daemon control

def _run_daemon():
    """Replace this process with the daemon (foreground)."""
    # daemon.py re-execs itself with LD_PRELOAD; argv must not carry our subcommand.
    sys.argv = [sys.argv[0]]
    from . import daemon
    return daemon.main()


def cmd_daemon(args):
    if args.replace:
        _stop(quiet=True)
    return _run_daemon()


def _stop(quiet=False):
    pids = find_daemons()
    if not pids:
        if not quiet:
            print("knober is not running")
        return True
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline and any(alive(p) for p in pids):
        time.sleep(0.05)
    stubborn = [p for p in pids if alive(p)]
    for pid in stubborn:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if stubborn and os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)  # a killed daemon can't clean up after itself
    if not quiet:
        print(f"stopped knober (pid {', '.join(map(str, pids))})"
              + (" — had to force-kill it" if stubborn else ""))
    return True


def _start():
    running = find_daemons()
    if running:
        print(f"knober is already running ({describe(running[0])})")
        return 0
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)  # left by a crashed/killed daemon; the new one makes a fresh one
    # Same interpreter and environment (PYTHONPATH etc.), detached from this terminal.
    # With no terminal attached, the daemon writes everything to LOG_PATH.
    subprocess.Popen([sys.executable, "-P", "-m", "knober.daemon"],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        pids = find_daemons()
        if pids and os.path.exists(SOCKET_PATH):
            print(f"started knober ({describe(pids[0])})")
            return 0
        time.sleep(0.1)
    print(f"knober did not start; see the log:  knober logs   ({LOG_PATH})", file=sys.stderr)
    return 1


def cmd_start(args):
    return _start()


def cmd_stop(args):
    _stop()
    return 0


def cmd_restart(args):
    _stop(quiet=True)
    return _start()


def cmd_status(args):
    from .config import CONFIG_PATH, lint, load_config, read_user_config
    pids = find_daemons()
    if pids:
        print(f"● knober is running ({describe(pids[0])})")
        if len(pids) > 1:
            print(f"  warning: {len(pids)} copies running (pids {', '.join(map(str, pids))}); "
                  "fix with: knober restart")
    else:
        print("○ knober is not running (start it with: knober start)")
    print(f"  version: {__version__}")
    cfg, err = load_config()
    if not os.path.exists(CONFIG_PATH):
        state = "not created, using built-in defaults (create it with: knober config init)"
    elif err:
        state = "ERROR, see: knober config check"
    else:
        try:
            n = len(lint(read_user_config()))
        except Exception:
            n = 0
        state = "ok" + (f", {n} warning{'s' * (n != 1)} (see: knober config check)" if n else "")
    print(f"  config:  {CONFIG_PATH} ({state})")
    if cfg:
        from .config import paginate
        pages = paginate(cfg["items"], cfg["paging"]["per_page"])
        print(f"  menu:    {len(cfg['items'])} node{'s' * (len(cfg['items']) != 1)} on {len(pages)} page{'s' * (len(pages) != 1)}")
    print(f"  log:     {LOG_PATH}")
    return 0 if pids else 3  # 3 = "not running", as systemctl status does


def cmd_event(args):
    from .ctl import handle
    return handle(args.command)


def cmd_logs(args):
    if not os.path.exists(LOG_PATH):
        print(f"no log yet ({LOG_PATH}). knober only writes one when it runs without a terminal,\n"
              "e.g. from Hyprland's autostart or `knober start`.", file=sys.stderr)
        return 1
    tail = ["tail", "-n", str(args.lines)] + (["-F"] if args.follow else []) + [LOG_PATH]
    os.execvp(tail[0], tail)


# ---------------------------------------------------------------- config

def cmd_config_path(args):
    from .config import CONFIG_PATH
    print(CONFIG_PATH)
    if not os.path.exists(CONFIG_PATH):
        print("(doesn't exist yet: knober is using its built-in defaults; create it with: knober config init)",
              file=sys.stderr)
    return 0


def _init_config(force=False):
    from .config import CONFIG_PATH, EXAMPLE_PATH
    if os.path.exists(CONFIG_PATH) and not force:
        print(f"{CONFIG_PATH} already exists (use --force to overwrite it)", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    if force and os.path.exists(CONFIG_PATH):
        shutil.copy2(CONFIG_PATH, CONFIG_PATH + ".bak")
        print(f"backed up the old config to {CONFIG_PATH}.bak")
    shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
    print(f"created {CONFIG_PATH}")
    return 0


def cmd_config_init(args):
    return _init_config(args.force)


def cmd_config_edit(args):
    from .config import CONFIG_PATH
    if not os.path.exists(CONFIG_PATH):
        _init_config()
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or next(
        (e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
    if not editor:
        print(f"no editor found; set $EDITOR or edit {CONFIG_PATH} by hand", file=sys.stderr)
        return 1
    cmd = shlex.split(editor) + [CONFIG_PATH]
    os.execvp(cmd[0], cmd)


def cmd_config_check(args):
    from .config import CONFIG_PATH, check_fonts, geometry, lint, load_config, paginate, read_user_config
    exists = os.path.exists(CONFIG_PATH)
    print(f"checking {CONFIG_PATH}" + ("" if exists else " (doesn't exist: checking the built-in defaults)"))
    cfg, err = load_config()
    if err:
        print(f"✗ error: {err}\n  knober ignores the file until this is fixed and keeps its last working settings.")
        return 1
    warnings = [f"[fonts] {p}" for p in check_fonts(cfg["fonts"])] + lint(read_user_config())
    for w in warnings:
        print(f"! warning: {w}")
    per_page = cfg["paging"]["per_page"]
    pages = paginate(cfg["items"], per_page)
    print(f"✓ {len(cfg['items'])} node{'s' * (len(cfg['items']) != 1)}, {len(pages)} page{'s' * (len(pages) != 1)} "
          f"(max {per_page} on the arc, counting page buttons):")
    for n, (has_prev, items, has_next) in enumerate(pages, start=1):
        row = (["‹ prev"] if has_prev else []) + [i.get("label", "?") for i in items] + (["next ›"] if has_next else [])
        print(f"    page {n}: " + "  ·  ".join(row))
    size = geometry(cfg["layout"])[1]
    print(f"✓ menu window: {size}×{size} px in the bottom-right corner")
    print("✓ config OK" + (f", with {len(warnings)} warning{'s' * (len(warnings) != 1)}" if warnings else ""))
    return 0


# ---------------------------------------------------------------- argument parsing

def build_parser():
    p = argparse.ArgumentParser(
        prog="knober",
        description="Turn your keyboard's volume knob into a pie-menu controller (Hyprland).",
        epilog="With no command, knober runs the menu program in the foreground "
               "(that's what Hyprland's autostart line uses).")
    p.add_argument("-V", "--version", action="version", version=f"knober {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    d = sub.add_parser("daemon", help="run the menu program in the foreground")
    d.add_argument("--replace", action="store_true", help="stop a running copy first")
    d.set_defaults(func=cmd_daemon)

    sub.add_parser("start", help="start the menu program in the background").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="stop the menu program").set_defaults(func=cmd_stop)
    sub.add_parser("restart", help="restart it (e.g. after updating knober)").set_defaults(func=cmd_restart)
    sub.add_parser("status", help="is it running? is the config OK?").set_defaults(func=cmd_status)

    helps = {"up": "knob turned right: volume up, or next node",
             "down": "knob turned left: volume down, or previous node",
             "press": "knob pressed: open the menu, or run the selected node",
             "close": "close the menu", "reload": "re-read the config file now"}
    for e in EVENTS:
        sub.add_parser(e, help=helps[e]).set_defaults(func=cmd_event)

    lg = sub.add_parser("logs", help="show the log")
    lg.add_argument("-f", "--follow", action="store_true", help="keep showing new lines")
    lg.add_argument("-n", "--lines", type=int, default=50, metavar="N", help="last N lines (default 50)")
    lg.set_defaults(func=cmd_logs)

    c = sub.add_parser("config", help="config file helpers (path, init, edit, check)")
    csub = c.add_subparsers(dest="config_command", metavar="ACTION", required=True)
    csub.add_parser("path", help="print the config file location").set_defaults(func=cmd_config_path)
    ci = csub.add_parser("init", help="create it from the commented example")
    ci.add_argument("--force", action="store_true", help="overwrite an existing config (keeps a .bak)")
    ci.set_defaults(func=cmd_config_init)
    csub.add_parser("edit", help="open it in $EDITOR (creates it if needed)").set_defaults(func=cmd_config_edit)
    csub.add_parser("check", help="validate it and show how the pages split").set_defaults(func=cmd_config_check)
    return p


def main():
    args = build_parser().parse_args()
    if not args.command:
        return _run_daemon()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
