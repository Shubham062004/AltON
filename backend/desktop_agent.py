import os
import sys
import subprocess
import time
import psutil
import pyautogui
import pygetwindow as gw
import pyperclip

# Safety settings for PyAutoGUI
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.1

try:
    import pydirectinput
    pydirectinput.FAILSAFE = False
    pydirectinput.PAUSE = 0.05
except ImportError as e:
    pydirectinput = None
    print(f"[DESKTOP AGENT]: pydirectinput not available: {e} (Python: {sys.executable})")

try:
    from pywinauto import Desktop, Application
    from pywinauto.findwindows import ElementNotFoundError
    from pywinauto import timings
    PYWINAUTO_AVAILABLE = True
except ImportError as e:
    Desktop = None
    Application = None
    ElementNotFoundError = Exception
    timings = None
    PYWINAUTO_AVAILABLE = False
    print(f"[DESKTOP AGENT]: pywinauto not available: {e} (Python: {sys.executable})")

try:
    import win32gui
    import win32process
    import win32con
    import win32api
    WIN32_AVAILABLE = True
except ImportError:
    win32gui = None
    win32process = None
    win32con = None
    win32api = None
    WIN32_AVAILABLE = False

import functools
import glob
try:
    import winreg  # Windows-only stdlib module; project targets Windows
except ImportError:
    winreg = None

_APP_ALIASES = {
    "notebook": "notepad",
    "editor": "notepad",
    "code": "vscode",
    "browser": "chrome",
    "internet": "chrome"
}

_APP_MAPPINGS = {
    "notepad": "notepad.exe",
    "calc": "calc.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "vscode": "code",
    "volume": "sndvol.exe",
    "paint": "mspaint.exe",
    "chrome": "chrome",
    "cmd": "cmd.exe",
    "terminal": "wt.exe"
}

# Known browser executables for URL opening fallback
_BROWSER_EXE_PATHS = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "msedge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
}


@functools.lru_cache(maxsize=1)
def _index_start_menu_shortcuts() -> dict:
    """
    Scans both the per-user and all-users Start Menu for .lnk shortcuts and
    returns {lowercased shortcut name: full .lnk path}. This is what lets the
    agent open ANY installed application by name, not just a hardcoded list.
    Cached for the life of the process; call _index_start_menu_shortcuts.cache_clear()
    if the person installs new software mid-session.
    """
    index = {}
    search_roots = [
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
    ]
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for lnk_path in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            name = os.path.splitext(os.path.basename(lnk_path))[0].lower()
            index[name] = lnk_path
    return index


def _resolve_via_start_menu(app_name: str):
    """Fuzzy-match app_name against indexed Start Menu shortcuts."""
    index = _index_start_menu_shortcuts()
    if app_name in index:
        return index[app_name]
    # Fuzzy: substring match either direction (e.g. "spotify" matches "Spotify Music")
    candidates = [path for name, path in index.items() if app_name in name or name in app_name]
    return candidates[0] if candidates else None


def _resolve_via_app_paths_registry(app_name: str):
    """Checks the Windows 'App Paths' registry key, which most installers register."""
    if winreg is None:
        return None
    exe_guess = app_name if app_name.endswith(".exe") else f"{app_name}.exe"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_guess}"
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value and os.path.exists(value):
                    return value
        except (FileNotFoundError, OSError):
            continue
    return None


def resolve_app_target(app_name: str) -> str:
    """
    Resolves a natural-language app name to something launchable, trying in order:
    1. Known alias/mapping table (fast path for common apps)
    2. Windows 'App Paths' registry
    3. Start Menu shortcut index (covers virtually anything the user has installed)
    4. Falls back to the raw name (lets `start <name>` try its luck, e.g. for
       apps already on PATH)
    """
    name = app_name.lower().strip()
    name = _APP_ALIASES.get(name, name)

    if name in _APP_MAPPINGS:
        return _APP_MAPPINGS[name]

    reg_path = _resolve_via_app_paths_registry(name)
    if reg_path:
        return reg_path

    shortcut_path = _resolve_via_start_menu(name)
    if shortcut_path:
        return shortcut_path

    return name


def launch_app(app_name: str) -> bool:
    """Launch ANY local system application by name, forcing it to open in foreground and stay open as a detached process."""
    cmd = resolve_app_target(app_name)
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    try:
        # Quote the path in case it contains spaces (e.g. "C:\Program Files\...")
        subprocess.Popen(f'start "" "{cmd}"', shell=True, creationflags=creation_flags)
        return True
    except Exception as e:
        print(f"Failed to launch app {app_name} with resolved target {cmd}: {e}")
        try:
            subprocess.Popen([cmd], creationflags=creation_flags)
            return True
        except Exception as e2:
            print(f"Fallback direct launch failed: {e2}")
            return False


