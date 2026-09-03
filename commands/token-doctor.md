---
description: Find the fixed context overhead and the MCP servers loaded but never used
---

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tokens.py" doctor
```

Explain to the user:
- how many tokens of fixed overhead they pay on **every** turn,
- which connected servers/plugins they never actually called,
- the exact total those idle schemas cost across the measured turns.

Then ask whether to disable the idle ones for this project. Do not disable
anything without the user's explicit go-ahead.
