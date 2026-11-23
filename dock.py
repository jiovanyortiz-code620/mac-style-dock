"""
Mac-style dock for Windows.

The dock has four groups, with a separator line between each:
  Start + Search/Task View/Widgets | pinned apps | open apps that aren't pinned | tray, Wi-Fi, battery, clock

- Reads the apps pinned to your Windows taskbar, including Store apps like Copilot.
- Hides the Windows taskbar while the dock runs, and brings it back when you quit.
- Slides out of the way when an app covers it; move the mouse to the bottom edge to bring it back.
- Frosted-glass background, icons that grow when you hover, and dots under open apps.
- Click an app to open it (it bounces while opening). If it's already open,
  clicking switches to its window instead.
- Click Start for the Start menu, right-click Start for the Win+X menu.
- Click the ^ tile to show the real taskbar's tray icons (OneDrive, etc.).
- Right-click anywhere else on the dock to quit, or to make it start when you sign in.
- Add apps that aren't pinned to your taskbar in extra_apps.json.

If the taskbar ever stays hidden after a crash, run:  python dock.py --restore
(or, for the packaged app:  Dock.exe --restore)
"""

import ctypes
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QDir, QFileInfo, QLockFile, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileIconProvider,
    QMenu,
    QMessageBox,
    QWidget,
)

# ---------------- Settings you can tweak ----------------
ICON_SIZE = 40              # normal icon size in pixels (change this to resize the whole dock)
MAX_SCALE = 1.8             # how big the icon under the cursor gets (1.8 = 180%)
ANIMATION_SPEED = 0.25      # 0 to 1, higher = snappier animation

# These grow and shrink with ICON_SIZE so the dock keeps the same proportions
MAGNIFY_RANGE = ICON_SIZE * 2.5          # how far from the cursor icons start growing
GAP = round(ICON_SIZE * 0.15)            # space between icons
PADDING = round(ICON_SIZE * 0.12)        # space between the icons and the edge of the bar
BOTTOM_MARGIN = round(ICON_SIZE * 0.15)  # gap between the dock and the bottom of the screen

BAR_SHAPE = "rounded"            # "rounded" or "square" ("pill" only works with FROSTED_GLASS = False)
FROSTED_GLASS = True             # blur whatever is behind the dock
GLASS_TINT = (35, 35, 40, 160)   # color over the blur: (red, green, blue, opacity 0-255)
SOLID_COLOR = (45, 45, 48, 245)  # used if FROSTED_GLASS is False or blur isn't available
SHOW_SEPARATOR = True            # lines between the groups of icons
SHOW_OPEN_DOTS = True            # dot under apps that are open
SHOW_OPEN_APPS = True            # add icons for open apps that aren't pinned, like the taskbar
BOUNCE_ON_LAUNCH = True          # icon bounces when you open its app
HIDE_IN_FULLSCREEN = True        # hide the dock while an app is full screen (videos, games, F11)
# "windows" = slide away when the app in front covers the dock,
# "always"  = stay hidden until you move the mouse to the bottom edge (like a Mac),
# "never"   = always visible
AUTO_HIDE = "windows"

# Windows buttons right after Start. Remove any you don't want, or reorder them.
TASKBAR_BUTTONS = ["search", "task view", "widgets"]
# Tiles at the end: "tray" (OneDrive and other tray icons), "wifi", "battery",
# and "clock". Remove any you don't want, or reorder them.
STATUS_TILES = ["tray", "wifi", "battery", "clock"]

HIDE_WINDOWS_TASKBAR = True # False = keep the normal Windows taskbar
START_ICON = ""             # optional: path to an image for the Start button
# --------------------------------------------------------

BOTTOM_PAD = PADDING + round(ICON_SIZE * 0.1)  # room under the icons for the open-app dots
DOT_RADIUS = ICON_SIZE * 0.055    # size of the open-app dots
BOUNCE_HEIGHT = ICON_SIZE * 0.5   # how high an icon jumps
BOUNCE_TIME = 1.0                 # how long the bounce lasts, in seconds
BOUNCES = 2                       # how many hops
SEPARATOR_SPACE = GAP + 2         # extra space for each separator line
LABEL_MAX_WIDTH = 260             # long window titles get cut off with "..."
GRAY_TILE = (62, 62, 70)          # background color of the button and status tiles
SLIDE_SPEED = 0.25                # how fast the dock slides away / back (0 to 1)
HIDE_DELAY = 0.5                  # seconds to wait before sliding away
# Windows' own pop-ups (Start, Search, the desktop...) don't count as "covering" the dock
SHELL_WINDOWS = (
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
    "TopLevelWindowForOverflowXamlIsland", "NotifyIconOverflowWindow",
)

ON_WINDOWS = sys.platform == "win32"
# When packaged as Dock.exe, keep extra_apps.json next to the .exe, not in a temp folder
if getattr(sys, "frozen", False):
    APP_FOLDER = Path(sys.executable).parent
else:
    APP_FOLDER = Path(__file__).parent
EXTRA_APPS_FILE = APP_FOLDER / "extra_apps.json"
# Windows keeps a shortcut (.lnk) for every normal app pinned to the taskbar in this folder
PINNED_FOLDER = (
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"
)
# Some pinned shortcuts (like Windows 11's File Explorer) don't point at a normal
# program file, so their icon gets a little shortcut arrow. List the real program here.
WINDOWS_FOLDER = os.environ.get("WINDIR", "C:/Windows")
SHORTCUT_FIXES = {
    "file explorer": os.path.join(WINDOWS_FOLDER, "explorer.exe"),
}
# Store apps have an ID like "Microsoft.WindowsStore_8wekyb3d8bbwe!App"
STORE_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*_[a-z0-9]{13}![A-Za-z][A-Za-z0-9.]*")


# ---------------- Windows API (you don't need to change this part) ----------------

VK_TAB, VK_LWIN = 0x09, 0x5B
VK_A, VK_B, VK_N, VK_S, VK_W, VK_X = 0x41, 0x42, 0x4E, 0x53, 0x57, 0x58
KEY_UP = 0x0002
SW_HIDE, SW_SHOW, SW_RESTORE = 0, 5, 9
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x08000000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND, DWMWCP_ROUNDSMALL = 2, 3   # Windows 11 corner sizes: 8 px and 4 px
DWMWA_COLOR_NONE = 0xFFFFFFFE
MONITOR_DEFAULTTONEAREST = 2
WLAN_CURRENT_CONNECTION = 7
WLAN_CONNECTED = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_BLURBEHIND = 3
SIIGBF_ICONONLY = 0x4
IID_SHELL_ITEM = "{43826d1e-e718-42ee-bc55-a1e261c37bfe}"
IID_IMAGE_FACTORY = "{bcc18b79-ba16-442f-80c4-8a59c30c463b}"

# Keyboard shortcuts behind the Start, Windows-button and status tiles
KEY_ACTIONS = {
    "start": (VK_LWIN,),                 # Start menu
    "search": (VK_LWIN, VK_S),           # Search
    "task view": (VK_LWIN, VK_TAB),      # Task View
    "widgets": (VK_LWIN, VK_W),          # Widgets (weather, news)
    "battery": (VK_LWIN, VK_A),          # Quick Settings (battery, sound, brightness)
    "clock": (VK_LWIN, VK_N),            # notifications and calendar
}
STATUS_KINDS = ("tray", "wifi", "battery", "clock")
BUTTON_NAMES = {"search": "Search", "task view": "Task View", "widgets": "Widgets"}
TASKBAR_PEEK = {"on": False}  # True while the real taskbar is shown for its tray icons


class AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("color", ctypes.c_uint),
        ("animation", ctypes.c_int),
    ]


