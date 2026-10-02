import subprocess
from typing import Dict, Any, Callable
from plugins.base_plugin import BasePlugin

class TerminalPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "terminal"

    @property
    def description(self) -> str:
        return "Executes system terminal & PowerShell commands securely."

    @property
    def permissions(self) -> str:
        return "CRITICAL"

    def execute_command(self, command: str) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=15
            )
            return {
                "status": "success" if res.returncode == 0 else "error",
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip(),
                "returncode": res.returncode
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "Run": self.execute_command
        }

def get_plugin_instance() -> BasePlugin:
    return TerminalPlugin()
