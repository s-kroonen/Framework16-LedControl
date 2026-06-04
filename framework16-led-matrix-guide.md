# Claude Code Guide: Framework 16 LED Matrix System Monitor

A step-by-step guide for using Claude Code to build a lightweight Windows background process
that displays CPU, RAM, and disk usage on the Framework 16 LED matrix input module, with a
system tray icon for live control.

---

## Prerequisites

Before opening Claude Code, have the following ready:

- Python 3.10+ installed (add to PATH during install)
- Framework 16 with LED matrix input module inserted
- The COM port your matrix is on (check Device Manager → Ports)
- Claude Code installed and authenticated

---

## Project overview

What you are building:

```
matrix-monitor/
├── main.py            # Entry point — starts threads, launches tray icon
├── poller.py          # psutil stats collection thread
├── renderer.py        # Converts stats into 9×34 LED frames
├── tray.py            # pystray system tray icon and right-click menu
├── config.py          # Loads and saves config.json
├── modes/
│   ├── __init__.py
│   ├── bar_chart.py   # Vertical bar chart per stat
│   ├── rolling_graph.py  # Scrolling history graph
│   └── text_scroll.py    # Scrolling text readout
├── config.json        # User settings (auto-created on first run)
├── icon.png           # 64×64 tray icon image
├── requirements.txt
└── build.bat          # PyInstaller build script
```

---

## Step 1 — Start the project

Open a terminal in an empty folder and start Claude Code:

```bash
mkdir matrix-monitor
cd matrix-monitor
claude
```

Paste this prompt:

```
Create a new Python project called matrix-monitor. Set up the folder structure below, create
a requirements.txt with these dependencies, and create a minimal main.py that just prints
"Matrix Monitor starting..." then exits cleanly.

Folder structure:
matrix-monitor/
├── main.py
├── poller.py
├── renderer.py
├── tray.py
├── config.py
├── modes/
│   ├── __init__.py
│   ├── bar_chart.py
│   ├── rolling_graph.py
│   └── text_scroll.py
├── config.json
└── requirements.txt

requirements.txt contents:
framework16_inputmodule
psutil
pystray
Pillow
pyserial
pyinstaller

Create all files as empty stubs with a single-line docstring describing what each file does.
```

---

## Step 2 — Config system

Prompt:

```
Implement config.py. It should:

1. Define a DEFAULT_CONFIG dict with these keys:
   - "com_port": "COM3"
   - "mode": "bar_chart"
   - "stats": ["cpu", "ram", "disk"]
   - "update_interval": 2
   - "brightness": 120
   - "scroll_speed": 2

2. Provide a load() function that reads config.json from the same directory as config.py.
   If the file does not exist, write the defaults and return them.
   If a key is missing from the file, fill it in from defaults and save the merged result.

3. Provide a save(data: dict) function that writes config.json with indent=2.

4. Provide a get() function that returns the current loaded config as a dict.

5. Call load() at module import time so config is always ready.

Keep it simple — no classes needed, just module-level functions and a module-level _config dict.
```

---

## Step 3 — Stats poller

Prompt:

```
Implement poller.py. It should:

1. Import psutil and threading.

2. Define a module-level dict called STATS with keys:
   "cpu", "ram", "disk", "net_up", "net_down"
   All values default to 0.0.

3. Define a _poll() function that runs in a loop:
   - Read CPU percent (interval=None, non-blocking)
   - Read RAM percent from psutil.virtual_memory()
   - Read disk percent from psutil.disk_usage('/')
   - Read network bytes sent/received using psutil.net_io_counters(), calculate MB/s delta
     between polls (store previous values as module-level vars)
   - Update STATS with the new values
   - Sleep for config.get()["update_interval"] seconds

4. Define a start() function that launches _poll() in a daemon thread (daemon=True so it
   dies when the main process exits).

5. The poller must never crash — wrap the loop body in try/except and log errors to stderr.
```

---

## Step 4 — Display modes

### Bar chart mode

Prompt:

