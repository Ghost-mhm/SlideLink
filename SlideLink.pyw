# SlideLink · EN: SlideLink desktop app for Windows: tray icon, app and presenter windows, single instance, autostart.
# SlideLink · DE: SlideLink-Desktop-App für Windows: Tray-Symbol, App- und Präsentationsfenster, nur eine Instanz, Autostart.
"""SlideLink desktop app (Windows).

Runs the SlideLink server in the background with a tray icon - like Steam - and opens
SlideLink in its own app window. Closing the window keeps SlideLink running so linked
phones stay connected; use the tray menu to quit.

    pythonw SlideLink.pyw               start (or bring up the window if already running)
    pythonw SlideLink.pyw --background  start in the tray only (used for "Start with Windows")
"""
import ctypes
import ctypes.wintypes as wt
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
PORT = int(os.environ.get("SLIDELINK_PORT", "8765"))
URL = f"http://localhost:{PORT}"
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SlideLink")
os.makedirs(APP_DIR, exist_ok=True)
ICON = os.path.join(ROOT, "static", "icon.ico")
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\SlideLink.lnk")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# pythonw has no console: keep a log instead
if sys.stdout is None or not sys.stdout.isatty():
    _log = open(os.path.join(APP_DIR, "slidelink.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log
    print(f"\n--- SlideLink start {time.strftime('%Y-%m-%d %H:%M:%S')} ---")


# EN: True if a SlideLink server already answers on this PC.
# DE: True, wenn auf diesem PC schon ein SlideLink-Server antwortet.
def server_running():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except OSError:
        return False


# EN: Path of Chrome or Edge (for clean app windows without tabs).
# DE: Pfad von Chrome oder Edge (für saubere App-Fenster ohne Tabs).
def find_browser():
    """Chrome or Edge, for a clean app window (own taskbar icon, no tabs or address bar)."""
    import winreg
    for exe in ("chrome.exe", "msedge.exe"):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    path = winreg.QueryValue(k, None)
                    if path and os.path.exists(path):
                        return path
            except OSError:
                pass
    for p in (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
              r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        p = os.path.expandvars(p)
        if os.path.exists(p):
            return p
    return None


_procs = {}          # "main" / "present" -> the browser process we started for that window


# EN: Visible top-level windows of a process.
# DE: Sichtbare Hauptfenster eines Prozesses.
def _windows_of(pid):
    """Visible top-level windows of a process."""
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    # EN: EnumWindows callback: collects the windows of the wanted process.
    # DE: EnumWindows-Rückruf: sammelt die Fenster des gesuchten Prozesses.
    def cb(hwnd, _):
        p = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(p))
        if p.value == pid and ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(hwnd)) \
                and ctypes.windll.user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd)) > 0:
            found.append(hwnd)
        return True
    ctypes.windll.user32.EnumWindows(proc(cb), 0)
    return found


# EN: Brings a window to the front (restores it only if minimized).
# DE: Holt ein Fenster nach vorne (stellt es nur wieder her, wenn es minimiert ist).
def _to_front(hwnd):
    u = ctypes.windll.user32
    if u.IsIconic(ctypes.c_void_p(hwnd)):
        u.ShowWindow(ctypes.c_void_p(hwnd), 9)                  # SW_RESTORE, only if minimized (keeps maximized)
    u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)  # Alt tap: Windows then allows taking the focus
    u.SetForegroundWindow(ctypes.c_void_p(hwnd))


# EN: True for presentation windows (title '<slide>/<count> · ...').
# DE: True für Präsentationsfenster (Titel '<Folie>/<Anzahl> · ...').
def _is_presenter_title(t):
    import re
    return bool(re.match(r"^\d+/\d+ · ", t))


