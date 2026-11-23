# knober

Turn the volume knob on your keyboard into a controller for a small pie menu
in the bottom-right corner of the screen, on **Hyprland (Wayland)**.

| Knob action | Menu closed | Menu open |
|---|---|---|
| **Rotate** | Volume up / down (same as before) | Move the selection |
| **Press** | Open the menu | Run the selected node |

The volume knob keeps working as usual. The menu only takes over the knob while
it is open, and it closes by itself after a few seconds without input.

The menu is a quarter-circle fan: a **Close** circle sits in the corner, and the
action nodes are spread on an arc around it, from left to top, each joined to
the centre by a spoke. The selected node is highlighted, and its name appears
under the pie.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Using it](#using-it)
- [Configuration](#configuration)
  - [Menu nodes](#menu-nodes-items)
  - [Built-in actions](#built-in-actions)
  - [Paging](#paging)
  - [All settings](#all-settings)
- [Commands](#commands)
- [Troubleshooting](#troubleshooting)
- [How it works](#how-it-works)
- [Known limitations](#known-limitations)
- [License](#license)

---

## Requirements

| Package (Arch) | Used for |
|---|---|
| `hyprland` | Catches the knob's keys |
| `python` 3.11 or newer | Runs knober (needs the built-in `tomllib`) |
| `python-gobject`, `python-cairo`, `gtk4`, `pango` | Draws the menu |
| `gtk4-layer-shell` | Puts the menu in a screen corner as an overlay on Wayland |
| `wireplumber` (`wpctl`) | Volume and mute |
| `playerctl` | Music control (play, pause, next, previous) |
| `systemd` (`busctl`) | Checks whether a player can skip tracks |
| A Nerd Font, such as `ttf-nerd-fonts-symbols` | Node icons (optional, but nodes show boxes without one) |

When you install the Arch package, pacman installs all of these except
Hyprland and the font for you.

---

## Installation

### 1. Install the package (Arch Linux)

```sh
git clone https://github.com/zukatooooo/Knober.git
cd Knober/packaging/arch
makepkg -si
```

This builds the version the PKGBUILD names (the `v0.1.0` tag) and installs:

| Path | What |
|---|---|
| `/usr/bin/knober` | The menu program (daemon) |
| `/usr/bin/knober-ctl` | Sends knob events to it |
| `/usr/share/doc/knober/config.example.toml` | Commented example config |
| `/usr/share/doc/knober/README.md` | This file |

Check it worked with `knober --version`. To remove it: `sudo pacman -R knober`.

### 2. Find out what your knob sends

Most knobs send `XF86AudioRaiseVolume` / `XF86AudioLowerVolume` when turned and
`XF86AudioMute` when pressed. To check, run `wev` (`sudo pacman -S wev`), then
turn and press the knob and note the key names.

### 3. Point Hyprland at knober

In `~/.config/hypr/hyprland.lua`, start knober at login:

```lua
hl.on("hyprland.start", function ()
  -- ... your other autostart programs ...
  hl.exec_cmd("knober")
end)
```

Bind the knob to `knober-ctl`. These replace the usual volume and mute binds:

```lua
hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("knober-ctl up"),    { locked = true, repeating = true })
hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("knober-ctl down"),  { locked = true, repeating = true })
hl.bind("XF86AudioMute",        hl.dsp.exec_cmd("knober-ctl press"), { locked = true })
```

If `wev` showed a different key name for the press, use it in the last line.
Then run `hyprctl reload`.

### 4. Create your config (optional)

```sh
mkdir -p ~/.config/knober
cp /usr/share/doc/knober/config.example.toml ~/.config/knober/config.toml
```

Without a config file, knober uses the built-in defaults, which are the same as
the example.

### 5. Start it now

The autostart line only runs at login. To start knober in the current session:

```sh
knober &
```

### Running without installing (development)

From a clone of the repository, `./knober` and `./knober-ctl` run the code in
`src/` directly, with no install needed. Use their full paths in the Hyprland
binds (for example `~/Knober/knober-ctl up`) to test changes before packaging
them. `python -m knober` also works when `src` is on `PYTHONPATH`.

### Releasing a new version

1. Bump `__version__` in `src/knober/__init__.py` and `pkgver` in
   `packaging/arch/PKGBUILD` (reset `pkgrel` to 1).
2. Commit, then tag and push: `git tag v0.2.0 && git push origin main v0.2.0`.
3. Rebuild and install: `cd packaging/arch && makepkg -si`.

---

## Using it

- **Press** the knob to open the menu. The centre **Close** circle is selected.
- **Turn** the knob to move along the nodes. Turning clockwise goes from the
  centre to the left-most node, then up the arc. The selection wraps around.
- **Press** again to run the selected node. The menu closes, unless the node is
  a page button.
- The name of the selected node appears under the pie.
- The menu closes by itself after `auto_hide_ms` with no knob input.

Mute is now a menu node, because pressing the knob opens the menu instead of
muting.

---

## Configuration

Settings live in **`~/.config/knober/config.toml`**. To use a different path,
set the `KNOBER_CONFIG` environment variable.

- **Every key is optional.** Keep only what you want to change; anything missing
  falls back to the built-in default.
- **No restart needed.** The file is re-read every time the menu opens, or when
  you run `knober-ctl reload`.
- **Mistakes don't break anything.** A file that can't be read (bad TOML, invalid
  `per_page`, a size of 0 or less) is rejected, and knober keeps its last working
  settings. Smaller problems (a font that isn't installed, an unknown weight or
  style) fall back to a default. Either way, a message is printed to knober's
  output (see [Troubleshooting](#troubleshooting)).

The file uses [TOML](https://toml.io). Text goes in quotes, `true`/`false` are
lowercase, and `#` starts a comment.

### Menu nodes (`[[items]]`)

Each node is one `[[items]]` block. Nodes appear on the arc in file order, from
left (first) to top (last).

```toml
[[items]]
icon  = "\U000F033E"   # Nerd Font glyph: paste it, or write its code
label = "Lock"         # shown under the pie when selected
cmd   = "hyprlock"     # what runs when you press
```

| Key | Required | Meaning |
|---|---|---|
| `label` | yes | Name shown under the pie |
| `icon` | no | One Nerd Font glyph. Without one, a `•` is shown. Find codes on the [Nerd Fonts cheat sheet](https://www.nerdfonts.com/cheat-sheet). For `nf-md-…` icons, write the code as `"\U000Fxxxx"`. |
| `cmd` | no | A shell command or a [built-in action](#built-in-actions). Empty or missing means pressing just closes the menu. |

`cmd` runs through the shell, so pipes, `$(...)` and `~` work. Commands run in
the background, and their output is discarded. For anything longer than one
line, write a script and put its path in `cmd`.

Some ideas:

| Action | `cmd` |
|---|---|
| Lock screen | `hyprlock` |
| Screenshot of an area | `grim -g "$(slurp)" ~/Pictures/shot-$(date +%s).png` |
| Open an app | `firefox` |
| Switch workspace | `hyprctl dispatch workspace 3` |
| Close the focused window | `hyprctl dispatch killactive` |
| Toggle floating | `hyprctl dispatch togglefloating` |
| Random wallpaper | `waypaper --random` |
| Switch audio output | `wpctl set-default <id>` (IDs from `wpctl status`) |

Inside TOML strings, write `"` as `\"`, as in the screenshot example in
`config.example.toml`.

### Built-in actions

Plain `playerctl play-pause` only controls the *first* player it finds. With
Spotify and a YouTube tab open at the same time, that is often the wrong one.
Use these in `cmd` instead:

| `cmd` | What it does |
|---|---|
| `@media play-pause` | If anything is playing, pause **everything** that is playing. If nothing is, resume exactly the players it paused last time (or the first player). |
| `@media next` | Skip forward on a player that **can** skip. Priority: playing, then last paused, then any. |
| `@media previous` | Same, backwards. |

The next/previous actions ask each player whether it can skip (the MPRIS
`CanGoNext` / `CanGoPrevious` properties), because a YouTube video can't, and
neither can Spotify while an ad is playing.

### Paging

The arc holds at most `per_page` nodes (default 6), **counting** the page
buttons. The centre circle is not counted. With more items than fit, they are
split into pages:

| Page | On the arc |
|---|---|
| First | Actions + **Next page** (no Previous) |
| Middle | **Previous page** + actions + **Next page** |
| Last | **Previous page** + remaining actions (no Next) |
| Everything fits | Just the actions, with no page buttons |

For example, 12 items with `per_page = 6`: page 1 has 5 items + Next, page 2 has
Previous + 4 items + Next, and page 3 has Previous + 3 items.

- **Previous page** is always the first node on the arc (left), and **Next page**
  the last (top).
- Pressing a page button keeps the menu open. After **Next**, the first action
  of the new page is selected. After **Previous**, the last action of that page
  is selected.
- When there are several pages, the label shows the page number, for example
  `TEST 10  3/3`.
- Every time the menu opens, it starts on page 1.

### All settings

Defaults are shown. They are identical to `config.example.toml`
(installed at `/usr/share/doc/knober/config.example.toml`).

#### `[general]`

| Key | Default | Meaning |
|---|---|---|
| `auto_hide_ms` | `6000` | Close the menu after this many milliseconds without knob input |
| `volume_step` | `5` | Volume change in % per knob click while the menu is closed |
| `volume_max` | `100` | Highest volume in % that turning up can reach. Above 100 allows boost. |

#### `[colors]`

By default, colours come from your **pywal** palette in Hyprland's `colors.lua`,
so the menu follows your wallpaper. The file is re-read on every redraw. Set a
role to `"#rrggbb"` to override it.

| Key | Default | Used for | pywal slot |
|---|---|---|---|
| `pywal_file` | `"~/.config/hypr/colors.lua"` | Where to read the palette from | |
| `background` | `""` | Node fill, background body | `color0` |
| `line` | `""` | Node outlines, background border | `color3` |
| `accent` | `""` | Selected node and its spoke | `color4` |
| `text` | `""` | Icons and label | `color7` |
| `dim` | `""` | Arc and unselected spokes | `color8` |

`""` means "take it from pywal". If the pywal file is missing, fixed fallback
colours are used.

#### `[fonts]`

| Key | Default | Meaning |
|---|---|---|
| `icon_family` | `"Symbols Nerd Font"` | Font for the icons. It must contain Nerd Font glyphs. |
| `icon_scale` | `0.85` | Icon size relative to the circle radius |
| `icon_weight` | `"normal"` | Icon weight |
| `label_family` | `"monospace"` | Font for the label under the pie |
| `label_size` | `10` | Label size in points |
| `label_weight` | `"bold"` | Label weight |
| `label_style` | `"normal"` | `normal`, `italic` or `oblique` |
| `label_uppercase` | `true` | Show the label in capitals |

- **Families:** any installed font. List them with `fc-list : family`. The
  generic names `monospace`, `sans`, `sans-serif` and `serif` also work.
- **Weights:** `thin`, `ultralight`, `light`, `semilight`, `book`, `normal`,
  `medium`, `semibold`, `bold`, `ultrabold`, `heavy`, `ultraheavy`, or any number
  from `100` to `1000`. Whether in-between weights look different depends on the
  font.

#### `[layout]`

All sizes are in pixels.

| Key | Default | Meaning |
|---|---|---|
| `show_background` | `false` | Draw a bordered quarter-circle behind the nodes |
| `show_label` | `true` | Show the selected node's name under the pie |
| `gap` | `2` | Space between the screen edge and the background border. Matches Hyprland `general.gaps_out`. |
| `border_width` | `2` | Background border width. Matches Hyprland `general.border_size`. |
| `line_width` | `2` | Width of the spokes, the arc and the circle outlines |
| `hub_inset` | `56` | Distance from the screen corner to the centre of the hub |
| `hub_radius` | `32` | Size of the centre circle |
| `arc_radius` | `180` | How far the nodes sit from the hub. Raise it if nodes overlap. |
| `item_radius` | `24` | Size of the node circles |
| `pie_padding` | `20` | Space between the furthest node and the background border |

The menu window resizes itself to fit these values.

#### `[paging]`

| Key | Default | Meaning |
|---|---|---|
| `per_page` | `6` | Max nodes on the arc per page, including page buttons. Whole number, minimum `3`. |
| `show_page_number` | `true` | Add `n/total` after the label when there are several pages |
| `prev_icon` | `"\U000F0141"` | Icon of the Previous page button (chevron left) |
| `prev_label` | `"Previous page"` | Label of the Previous page button |
| `next_icon` | `"\U000F0142"` | Icon of the Next page button (chevron right) |
| `next_label` | `"Next page"` | Label of the Next page button |

#### `[center]`

The circle in the middle. It is selected when the menu opens, and it is not
counted toward `per_page`.

| Key | Default | Meaning |
|---|---|---|
| `icon` | `"\U000F0156"` | Icon (a cross) |
| `label` | `"Close"` | Label |
| `cmd` | `""` | Command to run. `""` means just close the menu. |

---

## Commands

`knober-ctl <command>` sends one command to the running knober:

| Command | Effect |
|---|---|
| `up` | Menu closed: volume up. Menu open: select the next node. |
| `down` | Menu closed: volume down. Menu open: select the previous node. |
| `press` | Menu closed: open it. Menu open: run the selected node. |
| `close` | Close the menu |
| `reload` | Re-read the config file now |

Hyprland calls `up`, `down` and `press` for you. `close` and `reload` are for
scripts or for use by hand.

If knober isn't running, `knober-ctl` falls back to the plain behaviour:
`up`/`down` change the volume by 5% and `press` toggles mute. The knob never goes
dead.

---

## Troubleshooting

### See knober's messages

The autostarted copy has no visible output. To see messages (config errors,
font warnings, which player `@media next` picked), restart knober in a terminal:

```sh
pkill -f knober.daemon
knober
```

Only one copy can run at a time. If one is already running, a second one exits
immediately without a message. So if your code changes don't seem to take
effect, check that the old copy really stopped:

```sh
ps -eo pid,lstart,args | grep '[k]nober.daemon'
```

### Pressing the knob doesn't open the menu

- Check that knober is running (the command above).
- Check the key name with `wev`. If it isn't `XF86AudioMute`, change the third
  bind in `hyprland.lua`.
- Run `knober-ctl press` by hand. If the menu opens, the problem is the
  Hyprland bind, not knober.

### Next / Previous track does nothing

- Run knober in a terminal and use the node. It prints `next -> ['spotify']` or
  `no player can next`.
- `no player can next` means no open player supports skipping right now. Common
  causes: only a YouTube video is open, or Spotify is playing an ad.
- **Spotify free accounts** allow about 6 skips per hour and can't skip ads. Over
  the limit, Spotify silently ignores skips.
- **Previous** restarts the current song if it has played for more than a few
  seconds. That is the player's normal behaviour.

### Icons show as boxes or wrong symbols

`icon_family` doesn't have the glyphs. Install a Nerd Font
(`ttf-nerd-fonts-symbols`) or set `icon_family` to one you have, for example
`"JetBrainsMono Nerd Font"`.

### Nodes overlap

Raise `arc_radius`, lower `item_radius`, or lower `per_page` so fewer nodes share
the arc.

### Warnings about Vulkan or EGL when started in a terminal

`Failed to detect any valid GPUs` and `failed to create dri2 screen` come from
GTK's renderer. GTK falls back to software drawing, so the menu still works, and
you can ignore them.

---

## How it works

```
 knob ──▶ Hyprland bind ──▶ knober-ctl up|down|press ──UNIX socket──▶ knober ──▶ pie menu
                                     │                                     │
                                     └─ daemon not running:                ├─ wpctl (volume, mute)
                                        plain wpctl fallback               ├─ playerctl + busctl (@media)
                                                                           └─ any shell command
```

- **`knober`** (`src/knober/daemon.py`) is the background program. It is a GTK4 application whose window
  is a **layer-shell overlay** anchored to the bottom-right corner. Its keyboard
  mode is "none", so it never takes focus from your apps, and its input region is
  empty, so mouse clicks pass through it. The pie is drawn with cairo and the text
  with Pango.
- It listens on a UNIX datagram socket at `$XDG_RUNTIME_DIR/knober.sock` and acts
  on the commands it receives. The menu window is only hidden between uses, not
  destroyed, so opening it is instant.
- **`knober-ctl`** (`src/knober/ctl.py`) is a tiny client that sends one word to that socket. It is
  started on every knob click, so it imports nothing heavy.
- **LD_PRELOAD:** gtk4-layer-shell must load before `libwayland-client`, which
  Python can't guarantee. The daemon therefore finds the library by name and
  re-launches itself once (as `python -m knober.daemon`) with it in `LD_PRELOAD`.
  That is why the process shows up as `python3 -m knober.daemon`.

### Files

| File | Purpose |
|---|---|
| `src/knober/daemon.py` | The background program: menu, drawing, config, actions |
| `src/knober/ctl.py` | `knober-ctl`: the client called from the Hyprland binds |
| `src/knober/__init__.py` | Version number and socket path (shared by both) |
| `src/knober/config.example.toml` | Commented reference config with every setting |
| `knober`, `knober-ctl` | Launchers to run from a checkout without installing |
| `pyproject.toml` | Python package metadata and the two commands |
| `packaging/arch/PKGBUILD` | Arch Linux package recipe |
| `~/.config/knober/config.toml` | Your settings (not in this repo) |

### Code map (`src/knober/daemon.py`)

| Part | What it does |
|---|---|
| `DEFAULTS` | Every setting and its default value |
| `load_config()`, `_merge()` | Read the TOML file, merge it over the defaults, validate it |
| `paginate()` | Split the items into pages with Previous/Next buttons |
| `geometry()` | Work out the pie radius and window size from `[layout]` |
| `make_font()`, `check_fonts()` | Build Pango fonts and warn about bad font settings |
| `load_colors()` | Read pywal colours and apply overrides |
| `media()`, `_can()` | The `@media` actions: choose the right player |
| `run()` | Run a node's command |
| `Knober.handle()` | Knob logic: what `up`, `down`, `press`, `close` and `reload` do |
| `Knober.build_page()`, `turn_page()` | Which nodes are on screen; page turns |
| `Knober.positions()`, `draw()` | Where the circles go, and drawing them |

---

## Known limitations

- **Corner:** the menu is always in the bottom-right corner, fanning from left to
  up. The corner, the arc angles and the window margins are not yet configurable.
- **Rotation direction:** clockwise always moves forward through the nodes.
- **Fallback volume:** when knober isn't running, `knober-ctl` uses a fixed 5%
  step and 100% cap. It does not read `volume_step` / `volume_max` from the
  config.
- **Volume commands:** volume always uses `wpctl` on the default output.
- **One command per node:** there are no submenus, and no "dial" nodes that turn
  the knob into a brightness or seek control.
- **Drawing details:** a few values (spoke and arc transparency, background
  opacity, label offsets) are still fixed in `draw()`.

---

## License

knober is free software, licensed under the
[GNU General Public License v3.0 or later](LICENSE). You may use, study, share
and change it. If you share a changed version, it must stay under the same
licence, with its source code available.
