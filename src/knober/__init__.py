"""knober — turn a keyboard volume knob into a pie-menu controller (Hyprland/Wayland).

Kept import-free on purpose: `knober-ctl` imports this on every knob click.
"""
import os

__version__ = "0.1.0"

SOCKET_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "knober.sock")