```
Implement modes/bar_chart.py.

The Framework 16 LED matrix is 9 columns wide and 34 rows tall (9×34).
A "frame" is a list of 34 lists, each containing 9 brightness values (0–255).

Implement a render(stats: dict) -> list function that:

1. Takes the STATS dict from poller.py (keys: cpu, ram, disk, net_up, net_down).
2. Reads config.get()["stats"] to know which stats to show (e.g. ["cpu", "ram", "disk"]).
3. Divides the 9 columns evenly across the active stats, with a 1-column gap between each bar.
4. For each stat:
   - Calculate bar height as int(value / 100 * 34) pixels from the bottom.
   - Fill those pixels with brightness 200.
   - Fill the remaining pixels above with brightness 0.
5. Returns the completed 34×9 frame as a list of lists.

If fewer than 3 stats are selected, center the bars horizontally.
Handle division by zero if stats list is empty by returning a blank frame.
```

### Rolling graph mode

Prompt:

```
Implement modes/rolling_graph.py.

It should maintain a rolling history buffer of the last 34 readings for each active stat.
Use collections.deque(maxlen=34) per stat.

Implement:
1. update(stats: dict) — appends the current values to each stat's deque.
2. render(stats: dict) -> list — builds a 34×9 frame where:
   - Each column (left to right) represents one time step, oldest on the left.
   - The 9 rows are split vertically between active stats (e.g. 3 stats = 3 rows each).
   - Within each stat's row band, illuminate pixels from the bottom up proportional to the
     stat value (0–100% maps to 0–band_height pixels), brightness 220.
   - A single bright pixel (brightness 255) marks the top of each bar as a peak indicator.
   - Unused pixels are 0.

Import update() and call it every render cycle from renderer.py.
```

### Text scroll mode

Prompt:

```
Implement modes/text_scroll.py.

The LED matrix is 9 wide × 34 tall and is oriented vertically (portrait).
Text should scroll upward from the bottom.

Implement a simple 5×3 pixel bitmap font for digits 0–9 and the characters:
C R A M D I S K % . space

Define each character as a list of 5 rows, each row a list of 3 column bits (1=on, 0=off).

Implement:
1. build_message(stats: dict) -> str — formats a string like "CPU 45% RAM 61% DSK 23%"
   using only the active stats from config.
2. render(stats: dict) -> list — returns a 34×9 frame showing the message scrolling upward.
   Use a module-level scroll_offset that increments by config["scroll_speed"] pixels per call.
   Reset when the message has fully scrolled past.
   Center the text horizontally in the 9-column grid.
   Brightness for lit pixels: 200.
```

---

## Step 5 — Frame renderer

Prompt:

```
Implement renderer.py.

This module bridges the display modes and the hardware.

1. Import framework16_inputmodule and each mode from the modes/ package.

2. Define a connect(com_port: str) function that opens a serial connection to the LED matrix
   using framework16_inputmodule. Store the connection as a module-level variable.
   Return True on success, False on failure (log the error, do not crash).

3. Define a send_frame(frame: list) function that sends a 34×9 brightness frame to the matrix.
   The frame is a list of 34 rows × 9 columns, values 0–255.
   Use the framework16_inputmodule API to send the full frame in one call.

4. Define a render_loop() function that runs in a loop:
   - Read config["mode"] to pick the active mode module.
   - Call that mode's render(stats) with poller.STATS.
   - Call send_frame() with the result.
   - Sleep for config["update_interval"] seconds.
   - Wrap in try/except — on serial error, attempt reconnect once then continue.

5. Define a start() function that calls connect() then launches render_loop() in a daemon thread.

6. Define a set_brightness(value: int) function that sends a brightness command (0–255)
   to the matrix immediately.
```

---

## Step 6 — System tray icon

Prompt:

