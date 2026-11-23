"""Config file handling: defaults, loading, validation, paging maths.

Deliberately free of GTK imports so `knober config check` and `knober-ctl`
can use it cheaply. (check_fonts imports Pango only when called.)
"""
import copy
import math
import os
import tomllib

CONFIG_PATH = os.path.expanduser(os.environ.get("KNOBER_CONFIG", "~/.config/knober/config.toml"))
EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), "config.example.toml")

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


def _weight(v):
    """'bold' / 700 / "650" -> int 100..1000, or None if invalid."""
    if isinstance(v, str):
        v = _WEIGHTS.get(v.strip().lower().replace("-", "").replace(" ", ""), v)
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if 100 <= v <= 1000 else None


_STYLES = ("normal", "italic", "oblique")


def check_fonts(F):
    """Problems with the [fonts] section, as human-readable strings (empty = all good)."""
    import gi
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo

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


BUILTIN_ACTIONS = ("@media play-pause", "@media next", "@media previous")
_ITEM_KEYS = {"icon", "label", "cmd"}


def read_user_config():
    """The raw TOML as written by the user ({} if the file doesn't exist). Raises on bad TOML."""
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def lint(user):
    """Warnings for things that load fine but are probably mistakes: unknown keys
    (typos are otherwise silently ignored) and unknown @-actions."""
    warnings = []
    for section, value in user.items():
        if section not in DEFAULTS:
            warnings.append(f"unknown section [{section}]")
        elif section == "items":
            for n, item in enumerate(value if isinstance(value, list) else [], start=1):
                if isinstance(item, dict):
                    warnings += [f"[[items]] #{n} ({item.get('label', '?')}): unknown key {k!r}"
                                 for k in item if k not in _ITEM_KEYS]
        elif isinstance(value, dict):
            allowed = _ITEM_KEYS if section == "center" else DEFAULTS[section]
            warnings += [f"[{section}] unknown key {k!r}" for k in value if k not in allowed]
    entries = [("[center]", user.get("center", {}))] + [
        (f"[[items]] #{n}", i) for n, i in enumerate(user.get("items", []) or [], start=1)]
    for where, entry in entries:
        cmd = entry.get("cmd", "") if isinstance(entry, dict) else ""
        if isinstance(cmd, str) and cmd.startswith("@") and cmd not in BUILTIN_ACTIONS:
            warnings.append(f"{where}: unknown action {cmd!r} (known: {', '.join(BUILTIN_ACTIONS)})")
    return warnings
