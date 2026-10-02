import os
import sys
import time
import threading
import uvicorn
import webview

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.main import app as fastapi_app

class DesktopAPI:
    def __init__(self, window_ref):
        self.window = window_ref[0] if isinstance(window_ref, list) else window_ref
        self._is_always_on_top = False

    def set_window(self, window):
        self.window = window

    def minimize_window(self):
        if self.window:
            self.window.minimize()
            return {"status": "success", "action": "minimize"}
        return {"status": "error", "message": "No window handle"}

    def maximize_window(self):
        if self.window:
            self.window.toggle_fullscreen()
            return {"status": "success", "action": "toggle_fullscreen"}
        return {"status": "error", "message": "No window handle"}

    def close_window(self):
        if self.window:
            self.window.destroy()
            return {"status": "success", "action": "close"}
        return {"status": "error", "message": "No window handle"}

    def toggle_always_on_top(self):
        if self.window:
            self._is_always_on_top = not self._is_always_on_top
            self.window.on_top = self._is_always_on_top
            return {"status": "success", "always_on_top": self._is_always_on_top}
        return {"status": "error", "message": "No window handle"}

    def get_system_info(self):
        import platform
        import psutil
        return {
            "os": platform.system(),
            "os_release": platform.release(),
            "cpu_usage": psutil.cpu_percent(interval=None),
            "memory_usage": psutil.virtual_memory().percent
        }


def run_backend():
    """Runs Uvicorn server in background thread."""
    uvicorn.run(fastapi_app, host="127.0.0.1", port=8080, log_level="warning")


def setup_tray(window):
    """Optional system tray icon integration using pystray."""
    try:
        from PIL import Image, ImageDraw
        import pystray

        def create_icon():
            # Create a sci-fi blue/cyan circle icon for system tray
            image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            dc = ImageDraw.Draw(image)
            dc.ellipse((4, 4, 60, 60), fill=(8, 12, 20, 255), outline=(0, 240, 255, 255), width=3)
            dc.ellipse((20, 20, 44, 44), fill=(0, 180, 255, 255))
            return image

        def on_show(icon, item):
            window.show()
            window.restore()

        def on_hide(icon, item):
            window.hide()

        def on_exit(icon, item):
            icon.stop()
            window.destroy()

        menu = pystray.Menu(
            pystray.MenuItem('Show Ultron Agent', on_show, default=True),
            pystray.MenuItem('Hide to Tray', on_hide),
            pystray.MenuItem('Exit Ultron', on_exit)
        )

        icon = pystray.Icon("UltronOS", create_icon(), "ULTRON OS Desktop Agent", menu)
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception as e:
        print(f"[DESKTOP APP]: System tray initialization skipped: {e}")


def main():
    print("============================================================")
    print("  ULTRON OS - Starting Native Desktop Assistant Agent")
    print("============================================================")

    # 1. Start FastAPI backend thread
    server_thread = threading.Thread(target=run_backend, daemon=True)
    server_thread.start()

    # Wait briefly for FastAPI server to initialize
    time.sleep(1.5)

    # 2. Create Desktop Window API
    window_container = [None]
    api = DesktopAPI(window_container)

    # 3. Launch PyWebView Native Window
    window = webview.create_window(
        title="ULTRON OS - Personal Desktop Agent",
        url="http://127.0.0.1:8080",
        width=1280,
        height=820,
        min_size=(900, 600),
        resizable=True,
        frameless=True,
        easy_drag=True,
        background_color="#080c14",
        js_api=api
    )
    window_container[0] = window

    # 4. Start System Tray Icon
    window.events.shown += lambda: setup_tray(window)

    # 5. Start PyWebView GUI loop
    webview.start(debug=False)


if __name__ == "__main__":
    main()