```
Implement tray.py using pystray and Pillow.

1. Generate a simple 64×64 tray icon programmatically using Pillow:
   - Dark background (#1a1a2e)
   - A 3×5 grid of small white squares representing the LED matrix
   - Save it as icon.png next to the script on first run if it does not exist.

2. Build a pystray.Icon with a right-click menu containing:

   "Matrix Monitor"        ← title, disabled
   ─────────────────
   Mode ►
     ● Bar chart
       Rolling graph
       Text scroll
   ─────────────────
   Stats ►
     ✓ CPU
     ✓ RAM
     ✓ Disk
       Network up
       Network down
   ─────────────────
   Brightness ►
     Low (60)
     Medium (120)
     High (200)
     Max (255)
   ─────────────────
   Update interval ►
     Fast (1s)
     Normal (2s)
     Slow (5s)
   ─────────────────
   Open config file
   ─────────────────
   Quit

3. Each menu action should update config via config.save() and take effect on the next
   render cycle — no restart needed.

4. "Open config file" should open config.json in the default text editor using os.startfile().

5. Define a start(on_quit_callback) function that runs icon.run() — this is blocking and
   must be called from the main thread.

6. Checked items (active mode, enabled stats, current brightness, current interval) should
   reflect the current config when the menu is opened. Use pystray's checked= parameter.
```

---

## Step 7 — Main entry point

Prompt:

```
Implement main.py as the application entry point.

1. Import config, poller, renderer, tray.

2. In a main() function:
   a. Call config.load() (already called at import, but call explicitly for clarity).
   b. Print the loaded COM port and mode to stderr for debugging.
   c. Call poller.start() to begin collecting stats in the background.
   d. Call renderer.start() to begin sending frames to the matrix.
   e. Call tray.start(on_quit) where on_quit calls sys.exit(0).

3. The call to tray.start() is blocking — it runs the pystray main loop on the main thread.
   Everything else runs in daemon threads.

4. Wrap main() in if __name__ == "__main__": and catch KeyboardInterrupt gracefully.

5. Use pythonw.exe compatibility — do not write to stdout (pystray on Windows silently drops
   stdout). Use stderr for all debug output, or a simple log file.
```

---

## Step 8 — Test the full loop

Prompt:

```
The project is now structurally complete. Help me test it step by step.

1. First, verify I can import everything without errors:
   python -c "import config; import poller; import renderer; import tray; print('All imports OK')"

2. Check that config.json was created with defaults. Show me its contents.

3. Test the poller in isolation — write a quick inline test that starts the poller, waits 3
   seconds, and prints the STATS dict.

4. Test the serial connection separately — write a snippet that connects to the COM port from
   config.json, sends a blank frame (all zeros), then sends a full-brightness frame, and
   disconnects. This confirms the hardware path works before running the full app.

5. If any step fails, diagnose the error and fix it.
```

---

## Step 9 — Windows startup integration

Prompt:

```
Add Windows startup support. Create two files:

1. install_startup.py — a script the user runs once that registers matrix-monitor to start
   with Windows using Task Scheduler (schtasks). It should:
   - Find the path to pythonw.exe automatically using sys.executable.
   - Find the absolute path to main.py.
   - Run a schtasks /create command that:
     * Triggers on user logon
     * Runs pythonw.exe with main.py as the argument
     * Task name: "Framework Matrix Monitor"
     * Run whether user is logged in or not: no (interactive only)
     * Hidden: yes (no console window)
   - Print success or error.
   - Require no admin rights (use /sc onlogon /ru currentuser).

2. uninstall_startup.py — removes the task with schtasks /delete.

Also create a run_silent.vbs as an alternative startup method for users who prefer the
Startup folder approach over Task Scheduler:

   CreateObject("WScript.Shell").Run "pythonw.exe ""C:\path\to\main.py""", 0, False

With a comment explaining the user must edit the path.
```

---

## Step 10 — Build a standalone executable

Prompt:

```
Create build.bat — a Windows batch script that uses PyInstaller to compile the project into
a single .exe with no console window.

The command should:
- Use --onefile to produce a single matrix-monitor.exe
- Use --noconsole (equivalent to --windowed) so no terminal appears
- Use --name "matrix-monitor"
- Include config.json and icon.png as data files with --add-data
- Set the icon to icon.png with --icon
- Output to a dist/ folder

Also write a brief comment at the top of the .bat explaining that the user should run this
from the project root after activating their virtual environment.

After building, show the user the command to run the exe and verify it appears in the
system tray.
```

---

## Step 11 — Polish and error handling

Prompt:

