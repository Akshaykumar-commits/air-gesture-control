"""
Advanced Air Gesture Control v6 (Windows) - neon visuals, no lag (effects run in their own thread)
--------------------------------------------------------------------
Requires: opencv-python, mediapipe==0.10.14, pyautogui, numpy
Run:      python advanced_gesture_control_v6.py
Extra:    pip install screen-brightness-control   (needed for brightness)

WHAT'S NEW IN v2
  * Camera runs in its own thread + lighter hand model + smaller detection image (much less lag)
  * Cursor moved with the native Windows API (much faster than PyAutoGUI)
  * Adaptive smoothing: steady when you move slowly, fast when you move quickly
  * Clicks land where the cursor WAS before you pinched (no more missed clicks)
  * Pinch has hysteresis, so it doesn't flicker on and off
  * Hold gestures forgive short detection flickers
  * Swipes are easier (shorter, quicker motion) and use any open hand
  * Top-left of the preview shows finger states: T I M R P (1 = finger up)

GESTURES
  POINT   index finger only            -> move cursor
  PINCH   thumb + index                -> quick pinch = left click, hold = drag
  PINCH_R thumb + middle               -> right click
  PEACE   index + middle up            -> scroll (move hand up / down)
  OPEN    all fingers up + swipe       -> left/right = arrow keys (slides / pages)
                                          up = Task View, down = Show desktop
  SHAKA   thumb + pinky                -> volume (move hand up / down)
  LOVE    thumb + index + pinky        -> SCREEN BRIGHTNESS (move hand up = brighter, down = dimmer)
  THREE   index+middle+ring, hold      -> open app 1 (Chrome)
  FOUR    4 fingers, thumb in, hold    -> open app 2 (File Explorer)
  ROCK    index + pinky (thumb folded), hold -> close window (Alt+F4)
  FIST    hold                         -> LOCK / UNLOCK

KEYS in the preview window: q or Esc = quit, l = lock/unlock,
  h = show/hide gesture panel, d = small/large preview, i = show finger states
SAFETY: move the real mouse into the top-left corner to stop the program.
"""

import collections
import ctypes
import math
import subprocess
import threading
import time

import cv2
import mediapipe as mp
import numpy as np
import pyautogui

try:
    import screen_brightness_control as sbc
except ImportError:
    sbc = None

# ============================== SETTINGS ==============================
CAMERA_INDEX = 0
FRAME_W, FRAME_H = 640, 480
DETECT_W, DETECT_H = 320, 240    # image size used for hand detection (smaller = faster)

# Active zone (fractions of the camera image) that maps to your whole screen
ZONE_LEFT, ZONE_RIGHT = 0.30, 0.70
ZONE_TOP, ZONE_BOTTOM = 0.25, 0.65

SMOOTH_MIN, SMOOTH_MAX = 0.30, 0.95   # cursor smoothing (low = steadier, high = snappier)
SMOOTH_DIST = 150.0                   # screen pixels of movement where smoothing is lightest

PINCH_ON = 0.28              # pinch starts when fingertips are this close (x palm size)
PINCH_OFF = 0.45             # pinch ends when they separate this far
DRAG_HOLD = 0.40             # pinch held longer than this (seconds) becomes a drag

SCROLL_GAIN = 4000
SCROLL_DEADZONE = 0.004

SWIPE_DISTANCE = 0.20        # fraction of the frame the hand must travel
SWIPE_WINDOW = 0.50          # ...within this many seconds
SWIPE_COOLDOWN = 0.8
SWIPE_RIGHT_KEY = "right"
SWIPE_LEFT_KEY = "left"

BRIGHTNESS_STEP = 10        # percent changed per step by the LOVE gesture

HOLD_SECONDS = {"THREE": 0.8, "FOUR": 1.0, "ROCK": 1.5, "FIST": 0.8}
HOLD_GRACE = 0.30            # a hold survives detection flickers shorter than this
APP_THREE = "chrome"         # try: notepad, calc, code, spotify, msedge, or a full path
APP_FOUR = "explorer"
ENABLE_CLOSE_WINDOW = True
SHOW_PREVIEW = True          # show the neon preview window (applies on next Start)

STABLE_FRAMES = 2            # frames a pose must persist before it counts
GLOW = False                 # (unused in v4, effects are always on)

