#!/usr/bin/env python3
"""Promodor — a floating Pomodoro clock for the Linux desktop.

A small borderless widget that can float above all windows, sit on the
desktop below them, or behave like a normal window. An optional to-do list
can be enabled from Settings.
"""
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid

# GNOME on Wayland does not let clients keep themselves above/below other
# windows or refuse focus. Running through XWayland restores those hints.
# Some sessions export GDK_BACKEND=wayland, so override it unless the user
# explicitly opts into native Wayland with PROMODOR_NATIVE_WAYLAND=1.
if os.environ.get("PROMODOR_NATIVE_WAYLAND") != "1":
    os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

APP_ID = "dev.prateek.Promodor"
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "promodor"
)
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
TODOS_FILE = os.path.join(CONFIG_DIR, "todos.json")

DEFAULTS = {
    "focus_min": 25,
    "short_min": 5,
    "long_min": 15,
    "long_every": 4,
    "auto_start_breaks": True,
    "auto_start_focus": False,
    "layer": "above",  # above | normal | below
    "all_workspaces": True,
    "show_in_taskbar": False,
    "todo_enabled": False,
    "todo_visible": True,
    "sound": True,
    "notify": True,
    "opacity": 0.90,
    "x": None,
    "y": None,
}

FOCUS, SHORT, LONG = "focus", "short", "long"
PHASES = {
    FOCUS: {"label": "FOCUS", "key": "focus_min", "color": (1.00, 0.42, 0.36)},
    SHORT: {"label": "SHORT BREAK", "key": "short_min", "color": (0.30, 0.79, 0.63)},
    LONG: {"label": "LONG BREAK", "key": "long_min", "color": (0.36, 0.61, 1.00)},
}
LAYERS = [("above", "Always on top"), ("normal", "Normal window"), ("below", "On desktop (below windows)")]

CSS = b"""
#root { background: transparent; color: #f2f2f5; }
#root button {
    background: transparent; border: none; box-shadow: none;
    color: #e8e8ee; min-width: 26px; min-height: 26px; padding: 3px;
    border-radius: 999px; -gtk-icon-shadow: none;
}
#root button:hover { background: rgba(255,255,255,0.12); }
#root button:active { background: rgba(255,255,255,0.20); }
#root button.primary { background: rgba(255,255,255,0.14); min-width: 34px; min-height: 34px; }
#root button.primary:hover { background: rgba(255,255,255,0.24); }
#root button.toggled { color: #ffb4a9; }
#root entry {
    background: rgba(255,255,255,0.08); color: #f2f2f5; border: none;
    border-radius: 8px; box-shadow: none; padding: 4px 8px; caret-color: #f2f2f5;
}
#root list, #root row, #root scrolledwindow, #root viewport { background: transparent; }
#root row { padding: 1px 2px; border-radius: 6px; }
#root row:hover { background: rgba(255,255,255,0.06); }
#root row.active { background: rgba(255,107,91,0.18); }
#root checkbutton { padding: 4px; }
#root check { background: rgba(255,255,255,0.12); border: none; border-radius: 4px; min-width: 16px; min-height: 16px; }
#root check:checked { background: #ff6b5b; color: white; }
#root .dim { color: rgba(242,242,245,0.55); font-size: 9pt; }
#root .task { font-size: 10pt; }
#root .done { color: rgba(242,242,245,0.40); }
#root .current { color: rgba(242,242,245,0.80); font-size: 9pt; }
#root separator { background: rgba(255,255,255,0.10); min-height: 1px; }
"""


# --------------------------------------------------------------------------- storage

