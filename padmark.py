#!/usr/bin/env python3
"""
padmark - Ultra-lightweight screen markup tool using trackpad 1:1 absolute input.
Designed for Linux (Wayland / Sway / wlroots and X11).
"""

import sys
import os
import glob
import fcntl
import struct
import argparse
import subprocess
import signal
import socket
import json
import time

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

# Try importing GtkLayerShell for native Wayland overlays (wlroots/Sway/Hyprland)
HAS_LAYER_SHELL = False
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except Exception:
    HAS_LAYER_SHELL = False

import cairo

# Linux input event constants
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

SYN_REPORT = 0x00
BTN_TOUCH = 0x14a  # 330
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_POSITION_X = 0x35  # 53
ABS_MT_POSITION_Y = 0x36  # 54

EVIOCGRAB = 0x40044590
EVIOCGNAME_256 = 0x82004506
EVIOCGABS_BASE = 0x80184540


def find_touchpad_device():
    """Scans /dev/input/event* to find the primary touchpad and its ABS ranges."""
    candidates = []
    for path in sorted(glob.glob('/dev/input/event*')):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                name_buf = bytearray(256)
                fcntl.ioctl(fd, EVIOCGNAME_256, name_buf)
                name = name_buf.split(b'\x00')[0].decode(errors='ignore')

                # Query ABS_X and ABS_Y bounds
                buf_x = bytearray(24)
                fcntl.ioctl(fd, EVIOCGABS_BASE + ABS_X, buf_x)
                _, min_x, max_x, _, _, _ = struct.unpack('iiiiii', buf_x)

                buf_y = bytearray(24)
                fcntl.ioctl(fd, EVIOCGABS_BASE + ABS_Y, buf_y)
                _, min_y, max_y, _, _, _ = struct.unpack('iiiiii', buf_y)

                is_touchpad = any(k in name.lower() for k in ['touchpad', 'synaptics', 'elan', 'glidepoint'])
                if is_touchpad and max_x > min_x and max_y > min_y:
                    candidates.append((path, name, min_x, max_x, min_y, max_y))
            finally:
                os.close(fd)
        except (OSError, PermissionError):
            continue

    if not candidates:
        return None
    # Return first matching touchpad
    return candidates[0]


def get_anchor_region_wayland(aspect_w, aspect_h):
    """Uses slurp with forced aspect ratio to let user click or click-and-drag out a proportional box."""
    try:
        proc = subprocess.run(
            [
                'slurp',
                '-a', f'{aspect_w}:{aspect_h}',
                '-d',
                '-b', '#00000000',
                '-c', '#3388ff',
                '-s', '#00000000',
                '-w', '1',
                '-f', '%x %y %w %h'
            ],
            capture_output=True, text=True, check=True
        )
        parts = proc.stdout.strip().split()
        if len(parts) == 4:
            return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


PID_DIR = os.environ.get('XDG_RUNTIME_DIR', '/tmp')


def get_active_overlays():
    """Returns list of running padmark permanent overlay metadata."""
    overlays = []
    pattern = os.path.join(PID_DIR, "padmark_perm_*.json")
    for f in glob.glob(pattern):
        try:
            with open(f, 'r') as fp:
                data = json.load(fp)
            pid = data.get('pid')
            # Test if process exists
            os.kill(pid, 0)
            data['meta_file'] = f
            overlays.append(data)
        except (OSError, ValueError, json.JSONDecodeError):
            # Clean up stale metadata file
            try:
                os.remove(f)
            except OSError:
                pass
    return overlays


def clear_running():
    """Terminates all running padmark permanent overlay processes."""
    overlays = get_active_overlays()
    for ov in overlays:
        pid = ov.get('pid')
        meta_file = ov.get('meta_file')
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if meta_file and os.path.exists(meta_file):
            try:
                os.remove(meta_file)
            except OSError:
                pass

    # Also clean up any lingering PID files
    for f in glob.glob(os.path.join(PID_DIR, "padmark_perm_*.pid")):
        try:
            with open(f, 'r') as fp:
                pid = int(fp.read().strip())
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            os.remove(f)
        except OSError:
            pass

    return len(overlays) > 0