# ---- visual effects ----
NEON = True                  # darker blue-tinted camera image
DARKEN = 0.60                # camera brightness in the preview (1.0 = normal)
SCANLINES = True             # faint horizontal scanlines
TRAIL_LENGTH = 0             # fingertip trail line (0 = off, e.g. 18 = on)
# ======================================================================

pyautogui.FAILSAFE = False   # we do our own corner check below
pyautogui.PAUSE = 0

user32 = ctypes.windll.user32
mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils

HOLD_LABELS = {
    "THREE": f"Open {APP_THREE}",
    "FOUR": f"Open {APP_FOUR}",
    "ROCK": "Close window",
    "FIST": "Lock / Unlock",
}


class Control:
    """Shared object so the app window can start/stop the engine and read its status."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.lock_toggle = False
        self.locked = False
        self.fps = 0.0
        self.gesture = "NONE"
        self.error = ""
        self.running = False


# ---------------------- fast native mouse helpers ----------------------
def set_cursor(x, y):
    user32.SetCursorPos(int(x), int(y))


def mouse_down():
    user32.mouse_event(0x0002, 0, 0, 0, 0)


def mouse_up():
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def left_click():
    mouse_down()
    mouse_up()


def right_click():
    user32.mouse_event(0x0008, 0, 0, 0, 0)
    user32.mouse_event(0x0010, 0, 0, 0, 0)


def wheel(delta):
    user32.mouse_event(0x0800, 0, 0, int(delta), 0)


_brightness_busy = False


def change_brightness(delta):
    """Runs in a background thread so the video never freezes."""
    global _brightness_busy
    if sbc is None or _brightness_busy:
        return
    _brightness_busy = True

    def work():
        global _brightness_busy
        try:
            current = sbc.get_brightness()
            current = current[0] if isinstance(current, (list, tuple)) else current
            sbc.set_brightness(int(clamp(current + delta, 0, 100)))
        except Exception as e:
            print("Brightness change failed:", e)
        finally:
            _brightness_busy = False

    threading.Thread(target=work, daemon=True).start()


# ---------------------------- camera thread ----------------------------
class CameraThread(threading.Thread):
    """Reads frames continuously so the main loop always gets the newest one."""

    def __init__(self):
        super().__init__(daemon=True)
        self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame = None
        self.count = 0
        self.running = True

    def opened(self):
        return self.cap.isOpened()

    def run(self):
        while self.running:
            ok, f = self.cap.read()
            if ok:
                self.frame = f
                self.count += 1
            else:
                time.sleep(0.005)

    def stop(self):
        self.running = False
        time.sleep(0.05)
        self.cap.release()


# ---------------------------- hand analysis ----------------------------
def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def finger_states(p):
    """(thumb, index, middle, ring, pinky) -> True when extended."""
    wrist = p[0]

    def ext(tip, pip):
        return dist(p[tip], wrist) > dist(p[pip], wrist) * 1.05

    thumb = dist(p[4], p[17]) > dist(p[3], p[17])
    return thumb, ext(8, 6), ext(12, 10), ext(16, 14), ext(20, 18)


def analyze(p, prev):
    """Returns (gesture_name, finger_states). `prev` = last frame's raw gesture."""
    hs = dist(p[0], p[9]) or 1.0
    t, i, m, r, pk = finger_states(p)
    states = (t, i, m, r, pk)

    thr_i = (PINCH_OFF if prev == "PINCH" else PINCH_ON) * hs
    thr_m = (PINCH_OFF if prev == "PINCH_R" else PINCH_ON) * hs

    if dist(p[4], p[8]) < thr_i and dist(p[8], p[0]) > 1.0 * hs:
        return "PINCH", states
    if dist(p[4], p[12]) < thr_m and dist(p[12], p[0]) > 1.0 * hs:
        return "PINCH_R", states
    if t and pk and not (i or m or r):
        return "SHAKA", states
    if t and i and m and r and pk:
        return "PALM", states
    if i and m and r and pk and not t:
        return "FOUR", states
    if i and m and r and not pk:
        return "THREE", states
    if i and m and not (r or pk):
        return "PEACE", states
    if t and i and pk and not (m or r):
        return "LOVE", states
    if i and pk and not (t or m or r):
        return "ROCK", states
    if i and not (m or r or pk):
        return "POINT", states
    if not (i or m or r or pk):
        return "FIST", states
    return "NONE", states


