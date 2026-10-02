"""PostToolUse on every tool — the session's own call is over: what it changed is its own (remembered as where the tree
now stands). And at the first look at a file (Read; cat, head, tail, sed, awk, grep, rg, less, nl on a file), a line per
fact that bears on it — this arrives before the next call is decided, which is before an edit."""
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
    except SystemExit:
        return 0
    _common.say("PostToolUse", lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
