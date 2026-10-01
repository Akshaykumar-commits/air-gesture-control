"""
Air Gesture Control - Windows app
Small settings window around engine.py (Start/Stop, camera, sliders, tray icon).
"""
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import engine

APP_NAME = "Air Gesture Control"
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "GestureControl")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

DEFAULTS = {
    "camera": 0,
    "zone_size": 0.40,      # smaller = faster cursor (less hand travel)
    "smoothing": 0.30,      # lower = steadier, higher = snappier
    "app_three": "chrome",
    "app_four": "explorer",
    "close_window": True,
    "preview": True,
    "neon": True,
    "autostart": False,
}

PRIVACY = (
    "Privacy: this app uses your camera only to find your hand, on this PC.\n"
    "Video is never saved, recorded or sent anywhere. The app works offline."
)


def load_settings():
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data.update(json.load(f))
    except Exception:
        pass
    return data


def save_settings(data):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("Could not save settings:", e)


def apply_to_engine(s):
    """Push settings into the engine's module-level variables (read live every frame)."""
    z = float(s["zone_size"])
    engine.ZONE_LEFT, engine.ZONE_RIGHT = 0.5 - z / 2, 0.5 + z / 2
    engine.ZONE_TOP, engine.ZONE_BOTTOM = 0.45 - z / 2, 0.45 + z / 2
    engine.SMOOTH_MIN = float(s["smoothing"])
    engine.CAMERA_INDEX = int(s["camera"])
    engine.APP_THREE = s["app_three"].strip() or "chrome"
    engine.APP_FOUR = s["app_four"].strip() or "explorer"
    engine.HOLD_LABELS["THREE"] = f"Open {engine.APP_THREE}"
    engine.HOLD_LABELS["FOUR"] = f"Open {engine.APP_FOUR}"
    engine.ENABLE_CLOSE_WINDOW = bool(s["close_window"])
    engine.SHOW_PREVIEW = bool(s["preview"])
    engine.NEON = bool(s["neon"])


