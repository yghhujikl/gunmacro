import os
import sys
import ctypes
import json
import random
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont

APP_NAME = "Gun macro"
IS_WIN = sys.platform == "win32"

# ---------- Paths ----------
# Everything lives in %LOCALAPPDATA%\GunMacro so it works on any PC:
#   app\   the exe + support files (managed by Loader.exe, replaced on updates)
#   data\  your configs and last session (never touched by updates)
ROOT_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "GunMacro")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_CANDIDATES = [
    os.path.join(getattr(sys, "_MEIPASS", SCRIPT_DIR), "Icon.png"),   # bundled inside the exe
    os.path.join(SCRIPT_DIR, "Icon.png"),
    os.path.join(ROOT_DIR, "app", "Icon.png"),
]
DATA_DIR = os.path.join(ROOT_DIR, "data")
CONFIG_DIR = os.path.join(DATA_DIR, "configs")
SESSION_FILE = os.path.join(DATA_DIR, "last_session.json")
META_FILE = os.path.join(DATA_DIR, "meta.json")                 # remembers when you last used it

# ---------- Version + update check ----------
VERSION = "1.0.0"          # bump this on every release
# Where to look for the newest version number. Set ONE of these:
#   UPDATE_URL  - a link to a text file ("1.2.0") or JSON ({"version": "1.2.0"})
#   UPDATE_FILE - a local file your Loader writes the newest version into
# With neither set, the update status is simply hidden.
UPDATE_URL = ""
UPDATE_FILE = os.path.join(ROOT_DIR, "latest_version.txt")
UPDATE_OK = "#7bc88f"
UPDATE_NEW = "#e5b567"
COPY_OK = "#7bc88f"
DISCORD_NAME = "@reruinz"

# ---------- Theme ----------
BG = "#141414"
BAR = "#0c0c0c"
TEXT = "#e6e6e6"
SUBTEXT = "#7a7a7a"
BTN = "#ffffff"
BTN_HOVER = "#e4e4e4"
BTN_TEXT = "#111111"
CLOSE_HOVER = "#c42b1c"
BAR_BTN_HOVER = "#242424"
RADIUS = 10          # window corner radius
BTN_RADIUS = 6       # button corner radius (subtle)

# ---------- Keybind box ----------
KEYBOX = "#2e2e2e"
KEYBOX_HOVER = "#3a3a3a"
KEYBOX_ACTIVE = "#454545"
KEYBOX_MIN = 38
KEYBOX_PAD = 18
CARD = "#1c1c1c"
CARD_H = 78
CARD_RADIUS = 10

# ---------- Mode card ----------
MODES = ["DB", "Rev", "Tact"]
MODE_H = 182
NUM_BOX = 34
NUM_MAX_DIGITS = 1   # highest number is 9
DIVIDER = "#363636"

# ---------- Run time + configs ----------
RUN_MIN, RUN_MAX, RUN_DEFAULT = 0.05, 1.0, 0.5
TIME_H = 64
RELOAD_H = 64
ROW_H = 50
MAX_CONFIGS = 50
NAME_MAX = 20
CFG_BTN = "#2a2a2a"
CFG_BTN_HOVER = "#3a3a3a"
DANGER = "#8f2f2f"
DANGER_HOVER = "#a83a3a"
ERROR_COLOR = "#e5737a"

# ---------- Adjustments tab ----------
SEQ_H = 118
SEQ_MAX = 8                       # most guns allowed in the sequence
SLIDER_W = 190
TYPE_RATE_DEFAULT = 26            # number presses per second
CLICK_RATE_DEFAULT = 32           # auto-clicks per second

# ---------- Sidebar ----------
SIDEBAR = "#0f0f0f"
HILITE = "#262626"
TABS = ["Gun Macro", "Extras", "Adjustments", "Config"]
CONFIG_TAB = TABS.index("Config")
SB_W = 190           # sidebar width
TAB_H = 44
TAB_GAP = 8
TAB_TOP = 24

if IS_WIN:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("gunmacro.app.1")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

GWL_EXSTYLE = -20
GWL_STYLE = -16
WS_MINIMIZEBOX = 0x00020000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080


# ---------- Input sending (Windows SendInput) ----------
TYPE_DELAY = 0.030      # pause between number presses (seconds)  ~ 25/sec
KEY_HOLD = 0.008        # how long each key stays held down
CLICK_DELAY = 0.025     # pause between clicks (seconds)           ~ 30/sec
CLICK_HOLD = 0.006      # how long each click stays held down

RELOAD_VK = 0x52         # the R key
RELOAD_TAPS = 2          # R presses after each number
RELOAD_DELAY = 0.012     # pause between reload key presses (seconds)
RELOAD_FRACTION = 0.35   # reload phase = last 35% of the run (at least one full pass)

JITTER = 0.35            # random jitter: each delay varies by +/- 35%
MID_RELOAD_DELAY = 0.08  # mid reload: one R tap every 80ms (~12/sec)
HOLD_MAX = 3600          # hold mode: safety cap on one run (seconds)

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# ---------- Mouse buttons usable as the keybind ----------
# Left click is excluded: the UI and the auto-clicker both use it.
MOUSE_NAMES = {
    0x02: "Right Click",
    0x04: "Middle Click",
    0x05: "Mouse 4",
    0x06: "Mouse 5",
}
MOUSE_VKS = tuple(MOUSE_NAMES)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _send(inp):
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def key_tap(vk):
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    for flags in (KEYEVENTF_SCANCODE, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP):
        inp = INPUT(type=1)
        inp.u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        _send(inp)
        if flags == KEYEVENTF_SCANCODE:
            time.sleep(KEY_HOLD)


def mouse_click():
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        inp = INPUT(type=0)
        inp.u.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
        _send(inp)
        if flag == MOUSEEVENTF_LEFTDOWN:
            time.sleep(CLICK_HOLD)


def key_is_down(vk):
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class MacroRunner:
    """Cycles the hotbar numbers and auto-clicks for a set time, then stops."""

    def __init__(self):
        self.running = False
        self.last_end = 0.0                  # when the last run finished (for cooldown)
        self._stop = threading.Event()

    def start(self, seq_vks, reload_vks, seconds, auto_reload=False, jitter=False,
              mid_reload=False, type_delay=TYPE_DELAY, click_delay=CLICK_DELAY,
              watch_hwnd=None):
        """seq_vks: keys pressed in order (repeats allowed).
        reload_vks: every gun number, used by the reload pass.
        watch_hwnd: stop the run if this window loses focus."""
        if self.running or not seq_vks:
            return
        self.running = True
        self._stop.clear()
        end = time.perf_counter() + seconds

        # when the reload phase begins (None = auto reload off)
        reload_at = None
        if auto_reload:
            one_pass = len(reload_vks) * (1 + RELOAD_TAPS) * (KEY_HOLD + RELOAD_DELAY)
            window = min(seconds, max(RELOAD_FRACTION * seconds, one_pass * 1.2))
            reload_at = end - window
        self._vks = reload_vks
        self._end = end
        self._reload_at = reload_at

        def alive():
            return time.perf_counter() < self._end and not self._stop.is_set()

        def reloading():
            return self._reload_at is not None and time.perf_counter() >= self._reload_at

        def pause(base):
            time.sleep(base * random.uniform(1 - JITTER, 1 + JITTER) if jitter else base)

        def typer():
            i = 0
            while alive():
                if reloading():
                    # near the end: each number, then R a few times
                    for vk in reload_vks:
                        if not alive():
                            break
                        key_tap(vk)
                        pause(RELOAD_DELAY)
                        for _ in range(RELOAD_TAPS):
                            if not alive():
                                break
                            key_tap(RELOAD_VK)
                            pause(RELOAD_DELAY)
                    continue
                key_tap(seq_vks[i % len(seq_vks)])
                i += 1
                pause(type_delay)

        def clicker():
            while alive():
                if reloading():              # no clicking while reloading
                    time.sleep(0.005)
                    continue
                mouse_click()
                pause(click_delay)

        def reloader():
            # mid reload runs on its own thread so the number/click speed is untouched
            while alive():
                if reloading():              # the release pass handles R itself
                    time.sleep(0.005)
                    continue
                key_tap(RELOAD_VK)
                pause(MID_RELOAD_DELAY)

        def watcher():
            # stop the moment another window takes focus
            while alive():
                if ctypes.windll.user32.GetForegroundWindow() != watch_hwnd:
                    self.stop()
                    return
                time.sleep(0.01)

        def run():
            threads = [threading.Thread(target=typer, daemon=True),
                       threading.Thread(target=clicker, daemon=True)]
            if mid_reload:
                threads.append(threading.Thread(target=reloader, daemon=True))
            if watch_hwnd is not None:
                threads.append(threading.Thread(target=watcher, daemon=True))
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.last_end = time.perf_counter()
            self.running = False

        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def release(self, auto_reload):
        """Hold mode: key let go. With auto reload, finish with one reload pass."""
        if not self.running:
            return
        if not auto_reload:
            self.stop()
            return
        now = time.perf_counter()
        one_pass = len(self._vks) * (1 + RELOAD_TAPS) * (KEY_HOLD + RELOAD_DELAY)
        self._reload_at = now                  # clicking stops, numbers + R start
        self._end = now + one_pass * 1.2       # ends after one full pass


MODIFIER_KEYSYMS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Caps_Lock", "Num_Lock", "Scroll_Lock", "Win_L", "Win_R",
    "Super_L", "Super_R", "Meta_L", "Meta_R", "ISO_Level3_Shift",
}
KEY_NAMES = {
    "Return": "Enter", "space": "Space", "Escape": "Esc",
    "BackSpace": "Backspace", "Prior": "Page Up", "Next": "Page Down",
    "Caps_Lock": "Caps Lock",
}
SHIFT_MASK = 0x0001
CTRL_MASK = 0x0004
ALT_MASK = 0x20000 if IS_WIN else 0x0008


def key_name(keysym, keycode):
    """Readable name for a pressed key (letters, numbers, F-keys, etc.)."""
    if IS_WIN:
        if 0x30 <= keycode <= 0x39 or 0x41 <= keycode <= 0x5A:
            return chr(keycode)                      # 0-9, A-Z (ignores Shift symbols)
        if 0x60 <= keycode <= 0x69:
            return f"Num {keycode - 0x60}"
    if keysym in KEY_NAMES:
        return KEY_NAMES[keysym]
    if keysym.startswith("KP_"):
        return "Num " + keysym[3:]
    if len(keysym) == 1:
        return keysym.upper()
    return keysym


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError:
        return False


