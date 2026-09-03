#!/usr/bin/env python3
"""
token-diet :: PreToolUse guard

Stops oversized tool payloads BEFORE they enter the context window.

Why this matters: anything that enters context is re-read (cache_read) on every
subsequent turn of the session. A single 40k-token dump in a 300-turn session is
not 40k tokens -- it is 40k x remaining turns. Blocking it once is the highest
leverage action available.

Contract: PreToolUse hook.
  stdin  -> JSON {session_id, cwd, tool_name, tool_input, ...}
  exit 0 -> allow (silent)
  exit 2 -> block; stderr is fed back to Claude as the correction to apply

Exit code 2 is used deliberately: it is the one hook contract supported by every
Claude Code version, so this plugin degrades safely instead of silently failing
open. Kill switch: TOKEN_DIET=off
"""
import json
import os
import re
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/token-diet")
CONFIG_PATH = os.path.expanduser("~/.claude/token-diet.json")

DEFAULTS = {
    "enabled": True,
    "max_read_bytes": 60000,        # ~15k tokens: block unranged reads past this
    "max_read_lines": 1200,
    "max_image_bytes": 400000,      # downscale huge screenshots/scans first
    "cat_max_bytes": 60000,
    "screenshot_teach_once": True,  # nag once per session, then stop
    "block_bash": True,
    "block_read": True,
    "block_reread": True,
    "block_screenshots": True,
    "allow_paths": [],              # substrings that bypass every check
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    return cfg


def block(msg):
    sys.stderr.write("[token-diet] " + msg + "\n")
    sys.exit(2)


def allow():
    sys.exit(0)


# ---------------------------------------------------------------- session state
def state_path(session_id):
    os.makedirs(STATE_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "nosession")
    return os.path.join(STATE_DIR, safe + ".json")


def load_state(session_id):
    try:
        with open(state_path(session_id)) as fh:
            return json.load(fh)
    except Exception:
        return {"reads": {}, "taught": [], "saved": 0, "blocks": 0}


def save_state(session_id, st):
    try:
        st["ts"] = time.time()
        tmp = state_path(session_id) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(st, fh)
        os.replace(tmp, state_path(session_id))
    except Exception:
        pass


def record(session_id, st, saved_tokens):
    st["saved"] = st.get("saved", 0) + max(0, int(saved_tokens))
    st["blocks"] = st.get("blocks", 0) + 1
    save_state(session_id, st)


# ---------------------------------------------------------------------- helpers
def approx_tokens(nbytes):
    return int(nbytes / 4)


HAS_LIMIT = re.compile(
    r"(\|\s*(head|tail|wc|jq|cut)\b)"
    r"|(\bhead\s+-)|(\btail\s+-)|(\bwc\b)"
    r"|(\bsed\s+-n\b)"
    r"|(--max-count)|(-m\s*\d+)"
    r"|(-n\s*\d+)"
    r"|(--quiet)|(--silent)|(\s-q\b)|(\s-s\b)"
    r"|(>\s*/dev/null)"
    r"|(\|\s*grep\b)"
    r"|(--stat\b)|(--name-only)|(--oneline)"
)

# (regex, human name, suggested bounded replacement)
VERBOSE = [
    (r"\bnpm\s+(i|install|ci)\b", "npm install",
     "append  2>&1 | tail -n 15"),
    (r"\b(yarn|pnpm)\s+(install|add)\b", "package install",
     "append  2>&1 | tail -n 15"),
    (r"\bpip3?\s+install\b", "pip install",
     "append  2>&1 | tail -n 10"),
    (r"\bnpm\s+run\s+(build|dev)\b", "build",
     "append  2>&1 | tail -n 30"),
    (r"\b(webpack|vite|rollup|esbuild|tsc)\b", "bundler/compiler",
     "append  2>&1 | tail -n 30"),
    (r"\b(pytest|jest|vitest|mocha)\b", "test runner",
     "add -q (or --reporter=dot) and append  2>&1 | tail -n 30"),
    (r"\bcargo\s+(build|test)\b", "cargo",
     "append  2>&1 | tail -n 30"),
    (r"\bdocker\s+(build|compose\s+up)\b", "docker",
     "append  2>&1 | tail -n 20"),
    (r"\bgit\s+log\b", "git log",
     "add --oneline -n 20"),
    (r"\bgit\s+diff\b", "git diff",
     "use --stat first, then diff only the files you need"),
    (r"\bgit\s+show\b", "git show",
     "add --stat, or target a single path"),
    (r"\bls\s+-[a-zA-Z]*R", "recursive ls",
     "use  find . -maxdepth 2  or  ls | head -n 50"),
    (r"\btree\b", "tree",
     "add -L 2 and append  | head -n 60"),
    # -s may be bundled (-fsSL), and -o/-O send the body to a file, not to us.
    (r"\bcurl\b(?!.*(?:-[A-Za-z]*s|--silent|--output|-o\s|-O\b))", "curl",
     "add -s and append  | head -c 4000"),
    (r"\bfind\s+/\s", "find from root",
     "scope the search to a project directory and append  | head -n 50"),
    # NB: (?![<>]) keeps this off writes -- `cat > f <<'EOF'` is a heredoc write,
    # not a read, and its body routinely contains `*`.
    (r"\b(cat|bat)\s+(?![<>])[^|;<>]*\*", "cat with glob",
     "read one file at a time with  sed -n '1,120p' FILE"),
    (r"\bjournalctl\b|\bdmesg\b", "system log",
     "append  | tail -n 50"),
    (r"\benv\b\s*$|\bprintenv\b\s*$", "full environment dump",
     "grep for the specific variable instead"),
]

GREP_RECURSIVE = re.compile(r"\b(grep|rg|ag)\b.*(-r|-R|--recursive|\s\.\s*$|\s\.$)")

QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
SEP = re.compile(r"\|\||&&|[|;\n]")
ENV_PREFIX = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*")
HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def _strip_heredocs(cmd):
    """Drop heredoc bodies. They are data being written, not commands."""
    out, terminator = [], None
    for ln in cmd.split("\n"):
        if terminator is not None:
            if ln.strip() == terminator:
                terminator = None
            continue
        out.append(ln)
        m = HEREDOC.search(ln)
        if m:
            terminator = m.group(1)
    return "\n".join(out)


def command_segments(cmd):
    """Yield the parts of a command line where a command actually starts.

    Quoted text and heredoc bodies are removed first, so a tool NAME appearing
    inside an argument is never mistaken for a tool CALL -- `grep -n 'curl' f`
    greps, it does not curl. Leading VAR=x assignments are stripped so the real
    command is at position 0 and patterns can be anchored there.
    """
    clean = QUOTED.sub(" ", _strip_heredocs(cmd))
    for seg in SEP.split(clean):
        seg = ENV_PREFIX.sub("", seg).strip()
        if seg:
            yield seg


def check_bash(cmd, cfg, session_id, st):
    if not cfg["block_bash"]:
        allow()
    if HAS_LIMIT.search(cmd):
        allow()
    # `cat > f`, `cat >> f`, `cat < f`: a write or a heredoc, never a read into
    # context. Nothing below applies.
    if re.search(r"\b(?:cat|bat|tee)\s*[<>]", cmd):
        allow()

    # cat / head-less read of a concrete large file
    m = None
    for seg in command_segments(cmd):
        m = re.match(r"(?:cat|bat|less|more)\s+([^\s|;&><]+)", seg)
        if m:
            break
    if m:
        target = m.group(1).strip("'\"")
        path = target if os.path.isabs(target) else os.path.join(os.getcwd(), target)
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        if size > cfg["cat_max_bytes"]:
            record(session_id, st, approx_tokens(size))
            block(
                "'%s' is %s KB (~%s tokens). Dumping it costs that on EVERY later turn.\n"
                "Do this instead:\n"
                "  - structure first:  python3 %s/outline.py '%s'\n"
                "  - then the range you need:  sed -n '120,200p' '%s'\n"
                "  - or search it:  grep -n 'symbol' '%s' | head -n 30"
                % (target, size // 1024, approx_tokens(size),
                   os.path.dirname(os.path.abspath(__file__)), target, target, target)
            )

    segs = list(command_segments(cmd))
    if (any(GREP_RECURSIVE.match(s) for s in segs)
            and not re.search(r"-l\b|-c\b|--files-with-matches", cmd)):
        record(session_id, st, 3000)
        block(
            "Recursive search without a bound floods context with match bodies.\n"
            "Two-step it:\n"
            "  1) which files:   rg -l 'pattern' | head -n 30\n"
            "  2) then read only the interesting ones with a line range."
        )

    for seg in command_segments(cmd):
        for pat, name, fix in VERBOSE:
            if not re.match(pat, seg):
                continue
            record(session_id, st, 2500)
            block(
                "'%s' output is unbounded and lands in context permanently.\n"
                "Re-run it bounded: %s\n"
                "(Only the tail matters -- errors and the final status are at the end.)"
                % (name, fix)
            )
    allow()


def check_read(inp, cfg, session_id, st):
    if not cfg["block_read"]:
        allow()
    path = inp.get("file_path") or ""
    for frag in cfg.get("allow_paths", []):
        if frag and frag in path:
            allow()
    try:
        size = os.path.getsize(path)
    except Exception:
        allow()

    ext = os.path.splitext(path)[1].lower()
    is_img = ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff")

    if is_img:
        if size > cfg["max_image_bytes"]:
            record(session_id, st, 1200)
            block(
                "Image is %s KB. Full-size screenshots burn ~1600 tokens each and\n"
                "rarely need that resolution. Downscale first, then read the copy:\n"
                "  sips -Z 1000 '%s' --out /tmp/td_small.png >/dev/null && echo done\n"
                "Read /tmp/td_small.png instead." % (size // 1024, path)
            )
        allow()

    has_range = inp.get("limit") is not None or inp.get("offset") is not None

    # duplicate-read guard: same file, same range, unchanged on disk
    if cfg["block_reread"]:
        try:
            mtime = int(os.path.getmtime(path))
        except Exception:
            mtime = 0
        key = "%s|%s|%s" % (path, inp.get("offset"), inp.get("limit"))
        prev = st.get("reads", {}).get(key)
        if prev == mtime:
            record(session_id, st, approx_tokens(min(size, 40000)))
            block(
                "You already read this exact range of '%s' in this session and the file\n"
                "has not changed since. It is still in your context -- scroll back instead\n"
                "of paying for it twice. If you need a DIFFERENT part, pass offset/limit."
                % os.path.basename(path)
            )
        st.setdefault("reads", {})[key] = mtime
        save_state(session_id, st)

    if not has_range and size > cfg["max_read_bytes"]:
        record(session_id, st, approx_tokens(size - 20000))
        block(
            "'%s' is %s KB (~%s tokens) and you asked for all of it.\n"
            "Get the map before the territory:\n"
            "  1) python3 %s/outline.py '%s'      <- symbols + line numbers, ~2%% of the cost\n"
            "  2) Read again with offset/limit around the lines that matter.\n"
            "If you truly need the whole file, pass limit=%d explicitly to confirm."
            % (os.path.basename(path), size // 1024, approx_tokens(size),
               os.path.dirname(os.path.abspath(__file__)), path, cfg["max_read_lines"])
        )
    allow()


def iter_actions(tool_input):
    """Yield action dicts from either a single browser call or a browser_batch."""
    acts = tool_input.get("actions")
    if isinstance(acts, list):
        for a in acts:
            if isinstance(a, dict) and isinstance(a.get("input"), dict):
                yield a["input"]
    else:
        yield tool_input


def check_browser(tool_input, cfg, session_id, st):
    if not cfg["block_screenshots"]:
        allow()
    unscaled = 0
    for a in iter_actions(tool_input):
        if a.get("action") in ("screenshot", "zoom") and "scale" not in a:
            unscaled += 1
    if not unscaled:
        allow()
    if cfg["screenshot_teach_once"] and "screenshot_scale" in st.get("taught", []):
        allow()
    st.setdefault("taught", []).append("screenshot_scale")
    record(session_id, st, 1200 * unscaled)
    block(
        "%d screenshot(s) requested at full resolution (~1600 tokens each).\n"
        "Add  \"scale\": 0.5  to the screenshot action -- image tokens scale with area,\n"
        "so half-size costs a QUARTER of the tokens and stays readable for layout checks.\n"
        "Use scale 1 only when you must read small text.\n"
        "(This is a one-time reminder for this session.)" % unscaled
    )


def main():
    if os.environ.get("TOKEN_DIET", "").lower() in ("off", "0", "false"):
        allow()
    try:
        payload = json.load(sys.stdin)
    except Exception:
        allow()

    cfg = load_config()
    if not cfg.get("enabled", True):
        allow()

    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input", {}) or {}
    sid = payload.get("session_id", "")
    st = load_state(sid)

    if tool == "Bash":
        check_bash(inp.get("command", "") or "", cfg, sid, st)
    elif tool == "Read":
        check_read(inp, cfg, sid, st)
    elif "browser" in tool or "computer" in tool or "chrome" in tool:
        check_browser(inp, cfg, sid, st)
    allow()


if __name__ == "__main__":
    main()
