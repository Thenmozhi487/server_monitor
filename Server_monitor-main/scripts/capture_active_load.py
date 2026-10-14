"""Orchestrates running a load test and automatically capturing the live in-stress screenshot."""

import argparse
import os
import shutil
import subprocess
import sys
import time

sys.path.append(os.path.dirname(__file__))
from capture_screenshot import capture_dashboard_screenshot


def run_and_capture(mode: str = "cpu"):
    if mode == "cpu":
        print("Launching CPU stress load...")
        load_proc = subprocess.Popen(
            [sys.executable, "scripts/load_test.py", "--mode", "cpu", "--duration", "25", "--before", "0", "--after", "10"]
        )

        # Wait 16 seconds for collector to register 3 consecutive breach samples (>70%)
        print("Waiting 16s for 3-sample debounce threshold breach...")
        time.sleep(16)

        print("Capturing live CPU load and active alert screenshots...")
        cpu_png = capture_dashboard_screenshot("cpu-load", delay_seconds=4)
        active_png = os.path.join(os.path.dirname(cpu_png), "alert-active.png")
        shutil.copyfile(cpu_png, active_png)
        print(f"Created {active_png}")

        load_proc.wait()
        print("CPU stress load completed.")

    elif mode == "memory":
        print("Launching Memory stress load (capped safely at 80% RAM)...")
        load_proc = subprocess.Popen(
            [sys.executable, "scripts/load_test.py", "--mode", "memory", "--duration", "20", "--before", "0", "--after", "5"]
        )

        print("Waiting 12s for memory allocation and telemetry sampling...")
        time.sleep(12)

        print("Capturing memory load screenshot...")
        capture_dashboard_screenshot("memory-load", delay_seconds=4)

        load_proc.wait()
        print("Memory stress load completed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["cpu", "memory"], default="cpu")
    args = parser.parse_args()
    run_and_capture(args.mode)


if __name__ == "__main__":
    main()