def delete_clicked_markup():
    """Prompts user to click on an existing markup to delete it."""
    overlays = get_active_overlays()
    if not overlays:
        return

    # Use slurp with a distinct red/accent crosshair
    try:
        proc = subprocess.run(
            [
                'slurp',
                '-p',
                '-b', '#00000000',
                '-c', '#ff3344',
                '-w', '2',
                '-f', '%x %y'
            ],
            capture_output=True, text=True, check=True
        )
        parts = proc.stdout.strip().split()
        if len(parts) != 2:
            return
        click_x, click_y = int(parts[0]), int(parts[1])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return

    # Check which markup contains the click point
    PADDING = 20  # grace padding in pixels around the markup box
    target = None
    for ov in reversed(overlays):
        ox, oy, ow, oh = ov['x'], ov['y'], ov['w'], ov['h']
        if (ox - PADDING <= click_x <= ox + ow + PADDING and
            oy - PADDING <= click_y <= oy + oh + PADDING):
            target = ov
            break

    if target:
        pid = target.get('pid')
        meta_file = target.get('meta_file')
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if meta_file and os.path.exists(meta_file):
            try:
                os.remove(meta_file)
            except OSError:
                pass


class PadMarkApp:
    def __init__(self, mode='temp', box_width=360, anchor=None, pen_color=(0.2, 0.8, 1.0, 0.95), line_width=3.5, pin_workspace=False):
        self.mode = mode
        self.desired_box_width = box_width
        self.pen_color = pen_color
        self.line_width = line_width
        self.pin_workspace = pin_workspace
        self.pinned_workspace = None
        self.ipc_sock = None
        self.meta_file = None
        self.is_active = True

        # Locate hardware touchpad
        device_info = find_touchpad_device()
        if not device_info:
            print("Error: No accessible touchpad device found in /dev/input/event*.", file=sys.stderr)
            print("Check that you have read permissions (e.g. member of 'input' group).", file=sys.stderr)
            sys.exit(1)

        self.dev_path, self.dev_name, self.min_x, self.max_x, self.min_y, self.max_y = device_info
        self.range_x = self.max_x - self.min_x
        self.range_y = self.max_y - self.min_y
        self.aspect_ratio = self.range_y / self.range_x

        # Get anchor location and size (click-and-drag with locked aspect ratio)
        if anchor:
            if len(anchor) == 4:
                sel_x, sel_y, sel_w, sel_h = anchor
                self.box_w = sel_w
                self.box_h = sel_h
                self.anchor_x = sel_x
                self.anchor_y = sel_y
                is_dragged = True
            else:
                self.anchor_x, self.anchor_y = anchor[:2]
                self.box_w = self.desired_box_width
                self.box_h = int(self.box_w * self.aspect_ratio)
                is_dragged = False
        else:
            region = get_anchor_region_wayland(self.range_x, self.range_y)
            if region is None:
                # User pressed Esc or cancelled selection
                sys.exit(0)
            sel_x, sel_y, sel_w, sel_h = region
            if sel_w >= 15 and sel_h >= 15:
                # User dragged out a box
                self.anchor_x = sel_x
                self.anchor_y = sel_y
                self.box_w = sel_w
                self.box_h = sel_h
                is_dragged = True
            else:
                # Single click fallback: use default box width centered on click
                self.anchor_x = sel_x
                self.anchor_y = sel_y
                self.box_w = self.desired_box_width
                self.box_h = int(self.box_w * self.aspect_ratio)
                is_dragged = False

        # Determine target monitor and convert global coordinates to monitor-local
        center_x = self.anchor_x + self.box_w // 2 if is_dragged else self.anchor_x
        center_y = self.anchor_y + self.box_h // 2 if is_dragged else self.anchor_y

        display = Gdk.Display.get_default()
        self.target_monitor = display.get_monitor_at_point(center_x, center_y) if display else None
        if self.target_monitor:
            geom = self.target_monitor.get_geometry()
            offset_x, offset_y = geom.x, geom.y
        else:
            offset_x, offset_y = 0, 0

        if is_dragged:
            # Box position anchored directly at dragged top-left relative to monitor
            self.box_x = max(0, self.anchor_x - offset_x)
            self.box_y = max(0, self.anchor_y - offset_y)
        else:
            # Centered on single click point relative to monitor
            self.box_x = max(10, (self.anchor_x - offset_x) - self.box_w // 2)
            self.box_y = max(10, (self.anchor_y - offset_y) - self.box_h // 2)

        # In permanent mode, save overlay bounding box metadata for interactive deletion
        if self.mode == 'perm':
            self.meta_file = os.path.join(PID_DIR, f"padmark_perm_{os.getpid()}.json")
            meta = {
                "pid": os.getpid(),
                "x": offset_x + self.box_x,
                "y": offset_y + self.box_y,
                "w": self.box_w,
                "h": self.box_h
            }
            try:
                with open(self.meta_file, 'w') as fp:
                    json.dump(meta, fp)
            except OSError:
                self.meta_file = None
        else:
            self.meta_file = None

        # Open and grab touchpad exclusively
        try:
            self.touchpad_fd = os.open(self.dev_path, os.O_RDONLY | os.O_NONBLOCK)
            fcntl.ioctl(self.touchpad_fd, EVIOCGRAB, 1)
        except PermissionError:
            print(f"Error: Permission denied opening {self.dev_path}. Run 'sudo usermod -aG input $USER'.", file=sys.stderr)
            sys.exit(1)
        except OSError as e:
            print(f"Error grabbing {self.dev_path}: {e}", file=sys.stderr)
            sys.exit(1)

        # Color palette presets
        self.PALETTE = {
            '1': ((0.0, 0.9, 1.0), "Cyan"),
            '2': ((1.0, 0.85, 0.1), "Yellow"),
            '3': ((1.0, 0.25, 0.25), "Red"),
            '4': ((0.2, 0.95, 0.35), "Green"),
            '5': ((1.0, 0.3, 0.8), "Magenta"),
            '6': ((1.0, 1.0, 1.0), "White"),
        }
        # Discrete thickness steps for pen (px)
        self.THICKNESS_LEVELS = [1.5, 2.5, 4.0, 7.0, 12.0, 20.0]
        self.thickness_idx = 2  # default 4.0px

        # Active pen state
        self.current_base_color = pen_color[:3] if isinstance(pen_color, tuple) else (0.0, 0.9, 1.0)
        self.current_color_name = "Cyan"
        self.is_highlighter = False

        # Toast / OSD badge state
        self.toast_text = None
        self.toast_time = 0.0

        # Drawing state
        self.strokes = []        # List of completed stroke dicts
        self.undone_strokes = [] # Redo stack
        self.current_stroke = [] # Points in current active stroke
        self.pen_down = False
        self.curr_norm_x = 0.0
        self.curr_norm_y = 0.0

        # Setup GTK Window
        self.setup_ui()

        # Sway workspace pinning (if requested)
        if self.pin_workspace:
            self.init_sway_workspace_tracking()

        # Watch touchpad input via GLib main loop
        self.watch_id = GLib.io_add_watch(self.touchpad_fd, GLib.IO_IN, self.on_touchpad_events)

        # Clean shutdown on signals
        signal.signal(signal.SIGINT, self.cleanup_and_quit)
        signal.signal(signal.SIGTERM, self.cleanup_and_quit)

    def setup_ui(self):
        self.window = Gtk.Window(Gtk.WindowType.TOPLEVEL)
        self.window.set_title("PadMark Overlay")
        self.window.set_decorated(False)
        self.window.set_app_paintable(True)

        # Transparent visual
        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.window.set_visual(visual)

        if HAS_LAYER_SHELL and os.environ.get('WAYLAND_DISPLAY'):
            GtkLayerShell.init_for_window(self.window)
            if self.target_monitor:
                GtkLayerShell.set_monitor(self.window, self.target_monitor)
            GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.TOP, True)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.RIGHT, True)
            GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        else:
            # X11 fallback: fullscreen transparent window
            self.window.fullscreen()
            self.window.set_keep_above(True)

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.connect("draw", self.on_draw)
        self.window.add(self.drawing_area)

        self.window.connect("key-press-event", self.on_key_press)
        self.window.connect("destroy", self.cleanup_and_quit)

        self.window.show_all()

    def init_sway_workspace_tracking(self):
        swaysock = os.environ.get('SWAYSOCK')
        if not swaysock or not os.path.exists(swaysock):
            return

        try:
            # Query currently focused workspace
            s_query = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s_query.connect(swaysock)
            s_query.sendall(b'i3-ipc' + struct.pack('=II', 0, 1))
            header = s_query.recv(14)
            _, length, _ = struct.unpack('=6sII', header)
            data = json.loads(s_query.recv(length))
            s_query.close()

            focused = [ws['name'] for ws in data if ws.get('focused')]
            if not focused:
                return
            self.pinned_workspace = focused[0]

            # Subscribe to workspace focus events
            self.ipc_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.ipc_sock.connect(swaysock)
            self.ipc_sock.setblocking(False)

            payload = json.dumps(['workspace']).encode()
            msg = b'i3-ipc' + struct.pack('=II', len(payload), 2) + payload
            self.ipc_sock.sendall(msg)

            GLib.io_add_watch(self.ipc_sock.fileno(), GLib.IO_IN, self.on_sway_ipc_event)
        except Exception:
            pass

    def on_sway_ipc_event(self, source, condition):
        try:
            header = self.ipc_sock.recv(14)
            if len(header) < 14:
                return True
            _, length, _ = struct.unpack('=6sII', header)
            data = json.loads(self.ipc_sock.recv(length))
            if data.get('change') == 'focus':
                curr_name = data.get('current', {}).get('name')
                if curr_name == self.pinned_workspace:
                    self.window.show()
                else:
                    self.window.hide()
        except Exception:
            pass
        return True

    def show_toast(self, text):
        self.toast_text = text
        self.toast_time = time.time()
        self.drawing_area.queue_draw()
        GLib.timeout_add(30, self.check_toast_refresh)

    def check_toast_refresh(self):
        if not self.is_active or not self.toast_text:
            return False
        dt = time.time() - self.toast_time
        if dt < 1.2:
            self.drawing_area.queue_draw()
            return True
        else:
            self.toast_text = None
            self.drawing_area.queue_draw()
            return False

    def on_key_press(self, widget, event):
        keyval = event.keyval
        state = event.state

        # Esc: Finish & ungrab
        if keyval == Gdk.KEY_Escape:
            self.deactivate()
            return True

        # u / U: Undo / Redo
        if keyval in (Gdk.KEY_u, Gdk.KEY_U):
            is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK) or keyval == Gdk.KEY_U
            if is_shift:
                # Redo
                if self.undone_strokes:
                    restored = self.undone_strokes.pop()
                    self.strokes.append(restored)
                    self.show_toast("Redo")
                else:
                    self.show_toast("Nothing to Redo")
            else:
                # Undo
                if self.strokes:
                    popped = self.strokes.pop()
                    self.undone_strokes.append(popped)
                    self.show_toast("Undo")
                else:
                    self.show_toast("Nothing to Undo")
            return True

        # s / S: Thin (decrease thickness)
        if keyval in (Gdk.KEY_s, Gdk.KEY_S):
            if self.thickness_idx > 0:
                self.thickness_idx -= 1
            w = self.THICKNESS_LEVELS[self.thickness_idx]
            self.show_toast(f"Thin [{w:.1f}px]")
            return True

        # d / D: Thick (increase thickness)
        if keyval in (Gdk.KEY_d, Gdk.KEY_D):
            if self.thickness_idx < len(self.THICKNESS_LEVELS) - 1:
                self.thickness_idx += 1
            w = self.THICKNESS_LEVELS[self.thickness_idx]
            self.show_toast(f"Thick [{w:.1f}px]")
            return True

        # a / A: Highlighter toggle
        if keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.is_highlighter = not self.is_highlighter
            label = "Highlighter [ON]" if self.is_highlighter else "Pen [ON]"
            self.show_toast(label)
            return True

        # 1 - 6: Color presets
        key_char = chr(Gdk.keyval_to_unicode(keyval)) if keyval < 0x10000 else ''
        if key_char in self.PALETTE:
            color, name = self.PALETTE[key_char]
            self.current_base_color = color
            self.current_color_name = name
            self.show_toast(f"Color: {name}")
            return True

        # p / P: Toggle mode (temporary <-> permanent)
        if keyval in (Gdk.KEY_p, Gdk.KEY_P):
            self.mode = 'temp' if self.mode == 'perm' else 'perm'
            mode_desc = "Permanent (Pins on Esc)" if self.mode == 'perm' else "Temporary (Closes on Esc)"
            self.show_toast(f"Mode: {mode_desc}")
            return True

        # c / C: Clear current canvas
        if keyval in (Gdk.KEY_c, Gdk.KEY_C):
            if self.strokes:
                self.undone_strokes.extend(self.strokes)
                self.strokes.clear()
                self.show_toast("Canvas Cleared")
            return True

        return False

    def deactivate(self):
        """Called when Esc is pressed."""
        if not self.is_active:
            return

        # Release touchpad grab immediately
        self.ungrab_touchpad()
        self.is_active = False

        if self.mode == 'temp':
            # Temporary mode: immediately close and dismiss
            Gtk.main_quit()
        else:
            # Permanent mode: make window click-through and remove keyboard grab
            if HAS_LAYER_SHELL and os.environ.get('WAYLAND_DISPLAY'):
                GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)
            
            # Make the entire window pass input directly to underlying apps
            gdk_win = self.window.get_window()
            if gdk_win:
                gdk_win.input_shape_combine_region(cairo.Region(), 0, 0)
            
            # Redraw to remove the boundary box while keeping the drawn strokes
            self.drawing_area.queue_draw()

    def ungrab_touchpad(self):
        if hasattr(self, 'touchpad_fd') and self.touchpad_fd >= 0:
            try:
                fcntl.ioctl(self.touchpad_fd, EVIOCGRAB, 0)
            except OSError:
                pass
            try:
                os.close(self.touchpad_fd)
            except OSError:
                pass
            self.touchpad_fd = -1

    def cleanup_and_quit(self, *args):
        self.ungrab_touchpad()
        if hasattr(self, 'ipc_sock') and self.ipc_sock:
            try:
                self.ipc_sock.close()
            except OSError:
                pass
            self.ipc_sock = None
        if hasattr(self, 'meta_file') and self.meta_file and os.path.exists(self.meta_file):
            try:
                os.remove(self.meta_file)
            except OSError:
                pass
        Gtk.main_quit()

    def on_touchpad_events(self, source, condition):
        if not self.is_active or self.touchpad_fd < 0:
            return False

        # Read available 24-byte input_event packets
        EVENT_SIZE = 24
        try:
            raw_data = os.read(self.touchpad_fd, EVENT_SIZE * 64)
        except BlockingIOError:
            return True
        except OSError:
            return False

        num_events = len(raw_data) // EVENT_SIZE
        need_redraw = False

        for i in range(num_events):
            chunk = raw_data[i * EVENT_SIZE:(i + 1) * EVENT_SIZE]
            tv_sec, tv_usec, ev_type, ev_code, ev_val = struct.unpack('qqHHi', chunk)

            if ev_type == EV_ABS:
                if ev_code in (ABS_X, ABS_MT_POSITION_X):
                    self.curr_norm_x = max(0.0, min(1.0, (ev_val - self.min_x) / self.range_x))
                elif ev_code in (ABS_Y, ABS_MT_POSITION_Y):
                    self.curr_norm_y = max(0.0, min(1.0, (ev_val - self.min_y) / self.range_y))

            elif ev_type == EV_KEY and ev_code == BTN_TOUCH:
                if ev_val == 1:
                    # Pen Down: start new stroke
                    self.pen_down = True
                    px = self.box_x + self.curr_norm_x * self.box_w
                    py = self.box_y + self.curr_norm_y * self.box_h
                    self.current_stroke = [(px, py)]
                    need_redraw = True
                elif ev_val == 0:
                    # Pen Up: finalize stroke
                    self.pen_down = False
                    if len(self.current_stroke) > 1:
                        eff_alpha = 0.35 if self.is_highlighter else 0.95
                        eff_width = self.THICKNESS_LEVELS[self.thickness_idx] * (3.5 if self.is_highlighter else 1.0)
                        self.strokes.append({
                            'points': list(self.current_stroke),
                            'color': (*self.current_base_color, eff_alpha),
                            'width': eff_width,
                            'is_highlighter': self.is_highlighter
                        })
                        self.undone_strokes.clear()
                    self.current_stroke = []
                    need_redraw = True

            elif ev_type == EV_SYN and ev_code == SYN_REPORT:
                if self.pen_down:
                    px = self.box_x + self.curr_norm_x * self.box_w
                    py = self.box_y + self.curr_norm_y * self.box_h
                    if not self.current_stroke or (px, py) != self.current_stroke[-1]:
                        self.current_stroke.append((px, py))
                        need_redraw = True

        if need_redraw:
            self.drawing_area.queue_draw()

        return True

    def on_draw(self, widget, cr):
        # Clear background to transparent
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Draw anchor box indicator if actively taking input
        if self.is_active:
            # Anchor border (outline only, matches active ink color!)
            cr.set_source_rgba(*self.current_base_color, 0.85)
            cr.set_line_width(1.5)
            if self.mode == 'temp':
                cr.set_dash([4.0, 4.0], 0.0)  # Dashed for temporary
            else:
                cr.set_dash([], 0.0)           # Solid for permanent
            cr.rectangle(self.box_x, self.box_y, self.box_w, self.box_h)
            cr.stroke()
            cr.set_dash([], 0.0)

        # Render all completed strokes
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        for stroke in self.strokes:
            pts = stroke['points']
            if len(pts) < 2:
                continue
            cr.set_source_rgba(*stroke['color'])
            cr.set_line_width(stroke['width'])
            cr.move_to(pts[0][0], pts[0][1])
            for pt in pts[1:]:
                cr.line_to(pt[0], pt[1])
            cr.stroke()

        # Render current active stroke
        if self.current_stroke and len(self.current_stroke) > 1:
            eff_alpha = 0.35 if self.is_highlighter else 0.95
            eff_width = self.THICKNESS_LEVELS[self.thickness_idx] * (3.5 if self.is_highlighter else 1.0)
            cr.set_source_rgba(*self.current_base_color, eff_alpha)
            cr.set_line_width(eff_width)
            cr.move_to(self.current_stroke[0][0], self.current_stroke[0][1])
            for pt in self.current_stroke[1:]:
                cr.line_to(pt[0], pt[1])
            cr.stroke()

        # Draw toast badge in top-left of box if recently triggered
        if self.is_active and self.toast_text:
            dt = time.time() - self.toast_time
            if dt < 1.2:
                toast_alpha = 1.0 if dt < 0.8 else max(0.0, 1.0 - (dt - 0.8) / 0.4)
                cr.save()
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(11.0)
                ext = cr.text_extents(self.toast_text)

                pw = ext.width + 16
                ph = ext.height + 10
                px = self.box_x + 8
                py = self.box_y + 8

                # Background pill
                cr.set_source_rgba(0.08, 0.08, 0.12, 0.88 * toast_alpha)
                rad = 4.0
                cr.new_sub_path()
                cr.arc(px + pw - rad, py + rad, rad, -1.5707963, 0.0)
                cr.arc(px + pw - rad, py + ph - rad, rad, 0.0, 1.5707963)
                cr.arc(px + rad, py + ph - rad, rad, 1.5707963, 3.1415926)
                cr.arc(px + rad, py + rad, rad, 3.1415926, 4.7123889)
                cr.close_path()
                cr.fill()

                # Border pill in active color
                cr.set_source_rgba(*self.current_base_color, 0.9 * toast_alpha)
                cr.set_line_width(1.0)
                cr.stroke()

                # Text
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.95 * toast_alpha)
                cr.move_to(px + 8, py + ph - 4)
                cr.show_text(self.toast_text)
                cr.restore()
            else:
                self.toast_text = None

        return False


