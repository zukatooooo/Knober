"""Send a knob event to the knober daemon: knober-ctl up|down|press|close|reload

If the daemon isn't running, falls back to normal behaviour
(volume up/down, mute toggle) so the knob never stops working.

This runs on every knob click, so it must stay light: standard library only.
"""
import os
import socket
import subprocess
import sys

from . import SOCKET_PATH, __version__

COMMANDS = ("up", "down", "press", "close", "reload")
FALLBACK = {
    "up": "wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+",
    "down": "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-",
    "press": "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle",
}
USAGE = f"usage: knober-ctl {{{'|'.join(COMMANDS)}}}"


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    if args[0] in ("-V", "--version"):
        print(f"knober-ctl {__version__}")
        return 0
    cmd = args[0]
    if cmd not in COMMANDS:
        print(f"knober-ctl: unknown command {cmd!r}\n{USAGE}", file=sys.stderr)
        return 2
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(cmd.encode(), SOCKET_PATH)
    except OSError:
        if cmd in FALLBACK:
            subprocess.run(FALLBACK[cmd], shell=True)
        else:
            print("knober-ctl: knober is not running", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
