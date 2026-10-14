"""Background metric collector for Server Health Dashboard.

Samples CPU, memory, disk utilization and system uptime on a fixed interval,
evaluates threshold alerts, and persists telemetry to SQLite.
Runs decoupled in a background daemon thread.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple

import psutil

from alerts import AlertEvaluator
from db import cleanup_retention, init_db, insert_metric

logger = logging.getLogger(__name__)


def format_uptime(uptime_seconds: float) -> str:
    """Format uptime seconds into 'Xd Yh Zm' format."""
    total_seconds = max(0, int(uptime_seconds))
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"


class CollectorLock:
    """Inter-process and inter-thread file lock to ensure only ONE collector runs.

    Rationale:
    Running multiple collector instances simultaneously (for example, if Gunicorn
    were spawned with multiple worker processes) would lead to:
      1. Duplicate metric samples at identical timestamps.
      2. Race conditions in alert debounce counters.
      3. Unnecessary SQLite write lock contention.
    This lock ensures that only one collector thread executes across processes.
    """

    def __init__(self, lock_file_path: str = "collector.lock") -> None:
        self.lock_file_path = lock_file_path
        self._fd: Optional[int] = None
        self._is_locked = False

    def acquire(self) -> bool:
        """Attempt to acquire non-blocking exclusive file lock."""
        try:
            # Open or create lockfile
            self._fd = os.open(
                self.lock_file_path,
                os.O_CREAT | os.O_RDWR,
            )

            if sys.platform == "win32":
                import msvcrt

                try:
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                    self._is_locked = True
                    return True
                except (OSError, IOError):
                    os.close(self._fd)
                    self._fd = None
                    return False
            else:
                import fcntl

                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._is_locked = True
                    return True
                except (OSError, IOError):
                    os.close(self._fd)
                    self._fd = None
                    return False
        except Exception as e:
            logger.warning("Error acquiring collector lock: %s", e)
            return False

    def release(self) -> None:
        """Release file lock and close file descriptor."""
        if not self._is_locked or self._fd is None:
            return

        try:
            if sys.platform == "win32":
                import msvcrt

                try:
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except Exception:
                    pass

            os.close(self._fd)
        except Exception as e:
            logger.warning("Error releasing collector lock: %s", e)
        finally:
            self._fd = None
            self._is_locked = False


class MetricCollector:
    """Background metric collector engine."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.db_path = config.get("database", {}).get("path", "server_health.db")

        collector_cfg = config.get("collector", {})
        self.sample_interval = float(collector_cfg.get("sample_interval_seconds", 5))
        self.disk_path = collector_cfg.get("disk_path", "/")
        self.retention_days = int(collector_cfg.get("retention_days", 7))
        self.retention_interval = float(
            collector_cfg.get("retention_interval_seconds", 3600)
        )

        # Initialize SQLite tables
        init_db(self.db_path)

        # Initialize Alert Evaluator
        self.alert_evaluator = AlertEvaluator(
            self.db_path, config.get("alerts", {})
        )

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = CollectorLock(
            os.path.join(os.path.dirname(self.db_path) or ".", "collector.lock")
        )
        self.last_retention_check: float = 0.0

        # Prime psutil CPU percent immediately at startup so that the first
        # collected sample is never a meaningless 0.0 reading
        self._prime_psutil()

    def _prime_psutil(self) -> None:
        """Call psutil.cpu_percent(interval=None) to initialize internal baseline counters."""
        try:
            psutil.cpu_percent(interval=None)
            logger.info("psutil.cpu_percent baseline primed successfully.")
        except Exception as e:
            logger.error("Failed to prime psutil.cpu_percent: %s", e)

    def _sample_disk(self) -> float:
        """Sample disk usage percent for configured path with platform fallback."""
        target_path = self.disk_path
        if sys.platform == "win32" and target_path == "/":
            # On Windows, '/' is not a valid drive specification
            target_path = os.path.splitdrive(os.path.abspath("."))[0] + "\\"
            if not target_path or target_path == "\\":
                target_path = os.getenv("SystemDrive", "C:") + "\\"

        try:
            return float(psutil.disk_usage(target_path).percent)
        except Exception:
            try:
                # Use standard library shutil.disk_usage fallback
                usage = shutil.disk_usage(target_path)
                return round((usage.used / usage.total) * 100.0, 2)
            except Exception as e:
                logger.error("Failed to sample disk usage for %s: %s", target_path, e)
                return 0.0

    def collect_single_sample(self) -> Tuple[float, float, float, float]:
        """Collect current CPU, memory, disk utilization, and timestamp."""
        now = time.time()
        # cpu_percent with interval=None calculates delta since last call
        cpu = float(psutil.cpu_percent(interval=None))
        # virtual_memory().percent (not used/total)
        mem = float(psutil.virtual_memory().percent)
        # disk_usage percent
        disk = self._sample_disk()

        return now, cpu, mem, disk

    def run_cycle(self) -> None:
        """Execute one collection, evaluation, and optional retention cycle."""
        now, cpu, mem, disk = self.collect_single_sample()

        # 1. Insert metric into SQLite
        insert_metric(self.db_path, now, cpu, mem, disk)

        # 2. Evaluate alert thresholds
        self.alert_evaluator.evaluate_metric("cpu", cpu, now)
        self.alert_evaluator.evaluate_metric("memory", mem, now)
        self.alert_evaluator.evaluate_metric("disk", disk, now)

        # 3. Retention check
        if (now - self.last_retention_check) >= self.retention_interval:
            self.last_retention_check = now
            deleted = cleanup_retention(self.db_path, self.retention_days)
            if deleted > 0:
                logger.info(
                    "Retention cleanup: purged %d metric rows older than %d days.",
                    deleted,
                    self.retention_days,
                )

    def _worker_loop(self) -> None:
        """Main loop executed by background daemon thread."""
        logger.info(
            "Background collector thread started (interval: %.1fs, disk: %s).",
            self.sample_interval,
            self.disk_path,
        )

        # Run initial retention check on startup
        try:
            self.last_retention_check = time.time()
            deleted = cleanup_retention(self.db_path, self.retention_days)
            if deleted > 0:
                logger.info("Startup retention cleanup: purged %d old rows.", deleted)
        except Exception as e:
            logger.warning("Startup retention cleanup failed: %s", e)

        while not self._stop_event.is_set():
            cycle_start = time.time()
            try:
                self.run_cycle()
            except Exception as e:
                logger.error("Exception in collector cycle: %s", e, exc_info=True)

            # Sleep remaining time of interval, responsive to stop_event
            elapsed = time.time() - cycle_start
            sleep_time = max(0.1, self.sample_interval - elapsed)
            self._stop_event.wait(timeout=sleep_time)

        logger.info("Background collector thread terminated gracefully.")

    def start(self) -> bool:
        """Acquire lock and start background collector thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Collector thread is already running.")
            return True

        if not self._lock.acquire():
            logger.warning(
                "Could not acquire collector lock. Another collector is already running. "
                "Skipping background collector launch in this process."
            )
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="ServerHealthCollectorThread",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop background collector thread and release lock."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None
        self._lock.release()


_GLOBAL_COLLECTOR: Optional[MetricCollector] = None
_GLOBAL_COLLECTOR_LOCK = threading.Lock()


def start_collector(config: Dict[str, Any]) -> Optional[MetricCollector]:
    """Start global singleton collector thread."""
    global _GLOBAL_COLLECTOR
    with _GLOBAL_COLLECTOR_LOCK:
        if _GLOBAL_COLLECTOR is None:
            _GLOBAL_COLLECTOR = MetricCollector(config)
            _GLOBAL_COLLECTOR.start()
        return _GLOBAL_COLLECTOR


def stop_collector() -> None:
    """Stop global singleton collector thread."""
    global _GLOBAL_COLLECTOR
    with _GLOBAL_COLLECTOR_LOCK:
        if _GLOBAL_COLLECTOR is not None:
            _GLOBAL_COLLECTOR.stop()
            _GLOBAL_COLLECTOR = None


def get_uptime_string() -> str:
    """Calculate and return system uptime formatted as 'Xd Yh Zm'."""
    try:
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        return format_uptime(uptime_seconds)
    except Exception as e:
        logger.error("Failed to calculate uptime: %s", e)
        return "0d 0h 0m"