def main():
    parser = argparse.ArgumentParser(
        description="padmark: Extremely lightweight trackpad screen markup tool."
    )
    parser.add_argument(
        "--mode", choices=["temp", "perm"], default="temp",
        help="Markup mode: 'temp' (closes on Esc) or 'perm' (leaves markup visible on screen after Esc)."
    )
    parser.add_argument(
        "--width", type=int, default=360,
        help="Width in pixels of the anchored markup box (height is auto-computed from trackpad aspect ratio)."
    )
    parser.add_argument(
        "--color", default="cyan",
        choices=["cyan", "yellow", "red", "green", "white"],
        help="Pen color (default: cyan)."
    )
    parser.add_argument(
        "--toggle", action="store_true",
        help="Toggle mode: if a permanent markup is currently on screen, dismiss it; otherwise start a new markup."
    )
    parser.add_argument(
        "--delete", action="store_true",
        help="Interactive delete: click an existing markup box to delete only that one."
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="Dismiss all active permanent markup overlays."
    )
    parser.add_argument(
        "--pin-workspace", action="store_true",
        help="Pin permanent markup to the current Sway workspace only (auto-hides on other workspaces)."
    )
    args = parser.parse_args()

    if args.delete:
        # Interactively click on an existing markup to delete it
        delete_clicked_markup()
        sys.exit(0)

    if args.clear:
        # Terminate any running permanent overlays
        cleared = clear_running()
        if not cleared:
            subprocess.run(["pkill", "-f", "padmark.py.*--mode.*perm"])
        sys.exit(0)

    if args.toggle:
        # If permanent markup is already active, dismiss it (toggle off)
        if clear_running():
            sys.exit(0)
        # Otherwise, proceed to launch new markup (toggle on)

    colors = {
        "cyan": (0.0, 0.9, 1.0, 0.95),
        "yellow": (1.0, 0.85, 0.1, 0.95),
        "red": (1.0, 0.25, 0.25, 0.95),
        "green": (0.2, 0.95, 0.35, 0.95),
        "white": (1.0, 1.0, 1.0, 0.95),
    }

    app = PadMarkApp(
        mode=args.mode,
        box_width=args.width,
        pen_color=colors.get(args.color, colors["cyan"]),
        pin_workspace=args.pin_workspace
    )
    Gtk.main()


if __name__ == '__main__':
    main()