def launch_app_and_wait(app_name: str, timeout: float = 8.0) -> dict:
    """
    Launches an application and waits for its window to appear, using pywinauto
    for reliable window detection. Returns a dict with status info and optionally
    a pywinauto window wrapper for immediate interaction.

    Returns: {"success": bool, "window": pywinauto_wrapper_or_None, "method": str, "message": str}
    """
    app_lower = app_name.lower().strip()
    target_name = _APP_ALIASES.get(app_lower, app_lower)
    result = {"success": False, "window": None, "method": "", "message": ""}

    # --- Strategy 1: pywinauto Application().start() ---
    if PYWINAUTO_AVAILABLE and Application is not None:
        cmd = resolve_app_target(target_name)
        try:
            print(f"[LAUNCH]: Trying pywinauto Application().start() for '{cmd}'")
            app = Application(backend="uia").start(f'"{cmd}"' if " " in cmd else cmd, timeout=10)
            # Wait for the main window to appear
            try:
                app.wait_cpu_usage_lower(threshold=5, timeout=5)
            except Exception:
                pass
            # Try to connect to the window
            try:
                dlg = app.top_window()
                if dlg and dlg.window_text():
                    dlg.set_focus()
                    result["success"] = True
                    result["window"] = dlg
                    result["method"] = "pywinauto_start"
                    result["message"] = f"Launched '{app_name}' via pywinauto — window: '{dlg.window_text()}'"
                    print(f"[LAUNCH]: {result['message']}")
                    return result
            except Exception as e:
                print(f"[LAUNCH]: pywinauto top_window() after start failed: {e}")
        except Exception as e:
            print(f"[LAUNCH]: pywinauto Application().start() failed for '{cmd}': {e}")

    # --- Strategy 2: subprocess launch + pywinauto window wait ---
    launched = launch_app(target_name)
    if launched:
        # Poll for the window using pywinauto UIA Desktop enumeration
        win = _wait_for_window_pywinauto(target_name, timeout=timeout)
        if win:
            try:
                win.set_focus()
            except Exception:
                pass
            result["success"] = True
            result["window"] = win
            result["method"] = "subprocess_plus_pywinauto_wait"
            result["message"] = f"Launched '{app_name}' via subprocess, window found by pywinauto"
            print(f"[LAUNCH]: {result['message']}")
            return result

        # Fallback: check via pygetwindow / psutil
        if _poll_window_exists(target_name, timeout=max(2.0, timeout - 5.0)):
            result["success"] = True
            result["method"] = "subprocess_plus_poll"
            result["message"] = f"Launched '{app_name}' via subprocess, detected via window title/process poll"
            print(f"[LAUNCH]: {result['message']}")
            return result

    # --- Strategy 3: Try known alternate paths ---
    from . import executor as _executor
    alt_launched = _executor.resolve_alt_path_and_launch(target_name)
    if alt_launched:
        win = _wait_for_window_pywinauto(target_name, timeout=5.0)
        if win:
            try:
                win.set_focus()
            except Exception:
                pass
            result["success"] = True
            result["window"] = win
            result["method"] = "alt_path_plus_pywinauto"
            result["message"] = f"Launched '{app_name}' via alternate path, window found by pywinauto"
            return result
        if _poll_window_exists(target_name, timeout=3.0):
            result["success"] = True
            result["method"] = "alt_path_plus_poll"
            result["message"] = f"Launched '{app_name}' via alternate path, detected via poll"
            return result

    result["message"] = f"All launch strategies failed for '{app_name}'"
    print(f"[LAUNCH]: {result['message']}")
    return result


def _wait_for_window_pywinauto(title_substring: str, timeout: float = 8.0):
    """
    Polls the UIA Desktop for a window whose title contains title_substring.
    Returns the pywinauto wrapper if found, None otherwise.
    """
    if not PYWINAUTO_AVAILABLE:
        return None
    needle = title_substring.lower().strip()
    alias_map = {
        "notebook": "notepad", "editor": "notepad",
        "code": "visual studio code", "vscode": "visual studio code",
        "browser": "chrome", "internet": "chrome",
        "calc": "calculator", "calculator": "calculator",
    }
    search_terms = [needle]
    if needle in alias_map:
        search_terms.append(alias_map[needle])

    start = time.time()
    while time.time() - start < timeout:
        try:
            for w in Desktop(backend="uia").windows():
                try:
                    title = (w.window_text() or "").lower()
                except Exception:
                    continue
                for term in search_terms:
                    if term and term in title:
                        return w
        except Exception as e:
            print(f"[LAUNCH WAIT]: pywinauto Desktop enum error: {e}")
        time.sleep(0.5)
    return None


def _poll_window_exists(app_name: str, timeout: float = 5.0) -> bool:
    """Fallback poll using pygetwindow and psutil."""
    app_lower = app_name.lower().strip()
    alias_map = {"notebook": "notepad", "editor": "notepad", "code": "vscode", "browser": "chrome", "internet": "chrome"}
    target_name = alias_map.get(app_lower, app_lower)

    start = time.time()
    while time.time() - start < timeout:
        try:
            for w in gw.getAllWindows():
                if w.title and target_name in w.title.lower():
                    return True
        except Exception:
            pass
        try:
            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and target_name in proc.info['name'].lower():
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def close_app(process_name: str) -> bool:
    """Kill running process by name."""
    proc_name = process_name.lower().strip()
    if not proc_name.endswith(".exe"):
        proc_name += ".exe"
    
    killed = False
    for proc in psutil.process_iter(['name']):
        try:
            if proc.info['name'] and proc.info['name'].lower() == proc_name:
                proc.kill()
                killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return killed

def _get_uia_window(title_substring: str, timeout: float = 3.0):
    """
    Finds a top-level window via UI Automation whose title contains
    title_substring (case-insensitive). Returns a pywinauto WindowSpecification
    or None. This targets a SPECIFIC window by handle rather than relying on
    whatever the OS currently reports as foreground — which is what makes it
    resilient to the UIPI / focus-tracking issues pyautogui alone runs into.
    """
    if not PYWINAUTO_AVAILABLE:
        return None
    needle = title_substring.lower().strip()
    try:
        for w in Desktop(backend="uia").windows():
            try:
                title = w.window_text() or ""
            except Exception:
                continue
            if needle and needle in title.lower():
                return w
    except Exception as e:
        print(f"[UIA]: Desktop window enumeration failed: {e}")
    return None