class CompositionData(ctypes.Structure):
    _fields_ = [
        ("attribute", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("size", ctypes.c_size_t),
    ]


if ON_WINDOWS:
    from ctypes import wintypes as w

    def api(dll, name, result, *args):
        """Look up a Windows function and tell ctypes its exact types."""
        function = getattr(dll, name)
        function.restype = result
        function.argtypes = args
        return function

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    dwmapi = ctypes.windll.dwmapi
    gdi32 = ctypes.windll.gdi32
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("monitor", w.RECT), ("work", w.RECT), ("flags", w.DWORD)]

    class PowerStatus(ctypes.Structure):
        _fields_ = [
            ("ac_line", ctypes.c_ubyte), ("battery_flag", ctypes.c_ubyte),
            ("percent", ctypes.c_ubyte), ("saver", ctypes.c_ubyte),
            ("life", w.DWORD), ("full_life", w.DWORD),
        ]

    class GUID(ctypes.Structure):
        _fields_ = [("data1", w.DWORD), ("data2", w.WORD), ("data3", w.WORD), ("data4", ctypes.c_ubyte * 8)]

    class Size(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class Bitmap(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_long), ("width", ctypes.c_long), ("height", ctypes.c_long),
            ("width_bytes", ctypes.c_long), ("planes", w.WORD), ("bits_pixel", w.WORD),
            ("bits", ctypes.c_void_p),
        ]

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("size", w.DWORD), ("width", ctypes.c_long), ("height", ctypes.c_long),
            ("planes", w.WORD), ("bit_count", w.WORD), ("compression", w.DWORD),
            ("size_image", w.DWORD), ("x_ppm", ctypes.c_long), ("y_ppm", ctypes.c_long),
            ("colors_used", w.DWORD), ("colors_important", w.DWORD),
        ]

    FindWindow = api(user32, "FindWindowW", w.HWND, w.LPCWSTR, w.LPCWSTR)
    FindWindowEx = api(user32, "FindWindowExW", w.HWND, w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR)
    ShowWindow = api(user32, "ShowWindow", w.BOOL, w.HWND, ctypes.c_int)
    IsWindowVisible = api(user32, "IsWindowVisible", w.BOOL, w.HWND)
    IsIconic = api(user32, "IsIconic", w.BOOL, w.HWND)
    IsZoomed = api(user32, "IsZoomed", w.BOOL, w.HWND)
    SetForegroundWindow = api(user32, "SetForegroundWindow", w.BOOL, w.HWND)
    GetForegroundWindow = api(user32, "GetForegroundWindow", w.HWND)
    GetWindow = api(user32, "GetWindow", w.HWND, w.HWND, w.UINT)
    GetWindowRect = api(user32, "GetWindowRect", w.BOOL, w.HWND, ctypes.POINTER(w.RECT))
    GetWindowText = api(user32, "GetWindowTextW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
    GetWindowTextLength = api(user32, "GetWindowTextLengthW", ctypes.c_int, w.HWND)
    GetWindowLong = api(user32, "GetWindowLongW", w.LONG, w.HWND, ctypes.c_int)
    GetClassName = api(user32, "GetClassNameW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
    GetWindowThreadProcessId = api(
        user32, "GetWindowThreadProcessId", w.DWORD, w.HWND, ctypes.POINTER(w.DWORD)
    )
    EnumWindows = api(user32, "EnumWindows", w.BOOL, WNDENUMPROC, w.LPARAM)
    EnumChildWindows = api(user32, "EnumChildWindows", w.BOOL, w.HWND, WNDENUMPROC, w.LPARAM)
    MonitorFromWindow = api(user32, "MonitorFromWindow", w.HANDLE, w.HWND, w.DWORD)
    GetMonitorInfo = api(user32, "GetMonitorInfoW", w.BOOL, w.HANDLE, ctypes.POINTER(MonitorInfo))
    GetDC = api(user32, "GetDC", w.HDC, w.HWND)
    ReleaseDC = api(user32, "ReleaseDC", ctypes.c_int, w.HWND, w.HDC)
    keybd_event = api(user32, "keybd_event", None, ctypes.c_ubyte, ctypes.c_ubyte, w.DWORD, ctypes.c_size_t)
    OpenProcess = api(kernel32, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    QueryFullProcessImageName = api(
        kernel32, "QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)
    )
    CloseHandle = api(kernel32, "CloseHandle", w.BOOL, w.HANDLE)
    GetSystemPowerStatus = api(kernel32, "GetSystemPowerStatus", w.BOOL, ctypes.POINTER(PowerStatus))
    DwmGetWindowAttribute = api(
        dwmapi, "DwmGetWindowAttribute", ctypes.c_long, w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD
    )
    DwmSetWindowAttribute = api(
        dwmapi, "DwmSetWindowAttribute", ctypes.c_long, w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD
    )
    GetObject = api(gdi32, "GetObjectW", ctypes.c_int, w.HANDLE, ctypes.c_int, ctypes.c_void_p)
    GetDIBits = api(
        gdi32, "GetDIBits", ctypes.c_int, w.HDC, w.HANDLE, w.UINT, w.UINT,
        ctypes.c_void_p, ctypes.POINTER(BitmapInfoHeader), w.UINT,
    )
    DeleteObject = api(gdi32, "DeleteObject", w.BOOL, w.HANDLE)
    CoInitialize = api(ole32, "CoInitialize", ctypes.c_long, ctypes.c_void_p)
    CoTaskMemFree = api(ole32, "CoTaskMemFree", None, ctypes.c_void_p)
    CLSIDFromString = api(ole32, "CLSIDFromString", ctypes.c_long, w.LPCWSTR, ctypes.POINTER(GUID))
    SHCreateItemFromParsingName = api(
        shell32, "SHCreateItemFromParsingName", ctypes.c_long,
        w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    try:  # tells us which Store app a window belongs to (Windows 8 and newer)
        GetApplicationUserModelId = api(
            kernel32, "GetApplicationUserModelId", ctypes.c_long, w.HANDLE, ctypes.POINTER(w.UINT), w.LPWSTR
        )
    except AttributeError:
        GetApplicationUserModelId = None
    try:  # used for the frosted glass; not listed in Windows' docs, so be careful
        SetWindowCompositionAttribute = api(
            user32, "SetWindowCompositionAttribute", w.BOOL, w.HWND, ctypes.POINTER(CompositionData)
        )
    except AttributeError:
        SetWindowCompositionAttribute = None

    # Wi-Fi status (wlanapi). These match Windows' WLAN_* structures field by field.
    class WlanInterfaceInfo(ctypes.Structure):
        _fields_ = [("guid", GUID), ("description", w.WCHAR * 256), ("state", ctypes.c_int)]

    class WlanInterfaceList(ctypes.Structure):
        _fields_ = [("count", w.DWORD), ("index", w.DWORD), ("items", WlanInterfaceInfo * 1)]

    class WlanConnection(ctypes.Structure):
        _fields_ = [
            ("state", ctypes.c_int), ("mode", ctypes.c_int), ("profile", w.WCHAR * 256),
            ("ssid_length", w.ULONG), ("ssid", ctypes.c_ubyte * 32), ("bss_type", ctypes.c_int),
            ("bssid", ctypes.c_ubyte * 6), ("phy_type", ctypes.c_int), ("phy_index", w.ULONG),
            ("signal", w.ULONG), ("rx_rate", w.ULONG), ("tx_rate", w.ULONG),
        ]

    try:
        wlanapi = ctypes.windll.wlanapi
        WlanOpenHandle = api(
            wlanapi, "WlanOpenHandle", w.DWORD, w.DWORD, ctypes.c_void_p,
            ctypes.POINTER(w.DWORD), ctypes.POINTER(w.HANDLE),
        )
        WlanEnumInterfaces = api(
            wlanapi, "WlanEnumInterfaces", w.DWORD, w.HANDLE, ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(WlanInterfaceList)),
        )
        WlanQueryInterface = api(
            wlanapi, "WlanQueryInterface", w.DWORD, w.HANDLE, ctypes.POINTER(GUID), ctypes.c_int,
            ctypes.c_void_p, ctypes.POINTER(w.DWORD), ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        )
        WlanFreeMemory = api(wlanapi, "WlanFreeMemory", None, ctypes.c_void_p)
        WlanCloseHandle = api(wlanapi, "WlanCloseHandle", w.DWORD, w.HANDLE, ctypes.c_void_p)
    except (OSError, AttributeError):  # PCs without Wi-Fi may not have wlanapi
        wlanapi = None


def press_keys(*keys):
    """Pretend to press a key combo, like Win or Win+X."""
    for key in keys:
        keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        keybd_event(key, 0, KEY_UP, 0)


def taskbar_windows():
    """Find the Windows taskbar, plus the extra taskbars on other monitors."""
    found = []
    main_taskbar = FindWindow("Shell_TrayWnd", None)
    if main_taskbar:
        found.append(main_taskbar)
    other = FindWindowEx(None, None, "Shell_SecondaryTrayWnd", None)
    while other:
        found.append(other)
        other = FindWindowEx(None, other, "Shell_SecondaryTrayWnd", None)
    return found


def set_taskbar_visible(visible):
    """Hide or show the Windows taskbar."""
    for hwnd in taskbar_windows():
        ShowWindow(hwnd, SW_SHOW if visible else SW_HIDE)


def keep_taskbar_hidden():
    """Windows sometimes brings the taskbar back (like when Start opens), so hide it again."""
    if TASKBAR_PEEK["on"]:
        return  # you asked to see the taskbar's tray icons, so leave it alone
    for hwnd in taskbar_windows():
        if IsWindowVisible(hwnd):
            ShowWindow(hwnd, SW_HIDE)


def enable_blur(hwnd):
    """Ask Windows to blur whatever is behind a window. Returns True if Windows said yes."""
    if SetWindowCompositionAttribute is None:
        return False
    accent = AccentPolicy(ACCENT_ENABLE_BLURBEHIND, 0, 0, 0)
    data = CompositionData(WCA_ACCENT_POLICY, ctypes.addressof(accent), ctypes.sizeof(accent))
    return bool(SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))


def round_window_corners(hwnd, small):
    """
    Ask Windows 11 to round a window's corners. This is what keeps the blur inside
    the rounded bar (cutting the window to shape doesn't clip the blur).
    """
    corner = ctypes.c_int(DWMWCP_ROUNDSMALL if small else DWMWCP_ROUND)
    DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))
    no_border = w.DWORD(DWMWA_COLOR_NONE)  # skip Windows' gray edge; the dock draws its own
    DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR, ctypes.byref(no_border), ctypes.sizeof(no_border))


