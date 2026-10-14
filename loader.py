r"""
Gun macro Loader  ->  built into Loader.exe

Checks the update server, downloads the app only when there is a new version,
verifies every file (SHA-256), swaps the files in safely, then launches the app.

Everything is stored per-user in %LOCALAPPDATA%\GunMacro (no admin needed):
    app\      GunMacro.exe + support files (managed by this loader)
    data\     the app's configs / last session (never touched by updates)
    state.json   which version is installed
"""
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import tkinter as tk

# ====================== SET THIS ONCE ======================
REPO = "yshhsdbjsj-sketch/gunmacro"
# ===========================================================

APP_NAME = "Gun macro"
LOADER_VERSION = 1                 # bump only if the loader itself needs a re-download
EXE_NAME = "GunMacro.exe"
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/manifest.json"

ROOT_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "GunMacro")
APP_DIR = os.path.join(ROOT_DIR, "app")
STAGE_DIR = os.path.join(ROOT_DIR, "staging")
STATE_FILE = os.path.join(ROOT_DIR, "state.json")

TIMEOUT = 20                       # seconds per network call
RETRIES = 3
CHUNK = 64 * 1024
MAX_MANIFEST = 256 * 1024
MAX_FILE = 500 * 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
VER_RE = re.compile(r"^[0-9A-Za-z._-]{1,32}$")

BG, TEXT, SUBTEXT = "#141414", "#e6e6e6", "#7a7a7a"
TRACK, FILL, ERROR = "#2e2e2e", "#ffffff", "#e5737a"


class UpdateError(Exception):
    pass


class AppRunning(UpdateError):
    pass


class BadDownload(OSError):
    pass


# ---------- small helpers ----------
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


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


# ---------- network ----------
def open_url(url):
    if not url.lower().startswith("https://"):
        raise UpdateError("Refusing to use a non-HTTPS address.")
    req = urllib.request.Request(url, headers={"User-Agent": f"GunMacroLoader/{LOADER_VERSION}"})
    resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    if not resp.geturl().lower().startswith("https://"):
        resp.close()
        raise UpdateError("The server redirected to a non-HTTPS address.")
    return resp


def validate_manifest(m):
    bad = UpdateError("The update info from the server is invalid.")
    if not isinstance(m, dict):
        raise bad
    version = m.get("version")
    if not isinstance(version, str) or not VER_RE.match(version):
        raise bad
    files = m.get("files")
    if not isinstance(files, list) or not files:
        raise bad
    out, seen = [], set()
    for f in files:
        if not isinstance(f, dict):
            raise bad
        name, url, sha, size = f.get("name"), f.get("url"), f.get("sha256"), f.get("size")
        if not (isinstance(name, str) and NAME_RE.match(name)):
            raise bad
        if name.lower() in seen or name.lower() == "loader.exe":
            raise bad
        if not (isinstance(url, str) and url.lower().startswith("https://")):
            raise bad
        if not (isinstance(sha, str) and SHA_RE.match(sha.lower())):
            raise bad
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE:
            raise bad
        seen.add(name.lower())
        out.append({"name": name, "url": url, "sha256": sha.lower(), "size": size})
    if EXE_NAME.lower() not in seen:
        raise bad
    min_loader = m.get("min_loader", 1)
    if isinstance(min_loader, bool) or not isinstance(min_loader, int):
        raise bad
    return {"version": version, "files": out, "min_loader": min_loader}


def fetch_manifest():
    last = None
    for attempt in range(RETRIES):
        try:
            with open_url(MANIFEST_URL) as resp:
                raw = resp.read(MAX_MANIFEST + 1)
        except UpdateError:
            raise
        except OSError as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if len(raw) > MAX_MANIFEST:
            raise UpdateError("The update info from the server is invalid.")
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise UpdateError("The update info from the server is invalid.")
        return validate_manifest(data)
    raise UpdateError(f"Couldn't reach the update server. ({last})")


