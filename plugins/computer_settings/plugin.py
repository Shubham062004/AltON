import os
import sys
import subprocess
from typing import Dict, Any, Callable
from plugins.base_plugin import BasePlugin

class ComputerSettingsPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "computer_settings"

    @property
    def description(self) -> str:
        return "OS Computer Settings: Volume adjustment, brightness, WiFi, and system power controls."

    @property
    def permissions(self) -> str:
        return "HIGH"

    def adjust_volume(self, action: str = "mute") -> Dict[str, Any]:
        """action: 'up', 'down', 'mute'"""
        action = action.lower().strip()
        try:
            if sys.platform == "win32":
                import pyautogui
                if action == "up":
                    pyautogui.press("volumeup")
                elif action == "down":
                    pyautogui.press("volumedown")
                else:
                    pyautogui.press("volumemute")
                return {"status": "success", "action": f"volume_{action}"}
            else:
                return {"status": "error", "message": "Volume control unsupported on non-Windows OS"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def adjust_brightness(self, level: int = 50) -> Dict[str, Any]:
        try:
            if sys.platform == "win32":
                import screen_brightness_control as sbc
                sbc.set_brightness(level)
                return {"status": "success", "brightness_level": level}
            return {"status": "error", "message": "Brightness control requires screen_brightness_control module"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "Volume": self.adjust_volume,
            "Brightness": self.adjust_brightness
        }

def get_plugin_instance() -> BasePlugin:
    return ComputerSettingsPlugin()
