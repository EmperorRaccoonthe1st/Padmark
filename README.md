# padmark

A lightweight screen markup tool for Linux that maps a defined region on the screen 1:1 to the laptop trackpad in absolute coordinates.

Most on-screen annotation utilities rely on relative pointer movement across the full display. Drawing legible handwriting or small diagrams this way using a trackpad is difficult because trackpads accelerate and smooth pointer movements relative to the entire screen. `padmark` solves this by capturing raw multi-touch input directly from the hardware, locking the device while active so the system cursor does not move, and mapping finger strokes in real time to an anchored box on screen.

## How It Works

1. **Selection:** Triggering the application prompts the user to define an area using `slurp`. Dragging locks the selection box to the exact physical aspect ratio of the laptop trackpad. Alternatively, a single click anchors a default-sized box.
2. **Hardware Hijacking:** `padmark` opens the touchpad event node in `/dev/input/event*` and issues an `EVIOCGRAB` ioctl. This instructs the Linux input subsystem to route all touchpad events exclusively to the application. The system mouse pointer is frozen during drawing, while external mice and TrackPoints remain functional.
3. **Absolute Inking:** Physical trackpad boundaries (`ABS_X` / `ABS_Y`) are normalized and projected 1:1 onto the anchored box. Line strokes are drawn into a transparent GTK Layer Shell overlay with Cairo.
4. **Completion:** Pressing `Escape` releases the hardware grab immediately, restoring standard trackpad cursor control. In permanent mode, the overlay removes its input region, making the drawing click-through so the user can continue working with applications underneath.

## Requirements

### Runtime Dependencies
- Python 3.8+
- GTK 3 (`gtk3`)
- PyGObject (`python-gobject`)
- GtkLayerShell (`gtk-layer-shell`)
- Cairo (`python-cairo`)
- Slurp (`slurp`)

On Arch Linux:
```bash
sudo pacman -S python python-gobject gtk3 gtk-layer-shell slurp python-cairo
```

On Debian/Ubuntu:
```bash
sudo apt install python3 python3-gi python3-cairo libgtk-3-0 libgtk-layer-shell0 slurp
```

### Device Permissions
Reading raw hardware input from `/dev/input/` requires access to the `input` group. If not already a member:

```bash
sudo usermod -aG input $USER
```

Log out and back in for group membership changes to take effect.

## Usage

### Command-Line Interface

```bash
# Permanent markup (stays pinned after Esc, click-through)
./padmark.py --mode perm

# Permanent markup pinned to the current Sway workspace only
./padmark.py --mode perm --pin-workspace

# Temporary markup (vanishes immediately upon pressing Esc)
./padmark.py --mode temp

# Interactive deletion (displays crosshair to click and remove a single markup)
./padmark.py --delete

# Clear all active markups across all screens
./padmark.py --clear

# Custom initial box width and default color
./padmark.py --width 450 --color yellow
```

### In-Session Keyboard Shortcuts (While Drawing)

While drawing inside the box, `padmark` has exclusive keyboard grab. The following shortcuts can be pressed to adjust settings on the fly without interrupting the workflow:

| Key | Action | Description |
| :--- | :--- | :--- |
| `s` | Thin | Decreases line thickness step (down to 1.5px) |
| `d` | Thick | Increases line thickness step (up to 20.0px) |
| `a` | Highlighter | Toggles wide translucent highlighter mode (text remains readable underneath) |
| `u` | Undo | Removes the most recent stroke |
| `Shift + u` | Redo | Restores the most recently undone stroke |
| `1` - `6` | Palette | Swaps ink color (1: Cyan, 2: Yellow, 3: Red, 4: Green, 5: Magenta, 6: White) |
| `p` | Mode Toggle | Switches between Temporary mode (vanishes on Esc) and Permanent mode (pins on Esc) |
| `c` | Clear | Erases all strokes inside the current box without exiting |
| `Escape` | Finish | Releases trackpad grab and closes or pins according to active mode |

### Visual Feedback
- **Outline Glow:** The bounding box outline shifts color in real time to reflect the active pen color.
- **Outline Style:** Dashed outline indicates Temporary mode; solid outline indicates Permanent mode.
- **Status Indicator:** Changing a tool, color, or thickness triggers a brief, unobtrusive status badge in the top-left corner of the box that fades after one second.

## Window Manager Integration

### Sway (`~/.config/sway/config`)

```i3config
# Create a new markup box (drag to size, draw on trackpad, Esc to pin)
bindsym $mod+m exec /path/to/padmark.py --mode perm --pin-workspace

# Interactively click on an existing markup to delete it
bindsym $mod+Shift+m exec /path/to/padmark.py --delete

# Delete all active markups across all workspaces
bindsym $mod+Shift+Mod1+m exec /path/to/padmark.py --clear
```

### Hyprland (`~/.config/hypr/hyprland.conf`)

```ini
# Create new markup
bind = $mainMod, M, exec, /path/to/padmark.py --mode perm

# Delete clicked markup
bind = $mainMod SHIFT, M, exec, /path/to/padmark.py --delete

# Clear all markups
bind = $mainMod SHIFT ALT, M, exec, /path/to/padmark.py --clear
```

## Architecture

- **`padmark.py`**: The primary executable. Contains touchpad autodiscovery, evdev event parsing, Cairo rendering, GTK Layer Shell surface initialization, and Sway IPC socket integration for workspace tracking.
- **Touchpad Aspect Ratio Matching**: Physical axes ranges (`ABS_X` and `ABS_Y`) are queried via `ioctl(EVIOCGABS)`. The selection tool enforces this ratio so circular finger motions produce circular drawings on screen without elongation or skew.
- **Fail-Safe Device Release**: If the process receives a termination signal (`SIGINT`, `SIGTERM`) or terminates unexpectedly, the Linux kernel automatically closes the file descriptor and releases the `EVIOCGRAB` lock, ensuring the trackpad is never left frozen.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
