# Installation

## Windows

### Requirements
- Windows 10 / 11 (x64)
- Framework 16 with LED Matrix Input Module inserted
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) *(optional — required for CPU/DDR temperatures and iGPU load)*

### Steps

1. Download **`LedMatrixControl-windows-x64.exe`** from the release assets.
2. Double-click to run — no installer needed. The app starts silently and appears in the system tray.
3. Right-click the tray icon to configure display modes, bar slots, brightness, and alerts.
4. To start automatically on boot: **right-click tray → Start on boot**.

### Temperatures and iGPU load

Install [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor), launch it once, and leave it running in the background. The LED app connects to it automatically on the next start.

### Uninstall

Delete the `.exe`. If you enabled "Start on boot", disable it from the tray first (or remove the registry entry manually under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → `LedMatrixControl`).

---

## Linux

Tested on **Fedora 39+**, **Ubuntu 22.04+**, **Debian 12+**.

### Requirements
- x86-64 system
- Framework 16 with LED Matrix Input Module inserted
- A desktop environment with a system tray (see note below)

### 1 — Serial port access

Add your user to the `dialout` group so the app can open the serial port without `sudo`:

```bash
sudo usermod -aG dialout $USER
```

**Log out and back in** (or reboot) for the group change to take effect.

### 2 — System tray support

The app uses a system tray icon. What you need depends on your desktop:

| Desktop | What to install |
|---------|----------------|
| **GNOME** | `gnome-shell-extension-appindicator` — see below |
| **KDE Plasma** | Nothing — works out of the box |
| **XFCE** | Nothing — works out of the box |
| **Other** | Install `libayatana-appindicator` for your distro |

#### GNOME — AppIndicator extension required

GNOME does not support system tray icons natively. The app uses the AppIndicator protocol, which requires a separate GNOME Shell extension regardless of what other extensions you have installed. **Dash to Panel, Arc Menu, and similar extensions do not include this — you need it in addition to them.**

**Fedora:**
```bash
sudo dnf install gnome-shell-extension-appindicator
```

**Ubuntu / Debian:**
```bash
sudo apt install gnome-shell-extension-appindicator
```

**Enable the extension** (required after installing):
```bash
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
```
Alternatively, open the **GNOME Extensions** app and toggle *AppIndicator and KStatusNotifierItem Support* on.

**Log out and back in** after enabling for the extension to take effect.

> **No tray icon after following the steps above?**
> Run the app from a terminal — if the tray fails it prints the exact error and keeps the LED matrix running headless. You can still use it without the tray; the config file at `~/.config/LedControl/config.json` can be edited by hand.

### 3 — Run the app

```bash
chmod +x LedMatrixControl-linux-x64
./LedMatrixControl-linux-x64
```

The app appears in the system tray. Right-click to configure.

### 4 — Start on boot *(optional)*

Enable from the tray: **right-click → Start on boot**. This writes an XDG autostart file to `~/.config/autostart/LedMatrixControl.desktop` — it will start automatically on next login.

To disable, either uncheck it from the tray or delete the file:
```bash
rm ~/.config/autostart/LedMatrixControl.desktop
```

### Temperatures *(optional)*

Install `lm-sensors` and run the detection tool once:

**Fedora:**
```bash
sudo dnf install lm_sensors
sudo sensors-detect --auto
```

**Ubuntu / Debian:**
```bash
sudo apt install lm-sensors
sudo sensors-detect --auto
```

The LED app reads sensor values via `psutil` — no extra configuration needed after `sensors-detect`.

**iGPU busy %**: only available on AMD hardware. The app reads it from the kernel DRM sysfs interface automatically — no extra software required.

### Uninstall

Delete the binary. If you enabled autostart:
```bash
rm ~/.config/autostart/LedMatrixControl.desktop
rm ~/.local/share/applications/LedMatrixControl.desktop
```

---

## Configuration

Settings are stored in:
- **Windows**: `%APPDATA%\LedControl\config.json`
- **Linux**: `~/.config/LedControl/config.json`

The file is created automatically on first run with defaults. You can edit it by hand or use the tray menu — changes take effect immediately.
