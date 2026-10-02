"""PreToolUse on every tool — two things. What another hand changed since this session's last call (anything between
the last call's end and this call's start is not this session's doing), said in a line. And, for a write to a file the
session never looked at, the note the read hook would have given (it arrives with the result: it informs the next edit,
not this one). A shell command is read for the files it writes against the shell's directory. Never a permission
decision, never a block."""
import os
import re
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
        st, _ = jokbo.session_state(target, p.get("session_id"))
        now = jokbo.snapshot(target)
        if st.get("last"):
            lines += jokbo.change_report(target, st["last"], now)
        jokbo.remember(target, p.get("session_id"), now)
        inp = p.get("tool_input") or {}
        command = inp.get("command") if isinstance(inp.get("command"), str) else None
        rels = []
        if command is not None and (jokbo.SHELL_WRITE.search(command) or jokbo.SCRIPT_WRITE.search(command)):
            rels = jokbo.shell_files(target, p["cwd"], command, jokbo.index(target)["files"])[0]
        elif p.get("tool_name") in ("Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"):
            paths = [inp.get("file_path") or inp.get("notebook_path") or ""]
            if isinstance(inp.get("input"), str):   # Codex apply_patch
                paths = [m.strip() for m in re.findall(r"\*\*\* (?:Update|Add) File: (.+)", inp["input"])]
            for path in paths:
                full = os.path.realpath(os.path.join(p["cwd"], path)) if path else ""
                if full.startswith(target + os.sep):
                    rels.append(os.path.relpath(full, target).replace(os.sep, "/"))
        lines += jokbo.tell(target, rels, p.get("session_id"), "write")
    except SystemExit:
        return 0
    _common.say("PreToolUse", lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
