#!/usr/bin/env bash
# Offline staging only: no login, network calls, or changes to any IBM toolkit.
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 NEW_STAGE_DIRECTORY (optional PYTHON_BIN environment override)" >&2
  exit 2
fi
KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${KIT_DIR}/../../.." && pwd)"
mkdir -- "$1"
STAGE="$(cd "$1" && pwd)"
PACKAGE="${STAGE}/example_server"
SYNTHETIC="${PACKAGE}/runtime_data/synthetic"
cp -R "${REPO}/src/example_server" "${PACKAGE}"
cp "${KIT_DIR}/server.py" "${KIT_DIR}/requirements.txt" "${STAGE}/"
mkdir -p "${SYNTHETIC}/pims_staffing" "${SYNTHETIC}/staffing"
cp "${REPO}"/data/synthetic/pims_staffing/*.csv "${SYNTHETIC}/pims_staffing/"
cp "${REPO}"/data/synthetic/staffing/*.csv "${SYNTHETIC}/staffing/"
# Rebuild known synthetic inputs; never copy a workstation DB or accept --tsa-csv.
SOURCE_DATE_EPOCH=1794873600 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${STAGE}" "${PYTHON_BIN:-python3}" \
  -m example_server.pims.generator --db "${SYNTHETIC}/pims/pims_pmis_synthetic.sqlite"
find "${PACKAGE}" -type d -name '__pycache__' -prune -exec rm -rf {} +
