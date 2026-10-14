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

# ====================== SET THIS ONCE ======================
REPO = "yghhujikl/gunmacro"
# ===========================================================

APP_NAME = "Gun macro"
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

# ---------- theme (matches the main app) ----------
BG, BAR = "#141414", "#0c0c0c"
TEXT, SUBTEXT = "#e6e6e6", "#7a7a7a"
TRACK, FILL, ERROR = "#2e2e2e", "#ffffff", "#e5737a"
BTN, BTN_HOVER, BTN_TEXT = "#ffffff", "#e4e4e4", "#111111"
CLOSE_HOVER = "#c42b1c"
RADIUS = 12                        # window corner radius

GWL_EXSTYLE = -20
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

        ui.status("Checking for updates")
        ui.progress(None)
        m = fetch_manifest()
        ui.version(f"v{m['version']}")

        if matches(final, m):
            ui.progress(1)
            ui.done("You're up to date", f"{APP_NAME} v{m['version']} is the latest version.")
            return

        had_old = os.path.exists(final)
        if is_locked(final):
            raise UpdateError("Close it, then run the Loader again.", f"{APP_NAME} is open")

        ui.status("Downloading update" if had_old else f"Downloading {APP_NAME}",
                  f"v{m['version']}  ·  0%")
        ui.progress(0)
        last = [-1]

        def on_progress(frac):
            ui.progress(frac)
            pct = int(frac * 100)
            if pct != last[0]:                 # don't flood the window with updates
                last[0] = pct
                ui.detail(f"v{m['version']}  ·  {pct}%")

        download(m, part, on_progress)

        ui.status("Installing", "Replacing the old version" if had_old else "Saving the file")
        ui.lock(True)                          # don't allow closing mid-swap
        try:
            os.replace(part, final)            # removes the old exe and puts the new one in its place
        except OSError as e:
            raise UpdateError("The file couldn't be replaced. Make sure "
                              f"{APP_NAME} is closed. ({e})", "Couldn't install the update")
        finally:
            ui.lock(False)

        ui.progress(1)
        ui.done("Update complete" if had_old else "Download complete",
                f"{APP_NAME} v{m['version']} is saved next to this Loader.")
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