def download_file(entry, dest, progress):
    """Download one file to `dest`, checking size and SHA-256. Retries on failure."""
    last = None
    for attempt in range(RETRIES):
        try:
            h = hashlib.sha256()
            done = 0
            with open_url(entry["url"]) as resp, open(dest, "wb") as out:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > entry["size"]:
                        raise BadDownload("file is larger than expected")
                    h.update(chunk)
                    out.write(chunk)
                    progress(done)
            if done != entry["size"]:
                raise BadDownload("download was cut short")
            if h.hexdigest() != entry["sha256"]:
                raise BadDownload("integrity check failed")
            return
        except UpdateError:
            raise
        except OSError as e:
            last = e
            try:
                os.remove(dest)
            except OSError:
                pass
            time.sleep(1.5 * (attempt + 1))
    raise UpdateError(f"Couldn't download {entry['name']}. ({last})")


# ---------- local install ----------
def file_ok(entry):
    path = os.path.join(APP_DIR, entry["name"])
    try:
        return os.path.getsize(path) == entry["size"] and sha256_file(path) == entry["sha256"]
    except OSError:
        return False


def installed_is_intact(state):
    """The installed exe is the exact one a previous install recorded."""
    path = os.path.join(APP_DIR, EXE_NAME)
    want = (state.get("files") or {}).get(EXE_NAME)
    try:
        return bool(want) and sha256_file(path) == want
    except OSError:
        return False


def recover():
    """If a previous update was interrupted, put the old files back."""
    try:
        names = os.listdir(APP_DIR)
    except OSError:
        return
    for n in names:
        if n.endswith(".bak"):
            bak = os.path.join(APP_DIR, n)
            final = bak[:-4]
            try:
                if os.path.exists(final):
                    os.remove(bak)
                else:
                    os.replace(bak, final)
            except OSError:
                pass


def save_state(manifest):
    write_json_atomic(STATE_FILE, {
        "version": manifest["version"],
        "files": {f["name"]: f["sha256"] for f in manifest["files"]},
    })


def install(todo, staged, manifest, old_state):
    """Swap verified files in. Old files are kept as .bak until everything worked."""
    os.makedirs(APP_DIR, exist_ok=True)
    backups, placed = [], []
    try:
        for f in todo:
            final = os.path.join(APP_DIR, f["name"])
            bak = final + ".bak"
            if os.path.exists(final):
                if os.path.exists(bak):
                    os.remove(bak)
                os.replace(final, bak)
                backups.append((final, bak))
            os.replace(staged[f["name"]], final)
            placed.append(final)
        save_state(manifest)
    except OSError as e:
        for final in placed:
            try:
                os.remove(final)
            except OSError:
                pass
        for final, bak in backups:
            try:
                os.replace(bak, final)
            except OSError:
                pass
        raise UpdateError(f"Couldn't install the update, your old version was kept. ({e})")

    for _final, bak in backups:
        try:
            os.remove(bak)
        except OSError:
            pass
    new_names = {f["name"] for f in manifest["files"]}
    for name in (old_state.get("files") or {}):
        if name not in new_names and isinstance(name, str) and NAME_RE.match(name):
            try:
                os.remove(os.path.join(APP_DIR, name))
            except OSError:
                pass


def download_and_install(ui, manifest, todo, state):
    if is_locked(os.path.join(APP_DIR, EXE_NAME)):
        raise AppRunning("Gun macro is open. Close it, then open the Loader again to update.")
    total = sum(f["size"] for f in todo)
    shutil.rmtree(STAGE_DIR, ignore_errors=True)
    os.makedirs(STAGE_DIR, exist_ok=True)

    ui.status(f"Downloading update... (v{manifest['version']})")
    ui.progress(0)
    staged, before = {}, 0
    for f in todo:
        dest = os.path.join(STAGE_DIR, f["name"] + ".part")
        download_file(f, dest, lambda n, b=before: ui.progress((b + n) / total))
        before += f["size"]
        staged[f["name"]] = dest

    ui.status("Verifying files...")
    for f in todo:
        if sha256_file(staged[f["name"]]) != f["sha256"]:
            raise UpdateError(f"{f['name']} failed the integrity check.")

    ui.status("Installing update...")
    ui.lock(True)
    try:
        install(todo, staged, manifest, state)
    finally:
        ui.lock(False)
        shutil.rmtree(STAGE_DIR, ignore_errors=True)