def focus_window_uia(title_substring: str) -> bool:
    """
    Focus a window by title using UI Automation. Tries set_focus() (which
    internally uses AttachThreadInput + SetForegroundWindow with fallbacks
    pywinauto already implements, including a taskbar-click fallback for
    UIPI-restricted cases) then falls back to restore+click.
    """
    win = _get_uia_window(title_substring)
    if not win:
        return False
    try:
        if win.is_minimized():
            win.restore()
        win.set_focus()
        return True
    except Exception as e:
        print(f"[UIA]: set_focus failed for '{title_substring}': {e}")
        try:
            win.click_input()
            return True
        except Exception as e2:
            print(f"[UIA]: click_input fallback also failed: {e2}")
            return False


def focus_window_robust(title_substring: str, max_retries: int = 4) -> bool:
    """
    Enhanced multi-strategy window focus with verification.
    Tries in order:
      1. pywinauto UIA set_focus()
      2. win32gui SetForegroundWindow with AttachThreadInput trick
      3. pygetwindow activate
      4. pydirectinput Alt+Tab simulation (as nuclear last resort)
    Each attempt is followed by a verification check.
    """
    needle = title_substring.lower().strip()

    for attempt in range(1, max_retries + 1):
        # ----- Strategy 1: pywinauto UIA -----
        if PYWINAUTO_AVAILABLE:
            win = _get_uia_window(title_substring)
            if win:
                try:
                    if win.is_minimized():
                        win.restore()
                    win.set_focus()
                    time.sleep(0.3)
                    if _verify_foreground(needle):
                        print(f"[FOCUS ROBUST]: Focused '{title_substring}' via pywinauto UIA (attempt {attempt})")
                        return True
                except Exception as e:
                    print(f"[FOCUS ROBUST]: pywinauto set_focus failed: {e}")
                # Try click_input fallback
                try:
                    win.click_input()
                    time.sleep(0.3)
                    if _verify_foreground(needle):
                        print(f"[FOCUS ROBUST]: Focused '{title_substring}' via pywinauto click_input (attempt {attempt})")
                        return True
                except Exception:
                    pass

        # ----- Strategy 2: win32gui with AttachThreadInput -----
        if WIN32_AVAILABLE and win32gui:
            try:
                target_hwnd = _find_hwnd_by_title(needle)
                if target_hwnd:
                    _force_foreground_win32(target_hwnd)
                    time.sleep(0.3)
                    if _verify_foreground(needle):
                        print(f"[FOCUS ROBUST]: Focused '{title_substring}' via win32gui (attempt {attempt})")
                        return True
            except Exception as e:
                print(f"[FOCUS ROBUST]: win32gui focus failed: {e}")

        # ----- Strategy 3: pygetwindow -----
        try:
            windows = gw.getWindowsWithTitle(title_substring)
            if not windows:
                # Try fuzzy
                all_wins = gw.getAllWindows()
                windows = [w for w in all_wins if w.title and needle in w.title.lower()]
            if windows:
                win = windows[0]
                if win.isMinimized:
                    win.restore()
                win.activate()
                time.sleep(0.5)
                if _verify_foreground(needle):
                    print(f"[FOCUS ROBUST]: Focused '{title_substring}' via pygetwindow (attempt {attempt})")
                    return True
        except Exception as e:
            print(f"[FOCUS ROBUST]: pygetwindow activate failed: {e}")

        # ----- Strategy 4: Alt+Tab brute force (last resort) -----
        if attempt == max_retries and pydirectinput:
            try:
                pydirectinput.hotkey('alt', 'tab')
                time.sleep(0.5)
                if _verify_foreground(needle):
                    print(f"[FOCUS ROBUST]: Focused '{title_substring}' via Alt+Tab")
                    return True
            except Exception:
                pass

        time.sleep(0.3)

    print(f"[FOCUS ROBUST]: All strategies failed for '{title_substring}' after {max_retries} attempts")
    return False


def _find_hwnd_by_title(needle: str):
    """Find a window handle by title substring using win32gui."""
    if not win32gui:
        return None
    found = []
    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and needle in title.lower():
                found.append(hwnd)
        return True
    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass
    return found[0] if found else None


def _force_foreground_win32(hwnd):
    """Use the AttachThreadInput trick to steal foreground focus."""
    if not win32gui or not win32con:
        return
    try:
        # Get thread IDs
        foreground_hwnd = win32gui.GetForegroundWindow()
        if foreground_hwnd == hwnd:
            return  # Already focused
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        current_thread = win32api.GetCurrentThreadId() if win32api else 0

        if current_thread and target_thread:
            win32process.AttachThreadInput(current_thread, target_thread, True)
        
        # Restore if minimized
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE if hasattr(win32con, 'SW_RESTORE') else 9)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)

        if current_thread and target_thread:
            win32process.AttachThreadInput(current_thread, target_thread, False)
    except Exception as e:
        print(f"[WIN32]: _force_foreground_win32 error: {e}")


def _verify_foreground(needle: str) -> bool:
    """Check if the current foreground window title contains the needle."""
    title = ""
    if win32gui:
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            pass
    if not title:
        try:
            active = gw.getActiveWindow()
            title = active.title if active else ""
        except Exception:
            pass
    return bool(title and needle in title.lower())


_PYWINAUTO_ESCAPE_CHARS = str.maketrans({
    "{": "{{}", "}": "{}}", "(": "{(}", ")": "{)}",
    "+": "{+}", "^": "{^}", "%": "{%}", "~": "{~}"
})

