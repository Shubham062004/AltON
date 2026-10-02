import ctypes
import platform
import time
import psutil
from typing import Dict, Any, Optional

_OS = platform.system()

DEFAULT_THRESHOLDS = {
    "cpu": 90.0,
    "ram": 90.0,
    "temp": 85.0,
    "gpu": 95.0,
}

_COOLDOWN = 300
_CPU_STREAK = 3

_nvml_lib = None
_nvml_ok = None


def _nvml_gpu() -> float:
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            if _OS == "Windows":
                candidates = ("nvml", r"C:\Windows\System32\nvml.dll")
                _load = ctypes.WinDLL
            else:
                candidates = ("libnvidia-ml.so.1", "libnvidia-ml.so", "libnvidia-ml.dylib")
                _load = ctypes.CDLL
            for name in candidates:
                try:
                    lib = _load(name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            _nvml_ok = False
            return -1.0

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        u = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
        _nvml_ok = True
        return float(u.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


def _get_gpu_usage() -> float:
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    except Exception:
        pass
    return _nvml_gpu()


def _get_cpu_temp() -> float:
    try:
        temps = psutil.sensors_temperatures()
        for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz", "cpu-thermal"]:
            if name in temps and temps[name]:
                return temps[name][0].current
    except Exception:
        pass

    if _OS == "Windows":
        try:
            import wmi
            w = wmi.WMI(namespace="root/wmi")
            tz = w.MSAcpi_ThermalZoneTemperature()
            if tz:
                return (tz[0].CurrentTemperature / 10.0) - 273.15
        except Exception:
            pass
    return -1.0


def get_system_status() -> Dict[str, Any]:
    """Snapshot of current system metrics for ULTRON OS."""
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    temp = _get_cpu_temp()
    gpu = _get_gpu_usage()

    boot_time = psutil.boot_time()
    uptime_secs = time.time() - boot_time
    uptime_h = int(uptime_secs // 3600)
    uptime_m = int((uptime_secs % 3600) // 60)

    return {
        "cpu_percent": round(cpu, 1),
        "ram_percent": round(ram.percent, 1),
        "ram_used_gb": round(ram.used / (1024 ** 3), 1),
        "ram_total_gb": round(ram.total / (1024 ** 3), 1),
        "cpu_temp_c": round(temp, 1) if temp > 0 else None,
        "gpu_percent": round(gpu, 1) if gpu >= 0 else None,
        "uptime": f"{uptime_h}h {uptime_m}m",
        "process_count": len(psutil.pids()),
    }


class SystemMonitor:
    """Stateful system monitor — checks metrics against alert thresholds."""

    def __init__(self, thresholds: Optional[dict] = None):
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._last_alert: Dict[str, float] = {}
        self._cpu_streak = 0

    def _can_alert(self, key: str) -> bool:
        return (time.monotonic() - self._last_alert.get(key, 0)) > _COOLDOWN

    def _record(self, key: str):
        self._last_alert[key] = time.monotonic()

    def check(self) -> Optional[str]:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            temp = _get_cpu_temp()
            gpu = _get_gpu_usage()
        except Exception:
            return None

        alerts = []
        if cpu >= self.thresholds["cpu"]:
            self._cpu_streak += 1
            if self._cpu_streak >= _CPU_STREAK and self._can_alert("cpu"):
                alerts.append(f"[SYSTEM_ALERT] CPU usage is high ({cpu:.0f}%).")
                self._record("cpu")
                self._cpu_streak = 0
        else:
            self._cpu_streak = 0

        if ram >= self.thresholds["ram"] and self._can_alert("ram"):
            alerts.append(f"[SYSTEM_ALERT] RAM usage is high ({ram:.0f}%).")
            self._record("ram")

        if temp > 0 and temp >= self.thresholds["temp"] and self._can_alert("temp"):
            alerts.append(f"[SYSTEM_ALERT] CPU temperature is high ({temp:.0f}°C).")
            self._record("temp")

        if gpu >= 0 and gpu >= self.thresholds["gpu"] and self._can_alert("gpu"):
            alerts.append(f"[SYSTEM_ALERT] GPU load is high ({gpu:.0f}%).")
            self._record("gpu")

        return " ".join(alerts) if alerts else None
