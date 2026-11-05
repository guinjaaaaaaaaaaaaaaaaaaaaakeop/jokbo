"""PostToolUse and PostToolUseFailure on every tool — the session's own call is over, failed or not: what it changed is its
own, remembered as where the tree now stands (a failed call's writes were once told back as another hand's: the host
fires PostToolUseFailure for it). An Agent call's end makes its sub-agent's work the session's. And at the first look at
a file (Read; cat, head, tail, sed, awk, grep, rg, less, nl on a file), a line per fact that bears on it — this arrives
before the next call is decided, which is before an edit. And when a shell command searched the project's history or
documents (`git log -- PATH`, `git blame`, a recursive grep), what the documents say of that file or those words, once
per target per session: the session asked jokbo's question with grep (guin-site, 10-10); the host's own Grep tool too."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
from _common import jokbo  # noqa: E402


def main():
    p, target = _common.payload()
    if not p:
        return 0
    lines = []
    try:
        jokbo.remember(target, p.get("session_id"))
        jokbo.note_call(target, p.get("session_id"), p, ended=True)   # its plugin keeps working for the session a while after
        if p.get("tool_name") in jokbo.AGENT_TOOLS:
            jokbo.agent_answered(target, p.get("session_id"), p)   # what its sub-agent did is now the session's (a background one's: at its notification)
        if p.get("hook_event_name") == "PostToolUseFailure":
            return 0   # a failed call's writes are still this session's; nothing was read to tell about
        inp = p.get("tool_input") or {}
        command = inp.get("command") if isinstance(inp.get("command"), str) else None
        rels = []
        if p.get("tool_name") == "Read" and inp.get("file_path"):
            full = os.path.realpath(os.path.join(p["cwd"], inp["file_path"]))
            if full.startswith(target + os.sep):
                rels = [os.path.relpath(full, target).replace(os.sep, "/")]
        elif command is not None and jokbo.SHELL_READ.search(command):
            rels = jokbo.shell_files(target, p["cwd"], command, jokbo.index(target)["files"])[1]
        lines = jokbo.tell(target, rels, p.get("session_id"), "read")
        if command is not None:
            lines += jokbo.searched(target, p.get("session_id"), command, cwd=p["cwd"])
        elif p.get("tool_name") == "Grep" and isinstance(inp.get("pattern"), str):
            where = os.path.realpath(os.path.join(p["cwd"], inp.get("path") or "."))
            if where == target or where.startswith(target + os.sep):
                lines += jokbo.searched(target, p.get("session_id"), pattern=inp["pattern"])
    except (SystemExit, OSError):   # a hook that cannot write its state says nothing; it never prints a traceback
        return 0
    _common.say("PostToolUse", lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