def type_keyboard_into_window(title_substring: str, text: str) -> bool:
    """
    Types text directly into the named window via UI Automation's type_keys(),
    which sends WM_CHAR/keyboard events scoped to that specific window rather
    than depending on it currently owning OS-level keyboard focus. This is the
    primary typing path; callers should fall back to type_keyboard() (pydirectinput/
    pyautogui) only if this returns False (e.g. pywinauto isn't installed).
    """
    win = _get_uia_window(title_substring)
    if not win:
        return False
    try:
        if win.is_minimized():
            win.restore()
        win.set_focus()
        escaped = text.translate(_PYWINAUTO_ESCAPE_CHARS)
        win.type_keys(escaped, with_spaces=True, with_tabs=True, with_newlines=True, pause=0.02)
        return True
    except Exception as e:
        print(f"[UIA]: type_keys into '{title_substring}' failed: {e}")
        return False


def force_foreground(hwnd):
    """Forces a window to the foreground bypassing Windows UIPI restrictions via AttachThreadInput hack."""
    try:
        if not win32gui or not win32gui.IsWindowVisible(hwnd):
            return False
        
        # Get the thread of the window we want to focus
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        # Get the thread of the window currently holding focus
        foreground_thread = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[0]
        
        # Attach the threads to bypass the foreground lock
        if target_thread != foreground_thread:
            win32process.AttachThreadInput(foreground_thread, target_thread, True)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            win32process.AttachThreadInput(foreground_thread, target_thread, False)
        else:
            win32gui.SetForegroundWindow(hwnd)
            
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"Focus force failed: {e}")
        return False


def find_window_by_title(keyword):
    """Finds a window handle by searching for a keyword in its title."""
    if not keyword:
        return None
    keyword_lower = keyword.lower().strip()
    hwnd_list = []
    
    if win32gui:
        def callback(hwnd, extra):
            try:
                if win32gui.IsWindowVisible(hwnd):
                    txt = win32gui.GetWindowText(hwnd) or ""
                    if keyword_lower in txt.lower():
                        hwnd_list.append(hwnd)
            except Exception:
                pass
            return True
        
        try:
            win32gui.EnumWindows(callback, None)
        except Exception as e:
            print(f"[WIN32]: EnumWindows notice: {e}")
            
    if hwnd_list:
        return hwnd_list[0]
        
    # Fallback to pygetwindow
    try:
        wins = gw.getWindowsWithTitle(keyword)
        if wins:
            return wins[0]._hWnd
    except Exception:
        pass
    return None


def type_into_app(app_keyword, text):
    """Finds the app window, forces focus via AttachThreadInput UIPI bypass, and types text."""
    # 1. Find the window
    hwnd = find_window_by_title(app_keyword)
    if not hwnd:
        print(f"Window with keyword '{app_keyword}' not found.")
        # Fallback to fuzzy search or robust focus
        hwnd = focus_window_robust(app_keyword)
        if not hwnd:
            return False
        
    # 2. Force it to the foreground
    if force_foreground(hwnd):
        time.sleep(1.5)  # Wait for OS to register the focus change
        
        # 3. Type the text
        try:
            pyautogui.write(text, interval=0.05)
            return True
        except Exception as e:
            print(f"Pyautogui typing error: {e}")
            return False
    return False


def open_app_and_type(app_name, text_to_type):
    """Opens app natively using os.startfile/subprocess and types text forcefully using Win32 focus."""
    try:
        app_lower = app_name.lower().strip()
        # 1. Open App Natively
        if hasattr(os, "startfile"):
            if app_lower in ["notepad", "notebook"]:
                os.startfile('notepad.exe')
            elif app_lower == "vscode":
                os.startfile('code')
            elif app_lower in ["calc", "calculator"]:
                os.startfile('calc.exe')
            elif app_lower == "paint":
                os.startfile('mspaint.exe')
            else:
                target = resolve_app_target(app_lower)
                try:
                    os.startfile(target)
                except Exception:
                    launch_app(app_name)
        else:
            launch_app(app_name)
        
        time.sleep(2) # Wait for window creation
        
        # 2. Force Focus using Win32 API
        hwnd = find_window_by_title(app_name)
        if not hwnd:
            windows = []
            def find_window_cb(h, window_list):
                if win32gui and win32gui.IsWindowVisible(h) and app_lower in (win32gui.GetWindowText(h) or "").lower():
                    window_list.append(h)
                return True
            if win32gui:
                try:
                    win32gui.EnumWindows(find_window_cb, windows)
                except Exception:
                    pass
            if windows:
                hwnd = windows[0]
        
        if hwnd:
            if win32gui and win32con:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    force_foreground(hwnd)
            else:
                force_foreground(hwnd)
            time.sleep(1.5)
            
            # 3. Type Text
            pyautogui.write(text_to_type, interval=0.05)
            return True
        else:
            print(f"Window not found to focus for '{app_name}'. Falling back to type_into_app.")
            return type_into_app(app_name, text_to_type)
            
    except Exception as e:
        print(f"Desktop Automation Error: {e}")
        return False


def open_url_and_interact(url: str, search_query: str = None) -> bool:
    """Opens URL in default system browser (Chrome/Edge) and optionally interacts via keyboard."""
    try:
        # 1. Add autoplay if YouTube watch
        if ("youtube.com/watch" in url or "youtu.be" in url) and "autoplay" not in url:
            delimiter = "&" if "?" in url else "?"
            url += f"{delimiter}autoplay=1"

        # 2. Open in system's default browser
        webbrowser.open(url, new=2)
        time.sleep(3) # Wait for browser to load
        
        # 3. If it's a search task, use keyboard to type in YouTube's search bar
        if search_query:
            # Press '/' to focus YouTube search bar, type, and hit Enter
            pyautogui.press('/')
            time.sleep(0.5)
            pyautogui.write(search_query, interval=0.05)
            time.sleep(0.5)
            pyautogui.press('enter')
            
        return True
    except Exception as e:
        print(f"Browser Automation Error: {e}")
        return False


