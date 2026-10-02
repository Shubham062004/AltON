import os
import importlib
from typing import Dict, Any, List, Callable
from plugins.base_plugin import BasePlugin

class PluginManager:
    """
    Central Plugin Manager & Tool Registry for ULTRON OS.
    Auto-discovers and registers plugins located in plugins/ directory.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PluginManager, cls).__new__(cls, *args, **kwargs)
            cls._instance.plugins: Dict[str, BasePlugin] = {}
            cls._instance.tools: Dict[str, Callable] = {}
        return cls._instance

    def register_plugin(self, plugin: BasePlugin):
        self.plugins[plugin.name] = plugin
        for tool_name, func in plugin.get_tools().items():
            full_name = f"{plugin.name}.{tool_name}"
            self.tools[full_name] = func
            print(f"[PLUGIN MANAGER]: Registered tool '{full_name}' (Permissions: {plugin.permissions})")

    def discover_plugins(self, plugins_dir: str = None):
        if not plugins_dir:
            plugins_dir = os.path.dirname(os.path.abspath(__file__))

        for entry in os.listdir(plugins_dir):
            sub_path = os.path.join(plugins_dir, entry)
            if os.path.isdir(sub_path) and not entry.startswith("_"):
                plugin_module_name = f"plugins.{entry}.plugin"
                try:
                    module = importlib.import_module(plugin_module_name)
                    if hasattr(module, "get_plugin_instance"):
                        plugin_inst = module.get_plugin_instance()
                        if isinstance(plugin_inst, BasePlugin):
                            self.register_plugin(plugin_inst)
                except Exception as e:
                    print(f"[PLUGIN MANAGER]: Error loading plugin '{entry}': {e}")

    def execute_tool(self, tool_name: str, **kwargs) -> Any:
        if tool_name not in self.tools:
            return {"status": "error", "message": f"Tool '{tool_name}' not registered in Plugin Registry."}
        try:
            return self.tools[tool_name](**kwargs)
        except Exception as e:
            return {"status": "error", "message": f"Execution of tool '{tool_name}' failed: {str(e)}"}

    def list_plugins(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": p.name,
                "description": p.description,
                "permissions": p.permissions,
                "tools": list(p.get_tools().keys())
            }
            for p in self.plugins.values()
        ]
