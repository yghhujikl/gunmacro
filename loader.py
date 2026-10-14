r"""
Gun macro Loader  ->  built into Loader.exe

Its only job is to keep GunMacro.exe up to date. It does NOT launch the app.

Put Loader.exe in any folder and run it. GunMacro.exe is downloaded into that
same folder. Run the Loader again any time to check for an update: the new
version is downloaded, verified (SHA-256), and replaces the old GunMacro.exe.

Your settings live in %LOCALAPPDATA%\GunMacro\data and are never touched.
"""
import ctypes
import hashlib
import http.client
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import tkinter as tk
import tkinter.font as tkfont

# ====================== SET THIS ONCE ======================
REPO = "yghhujikl/gunmacro"
# ===========================================================

APP_NAME = "Gun Macro"
EXE_NAME = "GunMacro.exe"
IS_WIN = sys.platform == "win32"
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/manifest.json"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/".lower()

TIMEOUT = 20                       # seconds per network call
RETRIES = 3
CHUNK = 64 * 1024
MAX_MANIFEST = 64 * 1024
MAX_FILE = 500 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
VER_RE = re.compile(r"^[0-9A-Za-z._-]{1,32}$")

# ---------- theme ----------
BG, BAR = "#141414", "#0c0c0c"
TEXT, SUBTEXT = "#f2f2f2", "#6d6d6d"
LAV = "#8e89ad"                    # soft accent for the sub line
TILE, TILE_LINE = "#0e0e0e", "#2b2b2b"
TRACK, FILL, ERROR = "#2a2a2a", "#ffffff", "#e5737a"
BTN, BTN_HOVER, BTN_TEXT = "#ffffff", "#e4e4e4", "#111111"
CLOSE_HOVER, BAR_HOVER = "#c42b1c", "#242424"
RADIUS = 14                        # window corner radius
TONES = {"idle": "#6f6f6f", "ok": "#5fd38d", "warn": "#e6b450", "err": "#e5737a"}

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_MINIMIZEBOX = 0x00020000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080


class UpdateError(Exception):
    def __init__(self, message, title="Update failed"):
        super().__init__(message)
        self.title = title


class BadDownload(OSError):
    pass