def open_maps_location(location_name: str) -> bool:
    """Directly opens Google Maps in browser with the location searched."""
    try:
        # Encode the location name for URL
        query = location_name.replace(" ", "+")
        url = f"https://www.google.com/maps/search/{query}"
        webbrowser.open(url, new=2)
        return True
    except Exception as e:
        print(f"Maps Error: {e}")
        return False


def smart_open_url(url, search_query=None):
    """Opens URL in default browser. If YouTube or Maps, auto-searches without typing."""
    try:
        if "youtube.com" in url and search_query:
            query = search_query.replace(" ", "+")
            url = f"https://www.youtube.com/results?search_query={query}"
        elif "google.com/maps" in url and search_query:
            query = search_query.replace(" ", "+")
            url = f"https://www.google.com/maps/search/{query}"
            
        webbrowser.open(url, new=2)
        time.sleep(3) # Wait for browser to load
        return True
    except Exception as e:
        print(f"Smart Open URL Error: {e}")
        return False


def vision_click_element(element_description):
    """
    THE JARVIS PROTOCOL: Takes a screenshot, asks AI where the element is, and clicks it.
    Works on ANY website or app. No DOM needed.
    """
    try:
        print(f"Vision Protocol Activated: Looking for '{element_description}'")
        # 1. Take Screenshot of the main screen
        screenshot = pyautogui.screenshot()
        
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel('gemini-2.0-flash')
                prompt = f"Find the UI element related to '{element_description}'. Return ONLY the center X and Y coordinates of that element as two comma-separated numbers. Example format: 540,320"
                
                response = model.generate_content([prompt, screenshot])
                coord_text = response.text.strip() if response and response.text else ""
                
                if ',' in coord_text:
                    parts = coord_text.split(',')
                    x = int(''.join(filter(str.isdigit, parts[0])))
                    y = int(''.join(filter(str.isdigit, parts[1])))
                    
                    if x > 0 and y > 0:
                        pyautogui.moveTo(x, y, duration=0.5)
                        time.sleep(0.2)
                        pyautogui.click()
                        print(f"Successfully clicked at {x},{y} via Gemini Vision")
                        return True
            except Exception as ge:
                print(f"Gemini Vision call error: {ge}")

        # Fallback vision locate via executor helper
        try:
            import tempfile
            import uuid
            temp_shot = os.path.join(tempfile.gettempdir(), f"vclick_{uuid.uuid4().hex[:8]}.png")
            screenshot.save(temp_shot)
            from backend.executor import locate_element_via_vision
            w, h = screenshot.size
            coords = locate_element_via_vision(temp_shot, element_description, w, h)
            try:
                if os.path.exists(temp_shot):
                    os.remove(temp_shot)
            except Exception:
                pass
            if coords:
                x, y = coords
                pyautogui.moveTo(x, y, duration=0.5)
                time.sleep(0.2)
                pyautogui.click()
                print(f"Successfully clicked at {x},{y} via Vision fallback")
                return True
        except Exception as ve:
            print(f"Vision locate fallback notice: {ve}")

        return False
    except Exception as e:
        print(f"Vision Click Failed: {e}")
        return False


