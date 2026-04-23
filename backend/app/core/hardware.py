"""硬件检测与优化模块

启动时识别操作系统、CPU 架构、Apple Silicon 等信息，
自动设定线程池大小和 GPU 加速策略。
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class HardwareProfile:
    os_name: str = ""
    os_version: str = ""
    arch: str = ""  # x86_64 / arm64
    cpu_brand: str = ""
    cpu_cores: int = 1
    cpu_threads: int = 1
    is_apple_silicon: bool = False
    apple_chip: str = ""  # M1 / M2 / M3 / Max / Ultra / etc.
    memory_gb: float = 0.0
    gpu_name: str = ""
    gpu_memory_gb: float = 0.0
    gpu_cores: int = 0
    mps_available: bool = False
    cuda_available: bool = False
    recommended_workers: int = 4
    optimization_notes: list[str] = field(default_factory=list)


_profile: HardwareProfile | None = None
_thread_pool: ThreadPoolExecutor | None = None
_runtime_tuning: dict[str, object] = {}
_profile_lock = threading.Lock()


def detect_hardware() -> HardwareProfile:
    """检测本机硬件并返回 HardwareProfile，结果缓存。"""
    global _profile
    if _profile is not None:
        return _profile

    with _profile_lock:
        if _profile is not None:
            return _profile

        p = HardwareProfile()
        p.os_name = platform.system()
        p.os_version = platform.mac_ver()[0] if p.os_name == "Darwin" else platform.version()
        p.arch = platform.machine()
        p.cpu_cores = os.cpu_count() or 1

        # ── CPU 品牌 & Apple Silicon ──
        if p.os_name == "Darwin":
            try:
                brand = subprocess.check_output(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    timeout=3,
                    stderr=subprocess.DEVNULL,
                ).decode().strip()
                p.cpu_brand = brand
            except Exception:
                p.cpu_brand = platform.processor()

            # Apple Silicon 检测
            if p.arch == "arm64":
                p.is_apple_silicon = True
                # 尝试获取芯片型号
                try:
                    chip = subprocess.check_output(
                        ["sysctl", "-n", "hw.chip"],
                        timeout=3,
                        stderr=subprocess.DEVNULL,
                    ).decode().strip()
                    p.apple_chip = chip
                except Exception:
                    # 从品牌字符串提取
                    if "M1" in p.cpu_brand:
                        p.apple_chip = "M1 Max" if "Max" in p.cpu_brand else "M1 Ultra" if "Ultra" in p.cpu_brand else "M1 Pro" if "Pro" in p.cpu_brand else "M1"
                    elif "M2" in p.cpu_brand:
                        p.apple_chip = "M2 Max" if "Max" in p.cpu_brand else "M2 Ultra" if "Ultra" in p.cpu_brand else "M2 Pro" if "Pro" in p.cpu_brand else "M2"
                    elif "M3" in p.cpu_brand:
                        p.apple_chip = "M3 Max" if "Max" in p.cpu_brand else "M3 Ultra" if "Ultra" in p.cpu_brand else "M3 Pro" if "Pro" in p.cpu_brand else "M3"
                    elif "M4" in p.cpu_brand:
                        p.apple_chip = "M4 Max" if "Max" in p.cpu_brand else "M4 Ultra" if "Ultra" in p.cpu_brand else "M4 Pro" if "Pro" in p.cpu_brand else "M4"
                    else:
                        p.apple_chip = "Apple Silicon"

            # 物理内存
            try:
                mem_bytes = int(subprocess.check_output(
                    ["sysctl", "-n", "hw.memsize"],
                    timeout=3,
                    stderr=subprocess.DEVNULL,
                ).decode().strip())
                p.memory_gb = round(mem_bytes / (1024**3), 1)
            except Exception:
                pass

            # GPU 核心数 (Apple Silicon)
            try:
                import plistlib
                sp_out = subprocess.check_output(
                    ["system_profiler", "SPDisplaysDataType", "-xml"],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                )
                sp_data = plistlib.loads(sp_out)
                for item in sp_data:
                    for display in item.get("_items", []):
                        cores = display.get("sppci_cores")
                        if cores:
                            # Format: "40" or "40-core"
                            core_str = str(cores).replace("-core", "").strip()
                            p.gpu_cores = int(core_str)
            except Exception:
                pass

            # 性能核心数 (Apple)
            try:
                perf = int(subprocess.check_output(
                    ["sysctl", "-n", "hw.perflevel0.logicalcpu"],
                    timeout=3,
                    stderr=subprocess.DEVNULL,
                ).decode().strip())
                p.cpu_threads = perf
            except Exception:
                p.cpu_threads = p.cpu_cores
        else:
            p.cpu_brand = platform.processor()
            p.cpu_threads = p.cpu_cores
            # Linux 内存
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal"):
                            kb = int(line.split()[1])
                            p.memory_gb = round(kb / (1024**2), 1)
                            break
            except Exception:
                pass

        # ── GPU / MPS / CUDA ──
        try:
            import torch
            p.cuda_available = torch.cuda.is_available()
            if p.cuda_available:
                p.gpu_name = torch.cuda.get_device_name(0)
                p.gpu_memory_gb = round(torch.cuda.get_device_properties(0).total_mem / (1024**3), 1)
            p.mps_available = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        except ImportError:
            pass

        # ── 推荐线程池大小 ──
        if p.is_apple_silicon:
            # 高内存系统允许更大线程池
            max_workers = 32 if p.memory_gb >= 64 else 16
            p.recommended_workers = min(p.cpu_threads * 2, max_workers)
        else:
            max_workers = 32 if p.memory_gb >= 64 else 16
            p.recommended_workers = min(p.cpu_cores * 2, max_workers)

        # ── 优化建议 ──
        if p.mps_available:
            p.optimization_notes.append("MPS (Metal) GPU 加速可用，PyTorch 推理优先使用 device='mps'")
        if p.cuda_available:
            p.optimization_notes.append(f"CUDA GPU 可用: {p.gpu_name}, {p.gpu_memory_gb}GB 显存")
        if p.is_apple_silicon:
            p.optimization_notes.append(f"Apple {p.apple_chip} 检测成功，启用 {p.recommended_workers} 线程并行")
        if p.memory_gb >= 32:
            p.optimization_notes.append(f"内存 {p.memory_gb}GB 充裕，可并行加载多模型")

        _profile = p
        return p


def get_thread_pool() -> ThreadPoolExecutor:
    """获取全局线程池（根据硬件检测结果自动设定大小）。"""
    global _thread_pool
    if _thread_pool is None:
        profile = detect_hardware()
        _thread_pool = ThreadPoolExecutor(
            max_workers=profile.recommended_workers,
            thread_name_prefix="rt-worker",
        )
    return _thread_pool


def get_runtime_tuning() -> dict[str, object]:
    """返回当前运行时调优参数快照。"""
    if not _runtime_tuning:
        return apply_runtime_tuning()
    return dict(_runtime_tuning)


def apply_runtime_tuning(force_recreate_pool: bool = False) -> dict[str, object]:
    """根据硬件能力应用运行时参数，并返回已应用配置。"""
    global _thread_pool, _runtime_tuning

    profile = detect_hardware()
    workers = max(2, int(profile.recommended_workers))

    # 轻量运行时参数：用于并发和预热节奏控制。
    prefetch_batch = 4 if workers >= 12 else 3 if workers >= 8 else 2
    queue_target = max(2, min(8, workers // 2))
    asr_warmup_interval_ms = 900 if workers >= 10 else 1300
    tts_retry_delay_ms = 120 if workers >= 10 else 180

    # 让底层库可读取到并发建议（新建 worker 进程/线程时生效）。
    os.environ["RT_WORKERS"] = str(workers)
    os.environ["RT_TTS_PREFETCH_BATCH"] = str(prefetch_batch)
    os.environ["RT_ASR_WARMUP_INTERVAL_MS"] = str(asr_warmup_interval_ms)
    os.environ["RT_TTS_RETRY_DELAY_MS"] = str(tts_retry_delay_ms)

    should_recreate = force_recreate_pool or _thread_pool is None
    if _thread_pool is not None and not should_recreate:
        current = getattr(_thread_pool, "_max_workers", workers)
        should_recreate = int(current) != workers

    if should_recreate:
        if _thread_pool is not None:
            _thread_pool.shutdown(wait=False)
        _thread_pool = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="rt-worker",
        )

    _runtime_tuning = {
        "applied_at": datetime.now().isoformat(),
        "workers": workers,
        "prefetch_batch": prefetch_batch,
        "queue_target": queue_target,
        "asr_warmup_interval_ms": asr_warmup_interval_ms,
        "tts_retry_delay_ms": tts_retry_delay_ms,
        "device": get_optimal_device(),
    }
    return dict(_runtime_tuning)


def get_optimal_device() -> str:
    """返回最佳推理设备字符串：'mps' / 'cuda' / 'cpu'。"""
    profile = detect_hardware()
    if profile.mps_available:
        return "mps"
    if profile.cuda_available:
        return "cuda"
    return "cpu"


def log_hardware_summary() -> None:
    """向日志输出硬件检测摘要。"""
    p = detect_hardware()
    logger.info("=" * 60)
    logger.info("硬件检测结果")
    logger.info("-" * 60)
    logger.info(f"  系统: {p.os_name} {p.os_version} ({p.arch})")
    logger.info(f"  CPU: {p.cpu_brand}")
    logger.info(f"  核心数: {p.cpu_cores}  性能线程: {p.cpu_threads}")
    logger.info(f"  内存: {p.memory_gb} GB")
    if p.is_apple_silicon:
        logger.info(f"  芯片: Apple {p.apple_chip}")
    if p.mps_available:
        logger.info("  GPU: MPS (Metal Performance Shaders) ✓" + (f", {p.gpu_cores} cores" if p.gpu_cores else ""))
    elif p.cuda_available:
        logger.info(f"  GPU: {p.gpu_name} ({p.gpu_memory_gb}GB) CUDA ✓")
    else:
        logger.info("  GPU: 无可用加速")
    logger.info(f"  线程池: {p.recommended_workers} workers")
    for note in p.optimization_notes:
        logger.info(f"  → {note}")
    logger.info("=" * 60)
