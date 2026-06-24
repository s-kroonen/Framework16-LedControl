"""
Windows startup-on-boot registration.

Writes / removes a value in:
  HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

This runs per-user (no UAC prompt required).  When frozen as a PyInstaller
.exe, sys.executable is used directly.  When running as a .py script,
pythonw.exe is used so no console window appears on boot.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

_REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "LedMatrixControl"


def _launch_command() -> str:
    """Return the command string to register for startup."""
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller .exe
        return f'"{sys.executable}"'

    # Running as a Python script — use pythonw.exe (no console window)
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    script = os.path.abspath(sys.argv[0])
    return f'"{pythonw}" "{script}"'


def is_enabled() -> bool:
    """Return True if the startup registry entry exists."""
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


def enable() -> None:
    """Create / update the startup registry entry."""
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


def disable() -> None:
    """Remove the startup registry entry if it exists."""
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


def sync(enabled: bool) -> None:
    """Enable or disable startup based on a boolean flag."""
    if enabled:
        enable()
    else:
        disable()


def launch_background() -> None:
    """Launch a new background instance of the app (no console window)."""
    import subprocess
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


def create_start_menu_shortcut() -> None:
    """Create a Start Menu shortcut so the app can be launched without a terminal."""
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
        sc.Description = "Framework 16 LED Matrix Control"
        sc.save()
        log.info("Start Menu shortcut created: %s", link_path)
    except Exception as exc:
        log.warning("create_start_menu_shortcut: %s", exc)
