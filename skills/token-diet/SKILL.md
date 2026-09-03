---
name: token-diet
description: Diagnose and cut Claude Code token consumption. Use when the user asks why a session is expensive, wants a token/usage report, hits context limits or auto-compaction, asks how to save tokens or cost, or wants to know what is filling the context window. Also use when planning how to explore a large or unfamiliar codebase cheaply.
---

# Token Diet

Reduce the tokens a session burns, then prove it with measurement.

## The one idea that matters

**Tokens are charged per turn, not per read.** Everything sitting in context is
re-sent to the model on every subsequent turn (`cache_read`). A 20k-token file
dump at turn 10 of a 200-turn session is not 20k tokens — it is 20k × 190.

So the goal is never "read less once". It is **keep resident context small**.
Three things determine it, in order of size:

1. **Fixed overhead** — system prompt + every connected MCP server's tool schemas.
   Measured on real sessions: typically ~55–65k tokens, ~58% of an average turn,
   present before the user types anything.
2. **Tool results** — command output, file contents, screenshots.
3. **The conversation itself** — usually the smallest of the three.

## Diagnose first

Never guess where the tokens went. Run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tokens.py doctor    # fixed overhead + idle MCP servers
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tokens.py report --last 10
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tokens.py savings   # what the guard blocked
```

Report the actual numbers back to the user. `doctor` is the one that usually
finds the largest win, because fixed overhead is paid on every single turn.

## The playbook, in order of payoff

### 1. Cut fixed overhead (biggest lever, zero capability cost)
Disable MCP servers and plugins this project does not need. Each one loads its
full tool schemas into every turn whether called or not. `doctor` lists which
ones were enabled but never called. Desktop-app connectors are turned off in the
app's connector settings; CLI servers in `.mcp.json` / `settings.json`.
Re-enable per project when actually needed.

### 2. Never read a whole file to find one thing
Get the shape, then the range:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/outline.py FILE      # symbols + line numbers, ~5% of a full read
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/outline.py --dir src # repo map, biggest files first
```

Then `Read` with `offset`/`limit` around the lines that matter, or
`sed -n '120,200p' FILE`. Search before reading: `rg -l 'pattern'` to find the
files, `rg -n 'pattern' | head -n 30` to find the lines.

### 3. Bound every command's output
Anything that can print thousands of lines gets a limiter: `| tail -n 30` for
builds, installs and test runs (the errors and the verdict are at the end),
`--oneline -n 20` for `git log`, `--stat` before `git diff`, `-q` for test
runners, `| head -c 4000` for `curl`.

### 4. Right-size images
Image tokens scale with **area**. `"scale": 0.5` on a browser screenshot costs a
quarter of the tokens and is still perfectly readable for layout and flow checks.
Use full resolution only when small text must be read. Downscale large local
images before reading them: `sips -Z 1000 in.png --out /tmp/small.png`.

### 5. Do not re-read what is already in context
If a file was read earlier in the session and has not changed, it is still there.
Scroll back instead of paying again. Do not re-read a file to "verify" an edit —
`Edit` fails loudly if it did not apply.

### 6. Offload bulk exploration
When a question requires sweeping many files and only the conclusion matters,
run it in a subagent: the file dumps stay in the subagent's context and only the
answer enters yours. Only do this when the user permits subagents.

### 7. Restart beats compacting
Auto-compaction re-reads the entire context to summarise it. When a session's
work is done, a fresh session starting at the fixed-overhead baseline is far
cheaper than continuing near the compaction threshold.

## The guard hook

`scripts/guard.py` runs as a `PreToolUse` hook and blocks oversized payloads
*before* they enter context, returning the bounded alternative to run instead.
It is deliberately surgical — it allows anything already bounded.

- Kill switch for one command: `TOKEN_DIET=off`
- Tuning: `~/.claude/token-diet.json` (thresholds, per-check on/off, `allow_paths`)
- Self-test: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/test_guard.py`

When the guard blocks a call, do not fight it or disable it — run the bounded
command it suggests. That is the saving.
