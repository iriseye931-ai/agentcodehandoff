#!/usr/bin/env bash
set -euo pipefail

TMP_HOME="${1:-/tmp/agentcodehandoff-demo}"
BIN_DIR="${2:-/tmp/agentcodehandoff-demo-bin}"
CLI="python3 $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src/agentcodehandoff/cli.py"

rm -rf "$TMP_HOME" "$BIN_DIR"

echo "== init =="
$CLI --home "$TMP_HOME" init --install-wrappers --seed --bin-dir "$BIN_DIR"

echo
echo "== claude claim =="
$CLI --home "$TMP_HOME" claim \
  --agent claude \
  --scope ui-pass \
  --summary "Own the shell layout and visual polish" \
  --files "src/app.tsx,src/layout.tsx"

echo
echo "== hermes request =="
$CLI --home "$TMP_HOME" send \
  --from-agent hermes \
  --to-agent claude \
  --summary "Need README onboarding polish" \
  --details "Please tighten first-run docs and verify the supervised bridge steps." \
  --role request \
  --files "README.md,examples/demo-session.sh"

echo
echo "== status =="
$CLI --home "$TMP_HOME" status

echo
echo "== next steps in a real repo =="
echo "$CLI --home \"$TMP_HOME\" bridge-status"
echo "$CLI --home \"$TMP_HOME\" bridge-profile-show --agent claude"
echo "$CLI --home \"$TMP_HOME\" dashboard --view ops"
echo "$CLI --home \"$TMP_HOME\" bridge-recover"
