#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${AGENTCODEHANDOFF_BIN_DIR:-$HOME/.local/bin}"
VENV_DIR="${AGENTCODEHANDOFF_VENV_DIR:-$ROOT_DIR/.venv}"

cd "$ROOT_DIR"
python3 -m venv "$VENV_DIR"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/agentcodehandoff" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$VENV_DIR/bin/python" "$ROOT_DIR/src/agentcodehandoff/cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/agentcodehandoff"
"$BIN_DIR/agentcodehandoff" init --install-wrappers --seed --bin-dir "$BIN_DIR"

echo
echo "AgentCodeHandoff installed."
echo "CLI: agentcodehandoff"
echo "Virtualenv: $VENV_DIR"
echo "Wrappers installed to: $BIN_DIR"
echo
echo "If needed, add this to your shell profile:"
echo "export PATH=\"$BIN_DIR:\$PATH\""
echo "Optional bin override: export AGENTCODEHANDOFF_BIN_DIR=\"$BIN_DIR\""
echo "Optional venv override: export AGENTCODEHANDOFF_VENV_DIR=\"$VENV_DIR\""
