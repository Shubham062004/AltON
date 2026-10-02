from abc import ABC, abstractmethod
from typing import Dict, Any, List, Callable

class BasePlugin(ABC):
    """
    Abstract Base Class for all ULTRON OS Plugins.
    Every plugin exposes:
      - name
      - description
      - permissions (SAFE, MEDIUM, HIGH, CRITICAL)
      - tools (dictionary of callable functions)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def permissions(self) -> str:
        pass

    @abstractmethod
    def get_tools(self) -> Dict[str, Callable]:
        """Returns map of tool_name -> callable execution function."""
        pass
