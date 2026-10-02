import os
import sys
import subprocess
from typing import Dict, Any, Callable
from plugins.base_plugin import BasePlugin

class GameUpdaterPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "game_updater"

    @property
    def description(self) -> str:
        return "Game Updater Plugin: Checks and triggers Steam & Epic Games launcher updates."

    @property
    def permissions(self) -> str:
        return "HIGH"

    def check_steam_status(self) -> Dict[str, Any]:
        try:
            if sys.platform == "win32":
                import psutil
                for proc in psutil.process_iter(['name']):
                    if proc.info['name'] and 'steam' in proc.info['name'].lower():
                        return {"status": "success", "steam_running": True, "process": proc.info['name']}
                return {"status": "info", "steam_running": False, "message": "Steam is not currently running."}
            return {"status": "error", "message": "Unsupported platform."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def launch_game_client(self, client_name: str = "steam") -> Dict[str, Any]:
        client_name = client_name.lower().strip()
        try:
            if client_name == "steam":
                os.system("start steam://open/main")
                return {"status": "success", "message": "Triggered Steam client launch/update."}
            elif client_name in ("epic", "epicgames"):
                os.system("start com.epicgames.launcher://")
                return {"status": "success", "message": "Triggered Epic Games client launch/update."}
            return {"status": "error", "message": f"Unknown game client: {client_name}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "CheckSteam": self.check_steam_status,
            "LaunchClient": self.launch_game_client
        }

def get_plugin_instance() -> BasePlugin:
    return GameUpdaterPlugin()
