"""knober — turn a keyboard volume knob into a menu controller.

Daemon: shows a floating pie menu (bottom-right corner, layer-shell overlay).
It listens on a UNIX socket for commands sent by `knober-ctl`:

    up / down   menu hidden -> change volume ; menu shown -> move selection
    press       menu hidden -> open menu     ; menu shown -> run selected item
    close       hide the menu
    reload      re-read the config file

Settings live in ~/.config/knober/config.toml (or $KNOBER_CONFIG). Every key is
optional; anything missing falls back to DEFAULTS below. The file is re-read each
time the menu opens, so edits apply without restarting.
"""
import copy
import ctypes.util
import fcntl
import math
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

from . import LOCK_PATH, LOG_PATH, SOCKET_PATH, STATE_DIR, __version__
from .config import (CONFIG_PATH, DEFAULTS, _weight, check_fonts, geometry, lint, load_config,
                     paginate, read_user_config)

# gtk4-layer-shell must be loaded before libwayland, which Python can't
# guarantee -> re-exec ourselves once with LD_PRELOAD set.
_LS_LIB = ctypes.util.find_library("gtk4-layer-shell")  # e.g. "libgtk4-layer-shell.so.0"
if not _LS_LIB:
    sys.exit("knober: gtk4-layer-shell is not installed (sudo pacman -S gtk4-layer-shell)")
if "libgtk4-layer-shell" not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = (_LS_LIB + " " + os.environ.get("LD_PRELOAD", "")).strip()
    # -m works however we were started: /usr/bin/knober, python -m knober, ./knober
    # -P: never pick up a "knober" folder from the current directory instead of the install
    os.execv(sys.executable, [sys.executable, "-P", "-m", "knober.daemon", *sys.argv[1:]])

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import GLib, Gtk, Gdk, Pango, PangoCairo, Gtk4LayerShell as LayerShell  # noqa: E402
try:  # GLib >= 2.80 moved unix signal handling to GLibUnix
    from gi.repository import GLibUnix  # noqa: E402
    _signal_add = GLibUnix.signal_add
except ImportError:
    _signal_add = GLib.unix_signal_add

# pywal colour slot used for each role when no override is given
_PYWAL_ROLE = {"background": "color0", "line": "color3", "accent": "color4", "text": "color7", "dim": "color8"}
_PYWAL_FALLBACK = {"color0": "140808", "color3": "575F66", "color4": "A07057", "color7": "c4c1c1", "color8": "6b5858"}

CSS = """
window.knober, window.knober.background {
    background: none;
    background-color: transparent;
    box-shadow: none;
    border: none;
}
"""


# ---------------------------------------------------------------- drawing helpers

_PANGO_STYLES = {"normal": Pango.Style.NORMAL, "italic": Pango.Style.ITALIC, "oblique": Pango.Style.OBLIQUE}


def make_font(family, weight="normal", style="normal", size_pt=None, size_px=None):
    # Pango's set_weight() only accepts its named steps; the string form takes any 100..1000.
    desc = Pango.FontDescription.from_string(f"weight={_weight(weight) or 400}")
    desc.set_family(family)
    desc.set_style(_PANGO_STYLES.get(str(style).lower(), Pango.Style.NORMAL))
    if size_px is not None:
        desc.set_absolute_size(max(1, size_px) * Pango.SCALE)
    else:
        desc.set_size(int(max(1, size_pt) * Pango.SCALE))
    return desc



def _hex(v):
    v = v.lstrip("#")
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))


def load_colors(C):
    """Role -> (r, g, b): explicit override, else pywal palette, else fixed fallback."""
    pal = dict(_PYWAL_FALLBACK)
    try:
        with open(os.path.expanduser(C["pywal_file"])) as f:
            pal.update(re.findall(r'(color\d+)\s*=\s*"([0-9a-fA-F]{6})"', f.read()))
    except OSError:
        pass
    out = {}
    for role, slot in _PYWAL_ROLE.items():
        try:
            out[role] = _hex(C.get(role) or pal[slot])
        except (ValueError, KeyError):
            out[role] = _hex(_PYWAL_FALLBACK[slot])
    return out


# ---------------------------------------------------------------- actions

