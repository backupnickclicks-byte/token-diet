#!/usr/bin/env python3
"""
token-diet :: measure

Reads your real Claude Code transcripts (~/.claude/projects/**/*.jsonl) and shows
where tokens actually go, so savings are proven rather than promised.

  tokens.py report [--last N]   per-session usage + the tools that fill context
  tokens.py doctor              fixed overhead + MCP servers you load but never use
  tokens.py savings             what the guard hook has blocked so far
  tokens.py baseline            one number, for before/after comparison
  tokens.py estimate            projected saving from applying the playbook
"""
import collections
import glob
import json
import os
import sys

PROJECTS = os.path.expanduser("~/.claude/projects")
STATE_DIR = os.path.expanduser("~/.claude/token-diet")
IMG_TOK = 1600            # Claude caps a full-size image near this
IMG_TOK_HALF = 400        # scale 0.5 -> quarter the area -> quarter the tokens


def transcripts():
    return sorted(glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")),
                  key=os.path.getmtime, reverse=True)


def walk_content(content):
    """(text_bytes, image_count) for any tool_result payload shape."""
    if content is None:
        return 0, 0
    if isinstance(content, str):
        return len(content), 0
    if isinstance(content, dict):
        if content.get("type") == "image":
            return 0, 1
        t = n = 0
        for v in content.values():
            a, b = walk_content(v)
            t += a
            n += b
        return t, n
    if isinstance(content, list):
        t = n = 0
        for v in content:
            a, b = walk_content(v)
            t += a
            n += b
        return t, n
    return len(str(content)), 0


def scan(paths):
    s = {
        "turns": 0, "cache_read": 0, "cache_create": 0, "input": 0, "output": 0,
        "txt": collections.Counter(), "img": collections.Counter(),
        "calls": collections.Counter(), "servers": collections.Counter(),
        "sessions": [], "unscaled_shots": 0, "total_shots": 0,
        "unranged_reads": 0, "total_reads": 0,
    }
    for path in paths:
        first = None
        ctxs = []
        cr = out = 0
        names = {}
        for line in open(path, errors="ignore"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            msg = d.get("message") or {}
            usage = msg.get("usage")
            if d.get("type") == "assistant" and usage:
                ctx = (usage.get("cache_read_input_tokens", 0)
                       + usage.get("cache_creation_input_tokens", 0)
                       + usage.get("input_tokens", 0))
                if first is None:
                    first = ctx
                ctxs.append(ctx)
                cr += usage.get("cache_read_input_tokens", 0)
                out += usage.get("output_tokens", 0)
                s["turns"] += 1
                s["cache_read"] += usage.get("cache_read_input_tokens", 0)
                s["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                s["input"] += usage.get("input_tokens", 0)
                s["output"] += usage.get("output_tokens", 0)
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    nm = b.get("name", "?")
                    inp = b.get("input") or {}
                    names[b.get("id")] = nm
                    s["calls"][nm] += 1
                    if nm.startswith("mcp__"):
                        s["servers"][nm.split("__")[1] if "__" in nm else nm] += 1
                    if nm == "Read":
                        s["total_reads"] += 1
                        if inp.get("limit") is None and inp.get("offset") is None:
                            s["unranged_reads"] += 1
                    if "browser" in nm or "computer" in nm:
                        acts = inp.get("actions")
                        acts = acts if isinstance(acts, list) else [{"input": inp}]
                        for a in acts:
                            ai = (a.get("input") or {}) if isinstance(a, dict) else {}
                            if ai.get("action") in ("screenshot", "zoom"):
                                s["total_shots"] += 1
                                if "scale" not in ai:
                                    s["unscaled_shots"] += 1
                if b.get("type") == "tool_result":
                    nm = names.get(b.get("tool_use_id"), "unknown")
                    tb, ni = walk_content(b.get("content"))
                    s["txt"][nm] += tb
                    s["img"][nm] += ni
        if ctxs:
            s["sessions"].append({
                "name": os.path.basename(path)[:8],
                "project": os.path.basename(os.path.dirname(path))[-28:],
                "turns": len(ctxs), "baseline": first,
                "avg": sum(ctxs) // len(ctxs), "peak": max(ctxs),
                "cache_read": cr, "output": out,
            })
    return s


def fmt(n):
    return format(int(n), ",")


def cmd_report(argv):
    n = 10
    if "--last" in argv:
        n = int(argv[argv.index("--last") + 1])
    paths = transcripts()[:n]
    if not paths:
        print("No transcripts found in %s" % PROJECTS)
        return 1
    s = scan(paths)
    print("TOKEN REPORT  --  %d session(s)\n" % len(s["sessions"]))
    print("%-9s %-28s %6s %9s %9s %9s %14s"
          % ("session", "project", "turns", "baseline", "avg ctx", "peak", "cache_read"))
    for row in sorted(s["sessions"], key=lambda r: -r["cache_read"]):
        print("%-9s %-28s %6d %9s %9s %9s %14s"
              % (row["name"], row["project"], row["turns"], fmt(row["baseline"]),
                 fmt(row["avg"]), fmt(row["peak"]), fmt(row["cache_read"])))

    billed = s["cache_read"] + s["cache_create"] + s["input"] + s["output"]
    print("\nTOTALS")
    print("  cache_read   %14s   <- context re-sent on every turn (the real bill)" % fmt(s["cache_read"]))
    print("  cache_create %14s" % fmt(s["cache_create"]))
    print("  output       %14s" % fmt(s["output"]))
    print("  TOTAL        %14s tokens over %s turns" % (fmt(billed), fmt(s["turns"])))

    rows = []
    for nm in set(list(s["txt"]) + list(s["img"])):
        tt = s["txt"][nm] // 4
        it = s["img"][nm] * IMG_TOK
        rows.append((tt + it, nm, s["calls"][nm], tt, it, s["img"][nm]))
    rows.sort(reverse=True)
    grand = sum(r[0] for r in rows) or 1
    print("\nWHAT FILLS THE CONTEXT (tool results)")
    print("  %-38s %6s %10s %9s %7s" % ("tool", "calls", "tokens", "images", "share"))
    for tot, nm, calls, tt, it, ni in rows[:10]:
        print("  %-38s %6d %10s %9d %6.1f%%" % (nm[:38], calls, fmt(tot), ni, 100 * tot / grand))
    print("  %-38s %6s %10s" % ("TOTAL", "", fmt(grand)))

    print("\nAVOIDABLE WASTE DETECTED")
    if s["total_reads"]:
        print("  unranged Read calls        %d / %d  (%.0f%%)"
              % (s["unranged_reads"], s["total_reads"],
                 100 * s["unranged_reads"] / s["total_reads"]))
    if s["total_shots"]:
        wasted = s["unscaled_shots"] * (IMG_TOK - IMG_TOK_HALF)
        print("  full-res screenshots       %d / %d  -> ~%s tokens recoverable at scale 0.5"
              % (s["unscaled_shots"], s["total_shots"], fmt(wasted)))
    return 0


def enabled_servers():
    """MCP servers / plugins configured across the usual config locations."""
    found = set()
    cfgs = [os.path.expanduser("~/.claude/settings.json"),
            os.path.expanduser("~/.claude.json"),
            os.path.join(os.getcwd(), ".mcp.json"),
            os.path.join(os.getcwd(), ".claude", "settings.json")]
    for c in cfgs:
        try:
            data = json.load(open(c))
        except Exception:
            continue
        for key in ("mcpServers", "enabledPlugins"):
            v = data.get(key)
            if isinstance(v, dict):
                for name, on in v.items():
                    if on is not False:
                        found.add(name.split("@")[0])
    try:
        reg = json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))
        for name in (reg.get("plugins") or {}):
            found.add(name.split("@")[0])
    except Exception:
        pass
    return found


