r"""
Gun macro Loader  ->  built into Loader.exe

Its only job is to keep GunMacro.exe up to date. It does NOT launch the app.

Put Loader.exe in any folder and run it. GunMacro.exe is downloaded into that
same folder. Run the Loader again any time to check for an update: the new
version is downloaded, verified (SHA-256), and replaces the old GunMacro.exe.

Your settings live in %LOCALAPPDATA%\GunMacro\data and are never touched.
"""
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
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/manifest.json"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/".lower()

TIMEOUT = 20                       # seconds per network call
RETRIES = 3
CHUNK = 64 * 1024
MAX_MANIFEST = 64 * 1024
MAX_FILE = 500 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
VER_RE = re.compile(r"^[0-9A-Za-z._-]{1,32}$")

BG, TEXT = "#141414", "#e6e6e6"
TRACK, FILL, ERROR = "#2e2e2e", "#ffffff", "#e5737a"


class UpdateError(Exception):
    pass


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


# ---------- network ----------
def open_url(url):
    if not url.lower().startswith("https://"):
        raise UpdateError("Refusing to use a non-HTTPS address.")
    req = urllib.request.Request(url, headers={"User-Agent": "GunMacroLoader/2"})
    resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    if not resp.geturl().lower().startswith("https://"):
        resp.close()
        raise UpdateError("The server redirected to a non-HTTPS address.")
    return resp


def with_retries(what, fn):
    """Run fn(); retry on network trouble, give up at once on 'not found' style errors."""
    last = None
    for attempt in range(RETRIES):
        try:
            return fn()
        except UpdateError:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UpdateError(f"{what}: not found (404). Has a release been published on GitHub?")
            if 400 <= e.code < 500:
                raise UpdateError(f"{what}: the server answered HTTP {e.code}.")
            last = e
        except (OSError, http.client.HTTPException) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise UpdateError(f"{what} failed. Check your internet connection. ({last})")


def fetch_manifest():
    bad = UpdateError("The update info from the server is invalid.")

    def get():
        with open_url(MANIFEST_URL) as resp:
            return resp.read(MAX_MANIFEST + 1)

    raw = with_retries("Checking for updates", get)
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

    with_retries(f"Downloading {EXE_NAME}", get)


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
            raise UpdateError(
                f"Can't write to this folder. Move Loader.exe somewhere like your Desktop. ({e})")

        ui.status("Checking for updates...")
        ui.progress(None)
        m = fetch_manifest()

        if matches(final, m):
            ui.progress(1)
            ui.done(f"You already have the latest version (v{m['version']}).")
            return

        had_old = os.path.exists(final)
        if is_locked(final):
            raise UpdateError(f"{APP_NAME} is open. Close it, then run the Loader again.")

        ui.status(f"{'Updating to' if had_old else 'Downloading'} v{m['version']}...")
        ui.progress(0)
        download(m, part, ui.progress)

        ui.status("Installing...")
        ui.lock(True)                      # don't allow closing mid-swap
        try:
            os.replace(part, final)        # removes the old exe and puts the new one in its place
        except OSError as e:
            raise UpdateError(f"Couldn't replace the old {EXE_NAME}. Is it open? ({e})")
        finally:
            ui.lock(False)

        ui.progress(1)
        ui.done(f"{'Updated to' if had_old else 'Downloaded'} v{m['version']}.\n"
                f"{EXE_NAME} is in the same folder as this Loader.")
    finally:
        remove_quietly(part)


# ---------- window ----------
class LoaderUI(tk.Tk):
    W = 370

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} Loader")
        self.configure(bg=BG)
        self.resizable(False, False)
        w, h = 420, 230
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self._load_icon()

        tk.Label(self, text=APP_NAME, bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 18)).pack(pady=(22, 2))
        self.status_lbl = tk.Label(self, text="Starting...", bg=BG, fg=TEXT,
                                   font=("Segoe UI", 10), wraplength=380, justify="center")
        self.status_lbl.pack(pady=(6, 12))
        self.bar = tk.Canvas(self, width=self.W, height=8, bg=TRACK, highlightthickness=0)
        self.bar.pack()
        self._fill = self.bar.create_rectangle(0, 0, 0, 8, fill=FILL, outline=FILL)
        self.close_btn = tk.Button(self, text="Close", command=self.destroy, bg="#2a2a2a",
                                   fg=TEXT, relief="flat", padx=16, pady=4, cursor="hand2")

        self._frac = None
        self._locked = False
        self._q = queue.Queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self._pump)
        threading.Thread(target=self._work, daemon=True).start()

    def _load_icon(self):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "Icon.png")
        if os.path.exists(path):
            try:
                self._icon = tk.PhotoImage(file=path)
                self.iconphoto(True, self._icon)
            except tk.TclError:
                pass

    def _on_close(self):
        if not self._locked:
            self.destroy()

    # thread-safe API used by the worker
    def status(self, text):
        self._q.put(("status", text))

    def progress(self, frac):
        self._q.put(("progress", frac))

    def lock(self, on):
        self._q.put(("lock", on))

    def done(self, text):
        self._q.put(("done", text))

    def fail(self, text):
        self._q.put(("fail", text))

    def _work(self):
        try:
            run(self)
        except UpdateError as e:
            self.fail(str(e))
        except Exception as e:  # last-resort safety net
            self.fail(f"Something went wrong. ({e})")

    def _pump(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "status":
                    self.status_lbl.config(text=item[1], fg=TEXT)
                elif kind == "progress":
                    self._frac = item[1]
                elif kind == "lock":
                    self._locked = item[1]
                elif kind == "done":
                    self.status_lbl.config(text=item[1], fg=TEXT)
                    self._frac = 1
                    self.close_btn.pack(pady=(14, 0))
                elif kind == "fail":
                    self.status_lbl.config(text=item[1], fg=ERROR)
                    self._frac = 0
                    self.close_btn.pack(pady=(14, 0))
        except queue.Empty:
            pass
        self._draw_bar()
        self.after(40, self._pump)

    def _draw_bar(self):
        if self._frac is None:        # unknown length: sliding block
            pos = (time.time() * 320) % (self.W + 90) - 90
            self.bar.coords(self._fill, max(pos, 0), 0, min(pos + 90, self.W), 8)
        else:
            self.bar.coords(self._fill, 0, 0, self.W * max(0.0, min(1.0, self._frac)), 8)


def main():
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("gunmacro.loader.2")
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    LoaderUI().mainloop()


if __name__ == "__main__":
    main()