def _players():
    """[(name, status)] for every MPRIS player (spotify, firefox, mpv, ...)."""
    out = subprocess.run(["playerctl", "-a", "metadata", "--format", "{{playerInstance}}\t{{status}}"],
                         capture_output=True, text=True).stdout
    return [tuple(l.split("\t", 1)) for l in out.splitlines() if "\t" in l]


_last_paused = []  # players we paused, so play-pause can resume exactly them


def media(action):
    """playerctl alone only controls the first player; this targets the right one(s)."""
    global _last_paused
    players = _players()
    playing = [n for n, s in players if s == "Playing"]
    if action == "play-pause":
        if playing:
            for n in playing:
                subprocess.run(["playerctl", "-p", n, "pause"])
            _last_paused = playing
        else:
            names = [n for n, _ in players]
            targets = [n for n in _last_paused if n in names] or names[:1]
            for n in targets:
                subprocess.run(["playerctl", "-p", n, "play"])
    else:  # next / previous
        names = [n for n, _ in players]
        prop = "CanGoNext" if action == "next" else "CanGoPrevious"
        # preference: playing > last paused > any; skip players that can't skip
        # (e.g. a YouTube video in Firefox reports CanGoNext=false)
        ordered = playing + [n for n in _last_paused if n in names] + names
        target = [n for n in ordered if _can(n, prop)][:1]
        log(f"{action} -> {target or 'no player can ' + action}")
        for n in target:
            subprocess.run(["playerctl", "-p", n, action])


def _can(player, prop):
    out = subprocess.run(["busctl", "--user", "get-property", f"org.mpris.MediaPlayer2.{player}",
                          "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", prop],
                         capture_output=True, text=True).stdout
    return out.strip() == "b true"