class App:
    def __init__(self):
        self.s = load_settings()
        self.control = engine.Control()
        self.thread = None
        self.tray = None

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build_ui()
        self.poll()
        if self.s["autostart"]:
            self.root.after(500, self.start)

    # ------------------------------ UI ------------------------------
    def build_ui(self):
        pad = {"padx": 12, "pady": 4}
        main = ttk.Frame(self.root, padding=8)
        main.grid()

        self.status = tk.StringVar(value="Stopped")
        ttk.Label(main, textvariable=self.status, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)

        btns = ttk.Frame(main)
        btns.grid(row=1, column=0, columnspan=2, sticky="we", **pad)
        self.btn_start = ttk.Button(btns, text="Start", command=self.start)
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop = ttk.Button(btns, text="Stop", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 6))
        self.btn_lock = ttk.Button(btns, text="Lock / Unlock", command=self.toggle_lock, state="disabled")
        self.btn_lock.pack(side="left")

        ttk.Label(main, text="Camera").grid(row=2, column=0, sticky="w", **pad)
        self.cam_var = tk.StringVar(value=f"Camera {self.s['camera']}")
        cb = ttk.Combobox(main, textvariable=self.cam_var, state="readonly", width=14,
                          values=[f"Camera {i}" for i in range(5)])
        cb.grid(row=2, column=1, sticky="w", **pad)

        self.zone = self.slider(main, 3, "Cursor speed (faster \u2192)", 0.80, 0.20,
                                self.s["zone_size"])
        self.smooth = self.slider(main, 4, "Responsiveness (snappier \u2192)", 0.10, 0.80,
                                  self.s["smoothing"])

        ttk.Label(main, text="App for 3 fingers").grid(row=5, column=0, sticky="w", **pad)
        self.app3 = tk.StringVar(value=self.s["app_three"])
        ttk.Entry(main, textvariable=self.app3, width=22).grid(row=5, column=1, sticky="w", **pad)
        ttk.Label(main, text="App for 4 fingers").grid(row=6, column=0, sticky="w", **pad)
        self.app4 = tk.StringVar(value=self.s["app_four"])
        ttk.Entry(main, textvariable=self.app4, width=22).grid(row=6, column=1, sticky="w", **pad)

        self.v_close = tk.BooleanVar(value=self.s["close_window"])
        self.v_prev = tk.BooleanVar(value=self.s["preview"])
        self.v_neon = tk.BooleanVar(value=self.s["neon"])
        self.v_auto = tk.BooleanVar(value=self.s["autostart"])
        ttk.Checkbutton(main, text="Rock sign closes window (Alt+F4)", variable=self.v_close).grid(
            row=7, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(main, text="Show camera preview (applies on next Start)", variable=self.v_prev).grid(
            row=8, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(main, text="Neon look in preview", variable=self.v_neon).grid(
            row=9, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(main, text="Start tracking when the app opens", variable=self.v_auto).grid(
            row=10, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(main, text=PRIVACY, foreground="#555", justify="left").grid(
            row=11, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))
        ttk.Label(main, text="Safety: move the real mouse to the top-left corner to stop.",
                  foreground="#555").grid(row=12, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 6))

        # live-apply on any change
        for var in (self.cam_var, self.app3, self.app4, self.v_close, self.v_prev, self.v_neon, self.v_auto):
            var.trace_add("write", lambda *_: self.collect())
        for sc in (self.zone, self.smooth):
            sc.configure(command=lambda _v: self.collect())

    def slider(self, parent, row, text, lo, hi, value):
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", padx=12, pady=4)
        sc = ttk.Scale(parent, from_=lo, to=hi, length=170)
        sc.set(value)
        sc.grid(row=row, column=1, sticky="w", padx=12, pady=4)
        return sc

    def collect(self):
        try:
            self.s.update(
                camera=int(self.cam_var.get().split()[-1]),
                zone_size=round(float(self.zone.get()), 3),
                smoothing=round(float(self.smooth.get()), 3),
                app_three=self.app3.get(), app_four=self.app4.get(),
                close_window=self.v_close.get(), preview=self.v_prev.get(),
                neon=self.v_neon.get(), autostart=self.v_auto.get(),
            )
        except Exception:
            return
        apply_to_engine(self.s)        # live: cursor speed etc. change instantly
        save_settings(self.s)

    # ---------------------------- control ----------------------------
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.collect()
        self.control.error = ""
        self.thread = threading.Thread(target=self._run_engine, daemon=True)
        self.thread.start()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_lock.configure(state="normal")

    def _run_engine(self):
        try:
            engine.main(self.control)
        except Exception as e:
            self.control.error = f"Engine error: {e}"

    def stop(self):
        self.control.stop_event.set()

    def toggle_lock(self):
        self.control.lock_toggle = True

    def poll(self):
        alive = self.thread is not None and self.thread.is_alive()
        if alive and self.control.running:
            state = "LOCKED" if self.control.locked else "Running"
            self.status.set(f"{state}  \u2022  {self.control.fps:.0f} FPS  \u2022  {self.control.gesture}")
        elif alive:
            self.status.set("Starting...")
        else:
            if self.thread is not None:          # just finished
                self.thread = None
                self.btn_start.configure(state="normal")
                self.btn_stop.configure(state="disabled")
                self.btn_lock.configure(state="disabled")
                if self.control.error:
                    messagebox.showwarning(APP_NAME, self.control.error)
                    self.control.error = ""
            self.status.set("Stopped")
        self.root.after(300, self.poll)

    # ------------------------------ tray ------------------------------
    def make_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            return None
        img = Image.new("RGB", (64, 64), (10, 20, 40))
        d = ImageDraw.Draw(img)
        d.ellipse((12, 12, 52, 52), outline=(0, 200, 255), width=5)
        d.ellipse((26, 26, 38, 38), fill=(0, 200, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Open settings", lambda: self.root.after(0, self.show), default=True),
            pystray.MenuItem("Start", lambda: self.root.after(0, self.start)),
            pystray.MenuItem("Stop", lambda: self.root.after(0, self.stop)),
            pystray.MenuItem("Lock / Unlock", lambda: self.root.after(0, self.toggle_lock)),
            pystray.MenuItem("Quit", lambda: self.root.after(0, self.quit)),
        )
        return pystray.Icon("GestureControl", img, APP_NAME, menu)

    def on_close(self):
        """Closing the window hides it to the tray (if available) so tracking keeps running."""
        if self.tray is None:
            self.tray = self.make_tray()
            if self.tray:
                self.tray.run_detached()
        if self.tray:
            self.root.withdraw()
        else:
            self.quit()

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def quit(self):
        self.control.stop_event.set()
        if self.tray:
            self.tray.stop()
        self.root.after(300, self.root.destroy)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
