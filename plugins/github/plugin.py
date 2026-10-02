import subprocess
from typing import Dict, Any, Callable
from plugins.base_plugin import BasePlugin

class GitHubPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "github"

    @property
    def description(self) -> str:
        return "Git & GitHub Integration: Status, commit, push, branch management."

    @property
    def permissions(self) -> str:
        return "HIGH"

    def git_status(self) -> Dict[str, Any]:
        try:
            res = subprocess.run(["git", "status"], capture_output=True, text=True, timeout=5)
            return {"status": "success", "output": res.stdout.strip()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def git_push(self) -> Dict[str, Any]:
        try:
            res = subprocess.run(["git", "push"], capture_output=True, text=True, timeout=15)
            return {"status": "success" if res.returncode == 0 else "error", "output": res.stdout.strip() or res.stderr.strip()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "Status": self.git_status,
            "Push": self.git_push
        }

def get_plugin_instance() -> BasePlugin:
    return GitHubPlugin()