def window_class(hwnd):
    """The internal type name Windows gives a window, like "Progman" for the desktop."""
    name = ctypes.create_unicode_buffer(256)
    GetClassName(hwnd, name, 256)
    return name.value


def window_title(hwnd):
    """The text in a window's title bar."""
    length = GetWindowTextLength(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    GetWindowText(hwnd, title, length + 1)
    return title.value


def is_app_window(hwnd):
    """True for normal app windows (the kind Alt+Tab shows), False for everything else."""
    if not IsWindowVisible(hwnd) or GetWindow(hwnd, GW_OWNER):
        return False  # hidden, or a popup/dialog that belongs to another window
    if GetWindowTextLength(hwnd) == 0:
        return False  # no title
    if GetWindowLong(hwnd, GWL_EXSTYLE) & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
        return False  # little tool windows (like this dock) and overlays
    cloaked = w.DWORD()
    DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    if cloaked.value:
        return False  # hidden by Windows, like apps on another virtual desktop
    rect = w.RECT()
    GetWindowRect(hwnd, ctypes.byref(rect))
    if rect.right - rect.left <= 1 or rect.bottom - rect.top <= 1:
        return False  # invisible 0-size helper windows
    return window_class(hwnd) not in ("Progman", "WorkerW")  # the desktop itself


def process_info(hwnd):
    """(exe path, Store app ID) of the program that owns a window. The ID is "" for normal apps."""
    process_id = w.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if not handle:
        return "", ""
    path = ctypes.create_unicode_buffer(1024)
    length = w.DWORD(1024)
    exe = path.value if QueryFullProcessImageName(handle, 0, path, ctypes.byref(length)) else ""
    app_id = ""
    if GetApplicationUserModelId is not None:
        buffer = ctypes.create_unicode_buffer(256)
        size = w.UINT(256)
        if GetApplicationUserModelId(handle, ctypes.byref(size), buffer) == 0:
            app_id = buffer.value
    CloseHandle(handle)
    return exe, app_id


def window_program(hwnd):
    """
    Like process_info, but Store apps (Settings, Calculator) sit inside a frame window
    that belongs to ApplicationFrameHost.exe, so look inside the frame for the real app.
    """
    exe, app_id = process_info(hwnd)
    if Path(exe).name.lower() != "applicationframehost.exe":
        return exe, app_id
    found = []

    def check(child, _):
        child_exe, child_id = process_info(child)
        if child_exe and child_exe.lower() != exe.lower():
            found.append((child_exe, child_id))
            return False  # found it, stop looking
        return True

    EnumChildWindows(hwnd, WNDENUMPROC(check), 0)
    return found[0] if found else (exe, app_id)


def open_windows():
    """Every open app window as (window, exe, Store app ID, title), front to back like Alt+Tab."""
    if not ON_WINDOWS:
        return []
    found = []

    def check(hwnd, _):
        if is_app_window(hwnd):
            exe, app_id = window_program(hwnd)
            if exe:
                found.append((hwnd, exe, app_id, window_title(hwnd)))
        return True  # keep going

    EnumWindows(WNDENUMPROC(check), 0)
    return found


def fullscreen_app_on(dock_hwnd):
    """True if the app in front is full screen on the same monitor as the dock."""
    hwnd = GetForegroundWindow()
    if not hwnd:
        return False
    # The desktop and Windows' own pop-ups (like Start) don't count
    if window_class(hwnd) in ("Progman", "WorkerW", "Windows.UI.Core.CoreWindow"):
        return False
    if IsZoomed(hwnd):
        return False  # a maximized window isn't full screen
    monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if monitor != MonitorFromWindow(dock_hwnd, MONITOR_DEFAULTTONEAREST):
        return False  # it's full screen on a different monitor
    info = MonitorInfo()
    info.size = ctypes.sizeof(MonitorInfo)
    GetMonitorInfo(monitor, ctypes.byref(info))
    window = w.RECT()
    GetWindowRect(hwnd, ctypes.byref(window))
    screen = info.monitor
    # Full screen = the window covers the whole monitor
    return (
        window.left <= screen.left
        and window.top <= screen.top
        and window.right >= screen.right
        and window.bottom >= screen.bottom
    )


def bring_to_front(hwnd):
    """Switch to a window, un-minimizing it if needed."""
    if IsIconic(hwnd):
        ShowWindow(hwnd, SW_RESTORE)
    SetForegroundWindow(hwnd)


def front_window_covers(left, top, right, bottom):
    """True if the app in front overlaps this part of the screen (in real screen pixels)."""
    hwnd = GetForegroundWindow()
    if not hwnd or IsIconic(hwnd) or window_class(hwnd) in SHELL_WINDOWS:
        return False
    process_id = w.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if process_id.value == os.getpid():
        return False  # that's the dock itself (or its menu)
    # The window's visible edges (GetWindowRect also counts invisible resize borders)
    frame = w.RECT()
    if DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(frame), ctypes.sizeof(frame)) != 0:
        GetWindowRect(hwnd, ctypes.byref(frame))
    return frame.left < right and frame.right > left and frame.top < bottom and frame.bottom > top


