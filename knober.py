#!/usr/bin/env python3
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
import math
import os
import re
import socket
import subprocess
import sys
import threading
import tomllib

# gtk4-layer-shell must be loaded before libwayland, which Python can't
# guarantee -> re-exec ourselves once with LD_PRELOAD set.
_LS_LIB = "/usr/lib/libgtk4-layer-shell.so"
if _LS_LIB not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = (_LS_LIB + " " + os.environ.get("LD_PRELOAD", "")).strip()
    os.execv(sys.executable, [sys.executable] + sys.argv)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("PangoCairo", "1.0")
import cairo  # noqa: E402
from gi.repository import GLib, Gtk, Gdk, Pango, PangoCairo, Gtk4LayerShell as LayerShell  # noqa: E402

SOCKET_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "knober.sock")
CONFIG_PATH = os.path.expanduser(os.environ.get("KNOBER_CONFIG", "~/.config/knober/config.toml"))

# Built-in defaults. config.toml only needs the keys you want to change.
# Icons are Nerd Font glyphs. Items sit on the arc, left -> top (clockwise).
DEFAULTS = {
    "general": {
        "auto_hide_ms": 6000,   # close the menu after this much inactivity
        "volume_step": 5,       # % per knob click while the menu is closed
        "volume_max": 100,      # % cap for volume up (>100 = allow boost)
    },
    "colors": {
        # Palette source: pywal colours from Hyprland's colors.lua.
        "pywal_file": "~/.config/hypr/colors.lua",
        # "#rrggbb" overrides; "" = take it from the pywal palette.
        "background": "",   # node fill        (pywal color0)
        "line": "",         # node outlines    (pywal color3)
        "accent": "",       # selected node    (pywal color4)
        "text": "",         # icons and label  (pywal color7)
        "dim": "",          # spokes, arc      (pywal color8)
    },
    "fonts": {
        # weight: thin, ultralight, light, semilight, book, normal, medium, semibold,
        #         bold, ultrabold, heavy, ultraheavy — or a number 100..1000
        # style:  normal, italic, oblique
        "icon_family": "Symbols Nerd Font",
        "icon_scale": 0.85,         # icon size relative to circle radius
        "icon_weight": "normal",
        "label_family": "monospace",
        "label_size": 10,           # points
        "label_weight": "bold",
        "label_style": "normal",
        "label_uppercase": True,
    },
    "layout": {
        "show_background": False,  # draw the bordered quarter-circle behind the nodes
        "show_label": True,        # name of the selected node, next to the hub
        "gap": 2,                  # Hyprland general.gaps_out: screen edge -> border
        "border_width": 2,         # background border (Hyprland general.border_size)
        "line_width": 2,           # spokes, guide arc and node circle outlines
        "hub_inset": 56,           # hub centre distance from the screen corner
        "hub_radius": 32,          # centre circle radius
        "arc_radius": 180,         # radius of the arc the item circles sit on
        "item_radius": 24,         # item circle radius
        "pie_padding": 20,         # space between the furthest item and the border
    },
    "paging": {
        # Max nodes on the arc per page, counting the previous/next page buttons
        # (the centre circle is not counted). Must be at least 3.
        "per_page": 6,
        "show_page_number": True,   # "1/3" after the label when there are several pages
        "prev_icon": "\U000F0141", "prev_label": "Previous page",
        "next_icon": "\U000F0142", "next_label": "Next page",
    },
    # The circle in the pie's centre. Selected when the menu opens.
    # cmd = "" -> pressing it just closes the menu.
    "center": {"icon": "\U000F0156", "label": "Close", "cmd": ""},
    "items": [
        {"icon": "\U000F040E", "label": "Play / Pause", "cmd": "@media play-pause"},
        {"icon": "\U000F04AD", "label": "Next track", "cmd": "@media next"},
        {"icon": "\U000F04AE", "label": "Previous track", "cmd": "@media previous"},
        {"icon": "\U000F0581", "label": "Mute speakers", "cmd": "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"},
        {"icon": "\U000F036D", "label": "Mute mic", "cmd": "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"},
    ],
}

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


# ---------------------------------------------------------------- config

