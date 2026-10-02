"""PreToolUse hook on Edit/Write and on Bash — before a file is written, whichever tool writes it: which documents speak of
it (ranked by shared words, with how current each is against the file), who last changed it, and what the declared
`file` reads say. The host delivers this with the tool's result, so it informs the next edit, not this one: the read hook
(post_read.py) is the one that arrives before an edit, when the file is first looked at; this one covers a file written
without being read. A shell command is read for the files it writes (`jokbo.shell_files`, against the shell's directory).
Each file once per session (shared with the read hook); a new file says nothing. Facts only: never blocks, never a
permission decision. Outside a git work tree, a record path, a worker session (AGENT_WORKER=1) or hunsu's probe
(HUNSU_SURVEY=1): silent. Writes only the cache (`.jokbo/`, ignored)."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import jokbo  # noqa: E402


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if os.environ.get("AGENT_WORKER") or os.environ.get("HUNSU_SURVEY"):
        return 0
    cwd = os.path.realpath(payload.get("cwd") or os.getcwd())
    inp = payload.get("tool_input") or {}
    command = inp.get("command") if isinstance(inp.get("command"), str) else None
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    if not path and isinstance(inp.get("input"), str):   # Codex apply_patch: the files the patch names
        path = " ".join(m.strip() for m in re.findall(r"\*\*\* (?:Update|Add) File: (.+)", inp["input"]))
    if not path and not command:
        return 0
    if command is not None and not (jokbo.SHELL_WRITE.search(command) or jokbo.SCRIPT_WRITE.search(command)):
        return 0   # most shell calls write nothing: say nothing, build nothing
    try:
        if jokbo.git(cwd, "rev-parse", "--is-inside-work-tree") is None:
            return 0
        target = jokbo.project_root(cwd)
        if command is not None:
            rels = jokbo.shell_files(target, cwd, command, jokbo.index(target)["files"])[0]
        else:
            rels = []
            for p in path.split(" ") if "\n" not in path and inp.get("input") else [path]:
                full = os.path.realpath(os.path.join(cwd, p))
                if full.startswith(target + os.sep):
                    rels.append(os.path.relpath(full, target).replace(os.sep, "/"))
        lines = jokbo.tell(target, rels, payload.get("session_id"), "write")
    except SystemExit:
        return 0
    if lines:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
