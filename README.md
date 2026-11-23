# Mac-Style Dock for Windows

A small Python dock that replaces the Windows taskbar with a frosted-glass bar. Icons magnify when you hover, bounce when you open them, and get a dot underneath while they're open.

It has four groups, with a line between each:

**Start, Search, Task View, Widgets** | **your pinned apps** (Store apps like Copilot too) | **open apps that aren't pinned** | **tray, Wi-Fi, battery, clock**

## Setup

Put `dock.py` in its own folder (for example `C:\Users\you\dock`), then open **cmd** and run:

```
python --version
```
Checks that Python is installed (3.9 or newer; if it says "not found," install it from python.org and check "Add Python to PATH").

```
pip install PySide6
```
Installs PySide6, the library that draws the dock window and icons.

```
cd C:\Users\you\dock
```
Moves you into the folder where you saved `dock.py`.

```
python dock.py
```
Starts the dock, hides the Windows taskbar, and creates an empty `extra_apps.json`.

**Updating from the old version?** Delete the old `apps.json`. The dock now reads your pinned taskbar apps by itself.

**Recommended:** Turn on **Settings → Personalization → Taskbar → Taskbar behaviors → Automatically hide the taskbar**. Without it, Windows still saves an empty strip at the bottom where the taskbar used to be, so maximized windows won't reach the bottom of the screen.

## Using it