def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config():
    """-> (config, error). On a broken file returns (None, message)."""
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_PATH, "rb") as f:
            user = tomllib.load(f)
    except FileNotFoundError:
        return cfg, None
    except (tomllib.TOMLDecodeError, OSError) as e:
        return None, f"{CONFIG_PATH}: {e}"
    _merge(cfg, user)
    items = cfg["items"]
    if not isinstance(items, list) or not all(isinstance(i, dict) and "label" in i for i in items):
        return None, f"{CONFIG_PATH}: every [[items]] entry needs at least a label"
    F = cfg["fonts"]
    for key in ("label_size", "icon_scale"):  # these would break drawing, so refuse them
        if not isinstance(F[key], (int, float)) or isinstance(F[key], bool) or F[key] <= 0:
            return None, f"{CONFIG_PATH}: [fonts] {key} must be a positive number, got {F[key]!r}"
    per_page = cfg["paging"]["per_page"]
    if not isinstance(per_page, int) or isinstance(per_page, bool) or per_page < 3:
        # 3 = prev + one action + next on a middle page; fewer can't make progress
        return None, f"{CONFIG_PATH}: [paging] per_page must be a whole number >= 3, got {per_page!r}"
    return cfg, None


def paginate(items, per_page):
    """Split items into pages -> [(has_prev, page_items, has_next)].
    first page: up to per_page-1 items + next; middle: prev + per_page-2 items + next;
    last page: prev + the rest. Everything fits -> one page, no buttons."""
    pages, i, n = [], 0, len(items)
    while i < n:
        has_prev = bool(pages)
        room = per_page - has_prev
        if n - i <= room:  # the rest fits: this is the last page
            pages.append((has_prev, items[i:], False))
            break
        take = room - 1    # leave a slot for the next button
        pages.append((has_prev, items[i:i + take], True))
        i += take
    return pages or [(False, [], False)]


def geometry(L):
    """(pie_radius, window_size). The background is an exact 90deg wedge whose square corner
    sits in the screen corner, `gap` px in from both edges, like a tiled window. Its radius
    just clears the furthest possible item (the one at 225deg)."""
    hub_from_corner = (L["hub_inset"] - L["gap"]) * math.sqrt(2)
    pie_r = math.ceil(hub_from_corner + L["arc_radius"] + L["item_radius"] + L["pie_padding"])
    return pie_r, L["gap"] + pie_r


_WEIGHTS = {"thin": 100, "ultralight": 200, "extralight": 200, "light": 300, "semilight": 350,
            "book": 380, "normal": 400, "regular": 400, "medium": 500, "semibold": 600,
            "demibold": 600, "bold": 700, "ultrabold": 800, "extrabold": 800, "heavy": 900,
            "black": 900, "ultraheavy": 1000}
_STYLES = {"normal": Pango.Style.NORMAL, "italic": Pango.Style.ITALIC, "oblique": Pango.Style.OBLIQUE}


def _weight(v):
    """'bold' / 700 / "650" -> int 100..1000, or None if invalid."""
    if isinstance(v, str):
        v = _WEIGHTS.get(v.strip().lower().replace("-", "").replace(" ", ""), v)
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if 100 <= v <= 1000 else None


def make_font(family, weight="normal", style="normal", size_pt=None, size_px=None):
    # Pango's set_weight() only accepts its named steps; the string form takes any 100..1000.
    desc = Pango.FontDescription.from_string(f"weight={_weight(weight) or 400}")
    desc.set_family(family)
    desc.set_style(_STYLES.get(str(style).lower(), Pango.Style.NORMAL))
    if size_px is not None:
        desc.set_absolute_size(max(1, size_px) * Pango.SCALE)
    else:
        desc.set_size(int(max(1, size_pt) * Pango.SCALE))
    return desc


def check_fonts(F):
    """Problems with the [fonts] section, as human-readable strings (empty = all good)."""
    problems = []
    families = {f.get_name().lower() for f in PangoCairo.FontMap.get_default().list_families()}
    generic = {"monospace", "sans", "sans-serif", "serif", "system-ui"}
    for key in ("icon_family", "label_family"):
        if F[key].lower() not in families | generic:
            problems.append(f"{key} = {F[key]!r} is not installed (using a fallback font; see `fc-list : family`)")
    for key in ("icon_weight", "label_weight"):
        if _weight(F[key]) is None:
            problems.append(f"{key} = {F[key]!r} is not a weight name or a number 100..1000 (using normal)")
    if str(F["label_style"]).lower() not in _STYLES:
        problems.append(f"label_style = {F['label_style']!r} must be normal, italic or oblique (using normal)")
    for key in ("label_size", "icon_scale"):
        if not isinstance(F[key], (int, float)) or F[key] <= 0:
            problems.append(f"{key} = {F[key]!r} must be a positive number")
    return problems


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
        print(f"knober: {action} -> {target or 'no player can ' + action}", flush=True)
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
            print(f"knober: config error, keeping previous settings: {err}", flush=True)
            if self.cfg is None:
                cfg = copy.deepcopy(DEFAULTS)
            else:
                return
        if cfg != self.cfg:  # only warn when something changed, not on every menu open
            for p in check_fonts(cfg["fonts"]):
                print(f"knober: [fonts] {p}", flush=True)
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


if __name__ == "__main__":
    Knober().run(None)
