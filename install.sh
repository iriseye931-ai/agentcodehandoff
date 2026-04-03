#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${AGENTCODEHANDOFF_BIN_DIR:-$HOME/.local/bin}"

cd "$ROOT_DIR"
python3 -m pip install -e .
agentcodehandoff init --install-wrappers --seed --bin-dir "$BIN_DIR"

echo
echo "AgentCodeHandoff installed."
echo "CLI: agentcodehandoff"
echo "Wrappers installed to: $BIN_DIR"
echo
echo "If needed, add this to your shell profile:"
echo "export PATH=\"$BIN_DIR:\$PATH\""
echo "Optional bin override: export AGENTCODEHANDOFF_BIN_DIR=\"$BIN_DIR\""
