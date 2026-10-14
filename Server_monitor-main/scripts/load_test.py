"""Controlled system load test runner (CPU and Memory).

Features:
- Pure-Python multi-process stressor (cross-platform Linux & Windows fallback).
- Configurable BEFORE (baseline), DURING (stress), and AFTER (recovery) phases.
- Modes: cpu, memory, both.
- Memory stressor strictly capped at 80% of total system RAM to prevent OOM killer.
- Time-limited with self-terminating watchdog timers.
- Timestamped console output matching dashboard time-series points.
"""

from __future__ import annotations

import argparse
import datetime
import multiprocessing as mp
import os
import sys
import time
from typing import List

import psutil


def log_event(phase: str, message: str) -> None:
    """Print structured timestamped log event."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    epoch = time.time()
    print(f"[{ts}] [{epoch:.1f}] [{phase.upper():<7}] {message}", flush=True)


def _cpu_worker(stop_event: mp.Event) -> None:
    """Intensive CPU worker computing hashes in batched loops."""
    import hashlib
    data = b"antigravity_benchmark_stress_block_0123456789" * 200
    while not stop_event.is_set():
        for _ in range(10000):
            hashlib.sha256(data).digest()


def _memory_worker(stop_event: mp.Event, target_bytes: int) -> None:
    """Allocates memory up to target_bytes and holds it until stop_event is set."""
    chunk_size = 32 * 1024 * 1024  # 32 MB chunks
    chunks: List[bytearray] = []
    allocated = 0

    try:
        while allocated < target_bytes and not stop_event.is_set():
            alloc_now = min(chunk_size, target_bytes - allocated)
            chunks.append(bytearray(alloc_now))
            allocated += alloc_now
            time.sleep(0.01)

        # Hold memory until signaled to release
        while not stop_event.is_set():
            time.sleep(0.1)
    finally:
        chunks.clear()


def run_cpu_load(duration_seconds: int) -> None:
    """Run CPU load across all available CPU cores."""
    num_cores = mp.cpu_count()
    num_workers = max(4, num_cores * 2)
    stop_event = mp.Event()
    workers: List[mp.Process] = []

    log_event("DURING", f"Spawning {num_workers} CPU stress workers across {num_cores} cores for {duration_seconds}s...")

    for i in range(num_workers):
        p = mp.Process(target=_cpu_worker, args=(stop_event,), daemon=True)
        p.start()
        workers.append(p)

    start_time = time.time()
    try:
        while time.time() - start_time < duration_seconds:
            cpu = psutil.cpu_percent(interval=1.0)
            log_event("DURING", f"Active CPU stress: current host CPU = {cpu:.1f}%")
    finally:
        log_event("DURING", "Signaling CPU workers to terminate...")
        stop_event.set()
        for p in workers:
            p.join(timeout=2.0)
            if p.is_alive():
                p.terminate()
        log_event("DURING", "All CPU stress workers stopped.")


def run_memory_load(duration_seconds: int) -> None:
    """Run memory load capped safely at 80% of total system RAM."""
    vm = psutil.virtual_memory()
    total_bytes = vm.total
    used_bytes = vm.used
    max_safe_bytes = int(total_bytes * 0.80)  # Absolute 80% RAM cap

    # Target allocation: bring total usage up to 80%
    needed_bytes = max(0, max_safe_bytes - used_bytes)
    # Cap allocation to at most available - 500MB safety margin
    safe_alloc = min(needed_bytes, max(0, vm.available - (500 * 1024 * 1024)))

    mb_to_alloc = safe_alloc / (1024 * 1024)
    log_event(
        "DURING",
        f"RAM total: {total_bytes / (1024**3):.1f} GB, currently used: {used_bytes / (1024**3):.1f} GB. "
        f"Targeting safe allocation of {mb_to_alloc:.0f} MB (capped <= 80% RAM)."
    )

    if safe_alloc <= 0:
        log_event("DURING", "Current RAM usage already exceeds 80%. Skipping allocation for safety.")
        time.sleep(duration_seconds)
        return

    stop_event = mp.Event()
    p = mp.Process(target=_memory_worker, args=(stop_event, safe_alloc), daemon=True)
    p.start()

    start_time = time.time()
    try:
        while time.time() - start_time < duration_seconds:
            mem = psutil.virtual_memory().percent
            log_event("DURING", f"Active Memory stress: current host RAM = {mem:.1f}%")
            time.sleep(2.0)
    finally:
        log_event("DURING", "Releasing allocated memory chunks...")
        stop_event.set()
        p.join(timeout=3.0)
        if p.is_alive():
            p.terminate()
        log_event("DURING", "Memory released.")


def run_load_sequence(
    mode: str,
    duration: int,
    before: int,
    after: int,
) -> None:
    """Execute complete Before -> During -> After sequence."""
    log_event("START", f"Starting load test suite [mode={mode}, before={before}s, during={duration}s, after={after}s]")

    # 1. BEFORE PHASE (Baseline Idle)
    log_event("BEFORE", f"Entering baseline idle phase for {before}s...")
    start_before = time.time()
    while time.time() - start_before < before:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        log_event("BEFORE", f"Baseline snapshot: CPU = {cpu:.1f}%, RAM = {mem:.1f}%")
        time.sleep(max(1.0, before / 3.0))

    # 2. DURING PHASE (Stress Load)
    log_event("DURING", f"Commencing stress phase [mode={mode}, duration={duration}s]...")
    if mode == "cpu":
        run_cpu_load(duration)
    elif mode == "memory":
        run_memory_load(duration)
    elif mode == "both":
        # Run both in separate processes
        stop_event = mp.Event()
        p_cpu = mp.Process(target=_cpu_worker, args=(stop_event,), daemon=True)
        # Allocate moderate safe memory chunk
        vm = psutil.virtual_memory()
        safe_mem = min(int(vm.total * 0.75) - vm.used, vm.available - (500 * 1024 * 1024))
        p_mem = mp.Process(target=_memory_worker, args=(stop_event, max(0, safe_mem)), daemon=True)

        p_cpu.start()
        p_mem.start()
        t0 = time.time()
        try:
            while time.time() - t0 < duration:
                log_event(
                    "DURING",
                    f"Combined stress: CPU = {psutil.cpu_percent(interval=1.0):.1f}%, RAM = {psutil.virtual_memory().percent:.1f}%"
                )
        finally:
            stop_event.set()
            p_cpu.join(timeout=2.0)
            p_mem.join(timeout=2.0)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    # 3. AFTER PHASE (Recovery Cooldown)
    log_event("AFTER", f"Entering post-load recovery phase for {after}s...")
    start_after = time.time()
    while time.time() - start_after < after:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        log_event("AFTER", f"Recovery snapshot: CPU = {cpu:.1f}%, RAM = {mem:.1f}%")
        time.sleep(max(1.0, after / 3.0))

    log_event("COMPLETE", "Load test sequence finished successfully.")


def main() -> None:
    mp.freeze_support()
    parser = argparse.ArgumentParser(description="Real-Time Server Health Dashboard Load Test")
    parser.add_argument(
        "--mode",
        choices=["cpu", "memory", "both"],
        default="cpu",
        help="Stress mode (default: cpu)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=25,
        help="Duration of stress phase in seconds (default: 25)",
    )
    parser.add_argument(
        "--before",
        type=int,
        default=10,
        help="Duration of baseline phase before load in seconds (default: 10)",
    )
    parser.add_argument(
        "--after",
        type=int,
        default=15,
        help="Duration of recovery phase after load in seconds (default: 15)",
    )

    args = parser.parse_args()
    run_load_sequence(args.mode, args.duration, args.before, args.after)


if __name__ == "__main__":
    main()
