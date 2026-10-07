"""
Startup-on-boot registration and app launching.

Windows:
  Writes to HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run (no UAC).
  Creates a Start Menu shortcut via WScript.Shell.

Linux:
  Writes an XDG autostart .desktop file to ~/.config/autostart/.
  Creates an application shortcut in ~/.local/share/applications/.
  Launches background instance via nohup.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_APP_NAME   = "LedMatrixControl"
_APP_LABEL  = "Framework LED Matrix Control"

# Windows registry key
_REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _launch_command() -> str:
    """Return the shell command used to launch the app."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    if _IS_WINDOWS:
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        script = os.path.abspath(sys.argv[0])
        return f'"{pythonw}" "{script}"'

    # Linux — use the current Python interpreter
    script = os.path.abspath(sys.argv[0])
    return f'"{sys.executable}" "{script}"'


def _xdg_autostart_path() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(xdg_config) / "autostart" / f"{_APP_NAME}.desktop"


def _xdg_applications_path() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(xdg_data) / "applications" / f"{_APP_NAME}.desktop"


def _desktop_file_contents() -> str:
    cmd = _launch_command().replace('"', '')   # Exec= line without extra quotes
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_APP_LABEL}\n"
        f"Comment=Framework 16 LED Matrix system monitor\n"
        f"Exec={cmd}\n"
        "Terminal=false\n"
        "Categories=Utility;System;\n"
        "StartupNotify=false\n"
    )


# ---------------------------------------------------------------------------
# is_enabled / enable / disable / sync
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """Return True if the startup entry exists."""
    if _IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN) as key:
                winreg.QueryValueEx(key, _APP_NAME)
                return True
        except FileNotFoundError:
            return False
        except Exception as exc:
            log.debug("startup.is_enabled error: %s", exc)
            return False
    else:
        return _xdg_autostart_path().exists()


def enable() -> None:
    """Create / update the startup entry."""
    if _IS_WINDOWS:
        _enable_windows()
    else:
        _enable_linux()


def _enable_windows() -> None:
    try:
        import winreg
        cmd = _launch_command()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_RUN, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, cmd)
        log.info("Startup enabled: %s", cmd)
    except Exception as exc:
        log.error("Cannot enable startup: %s", exc)


def _enable_linux() -> None:
    try:
        path = _xdg_autostart_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_desktop_file_contents(), encoding="utf-8")
        log.info("Autostart desktop file written: %s", path)
    except Exception as exc:
        log.error("Cannot enable startup: %s", exc)


def disable() -> None:
    """Remove the startup entry."""
    if _IS_WINDOWS:
        _disable_windows()
    else:
        _disable_linux()


def _disable_windows() -> None:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_RUN, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _APP_NAME)
        log.info("Startup disabled")
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.error("Cannot disable startup: %s", exc)


def _disable_linux() -> None:
    try:
        path = _xdg_autostart_path()
        if path.exists():
            path.unlink()
            log.info("Autostart desktop file removed: %s", path)
    except Exception as exc:
        log.error("Cannot disable startup: %s", exc)


def sync(enabled: bool) -> None:
    """Enable or disable startup based on a boolean flag."""
    if enabled:
        enable()
    else:
        disable()


# ---------------------------------------------------------------------------
# launch_background
# ---------------------------------------------------------------------------

def launch_background() -> None:
    """Launch a new background instance of the app without a console window."""
    import subprocess
    if _IS_WINDOWS:
        cmd = _launch_command()
        try:
            subprocess.Popen(
                cmd,
                shell=True,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            log.info("Background instance launched: %s", cmd)
        except Exception as exc:
            log.error("launch_background failed: %s", exc)
    else:
        script = os.path.abspath(sys.argv[0])
        try:
            subprocess.Popen(
                [sys.executable, script],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            log.info("Background instance launched: %s %s", sys.executable, script)
        except Exception as exc:
            log.error("launch_background failed: %s", exc)


# ---------------------------------------------------------------------------
# create_app_shortcut  (Start Menu on Windows, applications dir on Linux)
# ---------------------------------------------------------------------------

def create_start_menu_shortcut() -> None:
    """Create a launcher shortcut so the app can be started without a terminal."""
    if _IS_WINDOWS:
        _create_shortcut_windows()
    else:
        _create_shortcut_linux()


def _create_shortcut_windows() -> None:
    try:
        import win32com.client  # type: ignore  (pywin32)
        shell = win32com.client.Dispatch("WScript.Shell")
        programs_dir = shell.SpecialFolders("Programs")
        link_path = os.path.join(programs_dir, f"{_APP_NAME}.lnk")
        sc = shell.CreateShortCut(link_path)
        if getattr(sys, "frozen", False):
            sc.Targetpath = sys.executable
            sc.WorkingDirectory = os.path.dirname(sys.executable)
        else:
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            script = os.path.abspath(sys.argv[0])
            sc.Targetpath = pythonw
            sc.Arguments = f'"{script}"'
            sc.WorkingDirectory = os.path.dirname(script)
        sc.Description = _APP_LABEL
        sc.save()
        log.info("Start Menu shortcut created: %s", link_path)
    except Exception as exc:
        log.warning("create_start_menu_shortcut: %s", exc)


def _create_shortcut_linux() -> None:
    try:
        path = _xdg_applications_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_desktop_file_contents(), encoding="utf-8")
        log.info("Application shortcut written: %s", path)
    except Exception as exc:
        log.warning("create_start_menu_shortcut (linux): %s", exc)