def safe_name(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip().rstrip(". ")
    return text[:NAME_MAX]


def cfg_path(name):
    return os.path.join(CONFIG_DIR, name + ".json")


def list_configs():
    try:
        files = [f for f in os.listdir(CONFIG_DIR) if f.lower().endswith(".json")]
        files.sort(key=lambda f: (os.path.getctime(os.path.join(CONFIG_DIR, f)), f.lower()))
    except OSError:
        return []
    return [f[:-5] for f in files]


def mix(c1, c2, t):
    """Blend hex color c1 toward c2 by t (0..1)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rr_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    return canvas.create_polygon(rr_points(x1, y1, x2, y2, r), smooth=True, **kw)


def ease_in_out(t):
    return t * t * (3 - 2 * t)


def parse_version(text):
    nums = [int(n) for n in re.findall(r"\d+", str(text))][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def _clean_version(raw):
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            raw = str(data.get("version") or data.get("latest") or "")
    except ValueError:
        pass
    raw = raw.splitlines()[0].strip() if raw else ""
    return raw.lstrip("vV") if re.search(r"\d", raw) else None


def fetch_latest_version():
    """('ok', '1.2.0') or ('error', None). Runs on a background thread."""
    try:
        if UPDATE_URL:
            with urllib.request.urlopen(UPDATE_URL, timeout=4) as r:
                raw = r.read(4096).decode("utf-8", "ignore")
        else:
            with open(UPDATE_FILE, "r", encoding="utf-8") as f:
                raw = f.read(4096)
    except (OSError, ValueError):
        return ("error", None)
    v = _clean_version(raw)
    return ("ok", v) if v else ("error", None)


def load_last_used():
    d = read_json(META_FILE)
    try:
        return datetime.fromisoformat(d["last_used"])
    except (TypeError, KeyError, ValueError):
        return None


def touch_last_used():
    write_json(META_FILE, {"last_used": datetime.now().isoformat(timespec="seconds")})


def describe_last_used(dt, now=None):
    """'Last used: Yesterday at 3:42 PM' / 'Tuesday at ...' / 'Tuesday, Oct 6 at ...'."""
    if dt is None:
        return "Welcome! First time here"
    now = now or datetime.now()
    days = (now.date() - dt.date()).days
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if days <= 0:
        day = "Today"
    elif days == 1:
        day = "Yesterday"
    elif days < 7:
        day = dt.strftime("%A")
    elif dt.year == now.year:
        day = f"{dt.strftime('%A, %b')} {dt.day}"
    else:
        day = f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    return f"Last used: {day} at {clock}"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg=BG)
        self.width, self.height = 520, 345
        self._drag_x = self._drag_y = 0

        self._load_icon()
        self.overrideredirect(True)
        self._build_top_bar()
        self._build_body()
        self._center()
        self._start_update_check()

        self.after(50, self._setup_window)

    # ---------- Icon ----------
    def _load_icon(self):
        for path in ICON_CANDIDATES:
            if os.path.exists(path):
                try:
                    self._icon_img = tk.PhotoImage(file=path)
                    self.iconphoto(True, self._icon_img)
                    return
                except Exception as e:
                    print("Could not load icon:", e)
        print("Icon.png not found in:", ICON_CANDIDATES)

    # ---------- Windows integration ----------
    def _hwnd(self):
        return ctypes.windll.user32.GetParent(self.winfo_id())

    def _setup_window(self):
        if not IS_WIN:
            return
        hwnd = self._hwnd()
        user32 = ctypes.windll.user32
        base = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, base | WS_MINIMIZEBOX)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        self.withdraw()
        self.after(10, self.deiconify)
        self._round_corners()

    def _round_corners(self, w=None, h=None, redraw=True):
        if not IS_WIN:
            return
        if w is None or h is None:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, w + 1, h + 1, RADIUS * 2, RADIUS * 2
        )
        ctypes.windll.user32.SetWindowRgn(self._hwnd(), rgn, redraw)

    def _fade_close(self):
        if getattr(self, "_closing", False):
            return
        self._closing = True
        if getattr(self, "_main_built", False):
            self._save_session()
            touch_last_used()                  # remember when you last used it
        self._poll_stop = True
        if getattr(self, "runner", None):
            self.runner.stop()
        steps = 20

        def step(i=0):
            alpha = 1 - i / steps
            try:
                self.attributes("-alpha", max(alpha, 0))
            except tk.TclError:
                self.destroy()
                return
            if i < steps:
                self.after(15, lambda: step(i + 1))
            else:
                self.destroy()

        step()

    def _minimize(self):
        # Native minimize: no overrideredirect toggling, so no white flash
        if IS_WIN:
            ctypes.windll.user32.ShowWindow(self._hwnd(), 6)  # SW_MINIMIZE
        else:
            self.overrideredirect(False)
            self.iconify()

    # ---------- Top bar ----------
    def _bar_button(self, parent, text, command, hover):
        b = tk.Label(parent, text=text, bg=BAR, fg=TEXT,
                     font=("Segoe UI", 11), width=5, cursor="hand2")
        b.pack(side="right", fill="y")
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.config(bg=hover))
        b.bind("<Leave>", lambda e: b.config(bg=BAR))

    def _build_top_bar(self):
        bar = tk.Frame(self, bg=BAR, height=38)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        title = tk.Label(bar, text=APP_NAME, bg=BAR, fg=TEXT,
                         font=("Segoe UI", 10, "bold"))
        title.pack(side="left", padx=16)

        self._bar_button(bar, "✕", self._fade_close, CLOSE_HOVER)
        self._bar_button(bar, "—", self._minimize, BAR_BTN_HOVER)

        for w in (bar, title):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

    def _start_drag(self, e):
        self._drag_x, self._drag_y = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _on_drag(self, e):
        self.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    # ---------- Body ----------
    def _build_body(self):
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        self.heading = tk.Label(body, text="Gun macro", bg=BG, fg=TEXT,
                                font=("Segoe UI Semibold", 28))
        self.heading.pack(pady=(40, 2))
        self.subtitle = tk.Label(body, text="Built for zee hood", bg=BG, fg=SUBTEXT,
                                 font=("Segoe UI", 10))
        self.subtitle.pack(pady=(0, 4))

        # version + update status
        self.ver_row = tk.Frame(body, bg=BG)
        self.ver_row.pack(pady=(0, 22))
        self.ver_label = tk.Label(self.ver_row, text=f"v{VERSION}", bg=BG, fg=SUBTEXT,
                                  font=("Segoe UI", 9))
        self.ver_label.pack(side="left")
        self.update_label = tk.Label(self.ver_row, text="", bg=BG, fg=SUBTEXT,
                                     font=("Segoe UI", 9))
        self.update_label.pack(side="left")

        bw, bh = 360, 50
        cv = tk.Canvas(body, width=bw, height=bh, bg=BG,
                       highlightthickness=0, cursor="hand2")
        cv.pack()
        shape = rounded_rect(cv, 1, 1, bw - 1, bh - 1, BTN_RADIUS,
                             fill=BTN, outline=BTN)
        btn_text = cv.create_text(bw / 2, bh / 2, text="Continue", fill=BTN_TEXT,
                                  font=("Segoe UI Semibold", 13))
        self.btn_cv, self.btn_shape, self.btn_text = cv, shape, btn_text
        self.body = body

        def hover(color):
            cv.itemconfig(shape, fill=color, outline=color)

        cv.bind("<Enter>", lambda e: hover(BTN_HOVER))
        cv.bind("<Leave>", lambda e: hover(BTN))
        cv.bind("<Button-1>", lambda e: self.on_continue())

        # when you last used it
        self._prev_used = load_last_used()
        self.last_used_label = tk.Label(body, text=describe_last_used(self._prev_used),
                                        bg=BG, fg=SUBTEXT, font=("Segoe UI", 9))
        self.last_used_label.pack(pady=(14, 0))

        # clickable credit: copies the Discord name
        self._copied = False
        self._copy_job = None
        self.credit = tk.Label(body, text=f"Made by reruinz  \u00b7  Discord {DISCORD_NAME}",
                               bg=BG, fg=SUBTEXT, font=("Segoe UI", 9), cursor="hand2")
        self.credit.pack(side="bottom", pady=(0, 14))
        self.credit.bind("<Enter>", lambda e: None if self._copied else self.credit.config(fg=TEXT))
        self.credit.bind("<Leave>", lambda e: None if self._copied else self.credit.config(fg=SUBTEXT))
        self.credit.bind("<Button-1>", lambda e: self._copy_credit())

    # ---------- Splash extras: copy credit + update check ----------
    def _copy_credit(self):
        text = DISCORD_NAME
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()                      # lets the clipboard keep it after we close
        except tk.TclError:
            pass
        if IS_WIN:                             # clip.exe also keeps it after the app exits
            try:
                subprocess.run(["clip"], input=text.encode("ascii", "ignore"),
                               creationflags=0x08000000, check=False)
            except OSError:
                pass
        if self._copy_job:
            self.after_cancel(self._copy_job)
        self._copied = True
        self.credit.config(text=f"Copied {text} to clipboard", fg=COPY_OK)

        def restore():
            self._copied = False
            self._copy_job = None
            try:
                self.credit.config(text=f"Made by reruinz  \u00b7  Discord {DISCORD_NAME}",
                                   fg=SUBTEXT)
            except tk.TclError:
                pass

        self._copy_job = self.after(1600, restore)

    def _start_update_check(self):
        self._update_result = None
        if not (UPDATE_URL or os.path.isfile(UPDATE_FILE)):
            return                             # nothing configured: show nothing
        self.update_label.config(text="  \u00b7  Checking for updates...", fg=SUBTEXT)

        def work():
            self._update_result = fetch_latest_version()

        threading.Thread(target=work, daemon=True).start()
        self.after(150, self._poll_update)

    def _poll_update(self):
        if getattr(self, "_closing", False):
            return
        res = self._update_result
        if res is None:
            self.after(150, self._poll_update)
            return
        kind, latest = res
        if kind == "ok" and parse_version(latest) > parse_version(VERSION):
            text, color = f"  \u00b7  Update available (v{latest})", UPDATE_NEW
        elif kind == "ok":
            text, color = "  \u00b7  Up to date", UPDATE_OK
        else:
            text, color = "  \u00b7  Couldn't check for updates", SUBTEXT
        try:
            self.update_label.config(text=text, fg=color)
        except tk.TclError:
            pass

    def _set_frame(self, x, y, w, h):
        """Resize/move in one native call; region is updated first so the
        window never gets clipped by an outdated shape."""
        if IS_WIN:
            self._round_corners(w, h, redraw=False)
            ctypes.windll.user32.MoveWindow(self._hwnd(), x, y, w, h, True)
        else:
            self.geometry(f"{w}x{h}+{x}+{y}")

    def on_continue(self):
        if getattr(self, "_animating", False):
            return
        self._animating = True
        touch_last_used()                           # starting counts as using it
        self._preloaded = read_json(SESSION_FILE)   # real work while the bar runs

        # Phase 0: the button turns into a loading bar
        cv = self.btn_cv
        for seq in ("<Enter>", "<Leave>", "<Button-1>"):
            cv.unbind(seq)
        cv.config(cursor="arrow")
        cv.delete("all")
        bw = int(cv.cget("width"))
        cv.create_text(bw / 2, 12, text="Loading...", fill=SUBTEXT,
                       font=("Segoe UI", 10), tags="barlabel")
        rounded_rect(cv, 0, 30, bw, 38, 4, fill=KEYBOX, outline=KEYBOX, tags="bartrack")
        load_steps = 44

        def load(i=0):
            t = ease_in_out(i / load_steps)
            cv.delete("barfill")
            if i:
                rounded_rect(cv, 0, 30, max(9, bw * t), 38, 4, fill=BTN, outline=BTN,
                             tags="barfill")
            if i < load_steps:
                self.after(16, lambda: load(i + 1))
            else:
                fade()

        # Phase 1: fade the content out (window size stays fixed = no flicker)
        fade_steps = 14
        labels = [self.heading, self.subtitle, self.ver_label, self.update_label,
                  self.last_used_label, self.credit]

        def fade(i=0):
            if i == 0:
                self._fade_bases = [(w, str(w.cget("fg"))) for w in labels]
            f = i / fade_steps
            for w, base in self._fade_bases:
                w.config(fg=mix(base, BG, f))
            cv.itemconfig("barlabel", fill=mix(SUBTEXT, BG, f))
            cv.itemconfig("bartrack", fill=mix(KEYBOX, BG, f), outline=mix(KEYBOX, BG, f))
            cv.itemconfig("barfill", fill=mix(BTN, BG, f), outline=mix(BTN, BG, f))
            if i < fade_steps:
                self.after(16, lambda: fade(i + 1))
            else:
                for wdg in (self.heading, self.subtitle, self.ver_row, self.btn_cv,
                            self.last_used_label, self.credit):
                    wdg.pack_forget()
                self.update_idletasks()
                grow()

        # Phase 2: grow from the center on every side (empty body = smooth)
        def grow():
            x0, y0 = self.winfo_x(), self.winfo_y()
            w0, h0 = self.winfo_width(), self.winfo_height()
            cx, cy = x0 + w0 / 2, y0 + h0 / 2
            w1 = min(int(w0 * 1.55), self.winfo_screenwidth() - 40)
            h1 = min(int(h0 * 1.8), self.winfo_screenheight() - 80)
            steps = 36

            def ease(t):
                return t * t * (3 - 2 * t)

            def step(i=0):
                t = ease(i / steps)
                w = int(w0 + (w1 - w0) * t)
                h = int(h0 + (h1 - h0) * t)
                self._set_frame(int(cx - w / 2), int(cy - h / 2), w, h)
                if i < steps:
                    self.after(14, lambda: step(i + 1))
                else:
                    self.width, self.height = w, h
                    self._animating = False
                    self._build_main()

            step()

        load()

    # ---------- Main screen (sidebar + tabs) ----------
    def _tab_y(self, idx):
        return TAB_TOP + idx * (TAB_H + TAB_GAP)

    def _build_main(self):
        self.sidebar = tk.Canvas(self.body, width=SB_W, bg=SIDEBAR,
                                 highlightthickness=0)
        self.sidebar.pack(side="left", fill="y")

        self.content = tk.Frame(self.body, bg=BG)
        self.content.pack(side="right", fill="both", expand=True)
        self.page_title = tk.Label(self.content, text=TABS[0], bg=BG, fg=TEXT,
                                   font=("Segoe UI Semibold", 22))
        self.page_title.pack(anchor="nw", padx=32, pady=(28, 4))
        self.page_sub = tk.Label(self.content, text="", bg=BG, fg=SUBTEXT,
                                 font=("Segoe UI", 10))
        self.page_sub.pack(anchor="nw", padx=34)
        self._build_mode_card()
        self._build_keybind_row()
        self._build_time_row()
        self._build_reload_row()
        self._build_extras()
        self._build_tuning()
        self._build_config_view()

        self.active_tab = 0
        self._hl_job = None
        self._hl_y = self._tab_y(0)
        y = self._hl_y

        self._hl = rounded_rect(self.sidebar, 12, y, SB_W - 12, y + TAB_H, 8,
                                fill=HILITE, outline=HILITE)
        self._bar = self.sidebar.create_rectangle(
            12, y + 11, 15, y + TAB_H - 11, fill=BTN, outline=BTN)

        self._tab_items = []
        for i, name in enumerate(TABS):
            item = self.sidebar.create_text(
                38, self._tab_y(i) + TAB_H / 2, text=name, anchor="w",
                fill=TEXT if i == 0 else SUBTEXT,
                font=("Segoe UI Semibold", 11))
            self._tab_items.append(item)

        # credits at the bottom of the sidebar
        self._credit1 = self.sidebar.create_text(
            SB_W / 2, 0, text="Made by reruinz", fill=SUBTEXT,
            font=("Segoe UI Semibold", 9))
        self._credit2 = self.sidebar.create_text(
            SB_W / 2, 0, text="Discord is @reruinz", fill=SUBTEXT,
            font=("Segoe UI", 9))
        self.sidebar.bind("<Configure>", self._place_credits)

        self.bind_all("<Button-1>", self._on_global_click, add="+")
        self._start_macro_listener()
        self.sidebar.bind("<Button-1>", self._on_sidebar_click)
        self.sidebar.bind("<Motion>", self._on_sidebar_motion)

        # restore what you had last time, then keep saving it
        self._saved_snapshot = None
        data = getattr(self, "_preloaded", None)
        if not isinstance(data, dict):
            data = read_json(SESSION_FILE) or {}
        self._apply_snapshot(data)
        self._main_built = True
        self._autosave_tick()

    # ---------- Settings snapshot / session ----------
    def _snapshot(self):
        return {
            "numbers": list(self.gun_numbers),
            "keybind": self.keybind,
            "vk": self.keybind_vk,
            "mods": list(self.keybind_mods),
            "run_time": self.run_time,
            "auto_reload": self.auto_reload,
            "hold_mode": self.hold_mode,
            "mid_reload": self.mid_reload,
            "roblox_only": self.roblox_only,
            "jitter": self.jitter,
            "panic": self.panic,
            "panic_vk": self.panic_vk,
            "panic_mods": list(self.panic_mods),
            "stop_on_blur": self.stop_on_blur,
            "sequence": list(self.sequence),
            "type_rate": self.type_rate,
            "click_rate": self.click_rate,
            "cooldown": self.cooldown,
        }

    def _apply_snapshot(self, d):
        if not isinstance(d, dict):
            return
        self._capturing = False
        self._panic_capturing = False
        self._time_active = False
        self._num_active = None

        nums = d.get("numbers")
        if isinstance(nums, list) and len(nums) == len(MODES):
            clean, seen = [], set()
            for n in nums:
                n = str(n)
                if not (len(n) == 1 and n.isdigit()) or n in seen:
                    n = ""
                if n:
                    seen.add(n)
                clean.append(n)
            self.gun_numbers = clean

        kb = d.get("keybind")
        if isinstance(kb, str) and kb:
            self.keybind = kb
            vk = d.get("vk")
            self.keybind_vk = vk if isinstance(vk, int) and not isinstance(vk, bool) else None
            mods = d.get("mods")
            if isinstance(mods, list) and len(mods) == 3:
                self.keybind_mods = tuple(bool(m) for m in mods)
            else:
                self.keybind_mods = (False, False, False)

        rt = d.get("run_time")
        if isinstance(rt, (int, float)) and not isinstance(rt, bool):
            self.run_time = round(min(RUN_MAX, max(RUN_MIN, float(rt))), 3)

        ar = d.get("auto_reload")
        if isinstance(ar, bool):
            self._set_reload(ar, animate=False)

        for key in ("hold_mode", "mid_reload", "roblox_only", "jitter", "stop_on_blur"):
            v = d.get(key)
            if isinstance(v, bool):
                self._set_toggle(key, v, animate=False)
        if not self.hold_mode and self.mid_reload:
            self._set_toggle("mid_reload", False, animate=False)

        seq = d.get("sequence")
        if (isinstance(seq, list) and 1 <= len(seq) <= SEQ_MAX
                and all(isinstance(i, int) and not isinstance(i, bool)
                        and 0 <= i < len(MODES) for i in seq)):
            self.sequence = list(seq)
        for key in self._sliders:
            v = d.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self._set_slider(key, v)
        self._draw_sequence()

        pk = d.get("panic")
        if isinstance(pk, str) and pk:
            self.panic = pk
            pvk = d.get("panic_vk")
            self.panic_vk = pvk if isinstance(pvk, int) and not isinstance(pvk, bool) else None
            pm = d.get("panic_mods")
            if isinstance(pm, list) and len(pm) == 3:
                self.panic_mods = tuple(bool(m) for m in pm)
            else:
                self.panic_mods = (False, False, False)
        self._draw_panic()

        self._draw_mode_card()
        self._draw_keybox(self.keybind)
        self._draw_timebox()

    def _save_session(self):
        write_json(SESSION_FILE, self._snapshot())

    def _autosave_tick(self):
        if getattr(self, "_closing", False):
            return
        snap = self._snapshot()
        if snap != self._saved_snapshot and write_json(SESSION_FILE, snap):
            self._saved_snapshot = snap
        self.after(1000, self._autosave_tick)

    # ---------- Run time row ----------
    def _build_time_row(self):
        self.run_time = RUN_DEFAULT
        self._time_active = False
        self._time_buf = ""
        self._time_hover = False
        self._time_warn_job = None
        self._timebox_font = tkfont.Font(family="Segoe UI Semibold", size=10)

        self.time_row = tk.Canvas(self.content, height=TIME_H, bg=BG,
                                  highlightthickness=0)
        self.time_row.pack(side="bottom", fill="x", padx=32, pady=(0, 10))
        self.time_row.create_text(22, TIME_H / 2, text="Run Time", anchor="w",
                                  fill=TEXT, font=("Segoe UI Semibold", 12))
        self._time_warn = self.time_row.create_text(
            0, TIME_H / 2, text="", fill=ERROR_COLOR,
            font=("Segoe UI Semibold", 10))

        self.timebox = tk.Canvas(self.time_row, width=KEYBOX_MIN, height=KEYBOX_MIN,
                                 bg=CARD, highlightthickness=0, cursor="hand2")
        self._timebox_win = self.time_row.create_window(
            0, TIME_H / 2, window=self.timebox, anchor="e")

        self.time_row.bind("<Configure>", self._layout_time_row)
        self.timebox.bind("<Button-1>", self._on_timebox_click)
        self.timebox.bind("<Enter>", lambda e: self._set_time_hover(True))
        self.timebox.bind("<Leave>", lambda e: self._set_time_hover(False))
        self.bind("<KeyPress>", self._on_time_key, add="+")
        self._draw_timebox()

    def _layout_time_row(self, e):
        c = self.time_row
        c.delete("card")
        rounded_rect(c, 1, 1, e.width - 1, TIME_H - 1, CARD_RADIUS,
                     fill=CARD, outline=CARD, tags="card")
        c.tag_lower("card")
        c.coords(self._timebox_win, e.width - 20, TIME_H / 2)
        c.coords(self._time_warn, e.width / 2, TIME_H / 2)

    def _set_time_hover(self, state):
        self._time_hover = state
        self._draw_timebox()

    def _draw_timebox(self):
        if self._time_active:
            text = self._time_buf or "..."
        else:
            text = f"{self.run_time:g}s"
        w = max(KEYBOX_MIN + 16, self._timebox_font.measure(text) + KEYBOX_PAD)
        if self._time_active:
            color = KEYBOX_ACTIVE
        elif self._time_hover:
            color = KEYBOX_HOVER
        else:
            color = KEYBOX
        self.timebox.config(width=w, height=KEYBOX_MIN)
        self.timebox.delete("all")
        rounded_rect(self.timebox, 1, 1, w - 1, KEYBOX_MIN - 1, 7,
                     fill=color, outline=color)
        self.timebox.create_text(w / 2, KEYBOX_MIN / 2, text=text, fill=TEXT,
                                 font=self._timebox_font)

    def _warn_time(self, text, ms=1500):
        if self._time_warn_job:
            self.after_cancel(self._time_warn_job)
        self.time_row.itemconfig(self._time_warn, text=text)
        self._time_warn_job = self.after(
            ms, lambda: self.time_row.itemconfig(self._time_warn, text=""))

    def _on_timebox_click(self, _e):
        if self._time_active:                 # click again = confirm
            self._finish_time()
            return
        if self._capturing:
            self._capturing = False
            self._draw_keybox(self.keybind)
        if self._num_active is not None:
            self._finish_number()
            self._draw_mode_card()
        self._time_active = True
        self._time_buf = ""
        self._draw_timebox()
        self.focus_force()

    def _finish_time(self):
        if not self._time_active:
            return
        self._time_active = False
        buf = self._time_buf
        if buf:
            try:
                v = float(buf)
            except ValueError:
                self._warn_time("Enter a valid number")
            else:
                if v < RUN_MIN:
                    v = RUN_MIN
                    self._warn_time("Min run time is 0.05 seconds")
                elif v > RUN_MAX:
                    v = RUN_MAX
                    self._warn_time("Max run time is 1 second")
                self.run_time = round(v, 3)
        self._draw_timebox()

    def _on_time_key(self, e):
        if not self._time_active:
            return
        if e.keysym == "Return":
            self._finish_time()
            return "break"
        if e.keysym == "Escape":
            self._time_active = False
        elif e.keysym == "BackSpace":
            self._time_buf = self._time_buf[:-1]
        elif e.char and e.char in "0123456789.":
            if e.char == "." and "." in self._time_buf:
                return "break"
            if len(self._time_buf) < 5:
                self._time_buf += e.char
        else:
            return "break"
        self._draw_timebox()
        return "break"

    # ---------- Auto reload row ----------
    def _build_reload_row(self):
        self.auto_reload = False
        self._reload_p = 0.0              # toggle animation progress 0..1
        self._reload_job = None

        self.reload_row = tk.Canvas(self.content, height=RELOAD_H, bg=BG,
                                    highlightthickness=0)
        self.reload_row.pack(side="bottom", fill="x", padx=32, pady=(0, 10))
        self.reload_row.create_text(22, RELOAD_H / 2 - 10, text="Auto Reload",
                                    anchor="w", fill=TEXT,
                                    font=("Segoe UI Semibold", 12))
        self.reload_row.create_text(22, RELOAD_H / 2 + 12,
                                    text="Spams R on every number near the end of the run",
                                    anchor="w", fill=SUBTEXT, font=("Segoe UI", 9))

        self.reload_sw = tk.Canvas(self.reload_row, width=46, height=24, bg=CARD,
                                   highlightthickness=0, cursor="hand2")
        self._reload_win = self.reload_row.create_window(
            0, RELOAD_H / 2, window=self.reload_sw, anchor="e")
        self.reload_row.bind("<Configure>", self._layout_reload_row)
        self.reload_sw.bind("<Button-1>", lambda e: self._set_reload(not self.auto_reload))
        self._draw_reload_switch()

    def _layout_reload_row(self, e):
        c = self.reload_row
        c.delete("card")
        rounded_rect(c, 1, 1, e.width - 1, RELOAD_H - 1, CARD_RADIUS,
                     fill=CARD, outline=CARD, tags="card")
        c.tag_lower("card")
        c.coords(self._reload_win, e.width - 20, RELOAD_H / 2)

    def _draw_reload_switch(self):
        p = self._reload_p
        c = self.reload_sw
        c.delete("all")
        track = mix(KEYBOX, BTN, p)
        rounded_rect(c, 1, 1, 45, 23, 11, fill=track, outline=track)
        x = 12 + 22 * p
        knob = mix("#bdbdbd", "#111111", p)
        c.create_oval(x - 8, 12 - 8, x + 8, 12 + 8, fill=knob, outline=knob)

    def _set_reload(self, value, animate=True):
        self.auto_reload = bool(value)
        if self._reload_job:
            self.after_cancel(self._reload_job)
            self._reload_job = None
        target = 1.0 if self.auto_reload else 0.0
        if not animate:
            self._reload_p = target
            self._draw_reload_switch()
            return
        start = self._reload_p
        steps = 10

        def step(i=0):
            self._reload_p = start + (target - start) * ease_in_out(i / steps)
            self._draw_reload_switch()
            if i < steps:
                self._reload_job = self.after(12, lambda: step(i + 1))
            else:
                self._reload_job = None

        step()

    # ---------- Extras tab ----------
    def _build_extras(self):
        self.extras_view = tk.Frame(self.content, bg=BG)   # packed only on the Extras tab
        self.hold_mode = False
        self.mid_reload = False
        self.roblox_only = False
        self.jitter = False
        self.stop_on_blur = False
        self.panic = "None"
        self.panic_vk = None
        self.panic_mods = (False, False, False)
        self._panic_capturing = False
        self._panic_hover = False
        self._panic_font = tkfont.Font(family="Segoe UI Semibold", size=10)
        self._toggles = {}

        tk.Frame(self.extras_view, bg=BG, height=14).pack()
        self._make_toggle("hold_mode", "Hold Mode",
                          "Runs while the key is held; on release it reloads if Auto Reload is on")
        self._make_toggle("mid_reload", "Auto Mid Reload",
                          "Spams R while it shoots. Needs Hold Mode on")
        self._make_toggle("roblox_only", "Roblox Only",
                          "Only fires when the Roblox window is in front")
        self._make_toggle("jitter", "Random Jitter",
                          "Randomizes the delays a little on every press")
        self._make_toggle("stop_on_blur", "Stop On Focus Loss",
                          "Cancels a run if you switch away from the window it started in")
        self._build_panic_row()

    def _make_toggle(self, key, title, desc):
        row = tk.Canvas(self.extras_view, height=RELOAD_H, bg=BG, highlightthickness=0)
        row.pack(side="top", fill="x", padx=32, pady=(0, 10))
        row.create_text(22, RELOAD_H / 2 - 10, text=title, anchor="w", fill=TEXT,
                        font=("Segoe UI Semibold", 12))
        row.create_text(22, RELOAD_H / 2 + 12, text=desc, anchor="w", fill=SUBTEXT,
                        font=("Segoe UI", 9))
        sw = tk.Canvas(row, width=46, height=24, bg=CARD, highlightthickness=0,
                       cursor="hand2")
        win = row.create_window(0, RELOAD_H / 2, window=sw, anchor="e")

        def layout(e):
            row.delete("card")
            rounded_rect(row, 1, 1, e.width - 1, RELOAD_H - 1, CARD_RADIUS,
                         fill=CARD, outline=CARD, tags="card")
            row.tag_lower("card")
            row.coords(win, e.width - 20, RELOAD_H / 2)

        row.bind("<Configure>", layout)
        sw.bind("<Button-1>", lambda e: self._on_toggle_click(key))
        self._toggles[key] = {"p": 0.0, "job": None, "sw": sw}
        self._draw_toggle(key)

    def _on_toggle_click(self, key):
        if key == "mid_reload" and not self.hold_mode:
            self._flash("Turn on Hold Mode first", error=True)
            return
        self._set_toggle(key, not getattr(self, key))
        if key == "hold_mode" and not self.hold_mode and self.mid_reload:
            self._set_toggle("mid_reload", False)     # only works with hold mode

    def _draw_toggle(self, key):
        st = self._toggles[key]
        p, c = st["p"], st["sw"]
        c.delete("all")
        track = mix(KEYBOX, BTN, p)
        rounded_rect(c, 1, 1, 45, 23, 11, fill=track, outline=track)
        x = 12 + 22 * p
        knob = mix("#bdbdbd", "#111111", p)
        c.create_oval(x - 8, 4, x + 8, 20, fill=knob, outline=knob)

    def _set_toggle(self, key, value, animate=True):
        setattr(self, key, bool(value))
        st = self._toggles[key]
        if st["job"]:
            self.after_cancel(st["job"])
            st["job"] = None
        target = 1.0 if value else 0.0
        if not animate:
            st["p"] = target
            self._draw_toggle(key)
            return
        start = st["p"]
        steps = 10

        def step(i=0):
            st["p"] = start + (target - start) * ease_in_out(i / steps)
            self._draw_toggle(key)
            if i < steps:
                st["job"] = self.after(12, lambda: step(i + 1))
            else:
                st["job"] = None

        step()

    def _build_panic_row(self):
        row = tk.Canvas(self.extras_view, height=RELOAD_H, bg=BG, highlightthickness=0)
        row.pack(side="top", fill="x", padx=32, pady=(0, 10))
        row.create_text(22, RELOAD_H / 2 - 10, text="Panic Key", anchor="w", fill=TEXT,
                        font=("Segoe UI Semibold", 12))
        row.create_text(22, RELOAD_H / 2 + 12, anchor="w", fill=SUBTEXT,
                        text="Instantly kills a run. Esc while setting it clears it",
                        font=("Segoe UI", 9))
        self.panic_box = tk.Canvas(row, width=KEYBOX_MIN, height=KEYBOX_MIN, bg=CARD,
                                   highlightthickness=0, cursor="hand2")
        win = row.create_window(0, RELOAD_H / 2, window=self.panic_box, anchor="e")

        def layout(e):
            row.delete("card")
            rounded_rect(row, 1, 1, e.width - 1, RELOAD_H - 1, CARD_RADIUS,
                         fill=CARD, outline=CARD, tags="card")
            row.tag_lower("card")
            row.coords(win, e.width - 20, RELOAD_H / 2)

        row.bind("<Configure>", layout)
        self.panic_box.bind("<Button-1>", self._on_panic_click)
        self.panic_box.bind("<Enter>", lambda e: self._set_panic_hover(True))
        self.panic_box.bind("<Leave>", lambda e: self._set_panic_hover(False))
        self.bind("<KeyPress>", self._on_panic_key, add="+")
        self._draw_panic()

    def _set_panic_hover(self, state):
        self._panic_hover = state
        self._draw_panic()

    def _draw_panic(self, text=None):
        if text is None:
            text = self.panic
        w = max(KEYBOX_MIN, self._panic_font.measure(text) + KEYBOX_PAD)
        if self._panic_capturing:
            color = KEYBOX_ACTIVE
        elif self._panic_hover:
            color = KEYBOX_HOVER
        else:
            color = KEYBOX
        self.panic_box.config(width=w, height=KEYBOX_MIN)
        self.panic_box.delete("all")
        rounded_rect(self.panic_box, 1, 1, w - 1, KEYBOX_MIN - 1, 7,
                     fill=color, outline=color)
        self.panic_box.create_text(w / 2, KEYBOX_MIN / 2, text=text, fill=TEXT,
                                   font=self._panic_font)

    def _on_panic_click(self, _e):
        if self._panic_capturing:            # click again = cancel
            self._panic_capturing = False
            self._draw_panic()
            return
        self._panic_capturing = True
        self._draw_panic("...")
        self.focus_force()

    def _on_panic_key(self, e):
        if not self._panic_capturing:
            return
        if e.keysym in MODIFIER_KEYSYMS:
            return "break"
        if e.keysym == "Escape":             # clear the panic key
            self.panic, self.panic_vk = "None", None
            self.panic_mods = (False, False, False)
            self._panic_capturing = False
            self._draw_panic()
            return "break"
        mods = (bool(e.state & CTRL_MASK), bool(e.state & ALT_MASK),
                bool(e.state & SHIFT_MASK))
        vk = e.keycode if IS_WIN else None
        if vk is not None and vk == self.keybind_vk and mods == self.keybind_mods:
            self._flash("Panic key can't be the same as the macro key", error=True)
            self._panic_capturing = False
            self._draw_panic()
            return "break"
        parts = []
        if mods[0]:
            parts.append("Ctrl")
        if mods[1]:
            parts.append("Alt")
        if mods[2]:
            parts.append("Shift")
        parts.append(key_name(e.keysym, e.keycode))
        self.panic = " + ".join(parts)
        self.panic_vk = vk
        self.panic_mods = mods
        self._panic_capturing = False
        self._draw_panic()
        return "break"

    # ---------- Adjustments tab: sequence + speed + cooldown ----------
    def _build_tuning(self):
        self.tuning_view = tk.Frame(self.content, bg=BG)   # packed only on the Adjustments tab
        self.sequence = list(range(len(MODES)))             # indexes into MODES
        self.type_rate = TYPE_RATE_DEFAULT
        self.click_rate = CLICK_RATE_DEFAULT
        self.cooldown = 0.0
        self._sliders = {}
        self._seq_font = tkfont.Font(family="Segoe UI Semibold", size=10)

        tk.Frame(self.tuning_view, bg=BG, height=14).pack()
        self._build_sequence_row()
        self._make_slider("type_rate", "Number Speed", "Hotbar presses per second",
                          8, 40, 1, lambda v: f"{v:g}/s")
        self._make_slider("click_rate", "Click Speed", "Auto-clicks per second",
                          8, 50, 1, lambda v: f"{v:g}/s")
        self._make_slider("cooldown", "Cooldown", "Pause after a run ends",
                          0, 2, 0.05, lambda v: "Off" if v <= 0 else f"{v:g}s")

    # --- sequence editor ---
    def _build_sequence_row(self):
        row = tk.Canvas(self.tuning_view, height=SEQ_H, bg=BG, highlightthickness=0)
        row.pack(side="top", fill="x", padx=32, pady=(0, 10))
        self.seq_row = row
        row.bind("<Configure>", self._draw_sequence)

        # clicks + hover are bound to tags once; hover only recolors (never redraws)
        for i in range(len(MODES)):
            self._seq_bind(f"seqadd{i}", f"seqaddbg{i}", CFG_BTN, CFG_BTN_HOVER,
                           lambda i=i: self._seq_add(i))
        self._seq_bind("seqreset", "seqresetbg", CFG_BTN, CFG_BTN_HOVER, self._seq_reset)
        for pos in range(SEQ_MAX):
            self._seq_bind(f"seqchip{pos}", f"seqchipbg{pos}", KEYBOX, DANGER,
                           lambda pos=pos: self._seq_remove(pos))
        self._draw_sequence()

    def _seq_bind(self, tag, bg_tag, base, hover, action):
        r = self.seq_row
        r.tag_bind(tag, "<Button-1>", lambda e: action())

        def enter(_e):
            r.config(cursor="hand2")
            r.itemconfig(bg_tag, fill=hover, outline=hover)

        def leave(_e):
            r.config(cursor="arrow")
            r.itemconfig(bg_tag, fill=base, outline=base)

        r.tag_bind(tag, "<Enter>", enter)
        r.tag_bind(tag, "<Leave>", leave)

    def _draw_sequence(self, e=None):
        c = self.seq_row
        w = e.width if e else c.winfo_width()
        c.delete("all")
        rounded_rect(c, 1, 1, w - 1, SEQ_H - 1, CARD_RADIUS, fill=CARD, outline=CARD)
        c.create_text(22, 24, text="Sequence", anchor="w", fill=TEXT,
                      font=("Segoe UI Semibold", 12))
        c.create_text(22, 47, anchor="w", fill=SUBTEXT, font=("Segoe UI", 9),
                      text="Order the guns fire in. Click one below to remove it")

        # add / reset buttons, right-aligned on the top row
        buttons = [(f"seqadd{i}", f"seqaddbg{i}", f"+ {MODES[i]}") for i in range(len(MODES))]
        buttons.append(("seqreset", "seqresetbg", "Reset"))
        x_right = w - 18
        for tag, bg_tag, label in reversed(buttons):
            bw = self._seq_font.measure(label) + 22
            x1 = x_right - bw
            rounded_rect(c, x1, 11, x_right, 37, 6, fill=CFG_BTN, outline=CFG_BTN,
                         tags=(tag, bg_tag))
            c.create_text((x1 + x_right) / 2, 24, text=label, fill=TEXT,
                          font=self._seq_font, tags=tag)
            x_right = x1 - 6

        # the sequence itself
        x = 22
        for pos, gi in enumerate(self.sequence):
            label = MODES[gi]
            cw = max(46, self._seq_font.measure(label) + 24)
            rounded_rect(c, x, 73, x + cw, 103, 7, fill=KEYBOX, outline=KEYBOX,
                         tags=(f"seqchip{pos}", f"seqchipbg{pos}"))
            c.create_text(x + cw / 2, 88, text=label, fill=TEXT, font=self._seq_font,
                          tags=f"seqchip{pos}")
            x += cw + 6

    def _seq_add(self, gi):
        if len(self.sequence) >= SEQ_MAX:
            self._flash(f"Sequence is full (max {SEQ_MAX})", error=True)
            return
        self.sequence.append(gi)
        self._draw_sequence()

    def _seq_remove(self, pos):
        if pos >= len(self.sequence):
            return
        if len(self.sequence) == 1:
            self._flash("The sequence needs at least one gun", error=True)
            return
        self.sequence.pop(pos)
        self._draw_sequence()

    def _seq_reset(self):
        self.sequence = list(range(len(MODES)))
        self._draw_sequence()

    # --- sliders ---
    def _make_slider(self, key, title, desc, lo, hi, step, fmt):
        row = tk.Canvas(self.tuning_view, height=RELOAD_H, bg=BG, highlightthickness=0)
        row.pack(side="top", fill="x", padx=32, pady=(0, 10))
        row.create_text(22, RELOAD_H / 2 - 10, text=title, anchor="w", fill=TEXT,
                        font=("Segoe UI Semibold", 12))
        row.create_text(22, RELOAD_H / 2 + 12, text=desc, anchor="w", fill=SUBTEXT,
                        font=("Segoe UI", 9))
        val = row.create_text(0, RELOAD_H / 2, text="", anchor="e", fill=TEXT,
                              font=("Segoe UI Semibold", 11))
        sc = tk.Canvas(row, width=SLIDER_W, height=28, bg=CARD, highlightthickness=0,
                       cursor="hand2")
        win = row.create_window(0, RELOAD_H / 2, window=sc, anchor="e")

        def layout(e):
            row.delete("card")
            rounded_rect(row, 1, 1, e.width - 1, RELOAD_H - 1, CARD_RADIUS,
                         fill=CARD, outline=CARD, tags="card")
            row.tag_lower("card")
            row.coords(win, e.width - 88, RELOAD_H / 2)
            row.coords(val, e.width - 20, RELOAD_H / 2)

        row.bind("<Configure>", layout)
        sc.bind("<Button-1>", lambda e: self._slider_drag(key, e.x))
        sc.bind("<B1-Motion>", lambda e: self._slider_drag(key, e.x))
        self._sliders[key] = {"row": row, "sc": sc, "val": val, "lo": lo, "hi": hi,
                              "step": step, "fmt": fmt}
        self._draw_slider(key)

    def _slider_drag(self, key, x):
        st = self._sliders[key]
        frac = min(1.0, max(0.0, (x - 10) / (SLIDER_W - 20)))
        self._set_slider(key, st["lo"] + frac * (st["hi"] - st["lo"]))

    def _set_slider(self, key, value):
        st = self._sliders[key]
        step = st["step"]
        v = round(round(float(value) / step) * step, 3)
        v = min(st["hi"], max(st["lo"], v))
        if float(step).is_integer():
            v = int(round(v))
        setattr(self, key, v)
        self._draw_slider(key)

    def _draw_slider(self, key):
        st = self._sliders[key]
        c, v = st["sc"], getattr(self, key)
        frac = (v - st["lo"]) / (st["hi"] - st["lo"])
        x = 10 + frac * (SLIDER_W - 20)
        c.delete("all")
        c.create_line(10, 14, SLIDER_W - 10, 14, width=4, fill=KEYBOX, capstyle="round")
        if x > 10:
            c.create_line(10, 14, x, 14, width=4, fill=BTN, capstyle="round")
        c.create_oval(x - 8, 6, x + 8, 22, fill=BTN, outline=BTN)
        st["row"].itemconfig(st["val"], text=st["fmt"](v))

    # ---------- Config tab ----------
    def _build_config_view(self):
        self.config_view = tk.Frame(self.content, bg=BG)   # packed only on the Config tab
        self._rename_state = None
        self._rename_started = False
        self._flash_job = None

        # scrollable list: a canvas holding one inner frame of rows
        self.cfg_canvas = tk.Canvas(self.config_view, bg=BG, highlightthickness=0,
                                    yscrollincrement=22)
        self.cfg_canvas.pack(fill="both", expand=True)
        self.cfg_inner = tk.Frame(self.cfg_canvas, bg=BG)
        self._cfg_win = self.cfg_canvas.create_window(0, 0, window=self.cfg_inner,
                                                      anchor="nw")
        self.cfg_canvas.bind("<Configure>", self._cfg_resize)
        self.cfg_inner.bind("<Configure>", lambda e: self._cfg_update_region())
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _cfg_resize(self, e):
        self.cfg_canvas.itemconfigure(self._cfg_win, width=e.width)
        self._cfg_update_region()

    def _cfg_update_region(self):
        c = self.cfg_canvas
        h = max(self.cfg_inner.winfo_reqheight(), c.winfo_height())
        c.configure(scrollregion=(0, 0, c.winfo_width(), h))
        self._cfg_draw_bar()

    def _cfg_draw_bar(self):
        c = self.cfg_canvas
        c.delete("sbar")
        top, bot = c.yview()
        if bot - top >= 0.999:
            return                                   # everything fits: no bar
        ch, w = c.winfo_height(), c.winfo_width()
        y0 = c.canvasy(0)
        c.create_rectangle(w - 7, y0 + top * ch + 2, w - 4, y0 + bot * ch - 2,
                           fill="#3a3a3a", outline="#3a3a3a", tags="sbar")

    def _on_wheel(self, e):
        if getattr(self, "active_tab", None) != CONFIG_TAB:
            return
        c = self.cfg_canvas
        if c.yview() == (0.0, 1.0):
            return
        c.yview_scroll(-2 if e.delta > 0 else 2, "units")
        self._cfg_draw_bar()

    def _flash(self, text, error=False, ms=1800):
        if self._flash_job:
            self.after_cancel(self._flash_job)
        self.page_sub.config(text=text, fg=ERROR_COLOR if error else SUBTEXT)
        self._flash_job = self.after(ms, lambda: self.page_sub.config(text=""))

    def _render_configs(self):
        self._rename_state = None
        for w in self.cfg_inner.winfo_children():
            w.destroy()
        names = list_configs()
        if not names:
            tk.Label(self.cfg_inner, text="No saved configs yet. Press + to save your current setup.",
                     bg=BG, fg=SUBTEXT, font=("Segoe UI", 10)
                     ).pack(anchor="w", padx=34, pady=(22, 10))
        else:
            tk.Frame(self.cfg_inner, bg=BG, height=18).pack()
        for name in names:
            self._make_config_row(name)
        self._make_plus_row()

    def _make_config_row(self, name):
        row = tk.Canvas(self.cfg_inner, height=ROW_H, bg=BG, highlightthickness=0)
        row.pack(fill="x", padx=32, pady=(0, 8))
        text_id = row.create_text(20, ROW_H / 2, text=name, anchor="w", fill=TEXT,
                                  font=("Segoe UI Semibold", 11))
        bar = tk.Frame(row, bg=CARD)

        def mk(label, cmd):
            b = tk.Label(bar, text=label, bg=CFG_BTN, fg=TEXT, font=("Segoe UI", 9),
                         padx=9, pady=4, cursor="hand2")
            b.pack(side="left", padx=3)
            b.bind("<Button-1>", lambda e: cmd())
            b.bind("<Enter>", lambda e: b.config(bg=CFG_BTN_HOVER))
            b.bind("<Leave>", lambda e: b.config(bg=CFG_BTN))
            return b

        mk("Load", lambda: self._config_load(name))
        mk("Save", lambda: self._config_save(name))
        mk("Rename", lambda: self._start_rename(row, text_id, name))
        mk("Folder", lambda: self._config_open(name))

        # delete needs a second click to confirm
        armed = {"on": False}
        d = tk.Label(bar, text="Delete", bg=CFG_BTN, fg=TEXT, font=("Segoe UI", 9),
                     padx=9, pady=4, cursor="hand2")
        d.pack(side="left", padx=3)

        def delete_click(_e):
            if armed["on"]:
                self._config_delete(name)
                return
            armed["on"] = True
            d.config(text="Sure?", bg=DANGER)

            def reset():
                try:
                    armed["on"] = False
                    d.config(text="Delete", bg=CFG_BTN)
                except tk.TclError:
                    pass
            self.after(2000, reset)

        d.bind("<Button-1>", delete_click)
        d.bind("<Enter>", lambda e: d.config(bg=DANGER_HOVER if armed["on"] else CFG_BTN_HOVER))
        d.bind("<Leave>", lambda e: d.config(bg=DANGER if armed["on"] else CFG_BTN))

        win = row.create_window(0, ROW_H / 2, window=bar, anchor="e")

        def layout(e):
            row.delete("card")
            rounded_rect(row, 1, 1, e.width - 1, ROW_H - 1, CARD_RADIUS,
                         fill=CARD, outline=CARD, tags="card")
            row.tag_lower("card")
            row.coords(win, e.width - 12, ROW_H / 2)

        row.bind("<Configure>", layout)

    def _make_plus_row(self):
        c = tk.Canvas(self.cfg_inner, height=ROW_H, bg=BG, highlightthickness=0,
                      cursor="hand2")
        c.pack(fill="x", padx=32, pady=(0, 8))
        state = {"hover": False}

        def draw(_e=None):
            w = c.winfo_width()
            col = "#262626" if state["hover"] else CARD
            c.delete("all")
            rounded_rect(c, 1, 1, w - 1, ROW_H - 1, CARD_RADIUS, fill=col, outline=col)
            c.create_text(w / 2, ROW_H / 2 - 1, text="+", fill=TEXT,
                          font=("Segoe UI", 20))

        def hover(v):
            state["hover"] = v
            draw()

        c.bind("<Configure>", draw)
        c.bind("<Enter>", lambda e: hover(True))
        c.bind("<Leave>", lambda e: hover(False))
        c.bind("<Button-1>", lambda e: self._config_add())

    def _config_add(self):
        names = list_configs()
        if len(names) >= MAX_CONFIGS:
            self._flash(f"Max {MAX_CONFIGS} configs. Delete one to add another.", error=True)
            return
        used = {n.lower() for n in names}
        n = 1
        while f"Config {n}".lower() in used:
            n += 1
        name = f"Config {n}"
        if write_json(cfg_path(name), self._snapshot()):
            self._flash(f"Saved '{name}'")
            self._render_configs()
            self.after(60, lambda: (self.cfg_canvas.yview_moveto(1.0), self._cfg_draw_bar()))
        else:
            self._flash("Couldn't save the config", error=True)

    def _config_load(self, name):
        data = read_json(cfg_path(name))
        if not isinstance(data, dict):
            self._flash("Couldn't read that config", error=True)
            return
        self._apply_snapshot(data)
        self._flash(f"Loaded '{name}'")

    def _config_save(self, name):
        if write_json(cfg_path(name), self._snapshot()):
            self._flash(f"Updated '{name}' with your current settings")
        else:
            self._flash("Couldn't save the config", error=True)

    def _config_open(self, name):
        path = cfg_path(name)
        try:
            if IS_WIN:
                if os.path.exists(path):
                    subprocess.Popen(f'explorer /select,"{path}"')
                else:
                    os.startfile(CONFIG_DIR)
            else:
                subprocess.Popen(["xdg-open", CONFIG_DIR])
        except OSError:
            self._flash("Couldn't open the folder", error=True)

    def _config_delete(self, name):
        try:
            os.remove(cfg_path(name))
        except OSError:
            self._flash("Couldn't delete the config", error=True)
            return
        self._flash(f"Deleted '{name}'")
        self._render_configs()

    def _start_rename(self, row, text_id, name):
        self._cancel_rename()
        self._rename_started = True            # keep the global click from cancelling it
        row.itemconfigure(text_id, state="hidden")
        entry = tk.Entry(row, width=NAME_MAX - 2, bg="#2a2a2a", fg=TEXT,
                         insertbackground=TEXT, relief="flat", highlightthickness=0,
                         font=("Segoe UI Semibold", 11))
        vcmd = (self.register(lambda P: len(P) <= NAME_MAX), "%P")
        entry.config(validate="key", validatecommand=vcmd)
        entry.insert(0, name)
        entry.select_range(0, "end")
        win = row.create_window(16, ROW_H / 2, window=entry, anchor="w")
        self._rename_state = {"row": row, "text_id": text_id, "entry": entry,
                              "win": win, "name": name}
        entry.bind("<Return>", lambda e: self._commit_rename())
        entry.bind("<Escape>", lambda e: self._cancel_rename())
        entry.focus_force()

    def _cancel_rename(self):
        st = self._rename_state
        if not st:
            return
        self._rename_state = None
        try:
            st["entry"].destroy()
            st["row"].delete(st["win"])
            st["row"].itemconfigure(st["text_id"], state="normal")
        except tk.TclError:
            pass

    def _commit_rename(self):
        st = self._rename_state
        if not st:
            return
        old = st["name"]
        new = safe_name(st["entry"].get())
        if not new or new == old:
            self._cancel_rename()
            return
        if new.lower() in {n.lower() for n in list_configs() if n != old}:
            self._flash("That name is already used", error=True)
            self._cancel_rename()
            return
        try:
            os.rename(cfg_path(old), cfg_path(new))
        except OSError:
            self._flash("Couldn't rename the config", error=True)
            self._cancel_rename()
            return
        self._rename_state = None
        self._flash(f"Renamed to '{new}'")
        self._render_configs()

    # ---------- Macro trigger ----------
    def _start_macro_listener(self):
        if not IS_WIN:
            return
        ctypes.windll.winmm.timeBeginPeriod(1)       # finer sleep timing
        self.runner = MacroRunner()
        self._poll_stop = False
        self._own_hwnd = self._hwnd()                # cached: Tk isn't thread-safe
        threading.Thread(target=self._poll_hotkey, daemon=True).start()

    def _poll_hotkey(self):
        was_down = False
        panic_was = False
        while not self._poll_stop:
            time.sleep(0.005)

            # panic key: kills a run instantly
            pvk = self.panic_vk
            pdown = False
            if pvk is not None:
                pc, pa, ps = self.panic_mods
                pdown = (key_is_down(pvk)
                         and key_is_down(0x11) == pc
                         and key_is_down(0x12) == pa
                         and key_is_down(0x10) == ps)
                if pdown and not panic_was:
                    self.runner.stop()
            panic_was = pdown

            # works for keyboard keys and mouse buttons alike (both are virtual keys)
            vk = self.keybind_vk
            if vk is None:
                was_down = False
                continue
            ctrl, alt, shift = self.keybind_mods
            down = (key_is_down(vk)
                    and key_is_down(0x11) == ctrl
                    and key_is_down(0x12) == alt
                    and key_is_down(0x10) == shift)
            if down and not was_down and not pdown:
                self._try_trigger()
            elif was_down and not down and self.hold_mode:
                self.runner.release(self.auto_reload)   # released: reload pass if on, else stop
            was_down = down

    def _fg_is_roblox(self, hwnd):
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value.strip().lower() == "roblox"

    def _try_trigger(self):
        if (self._capturing or self._panic_capturing
                or self._num_active is not None or self._time_active):
            return                                   # busy setting things up
        if not all(self.gun_numbers):
            return
        fg = ctypes.windll.user32.GetForegroundWindow()
        if fg == self._own_hwnd:
            return                                   # only fire when you're in the game
        if self.roblox_only and not self._fg_is_roblox(fg):
            return

        if self.runner.running:
            if not self.hold_mode:
                self.runner.stop()                   # press again = stop early
            return
        if self.cooldown > 0 and time.perf_counter() - self.runner.last_end < self.cooldown:
            return                                   # still cooling down

        all_vks = [0x30 + int(n) for n in self.gun_numbers]            # DB, Rev, Tact
        seq_vks = [0x30 + int(self.gun_numbers[i]) for i in self.sequence]
        type_delay = max(0.004, 1.0 / self.type_rate - KEY_HOLD)
        click_delay = max(0.002, 1.0 / self.click_rate - CLICK_HOLD)
        watch = fg if self.stop_on_blur else None
        if self.hold_mode:
            self.runner.start(seq_vks, all_vks, HOLD_MAX, False, self.jitter,
                              self.mid_reload, type_delay, click_delay, watch)
        else:
            self.runner.start(seq_vks, all_vks, self.run_time, self.auto_reload,
                              self.jitter, False, type_delay, click_delay, watch)

    # ---------- Mode card: DB | Rev | Tact (+ number inputs) ----------
    def _build_mode_card(self):
        self.gun_numbers = [""] * len(MODES)
        self._num_active = None
        self._num_prev = ""
        self._num_hover = None
        self._num_warn = ""
        self._num_warn_job = None
        self._num_font = tkfont.Font(family="Segoe UI Semibold", size=11)

        self.mode_card = tk.Canvas(self.content, height=MODE_H, bg=BG,
                                   highlightthickness=0)
        self.mode_card.pack(side="top", fill="x", padx=32, pady=(24, 0))
        self.mode_card.bind("<Configure>", self._draw_mode_card)

        for i in range(len(MODES)):
            tag = f"numbox{i}"
            self.mode_card.tag_bind(tag, "<Button-1>", lambda e, i=i: self._on_numbox_click(i))
            self.mode_card.tag_bind(tag, "<Enter>", lambda e, i=i: self._set_num_hover(i))
            self.mode_card.tag_bind(tag, "<Leave>", lambda e: self._set_num_hover(None))
        self.bind("<KeyPress>", self._on_num_key, add="+")

    def _num_color(self, i):
        if self._num_active == i:
            return KEYBOX_ACTIVE
        if self._num_hover == i:
            return KEYBOX_HOVER
        return KEYBOX

    def _set_num_hover(self, i):
        prev = self._num_hover
        self._num_hover = i
        self.mode_card.config(cursor="hand2" if i is not None else "arrow")
        # only recolor existing shapes; never delete/recreate under the cursor
        for k in (prev, i):
            if k is not None:
                col = self._num_color(k)
                self.mode_card.itemconfig(f"numbg{k}", fill=col, outline=col)

    def _draw_mode_card(self, e=None):
        c = self.mode_card
        w = e.width if e else c.winfo_width()
        c.delete("all")
        rounded_rect(c, 1, 1, w - 1, MODE_H - 1, CARD_RADIUS, fill=CARD, outline=CARD)
        section = w / len(MODES)
        for i, name in enumerate(MODES):
            cx = section * i + section / 2
            c.create_text(cx, 50, text=name, fill=TEXT,
                          font=("Segoe UI Semibold", 32))
            if i:
                x = section * i
                c.create_line(x, 22, x, MODE_H - 62, fill=DIVIDER, width=2)

            # tiny number input
            active = self._num_active == i
            value = self.gun_numbers[i]
            if active:
                text = value if value else "..."
            else:
                text = value if value else "-"
            bw = max(NUM_BOX, self._num_font.measure(text) + 18)
            color = self._num_color(i)
            y1 = 98
            tag = f"numbox{i}"
            rounded_rect(c, cx - bw / 2, y1, cx + bw / 2, y1 + NUM_BOX, 7,
                         fill=color, outline=color, tags=(tag, f"numbg{i}"))
            c.create_text(cx, y1 + NUM_BOX / 2, text=text,
                          fill=TEXT if (value or active) else SUBTEXT,
                          font=self._num_font, tags=tag)

        if self._num_warn:
            c.create_text(w / 2, MODE_H - 24, text=self._num_warn,
                          fill="#e5737a", font=("Segoe UI Semibold", 10))
        else:
            c.create_text(w / 2, MODE_H - 24, text="Set the number of your gun in this",
                          fill=SUBTEXT, font=("Segoe UI", 10))

    def _finish_number(self):
        """Close the open number box; reject numbers already used elsewhere."""
        i = self._num_active
        if i is None:
            return
        self._num_active = None
        value = self.gun_numbers[i]
        if value:
            for j, other in enumerate(self.gun_numbers):
                if j != i and other and int(other) == int(value):
                    self.gun_numbers[i] = self._num_prev
                    self._warn_numbers("That number is already used")
                    return

    def _warn_numbers(self, text, ms=1500):
        if self._num_warn_job:
            self.after_cancel(self._num_warn_job)
        self._num_warn = text
        self._num_warn_job = self.after(ms, self._clear_num_warn)

    def _clear_num_warn(self):
        self._num_warn_job = None
        self._num_warn = ""
        self._draw_mode_card()

    def _on_numbox_click(self, i):
        self._numbox_clicked = True     # tells the global click handler to leave it alone
        self._finish_time()
        if self._capturing:                       # stop keybind capture
            self._capturing = False
            self._draw_keybox(self.keybind)
        if self._num_active == i:                 # click again = confirm
            self._finish_number()
        else:
            self._finish_number()                 # confirm whichever box was open
            self._num_active = i
            self._num_prev = self.gun_numbers[i]
            self.focus_force()
        self._draw_mode_card()

    def _on_num_key(self, e):
        i = self._num_active
        if i is None:
            return
        if e.keysym == "Return":
            self._finish_number()
        elif e.keysym == "Escape":
            self.gun_numbers[i] = self._num_prev
            self._num_active = None
        elif e.keysym == "BackSpace":
            self.gun_numbers[i] = self.gun_numbers[i][:-1]
        elif e.char and e.char in "0123456789":
            self.gun_numbers[i] = e.char      # single digit, 0-9: new digit replaces old
        else:
            return "break"
        self._draw_mode_card()
        return "break"

    # ---------- Keybind row (Gun Macro tab) ----------
    def _build_keybind_row(self):
        self.keybind = "None"
        self.keybind_vk = None               # Windows virtual-key code (keyboard OR mouse button)
        self.keybind_mods = (False, False, False)   # ctrl, alt, shift
        self._capturing = False
        self._keybox_hover = False
        self._mouse_job = None
        self._mouse_prev = {}
        self._keybox_font = tkfont.Font(family="Segoe UI Semibold", size=10)

        # card ("its own window") holding the label and the keybox
        self.keybind_row = tk.Canvas(self.content, height=CARD_H, bg=BG,
                                     highlightthickness=0)
        self.keybind_row.pack(side="bottom", fill="x", padx=32, pady=(0, 28))

        self._card_label = self.keybind_row.create_text(
            22, CARD_H / 2, text="Set Keybind", anchor="w", fill=TEXT,
            font=("Segoe UI Semibold", 12))

        self._warn_job = None
        self._warn = self.keybind_row.create_text(
            0, CARD_H / 2, text="", fill="#e5737a",
            font=("Segoe UI Semibold", 10))

        self.keybox = tk.Canvas(self.keybind_row, width=KEYBOX_MIN,
                                height=KEYBOX_MIN, bg=CARD,
                                highlightthickness=0, cursor="hand2")
        self._keybox_win = self.keybind_row.create_window(
            0, CARD_H / 2, window=self.keybox, anchor="e")

        self.keybind_row.bind("<Configure>", self._layout_card)
        self.keybox.bind("<Button-1>", self._on_keybox_click)
        self.keybox.bind("<Enter>", lambda e: self._set_hover(True))
        self.keybox.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<KeyPress>", self._on_key_press, add="+")
        self._draw_keybox(self.keybind)

    def _layout_card(self, e):
        c = self.keybind_row
        c.delete("card")
        rounded_rect(c, 1, 1, e.width - 1, CARD_H - 1, CARD_RADIUS,
                     fill=CARD, outline=CARD, tags="card")
        c.tag_lower("card")
        c.coords(self._keybox_win, e.width - 20, CARD_H / 2)
        c.coords(self._warn, e.width / 2, CARD_H / 2)

    def _set_hover(self, state):
        self._keybox_hover = state
        self._draw_keybox(self._keybox_text)

    def _draw_keybox(self, text):
        self._keybox_text = text
        w = max(KEYBOX_MIN, self._keybox_font.measure(text) + KEYBOX_PAD)
        if self._capturing:
            color = KEYBOX_ACTIVE
        elif self._keybox_hover:
            color = KEYBOX_HOVER
        else:
            color = KEYBOX
        self.keybox.config(width=w, height=KEYBOX_MIN)
        self.keybox.delete("all")
        rounded_rect(self.keybox, 1, 1, w - 1, KEYBOX_MIN - 1, 7,
                     fill=color, outline=color)
        self.keybox.create_text(w / 2, KEYBOX_MIN / 2, text=text, fill=TEXT,
                                font=self._keybox_font)

    def _on_keybox_click(self, _e):
        self._finish_time()
        if self._capturing:                  # click again = cancel
            self._capturing = False
            self._draw_keybox(self.keybind)
            return
        if not all(self.gun_numbers):        # numbers must be set first
            self._show_warning("Please set the guns hotbar numbers")
            return
        if self._num_active is not None:
            self._finish_number()
            self._draw_mode_card()
        self._capturing = True
        self._draw_keybox("...")
        self.focus_force()                   # borderless windows need this to get keys
        self._start_mouse_capture()          # also listen for mouse buttons

    def _show_warning(self, text, ms=1500):
        if self._warn_job:
            self.after_cancel(self._warn_job)
        self.keybind_row.itemconfig(self._warn, text=text)
        self._warn_job = self.after(ms, self._clear_warning)

    def _clear_warning(self):
        self._warn_job = None
        self.keybind_row.itemconfig(self._warn, text="")

    def _commit_keybind(self, name, vk, mods):
        """Store a finished keybind (keyboard key or mouse button) and show it."""
        parts = []
        if mods[0]:
            parts.append("Ctrl")
        if mods[1]:
            parts.append("Alt")
        if mods[2]:
            parts.append("Shift")
        parts.append(name)
        self.keybind = " + ".join(parts)
        self.keybind_vk = vk
        self.keybind_mods = tuple(bool(m) for m in mods)
        self._capturing = False
        self._draw_keybox(self.keybind)

    def _on_key_press(self, e):
        if not getattr(self, "_capturing", False):
            return
        if e.keysym in MODIFIER_KEYSYMS:     # wait for the real key
            return "break"
        mods = (bool(e.state & CTRL_MASK), bool(e.state & ALT_MASK),
                bool(e.state & SHIFT_MASK))
        self._commit_keybind(key_name(e.keysym, e.keycode),
                             e.keycode if IS_WIN else None, mods)
        return "break"

    # ---------- Mouse button capture ----------
    # Tk can't see Mouse 4/5 on Windows, so while the box says "..." we watch the
    # buttons directly. A press only counts when the pointer is over this window.
    def _start_mouse_capture(self):
        if not IS_WIN:
            return
        self._mouse_prev = {vk: key_is_down(vk) for vk in MOUSE_VKS}   # ignore buttons already held
        if self._mouse_job:
            self.after_cancel(self._mouse_job)
        self._poll_mouse_capture()

    def _pointer_in_window(self):
        x, y = self.winfo_pointerx(), self.winfo_pointery()
        left, top = self.winfo_rootx(), self.winfo_rooty()
        return (left <= x < left + self.winfo_width()
                and top <= y < top + self.winfo_height())

    def _poll_mouse_capture(self):
        self._mouse_job = None
        if not self._capturing:
            return                                   # finished or cancelled
        inside = self._pointer_in_window()
        for vk in MOUSE_VKS:
            down = key_is_down(vk)
            if down and not self._mouse_prev.get(vk) and inside:
                mods = (key_is_down(0x11), key_is_down(0x12), key_is_down(0x10))
                self._commit_keybind(MOUSE_NAMES[vk], vk, mods)
                return
            self._mouse_prev[vk] = down
        self._mouse_job = self.after(8, self._poll_mouse_capture)

    def _place_credits(self, e):
        self.sidebar.coords(self._credit1, SB_W / 2, e.height - 44)
        self.sidebar.coords(self._credit2, SB_W / 2, e.height - 24)

    def _on_global_click(self, e):
        """Clicking anywhere except the active input exits capture mode."""
        # rename box: clicking anywhere else cancels it
        if self._rename_started:
            self._rename_started = False
        elif self._rename_state and e.widget is not self._rename_state["entry"]:
            self._cancel_rename()

        # keybind capture: only the keybind box keeps it alive
        if self._capturing and e.widget is not self.keybox:
            self._capturing = False
            self._draw_keybox(self.keybind)

        # panic key capture: only the panic box keeps it alive
        if self._panic_capturing and e.widget is not self.panic_box:
            self._panic_capturing = False
            self._draw_panic()

        # run time input: only its box keeps it alive
        if self._time_active and e.widget is not self.timebox:
            self._finish_time()

        # number input: only the number boxes keep it alive
        if getattr(self, "_numbox_clicked", False):
            self._numbox_clicked = False   # click was on a number box; it handled itself
            return
        if self._num_active is not None:
            self._finish_number()
            self._draw_mode_card()

    def _tab_at(self, y):
        for i in range(len(TABS)):
            top = self._tab_y(i)
            if top <= y <= top + TAB_H:
                return i
        return None

    def _on_sidebar_motion(self, e):
        self.sidebar.config(cursor="hand2" if self._tab_at(e.y) is not None else "arrow")

    def _on_sidebar_click(self, e):
        idx = self._tab_at(e.y)
        if idx is not None:
            self._select_tab(idx)

    def _place_highlight(self, y):
        self._hl_y = y
        self.sidebar.coords(self._hl, *rr_points(12, y, SB_W - 12, y + TAB_H, 8))
        self.sidebar.coords(self._bar, 12, y + 11, 15, y + TAB_H - 11)
        # text brightens as the highlight passes over it
        span = TAB_H + TAB_GAP
        for i, item in enumerate(self._tab_items):
            near = max(0.0, 1 - abs(y - self._tab_y(i)) / span)
            self.sidebar.itemconfig(item, fill=mix(SUBTEXT, TEXT, near))

    def _select_tab(self, idx):
        if idx == self.active_tab:
            return
        self.active_tab = idx
        self.page_title.config(text=TABS[idx])
        for w in (self.mode_card, self.keybind_row, self.time_row,
                  self.reload_row, self.extras_view, self.tuning_view,
                  self.config_view):
            w.pack_forget()
        self._cancel_rename()
        if self._num_active is not None:
            self._finish_number()
            self._draw_mode_card()
        if self._capturing:
            self._capturing = False
            self._draw_keybox(self.keybind)
        if self._panic_capturing:
            self._panic_capturing = False
            self._draw_panic()
        self._finish_time()

        if idx == 0:
            self.mode_card.pack(side="top", fill="x", padx=32, pady=(24, 0))
            self.keybind_row.pack(side="bottom", fill="x", padx=32, pady=(0, 28))
            self.time_row.pack(side="bottom", fill="x", padx=32, pady=(0, 10))
            self.reload_row.pack(side="bottom", fill="x", padx=32, pady=(0, 10))
        elif idx == 1:
            self.extras_view.pack(side="top", fill="both", expand=True)
        elif idx == 2:
            self.tuning_view.pack(side="top", fill="both", expand=True)
        else:
            self._render_configs()
            self.config_view.pack(side="top", fill="both", expand=True)
            self.cfg_canvas.yview_moveto(0)

        if self._hl_job:
            self.after_cancel(self._hl_job)

        y0, y1 = self._hl_y, self._tab_y(idx)
        steps = 16

        def step(i=0):
            t = ease_in_out(i / steps)
            self._place_highlight(y0 + (y1 - y0) * t)
            if i < steps:
                self._hl_job = self.after(14, lambda: step(i + 1))
            else:
                self._hl_job = None

        step()

    def _center(self):
        x = (self.winfo_screenwidth() - self.width) // 2
        y = (self.winfo_screenheight() - self.height) // 2
        self.geometry(f"{self.width}x{self.height}+{x}+{y}")


if __name__ == "__main__":
    App().mainloop()