def battery_info():
    """(charge 0-100, plugged in?), or None on a PC without a battery."""
    if not ON_WINDOWS:
        return None
    status = PowerStatus()
    if not GetSystemPowerStatus(ctypes.byref(status)):
        return None
    if status.battery_flag == 128 or status.percent == 255:
        return None  # no battery, or Windows doesn't know
    return status.percent, status.ac_line == 1


def read_wifi():
    """(connected?, network name, signal 0-100), or None if this PC has no Wi-Fi."""
    if not ON_WINDOWS or wlanapi is None:
        return None
    version, handle = w.DWORD(), w.HANDLE()
    if WlanOpenHandle(2, None, ctypes.byref(version), ctypes.byref(handle)) != 0:
        return None
    try:
        interfaces = ctypes.POINTER(WlanInterfaceList)()
        if WlanEnumInterfaces(handle, None, ctypes.byref(interfaces)) != 0:
            return None
        try:
            count = interfaces.contents.count
            if count == 0:
                return None  # no Wi-Fi adapter
            first = ctypes.addressof(interfaces.contents.items)
            for adapter in ctypes.cast(first, ctypes.POINTER(WlanInterfaceInfo * count)).contents:
                if adapter.state != WLAN_CONNECTED:
                    continue
                size, data = w.DWORD(), ctypes.c_void_p()
                if WlanQueryInterface(handle, ctypes.byref(adapter.guid), WLAN_CURRENT_CONNECTION,
                                      None, ctypes.byref(size), ctypes.byref(data), None) == 0:
                    connection = ctypes.cast(data, ctypes.POINTER(WlanConnection)).contents
                    name = bytes(connection.ssid[:connection.ssid_length]).decode("utf-8", "replace")
                    signal = connection.signal
                    WlanFreeMemory(data)
                    return True, name, signal
            return False, "", 0  # has Wi-Fi, but not connected
        finally:
            WlanFreeMemory(interfaces)
    finally:
        WlanCloseHandle(handle, None)


WIFI_CACHE = {"time": -100.0, "value": None}


def wifi_status():
    """Same as read_wifi(), but only asks Windows every 5 seconds."""
    if time.monotonic() - WIFI_CACHE["time"] >= 5:
        try:
            WIFI_CACHE["value"] = read_wifi()
        except OSError:
            WIFI_CACHE["value"] = None
        WIFI_CACHE["time"] = time.monotonic()
    return WIFI_CACHE["value"]


def open_wifi_list():
    """Open Windows' list of Wi-Fi networks (or Quick Settings if that doesn't work)."""
    try:
        os.startfile("ms-availablenetworks:")
    except OSError:
        press_keys(VK_LWIN, VK_A)


# The next few functions ask Windows' shell (the part that draws Explorer and the Start
# menu) about Store apps, which don't have normal .exe icons. It uses "COM" objects,
# which in Python means calling functions by their number in the object's function table.

def com_method(obj, number, result, *args):
    """Get function number `number` from a COM object's function table."""
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *args)(table[number])


def shell_item(name, interface):
    """Ask the shell for an object describing something, like "shell:AppsFolder\\<app ID>"."""
    if not ON_WINDOWS:
        return None
    guid = GUID()
    CLSIDFromString(interface, ctypes.byref(guid))
    item = ctypes.c_void_p()
    if SHCreateItemFromParsingName(name, None, ctypes.byref(guid), ctypes.byref(item)) != 0:
        return None
    return item


def release(obj):
    com_method(obj, 2, ctypes.c_ulong)(obj)  # function 2 = "I'm done with you"


def shell_name(name):
    """The name Windows shows for something, like "Microsoft Store". "" if it doesn't exist."""
    item = shell_item(name, IID_SHELL_ITEM)
    if not item:
        return ""
    get_display_name = com_method(item, 5, ctypes.c_long, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
    text = ctypes.c_void_p()
    ok = get_display_name(item, 0, ctypes.byref(text)) == 0
    release(item)
    if not ok or not text.value:
        return ""
    value = ctypes.wstring_at(text.value)
    CoTaskMemFree(text)
    return value


def shell_icon(name, size=256):
    """A big, sharp icon for anything the shell knows, including Store apps. None if it fails."""
    factory = shell_item(name, IID_IMAGE_FACTORY)
    if not factory:
        return None
    get_image = com_method(factory, 3, ctypes.c_long, Size, ctypes.c_int, ctypes.POINTER(w.HANDLE))
    bitmap = w.HANDLE()
    ok = get_image(factory, Size(size, size), SIIGBF_ICONONLY, ctypes.byref(bitmap)) == 0
    release(factory)
    if not ok or not bitmap.value:
        return None
    # Copy the Windows bitmap's pixels into a Qt image
    info = Bitmap()
    GetObject(bitmap, ctypes.sizeof(info), ctypes.byref(info))
    width, height = info.width, abs(info.height)
    header = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = ctypes.create_string_buffer(width * height * 4)
    screen_dc = GetDC(None)
    GetDIBits(screen_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0)
    ReleaseDC(None, screen_dc)
    DeleteObject(bitmap)
    image = QImage(pixels.raw, width, height, QImage.Format_ARGB32_Premultiplied).copy()
    return QPixmap.fromImage(image)


# ---------------- Loading apps ----------------

def taskbar_pin_order():
    """Windows saves your pinned taskbar apps in the registry. Read it as raw bytes."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband",
        )
        data, _ = winreg.QueryValueEx(key, "Favorites")
        return data
    except (ImportError, OSError):
        return b""


def pinned_store_app_ids(order):
    """Find Store app IDs (like Microsoft.WindowsStore_8wekyb3d8bbwe!App) in the saved pins."""
    found = {}
    for start in (0, 1):  # the text can start on an even or an odd byte
        text = order[start:].decode("utf-16-le", errors="ignore")
        for match in STORE_APP_ID.finditer(text):
            app_id = match.group()
            if app_id not in found:
                found[app_id] = order.find(app_id.encode("utf-16-le"))  # where it is in the list
    return found


def load_pinned_apps():
    """Get the apps pinned to your taskbar (normal and Store apps), in the taskbar's order."""
    order = taskbar_pin_order()
    pins = []  # (position in the saved order, name, app)

    # Normal apps: one shortcut each in the pinned folder
    if PINNED_FOLDER.exists():
        for shortcut in PINNED_FOLDER.glob("*.lnk"):
            spot = order.find(shortcut.name.encode("utf-16-le"))
            app = {"name": shortcut.stem, "path": str(shortcut)}
            pins.append((spot if spot != -1 else len(order), shortcut.stem.lower(), app))

    # Store apps (Copilot, Microsoft Store...): only their ID is saved
    for app_id, spot in pinned_store_app_ids(order).items():
        name = shell_name("shell:AppsFolder\\" + app_id)
        if name:  # skip apps that were uninstalled
            pins.append((spot, name.lower(), {"name": name, "aumid": app_id}))

    pins.sort(key=lambda pin: (pin[0], pin[1]))
    return [app for _, _, app in pins]


def load_extra_apps():
    """Read extra apps from extra_apps.json, creating an empty one the first time."""
    if not EXTRA_APPS_FILE.exists():
        EXTRA_APPS_FILE.write_text("[]\n")
    try:
        return json.loads(EXTRA_APPS_FILE.read_text())
    except json.JSONDecodeError as error:
        # A popup instead of print(), since the .exe has no terminal window
        QMessageBox.warning(
            None,
            "Dock",
            f"extra_apps.json has a typo:\n{error}\n\n"
            "Tip: use forward slashes in paths, like C:/Windows/notepad.exe",
        )
        sys.exit(1)


def load_apps():
    """Pinned apps, then extras (skipping duplicates)."""
    apps = []
    seen = set()
    for app in load_pinned_apps() + load_extra_apps():
        if app["name"].lower() not in seen:
            seen.add(app["name"].lower())
            apps.append(app)
    return apps


def shortcut_target(app):
    """The real program (or file/folder) a dock item opens, following shortcuts. "" if unknown."""
    path = app.get("path", "")
    if not path:
        return ""
    info = QFileInfo(path)
    target = info.symLinkTarget() if info.isShortcut() else path
    if target and os.path.exists(target):
        return target
    return SHORTCUT_FIXES.get(app["name"].lower(), "")


def app_exe(app):
    """The .exe a dock item runs, cleaned up so it can be compared with open windows."""
    target = shortcut_target(app)
    return os.path.normcase(os.path.normpath(target)) if target else ""


# ---------------- Icons ----------------

def tile(color):
    """Start a 256x256 icon (big, so it stays sharp when shrunk): a rounded square."""
    pixmap = QPixmap(256, 256)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(*color))
    painter.drawRoundedRect(QRectF(15, 15, 226, 226), 50, 50)
    return pixmap, painter


