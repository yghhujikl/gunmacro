"""
Builds GunMacro.exe and Loader.exe and writes manifest.json into ./release

You normally don't run this yourself: GitHub runs it for you every time you
commit a change (see .github/workflows/release.yml).

To run it by hand (Windows):
    python build.py 1.1.0

Needs these next to this file:  gunmacro.py   loader.py   Icon.png
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP_SCRIPT = os.path.join(HERE, "gunmacro.py")
LOADER_SCRIPT = os.path.join(HERE, "loader.py")
ICON_PNG = os.path.join(HERE, "Icon.png")
OUT = os.path.join(HERE, "release")


def pip_install(pkg):
    print(f"Installing {pkg}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pkg])


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_from_loader():
    """Read REPO from loader.py without importing it."""
    with open(LOADER_SCRIPT, "r", encoding="utf-8") as f:
        m = re.search(r'^REPO\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        sys.exit('Could not find REPO = "..." in loader.py')
    return m.group(1)


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
        sys.exit("Usage: python build.py <version>   e.g. python build.py 1.1.0")
    version = sys.argv[1].strip().lstrip("v")

    for p in (APP_SCRIPT, LOADER_SCRIPT, ICON_PNG):
        if not os.path.exists(p):
            sys.exit(f"Missing file: {p}")
    repo = repo_from_loader()

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
    app_dst = os.path.join(OUT, "GunMacro.exe")
    shutil.copy2(app_exe, app_dst)
    shutil.copy2(loader_exe, os.path.join(OUT, "Loader.exe"))

    manifest = {
        "version": version,
        "url": f"https://github.com/{repo}/releases/download/v{version}/GunMacro.exe",
        "sha256": sha256(app_dst),
        "size": os.path.getsize(app_dst),
    }
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    shutil.rmtree(work, ignore_errors=True)

    print(f"\nDone! Version {version} is in: {OUT}")


if __name__ == "__main__":
    main()