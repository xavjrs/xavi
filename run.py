"""XAVI launcher: one command that works on Windows, macOS and Linux.

    python run.py web        the web app (Flask)        -> http://127.0.0.1:5000
    python run.py app        the desktop dashboard (Streamlit) -> http://127.0.0.1:8501
    python run.py terminal   the terminal version

The first run creates a private environment in .venv and installs what the chosen version needs
(about a minute; it needs internet). Later runs start straight away. Needs Python 3.10 or newer.
Extra option: --no-open (don't open the browser).
"""

import hashlib
import os
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
MIN_PYTHON = (3, 10)

MODES = {
    # name: (requirements file, command after the interpreter, default port or None)
    "web": ("web/requirements.txt", ["web/app.py"], 5000),
    "app": ("requirements.txt", ["-m", "streamlit", "run", "app.py", "--server.headless", "true",
                                 "--server.address", "127.0.0.1", "--server.port", "8501"], 8501),
    "terminal": ("requirements.txt", ["portfolio_tracker.py"], None),
}


def venv_python():
    folder, exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(VENV, folder, exe)


def fail(message):
    print(f"\n{message}\n")
    sys.exit(1)


def ensure_environment(requirements):
    """Create .venv if needed and install `requirements` into it (again only when the file changes)."""
    python = venv_python()
    if not os.path.exists(python):
        print("Setting up XAVI for the first time (this takes a minute)...")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    path = os.path.join(ROOT, requirements)
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    stamp = os.path.join(VENV, f".installed-{os.path.basename(os.path.dirname(path)) or 'root'}-{os.path.basename(path)}")
    if os.path.exists(stamp) and open(stamp, encoding="utf-8").read().strip() == digest:
        return python
    print(f"Installing packages from {requirements} ...")
    command = [python, "-m", "pip", "install", "--disable-pip-version-check", "-q",
               "--retries", "6", "--timeout", "60", "-r", path]
    for attempt in range(1, 4):                       # a dropped connection mid-download is common: try again
        if subprocess.run(command).returncode == 0:
            break
        if attempt < 3:
            print(f"Download interrupted (attempt {attempt} of 3). Trying again...")
            time.sleep(3)
    else:
        fail("Installing the packages failed. Check your internet connection and run it again\n"
             "(it carries on where it stopped). If it keeps failing, your Python version may be too\n"
             "new or too old for a package; Python 3.11, 3.12 or 3.13 is safest.")
    with open(stamp, "w", encoding="utf-8") as handle:
        handle.write(digest)
    return python


def open_browser_when_ready(url):
    """Open the browser once the server answers (so you never land on 'site can't be reached')."""
    for _ in range(120):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(url, timeout=2)
            webbrowser.open(url)
            return
        except Exception:
            continue


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    mode = args[0] if args else ""
    if mode not in MODES:
        fail(__doc__.strip())
    if sys.version_info < MIN_PYTHON:
        fail(f"XAVI needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer; you have {sys.version.split()[0]}.\n"
             "Download it from https://www.python.org/downloads/")
    requirements, command, port = MODES[mode]
    python = ensure_environment(requirements)
    env = dict(os.environ)
    if mode == "web":
        env.setdefault("XAVI_PORT", str(port))
        port = int(env["XAVI_PORT"])
    if port and "--no-open" not in argv:
        threading.Thread(target=open_browser_when_ready, args=(f"http://127.0.0.1:{port}",), daemon=True).start()
    print(f"Starting XAVI ({mode}). Press Ctrl+C in this window to stop it.")
    try:
        sys.exit(subprocess.call([python] + command, cwd=ROOT, env=env))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main(sys.argv[1:])