def white_pen(width):
    pen = QPen(QColor("white"), width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def pixel_font(pixels, bold=False):
    font = QFont("Segoe UI")
    font.setPixelSize(pixels)
    font.setBold(bold)
    return font


def start_icon():
    """Use START_ICON if you set one, otherwise draw a simple app-grid icon."""
    if START_ICON:
        return QIcon(START_ICON).pixmap(256, 256)
    pixmap, painter = tile((0, 120, 212))  # blue tile
    painter.setBrush(QColor("white"))      # 3x3 grid of dots
    dot = 33
    for row in range(3):
        for col in range(3):
            painter.drawEllipse(QRectF(69 + col * 59 - dot / 2, 69 + row * 59 - dot / 2, dot, dot))
    painter.end()
    return pixmap


def button_icon(kind):
    """Draw the Search, Task View, or Widgets button."""
    pixmap, painter = tile(GRAY_TILE)
    if kind == "search":  # magnifying glass
        painter.setPen(white_pen(22))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(115, 115), 48, 48)
        painter.drawLine(QPointF(150, 150), QPointF(186, 186))
    elif kind == "task view":  # two overlapping windows
        painter.setBrush(QColor(255, 255, 255, 110))
        painter.drawRoundedRect(QRectF(62, 70, 100, 80), 14, 14)
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(QRectF(94, 106, 100, 80), 14, 14)
    elif kind == "widgets":  # sun behind a cloud
        painter.setBrush(QColor(255, 196, 40))
        painter.drawEllipse(QPointF(150, 100), 40, 40)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(QPointF(105, 145), 38, 38)
        painter.drawEllipse(QPointF(145, 130), 46, 46)
        painter.drawRoundedRect(QRectF(67, 140, 136, 50), 25, 25)
    painter.end()
    return pixmap


def status_icon(kind):
    """Draw the tray, Wi-Fi, battery, or clock tile."""
    pixmap, painter = tile(GRAY_TILE)
    if kind == "clock":  # time on top, date underneath
        now = time.localtime()
        painter.setPen(QColor("white"))
        painter.setFont(pixel_font(76, bold=True))
        painter.drawText(QRectF(0, 40, 256, 100), Qt.AlignCenter, f"{now.tm_hour % 12 or 12}:{now.tm_min:02d}")
        painter.setPen(QColor(200, 200, 210))
        painter.setFont(pixel_font(52))
        painter.drawText(QRectF(0, 135, 256, 70), Qt.AlignCenter, f"{now.tm_mon}/{now.tm_mday}")
    elif kind == "wifi":  # Wi-Fi waves: more bright waves = stronger signal
        status = wifi_status()
        connected, _, signal = status if status else (False, "", 0)
        waves = 0 if not connected else (3 if signal >= 70 else 2 if signal >= 40 else 1)
        center = QPointF(128, 182)
        painter.setBrush(Qt.NoBrush)
        for number, radius in enumerate((50, 90, 130), start=1):
            pen = white_pen(22)
            pen.setColor(QColor(255, 255, 255, 255 if number <= waves else 70))  # dim = no signal
            painter.setPen(pen)
            arc = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            painter.drawArc(arc, 45 * 16, 90 * 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255 if connected else 70))
        painter.drawEllipse(center, 17, 17)
    elif kind == "battery":  # battery that fills up, a bolt while charging, and the %
        info = battery_info()
        percent, plugged = info if info else (0, False)
        if plugged:
            fill = QColor(70, 200, 90)    # green while plugged in
        elif percent <= 20:
            fill = QColor(235, 70, 60)    # red when low
        else:
            fill = QColor("white")
        body = QRectF(48, 66, 146, 80)
        painter.setPen(white_pen(12))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(body, 18, 18)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(QRectF(201, 90, 14, 32), 5, 5)  # the bump on the end
        level = body.adjusted(14, 14, -14, -14)
        level.setWidth(level.width() * percent / 100)
        painter.setBrush(fill)
        painter.drawRoundedRect(level, 7, 7)
        if plugged:  # lightning bolt
            painter.setPen(QPen(QColor(*GRAY_TILE), 7))
            painter.setBrush(QColor("white"))
            painter.drawPolygon(QPolygonF([
                QPointF(134, 72), QPointF(102, 112), QPointF(124, 112),
                QPointF(112, 142), QPointF(146, 100), QPointF(124, 100),
            ]))
        painter.setPen(QColor("white"))
        painter.setFont(pixel_font(56, bold=True))
        painter.drawText(QRectF(0, 152, 256, 70), Qt.AlignCenter, f"{percent}%")
    elif kind == "tray":  # ^ arrow (points down while the taskbar is showing)
        painter.setPen(white_pen(24))
        painter.setBrush(Qt.NoBrush)
        if TASKBAR_PEEK["on"]:
            points = [QPointF(84, 108), QPointF(128, 152), QPointF(172, 108)]
        else:
            points = [QPointF(84, 152), QPointF(128, 108), QPointF(172, 152)]
        painter.drawPolyline(QPolygonF(points))
    painter.end()
    return pixmap


def status_label(kind):
    """The hover label for a status tile."""
    if kind == "clock":
        now = time.localtime()
        return time.strftime("%A, %B ", now) + str(now.tm_mday)  # like "Wednesday, November 4"
    if kind == "wifi":
        status = wifi_status()
        if status is None:
            return "No Wi-Fi"
        connected, name, signal = status
        return f"Wi-Fi: {name} ({signal}%)" if connected else "Wi-Fi: not connected"
    if kind == "battery":
        info = battery_info()
        if info is None:
            return "No battery"
        percent, plugged = info
        if plugged:
            return f"Battery {percent}% (plugged in)" if percent >= 100 else f"Battery {percent}% (charging)"
        return f"Battery {percent}%"
    if kind == "tray":
        return "Hide taskbar" if TASKBAR_PEEK["on"] else "Show taskbar (tray icons)"
    return kind


def status_state(kind):
    """What a status tile is showing right now, so we know when to redraw it."""
    if kind == "clock":
        return time.strftime("%H:%M %m/%d")
    if kind == "wifi":
        return str(wifi_status())
    if kind == "battery":
        return str(battery_info())
    return str(TASKBAR_PEEK["on"])