class LoaderUI(tk.Tk):
    # design size in pixels at 100% display scaling
    W, H, BAR_H = 440, 286, 38
    CONTENT_W, BAR_THICK, BTN_H = 370, 8, 42

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} Loader")
        self.configure(bg=BG)
        self.k = max(1.0, self.winfo_fpixels("1i") / 96.0)      # display scaling
        self.w, self.h = self.s(self.W), self.s(self.H)
        self._drag_x = self._drag_y = 0
        self._closing = False
        self._locked = False
        self._close_ready = False
        self._target, self._shown = None, 0.0
        self._q = queue.Queue()

        self._load_icon()
        self.overrideredirect(True)
        self._build_bar()
        self._build_body()
        x = (self.winfo_screenwidth() - self.w) // 2
        y = (self.winfo_screenheight() - self.h) // 2
        self.geometry(f"{self.w}x{self.h}+{x}+{y}")

        self.after(50, self._setup_window)
        self.after(40, self._pump)
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
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW     # show in the taskbar
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        self.withdraw()
        self.after(10, self.deiconify)
        r = self.s(RADIUS) * 2
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, self.w + 1, self.h + 1, r, r)
        user32.SetWindowRgn(hwnd, rgn, True)

    def _build_bar(self):
        bar = tk.Frame(self, bg=BAR, height=self.s(self.BAR_H))
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        title = tk.Label(bar, text=f"{APP_NAME} Loader", bg=BAR, fg=TEXT,
                         font=("Segoe UI", 10, "bold"))
        title.pack(side="left", padx=self.s(16))

        x = tk.Label(bar, text="✕", bg=BAR, fg=TEXT, font=("Segoe UI", 11),
                     width=5, cursor="hand2")
        x.pack(side="right", fill="y")
        x.bind("<Button-1>", lambda e: self._fade_close())
        x.bind("<Enter>", lambda e: x.config(bg=CLOSE_HOVER))
        x.bind("<Leave>", lambda e: x.config(bg=BAR))

        self.ver_lbl = tk.Label(bar, text="", bg=BAR, fg=SUBTEXT, font=("Segoe UI", 9))
        self.ver_lbl.pack(side="right", padx=(0, self.s(6)))

        for w in (bar, title, self.ver_lbl):
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
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        s = self.s

        tk.Label(body, text=APP_NAME, bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 22)).place(relx=0.5, y=s(24), anchor="n")

        self.title_lbl = tk.Label(body, text="Starting", bg=BG, fg=TEXT,
                                  font=("Segoe UI Semibold", 12))
        self.title_lbl.place(relx=0.5, y=s(76), anchor="n")

        self.detail_lbl = tk.Label(body, text="", bg=BG, fg=SUBTEXT, font=("Segoe UI", 10),
                                   wraplength=s(self.CONTENT_W + 10), justify="center")
        self.detail_lbl.place(relx=0.5, y=s(102), anchor="n")

        # progress bar (rounded track + rounded fill)
        self.bw, self.bh = s(self.CONTENT_W), s(self.BAR_THICK)
        self.bar = tk.Canvas(body, width=self.bw, height=self.bh, bg=BG, highlightthickness=0)
        self.bar.place(relx=0.5, y=s(162), anchor="n")
        r = self.bh / 2
        rounded_rect(self.bar, 0, 0, self.bw, self.bh, r, fill=TRACK, outline=TRACK)
        self._fill = rounded_rect(self.bar, 0, 0, self.bh, self.bh, r, fill=FILL, outline=FILL,
                                  state="hidden")

        # close button: white, wide, rounded; appears when the Loader is finished
        self.cw, self.ch = s(self.CONTENT_W), s(self.BTN_H)
        self.close_cv = tk.Canvas(body, width=self.cw, height=self.ch, bg=BG,
                                  highlightthickness=0)
        self.close_cv.place(relx=0.5, y=s(186), anchor="n")
        self.close_cv.bind("<Enter>", lambda e: self._close_ready and self._draw_close(BTN_HOVER))
        self.close_cv.bind("<Leave>", lambda e: self._close_ready and self._draw_close(BTN))
        self.close_cv.bind("<Button-1>", lambda e: self._close_ready and self._fade_close())

    def _draw_close(self, color):
        c = self.close_cv
        c.delete("all")
        rounded_rect(c, 1, 1, self.cw - 1, self.ch - 1, self.s(8), fill=color, outline=color)
        c.create_text(self.cw / 2, self.ch / 2, text="Close", fill=BTN_TEXT,
                      font=("Segoe UI Semibold", 12))

    def _show_close(self):
        self._close_ready = True
        self.close_cv.config(cursor="hand2")
        self._draw_close(BTN)

    # ----- thread-safe API used by the worker -----
    def status(self, title, detail=""):
        self._q.put(("status", title, detail))

    def detail(self, text):
        self._q.put(("detail", text))

    def version(self, text):
        self._q.put(("version", text))

    def progress(self, frac):
        self._q.put(("progress", frac))

    def lock(self, on):
        self._q.put(("lock", on))

    def done(self, title, detail=""):
        self._q.put(("done", title, detail))

    def fail(self, title, detail=""):
        self._q.put(("fail", title, detail))

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
                if kind == "status":
                    self.title_lbl.config(text=item[1], fg=TEXT)
                    self.detail_lbl.config(text=clip(item[2]))
                elif kind == "detail":
                    self.detail_lbl.config(text=clip(item[1]))
                elif kind == "version":
                    self.ver_lbl.config(text=item[1])
                elif kind == "progress":
                    if item[1] is None:
                        self._target = None
                    else:
                        if self._target is None:
                            self._shown = 0.0
                        self._target = max(0.0, min(1.0, item[1]))
                elif kind == "lock":
                    self._locked = item[1]
                elif kind == "done":
                    self.title_lbl.config(text=item[1], fg=TEXT)
                    self.detail_lbl.config(text=clip(item[2]))
                    self._target = 1.0
                    self._show_close()
                elif kind == "fail":
                    self.title_lbl.config(text=item[1], fg=ERROR)
                    self.detail_lbl.config(text=clip(item[2]))
                    self._target, self._shown = 0.0, 0.0
                    self._show_close()
        except queue.Empty:
            pass
        self._draw_bar()
        self.after(40, self._pump)

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
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("gunmacro.loader.3")
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    LoaderUI().mainloop()


if __name__ == "__main__":
    main()