# EN: Brings an already open SlideLink window to the front; False if there is none.
# DE: Holt ein schon offenes SlideLink-Fenster nach vorne; False, wenn keines offen ist.
def _focus_existing(name):
    p = _procs.get(name)
    if p and p.poll() is None:
        wins = _windows_of(p.pid)
        if wins:
            _to_front(wins[0])
            return True
    if name == "main":
        # a SlideLink window from an earlier start (not tracked by this run): bring that one forward too
        for pid in _profile_browser_pids():
            for hwnd in _windows_of(pid):
                buf = ctypes.create_unicode_buffer(300)
                ctypes.windll.user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 300)
                if not _is_presenter_title(buf.value):
                    _set_window_icon(hwnd)
                    _to_front(hwnd)
                    return True
    return False


# EN: Waits for a new window, brings it to the front and maximizes it if wanted.
# DE: Wartet auf ein neues Fenster, holt es nach vorne und maximiert es auf Wunsch.
def _set_window_icon(hwnd):
    """Window + taskbar icon straight from icon.ico (Windows picks the exact size for the display scaling).
    Without this the browser window shows a scaled-up page icon, which looks blurry in the taskbar."""
    u = ctypes.windll.user32
    u.LoadImageW.restype = ctypes.c_void_p
    u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    big = u.LoadImageW(None, ICON, 1, u.GetSystemMetrics(11), u.GetSystemMetrics(12), 0x10)    # SM_CXICON, LR_LOADFROMFILE
    small = u.LoadImageW(None, ICON, 1, u.GetSystemMetrics(49), u.GetSystemMetrics(50), 0x10)  # SM_CXSMICON
    if big:
        u.SendMessageW(ctypes.c_void_p(hwnd), 0x0080, ctypes.c_void_p(1), ctypes.c_void_p(big))     # WM_SETICON, ICON_BIG
    if small:
        u.SendMessageW(ctypes.c_void_p(hwnd), 0x0080, ctypes.c_void_p(0), ctypes.c_void_p(small))   # ICON_SMALL
    try:
        _set_taskbar_identity(hwnd)
    except Exception as e:                     # never let the icon stop the window from opening
        print("taskbar identity:", e)


APP_ID = "SlideLink.Desktop"