```
Review the entire project for robustness. Fix or add the following:

1. If the COM port in config.json does not exist or the matrix is disconnected, the renderer
   should retry the connection every 10 seconds instead of crashing. Show a tooltip on the
   tray icon: "Matrix disconnected — retrying...".

2. If psutil cannot read disk usage on '/' (Windows uses drive letters), fall back to
   psutil.disk_usage('C:/').

3. Add a --com-port CLI argument to main.py so the user can override the config port:
   pythonw.exe main.py --com-port COM5

4. Add a simple rotating log file (max 1 MB, 1 backup) using Python's logging module.
   Log to matrix-monitor.log in the same directory as main.py.
   Replace all print/stderr calls with logger calls.

5. Ensure the app handles Windows sleep/wake correctly — after a sleep event, force a
   reconnect to the matrix (use win32api or a simple threading.Event for this).
```

---

## Step 12 — Optional additions

Once the core app works, you can extend it with these follow-up prompts:

### GPU usage (NVIDIA)

```
Add GPU usage monitoring using the pynvml library (pip install pynvml).
Add "gpu" as a supported stat in poller.py. Fall back gracefully if no NVIDIA GPU is found.
Add "GPU" as a toggleable option in the tray Stats submenu.
```

### Custom mode: clock

```
Add modes/clock.py. It should display the current time as scrolling text using the existing
bitmap font from text_scroll.py. Format: "HH:MM" using a colon character (add the colon to
the font if missing). This mode does not need poller.STATS — it reads datetime.now() directly.
Register it in renderer.py and add "Clock" to the tray Mode submenu.
```

### Settings GUI

```
Add a simple settings window using tkinter (built into Python, no extra install).
Accessible from a "Settings..." tray menu item. It should show:
- COM port dropdown (auto-populated by scanning available serial ports with serial.tools.list_ports)
- Mode selector (radio buttons)
- Stats checkboxes
- Brightness slider (0–255)
- Update interval radio buttons
A "Save" button writes to config.json and closes the window.
A "Save & Apply" button does the same and immediately updates the running renderer.
```

### Dual matrix support

```
The Framework 16 can have two LED matrix modules (left and right input bays).
Update renderer.py to support two simultaneous connections.
Add config keys "com_port_left" and "com_port_right".
Add a tray option "Dual matrix mode" that, when enabled, mirrors the frame to both matrices
or shows different stats on each (left = CPU/RAM, right = disk/network).
```

---

## Troubleshooting prompts

Keep these ready to paste if something goes wrong:

**Serial port not found:**
```
The matrix is connected but Python cannot find it. Help me list all available serial ports
using serial.tools.list_ports and match the Framework LED matrix by its USB VID:PID
(0x32AC:0x0020). Update config.py to auto-detect the port if com_port is set to "auto".
```

**Tray icon not appearing:**
```
The pystray icon is not showing in the Windows system tray. Check whether the icon image is
valid (64×64 RGB PNG), whether pystray.Icon.run() is being called from the main thread, and
whether any exception is being swallowed silently. Add explicit error logging around the
tray setup.
```

**High CPU usage:**
```
The monitor itself is using too much CPU. Profile it — add a simple elapsed-time log to the
render loop and identify whether the bottleneck is psutil polling, frame rendering, or serial
writes. Suggest fixes: increase the update interval, reduce serial write frequency if the
frame has not changed, or move the network delta calculation off the hot path.
```

**PyInstaller missing modules:**
```
The compiled .exe crashes with ModuleNotFoundError for framework16_inputmodule or pystray.
Add the missing packages as hidden imports in the PyInstaller command using --hidden-import.
List the exact flags needed.
```

---

## Final checklist

Before shipping, ask Claude Code to verify:

```
Run through this checklist and fix anything that fails:

[ ] python main.py starts without errors and the tray icon appears
[ ] Right-clicking the tray icon shows the full menu
[ ] Switching modes updates the matrix on the next cycle
[ ] Disconnecting the matrix USB and reconnecting recovers automatically
[ ] install_startup.py registers the task and it survives a reboot
[ ] The compiled .exe runs without a Python install present
[ ] config.json is created automatically on first run
[ ] The log file is created at matrix-monitor.log
[ ] CPU usage of the monitor process is under 1% at steady state
[ ] Memory usage is under 50 MB
```
