"""Определение конфигурации компьютера для подбора локальной модели.

Только стандартная библиотека: бэкенд собирается PyInstaller'ом, лишние зависимости ни к чему.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

GB = 1_000_000_000
#: Память считаем в двоичных гигабайтах — так её объём указывают производители («16 ГБ»)
GIB = 1024**3
_CACHE_TTL_S = 600
_cache: tuple[float, dict] | None = None


def _run(cmd: list[str], timeout: float = 3.0) -> str | None:
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True, **kwargs)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _total_ram_bytes() -> int | None:
    if sys.platform == "darwin":
        out = _run(["sysctl", "-n", "hw.memsize"])
        if out and out.isdigit():
            return int(out)
    elif sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
        except Exception:
            pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _cpu_name() -> str | None:
    if sys.platform == "darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or None


def _nvidia_gpus() -> list[tuple[str, float]]:
    exe = shutil.which("nvidia-smi")
    if not exe and sys.platform == "win32":
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(candidate) if candidate.exists() else None
    if not exe:
        return []
    out = _run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], timeout=5.0)
    gpus = []
    for line in (out or "").splitlines():
        try:
            name, mem_mib = [p.strip() for p in line.rsplit(",", 1)]
            gpus.append((name, float(mem_mib) / 1024))
        except ValueError:
            continue
    return gpus


def models_dir() -> Path:
    """Папка, куда Ollama складывает модели (для оценки свободного места)."""
    custom = os.environ.get("OLLAMA_MODELS")
    return Path(custom) if custom else Path.home() / ".ollama" / "models"


def _disk_free_gb(path: Path) -> float | None:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return round(shutil.disk_usage(probe).free / GB, 1)
    except OSError:
        return None


def _detect() -> dict:
    machine = platform.machine().lower()
    ram = _total_ram_bytes()
    cpu = _cpu_name()
    hw: dict = {
        "os": {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux" if sys.platform.startswith("linux") else sys.platform),
        "arch": machine,
        "cpu": cpu,
        "cpu_cores": os.cpu_count(),
        "ram_gb": round(ram / GIB, 1) if ram else None,
        "accelerator": None,
        "gpu": None,
        "vram_gb": None,
    }

    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        # Apple Silicon: общая память для процессора и видеоядра, Ollama считает на Metal
        hw["accelerator"] = "apple"
        hw["gpu"] = cpu or "Apple Silicon"
    else:
        gpus = _nvidia_gpus()
        if gpus:
            hw["accelerator"] = "nvidia"
            hw["gpu"] = gpus[0][0] if len(gpus) == 1 else f"{gpus[0][0]} ×{len(gpus)}"
            hw["vram_gb"] = round(sum(mem for _, mem in gpus), 1)

    return hw


def detect_hardware(force: bool = False) -> dict:
    """Конфигурация компьютера. Кэшируется: железо не меняется, а nvidia-smi отвечает не мгновенно.

    Свободное место на диске пересчитывается при каждом вызове.
    """
    global _cache
    now = time.monotonic()
    if force or _cache is None or now - _cache[0] > _CACHE_TTL_S:
        try:
            _cache = (now, _detect())
        except Exception as e:  # не даём сломать страницу из-за экзотического железа
            logger.warning(f"Hardware detection failed: {e}")
            _cache = (now, {"os": sys.platform, "arch": platform.machine(), "ram_gb": None, "accelerator": None})
    hw = dict(_cache[1])
    hw["disk_free_gb"] = _disk_free_gb(models_dir())
    return hw