def type_text_robust(text: str, target_title: str = None) -> dict:
    """
    Consolidated 4-tier typing pipeline with verification after each attempt.
    Tries in order:
      1. pywinauto type_keys() targeted to the specific window (most reliable)
      2. pydirectinput.write() — SendInput scan codes (bypasses most UIPI)
      3. Clipboard paste: pyperclip.copy() + Ctrl+V via pydirectinput
      4. pyautogui.write() — last resort

    Returns: {"success": bool, "method": str, "message": str}
    """
    result = {"success": False, "method": "", "message": ""}

    # Ensure focus first
    if target_title:
        focus_window_robust(target_title, max_retries=2)

    # ----- Tier 1: pywinauto type_keys (scoped to window) -----
    if target_title and PYWINAUTO_AVAILABLE:
        try:
            if type_keyboard_into_window(target_title, text):
                time.sleep(0.3)
                # Verify via UIA read-back
                uia_text = read_window_text_via_uia(target_title)
                if uia_text is not None and text.strip().lower() in uia_text.lower():
                    result["success"] = True
                    result["method"] = "pywinauto_type_keys"
                    result["message"] = f"Typed via pywinauto type_keys, verified via UIA read-back"
                    return result
                # Don't fail — the type might have worked but UIA can't read the control
                # (e.g. game windows, custom renderers). Try to verify via clipboard.
                if _verify_typed_text_clipboard(text):
                    result["success"] = True
                    result["method"] = "pywinauto_type_keys"
                    result["message"] = f"Typed via pywinauto type_keys, verified via clipboard"
                    return result
                print("[TYPE ROBUST]: pywinauto type_keys executed but verification inconclusive, trying next tier")
        except Exception as e:
            print(f"[TYPE ROBUST]: Tier 1 (pywinauto) error: {e}")

    # Re-focus for tier 2+
    if target_title:
        focus_window_robust(target_title, max_retries=2)

    # ----- Tier 2: pydirectinput.write() -----
    if pydirectinput:
        try:
            # pydirectinput.write() only works with basic ASCII chars
            # For complex text, we try char-by-char via press()
            ascii_safe = all(ord(c) < 128 and c.isprintable() for c in text)
            if ascii_safe:
                pydirectinput.write(text, interval=0.02)
            else:
                # char-by-char with shift handling for uppercase
                for char in text:
                    if char == ' ':
                        pydirectinput.press('space')
                    elif char == '\n':
                        pydirectinput.press('enter')
                    elif char == '\t':
                        pydirectinput.press('tab')
                    elif char.isalpha() and char.isupper():
                        pydirectinput.keyDown('shift')
                        pydirectinput.press(char.lower())
                        pydirectinput.keyUp('shift')
                    else:
                        try:
                            pydirectinput.press(char)
                        except Exception:
                            # Skip chars pydirectinput can't handle — clipboard tier will catch them
                            pass
            time.sleep(0.3)
            # Verify
            if target_title:
                uia_text = read_window_text_via_uia(target_title)
                if uia_text is not None and text.strip().lower() in uia_text.lower():
                    result["success"] = True
                    result["method"] = "pydirectinput_write"
                    result["message"] = "Typed via pydirectinput, verified via UIA"
                    return result
            if _verify_typed_text_clipboard(text):
                result["success"] = True
                result["method"] = "pydirectinput_write"
                result["message"] = "Typed via pydirectinput, verified via clipboard"
                return result
            print("[TYPE ROBUST]: pydirectinput write executed but verification inconclusive, trying next tier")
        except Exception as e:
            print(f"[TYPE ROBUST]: Tier 2 (pydirectinput) error: {e}")

    # Re-focus for tier 3
    if target_title:
        focus_window_robust(target_title, max_retries=2)
        # Select all and delete existing text before paste
        if pydirectinput:
            try:
                pydirectinput.hotkey('ctrl', 'a')
                time.sleep(0.1)
                pydirectinput.press('delete')
                time.sleep(0.1)
            except Exception:
                pass

    # ----- Tier 3: Clipboard paste (Ctrl+V) -----
    try:
        backup_clip = pyperclip.paste()
        pyperclip.copy(text)
        time.sleep(0.1)
        if pydirectinput:
            pydirectinput.hotkey('ctrl', 'v')
        else:
            pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.5)
        # Restore clipboard
        try:
            pyperclip.copy(backup_clip)
        except Exception:
            pass
        # Verify
        if target_title:
            uia_text = read_window_text_via_uia(target_title)
            if uia_text is not None and text.strip().lower() in uia_text.lower():
                result["success"] = True
                result["method"] = "clipboard_paste"
                result["message"] = "Typed via clipboard Ctrl+V, verified via UIA"
                return result
        if _verify_typed_text_clipboard(text):
            result["success"] = True
            result["method"] = "clipboard_paste"
            result["message"] = "Typed via clipboard Ctrl+V, verified via clipboard readback"
            return result
        print("[TYPE ROBUST]: clipboard paste executed but verification inconclusive, trying last tier")
    except Exception as e:
        print(f"[TYPE ROBUST]: Tier 3 (clipboard paste) error: {e}")

    # Re-focus for tier 4
    if target_title:
        focus_window_robust(target_title, max_retries=1)

    # ----- Tier 4: pyautogui.write() (last resort) -----
    try:
        pyautogui.write(text, interval=0.02)
        time.sleep(0.3)
        result["success"] = True  # Best-effort, can't always verify
        result["method"] = "pyautogui_write"
        result["message"] = "Typed via pyautogui (last resort, verification skipped)"
        return result
    except Exception as e:
        print(f"[TYPE ROBUST]: Tier 4 (pyautogui) error: {e}")

    result["message"] = f"All 4 typing tiers failed for text: '{text[:50]}...'"
    return result


def _verify_typed_text_clipboard(expected_text: str) -> bool:
    """Verify typed text by doing Ctrl+A, Ctrl+C and checking clipboard."""
    try:
        backup = pyperclip.paste()
        pyperclip.copy("")
        if pydirectinput:
            pydirectinput.hotkey('ctrl', 'a')
            time.sleep(0.15)
            pydirectinput.hotkey('ctrl', 'c')
        else:
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.15)
            pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.3)
        content = pyperclip.paste()
        pyperclip.copy(backup)
        return expected_text.strip().lower() in content.lower()
    except Exception as e:
        print(f"[VERIFY]: Clipboard verification failed: {e}")
        return False


def read_window_text_via_uia(title_substring: str):
    """
    Reads back the actual text content of a window's main text/edit control via
    UI Automation — this is a ground-truth verification method that doesn't
    depend on simulated Ctrl+A/Ctrl+C clipboard tricks (which are themselves
    subject to the same UIPI restrictions as typing). Returns the text string,
    or None if it couldn't be determined (caller should fall back to clipboard
    or vision verification in that case, not treat None as failure).
    """
    win = _get_uia_window(title_substring)
    if not win:
        return None
    for control_type in ("Edit", "Document", "Text"):
        try:
            ctrl = win.child_window(control_type=control_type)
            if ctrl.exists():
                return ctrl.window_text()
        except ElementNotFoundError:
            continue
        except Exception as e:
            print(f"[UIA]: Failed reading '{control_type}' control text: {e}")
            continue
    return None


