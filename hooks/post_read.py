"""PostToolUse hook on Read and on Bash — when the session first looks at a file: which documents speak of it, how current
each is, who last changed it, and what the declared `file` reads say. This is the moment that comes before an edit: an
agent reads a file before changing it, and context added to a read arrives before the next call is decided (context added
to an edit arrives with the edit's result — on guin-site, after every edit it was meant to inform). A shell command is read
for the files it shows (cat, head, tail, sed, awk, grep, rg, less, nl — files only, never a directory). Each file once per
session (shared with the edit hook); at most two told in full per call. Facts only. Silent outside a git work tree, for
record paths, in a worker session (AGENT_WORKER=1) and under hunsu's probe (HUNSU_SURVEY=1)."""
import json
import os
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
    path = inp.get("file_path") or ""
    if not path and not (command and jokbo.SHELL_READ.search(command)):
        return 0
    try:
        if jokbo.git(cwd, "rev-parse", "--is-inside-work-tree") is None:
            return 0
        target = jokbo.project_root(cwd)
        if command is not None:
            rels = jokbo.shell_files(target, cwd, command, jokbo.index(target)["files"])[1]
        else:
            full = os.path.realpath(os.path.join(cwd, path))
            rels = [os.path.relpath(full, target).replace(os.sep, "/")] if full.startswith(target + os.sep) else []
        lines = jokbo.tell(target, rels, payload.get("session_id"), "read")
    except SystemExit:
        return 0
    if lines:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