def _set_taskbar_identity(hwnd):
    """Registers the browser window with Windows as the SlideLink app (own app id + icon.ico).
    The taskbar then shows SlideLink's sharp icon, not the browser's scaled-up page icon, and groups it as its own app."""
    import uuid as _uuid

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

        def __init__(self, s):
            u = _uuid.UUID(s)
            super().__init__(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("pwsz", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]

    fmt = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"       # PKEY_AppUserModel_*
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    values = [(5, APP_ID)]                                # own app id: the taskbar then uses the window icon (icon.ico)

    store = ctypes.c_void_p()
    iid = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")  # IID_IPropertyStore
    if ctypes.windll.shell32.SHGetPropertyStoreForWindow(ctypes.c_void_p(hwnd), ctypes.byref(iid), ctypes.byref(store)) != 0:
        return
    vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(vtbl[6])
    commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    try:
        for pid, text in values[1:] + values[:1]:         # the id last: Windows reads the relaunch data when the id changes
            key = PROPERTYKEY(GUID(fmt), pid)
            pv = PROPVARIANT(31, 0, 0, 0, text, None)     # VT_LPWSTR
            set_value(store, ctypes.byref(key), ctypes.byref(pv))
        commit(store)
    finally:
        release(store)


def _keep_icon(pid, seconds=20):
    """The browser sets its own icon again while the page loads: set ours a few more times."""
    for _ in range(int(seconds / 2)):
        for hwnd in _windows_of(pid):
            _set_window_icon(hwnd)
        time.sleep(2)


def _front_when_ready(p, maximize=False):
    for _ in range(40):                     # bring the new window to the front once it exists (max ~10 s)
        time.sleep(0.25)
        wins = _windows_of(p.pid)
        if wins:
            _set_window_icon(wins[0])
            _to_front(wins[0])
            if maximize:                    # Chrome restores its remembered size; maximize it ourselves
                ctypes.windll.user32.ShowWindow(ctypes.c_void_p(wins[0]), 3)   # SW_MAXIMIZE
            threading.Thread(target=_keep_icon, args=(p.pid,), daemon=True).start()
            return


# EN: Opens (or focuses) the SlideLink app window; fullscreen=True means maximized with border.
# DE: Öffnet (oder fokussiert) das SlideLink-App-Fenster; fullscreen=True heißt maximiert mit Rahmen.
def open_window(path="/", fullscreen=False):
    """The SlideLink app window (library/editor). fullscreen=True: maximized window with title bar and border."""
    exe = find_browser()
    if not exe:
        return webbrowser.open(URL + path)
    if path == "/" and _focus_existing("main"):
        return
    size = ["--start-maximized"] if fullscreen else ["--window-size=1440,900"]   # app window: maximized, with its border
    p = subprocess.Popen([exe, f"--app={URL}{path}", f"--user-data-dir={os.path.join(APP_DIR, 'window')}",
                          *size, "--no-first-run", "--no-default-browser-check"], creationflags=NO_WINDOW)
    if path == "/":
        _procs["main"] = p
    threading.Thread(target=_front_when_ready, args=(p, fullscreen), daemon=True).start()


# EN: Browser processes that use SlideLink's own window profiles.
# DE: Browser-Prozesse, die SlideLinks eigene Fensterprofile nutzen.
def _profile_browser_pids():
    """Browser processes that run SlideLink's own window profiles (also ones from an earlier run)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='msedge.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{APP_DIR}\\window*' -or $_.CommandLine -like '*{APP_DIR}\\present-window*' }} | "
          "Where-Object { $_.CommandLine -notlike '*--type=*' } | ForEach-Object { $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                       text=True, creationflags=NO_WINDOW, timeout=20)
    return {int(x) for x in r.stdout.split() if x.isdigit()}


# EN: Closes the presentation window(s) on the PC (the app window stays).
# DE: Schließt das/die Präsentationsfenster auf dem PC (das App-Fenster bleibt).
def close_windows():
    """'Close window on PC' on the phone: closes the presentation window(s) on the PC.
    SlideLink itself keeps running in the tray, so the phone stays connected and can open them again."""
    # only presentation windows: their title is "<slide>/<count> · <title>"; the SlideLink app window stays open
    import re
    pids = {p.pid for p in _procs.values() if p.poll() is None} | _profile_browser_pids()
    for pid in pids:
        for hwnd in _windows_of(pid):
            buf = ctypes.create_unicode_buffer(300)
            ctypes.windll.user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 300)
            if re.match(r"^\d+/\d+ · ", buf.value):
                ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x0010, 0, 0)   # WM_CLOSE
    _procs.pop("present", None)


# EN: Opens the presentation borderless in fullscreen (kiosk), or brings it to the front.
# DE: Öffnet die Präsentation randlos im Vollbild (Kiosk) oder holt sie nach vorne.
def show_presenter():
    """'Show on PC': the presentation borderless in fullscreen (kiosk window; Alt+F4 closes it).
    If it is already open, it is brought to the front instead of opening a second one."""
    if _focus_existing("present"):
        return
    exe = find_browser()
    if not exe:
        return webbrowser.open(URL + "/present")
    profile = os.path.join(APP_DIR, "present-window")
    # a presenter left over from an earlier run can't be switched to kiosk mode: close it first
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='msedge.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '*{profile}*' }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                   creationflags=NO_WINDOW, timeout=20)
    p = subprocess.Popen([exe, "--kiosk", f"{URL}/present", f"--user-data-dir={profile}", "--no-first-run",
                          "--no-default-browser-check", "--disable-session-crashed-bubble",
                          "--autoplay-policy=no-user-gesture-required", "--disable-features=Translate"],
                         creationflags=NO_WINDOW)
    _procs["present"] = p
    threading.Thread(target=_front_when_ready, args=(p,), daemon=True).start()


# EN: True if 'Start with Windows' is on.
# DE: True, wenn 'Mit Windows starten' an ist.
def startup_enabled():
    return os.path.exists(STARTUP_LNK)


# EN: Switches 'Start with Windows' on or off (shortcut in the Startup folder).
# DE: Schaltet 'Mit Windows starten' an oder aus (Verknüpfung im Autostart-Ordner).
def set_startup(on):
    if not on:
        try:
            os.remove(STARTUP_LNK)
        except OSError:
            pass
        return
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LNK}');"
          f"$s.TargetPath='{pyw}';$s.Arguments='\"{os.path.abspath(__file__)}\" --background';"
          f"$s.WorkingDirectory='{ROOT}';$s.IconLocation='{ICON},0';$s.Description='SlideLink';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], creationflags=NO_WINDOW)


# ------------------------------------------------------------------ tray icon (pure ctypes)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


# EN: Win32 window class structure (for the hidden tray window).
# DE: Win32-Fensterklassen-Struktur (für das versteckte Tray-Fenster).
class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


# EN: Win32 GUID structure.
# DE: Win32-GUID-Struktur.
class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


# EN: Win32 tray icon data structure.
# DE: Win32-Struktur für Tray-Symbol-Daten.
class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.SetMenuDefaultItem.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE

WM_DESTROY, WM_CLOSE, WM_USER = 0x0002, 0x0010, 0x0400
WM_TRAY = WM_USER + 20
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0, 0x800, 0x8
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40

CMD_OPEN, CMD_PRESENT, CMD_DEVICES, CMD_STARTUP, CMD_FOLDER, CMD_QUIT = range(1, 7)


# EN: The tray icon with its menu, written with ctypes (no extra packages).
# DE: Das Tray-Symbol mit Menü, geschrieben mit ctypes (keine Zusatzpakete).
class Tray:
    # EN: Creates the hidden window and adds the tray icon.
    # DE: Erstellt das versteckte Fenster und fügt das Tray-Symbol hinzu.
    def __init__(self, on_quit):
        self.on_quit = on_quit
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")  # before any window message arrives
        self.nid = None
        self.hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wndproc)  # keep a reference or Windows calls freed memory
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=self.hinst,
                         lpszClassName="SlideLinkTray")
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, "SlideLinkTray", "SlideLink", 0, 0, 0, 0, 0, None, None, self.hinst, None)
        self.hicon = user32.LoadImageW(None, ICON, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        self.nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
                                   uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP, uCallbackMessage=WM_TRAY,
                                   hIcon=self.hicon)
        self.nid.szTip = "SlideLink – running"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))

    # EN: Shows a Windows notification balloon.
    # DE: Zeigt eine Windows-Benachrichtigung.
    def notify(self, title, text):
        self.nid.uFlags = NIF_INFO
        self.nid.szInfoTitle = title[:63]
        self.nid.szInfo = text[:255]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    # EN: Shows the right-click menu.
    # DE: Zeigt das Rechtsklick-Menü.
    def menu(self):
        m = user32.CreatePopupMenu()
        user32.AppendMenuW(m, MF_STRING, CMD_OPEN, "Open SlideLink")
        user32.AppendMenuW(m, MF_STRING, CMD_PRESENT, "Present current deck")
        user32.AppendMenuW(m, MF_STRING, CMD_DEVICES, "Linked devices…")
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING | (MF_CHECKED if startup_enabled() else 0), CMD_STARTUP, "Start with Windows")
        user32.AppendMenuW(m, MF_STRING, CMD_FOLDER, "Open data folder")
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING, CMD_QUIT, "Quit SlideLink")
        user32.SetMenuDefaultItem(m, CMD_OPEN, 0)
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD, pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(m)
        self.command(cmd)

    # EN: Runs the chosen menu entry.
    # DE: Führt den gewählten Menüeintrag aus.
    def command(self, cmd):
        if cmd == CMD_OPEN:
            open_window("/")
        elif cmd == CMD_PRESENT:
            threading.Thread(target=show_presenter, daemon=True).start()
        elif cmd == CMD_DEVICES:
            open_window("/?devices")
        elif cmd == CMD_STARTUP:
            set_startup(not startup_enabled())
        elif cmd == CMD_FOLDER:
            os.startfile(APP_DIR)
        elif cmd == CMD_QUIT:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    # EN: Window procedure: clicks on the icon, menu, quit, Explorer restarts.
    # DE: Fensterprozedur: Klicks auf das Symbol, Menü, Beenden, Explorer-Neustarts.
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                open_window("/")
            elif lparam == WM_RBUTTONUP:
                self.menu()
            return 0
        if msg == self.taskbar_created and self.nid is not None:  # Explorer restarted: put the icon back
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))
            return 0
        if msg == WM_CLOSE:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            self.on_quit()
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # EN: Windows message loop (runs until quit).
    # DE: Windows-Nachrichtenschleife (läuft bis zum Beenden).
    def loop(self):
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# EN: SlideLink already runs: ask it to show its window, then this second start quits.
# DE: SlideLink läuft schon: Es soll sein Fenster zeigen, dann beendet sich dieser zweite Start.
def _ask_running_instance_to_show():
    """SlideLink is already running: ask it to bring its window forward (or open it), then quit."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request(URL + "/api/op", data=json.dumps({"op": "open_on_pc", "by": "SlideLink"}).encode(),
                                     headers={"Content-Type": "application/json", "Origin": URL}, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        print("could not reach the running SlideLink:", e)


# EN: Starts SlideLink: single-instance check, server, folder watcher, tray icon and window.
# DE: Startet SlideLink: Einzelinstanz-Prüfung, Server, Ordnerüberwachung, Tray-Symbol und Fenster.
def main():
    background = "--background" in sys.argv
    # only one SlideLink: a second start just shows the window of the first one
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, "Local\\SlideLink.Desktop")   # kept open while SlideLink runs
    already = ctypes.get_last_error() == 183                     # ERROR_ALREADY_EXISTS
    if already or server_running():
        if not background:
            for _ in range(20):                 # the first one may still be starting its server
                if server_running():
                    break
                time.sleep(0.5)
            _ask_running_instance_to_show()
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    import server
    from http.server import ThreadingHTTPServer
    server.ON_SHOW_PC = show_presenter                                     # "Show on PC" from the phone: borderless fullscreen
    server.ON_OPEN_PC = lambda: open_window("/", fullscreen=True)           # "Open on PC" from the phone
    server.ON_CLOSE_PC = close_windows                                     # "Close window" from the phone
    server.start_background()                                              # watches ~/Claude Presentations
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"server on {URL}, data in {server.DATA_DIR}")

    tray = Tray(on_quit=httpd.shutdown)
    server.ON_NOTIFY = tray.notify       # "… added / updated" when the folder syncs
    first_run_flag = os.path.join(APP_DIR, ".tray-hint-shown")
    if not os.path.exists(first_run_flag):
        tray.notify("SlideLink is running", "Closing the window keeps SlideLink in the tray so your phone stays "
                                            "connected. Right-click the tray icon for options.")
        open(first_run_flag, "w").close()
    # auto-update: every new stable release from GitHub is installed silently (not while presenting),
    # and the newest phone app is pulled so phones are offered it
    import updater
    presenting = lambda: bool(_procs.get("present") and _procs["present"].poll() is None)
    cur = updater.installed_version()[0] or ""
    phone_app = lambda: ((server.app_latest() or {}).get("versionCode", 0), server.APP_DIR_PULLED,
                         not updater.parse(cur)[1] if cur else True)   # beta desktop (or source folder) also takes beta apps
    threading.Thread(target=updater.run_forever, args=(ROOT, tray.notify, presenting, print, phone_app), daemon=True).start()
    if not background:
        open_window("/", fullscreen=True)     # the app starts maximized (with border)
    tray.loop()


if __name__ == "__main__":
    main()