def load_icon(app, size):
    """Get the picture for one dock item."""
    action = app.get("action")
    if action == "start":
        return start_icon()
    if action in BUTTON_NAMES:
        return button_icon(action)
    if action in STATUS_KINDS:
        return status_icon(action)
    if app.get("icon"):
        return QIcon(app["icon"]).pixmap(size, size)
    if app.get("aumid"):  # Store app: ask the shell for its icon
        pixmap = shell_icon("shell:AppsFolder\\" + app["aumid"])
        if pixmap:
            return pixmap
    # Use the real program's icon (no shortcut arrow); fall back to the shortcut itself
    target = app.get("exe") or shortcut_target(app) or app.get("path", "")
    return QFileIconProvider().icon(QFileInfo(target)).pixmap(size, size)


# ---------------- Opening things ----------------

def launch(path):
    """Open an app, shortcut, file, or folder."""
    path = os.path.normpath(path)
    try:
        if ON_WINDOWS:
            os.startfile(path)
        else:
            subprocess.Popen([path])
    except OSError as error:
        print(f"Couldn't open {path}: {error}")


def open_app(app):
    """Open a dock item: a Store app by its ID, or anything else by its path."""
    if app.get("aumid"):
        if ON_WINDOWS:
            os.startfile("shell:AppsFolder\\" + app["aumid"])
    else:
        launch(app["path"])


# ---------------- Starting with Windows ----------------

# Apps listed under this registry key start when you sign in (Settings > Apps > Startup shows them)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command():
    """The command Windows should run at sign-in to start this dock."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'  # Dock.exe
    pythonw = Path(sys.executable).with_name("pythonw.exe")  # runs without a terminal window
    return f'"{pythonw}" "{Path(__file__).resolve()}"'


def starts_with_windows():
    """True if the dock is set to start when you sign in."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "Dock")
            return value == startup_command()
    except (ImportError, OSError):
        return False


def set_start_with_windows(on):
    """Turn starting at sign-in on or off."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, "Dock", 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, "Dock")
            except FileNotFoundError:
                pass


def corner_radius(bar_height):
    """
    How round the bar's corners are, based on BAR_SHAPE. These match the corners
    Windows 11 puts on the blur: 8 px for "rounded" and 4 px for "square".
    """
    if BAR_SHAPE == "square":
        return 4
    if BAR_SHAPE == "pill" and not FROSTED_GLASS:
        return bar_height / 2
    return 8  # "rounded"


# ---------------- The frosted glass behind the icons ----------------

class Glass(QWidget):
    """
    The dock's background bar. It's a separate window that sits right behind the
    icons, so Windows can blur it without blurring the space above the bar too.
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput  # clicks go to the dock, never to the glass
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # don't steal focus when it reappears
        self.radius = 0
        self.blurred = False
        if FROSTED_GLASS and ON_WINDOWS:
            hwnd = int(self.winId())
            self.blurred = enable_blur(hwnd)
            if self.blurred:
                round_window_corners(hwnd, small=(BAR_SHAPE == "square"))

    def place(self, rect, radius):
        """Move and resize the glass to sit exactly behind the dock's bar."""
        if rect == self.geometry() and radius == self.radius:
            return  # nothing changed
        self.radius = radius
        self.setGeometry(rect)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(*(GLASS_TINT if self.blurred else SOLID_COLOR)))
        painter.drawRoundedRect(QRectF(self.rect()), self.radius, self.radius)


# ---------------- The dock window ----------------

