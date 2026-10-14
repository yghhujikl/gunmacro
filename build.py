"""
Builds Loader.exe and GunMacro.exe and writes manifest.json.

Usage (run on Windows):
    python build.py 1.0.1

Put these next to this file:  gunmacro.py   loader.py   Icon.png
Set REPO at the top of loader.py first (your public GitHub repo).

Output goes to the "release" folder:
    Loader.exe      give this to users (they only download it once)
    GunMacro.exe    upload to a GitHub Release (tag v<version>)
    manifest.json   upload to the SAME release
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP_SCRIPT = os.path.join(HERE, "gunmacro.py")
LOADER_SCRIPT = os.path.join(HERE, "loader.py")
ICON_PNG = os.path.join(HERE, "Icon.png")
OUT = os.path.join(HERE, "release")

# Extra support files shipped next to GunMacro.exe (must sit in this folder).
# Example: EXTRA_FILES = ["sounds.wav"]. Python code is already inside GunMacro.exe.
EXTRA_FILES = []


def pip_install(pkg):
    print(f"Installing {pkg}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pkg])


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pyinstaller(script, name, ico, work, extra=()):
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--noconsole", "--clean", "--noconfirm",
        "--name", name, "--icon", ico,
        "--add-data", f"{ICON_PNG};.",
        "--distpath", os.path.join(work, "dist"),
        "--workpath", os.path.join(work, "build_" + name),
        "--specpath", work,
        *extra, script,
    ]
    print(f"\nBuilding {name}.exe ...")
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"Build of {name}.exe failed. Scroll up for the error.")
    return os.path.join(work, "dist", name + ".exe")


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python build.py <version>   e.g. python build.py 1.0.1")
    version = sys.argv[1].strip().lstrip("v")

    sys.path.insert(0, HERE)
    import loader  # only to read REPO / LOADER_VERSION
    if loader.REPO.startswith("YOUR_"):
        sys.exit("Set REPO at the top of loader.py first (e.g. \"yourname/gunmacro\").")
    for p in (APP_SCRIPT, LOADER_SCRIPT, ICON_PNG):
        if not os.path.exists(p):
            sys.exit(f"Missing file: {p}")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        pip_install("pyinstaller")
    try:
        from PIL import Image
    except ImportError:
        pip_install("pillow")
        from PIL import Image

    work = tempfile.mkdtemp(prefix="gunmacro_build_")
    ico = os.path.join(work, "Icon.ico")
    Image.open(ICON_PNG).convert("RGBA").save(
        ico, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])

    app_exe = pyinstaller(APP_SCRIPT, "GunMacro", ico, work)
    loader_exe = pyinstaller(
        LOADER_SCRIPT, "Loader", ico, work,
        extra=[arg for m in ("unittest", "pydoc", "sqlite3", "xmlrpc", "lib2to3", "curses")
               for arg in ("--exclude-module", m)])

    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    shipped = [("GunMacro.exe", app_exe)] + [(n, os.path.join(HERE, n)) for n in EXTRA_FILES]
    base = f"https://github.com/{loader.REPO}/releases/download/v{version}"
    files = []
    for name, src in shipped:
        dst = os.path.join(OUT, name)
        shutil.copy2(src, dst)
        files.append({"name": name, "url": f"{base}/{name}",
                      "sha256": sha256(dst), "size": os.path.getsize(dst)})
    shutil.copy2(loader_exe, os.path.join(OUT, "Loader.exe"))

    manifest = {"version": version, "min_loader": loader.LOADER_VERSION, "files": files}
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    shutil.rmtree(work, ignore_errors=True)

    print(f"\nDone! Version {version} is in: {OUT}")
    print(f"\nTo publish: on https://github.com/{loader.REPO}/releases create a release")
    print(f"tagged v{version} and upload GunMacro.exe + manifest.json (plus any extra files).")
    print("Users who already have Loader.exe will update the next time they open it.")


if __name__ == "__main__":
    main()