# ---------- main flow ----------
def launch(ui):
    exe = os.path.join(APP_DIR, EXE_NAME)
    if is_locked(exe):
        ui.fail("Gun macro is already running.")
        return
    ui.status("Launching...")
    try:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([exe], cwd=APP_DIR, close_fds=True, creationflags=flags)
    except OSError as e:
        ui.fail(f"Couldn't start Gun macro. ({e})")
        return
    time.sleep(0.8)
    ui.close()


def run(ui):
    os.makedirs(APP_DIR, exist_ok=True)
    recover()
    shutil.rmtree(STAGE_DIR, ignore_errors=True)
    state = read_json(STATE_FILE)
    state = state if isinstance(state, dict) else {}
    has_working = installed_is_intact(state)

    ui.status("Checking for updates...")
    ui.progress(None)
    try:
        manifest = fetch_manifest()
    except UpdateError as e:
        if has_working:
            ui.status(f"{e}\nLaunching the version you already have...", error=True)
            time.sleep(2.5)
            launch(ui)
        else:
            ui.fail(f"{e}\n\nYou need an internet connection the first time you open the Loader.")
        return

    if manifest["min_loader"] > LOADER_VERSION:
        ui.fail("This Loader is too old for the latest release. Please download the newest Loader.exe.")
        return

    todo = [f for f in manifest["files"] if not file_ok(f)]
    if not todo:
        if state.get("version") != manifest["version"]:
            try:
                save_state(manifest)
            except OSError:
                pass
        ui.progress(1)
        ui.status("You already have the latest version")
        time.sleep(0.9)
        launch(ui)
        return

    try:
        download_and_install(ui, manifest, todo, state)
    except AppRunning as e:
        ui.fail(str(e))
        return
    except UpdateError as e:
        if has_working:
            ui.status(f"Update failed: {e}\nLaunching the version you already have...", error=True)
            time.sleep(3)
            launch(ui)
        else:
            ui.fail(f"Update failed: {e}")
        return

    ui.progress(1)
    ui.status("Update installed")
    time.sleep(0.6)
    launch(ui)


# ---------- window ----------
class LoaderUI(tk.Tk):
    W = 370

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} Loader")
        self.configure(bg=BG)
        self.resizable(False, False)
        w, h = 420, 200
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
        if not self._locked:          # don't allow closing in the middle of swapping files
            self.destroy()

    # thread-safe API used by the worker
    def status(self, text, error=False):
        self._q.put(("status", text, error))

    def progress(self, frac):
        self._q.put(("progress", frac))

    def fail(self, text):
        self._q.put(("fail", text))

    def lock(self, on):
        self._q.put(("lock", on))

    def close(self):
        self._q.put(("close",))

    def _work(self):
        try:
            run(self)
        except Exception as e:  # last-resort safety net
            self.fail(f"Something went wrong. ({e})")

    def _pump(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "status":
                    self.status_lbl.config(text=item[1], fg=ERROR if item[2] else TEXT)
                elif kind == "progress":
                    self._frac = item[1]
                elif kind == "lock":
                    self._locked = item[1]
                elif kind == "fail":
                    self.status_lbl.config(text=item[1], fg=ERROR)
                    self._frac = 0
                    self.close_btn.pack(pady=(14, 0))
                elif kind == "close":
                    self.destroy()
                    return
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
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("gunmacro.loader.1")
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    LoaderUI().mainloop()


if __name__ == "__main__":
    main()