#!/usr/bin/env bash

# Stop the FarajaMH app started via start.sh by killing processes bound to port 5000.
# Usage:
#   chmod +x stop.sh
#   ./stop.sh

set -euo pipefail

PORT="${1:-5000}"

if ! command -v lsof >/dev/null 2>&1; then
  echo "lsof is required to detect processes on port ${PORT}."
  echo "Install lsof (e.g. 'sudo apt install lsof') and try again."
  exit 1
fi

PIDS=$(lsof -t -i:"${PORT}" || true)

if [ -z "${PIDS}" ]; then
  echo "No process found listening on port ${PORT}."
  exit 0
fi

echo "Stopping processes on port ${PORT}: ${PIDS}"
kill ${PIDS} || true

sleep 1

STILL=$(lsof -t -i:"${PORT}" || true)
if [ -n "${STILL}" ]; then
  echo "Some processes still running on port ${PORT}: ${STILL}"
  echo "You may need to stop them manually (e.g. kill -9)."
else
  echo "FarajaMH stopped (no process on port ${PORT})."
fi