- **Hover** over the dock to magnify icons.
- **Click an app** to open it. It bounces while it opens.
- **Click an app with a dot** (it's already open) to switch to its window instead of opening a new one.
- **Open apps that aren't pinned** (like cmd) show up at the end, after a separator, and disappear when you close them, just like the taskbar.
- **Click Start** to open the Start menu.
- **Right-click Start** for the Win+X menu (Task Manager, Settings, Shut down, etc.).
- **Search, Task View, Widgets** work like the taskbar buttons.
- **^ tile:** shows the real Windows taskbar so you can reach tray icons like OneDrive. Click it again to hide the taskbar.
- **Wi-Fi tile:** the bright waves show signal strength (dim = not connected). Hover to see the network name, click to pick a network.
- **Battery tile:** fills up with your charge, turns green with a bolt while charging, and red when low. Click it for Quick Settings. It's left out on PCs without a battery.
- **Clock tile:** opens notifications and the calendar.
- **Auto-hide:** when an app covers the dock, it slides down out of the way. Move the mouse to the bottom edge of the screen to bring it back.
- **Full screen:** the dock hides while a video, game, or F11 browser is full screen, and comes back when you exit.
- **Right-click anywhere else** on the dock and pick **Quit Dock**. The Windows taskbar comes back when the dock closes.

## If the taskbar stays hidden

If the dock crashes or gets force-closed, the taskbar might not come back. To fix it, run:

```
python dock.py --restore
```
Shows the Windows taskbar again without starting the dock.

You can also open Task Manager (Ctrl + Shift + Esc), right-click **Windows Explorer**, and choose **Restart**.

## Adding apps that aren't pinned

Pinned apps update by themselves: pin or unpin something on the taskbar, then restart the dock. To add apps that aren't pinned, open `extra_apps.json` in Notepad:

```json
[
  {"name": "Notepad", "path": "C:/Windows/notepad.exe"},
  {"name": "Downloads", "path": "C:/Users/you/Downloads"}
]
```

- Use **forward slashes** (`/`) in paths.
- `path` can be an `.exe`, a shortcut (`.lnk`), a folder, or a file.
- Want a different icon? Add `"icon": "C:/path/to/icon.png"`.

**Microsoft Store apps** (Calculator, Settings, Terminal) use an app ID instead of a path. Pinned ones should show up by themselves. To add one that isn't pinned, find its ID by running this in cmd:

```
powershell -command "Get-StartApps"
```
Lists every app in your Start menu with its name and app ID.

Then add it with `"aumid"` instead of `"path"`:

```json
{"name": "Calculator", "aumid": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"}
```

## Settings

Change these at the top of `dock.py`:

| Setting | What it does |
|---|---|
| `ICON_SIZE` | Normal icon size. Spacing and padding scale with it, so this one number resizes the whole dock |
| `MAX_SCALE` | How big the hovered icon gets (1.8 = 180%) |
| `MAGNIFY_RANGE` | How far from the cursor icons start growing |
| `ANIMATION_SPEED` | Higher = snappier, lower = smoother |
| `BAR_SHAPE` | `"rounded"` or `"square"`. `"pill"` only works with `FROSTED_GLASS = False`, since Windows 11 can only round the blur by a small amount |
| `FROSTED_GLASS` | `True` blurs what's behind the dock, `False` uses a solid bar |
| `GLASS_TINT` | Color over the blur: (red, green, blue, opacity 0–255) |
| `SOLID_COLOR` | Bar color when frosted glass is off |
| `SHOW_SEPARATOR` | Lines between the groups of icons |
| `TASKBAR_BUTTONS` | Which Windows buttons come after Start: `"search"`, `"task view"`, `"widgets"`. Remove or reorder them |
| `STATUS_TILES` | Tiles at the end: `"tray"`, `"wifi"`, `"battery"`, `"clock"`. Remove or reorder them |
| `AUTO_HIDE` | `"windows"` slides away when an app covers the dock, `"always"` stays hidden until you touch the bottom edge (like a Mac), `"never"` always shows |
| `SHOW_OPEN_DOTS` | Dots under open apps |
| `SHOW_OPEN_APPS` | Show open apps that aren't pinned at the end of the dock |
| `SHORTCUT_FIXES` | If a pinned app's icon shows a little shortcut arrow, add its name and real `.exe` here |
| `BOUNCE_ON_LAUNCH` | Bounce when an app opens |
| `HIDE_IN_FULLSCREEN` | Hide the dock while an app is full screen |
| `HIDE_WINDOWS_TASKBAR` | `False` keeps the normal taskbar visible |
| `START_ICON` | Path to an image to use for the Start button |

**Frosted glass not working?** If the bar isn't blurry (you can read text clearly through it), your version of Windows doesn't support the blur. Set `FROSTED_GLASS = False` to use a solid bar instead.

## Make it an app (Dock.exe)

First quit the dock (right-click it → **Quit Dock**). Put `dock.py` and `dock.ico` together in your dock folder, then run these in **cmd**:

```
cd C:\Users\you\dock
```
Moves into your dock folder.

```
pip install pyinstaller
```
Installs PyInstaller, the tool that packages a Python script into a Windows app.

```
pyinstaller --noconfirm --noconsole --name Dock --icon dock.ico dock.py
```
Builds the app into the `dist\Dock` folder (takes a minute or two); `--noconsole` means no black terminal window, and `--icon` sets the app's icon.

```
mkdir "%LOCALAPPDATA%\Programs"
```
Makes sure the folder for your installed apps exists (if it says it already exists, that's fine).

```
move dist\Dock "%LOCALAPPDATA%\Programs\Dock"
```
Moves the app to a permanent home, the same place apps like VS Code install to.

```
copy extra_apps.json "%LOCALAPPDATA%\Programs\Dock\"
```
Copies your extra apps list to the app (skip this if you never added any).

```
start "" "%LOCALAPPDATA%\Programs\Dock\Dock.exe"
```
Starts the app.

**Make it start when you sign in:** right-click the dock (anywhere but Start) and check **Start when I sign in**. It also shows up in **Settings → Apps → Startup**, where you can turn it off.

**Pin it to Start (optional):** run `explorer "%LOCALAPPDATA%\Programs\Dock"`, right-click **Dock.exe**, and choose **Pin to Start**.

**If the taskbar gets stuck hidden:** run `"%LOCALAPPDATA%\Programs\Dock\Dock.exe" --restore`.

**Updating it later:** quit the dock, run the same `pyinstaller` command, then:
```
xcopy /e /y /i dist\Dock "%LOCALAPPDATA%\Programs\Dock"
```
Copies the new version over the old one and keeps your `extra_apps.json`.

Only one dock can run at a time, so quit the `python dock.py` version before starting `Dock.exe` (or the other way around).