def pick_recent(hist, now, back=0.25):
    """Cursor position from about `back` seconds ago (before the pinch moved the finger)."""
    for t, x, y in hist:
        if now - t <= back:
            return x, y
    return None


# -------------------------------- drawing -----------------------------
FONT = cv2.FONT_HERSHEY_SIMPLEX
HAND_CONN = list(mp_hands.HAND_CONNECTIONS)
TIPS = (4, 8, 12, 16, 20)

GESTURE_COLORS = {
    "POINT": (255, 255, 0), "PINCH": (255, 0, 255), "PINCH_R": (0, 128, 255),
    "PEACE": (0, 255, 0), "PALM": (255, 200, 0), "SHAKA": (0, 255, 255),
    "LOVE": (180, 105, 255), "THREE": (0, 200, 255), "FOUR": (255, 150, 0),
    "ROCK": (0, 0, 255), "FIST": (200, 200, 200), "NONE": (255, 255, 255),
}
GESTURE_NAMES = {
    "POINT": "POINTER", "PINCH": "CLICK / DRAG", "PINCH_R": "RIGHT CLICK",
    "PEACE": "SCROLL", "PALM": "OPEN PALM", "SHAKA": "VOLUME",
    "LOVE": "BRIGHTNESS", "THREE": "APP LAUNCH", "FOUR": "APP LAUNCH",
    "ROCK": "CLOSE WINDOW", "FIST": "LOCK / UNLOCK", "NONE": "SEARCHING...",
}
PANEL_ITEMS = [
    ("POINT", "Point : move"), ("PINCH", "Pinch : click/drag"), ("PINCH_R", "Pinch+mid : right click"),
    ("PEACE", "Peace : scroll"), ("PALM", "Palm swipe : slides"), ("SHAKA", "Shaka : volume"),
    ("LOVE", "Love : brightness"), ("THREE", "3 fingers : open app"), ("FOUR", "4 fingers : explorer"),
    ("ROCK", "Rock : close"), ("FIST", "Fist : lock"),
]


def scale_color(c, k):
    k = max(0.0, min(1.0, k))
    return tuple(int(v * k) for v in c)