def open_url_in_system_browser(url: str, preferred_browser: str = None) -> dict:
    """
    Opens a URL in the system's default (or specified) browser using pywinauto
    for launch + window detection. This gives us a window handle for subsequent
    desktop-level interaction, unlike plain webbrowser.open().

    Returns: {"success": bool, "window": pywinauto_wrapper_or_None, "method": str, "browser": str}
    """
    result = {"success": False, "window": None, "method": "", "browser": ""}

    # Determine which browser to use
    browsers_to_try = []
    if preferred_browser:
        browsers_to_try.append(preferred_browser.lower())
    # Always try all known browsers as fallbacks
    browsers_to_try.extend(["chrome", "msedge", "firefox"])
    # Deduplicate while preserving order
    seen = set()
    unique_browsers = []
    for b in browsers_to_try:
        if b not in seen:
            seen.add(b)
            unique_browsers.append(b)

    # --- Strategy 1: pywinauto Application().start() with browser exe ---
    if PYWINAUTO_AVAILABLE and Application is not None:
        for browser_name in unique_browsers:
            exe_paths = _BROWSER_EXE_PATHS.get(browser_name, [])
            # Also check registry
            reg_path = _resolve_via_app_paths_registry(browser_name)
            if reg_path:
                exe_paths = [reg_path] + exe_paths

            for exe_path in exe_paths:
                if not os.path.exists(exe_path):
                    continue
                try:
                    print(f"[URL OPEN]: Trying pywinauto start '{exe_path}' with URL '{url}'")
                    app = Application(backend="uia").start(f'"{exe_path}" "{url}"', timeout=15)
                    time.sleep(2.0)  # Give the browser time to create the window
                    # Find the browser window
                    browser_window = None
                    for w in Desktop(backend="uia").windows():
                        try:
                            title = (w.window_text() or "").lower()
                            if browser_name in title or "chrome" in title or "edge" in title or "firefox" in title:
                                browser_window = w
                                break
                        except Exception:
                            continue
                    if browser_window:
                        try:
                            browser_window.set_focus()
                        except Exception:
                            pass
                        result["success"] = True
                        result["window"] = browser_window
                        result["method"] = "pywinauto_start"
                        result["browser"] = browser_name
                        print(f"[URL OPEN]: Opened '{url}' in {browser_name} via pywinauto")
                        return result
                    else:
                        # Browser started but we can't find the window — still success
                        result["success"] = True
                        result["method"] = "pywinauto_start_no_window"
                        result["browser"] = browser_name
                        print(f"[URL OPEN]: Started {browser_name} but couldn't locate window handle")
                        return result
                except Exception as e:
                    print(f"[URL OPEN]: pywinauto start failed for {browser_name}: {e}")

    # Ensure YouTube autoplay
    if ("youtube.com" in url or "youtu.be" in url) and "autoplay=1" not in url:
        delimiter = "&" if "?" in url else "?"
        url = f"{url}{delimiter}autoplay=1"

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    # --- Strategy 2: subprocess start + window detection ---
    for browser_name in unique_browsers:
        exe_paths = _BROWSER_EXE_PATHS.get(browser_name, [])
        reg_path = _resolve_via_app_paths_registry(browser_name)
        if reg_path:
            exe_paths = [reg_path] + exe_paths

        for exe_path in exe_paths:
            if not os.path.exists(exe_path):
                continue
            try:
                subprocess.Popen([exe_path, url], creationflags=creation_flags)
                time.sleep(2.0)
                result["success"] = True
                result["method"] = "subprocess_direct"
                result["browser"] = browser_name
                # Try to find the window
                if PYWINAUTO_AVAILABLE:
                    for w in Desktop(backend="uia").windows():
                        try:
                            title = (w.window_text() or "").lower()
                            if any(b in title for b in [browser_name, "chrome", "edge", "firefox"]):
                                result["window"] = w
                                break
                        except Exception:
                            continue
                print(f"[URL OPEN]: Opened '{url}' in {browser_name} via subprocess")
                return result
            except Exception as e:
                print(f"[URL OPEN]: subprocess failed for {browser_name}: {e}")

    # --- Strategy 3: webbrowser.open() fallback ---
    import webbrowser
    try:
        webbrowser.open(url)
        result["success"] = True
        result["method"] = "webbrowser_open"
        result["browser"] = "default"
        print(f"[URL OPEN]: Opened '{url}' via webbrowser.open()")
        return result
    except Exception as e:
        print(f"[URL OPEN]: webbrowser.open() failed: {e}")

    # --- Strategy 4: subprocess start command ---
    try:
        subprocess.Popen(f'start "" "{url}"', shell=True, creationflags=creation_flags)
        result["success"] = True
        result["method"] = "subprocess_start_cmd"
        result["browser"] = "default"
        print(f"[URL OPEN]: Opened '{url}' via subprocess start command")
        return result
    except Exception as e:
        print(f"[URL OPEN]: subprocess start also failed: {e}")

    result["message"] = f"All strategies failed to open URL: {url}"
    return result


def focus_window(title_substring: str) -> bool:
    """Find and focus window containing title substring. Routes through focus_window_robust for maximum reliability."""
    return focus_window_robust(title_substring, max_retries=3)


def get_active_window_title() -> str:
    """Get title of active focused window."""
    try:
        win = gw.getActiveWindow()
        return win.title if win else ""
    except Exception:
        return ""

def click_mouse(x: int, y: int, click_type: str = "left") -> None:
    """Click mouse at (x, y) coordinates. Prefers pydirectinput (SendInput scan codes), falls back to pyautogui."""
    if pydirectinput:
        try:
            pydirectinput.click(x, y, button=click_type.lower())
            return
        except Exception as e:
            print(f"[INPUT]: pydirectinput click failed, falling back to pyautogui: {e}")
    pyautogui.click(x, y, button=click_type.lower())

def double_click_mouse(x: int, y: int) -> None:
    """Double click mouse at (x, y) coordinates."""
    if pydirectinput:
        try:
            pydirectinput.doubleClick(x, y)
            return
        except Exception as e:
            print(f"[INPUT]: pydirectinput doubleClick failed, falling back to pyautogui: {e}")
    pyautogui.doubleClick(x, y)

def right_click_mouse(x: int, y: int) -> None:
    """Right click mouse at (x, y) coordinates."""
    if pydirectinput:
        try:
            pydirectinput.click(x, y, button="right")
            return
        except Exception as e:
            print(f"[INPUT]: pydirectinput right-click failed, falling back to pyautogui: {e}")
    pyautogui.click(x, y, button="right")