def run(cmd):
    if cmd and cmd.startswith("@media "):
        threading.Thread(target=media, args=(cmd.split(" ", 1)[1],), daemon=True).start()
    elif cmd:
        subprocess.Popen(cmd, shell=True, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- app

class Knober(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.knober")
        self.cfg = None
        self.index = 0
        self.page = 0
        self.hide_timer = None
        self.win = None
        self.area = None
        self.reload()

    def reload(self):
        """Re-read config.toml. A broken file keeps the last good config."""
        cfg, err = load_config()
        if err:
            log(f"config error, keeping previous settings: {err}")
            if self.cfg is None:
                cfg = copy.deepcopy(DEFAULTS)
            else:
                return
        if cfg != self.cfg:  # only warn when something changed, not on every menu open
            for p in check_fonts(cfg["fonts"]):
                log(f"config warning: [fonts] {p}")
            try:
                for w in lint(read_user_config()):
                    log(f"config warning: {w}")
            except Exception:  # unreadable file was already reported above
                pass
        self.cfg = cfg
        self.pages = paginate(cfg["items"], cfg["paging"]["per_page"])
        self.page = min(self.page, len(self.pages) - 1)
        self.build_page()
        self.index = min(self.index, len(self.items) - 1)
        self.pie_r, self.size = geometry(cfg["layout"])
        if self.area:
            self.area.set_content_width(self.size)
            self.area.set_content_height(self.size)
            self.win.set_default_size(self.size, self.size)

    def build_page(self):
        """self.items = what's on screen: [centre, (prev), page items..., (next)]."""
        P = self.cfg["paging"]
        has_prev, items, has_next = self.pages[self.page]
        prev = [{"icon": P["prev_icon"], "label": P["prev_label"], "_nav": -1}] if has_prev else []
        nxt = [{"icon": P["next_icon"], "label": P["next_label"], "_nav": 1}] if has_next else []
        self.items = [self.cfg["center"]] + prev + items + nxt  # index 0 = centre

    def turn_page(self, step):
        self.page = max(0, min(self.page + step, len(self.pages) - 1))
        self.build_page()
        has_prev, items, has_next = self.pages[self.page]
        if step > 0:   # came forward: select the first action of the new page
            self.index = 1 + has_prev
        else:          # came back: select the last action of the new page
            self.index = len(self.items) - 1 - has_next
        self.refresh()
        self.bump_timer()

    # ---------- UI ----------
    def do_activate(self):
        if self.win:  # activated again (e.g. via D-Bus): we already have our window
            return
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):   # GTK >= 4.12
            provider.load_from_string(CSS)
        else:
            provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)  # beat ~/.config/gtk-4.0/gtk.css

        win = Gtk.Window(application=self)
        win.add_css_class("knober")
        LayerShell.init_for_window(win)
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_anchor(win, LayerShell.Edge.BOTTOM, True)
        LayerShell.set_anchor(win, LayerShell.Edge.RIGHT, True)
        LayerShell.set_margin(win, LayerShell.Edge.BOTTOM, 0)
        LayerShell.set_margin(win, LayerShell.Edge.RIGHT, 0)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)  # never steal focus
        LayerShell.set_namespace(win, "knober")

        self.win = win
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self.draw)
        # empty input region: clicks pass through to windows under the pie
        win.connect("map", lambda w: w.get_surface().set_input_region(cairo.Region()))
        win.set_child(self.area)
        self.reload()  # applies the size now that the widgets exist
        self.hold()  # keep running while the window is hidden
        threading.Thread(target=self.serve, daemon=True).start()

    def refresh(self):
        self.area.queue_draw()

    def positions(self):
        """Centre (x, y, r) for every item; index 0 is the hub."""
        L = self.cfg["layout"]
        hx = hy = self.size - L["hub_inset"]
        arc_r = L["arc_radius"]
        pts = [(hx, hy, L["hub_radius"])]
        n = len(self.items) - 1
        for i in range(n):
            # first item at 180deg (left), last at 270deg (up), the rest evenly between
            a = math.pi + (i / (n - 1) if n > 1 else 0.5) * (math.pi / 2)
            pts.append((hx + arc_r * math.cos(a), hy + arc_r * math.sin(a), L["item_radius"]))
        return pts

    def draw(self, area, cr, w, h):
        L, F = self.cfg["layout"], self.cfg["fonts"]
        c = load_colors(self.cfg["colors"])
        bg, line, accent, fg, dim = c["background"], c["line"], c["accent"], c["text"], c["dim"]
        size, hub_inset, hub_r, arc_r = self.size, L["hub_inset"], L["hub_radius"], L["arc_radius"]
        hx = hy = size - hub_inset

        if L["show_background"]:
            # pie body: exact 90deg wedge, corner at (ox, oy), bordered on all three sides like a
            # Hyprland window border. The path runs along the middle of the border line.
            bw = L["border_width"]
            cr.set_line_width(bw)
            ox = oy = size - L["gap"] - bw / 2
            r = self.pie_r - bw / 2
            cr.new_path()
            cr.move_to(ox, oy)
            cr.line_to(ox - r, oy)
            cr.arc(ox, oy, r, math.pi, 1.5 * math.pi)
            cr.close_path()
            cr.set_source_rgba(*bg, 0.92)
            cr.fill_preserve()
            cr.set_line_join(cairo.LINE_JOIN_MITER)
            cr.set_source_rgba(*line, 0xee / 255)  # = Hyprland active_border rgba(color3 ee)
            cr.stroke()

        pts = self.positions()
        cr.set_line_width(L["line_width"])
        # guide arc through the item circles
        cr.new_path()
        cr.arc(hx, hy, arc_r, math.pi, 1.5 * math.pi)
        cr.set_source_rgba(*dim, 0.6)
        cr.stroke()

        # spokes: hub edge -> item edge
        for i, (x, y, r) in enumerate(pts[1:], start=1):
            ux, uy = (x - hx) / arc_r, (y - hy) / arc_r
            cr.new_path()
            cr.move_to(hx + ux * hub_r, hy + uy * hub_r)
            cr.line_to(x - ux * r, y - uy * r)
            cr.set_source_rgba(*(accent if i == self.index else dim), 1 if i == self.index else 0.6)
            cr.stroke()

        # circles + icons
        for i, (x, y, r) in enumerate(pts):
            sel = i == self.index
            cr.new_path()
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.set_source_rgba(*(accent if sel else bg), 1)
            cr.fill_preserve()
            cr.set_source_rgba(*(accent if sel else line), 1)
            cr.stroke()

            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(make_font(F["icon_family"], F["icon_weight"],
                                                  size_px=r * F["icon_scale"]))
            layout.set_text(self.items[i].get("icon", "•"), -1)
            _, logical = layout.get_pixel_extents()
            cr.new_path()
            cr.move_to(x - logical.width / 2 - logical.x, y - logical.height / 2 - logical.y)
            cr.set_source_rgba(*(bg if sel else fg), 1)
            PangoCairo.show_layout(cr, layout)

        if L["show_label"]:
            # selected item's name, in the strip below the spokes
            label = self.items[self.index].get("label", "")
            if self.cfg["paging"]["show_page_number"] and len(self.pages) > 1:
                label += f"  {self.page + 1}/{len(self.pages)}"
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(make_font(F["label_family"], F["label_weight"],
                                                  F["label_style"], size_pt=F["label_size"]))
            layout.set_text(label.upper() if F["label_uppercase"] else label, -1)
            _, logical = layout.get_pixel_extents()
            cr.new_path()
            cr.move_to(hx - hub_r - 14 - logical.width, size - hub_inset / 2 + 4 - logical.height / 2)
            cr.set_source_rgba(*fg, 0.85)
            PangoCairo.show_layout(cr, layout)

    def show(self):
        self.reload()  # pick up config edits every time the menu opens
        self.page = 0
        self.build_page()
        self.index = 0
        self.refresh()
        self.win.set_visible(True)
        self.bump_timer()

    def hide(self):
        self.win.set_visible(False)
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        return GLib.SOURCE_REMOVE

    def bump_timer(self):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        self.hide_timer = GLib.timeout_add(self.cfg["general"]["auto_hide_ms"], self._auto_hide)

    def _auto_hide(self):
        self.hide_timer = None
        self.win.set_visible(False)
        return GLib.SOURCE_REMOVE

    def volume(self, up):
        G = self.cfg["general"]
        if up:
            run(f"wpctl set-volume -l {G['volume_max'] / 100} @DEFAULT_AUDIO_SINK@ {G['volume_step']}%+")
        else:
            run(f"wpctl set-volume @DEFAULT_AUDIO_SINK@ {G['volume_step']}%-")

    # ---------- knob logic ----------
    def handle(self, cmd):
        visible = self.win.get_visible()
        if cmd == "press":
            if not visible:
                self.show()
            else:
                item = self.items[self.index]
                if "_nav" in item:  # previous / next page button: stay open
                    self.turn_page(item["_nav"])
                else:
                    self.hide()
                    run(item.get("cmd"))
        elif cmd in ("up", "down"):
            if not visible:
                self.volume(cmd == "up")
            else:
                step = 1 if cmd == "up" else -1  # clockwise = down the list
                self.index = (self.index + step) % len(self.items)
                self.refresh()
                self.bump_timer()
        elif cmd == "close":
            self.hide()
        elif cmd == "reload":
            self.reload()
            self.refresh()
        return GLib.SOURCE_REMOVE

    # ---------- socket server ----------
    def serve(self):
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        srv.bind(SOCKET_PATH)
        while True:
            data = srv.recv(64).decode().strip()
            GLib.idle_add(self.handle, data)

    def shutdown(self):
        """SIGTERM / SIGINT (knober stop, Ctrl+C): quit cleanly and remove our socket."""
        log("stopping")
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        self.quit()
        return GLib.SOURCE_REMOVE


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _log_to_file():
    """When not attached to a terminal (Hyprland autostart, `knober start`), send all
    output, including GTK's own warnings, to LOG_PATH. The previous run is kept as .1."""
    if sys.stderr.isatty():
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH):
        os.replace(LOG_PATH, LOG_PATH + ".1")
    fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-V", "--version"):
        print(f"knober {__version__}")
        return 0
    if args:
        print("usage: knober [--version]\n\nStarts the knober menu daemon. "
              "Send it knob events with knober-ctl.", file=sys.stderr if args[0] not in ("-h", "--help") else sys.stdout)
        return 0 if args[0] in ("-h", "--help") else 2
    # One daemon per session. The lock is held until the process exits (even if it
    # crashes), so unlike checking the process list this can't race with a second
    # copy starting at the same moment.
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("knober is already running; use `knober restart` to replace it", file=sys.stderr)
        return 0
    _log_to_file()
    log(f"knober {__version__} started (pid {os.getpid()}, config {CONFIG_PATH})")
    GLib.set_prgname("knober")
    app = Knober()
    for sig in (signal.SIGTERM, signal.SIGINT):
        _signal_add(GLib.PRIORITY_DEFAULT, sig, app.shutdown)
    return app.run(None)


if __name__ == "__main__":
    sys.exit(main())
