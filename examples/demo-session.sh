#!/usr/bin/env bash
set -euo pipefail

TMP_HOME="${1:-/tmp/agents-inbox-demo}"
BIN_DIR="${2:-/tmp/agents-inbox-demo-bin}"
CLI="python3 $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src/agents_inbox/cli.py"

rm -rf "$TMP_HOME" "$BIN_DIR"

echo "== init =="
$CLI --home "$TMP_HOME" init --install-wrappers --seed --bin-dir "$BIN_DIR"

echo
echo "== codex claim =="
$CLI --home "$TMP_HOME" claim \
  --agent codex \
  --scope ui-pass \
  --summary "Own the shell layout and visual polish" \
  --files "src/app.tsx,src/layout.tsx"

echo
echo "== claude handoff =="
$CLI --home "$TMP_HOME" send \
  --from-agent claude \
  --to-agent codex \
  --summary "Sphere pass complete" \
  --details "Mesh graph glow and panel chrome are ready for integration." \
  --files "src/mesh_graph.tsx"

echo
echo "== status =="
$CLI --home "$TMP_HOME" status