class Effects:
    """All the visual polish: neon skeleton, fingertip trail, click ripples, HUD, popups."""

    def __init__(self):
        self.trail = collections.deque(maxlen=TRAIL_LENGTH)
        self.ripples = []
        self.notes = []
        self.show_panel = True
        self.debug = False
        self.demo = False
        self.lock = threading.Lock()
        self._shape = None
        self._tint = None
        self._scan = None

    def ripple(self, x, y, color):
        with self.lock:
            self.ripples.append((float(x), float(y), color, time.time()))

    def notify(self, text, color=(255, 255, 255)):
        with self.lock:
            self.notes.append((text, color, time.time()))
            self.notes = self.notes[-3:]

    def _prepare(self, shape):
        if self._shape == shape:
            return
        h, w = shape[:2]
        self._shape = shape
        self._tint = np.full((h, w, 3), (200, 90, 0), np.uint8)
        scan = np.zeros((h, w, 3), np.uint8)
        scan[::3] = 22
        self._scan = scan

    def render(self, frame, pts, gesture, states, locked, progress, label, fps, now):
        h, w = frame.shape[:2]
        self._prepare(frame.shape)

        # sci-fi look: darker, slightly blue camera image with faint scanlines
        if NEON:
            frame = cv2.addWeighted(frame, DARKEN, self._tint, 0.12, 0)
            if SCANLINES:
                frame = cv2.subtract(frame, self._scan)

        color = (60, 60, 255) if locked else GESTURE_COLORS.get(gesture, (255, 255, 255))

        # ---- glowing layer: skeleton + fingertip trail + ripples ----
        layer = np.zeros_like(frame)
        if pts is not None:
            P = [(int(x), int(y)) for x, y in pts]
            for a, b in HAND_CONN:
                cv2.line(layer, P[a], P[b], color, 2, cv2.LINE_AA)
            for idx, pt in enumerate(P):
                cv2.circle(layer, pt, 6 if idx in TIPS else 3, (255, 255, 255), -1, cv2.LINE_AA)
            self.trail.append(P[8])
        elif self.trail:
            self.trail.popleft()

        n = len(self.trail)
        for k in range(1, n):
            f = k / n
            cv2.line(layer, self.trail[k - 1], self.trail[k], scale_color(color, f),
                     max(1, int(1 + 5 * f)), cv2.LINE_AA)

        with self.lock:
            self.ripples = [r for r in self.ripples if now - r[3] < 0.7]
            ripples = list(self.ripples)
        for (x, y, c, t0) in ripples:
            f = (now - t0) / 0.7
            cv2.circle(layer, (int(x), int(y)), int(8 + 60 * f), scale_color(c, 1 - f), 3, cv2.LINE_AA)
            cv2.circle(layer, (int(x), int(y)), int(4 + 30 * f), scale_color(c, 1 - f), 2, cv2.LINE_AA)

        small = cv2.resize(layer, (max(w // 4, 1), max(h // 4, 1)), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 3)
        glow = cv2.convertScaleAbs(cv2.resize(small, (w, h)), alpha=2.2)
        frame = cv2.add(frame, glow)
        frame = cv2.add(frame, layer)

        # ---- HUD ----
        bracket = (255, 200, 0)
        L, m = 28, 8
        for (x, y, sx, sy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            cv2.line(frame, (x, y), (x + sx * L, y), bracket, 2, cv2.LINE_AA)
            cv2.line(frame, (x, y), (x, y + sy * L), bracket, 2, cv2.LINE_AA)
        cv2.rectangle(
            frame,
            (int(ZONE_LEFT * w), int(ZONE_TOP * h)),
            (int(ZONE_RIGHT * w), int(ZONE_BOTTOM * h)),
            (90, 70, 40), 1,
        )

        cv2.putText(frame, "AIR GESTURE CONTROL", (18, 30), FONT, 0.6, (255, 220, 0), 2, cv2.LINE_AA)
        status = "LOCKED" if locked else "ONLINE"
        scolor = (60, 60, 255) if locked else (0, 255, 120)
        cv2.putText(frame, f"{status}  {fps:.0f} FPS", (w - 190, 30), FONT, 0.55, scolor, 2, cv2.LINE_AA)

        name = "LOCKED - HOLD FIST TO UNLOCK" if locked else GESTURE_NAMES.get(gesture, gesture)
        size = cv2.getTextSize(name, FONT, 0.9, 2)[0]
        cv2.putText(frame, name, ((w - size[0]) // 2, 64), FONT, 0.9, color, 2, cv2.LINE_AA)

        if self.show_panel:
            px, py, rw = 14, 92, 215
            overlay = frame.copy()
            cv2.rectangle(overlay, (px - 6, py - 16), (px + rw, py + 20 * len(PANEL_ITEMS) - 8), (20, 12, 5), -1)
            frame = cv2.addWeighted(overlay, 0.45, frame, 0.55, 0)
            for idx, (g, txt) in enumerate(PANEL_ITEMS):
                active = (g == gesture) and not locked
                c = GESTURE_COLORS[g] if active else (160, 160, 160)
                cv2.putText(frame, ("> " if active else "  ") + txt, (px, py + 20 * idx),
                            FONT, 0.48, c, 2 if active else 1, cv2.LINE_AA)

        # hold-to-trigger progress ring around the palm
        if progress > 0 and pts is not None:
            cx, cy = int(pts[9][0]), int(pts[9][1])
            cv2.ellipse(frame, (cx, cy), (48, 48), -90, 0, int(360 * progress), (0, 255, 120), 4, cv2.LINE_AA)
            cv2.putText(frame, label, (max(cx - 70, 4), max(cy - 58, 16)), FONT, 0.55, (255, 255, 255), 2, cv2.LINE_AA)

        # floating action popups
        with self.lock:
            self.notes = [n for n in self.notes if now - n[2] < 1.5]
            notes = list(self.notes)
        for idx, (text, c, t0) in enumerate(reversed(notes)):
            f = 1 - (now - t0) / 1.5
            tsize = cv2.getTextSize(text, FONT, 0.9, 2)[0]
            cv2.putText(frame, text, ((w - tsize[0]) // 2, h - 46 - 34 * idx),
                        FONT, 0.9, scale_color(c, f), 2, cv2.LINE_AA)

        if self.debug and states is not None:
            txt = " ".join(f"{n}{int(s)}" for n, s in zip("TIMRP", states))
            cv2.putText(frame, txt, (14, h - 30), FONT, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        cv2.putText(frame, "Q quit  L lock  H panel  D size  I debug", (14, h - 12),
                    FONT, 0.4, (120, 120, 120), 1, cv2.LINE_AA)
        return frame


# ------------------------- preview / effects thread -------------------
class Renderer(threading.Thread):
    """Draws the neon effects and shows the preview window in its own thread,
    so the visuals can never slow down cursor control."""

    def __init__(self, fx, win, sw, sh):
        super().__init__(daemon=True)
        self.fx, self.win, self.sw, self.sh = fx, win, sw, sh
        self.packet = None
        self.seq = 0
        self.running = True
        self.quit = False
        self.lock_toggle = False

    def submit(self, *packet):
        self.packet = packet          # replaces the previous frame (no queue, no backlog)
        self.seq += 1

    def run(self):
        win = self.win
        if not SHOW_PREVIEW:
            while self.running:          # no window, but keep the thread alive
                time.sleep(0.05)
            return
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 480, 360)
        cv2.moveWindow(win, max(self.sw - 500, 0), max(self.sh - 460, 0))
        try:
            cv2.setWindowProperty(win, cv2.WND_PROP_TOPMOST, 1)
        except cv2.error:
            pass
        seen = -1
        while self.running:
            pkt, seq = self.packet, self.seq
            new = pkt is not None and seq != seen
            if new:
                seen = seq
                frame, pts, gesture, states, locked, progress, label, fps = pkt
                out = self.fx.render(frame, pts, gesture, states, locked, progress, label, fps, time.time())
                cv2.imshow(win, out)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                self.quit = True
            elif key == ord("l"):
                self.lock_toggle = True
            elif key == ord("h"):
                self.fx.show_panel = not self.fx.show_panel
            elif key == ord("i"):
                self.fx.debug = not self.fx.debug
            elif key == ord("d"):
                self.fx.demo = not self.fx.demo
                pw, ph = (720, 540) if self.fx.demo else (480, 360)
                cv2.resizeWindow(win, pw, ph)
                cv2.moveWindow(win, max(self.sw - pw - 20, 0), max(self.sh - ph - 100, 0))
            if not new:
                time.sleep(0.003)
        cv2.destroyAllWindows()


# --------------------------------- main -------------------------------
def main(control=None):
    control = control or Control()
    control.stop_event.clear()
    control.error = ""
    sw, sh = pyautogui.size()
    cam = CameraThread()
    if not cam.opened():
        control.error = "Could not open the camera. Close other apps using it, or pick another camera."
        print(control.error)
        return
    cam.start()

    hands = mp_hands.Hands(
        max_num_hands=1,
        model_complexity=0,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )

    fx = Effects()
    renderer = Renderer(fx, "Air Gesture Control", sw, sh)
    renderer.start()

    history = collections.deque(maxlen=STABLE_FRAMES)
    raw_prev = "NONE"
    stable = "NONE"
    hold_g, hold_start, hold_seen, fired = "NONE", 0.0, 0.0, False
    locked = False

    cx = cy = None
    pos_hist = collections.deque()
    pinch_active = False
    dragging = False
    pinch_start = 0.0
    click_pos = (0, 0)
    right_active = False
    last_right = 0.0

    scroll_prev_y = None
    volume_anchor_y = None
    brightness_anchor_y = None
    trail = collections.deque()
    last_swipe = 0.0

    last_time = time.time()
    fps = 0.0
    seen_count = -1

    def release_drag():
        nonlocal dragging, pinch_active
        if dragging:
            mouse_up()
        dragging = False
        pinch_active = False

    control.running = True
    if sbc is None:
        print("NOTE: brightness gesture disabled. Run: pip install screen-brightness-control")
    try:
        while True:
            if renderer.quit or control.stop_event.is_set():
                break
            if cam.frame is None or cam.count == seen_count:
                time.sleep(0.002)
                continue
            seen_count = cam.count
            frame = cv2.flip(cam.frame, 1)
            h, w = frame.shape[:2]

            small = cv2.resize(frame, (DETECT_W, DETECT_H))
            result = hands.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            now = time.time()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - last_time, 1e-3))
            last_time = now

            # corner failsafe (real mouse to top-left corner)
            mx, my = pyautogui.position()
            if mx <= 1 and my <= 1:
                control.error = "Stopped: mouse was in the top-left corner (safety)."
                print(control.error)
                break

            raw, pts, states = "NONE", None, None
            if result.multi_hand_landmarks:
                hand = result.multi_hand_landmarks[0]
                pts = [(lm.x * w, lm.y * h) for lm in hand.landmark]
                raw, states = analyze(pts, raw_prev)
            raw_prev = raw

            history.append(raw)
            if len(history) == history.maxlen and len(set(history)) == 1:
                stable = raw

            # ---------- hold-to-trigger gestures (with flicker grace) ----------
            if stable == hold_g:
                hold_seen = now
            elif now - hold_seen > HOLD_GRACE:
                hold_g, hold_start, hold_seen, fired = stable, now, now, False
            need = HOLD_SECONDS.get(hold_g)
            if need and hold_g == "ROCK" and not ENABLE_CLOSE_WINDOW:
                need = None
            if need and locked and hold_g != "FIST":
                need = None
            progress, label = 0.0, ""
            if need:
                elapsed = now - hold_start
                progress = min(elapsed / need, 1.0)
                label = HOLD_LABELS[hold_g]
                if elapsed >= need and not fired:
                    fired = True
                    if hold_g == "FIST":
                        locked = not locked
                        release_drag()
                        fx.notify("LOCKED" if locked else "UNLOCKED", (60, 60, 255) if locked else (0, 255, 120))
                    elif hold_g == "THREE":
                        subprocess.Popen(f'start "" {APP_THREE}', shell=True)
                        fx.notify(HOLD_LABELS["THREE"].upper(), (0, 200, 255))
                    elif hold_g == "FOUR":
                        subprocess.Popen(f'start "" {APP_FOUR}', shell=True)
                        fx.notify(HOLD_LABELS["FOUR"].upper(), (255, 150, 0))
                    elif hold_g == "ROCK":
                        pyautogui.hotkey("alt", "f4")
                        fx.notify("WINDOW CLOSED", (0, 0, 255))

            # ---------- continuous gestures ----------
            if pts is None:
                release_drag()
                cx = cy = None
                scroll_prev_y = volume_anchor_y = brightness_anchor_y = None
                right_active = False
                trail.clear()
            elif not locked:
                while pos_hist and now - pos_hist[0][0] > 0.6:
                    pos_hist.popleft()

                # --- left click / drag ---
                if stable == "PINCH":
                    if not pinch_active:
                        pinch_active, pinch_start, dragging = True, now, False
                        click_pos = pick_recent(pos_hist, now) or pyautogui.position()
                        cx, cy = click_pos
                    elif not dragging and now - pinch_start > DRAG_HOLD:
                        set_cursor(*click_pos)
                        mouse_down()
                        dragging = True
                        fx.notify("DRAG", (255, 0, 255))
                elif pinch_active:
                    if dragging:
                        mouse_up()
                    else:
                        set_cursor(*click_pos)
                        left_click()
                        fx.ripple(pts[8][0], pts[8][1], (255, 0, 255))
                    pinch_active = dragging = False

                # --- cursor movement ---
                if stable == "POINT" or (stable == "PINCH" and dragging):
                    tx = np.interp(pts[8][0] / w, [ZONE_LEFT, ZONE_RIGHT], [0, sw - 1])
                    ty = np.interp(pts[8][1] / h, [ZONE_TOP, ZONE_BOTTOM], [0, sh - 1])
                    if cx is None:
                        cx, cy = pyautogui.position()
                    d = math.hypot(tx - cx, ty - cy)
                    a = clamp(d / SMOOTH_DIST, SMOOTH_MIN, SMOOTH_MAX)
                    cx += (tx - cx) * a
                    cy += (ty - cy) * a
                    set_cursor(clamp(cx, 5, sw - 6), clamp(cy, 5, sh - 6))
                    pos_hist.append((now, cx, cy))
                elif stable == "PINCH" and pinch_active:
                    set_cursor(*click_pos)          # hold still while deciding click vs drag
                elif stable != "PINCH_R":
                    cx = cy = None

                # --- right click ---
                if stable == "PINCH_R":
                    if not right_active and now - last_right > 0.8:
                        pos = pick_recent(pos_hist, now)
                        if pos:
                            set_cursor(*pos)
                        right_click()
                        fx.ripple(pts[8][0], pts[8][1], (0, 128, 255))
                        fx.notify("RIGHT CLICK", (0, 128, 255))
                        last_right = now
                    right_active = True
                else:
                    right_active = False

                # --- scroll ---
                if stable == "PEACE":
                    y = (pts[8][1] + pts[12][1]) / 2 / h
                    if scroll_prev_y is not None:
                        dy = scroll_prev_y - y
                        if abs(dy) > SCROLL_DEADZONE:
                            wheel(dy * SCROLL_GAIN)
                    scroll_prev_y = y
                else:
                    scroll_prev_y = None

                # --- volume ---
                if stable == "SHAKA":
                    y = pts[9][1] / h
                    if volume_anchor_y is None:
                        volume_anchor_y = y
                    dv = volume_anchor_y - y
                    if dv > 0.04:
                        pyautogui.press("volumeup")
                        fx.notify("VOLUME +", (0, 255, 255))
                        volume_anchor_y = y
                    elif dv < -0.04:
                        pyautogui.press("volumedown")
                        fx.notify("VOLUME -", (0, 255, 255))
                        volume_anchor_y = y
                else:
                    volume_anchor_y = None

                # --- screen brightness ---
                if stable == "LOVE":
                    y = pts[9][1] / h
                    if brightness_anchor_y is None:
                        brightness_anchor_y = y
                    db = brightness_anchor_y - y
                    if db > 0.05:
                        change_brightness(BRIGHTNESS_STEP)
                        fx.notify("BRIGHTNESS +", (180, 105, 255))
                        brightness_anchor_y = y
                    elif db < -0.05:
                        change_brightness(-BRIGHTNESS_STEP)
                        fx.notify("BRIGHTNESS -", (180, 105, 255))
                        brightness_anchor_y = y
                else:
                    brightness_anchor_y = None

                # --- swipes (any open hand: 4+ fingers up) ---
                if states is not None and all(states[1:]):
                    trail.append((now, pts[9][0] / w, pts[9][1] / h))
                    while trail and now - trail[0][0] > SWIPE_WINDOW:
                        trail.popleft()
                    if len(trail) >= 3 and now - last_swipe > SWIPE_COOLDOWN:
                        dx = trail[-1][1] - trail[0][1]
                        dy = trail[-1][2] - trail[0][2]
                        did = False
                        if abs(dx) > SWIPE_DISTANCE and abs(dx) > 1.5 * abs(dy):
                            pyautogui.press(SWIPE_RIGHT_KEY if dx > 0 else SWIPE_LEFT_KEY)
                            fx.notify("SWIPE >>" if dx > 0 else "<< SWIPE", (255, 200, 0))
                            did = True
                        elif abs(dy) > SWIPE_DISTANCE and abs(dy) > 1.5 * abs(dx):
                            pyautogui.hotkey("win", "tab" if dy < 0 else "d")
                            fx.notify("TASK VIEW" if dy < 0 else "SHOW DESKTOP", (255, 200, 0))
                            did = True
                        if did:
                            last_swipe = now
                            fired = True        # stop a swipe from also triggering a hold action
                            trail.clear()
                else:
                    trail.clear()

            control.fps, control.gesture, control.locked = fps, stable, locked

            # hand the finished frame to the preview thread (never blocks cursor control)
            renderer.submit(frame, pts, stable, states, locked, progress, label, fps)
            if renderer.lock_toggle or control.lock_toggle:
                renderer.lock_toggle = control.lock_toggle = False
                locked = not locked
                release_drag()
                fx.notify("LOCKED" if locked else "UNLOCKED", (60, 60, 255) if locked else (0, 255, 120))
    finally:
        control.running = False
        release_drag()
        hands.close()
        cam.stop()
        renderer.running = False
        renderer.join(timeout=1.5)


if __name__ == "__main__":
    main()
