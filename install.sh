#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${AGENTS_INBOX_BIN_DIR:-$HOME/.local/bin}"

cd "$ROOT_DIR"
python3 -m pip install -e .
agents-inbox init --install-wrappers --seed --bin-dir "$BIN_DIR"

echo
echo "Agents Inbox installed."
echo "CLI: agents-inbox"
echo "Wrappers installed to: $BIN_DIR"
echo
echo "If needed, add this to your shell profile:"
echo "export PATH=\"$BIN_DIR:\$PATH\""