class Dock(QWidget):
    def __init__(self, apps):
        super().__init__()
        # Group 1: Start + Windows buttons (never changes)
        self.front = [{"key": "front:start", "name": "Start", "action": "start"}]
        for button in TASKBAR_BUTTONS:
            if button in BUTTON_NAMES:
                self.front.append({"key": f"front:{button}", "name": BUTTON_NAMES[button], "action": button})

        # Group 2: pinned apps + extras (never changes)
        self.pinned = apps
        for i, app in enumerate(apps):
            app["key"] = f"pinned:{i}"
        self.pinned_exes = [app_exe(app) for app in apps]

        # Group 3: open apps that aren't pinned (changes as you open/close apps)
        self.open_order = []                 # in the order they opened

        # Group 4: tray, Wi-Fi, battery, clock (the pictures change, the list doesn't)
        self.status = []
        for kind in STATUS_TILES:
            if kind not in STATUS_KINDS:
                continue
            if kind == "tray" and not HIDE_WINDOWS_TASKBAR:
                continue  # the real taskbar is showing anyway
            if kind == "battery" and battery_info() is None:
                continue  # this PC has no battery
            self.status.append({"key": f"status:{kind}", "action": kind, "name": status_label(kind)})

        # Auto-hide: 0 = fully showing, 1 = slid down out of sight
        self.slide = 0.0
        self.slide_target = 0.0
        self.hide_at = None                  # when to start sliding away

        # Everything on the dock right now, left to right
        self.items = []
        self.windows = []                    # open windows for each item
        self.scales = []                     # current size of each icon (1.0 = normal)
        self.pixmaps = []
        self.separators = set()              # put a separator line before these positions
        self.icon_cache = {}                 # icons already loaded, so we only load once
        self.mouse_x = None                  # None means the mouse isn't over the dock
        self.icon_rects = []                 # where each icon was drawn last frame
        self.bounce_start = {}               # item number -> when its bounce started

        # No title bar, see-through background, always on top, no taskbar button
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # don't steal focus when it reappears
        self.setMouseTracking(True)

        self.glass = Glass()
        groups = [self.front, self.pinned, [], self.status]
        self.set_items(groups, [[] for group in groups for _ in group])
        self.glass.show()  # shown first, so the dock (with the icons) ends up on top of it

        # Run animate() about 60 times per second
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(16)

        # Check which apps are open (and update the clock) once a second
        self.window_timer = QTimer(self)
        self.window_timer.timeout.connect(self.refresh_open_windows)
        self.window_timer.start(1000)
        self.refresh_open_windows()

        # Hide when an app goes full screen; check twice a second
        self.fullscreen_timer = QTimer(self)
        self.fullscreen_timer.timeout.connect(self.check_fullscreen)
        if ON_WINDOWS and HIDE_IN_FULLSCREEN:
            self.fullscreen_timer.start(500)

        # Slide away when an app covers the dock; check 10 times a second
        self.hide_timer = QTimer(self)
        self.hide_timer.timeout.connect(self.check_auto_hide)
        self.hide_timer.start(100)

    # ----- Which icons are on the dock -----

    def set_items(self, groups, windows):
        """Put a new list of icons on the dock, with separator lines between the groups."""
        old_scales = {item["key"]: scale for item, scale in zip(self.items, self.scales)}
        items, separators = [], set()
        for group in groups:
            if group and items and SHOW_SEPARATOR:
                separators.add(len(items))  # line before this group
            items += group
        self.items, self.windows, self.separators = items, windows, separators
        self.scales = [old_scales.get(item["key"], 1.0) for item in items]
        self.pixmaps = [self.icon_for(item) for item in items]

        self.place_on_screen()
        resting = self.layout([ICON_SIZE] * len(items))
        self.resting_centers = [x + ICON_SIZE / 2 for x in resting]
        self.update_glass()
        self.update()

    def icon_for(self, item):
        """Load an item's icon the first time, then reuse it."""
        if item["key"] not in self.icon_cache:
            self.icon_cache[item["key"]] = load_icon(item, int(ICON_SIZE * MAX_SCALE))
        return self.icon_cache[item["key"]]

    def pinned_owner(self, exe, app_id):
        """Which pinned app a window belongs to (by its .exe or Store app ID), or None."""
        for i, app in enumerate(self.pinned):
            if app.get("aumid"):
                if app_id and app_id.lower() == app["aumid"].lower():
                    return i
                continue
            same_exe = self.pinned_exes[i] and os.path.normcase(exe) == self.pinned_exes[i]
            # Some shortcuts start a launcher (Discord runs Update.exe), so also match by name
            same_name = Path(exe).stem.lower() == app["name"].lower().replace(" ", "")
            if same_exe or same_name:
                return i
        return None

    def refresh_open_windows(self):
        """
        Once a second: find every open window, give it to its pinned icon (for the
        dot), and add an icon for each open app that isn't pinned, like the taskbar.
        """
        self.refresh_status()
        pinned_windows = [[] for _ in self.pinned]
        unpinned = {}  # one entry per unpinned app, even if it has several windows
        for hwnd, exe, app_id, title in open_windows():
            owner = self.pinned_owner(exe, app_id)
            if owner is not None:
                pinned_windows[owner].append(hwnd)
            elif SHOW_OPEN_APPS:
                key = "open:" + (app_id.lower() or os.path.normcase(exe))
                if key not in unpinned:
                    # Label it with its front window's title, like "Command Prompt"
                    unpinned[key] = {"key": key, "name": title, "exe": exe, "aumid": app_id, "windows": []}
                unpinned[key]["windows"].append(hwnd)

        # Keep unpinned apps in the order they opened, so icons don't jump around
        self.open_order = [key for key in self.open_order if key in unpinned]
        self.open_order += [key for key in unpinned if key not in self.open_order]
        open_apps = [unpinned[key] for key in self.open_order]

        groups = [self.front, self.pinned, open_apps, self.status]
        windows = (
            [[] for _ in self.front]
            + pinned_windows
            + [app["windows"] for app in open_apps]
            + [[] for _ in self.status]
        )
        items = [item for group in groups for item in group]
        if [item["key"] for item in items] != [item["key"] for item in self.items]:
            self.set_items(groups, windows)  # an app opened or closed: rebuild the dock
        else:
            self.items, self.windows = items, windows  # same icons; dots/titles may change
            self.update()

    def refresh_status(self):
        """Redraw the clock, battery and tray tiles when what they show changes."""
        changed = False
        for item in self.status:
            state = (status_label(item["action"]), status_state(item["action"]))
            if item.get("state") != state:
                item["state"] = state
                item["name"] = state[0]
                self.icon_cache.pop(item["key"], None)  # forget the old picture
                changed = True
        if changed and self.items:
            self.pixmaps = [self.icon_for(item) for item in self.items]
            self.update()

    def toggle_taskbar(self):
        """Show the real Windows taskbar (for tray icons like OneDrive), or hide it again."""
        TASKBAR_PEEK["on"] = not TASKBAR_PEEK["on"]
        set_taskbar_visible(TASKBAR_PEEK["on"])
        if TASKBAR_PEEK["on"]:
            press_keys(VK_LWIN, VK_B)  # jump to the tray, so it pops up even with auto-hide on
        self.refresh_status()

    def shown_bar(self):
        """Where the bar is on screen when the dock is showing (not slid away)."""
        bar = self.bar_rect()
        return QRect(
            self.base_x + round(bar.x()), self.base_y + round(bar.y()),
            round(bar.width()), round(bar.height()),
        )

    def check_auto_hide(self):
        """Slide the dock away when an app covers it, and back when you need it."""
        if AUTO_HIDE not in ("windows", "always") or not self.isVisible():
            self.slide_target, self.hide_at = 0.0, None
            return

        mouse = QCursor.pos()
        bar = self.shown_bar()
        # Bring it back when the mouse touches the bottom edge of the screen below the dock
        at_edge = mouse.y() >= self.area.bottom() - 2 and self.x() <= mouse.x() <= self.x() + self.width()
        # Keep it up while you're using it
        on_dock = self.slide < 0.5 and (self.mouse_x is not None or bar.contains(mouse))
        busy = QApplication.activePopupWidget() is not None or TASKBAR_PEEK["on"]

        if AUTO_HIDE == "always":
            covered = True
        elif ON_WINDOWS:
            # Windows measures in real screen pixels, Qt in scaled ones, so convert
            ratio = QApplication.primaryScreen().devicePixelRatio()
            covered = front_window_covers(
                round(bar.left() * ratio), round(bar.top() * ratio),
                round((bar.right() + 1) * ratio), round((bar.bottom() + 1) * ratio),
            )
        else:
            covered = False

        if covered and not (at_edge or on_dock or busy):
            if self.hide_at is None:
                self.hide_at = time.monotonic() + HIDE_DELAY  # wait a moment first
            if time.monotonic() >= self.hide_at:
                self.slide_target = 1.0
                self.mouse_x = None
        else:
            self.hide_at = None
            self.slide_target = 0.0

    def check_fullscreen(self):
        """Hide the dock while an app is full screen, and bring it back afterward."""
        fullscreen = fullscreen_app_on(int(self.winId()))
        if fullscreen and self.isVisible():
            self.mouse_x = None  # forget the hover so icons aren't stuck big
            self.hide()
            self.glass.hide()
        elif not fullscreen and not self.isVisible():
            self.glass.show()  # glass first, so the dock lands on top of it
            self.show()

    # ----- Layout -----

    def place_on_screen(self):
        """Size the window and center it at the bottom of the screen."""
        count = len(self.items)
        room_to_grow = ICON_SIZE * (MAX_SCALE - 1) * 3
        separators = SEPARATOR_SPACE * len(self.separators)
        width = int(count * (ICON_SIZE + GAP) + separators + room_to_grow + PADDING * 2)
        # Tall enough for a magnified icon, its bounce, and the name label (+40)
        height = int(ICON_SIZE * MAX_SCALE + BOUNCE_HEIGHT + PADDING + BOTTOM_PAD + 40)

        screen = QApplication.primaryScreen()
        # If the taskbar is hidden, use the whole screen; otherwise stay clear of the taskbar
        area = screen.geometry() if HIDE_WINDOWS_TASKBAR else screen.availableGeometry()
        self.area = area
        self.base_x = area.x() + (area.width() - width) // 2
        self.base_y = area.y() + area.height() - height - BOTTOM_MARGIN  # where it sits when showing
        # How far it slides down to hide: just enough to get the bar off the screen
        self.slide_distance = ICON_SIZE + PADDING + BOTTOM_PAD + BOTTOM_MARGIN + 4
        y = self.base_y + round(self.slide * self.slide_distance)
        self.setGeometry(self.base_x, y, width, height)

    def layout(self, sizes):
        """The left edge of each icon, centered in the window, given each icon's size."""
        total = sum(sizes) + GAP * (len(sizes) - 1) + SEPARATOR_SPACE * len(self.separators)
        x = (self.width() - total) / 2
        lefts = []
        for i, size in enumerate(sizes):
            if i in self.separators:
                x += SEPARATOR_SPACE  # leave room for a separator line
            lefts.append(x)
            x += size + GAP
        return lefts

    def current_sizes(self):
        return [ICON_SIZE * scale for scale in self.scales]

    def bar_rect(self):
        """The background bar. It stretches sideways as icons grow, like the Mac's."""
        sizes = self.current_sizes()
        lefts = self.layout(sizes)
        left = lefts[0] - PADDING
        right = lefts[-1] + sizes[-1] + PADDING
        height = ICON_SIZE + PADDING + BOTTOM_PAD
        return QRectF(left, self.height() - height, right - left, height)

    def update_glass(self):
        """Keep the glass window lined up behind the bar."""
        bar = self.bar_rect()
        on_screen = QRect(
            self.x() + round(bar.x()),
            self.y() + round(bar.y()),
            round(bar.width()),
            round(bar.height()),
        )
        self.glass.place(on_screen, corner_radius(bar.height()))

    # ----- Animation -----

    def target_scale(self, i):
        """How big icon i should be, based on how close the cursor is to it."""
        if self.mouse_x is None:
            return 1.0
        distance = abs(self.mouse_x - self.resting_centers[i])
        closeness = max(0.0, 1 - distance / MAGNIFY_RANGE)  # 1 = right on it, 0 = far
        return 1 + (MAX_SCALE - 1) * closeness

    def bounce_height(self, i):
        """How far up icon i is right now because of its launch bounce."""
        if i not in self.bounce_start:
            return 0
        progress = (time.monotonic() - self.bounce_start[i]) / BOUNCE_TIME  # 0 to 1
        if progress >= 1:
            return 0
        hop = abs(math.sin(progress * BOUNCES * math.pi))  # up and down, BOUNCES times
        return hop * BOUNCE_HEIGHT * (1 - progress * 0.4)  # each hop a bit lower

    def animate(self):
        """Move each icon's size a little closer to its target, and run any bounces."""
        changed = False
        for i in range(len(self.items)):
            target = self.target_scale(i)
            step = (target - self.scales[i]) * ANIMATION_SPEED
            if abs(step) > 0.001:
                self.scales[i] += step
                changed = True
            else:
                self.scales[i] = target

        # Keep redrawing while something is bouncing, and forget finished bounces
        for i, started in list(self.bounce_start.items()):
            changed = True
            if time.monotonic() - started > BOUNCE_TIME:
                del self.bounce_start[i]

        # Slide down (hide) or up (show) a little each frame
        if abs(self.slide_target - self.slide) > 0.002:
            self.slide += (self.slide_target - self.slide) * SLIDE_SPEED
        else:
            self.slide = self.slide_target
        y = self.base_y + round(self.slide * self.slide_distance)
        if y != self.y():
            self.move(self.base_x, y)
            changed = True

        if changed:
            self.update_glass()
            self.update()  # redraw

    # ----- Drawing -----

    def hovered_index(self):
        """Which icon the cursor is over (left to right), or None."""
        if self.mouse_x is None:
            return None
        for i, rect in enumerate(self.icon_rects):
            if rect.left() <= self.mouse_x <= rect.right():
                return i
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        sizes = self.current_sizes()
        lefts = self.layout(sizes)
        bar = self.bar_rect()
        radius = corner_radius(bar.height())

        # Fill with an almost invisible color so the mouse "sticks" to the dock
        # (fully see-through spots let clicks fall through to the window behind)
        if self.mouse_x is None:
            catch_area = bar
        else:
            catch_area = QRectF(bar.x(), 0, bar.width(), self.height())
        painter.fillRect(catch_area, QColor(0, 0, 0, 1))

        # Thin light edge around the bar (the Glass window fills it in)
        painter.setPen(QColor(255, 255, 255, 70))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(bar.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # Separator lines between the groups
        inset = bar.height() * 0.18  # lines are a bit shorter than the bar
        painter.setPen(QColor(255, 255, 255, 80))
        for i in self.separators:
            line_x = (lefts[i - 1] + sizes[i - 1] + lefts[i]) / 2
            painter.drawLine(
                QPointF(line_x, bar.top() + inset),
                QPointF(line_x, bar.bottom() - inset),
            )

        # Icons, growing upward from the bottom, plus a dot under open apps
        icon_bottom = self.height() - BOTTOM_PAD
        self.icon_rects = []
        for i, (pixmap, left, size) in enumerate(zip(self.pixmaps, lefts, sizes)):
            rect = QRectF(left, icon_bottom - size - self.bounce_height(i), size, size)
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
            self.icon_rects.append(rect)

            if SHOW_OPEN_DOTS and self.windows[i]:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, 210))
                dot_center = QPointF(left + size / 2, self.height() - BOTTOM_PAD / 2)
                painter.drawEllipse(dot_center, DOT_RADIUS, DOT_RADIUS)

        # Name label above the hovered icon
        hovered = self.hovered_index()
        if hovered is not None:
            icon_rect = self.icon_rects[hovered]
            painter.setFont(QFont("Segoe UI", 10))
            metrics = painter.fontMetrics()
            # Long window titles get cut off with "..."
            name = metrics.elidedText(self.items[hovered]["name"], Qt.ElideRight, LABEL_MAX_WIDTH)
            label_w = metrics.horizontalAdvance(name) + 16
            label_h = metrics.height() + 8
            label_x = icon_rect.center().x() - label_w / 2
            label_x = max(0, min(label_x, self.width() - label_w))  # keep it on screen
            label = QRectF(label_x, icon_rect.top() - label_h - 6, label_w, label_h)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(*SOLID_COLOR[:3], 235))
            painter.drawRoundedRect(label, 6, 6)
            painter.setPen(QColor("white"))
            painter.drawText(label, Qt.AlignCenter, name)

    # ----- Mouse -----

    def mouseMoveEvent(self, event):
        self.mouse_x = event.position().x()

    def leaveEvent(self, event):
        self.mouse_x = None
        self.update()

    def clicked_index(self, event):
        """Which icon was clicked, or None."""
        for i, rect in enumerate(self.icon_rects):
            if rect.contains(event.position()):
                return i
        return None

    def mouseReleaseEvent(self, event):
        i = self.clicked_index(event)
        item = self.items[i] if i is not None else {}
        action = item.get("action")

        if event.button() == Qt.LeftButton and i is not None:
            if action in KEY_ACTIONS:
                press_keys(*KEY_ACTIONS[action])      # Start, Search, clock, etc.
            elif action == "tray":
                self.toggle_taskbar()
            elif action == "wifi":
                open_wifi_list()
            elif self.windows[i]:
                bring_to_front(self.windows[i][0])    # already open: switch to it
            elif item.get("aumid") or item.get("path"):
                open_app(item)
                if BOUNCE_ON_LAUNCH:
                    self.bounce_start[i] = time.monotonic()
        elif event.button() == Qt.RightButton:
            if action == "start":
                press_keys(VK_LWIN, VK_X)             # opens the Win+X menu
            else:
                menu = QMenu(self)
                if ON_WINDOWS:
                    startup = menu.addAction("Start when I sign in")
                    startup.setCheckable(True)
                    startup.setChecked(starts_with_windows())
                    startup.toggled.connect(set_start_with_windows)
                    menu.addSeparator()
                quit_action = menu.addAction("Quit Dock")
                quit_action.triggered.connect(QApplication.quit)
                menu.exec(event.globalPosition().toPoint())


def main():
    # "python dock.py --restore" brings the taskbar back if it ever gets stuck hidden
    if "--restore" in sys.argv:
        if ON_WINDOWS:
            set_taskbar_visible(True)
        print("Taskbar restored.")
        return

    if ON_WINDOWS:
        CoInitialize(None)  # lets us ask Windows for Store app names and icons

    app = QApplication(sys.argv)

    # Only one dock at a time: if one is already running, quietly quit
    lock = QLockFile(QDir.temp().filePath("mac-style-dock.lock"))
    if not lock.tryLock(100):
        return

    dock = Dock(load_apps())
    dock.show()
    signal.signal(signal.SIGINT, lambda *args: app.quit())  # Ctrl+C quits cleanly

    hide_taskbar = ON_WINDOWS and HIDE_WINDOWS_TASKBAR
    if hide_taskbar:
        set_taskbar_visible(False)
        watcher = QTimer()
        watcher.timeout.connect(keep_taskbar_hidden)
        watcher.start(500)  # check twice a second

    try:
        app.exec()
    finally:
        if hide_taskbar:
            set_taskbar_visible(True)  # always bring the taskbar back on the way out


if __name__ == "__main__":
    main()
