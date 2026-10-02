"""PreToolUse hook on Edit/Write — before the first edit of a file in a session: which documents speak of it (ranked by
shared words, with how current each is against the file), who last changed it, and what the declared `file` reads say.
Facts only, once per file per session; never blocks and never asks (no permission decision). Outside the project, a
record path, or a worker session (AGENT_WORKER=1): silent. Writes only the cache (`.jokbo/`, ignored)."""
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
    if os.environ.get("AGENT_WORKER"):
        return 0
    target = os.path.abspath(payload.get("cwd") or os.getcwd())
    inp = payload.get("tool_input") or {}
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    if not path and isinstance(inp.get("input"), str):   # Codex apply_patch: the first file the patch names
        m = re.search(r"\*\*\* (?:Update|Add) File: (.+)", inp["input"])
        path = m.group(1).strip() if m else ""
    if not path:
        return 0
    full = os.path.abspath(os.path.join(target, path))
    if not full.startswith(target + os.sep):
        return 0
    rel = os.path.relpath(full, target).replace(os.sep, "/")
    if rel.startswith(jokbo.record_paths(target) + (".git/",)):
        return 0
    try:
        if jokbo.git(target, "rev-parse", "--is-inside-work-tree") is None:
            return 0
        sid = re.sub(r"[^\w.-]", "_", str(payload.get("session_id") or "no-session"))
        shown_path = os.path.join(jokbo.cache_dir(target), "sessions", sid + ".json")
        shown = jokbo.load(shown_path, default=[])
        if rel in shown:
            return 0
        lines = jokbo.file_report(target, rel, brief=True)
        jokbo.save(shown_path, shown + [rel])
    except SystemExit:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
