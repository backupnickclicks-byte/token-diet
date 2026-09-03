#!/usr/bin/env bash
# token-diet :: local installer
#
# Registers the guard hook in ~/.claude/settings.json without touching anything
# else, and writes a default tuning file. Makes a timestamped backup first.
#
#   ./install.sh            install
#   ./install.sh --uninstall  remove only token-diet's entries
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
MODE="${1:-install}"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
mkdir -p "$HOME/.claude"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

BACKUP="$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
cp "$SETTINGS" "$BACKUP"
echo "backup: $BACKUP"

python3 - "$SETTINGS" "$ROOT" "$MODE" <<'PY'
import json, sys
settings, root, mode = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(settings))
cmd = 'python3 "%s/scripts/guard.py"' % root
hooks = data.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])

# always drop any previous token-diet entry so re-running is idempotent
pre[:] = [e for e in pre
          if not any("token-diet" in (h.get("command") or "") or "guard.py" in (h.get("command") or "")
                     for h in (e.get("hooks") or []))]

if mode != "--uninstall":
    for matcher in ("Bash|Read|Grep|Glob", "mcp__.*(browser|computer|chrome).*"):
        pre.append({"matcher": matcher,
                    "hooks": [{"type": "command", "command": cmd, "timeout": 10}]})

if not pre:
    hooks.pop("PreToolUse", None)
if not hooks:
    data.pop("hooks", None)

json.dump(data, open(settings, "w"), indent=2)
print("uninstalled" if mode == "--uninstall" else "hook registered: %s" % cmd)
PY

CFG="$HOME/.claude/token-diet.json"
if [ "$MODE" != "--uninstall" ] && [ ! -f "$CFG" ]; then
  cat > "$CFG" <<'JSON'
{
  "enabled": true,
  "max_read_bytes": 60000,
  "max_read_lines": 1200,
  "max_image_bytes": 400000,
  "cat_max_bytes": 60000,
  "screenshot_teach_once": true,
  "block_bash": true,
  "block_read": true,
  "block_reread": true,
  "block_screenshots": true,
  "allow_paths": []
}
JSON
  echo "config: $CFG"
fi

if [ "$MODE" != "--uninstall" ]; then
  echo
  python3 "$ROOT/scripts/test_guard.py" || { echo "SELF-TEST FAILED — not safe to use"; exit 1; }
  echo
  echo "Done. Restart Claude Code so the hook loads, then run:  /tokens"
fi
