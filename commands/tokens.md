---
description: Token usage report — where this project's context is going, and what to cut
---

Run the token-diet measurement suite and interpret it for the user.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tokens.py" doctor
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tokens.py" report --last ${1:-10}
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tokens.py" savings
```

Then report, concisely:
1. The fixed overhead figure and what share of an average turn it is.
2. The top three tools filling the context, with their share.
3. The avoidable waste detected (unranged reads, full-res screenshots).
4. A short ranked list of what to change, biggest saving first — concrete
   actions, not general advice.

Use the real numbers from the output. Do not estimate.
