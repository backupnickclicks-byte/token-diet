#!/usr/bin/env python3
"""token-diet :: self-test. Proves the guard blocks what it claims to block."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard.py")
SID = "selftest-session"
STATE = os.path.expanduser("~/.claude/token-diet/%s.json" % SID)

def run(tool, inp, env=None, sid=SID):
    e = dict(os.environ)
    if env: e.update(env)
    p = subprocess.run([sys.executable, GUARD], input=json.dumps(
        {"session_id": sid, "cwd": os.getcwd(), "hook_event_name": "PreToolUse",
         "tool_name": tool, "tool_input": inp}),
        capture_output=True, text=True, env=e)
    return p.returncode, p.stderr.strip()

def main():
    if os.path.exists(STATE): os.remove(STATE)
    tmp = tempfile.mkdtemp()
    big = os.path.join(tmp, "big.js")
    with open(big, "w") as fh: fh.write("// line\n" * 20000)          # ~160 KB
    small = os.path.join(tmp, "small.js")
    with open(small, "w") as fh: fh.write("const a = 1;\n")
    bigpng = os.path.join(tmp, "shot.png")
    with open(bigpng, "wb") as fh: fh.write(b"\x89PNG" + b"\0" * 900000)

    BLOCK, ALLOW = 2, 0
    cases = [
        ("npm install unbounded",      "Bash", {"command": "npm install"}, BLOCK),
        ("npm install bounded",        "Bash", {"command": "npm install 2>&1 | tail -n 15"}, ALLOW),
        ("git log unbounded",          "Bash", {"command": "git log"}, BLOCK),
        ("git log bounded",            "Bash", {"command": "git log --oneline -n 20"}, ALLOW),
        ("recursive ls",               "Bash", {"command": "ls -R src"}, BLOCK),
        ("plain ls",                   "Bash", {"command": "ls src"}, ALLOW),
        ("cat big file",               "Bash", {"command": "cat %s" % big}, BLOCK),
        ("sed range on big file",      "Bash", {"command": "sed -n '1,50p' %s" % big}, ALLOW),
        ("cat small file",             "Bash", {"command": "cat %s" % small}, ALLOW),
        # regressions: a redirect makes `cat` a write, never a read into context
        ("cat heredoc write",          "Bash", {"command": "cat > out.py <<'EOF'\nglob.glob('a/*/b')\nEOF"}, ALLOW),
        ("cat append to big file",     "Bash", {"command": "cat >> %s" % big}, ALLOW),
        ("rg recursive unbounded",     "Bash", {"command": "rg -r 'TODO' ."}, BLOCK),
        ("rg files-only",              "Bash", {"command": "rg -l 'TODO' | head -n 30"}, ALLOW),
        ("Read big, no range",         "Read", {"file_path": big}, BLOCK),
        ("Read big, ranged",           "Read", {"file_path": big, "offset": 100, "limit": 80}, ALLOW),
        ("Read same range again",      "Read", {"file_path": big, "offset": 100, "limit": 80}, BLOCK),
        ("Read oversized image",       "Read", {"file_path": bigpng}, BLOCK),
        ("screenshot no scale (1st)",  "mcp__Claude_Browser__computer", {"action": "screenshot"}, BLOCK),
        ("screenshot no scale (2nd)",  "mcp__Claude_Browser__computer", {"action": "screenshot"}, ALLOW),
        ("batch screenshot w/ scale",  "mcp__Claude_Browser__browser_batch",
            {"actions": [{"name": "computer", "input": {"action": "screenshot", "scale": 0.5}}]}, ALLOW),
        ("kill switch honoured",       "Bash", {"command": "npm install"}, ALLOW),
    ]

    passed = failed = 0
    for i, (name, tool, inp, want) in enumerate(cases):
        env = {"TOKEN_DIET": "off"} if name.startswith("kill switch") else None
        rc, err = run(tool, inp, env)
        ok = (rc == want)
        passed += ok; failed += not ok
        mark = "PASS" if ok else "FAIL"
        exp = "block" if want == 2 else "allow"
        print("  [%s] %-28s expected=%-5s got=%s" % (mark, name, exp, "block" if rc == 2 else "allow"))
        if not ok and err:
            print("         stderr: %s" % err.splitlines()[0])

    st = {}
    if os.path.exists(STATE):
        st = json.load(open(STATE))
    print("\n  %d passed, %d failed" % (passed, failed))
    print("  tokens prevented from entering context during this test: ~%s (%d blocks)"
          % (format(st.get("saved", 0), ","), st.get("blocks", 0)))
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
