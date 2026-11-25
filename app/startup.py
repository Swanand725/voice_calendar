"""Start the scheduler automatically when you log in to Windows.

    python -m app.startup install    add a shortcut to your Startup folder
    python -m app.startup start      start the scheduler now, without waiting for a restart
    python -m app.startup status     show whether it's installed and running
    python -m app.startup remove     take the shortcut out again

The scheduler runs hidden (pythonw), so check data/scheduler.log if you want to see what it's doing.
"""
import os
import subprocess
import sys
from pathlib import Path

from . import singleton

ROOT = Path(__file__).resolve().parent.parent
SHORTCUT_NAME = "ATLAS scheduler.lnk"
NO_WINDOW = 0x08000000 | 0x00000008          # CREATE_NO_WINDOW | DETACHED_PROCESS


def startup_folder() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def pythonw() -> Path:
    """The windowless Python next to the one running this, so no console window appears."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return windowless if windowless.exists() else exe


def shortcut_script(link, target, args, workdir) -> str:
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    return ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + q(link) + "); "
            "$s.TargetPath = " + q(target) + "; $s.Arguments = " + q(args) + "; "
            "$s.WorkingDirectory = " + q(workdir) + "; $s.WindowStyle = 7; "
            "$s.Description = 'A.T.L.A.S. reminder scheduler'; $s.Save()")


def _windows_only():
    if os.name != "nt":
        raise SystemExit("This helper is for Windows. On other systems, start 'python -m app.scheduler' yourself.")


def install() -> None:
    _windows_only()
    link = startup_folder() / SHORTCUT_NAME
    script = shortcut_script(link, pythonw(), "-m app.scheduler", ROOT)
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    print(f"Installed: {link}\nThe scheduler will start the next time you log in. "
          "Run 'python -m app.startup start' to start it now.")


def start() -> None:
    _windows_only()
    probe = singleton.acquire()
    if probe is None:
        print("The scheduler is already running.")
        return
    probe.close()                       # release the port so the new copy can claim it
    subprocess.Popen([str(pythonw()), "-m", "app.scheduler"], cwd=ROOT, creationflags=NO_WINDOW,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Started the scheduler in the background.")


def status() -> None:
    probe = singleton.acquire()
    running = probe is None
    if probe is not None:
        probe.close()
    installed = os.name == "nt" and (startup_folder() / SHORTCUT_NAME).exists()
    print(f"Starts at login: {'yes' if installed else 'no'}\nRunning now:     {'yes' if running else 'no'}")


def remove() -> None:
    _windows_only()
    link = startup_folder() / SHORTCUT_NAME
    if link.exists():
        link.unlink()
        print("Removed the startup shortcut.")
    else:
        print("There was no startup shortcut.")


if __name__ == "__main__":
    commands = {"install": install, "start": start, "status": status, "remove": remove}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(__doc__)
    commands[sys.argv[1]]()