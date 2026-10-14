#!/usr/bin/env bash
# ==============================================================================
# Controlled Load Test Script for Server Health Dashboard
# Uses stress-ng if available on Linux; otherwise delegates to pure-Python fallback.
# ==============================================================================

set -euo pipefail

MODE="cpu"
DURATION=25
BEFORE=10
AFTER=15

print_usage() {
    echo "Usage: $0 [--mode cpu|memory|both] [--duration seconds] [--before seconds] [--after seconds]"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            MODE="$2"
            shift 2
            ;;
        --duration)
            DURATION="$2"
            shift 2
            ;;
        --before)
            BEFORE="$2"
            shift 2
            ;;
        --after)
            AFTER="$2"
            shift 2
            ;;
        -h|--help)
            print_usage
            ;;
        *)
            echo "Unknown option: $1"
            print_usage
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_LOAD_SCRIPT="$SCRIPT_DIR/load_test.py"

log() {
    local phase="$1"
    local msg="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$(date +%s)] [${phase^^}] $msg"
}

# Check if stress-ng is available
if ! command -v stress-ng &> /dev/null; then
    log "INFO" "stress-ng not detected on system. Executing pure-Python multiprocessing stressor..."
    if command -v python3 &> /dev/null; then
        exec python3 "$PYTHON_LOAD_SCRIPT" --mode "$MODE" --duration "$DURATION" --before "$BEFORE" --after "$AFTER"
    else
        exec python "$PYTHON_LOAD_SCRIPT" --mode "$MODE" --duration "$DURATION" --before "$BEFORE" --after "$AFTER"
    fi
fi

# stress-ng is installed
log "START" "Starting stress-ng load test [mode=$MODE, before=${BEFORE}s, during=${DURATION}s, after=${AFTER}s]"

# 1. BEFORE (Baseline)
log "BEFORE" "Baseline idle phase for ${BEFORE}s..."
sleep "$BEFORE"

# 2. DURING (Stress)
log "DURING" "Executing stress-ng for ${DURATION}s..."
NPROC=$(nproc || echo 2)

case "$MODE" in
    cpu)
        stress-ng --cpu "$NPROC" --cpu-load 95 --timeout "${DURATION}s" --metrics-brief || true
        ;;
    memory)
        # Cap memory safely at 75% of total to avoid OOM killer
        stress-ng --vm 2 --vm-bytes 75% --timeout "${DURATION}s" --metrics-brief || true
        ;;
    both)
        stress-ng --cpu "$NPROC" --cpu-load 90 --vm 2 --vm-bytes 70% --timeout "${DURATION}s" --metrics-brief || true
        ;;
    *)
        echo "Invalid mode: $MODE"
        exit 1
        ;;
esac

# 3. AFTER (Recovery)
log "AFTER" "Entering post-load recovery phase for ${AFTER}s..."
sleep "$AFTER"

log "COMPLETE" "stress-ng load test completed successfully."
