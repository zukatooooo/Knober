"""knober — turn a keyboard volume knob into a pie-menu controller (Hyprland/Wayland).

Kept import-free on purpose: `knober-ctl` imports this on every knob click.
"""
import os

__version__ = "0.2.0"

_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
SOCKET_PATH = os.path.join(_RUNTIME_DIR, "knober.sock")
LOCK_PATH = os.path.join(_RUNTIME_DIR, "knober.lock")  # held by the running daemon

STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "knober")
LOG_PATH = os.path.join(STATE_DIR, "knober.log")  # previous run is kept as knober.log.1