# ---------- helpers ----------
def app_folder():
    """The folder Loader.exe lives in (GunMacro.exe goes here too)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def is_locked(path):
    """True if the file is a running program (Windows won't let it be opened for writing)."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "ab"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def matches(path, manifest):
    """Is the file on disk exactly the version the manifest describes?"""
    try:
        return (os.path.getsize(path) == manifest["size"]
                and sha256_file(path) == manifest["sha256"])
    except OSError:
        return False


def remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def clip(text, n=150):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


# ---------- network ----------
def open_url(url):
    if not url.lower().startswith("https://"):
        raise UpdateError("Refusing to use a non-HTTPS address.", "Blocked for safety")
    req = urllib.request.Request(url, headers={"User-Agent": "GunMacroLoader/3"})
    resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    if not resp.geturl().lower().startswith("https://"):
        resp.close()
        raise UpdateError("The server redirected to a non-HTTPS address.", "Blocked for safety")
    return resp


def with_retries(title, fn):
    """Run fn(); retry on network trouble, give up at once on 'not found' style errors."""
    last = None
    for attempt in range(RETRIES):
        try:
            return fn()
        except UpdateError:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UpdateError("GitHub answered 404 (not found). Make sure a release "
                                  "is published on the repo.", title)
            if 400 <= e.code < 500:
                raise UpdateError(f"The server answered HTTP {e.code}.", title)
            last = e
        except (OSError, http.client.HTTPException) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise UpdateError(f"Check your internet connection and try again. ({last})", title)


def fetch_manifest():
    title = "Couldn't check for updates"
    bad = UpdateError("The update info from the server is invalid.", title)

    def get():
        with open_url(MANIFEST_URL) as resp:
            return resp.read(MAX_MANIFEST + 1)

    raw = with_retries(title, get)
    if len(raw) > MAX_MANIFEST:
        raise bad
    try:
        m = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise bad
    if not isinstance(m, dict):
        raise bad
    version, url, sha, size = m.get("version"), m.get("url"), m.get("sha256"), m.get("size")
    if not (isinstance(version, str) and VER_RE.match(version)):
        raise bad
    if not (isinstance(url, str) and url.lower().startswith(DOWNLOAD_PREFIX)):
        raise bad
    if not (isinstance(sha, str) and SHA_RE.match(sha.lower())):
        raise bad
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE:
        raise bad
    return {"version": version, "url": url, "sha256": sha.lower(), "size": size}


def download(manifest, dest, progress):
    """Download to `dest`, checking size and SHA-256. progress() gets 0..1."""
    def get():
        h = hashlib.sha256()
        done = 0
        with open_url(manifest["url"]) as resp, open(dest, "wb") as out:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > manifest["size"]:
                    raise BadDownload("file is larger than expected")
                h.update(chunk)
                out.write(chunk)
                progress(done / manifest["size"])
        if done != manifest["size"]:
            raise BadDownload("download was cut short")
        if h.hexdigest() != manifest["sha256"]:
            raise BadDownload("integrity check failed")

    with_retries("Download failed", get)


# ---------- main flow ----------
def run(ui):
    folder = app_folder()
    final = os.path.join(folder, EXE_NAME)
    part = final + ".part"
    try:
        # make sure we're allowed to write here (e.g. not inside Program Files)
        try:
            with open(part, "wb"):
                pass
        except OSError as e:
            raise UpdateError("Move Loader.exe to a folder you can write to, "
                              f"like your Desktop. ({e})", "Can't save here")

        ui.status("Checking for updates", "", "CHECKING", "idle")
        ui.progress(None)
        m = fetch_manifest()
        ver = m["version"]
        ui.version(f"v{ver}")

        if matches(final, m):
            ui.done("You're up to date", f"{APP_NAME} v{ver} is the latest version.",
                    "UP TO DATE", "ok")
            return

        had_old = os.path.exists(final)
        if is_locked(final):
            raise UpdateError("Close it, then run the Loader again.", f"{APP_NAME} is open")

        # nothing is installed until the user says so
        if had_old:
            ui.ask("Update found", f"{APP_NAME} v{ver} is ready to install.",
                   "Update Gun Macro", "UPDATE FOUND", "warn")
        else:
            ui.ask("Everything. In sync.", f"Your {APP_NAME} starts here.",
                   "Get Gun Macro", "READY", "ok")

        ui.status("Downloading update" if had_old else f"Downloading {APP_NAME}",
                  f"v{ver}  ·  0%", "DOWNLOADING", "ok")
        ui.progress(0)
        last = [-1]

        def on_progress(frac):
            ui.progress(frac)
            pct = int(frac * 100)
            if pct != last[0]:                 # don't flood the window with updates
                last[0] = pct
                ui.detail(f"v{ver}  ·  {pct}%")

        download(m, part, on_progress)

        ui.status("Installing", "Replacing the old version" if had_old else "Saving the file",
                  "INSTALLING", "ok")
        ui.lock(True)                          # don't allow closing mid-swap
        try:
            os.replace(part, final)            # removes the old exe and puts the new one in its place
        except OSError as e:
            raise UpdateError("The file couldn't be replaced. Make sure "
                              f"{APP_NAME} is closed. ({e})", "Couldn't install the update")
        finally:
            ui.lock(False)

        ui.done("Update complete" if had_old else "You're all set",
                f"{APP_NAME} v{ver} is saved next to this Loader.",
                "UPDATED" if had_old else "INSTALLED", "ok")
    finally:
        remove_quietly(part)


# ---------- window ----------
def rr_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    return canvas.create_polygon(rr_points(x1, y1, x2, y2, r), smooth=True, **kw)


def mix(c1, c2, t):
    """Blend hex colour c1 toward c2 by t (0..1)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class SpacedLabel(tk.Canvas):
    """Small-caps style text with extra space between the letters."""

    def __init__(self, parent, text, font, fg, bg, spacing, height):
        super().__init__(parent, bg=bg, highlightthickness=0, height=height)
        self._font, self._fg, self._sp, self._h = font, fg, spacing, height
        self.set_text(text)

    def set_text(self, text, fg=None):
        if fg:
            self._fg = fg
        self.delete("all")
        x = 0
        for ch in text:
            self.create_text(x, self._h / 2, text=ch, anchor="w",
                             font=self._font, fill=self._fg)
            x += self._font.measure(ch) + self._sp
        self.config(width=max(1, int(x - self._sp)))


class LoaderUI(tk.Tk):
    # design size in pixels at 100% display scaling
    W, H, BAR_H = 400, 330, 40
    CONTENT_W, BAR_THICK, BTN_H = 340, 8, 44

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} Loader")
        self.configure(bg=BG)
        self.k = max(1.0, self.winfo_fpixels("1i") / 96.0)      # display scaling
        self.w, self.h = self.s(self.W), self.s(self.H)
        self._drag_x = self._drag_y = 0
        self._closing = False
        self._locked = False
        self._mode = "busy"                 # busy | ask | done | fail
        self._btn_ready = False
        self._btn_text, self._btn_icon = "", False
        self._click = None
        self._cur_title = None
        self._fade_job = None
        self._target, self._shown = None, 0.0
        self._q = queue.Queue()

        self.f_mono = tkfont.Font(family="Consolas", size=8)
        self.f_mono_b = tkfont.Font(family="Consolas", size=9, weight="bold")
        self.f_big = tkfont.Font(family="Segoe UI Semibold", size=22)
        self.f_mid = tkfont.Font(family="Segoe UI Semibold", size=16)
        self.f_small = tkfont.Font(family="Segoe UI Semibold", size=13)
        self.f_btn = tkfont.Font(family="Segoe UI Semibold", size=11)
        self.f_brand = tkfont.Font(family="Segoe UI Semibold", size=32)

        try:
            self.attributes("-alpha", 0.0)        # fades in once it is ready
        except tk.TclError:
            pass
        self._load_icon()
        self.overrideredirect(True)
        self._build_bar()
        self._build_body()
        x = (self.winfo_screenwidth() - self.w) // 2
        y = (self.winfo_screenheight() - self.h) // 2
        self.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self.bind("<Return>", lambda e: self._on_button())

        self.after(50, self._setup_window)
        self.after(160, self._fade_in)
        self.after(33, self._pump)
        threading.Thread(target=self._work, daemon=True).start()

    def s(self, v):
        return int(round(v * self.k))

    # ----- window chrome -----
    def _load_icon(self):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "Icon.png")
        if os.path.exists(path):
            try:
                self._icon = tk.PhotoImage(file=path)
                self.iconphoto(True, self._icon)
            except tk.TclError:
                pass

    def _hwnd(self):
        return ctypes.windll.user32.GetParent(self.winfo_id())

    def _setup_window(self):
        if not IS_WIN:
            return
        user32 = ctypes.windll.user32
        hwnd = self._hwnd()
        base = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, base | WS_MINIMIZEBOX)   # allows native minimize
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW           # show in the taskbar
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        self.withdraw()
        self.after(10, self.deiconify)
        r = self.s(RADIUS) * 2
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, self.w + 1, self.h + 1, r, r)
        user32.SetWindowRgn(hwnd, rgn, True)
        self.after(80, self.focus_force)

    def _fade_in(self, i=0):
        steps = 10
        try:
            self.attributes("-alpha", min(1.0, i / steps))
        except tk.TclError:
            return
        if i < steps:
            self.after(16, lambda: self._fade_in(i + 1))

    def _minimize(self):
        if IS_WIN:
            ctypes.windll.user32.ShowWindow(self._hwnd(), 6)      # SW_MINIMIZE
        else:
            self.overrideredirect(False)
            self.iconify()

    def _bar_button(self, parent, text, command, hover):
        b = tk.Label(parent, text=text, bg=BAR, fg=TEXT, font=("Segoe UI", 11),
                     width=4, cursor="hand2")
        b.pack(side="right", fill="y")
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.config(bg=hover))
        b.bind("<Leave>", lambda e: b.config(bg=BAR))

    def _build_bar(self):
        s = self.s
        bar = tk.Frame(self, bg=BAR, height=s(self.BAR_H))
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        left = SpacedLabel(bar, "GUN MACRO", self.f_mono_b, TEXT, BAR, s(2), s(self.BAR_H))
        left.pack(side="left", padx=(s(18), 0))

        self._bar_button(bar, "✕", self._fade_close, CLOSE_HOVER)
        self._bar_button(bar, "—", self._minimize, BAR_HOVER)

        for w in (bar, left):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

    def _start_drag(self, e):
        self._drag_x, self._drag_y = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _on_drag(self, e):
        self.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

    def _fade_close(self):
        if self._locked or self._closing:        # never close in the middle of swapping files
            return
        self._closing = True
        steps = 12

        def step(i=0):
            try:
                self.attributes("-alpha", max(1 - i / steps, 0))
            except tk.TclError:
                self.destroy()
                return
            if i < steps:
                self.after(14, lambda: step(i + 1))
            else:
                self.destroy()

        step()

    # ----- body -----
    def _build_body(self):
        s = self.s
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # big wordmark (no background)
        tk.Label(body, text="Gun Macro", bg=BG, fg=TEXT, font=self.f_brand).place(
            relx=0.5, y=s(48), anchor="center")

        self.title_lbl = tk.Label(body, text="Checking for updates", bg=BG, fg=TEXT,
                                  font=self.f_big)
        self.title_lbl.place(relx=0.5, y=s(106), anchor="center")
        self.detail_lbl = tk.Label(body, text="", bg=BG, fg=LAV, font=("Segoe UI", 10),
                                   wraplength=s(self.CONTENT_W), justify="center")
        self.detail_lbl.place(relx=0.5, y=s(130), anchor="n")

        # progress bar (rounded track + rounded fill)
        self.bw, self.bh = s(self.CONTENT_W), s(self.BAR_THICK)
        self.bar = tk.Canvas(body, width=self.bw, height=self.bh, bg=BG, highlightthickness=0)
        r = self.bh / 2
        rounded_rect(self.bar, 0, 0, self.bw, self.bh, r, fill=TRACK, outline=TRACK)
        self._fill = rounded_rect(self.bar, 0, 0, self.bh, self.bh, r, fill=FILL, outline=FILL,
                                  state="hidden")
        self._bar_y = s(192) + s(self.BTN_H) // 2 - self.bh // 2

        # main button: white, wide, rounded
        self.cw, self.ch = s(self.CONTENT_W), s(self.BTN_H)
        self.btn_cv = tk.Canvas(body, width=self.cw, height=self.ch, bg=BG, highlightthickness=0)
        self.btn_cv.bind("<Enter>", lambda e: self._btn_ready and self._draw_button(BTN_HOVER))
        self.btn_cv.bind("<Leave>", lambda e: self._btn_ready and self._draw_button(BTN))
        self.btn_cv.bind("<Button-1>", lambda e: self._on_button())
        self._show_progress()

        # footer
        foot = SpacedLabel(body, "API CLIENT CONNECTED", self.f_mono, "#5c5c5c", BG, s(1.5), s(16))
        foot.place(x=s(30), y=s(262))

    # ----- button / progress area -----
    def _show_progress(self):
        self._btn_ready = False
        self.btn_cv.place_forget()
        self.bar.place(relx=0.5, y=self._bar_y, anchor="n")

    def _show_button(self, text, icon):
        self.bar.place_forget()
        self._btn_text, self._btn_icon = text, icon
        self.btn_cv.place(relx=0.5, y=self.s(192), anchor="n")
        self.btn_cv.config(cursor="hand2")
        self._btn_ready = True
        self._draw_button(BTN)

    def _draw_button(self, color):
        c, s = self.btn_cv, self.s
        c.delete("all")
        rounded_rect(c, 1, 1, self.cw - 1, self.ch - 1, s(10), fill=color, outline=color)
        cy = self.ch / 2
        tw = self.f_btn.measure(self._btn_text)
        if self._btn_icon:
            iw, gap = s(14), s(10)
            x0 = (self.cw - (iw + gap + tw)) / 2
            cx, lw = x0 + iw / 2, max(1, s(2))
            c.create_line(cx, cy - s(7), cx, cy + s(3), fill=BTN_TEXT, width=lw, capstyle="round")
            c.create_line(cx - s(4.5), cy - s(1), cx, cy + s(4), cx + s(4.5), cy - s(1),
                          fill=BTN_TEXT, width=lw, capstyle="round", joinstyle="round")
            c.create_line(cx - s(7), cy + s(8), cx + s(7), cy + s(8),
                          fill=BTN_TEXT, width=lw, capstyle="round")
            c.create_text(x0 + iw + gap, cy, text=self._btn_text, anchor="w",
                          fill=BTN_TEXT, font=self.f_btn)
        else:
            c.create_text(self.cw / 2, cy, text=self._btn_text, fill=BTN_TEXT, font=self.f_btn)

    def _on_button(self):
        if not self._btn_ready:
            return
        if self._mode == "ask":
            self._mode = "busy"
            self._target, self._shown = 0.0, 0.0
            self._show_progress()
            if self._click:
                self._click.set()              # lets the worker start the download
        elif self._mode in ("done", "fail"):
            self._fade_close()

    # ----- text -----
    def _title_font(self, text):
        n = len(text)
        return self.f_big if n <= 22 else self.f_mid if n <= 28 else self.f_small

    def _set_text(self, title, detail, tfg, dfg):
        changed = title != self._cur_title
        self._cur_title = title
        self.title_lbl.config(text=title, font=self._title_font(title))
        self.detail_lbl.config(text=clip(detail))
        if self._fade_job:
            self.after_cancel(self._fade_job)
            self._fade_job = None
        if not changed:
            self.title_lbl.config(fg=tfg)
            self.detail_lbl.config(fg=dfg)
            return
        self.title_lbl.config(fg=BG)
        self.detail_lbl.config(fg=BG)
        steps = 8

        def step(i=1):
            t = i / steps
            self.title_lbl.config(fg=mix(BG, tfg, t))
            self.detail_lbl.config(fg=mix(BG, dfg, t))
            if i < steps:
                self._fade_job = self.after(18, lambda: step(i + 1))
            else:
                self._fade_job = None

        step()

    # ----- thread-safe API used by the worker -----
    def status(self, title, detail="", tag="", tone="idle"):
        self._q.put(("show", "busy", title, detail, tag, tone, ""))

    def ask(self, title, detail, button, tag, tone):
        """Show a message and a button; wait until the user presses it."""
        self._click = threading.Event()
        self._q.put(("show", "ask", title, detail, tag, tone, button))
        self._click.wait()

    def done(self, title, detail="", tag="", tone="ok"):
        self._q.put(("show", "done", title, detail, tag, tone, ""))

    def fail(self, title, detail=""):
        self._q.put(("show", "fail", title, detail, "ERROR", "err", ""))

    def detail(self, text):
        self._q.put(("detail", text))

    def version(self, text):
        self._q.put(("version", text))

    def progress(self, frac):
        self._q.put(("progress", frac))

    def lock(self, on):
        self._q.put(("lock", on))

    def _work(self):
        try:
            run(self)
        except UpdateError as e:
            self.fail(e.title, str(e))
        except Exception as e:  # last-resort safety net
            self.fail("Something went wrong", str(e))

    def _pump(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "show":
                    _, mode, title, detail, _tag, _tone, button = item
                    self._mode = mode
                    self._set_text(title, detail, ERROR if mode == "fail" else TEXT,
                                   SUBTEXT if mode == "fail" else LAV)
                    if mode == "busy":
                        self._show_progress()
                    elif mode == "ask":
                        self._show_button(button, True)
                    else:
                        self._target = 1.0 if mode == "done" else 0.0
                        self._show_button("Close", False)
                elif kind == "detail":
                    self.detail_lbl.config(text=clip(item[1]))
                elif kind == "progress":
                    if item[1] is None:
                        self._target = None
                    else:
                        if self._target is None:
                            self._shown = 0.0
                        self._target = max(0.0, min(1.0, item[1]))
                elif kind == "lock":
                    self._locked = item[1]
        except queue.Empty:
            pass
        self._draw_bar()
        self.after(33, self._pump)

    def _draw_bar(self):
        bw, bh = self.bw, self.bh
        if self._target is None:              # unknown length: a soft sliding block
            blk = bw * 0.28
            pos = (time.time() * bw * 0.9) % (bw + blk) - blk
            x1, x2 = max(pos, 0), min(pos + blk, bw)
        else:                                 # eased towards the real value
            self._shown += (self._target - self._shown) * 0.25
            if abs(self._target - self._shown) < 0.002:
                self._shown = self._target
            x1, x2 = 0, bw * self._shown
        if x2 - x1 < bh:
            self.bar.itemconfigure(self._fill, state="hidden")
        else:
            self.bar.coords(self._fill, *rr_points(x1, 0, x2, bh, bh / 2))
            self.bar.itemconfigure(self._fill, state="normal")


def main():
    if IS_WIN:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("gunmacro.loader.4")
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    LoaderUI().mainloop()


if __name__ == "__main__":
    main()
