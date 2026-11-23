#!/usr/bin/env bash
# Register this MCP server as a LOCAL MCP toolkit in watsonx Orchestrate.
#
# A local MCP toolkit ships this server's code to Orchestrate, which installs it
# (via requirements.txt at package_root) and runs `python server.py` over stdio
# INSIDE the Orchestrate runtime — no HTTP endpoint, no Code Engine, no container.
#
# Prerequisites:
#   - Python 3.11-3.14
#   - ADK installed:  pip install --upgrade ibm-watsonx-orchestrate
#   - Config:         cp .env.example .env && edit
#
# Usage:
#   set -a; source deploy/ibm/local-mcp-toolkit/.env; set +a
#   #   export WXO_API_KEY="<your key>"     # else you'll be prompted (hidden)
#   bash deploy/ibm/local-mcp-toolkit/register-toolkit.sh
set -euo pipefail

# --- Validate config ----------------------------------------------------------
: "${WXO_ENV_NAME:?set WXO_ENV_NAME (source deploy/ibm/local-mcp-toolkit/.env)}"
: "${WXO_INSTANCE_URL:?set WXO_INSTANCE_URL}"
: "${TOOLKIT_NAME:?set TOOLKIT_NAME}"

# This script lives in the kit dir; toolkit.yaml sits next to it.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Use a disposable private path and stage before authenticating.
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEMP_DIR}"' EXIT
STAGE_DIR="${TEMP_DIR}/package"
TOOLKIT_YAML="${TEMP_DIR}/toolkit.yaml"
if [[ ! "${TOOLKIT_NAME}" =~ ^[A-Za-z][A-Za-z0-9_-]*$ ]]; then
  echo "FATAL: TOOLKIT_NAME must start with a letter; use only letters, digits, _ or -." >&2
  exit 2
fi
if [[ -f "${SCRIPT_DIR}/package/server.py" ]]; then
  # Standalone ZIP: only copy the verified package, never the adjacent .env.
  cp -R "${SCRIPT_DIR}/package" "${STAGE_DIR}"
else
  bash "${SCRIPT_DIR}/stage-toolkit.sh" "${STAGE_DIR}"
fi

echo "=== Target ==="
echo "  env name:      ${WXO_ENV_NAME}"
echo "  instance URL:  ${WXO_INSTANCE_URL}"
echo "  toolkit name:  ${TOOLKIT_NAME}"
echo ""

# --- Confirm the ADK is installed ---------------------------------------------
if ! command -v orchestrate >/dev/null 2>&1; then
  echo "FATAL: 'orchestrate' not found. Install the ADK:" >&2
  echo "       pip install --upgrade ibm-watsonx-orchestrate" >&2
  exit 1
fi

# --- Obtain the API key (never written to disk) -------------------------------
API_KEY="${WXO_API_KEY:-}"
if [[ -z "${API_KEY}" ]]; then
  echo "=== watsonx Orchestrate API key ==="
  read -r -s -p "  API key (input hidden): " API_KEY
  echo ""
  if [[ -z "${API_KEY}" ]]; then
    echo "FATAL: no API key provided." >&2
    exit 1
  fi
fi

# --- Point the ADK at the instance and activate it ----------------------------
echo "=== Configuring ADK environment '${WXO_ENV_NAME}' ==="
envs="$(orchestrate env list)"
existing="$(grep -Fw -- "${WXO_ENV_NAME}" <<< "${envs}" || true)"
if [[ -n "${existing}" ]]; then
  if ! grep -Fq -- "${WXO_INSTANCE_URL}" <<< "${existing}"; then
    echo "FATAL: existing local env could not be matched to the requested URL." >&2
    echo "       Choose a fresh local WXO_ENV_NAME for this sandbox." >&2
    exit 1
  fi
  echo "  existing env and URL match; reusing."
else
  orchestrate env add --name "${WXO_ENV_NAME}" --url "${WXO_INSTANCE_URL}" --type ibm_iam
fi
orchestrate env activate "${WXO_ENV_NAME}" --api-key "${API_KEY}"
orchestrate env list

# --- Generate the toolkit spec pointing at the staging dir --------------------
sed -E -e "s#^package_root:.*#package_root: ${STAGE_DIR}#" \
  -e "s#^name:.*#name: ${TOOLKIT_NAME}#" \
  "${SCRIPT_DIR}/toolkit.yaml" > "${TOOLKIT_YAML}"

# --- Import only under a fresh name. Never remove/replace a live toolkit. ------
echo "=== Importing local MCP toolkit '${TOOLKIT_NAME}' ==="
toolkits="$(orchestrate toolkits list)"
if grep -Fq -- "${TOOLKIT_NAME}" <<< "${toolkits}"; then
  echo "FATAL: toolkit name already appears in IBM; choose a new versioned name." >&2
  exit 1
fi
orchestrate toolkits import -f "${TOOLKIT_YAML}"

echo ""
echo "=== Registered ==="
orchestrate toolkits list
echo ""
echo "Next: build/edit an agent in the Orchestrate UI, add the '${TOOLKIT_NAME}'"
echo "tools, and chat-test a question (see README Part 3)."
