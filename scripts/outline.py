#!/usr/bin/env python3
"""
token-diet :: outline

Prints a file's SHAPE instead of its contents: symbols, signatures and the line
numbers where they live. Language-agnostic (regex based), so it works on the
polyglot repos nobody has a parser for.

Typical result: a 3,000-line source file becomes a ~60-line map -- roughly 2-5%
of the tokens -- which is enough to decide the 40 lines actually worth reading.

  python3 outline.py FILE [FILE...]
  python3 outline.py --dir SRC   # repo map: biggest files first
"""
import os
import re
import sys

PATTERNS = [
    r"^\s*(?:async\s+)?def\s+\w+\s*\(",
    r"^\s*class\s+\w+",
    r"^\s*@\w[\w.]*",
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*\w*\s*\(",
    r"^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>",
    r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+\w+",
    r"^\s*(?:export\s+)?(?:interface|type|enum)\s+\w+",
    r"^\s*export\s*\{",
    r"^\s*func\s+",
    r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+\w+",
    r"^\s*(?:pub\s+)?(?:struct|trait|impl|enum)\s+\w+",
    r"^\s*(?:public|private|protected|internal)\s+[\w<>\[\],\s]+\s+\w+\s*\(",
    r"^\s*(?:public|private|protected|static|final|abstract)*\s*function\s+\w+",
    r"^\s*(?:module|class|def)\s+\w+",
    r"^\s*(?:\.|#)[\w-]+\s*[,{]",
    r"^\s*@(?:media|keyframes|mixin|include|import)\b",
    r"^#{1,4}\s+\S",
    r"^\s*\"?[\w.-]+\"?\s*:\s*\{\s*$",
    r"^\s*(?:CREATE|ALTER|INSERT|SELECT)\s+",
    r"^\s*[\w-]+\s*\(\)\s*\{",
]
COMPILED = [re.compile(p, re.IGNORECASE) for p in PATTERNS]

SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", "vendor",
             "__pycache__", ".venv", "venv", "target", ".cache", "coverage"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".woff",
              ".woff2", ".ttf", ".ico", ".mp4", ".mp3", ".lock", ".map"}


def outline_file(path, max_lines=80):
    try:
        with open(path, errors="ignore") as fh:
            lines = fh.readlines()
    except Exception as exc:
        return ["  ! cannot read: %s" % exc], 0
    hits = []
    for i, raw in enumerate(lines, 1):
        if len(raw) > 400:
            continue
        line = raw.rstrip()
        if not line.strip():
            continue
        for rx in COMPILED:
            if rx.match(line):
                hits.append((i, line.strip()[:110]))
                break
    total = len(lines)
    if len(hits) > max_lines:
        step = len(hits) // max_lines + 1
        hits = hits[::step]
    out = ["%5d | %s" % (n, t) for n, t in hits]
    if not out:
        out = ["  (no symbols matched -- data or prose; sample with sed -n '1,40p')"]
    return out, total


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "--dir":
        root = args[1] if len(args) > 1 else "."
        rows = []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in BINARY_EXT or fn.startswith("._"):
                    continue
                p = os.path.join(dp, fn)
                try:
                    rows.append((os.path.getsize(p), p))
                except Exception:
                    pass
        rows.sort(reverse=True)
        print("REPO MAP  %s  (%d files, %s KB)"
              % (root, len(rows), format(sum(r[0] for r in rows) // 1024, ",")))
        print("Biggest first -- read ranges, never whole files.\n")
        for size, p in rows[:40]:
            print("%8s KB  %s" % (format(size // 1024, ","), os.path.relpath(p, root)))
        return 0
    for path in args:
        body, total = outline_file(path)
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        print("== %s  (%d lines, %d KB -> full read ~%s tokens)"
              % (path, total, size // 1024, format(size // 4, ",")))
        print("\n".join(body))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