def load_json(path, fallback):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def save_json(path, data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- side effects

def play_sound():
    for path in (
        "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
        "/usr/share/sounds/Yaru/stereo/complete.oga",
    ):
        if not os.path.exists(path):
            continue
        for player in ("pw-play", "paplay", "canberra-gtk-play"):
            exe = shutil.which(player)
            if exe:
                args = [exe, "-f", path] if player == "canberra-gtk-play" else [exe, path]
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    Gdk.beep()


def send_notification(title, body):
    exe = shutil.which("notify-send")
    if exe:
        subprocess.Popen(
            [exe, "-a", "Promodor", "-i", "alarm-symbolic", title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def icon_button(icon, tooltip, callback, primary=False):
    btn = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    btn.set_tooltip_text(tooltip)
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_can_focus(False)
    if primary:
        btn.get_style_context().add_class("primary")
    btn.connect("clicked", lambda *_: callback())
    return btn


# --------------------------------------------------------------------------- to-do panel

class TodoPanel(Gtk.Box):
    def __init__(self, app_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.win = app_window
        data = load_json(TODOS_FILE, {"items": [], "active": None})
        self.items = data.get("items", [])
        self.active = data.get("active")

        self.pack_start(Gtk.Separator(), False, False, 2)

        self.entry = Gtk.Entry(placeholder_text="Add a task and press Enter…")
        self.entry.connect("activate", self.on_add)
        self.entry.connect("button-press-event", self.win.on_entry_press)
        self.entry.connect("focus-out-event", lambda *_: self.win.release_focus())
        self.pack_start(self.entry, False, False, 0)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(220)
        scroller.add(self.listbox)
        self.pack_start(scroller, True, True, 0)

        footer = Gtk.Box(spacing=4)
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.get_style_context().add_class("dim")
        footer.pack_start(self.count_label, True, True, 0)
        clear = Gtk.Button(label="Clear done", relief=Gtk.ReliefStyle.NONE, can_focus=False)
        clear.get_style_context().add_class("dim")
        clear.connect("clicked", self.on_clear_done)
        footer.pack_end(clear, False, False, 0)
        self.pack_start(footer, False, False, 0)

        self.rebuild()

    # persistence -------------------------------------------------------------
    def save(self):
        save_json(TODOS_FILE, {"items": self.items, "active": self.active})

    def active_item(self):
        return next((i for i in self.items if i["id"] == self.active), None)

    def credit_pomodoro(self):
        item = self.active_item()
        if item:
            item["pomodoros"] = item.get("pomodoros", 0) + 1
            self.save()
            self.rebuild()

    # ui ----------------------------------------------------------------------
    def rebuild(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        for item in self.items:
            self.listbox.add(self.make_row(item))
        self.listbox.show_all()
        left = sum(1 for i in self.items if not i["done"])
        self.count_label.set_text(f"{left} left · click a task to focus on it" if self.items else "No tasks yet")
        self.win.update_current_task()

    def make_row(self, item):
        row = Gtk.ListBoxRow(activatable=False)
        if item["id"] == self.active:
            row.get_style_context().add_class("active")
        box = Gtk.Box(spacing=6)

        check = Gtk.CheckButton(active=item["done"], can_focus=False)
        check.connect("toggled", self.on_toggle, item)
        box.pack_start(check, False, False, 0)

        label = Gtk.Label(label=item["text"], xalign=0, ellipsize=Pango.EllipsizeMode.END)
        label.set_tooltip_text(item["text"])
        ctx = label.get_style_context()
        ctx.add_class("task")
        if item["done"]:
            ctx.add_class("done")
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_strikethrough_new(True))
            label.set_attributes(attrs)
        click = Gtk.EventBox()
        click.add(label)
        click.connect("button-press-event", self.on_pick, item)
        box.pack_start(click, True, True, 0)

        if item.get("pomodoros"):
            count = Gtk.Label(label=f"🍅{item['pomodoros']}")
            count.get_style_context().add_class("dim")
            box.pack_start(count, False, False, 0)

        delete = icon_button("window-close-symbolic", "Delete task", lambda: self.on_delete(item))
        box.pack_end(delete, False, False, 0)
        row.add(box)
        return row

    # handlers ----------------------------------------------------------------
    def on_add(self, entry):
        text = entry.get_text().strip()
        if not text:
            return
        item = {"id": uuid.uuid4().hex, "text": text, "done": False, "pomodoros": 0}
        self.items.append(item)
        if self.active is None:
            self.active = item["id"]
        entry.set_text("")
        self.save()
        self.rebuild()

    def on_toggle(self, check, item):
        item["done"] = check.get_active()
        if item["done"] and self.active == item["id"]:
            nxt = next((i for i in self.items if not i["done"]), None)
            self.active = nxt["id"] if nxt else None
        self.save()
        GLib.idle_add(self.rebuild)

    def on_pick(self, _w, event, item):
        if event.button != 1 or item["done"]:
            return False
        self.active = None if self.active == item["id"] else item["id"]
        self.save()
        self.rebuild()
        return True

    def on_delete(self, item):
        self.items = [i for i in self.items if i["id"] != item["id"]]
        if self.active == item["id"]:
            self.active = None
        self.save()
        GLib.idle_add(self.rebuild)

    def on_clear_done(self, *_):
        self.items = [i for i in self.items if not i["done"]]
        self.save()
        self.rebuild()


# --------------------------------------------------------------------------- settings

class SettingsDialog(Gtk.Dialog):
    def __init__(self, win):
        super().__init__(title="Promodor Settings", transient_for=win, modal=False)
        self.win = win
        self.set_keep_above(True)
        self.set_default_size(360, -1)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda d, _r: d.destroy())

        s = win.settings
        grid = Gtk.Grid(column_spacing=16, row_spacing=10, margin=18)
        self.get_content_area().add(grid)
        row = 0

        def section(title):
            nonlocal row
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(f"<b>{title}</b>")
            grid.attach(lbl, 0, row, 2, 1)
            row += 1

        def add(label, widget):
            nonlocal row
            grid.attach(Gtk.Label(label=label, xalign=0, hexpand=True), 0, row, 1, 1)
            widget.set_halign(Gtk.Align.END)
            grid.attach(widget, 1, row, 1, 1)
            row += 1

        def spin(key, lo, hi):
            sp = Gtk.SpinButton.new_with_range(lo, hi, 1)
            sp.set_value(s[key])
            sp.connect("value-changed", lambda w: self.set(key, int(w.get_value()), timers=True))
            return sp

        def switch(key, apply_window=False):
            sw = Gtk.Switch(active=s[key], valign=Gtk.Align.CENTER)
            sw.connect("notify::active", lambda w, _p: self.set(key, w.get_active(), window=apply_window))
            return sw

        section("Timer")
        add("Focus (minutes)", spin("focus_min", 1, 180))
        add("Short break (minutes)", spin("short_min", 1, 60))
        add("Long break (minutes)", spin("long_min", 1, 120))
        add("Long break after N focus sessions", spin("long_every", 2, 12))
        add("Auto-start breaks", switch("auto_start_breaks"))
        add("Auto-start focus after break", switch("auto_start_focus"))

        section("Window")
        layer = Gtk.ComboBoxText()
        for key, text in LAYERS:
            layer.append(key, text)
        layer.set_active_id(s["layer"])
        layer.connect("changed", lambda w: self.set("layer", w.get_active_id(), window=True))
        add("Position", layer)
        add("Show on all workspaces", switch("all_workspaces", apply_window=True))
        add("Show in taskbar / dock", switch("show_in_taskbar", apply_window=True))
        opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.3, 1.0, 0.05)
        opacity.set_value(s["opacity"])
        opacity.set_size_request(140, -1)
        opacity.set_draw_value(False)
        opacity.connect("value-changed", lambda w: self.set("opacity", round(w.get_value(), 2), window=True))
        add("Background opacity", opacity)

        section("Features")
        add("Enable to-do list", switch("todo_enabled", apply_window=True))
        add("Sound when a session ends", switch("sound"))
        add("Desktop notification", switch("notify"))

        self.show_all()

    def set(self, key, value, timers=False, window=False):
        self.win.settings[key] = value
        self.win.save_settings()
        if timers:
            self.win.on_durations_changed()
        if window:
            self.win.apply_window_settings()


# --------------------------------------------------------------------------- main window

class PomodoroWindow(Gtk.ApplicationWindow):
    CLOCK_SIZE = 190

    def __init__(self, app):
        super().__init__(application=app, title="Promodor")
        self.settings = {**DEFAULTS, **load_json(SETTINGS_FILE, {})}
        self.phase = FOCUS
        self.completed = 0
        self.running = False
        self.remaining = self.phase_seconds(FOCUS)
        self.end_time = None
        self._save_pos_id = None
        self.settings_dialog = None
        self.todo = None

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        # Never take keyboard focus on its own (window switches, workspace
        # changes, mapping); it is only borrowed while typing a task.
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self._press = None  # (x_root, y_root, button, time) of a press that may become a drag
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
        )
        self.connect("draw", self.on_draw_bg)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", lambda *_: setattr(self, "_press", None))
        self.connect("motion-notify-event", self.on_motion)
        self.connect("key-press-event", self.on_key)
        self.connect("focus-out-event", lambda *_: self.release_focus())
        self.connect("configure-event", self.on_configure)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=12, name="root")
        self.root.set_size_request(230, -1)
        self.add(self.root)

        # top bar: pin + todo + settings + close
        top = Gtk.Box(spacing=0)
        self.pin_btn = icon_button("view-pin-symbolic", "Toggle always on top", self.toggle_pin)
        top.pack_start(self.pin_btn, False, False, 0)
        top.pack_end(icon_button("window-close-symbolic", "Quit Promodor", self.quit), False, False, 0)
        top.pack_end(icon_button("emblem-system-symbolic", "Settings", self.open_settings), False, False, 0)
        self.todo_btn = icon_button("view-list-symbolic", "Show / hide to-do list", self.toggle_todo)
        top.pack_end(self.todo_btn, False, False, 0)
        self.root.pack_start(top, False, False, 0)

        self.clock = Gtk.DrawingArea()
        self.clock.set_size_request(self.CLOCK_SIZE, self.CLOCK_SIZE)
        self.clock.connect("draw", self.on_draw_clock)
        self.root.pack_start(self.clock, False, False, 0)

        self.current_label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
        self.current_label.get_style_context().add_class("current")
        self.current_label.set_no_show_all(True)
        self.root.pack_start(self.current_label, False, False, 0)

        controls = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=4)
        controls.pack_start(icon_button("view-refresh-symbolic", "Reset", self.reset), False, False, 0)
        self.play_btn = icon_button("media-playback-start-symbolic", "Start / pause", self.toggle_run, primary=True)
        controls.pack_start(self.play_btn, False, False, 0)
        controls.pack_start(icon_button("media-skip-forward-symbolic", "Skip to next", self.skip), False, False, 0)
        self.root.pack_start(controls, False, False, 0)

        self.todo_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.todo_holder.set_no_show_all(True)
        self.root.pack_start(self.todo_holder, True, True, 0)

        self.root.show_all()
        self.apply_window_settings()

        if self.settings["x"] is not None and self.settings["y"] is not None:
            self.move(self.settings["x"], self.settings["y"])
        else:
            geo = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
            area = geo.get_workarea()
            self.move(area.x + area.width - 280, area.y + 40)

        GLib.timeout_add(200, self.tick)

    # settings ------------------------------------------------------------------
    def save_settings(self):
        save_json(SETTINGS_FILE, self.settings)

    def phase_seconds(self, phase):
        return self.settings[PHASES[phase]["key"]] * 60

    def on_durations_changed(self):
        if not self.running and self.end_time is None:
            self.remaining = self.phase_seconds(self.phase)
        self.clock.queue_draw()

    def apply_window_settings(self):
        s = self.settings
        self.set_keep_above(s["layer"] == "above")
        self.set_keep_below(s["layer"] == "below")
        if s["all_workspaces"]:
            self.stick()
        else:
            self.unstick()
        self.set_skip_taskbar_hint(not s["show_in_taskbar"])
        self.set_skip_pager_hint(not s["show_in_taskbar"])

        ctx = self.pin_btn.get_style_context()
        (ctx.add_class if s["layer"] == "above" else ctx.remove_class)("toggled")

        self.todo_btn.set_visible(s["todo_enabled"])
        if s["todo_enabled"] and self.todo is None:
            self.todo = TodoPanel(self)
            self.todo_holder.add(self.todo)
            self.todo.show_all()
        show_todo = s["todo_enabled"] and s["todo_visible"]
        self.todo_holder.set_visible(show_todo)
        ctx = self.todo_btn.get_style_context()
        (ctx.add_class if show_todo else ctx.remove_class)("toggled")
        self.update_current_task()
        self.queue_draw()
        GLib.idle_add(self.shrink)

    def shrink(self):
        self.resize(1, 1)
        return False

    def update_current_task(self):
        item = self.todo.active_item() if (self.todo and self.settings["todo_enabled"]) else None
        if item:
            self.current_label.set_text(f"▸ {item['text']}")
            self.current_label.set_tooltip_text(item["text"])
            self.current_label.show()
        else:
            self.current_label.hide()

    # timer -------------------------------------------------------------------
    def tick(self):
        if self.running:
            self.remaining = max(0.0, self.end_time - time.monotonic())
            if self.remaining <= 0:
                self.complete()
            self.clock.queue_draw()
        return True

    def start(self):
        if self.remaining <= 0:
            self.remaining = self.phase_seconds(self.phase)
        self.end_time = time.monotonic() + self.remaining
        self.running = True
        self.play_btn.get_image().set_from_icon_name("media-playback-pause-symbolic", Gtk.IconSize.BUTTON)

    def pause(self):
        if self.running:
            self.remaining = max(0.0, self.end_time - time.monotonic())
        self.running = False
        self.play_btn.get_image().set_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON)

    def toggle_run(self):
        self.pause() if self.running else self.start()
        self.clock.queue_draw()

    def reset(self):
        self.pause()
        self.end_time = None
        self.remaining = self.phase_seconds(self.phase)
        self.clock.queue_draw()

    def set_phase(self, phase):
        self.pause()
        self.phase = phase
        self.end_time = None
        self.remaining = self.phase_seconds(phase)
        self.clock.queue_draw()

    def next_phase(self):
        if self.phase == FOCUS:
            return LONG if self.completed % self.settings["long_every"] == 0 else SHORT
        return FOCUS

    def skip(self):
        # skipping a focus session does not count it as completed
        if self.phase == FOCUS:
            self.set_phase(LONG if (self.completed + 1) % self.settings["long_every"] == 0 else SHORT)
        else:
            self.set_phase(FOCUS)

    def complete(self):
        finished = self.phase
        if finished == FOCUS:
            self.completed += 1
            if self.todo:
                self.todo.credit_pomodoro()
        nxt = self.next_phase()
        auto = self.settings["auto_start_breaks"] if finished == FOCUS else self.settings["auto_start_focus"]
        self.set_phase(nxt)

        if self.settings["sound"]:
            play_sound()
        if self.settings["notify"]:
            if finished == FOCUS:
                mins = self.settings[PHASES[nxt]["key"]]
                send_notification("Focus session complete 🍅", f"Take a {mins}-minute {'long' if nxt == LONG else 'short'} break.")
            else:
                send_notification("Break is over", "Time to focus again.")
        if auto:
            self.start()

    # window actions ----------------------------------------------------------
    def toggle_pin(self):
        self.settings["layer"] = "normal" if self.settings["layer"] == "above" else "above"
        self.save_settings()
        self.apply_window_settings()

    def toggle_todo(self):
        self.settings["todo_visible"] = not self.settings["todo_visible"]
        self.save_settings()
        self.apply_window_settings()

    def open_settings(self):
        if self.settings_dialog:
            self.settings_dialog.present()
            return
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.connect("destroy", lambda *_: setattr(self, "settings_dialog", None))

    def quit(self):
        self.save_settings()
        self.get_application().quit()

    # events ------------------------------------------------------------------
    def on_button_press(self, _w, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if event.button == 1:
            # Only arm a drag here; starting it on press would hand the pointer
            # to the window manager and swallow clicks meant for child widgets.
            if not self._is_interactive(Gtk.get_event_widget(event)):
                self._press = (event.x_root, event.y_root, event.button, event.time)
            return False
        if event.button == 3:
            self.show_menu(event)
            return True
        return False

    def _is_interactive(self, widget):
        while widget is not None and widget is not self:
            if widget is self.todo_holder or isinstance(widget, (Gtk.Button, Gtk.Entry, Gtk.Scale)):
                return True
            widget = widget.get_parent()
        return False

    def on_motion(self, _w, event):
        if not self._press or not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            self._press = None
            return False
        x, y, button, ev_time = self._press
        if abs(event.x_root - x) + abs(event.y_root - y) < 6:
            return False
        self._press = None
        self.begin_move_drag(button, int(x), int(y), ev_time)
        return True

    def show_menu(self, event):
        menu = Gtk.Menu()

        def item(label, cb):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda *_: cb())
            menu.append(mi)

        item("Pause" if self.running else "Start", self.toggle_run)
        item("Reset", self.reset)
        item("Skip", self.skip)
        menu.append(Gtk.SeparatorMenuItem())
        for phase, info in PHASES.items():
            item(f"Switch to {info['label'].title()}", lambda p=phase: self.set_phase(p))
        menu.append(Gtk.SeparatorMenuItem())
        group = []
        for key, text in LAYERS:
            mi = Gtk.RadioMenuItem.new_with_label(group, text)
            group = mi.get_group()
            mi.set_active(self.settings["layer"] == key)
            mi.connect("toggled", self.on_layer_menu, key)
            menu.append(mi)
        menu.append(Gtk.SeparatorMenuItem())
        if self.settings["todo_enabled"]:
            item("Hide to-do list" if self.settings["todo_visible"] else "Show to-do list", self.toggle_todo)
        item("Settings…", self.open_settings)
        item("Quit", self.quit)
        menu.show_all()
        menu.attach_to_widget(self, None)
        menu.popup_at_pointer(event)

    def on_layer_menu(self, mi, key):
        if mi.get_active():
            self.settings["layer"] = key
            self.save_settings()
            self.apply_window_settings()

    def on_entry_press(self, entry, event):
        if not self.get_accept_focus():
            self.set_accept_focus(True)
            self.present_with_time(event.time)
            entry.grab_focus()
        return False

    def release_focus(self):
        self.set_focus(None)
        self.set_accept_focus(False)

    def on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.release_focus()
            return True
        return False

    def on_configure(self, *_):
        if self._save_pos_id:
            GLib.source_remove(self._save_pos_id)
        self._save_pos_id = GLib.timeout_add(600, self._save_position)
        return False

    def _save_position(self):
        self._save_pos_id = None
        x, y = self.get_position()
        if (x, y) != (self.settings["x"], self.settings["y"]):
            self.settings["x"], self.settings["y"] = x, y
            self.save_settings()
        return False

    # drawing -----------------------------------------------------------------
    def on_draw_bg(self, widget, cr):
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_operator(0)  # CLEAR
        cr.paint()
        cr.set_operator(2)  # OVER
        rounded_rect(cr, 0.5, 0.5, w - 1, h - 1, 18)
        cr.set_source_rgba(0.09, 0.09, 0.11, self.settings["opacity"])
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.08)
        cr.set_line_width(1)
        cr.stroke()
        return False

    def on_draw_clock(self, widget, cr):
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 10
        r, g, b = PHASES[self.phase]["color"]
        total = self.phase_seconds(self.phase)
        progress = 1 - (self.remaining / total if total else 0)

        cr.set_line_width(9)
        cr.set_line_cap(1)  # ROUND
        cr.set_source_rgba(1, 1, 1, 0.08)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        if progress > 0.0005:
            cr.set_source_rgba(r, g, b, 1 if self.running else 0.6)
            start = -math.pi / 2
            cr.arc(cx, cy, radius, start, start + 2 * math.pi * progress)
            cr.stroke()

        secs = int(math.ceil(self.remaining))
        text = f"{secs // 60:02d}:{secs % 60:02d}"
        self._draw_text(cr, text, cx, cy - 6, "Sans Bold 30", (1, 1, 1, 0.95 if self.running else 0.75))
        self._draw_text(cr, PHASES[self.phase]["label"], cx, cy + 26, "Sans Bold 8", (r, g, b, 0.95))

        # session dots
        n = self.settings["long_every"]
        filled = self.completed % n
        if self.completed and filled == 0 and self.phase == LONG:
            filled = n
        spacing = 11
        x0 = cx - (n - 1) * spacing / 2
        for i in range(n):
            cr.arc(x0 + i * spacing, cy + 46, 3, 0, 2 * math.pi)
            if i < filled:
                cr.set_source_rgba(1.00, 0.42, 0.36, 0.95)
            else:
                cr.set_source_rgba(1, 1, 1, 0.18)
            cr.fill()

    def _draw_text(self, cr, text, cx, cy, font, rgba):
        layout = self.clock.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription.from_string(font))
        _, logical = layout.get_pixel_extents()
        cr.set_source_rgba(*rgba)
        cr.move_to(cx - logical.width / 2, cy - logical.height / 2)
        PangoCairo.show_layout(cr, layout)


# --------------------------------------------------------------------------- app

class PromodorApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = PomodoroWindow(self)
            self.window.show()
        self.window.present()
        self.window.set_focus(None)


def main():
    GLib.set_prgname("promodor")
    GLib.set_application_name("Promodor")
    return PromodorApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
