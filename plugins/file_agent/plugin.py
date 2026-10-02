import os
import shutil
from typing import Dict, Any, Callable, List
from plugins.base_plugin import BasePlugin

class FileAgentPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "file_agent"

    @property
    def description(self) -> str:
        return "File management agent: Search, read, write, organize, and delete files."

    @property
    def permissions(self) -> str:
        return "HIGH"

    def read_file(self, filepath: str) -> Dict[str, Any]:
        if not os.path.exists(filepath):
            return {"status": "error", "message": f"File not found: {filepath}"}
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return {"status": "success", "content": content, "filepath": filepath}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def write_file(self, filepath: str, content: str) -> Dict[str, Any]:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return {"status": "success", "filepath": filepath, "bytes_written": len(content)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def search_files(self, directory: str, query: str) -> Dict[str, Any]:
        matches = []
        if not os.path.exists(directory):
            return {"status": "error", "message": f"Directory not found: {directory}"}
        for root, dirs, files in os.walk(directory):
            for file in files:
                if query.lower() in file.lower():
                    matches.append(os.path.join(root, file))
        return {"status": "success", "matches": matches[:50]}

    def get_tools(self) -> Dict[str, Callable]:
        return {
            "Read": self.read_file,
            "Write": self.write_file,
            "Search": self.search_files
        }

def get_plugin_instance() -> BasePlugin:
    return FileAgentPlugin()