def cmd_doctor(argv):
    paths = transcripts()[:20]
    s = scan(paths)
    if not s["sessions"]:
        print("No transcripts to analyse yet.")
        return 1
    baselines = sorted(r["baseline"] for r in s["sessions"])
    avgs = sorted(r["avg"] for r in s["sessions"])
    base = baselines[len(baselines) // 2]
    avg = avgs[len(avgs) // 2]

    print("CONTEXT DOCTOR  (median of %d sessions)\n" % len(s["sessions"]))
    print("  fixed overhead (system prompt + tool schemas) : %9s tokens" % fmt(base))
    print("  average context per turn                      : %9s tokens" % fmt(avg))
    print("  => %.0f%% of every turn is overhead you did not write" % (100 * base / max(avg, 1)))
    print("\n  That overhead is re-sent on all %s turns measured here:" % fmt(s["turns"]))
    print("  %s tokens spent on fixed overhead alone." % fmt(base * s["turns"]))

    used = set(s["servers"])
    enabled = enabled_servers()
    print("\nMCP SERVERS / PLUGINS")
    if used:
        print("  actually used in these sessions:")
        for name, n in s["servers"].most_common():
            print("    + %-46s %d calls" % (name[:46], n))
    else:
        print("  none were called in these sessions")
    idle = sorted(e for e in enabled if not any(e in u for u in used))
    if idle:
        print("  enabled but never called (their schemas still load every turn):")
        for name in idle:
            print("    - %s" % name)
    print("""
  Every connected server ships its full tool schemas into the fixed overhead
  above, on every turn, whether you call it or not. Disabling the ones you are
  not using in this project is the single largest saving available and costs
  nothing in capability -- re-enable them per project when you need them.

  To measure your own before/after:
      python3 tokens.py baseline      # note the number
      # disable unused servers/plugins, start a NEW session
      python3 tokens.py baseline      # compare

  Connectors enabled in the Claude desktop app are managed in the app's own
  connector settings, not in these files -- turn off the ones this project does
  not need there, and the fixed overhead above drops the same way.""")
    return 0


def cmd_baseline(argv):
    paths = transcripts()[:1]
    if not paths:
        print("No transcripts found.")
        return 1
    s = scan(paths)
    if not s["sessions"]:
        print("Latest transcript has no usage data yet.")
        return 1
    r = s["sessions"][0]
    print("Most recent session : %s (%s)" % (r["name"], r["project"]))
    print("Fixed overhead      : %s tokens" % fmt(r["baseline"]))
    print("Average context/turn: %s tokens" % fmt(r["avg"]))
    print("Turns               : %d" % r["turns"])
    return 0


def cmd_savings(argv):
    total = blocks = 0
    files = glob.glob(os.path.join(STATE_DIR, "*.json"))
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        total += d.get("saved", 0)
        blocks += d.get("blocks", 0)
    print("GUARD SAVINGS  (%d session state files)\n" % len(files))
    print("  oversized payloads blocked : %d" % blocks)
    print("  tokens kept out of context : ~%s" % fmt(total))
    print("""
  Note: this counts tokens at the moment they were blocked. The true saving is
  larger, because anything that enters context is re-sent on every later turn --
  blocking a 20k dump at turn 10 of a 200-turn session saves 20k x 190.""")
    return 0



def cmd_estimate(argv):
    """Project the saving of applying the playbook, using THIS user's real numbers.

    Assumptions are printed, conservative, and each maps to one enforced rule.
    """
    cut = 0.5
    if "--overhead-cut" in argv:
        cut = float(argv[argv.index("--overhead-cut") + 1])
    paths = transcripts()[:20]
    s = scan(paths)
    if not s["sessions"]:
        print("No transcripts to analyse yet.")
        return 1

    turns = s["turns"]
    baselines = sorted(r["baseline"] for r in s["sessions"])
    base = baselines[len(baselines) // 2]
    spend = s["cache_read"] + s["cache_create"] + s["input"] + s["output"]

    # 1. fixed overhead: present in every turn's context
    overhead_total = base * turns
    overhead_saved = overhead_total * cut

    # 2. images: unscaled screenshots at scale 0.5 cost a quarter
    img_saved_once = s["unscaled_shots"] * (IMG_TOK - IMG_TOK_HALF)

    # 3. text payload: unranged reads + unbounded command output.
    #    Conservative: assume the guard removes 60% of Read text and 40% of Bash text.
    read_txt = s["txt"].get("Read", 0) // 4
    bash_txt = s["txt"].get("Bash", 0) // 4
    txt_saved_once = read_txt * 0.6 + bash_txt * 0.4

    # payload savings compound: content removed at turn t is not re-sent for the
    # remaining turns. Averaged over a session, a removed token is spared on
    # roughly half the turns. Use a deliberately low multiplier of turns/2/sessions.
    per_session_turns = turns / max(len(s["sessions"]), 1)
    multiplier = max(per_session_turns / 2.0, 1.0)
    payload_saved = (img_saved_once + txt_saved_once) * multiplier

    total_saved = overhead_saved + payload_saved
    pct = 100 * total_saved / max(spend, 1)

    print("SAVINGS ESTIMATE  --  projected on %d of your own sessions\n" % len(s["sessions"]))
    print("  measured spend                 %14s tokens (%s turns)" % (fmt(spend), fmt(turns)))
    print("\n  1. fixed overhead")
    print("     %s tok baseline x %s turns  = %s" % (fmt(base), fmt(turns), fmt(overhead_total)))
    print("     cutting %.0f%% of it (disable idle MCP servers/plugins)" % (cut * 100))
    print("     saved                        %14s" % fmt(overhead_saved))
    print("\n  2. tool payload (guard-enforced)")
    print("     %d full-res screenshots -> scale 0.5   %10s tok" % (s["unscaled_shots"], fmt(img_saved_once)))
    print("     ranged reads + bounded command output  %10s tok" % fmt(txt_saved_once))
    print("     x %.0f turns it would otherwise be re-sent" % multiplier)
    print("     saved                        %14s" % fmt(payload_saved))
    print("\n  TOTAL PROJECTED SAVING         %14s tokens  =  %.0f%%" % (fmt(total_saved), pct))
    print("""
  How to verify rather than trust this:
    1. python3 tokens.py report --last 5     <- note tokens/turn today
    2. apply the playbook (/token-doctor, then restart)
    3. work a comparable session
    4. python3 tokens.py report --last 1     <- compare tokens/turn

  Re-run with --overhead-cut 0.3 for a pessimistic view, 0.7 for aggressive.""")
    return 0


CMDS = {"report": cmd_report, "doctor": cmd_doctor,
        "savings": cmd_savings, "baseline": cmd_baseline,
        "estimate": cmd_estimate}

if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "report"
    if cmd not in CMDS:
        print(__doc__)
        sys.exit(1)
    sys.exit(CMDS[cmd](args))
