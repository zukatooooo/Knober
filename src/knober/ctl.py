"""Send a knob event to the knober daemon: knober-ctl up|down|press|close|reload

If the daemon isn't running, falls back to normal behaviour
(volume up/down, mute toggle) so the knob never stops working.

This runs on every knob click, so it must stay light: standard library only.
(`knober up` etc. do the same thing through the full CLI.)
"""
import socket
import subprocess
import sys

from . import SOCKET_PATH, __version__

COMMANDS = ("up", "down", "press", "close", "reload")
USAGE = f"usage: knober-ctl {{{'|'.join(COMMANDS)}}}"


def send(cmd):
    """Deliver cmd to the daemon. False if no daemon is listening."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(cmd.encode(), SOCKET_PATH)
        return True
    except OSError:
        return False


def fallback(cmd):
    """Plain knob behaviour while the daemon is down. Uses the volume settings from
    config.toml (only read on this rare path, so knob clicks stay fast)."""
    from .config import load_config
    cfg, _ = load_config()
    g = (cfg or {}).get("general", {})
    step, vmax = g.get("volume_step", 5), g.get("volume_max", 100)
    action = {
        "up": f"wpctl set-volume -l {vmax / 100} @DEFAULT_AUDIO_SINK@ {step}%+",
        "down": f"wpctl set-volume @DEFAULT_AUDIO_SINK@ {step}%-",
        "press": "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle",
    }.get(cmd)
    if action:
        subprocess.run(action, shell=True)
        return True
    return False


def handle(cmd):
    """Send cmd, falling back to plain volume control. Returns an exit code."""
    if send(cmd) or fallback(cmd):
        return 0
    print("knober: the menu program is not running (start it with: knober start)", file=sys.stderr)
    return 1


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    if args[0] in ("-V", "--version"):
        print(f"knober-ctl {__version__}")
        return 0
    if args[0] not in COMMANDS:
        print(f"knober-ctl: unknown command {args[0]!r}\n{USAGE}", file=sys.stderr)
        return 2
    return handle(args[0])


if __name__ == "__main__":
    sys.exit(main())