def hover_mouse(x: int, y: int) -> None:
    """Hover mouse over (x, y) coordinates."""
    if pydirectinput:
        try:
            pydirectinput.moveTo(x, y)
            return
        except Exception as e:
            print(f"[INPUT]: pydirectinput moveTo failed, falling back to pyautogui: {e}")
    pyautogui.moveTo(x, y)

def drag_and_drop(x1: int, y1: int, x2: int, y2: int) -> None:
    """Drag from (x1, y1) and drop at (x2, y2)."""
    if pydirectinput:
        try:
            pydirectinput.moveTo(x1, y1)
            pydirectinput.dragTo(x2, y2, button="left")
            return
        except Exception as e:
            print(f"[INPUT]: pydirectinput drag failed, falling back to pyautogui: {e}")
    pyautogui.moveTo(x1, y1)
    pyautogui.dragTo(x2, y2, button="left")

def type_keyboard(text: str, target_title: str = None) -> bool:
    """
    Type text into the focused element. If target_title is given, this
    routes through UI Automation first (type_keyboard_into_window), which
    targets that specific window regardless of what the OS currently reports
    as foreground — the most reliable path, and immune to most UIPI issues.
    Falls back to pydirectinput, then plain pyautogui, if UIA isn't available
    or the window can't be found by title.
    Returns True/False indicating which path actually ran (not a guarantee of
    on-screen success — callers should still verify).
    """
    if target_title and type_keyboard_into_window(target_title, text):
        return True
    if pydirectinput:
        try:
            pydirectinput.write(text)
            return True
        except Exception as e:
            print(f"[INPUT]: pydirectinput write failed, falling back to pyautogui: {e}")
    pyautogui.write(text, interval=0.02)
    return True

def press_key(key: str) -> None:
    """Press a single key (e.g. 'enter', 'tab', 'backspace')."""
    normalized = key.lower().replace(" ", "")
    if pydirectinput:
        try:
            pydirectinput.press(normalized)
            return
        except Exception as e:
            print(f"[INPUT]: pydirectinput press failed, falling back to pyautogui: {e}")
    pyautogui.press(normalized)

def press_hotkey(keys: list) -> None:
    """Press hotkey combination (e.g. ['ctrl', 's'])."""
    normalized = [k.lower().strip() for k in keys]
    if pydirectinput:
        try:
            pydirectinput.hotkey(*normalized)
            return
        except Exception as e:
            print(f"[INPUT]: pydirectinput hotkey failed, falling back to pyautogui: {e}")
    pyautogui.hotkey(*normalized)

def clipboard_copy(text: str) -> None:
    """Copy text to clipboard."""
    pyperclip.copy(text)

def clipboard_paste() -> str:
    """Get clipboard text."""
    return pyperclip.paste()

def volume_up() -> None:
    """Increase system volume."""
    pyautogui.press("volumeup")

def volume_down() -> None:
    """Decrease system volume."""
    pyautogui.press("volumedown")

def mute_volume() -> None:
    """Mute system volume."""
    pyautogui.press("volumemute")

def get_running_apps() -> list:
    """Get list of active visible windows."""
    try:
        return [win.title for win in gw.getAllWindows() if win.title and win.visible]
    except Exception:
        return []

def get_desktop_status() -> dict:
    """Return current desktop environment status. Each sub-check is isolated so one failure (e.g. an unreadable clipboard) can't take down the whole status report."""
    try:
        active_window = get_active_window_title()
    except Exception:
        active_window = ""
    try:
        mouse_pos = list(pyautogui.position())
    except Exception:
        mouse_pos = [0, 0]
    try:
        clip = clipboard_paste()
        clip_preview = clip[:200] if clip else ""
    except Exception:
        clip_preview = ""
    try:
        running = get_running_apps()[:10]
    except Exception:
        running = []
    return {
        "active_window": active_window,
        "mouse_position": mouse_pos,
        "clipboard_content": clip_preview,
        "running_apps": running,
        "pywinauto_available": PYWINAUTO_AVAILABLE,
        "pydirectinput_available": pydirectinput is not None,
        "win32_available": WIN32_AVAILABLE
    }


def diagnose() -> dict:
    """Return diagnostic info about the current Python environment and module availability.
    Useful for troubleshooting when desktop automation isn't working."""
    return {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "pydirectinput_available": pydirectinput is not None,
        "pywinauto_available": PYWINAUTO_AVAILABLE,
        "pyautogui_available": True,  # if we got this far, pyautogui is imported
        "pygetwindow_available": True,
        "win32gui_available": WIN32_AVAILABLE,
        "is_windows_store_python": "WindowsApps" in sys.executable,
        "warning": (
            "You are running the Windows Store Python stub which has limited "
            "subprocess support and may not have your packages installed. "
            "Use the real Python 3.10 instead."
        ) if "WindowsApps" in sys.executable else ""
    }


def open_url_in_system_browser(url: str) -> dict:
    """Opens a URL directly in the user's default system browser (Chrome, Edge, etc.) on Windows desktop.
    Brings the browser window to the foreground and keeps it open continuously as an independent detached process."""
    if ("youtube.com" in url or "youtu.be" in url) and "autoplay=1" not in url:
        delimiter = "&" if "?" in url else "?"
        url = f"{url}{delimiter}autoplay=1"

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    try:
        if sys.platform == "win32":
            subprocess.Popen(f'start "" "{url}"', shell=True, creationflags=creation_flags)
        else:
            webbrowser.open(url, new=2)
        time.sleep(0.5)
        return {
            "success": True,
            "method": "detached_process_start",
            "browser": "system_default"
        }
    except Exception as e:
        try:
            webbrowser.open(url, new=2)
            return {
                "success": True,
                "method": "webbrowser.open",
                "browser": "system_default"
            }
        except Exception as e2:
            return {"success": False, "error": str(e2)}
