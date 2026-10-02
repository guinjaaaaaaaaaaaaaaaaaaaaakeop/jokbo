"""PreToolUse hook on Edit/Write and on Bash — before the first edit of a file in a session, whichever tool makes it: which
documents speak of it (ranked by shared words, with how current each is against the file), who last changed it, and what
the declared `file` reads say. A shell command is read for the project files it is about to write (`jokbo.bash_targets`);
at most two files are told in full per call, the rest named. Facts only, once per file per session; never blocks and never
asks (no permission decision). Outside the project, a record path, a worker session (AGENT_WORKER=1) or hunsu's probe
(HUNSU_SURVEY=1): silent. Writes only the cache (`.jokbo/`, ignored)."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import jokbo  # noqa: E402


FULL = 2   # files told in full per call; a move of a directory names the rest


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if os.environ.get("AGENT_WORKER") or os.environ.get("HUNSU_SURVEY"):
        return 0
    target = os.path.abspath(payload.get("cwd") or os.getcwd())
    inp = payload.get("tool_input") or {}
    command = inp.get("command") if isinstance(inp.get("command"), str) else None
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    if not path and isinstance(inp.get("input"), str):   # Codex apply_patch: the first file the patch names
        m = re.search(r"\*\*\* (?:Update|Add) File: (.+)", inp["input"])
        path = m.group(1).strip() if m else ""
    if not path and not command:
        return 0
    if command is not None and not (jokbo.SHELL_WRITE.search(command) or jokbo.SCRIPT_WRITE.search(command)):
        return 0   # most shell calls write nothing: say nothing, build nothing
    try:
        if jokbo.git(target, "rev-parse", "--is-inside-work-tree") is None:
            return 0
        if command is not None:
            rels = jokbo.bash_targets(target, command, jokbo.index(target)["files"])
        else:
            full = os.path.abspath(os.path.join(target, path))
            if not full.startswith(target + os.sep):
                return 0
            rels = [os.path.relpath(full, target).replace(os.sep, "/")]
        rels = [r for r in rels if not r.startswith(jokbo.record_paths(target) + (".git/",))]
        sid = re.sub(r"[^\w.-]", "_", str(payload.get("session_id") or "no-session"))
        shown_path = os.path.join(jokbo.cache_dir(target), "sessions", sid + ".json")
        shown = jokbo.load(shown_path, default=[])
        new = [r for r in rels if r not in shown]
        if not new:
            return 0
        lines = []
        for rel in new[:FULL]:
            lines.extend(jokbo.file_report(target, rel, brief=True))
        if len(new) > FULL:
            engine = os.path.join(os.path.dirname(HERE), "jokbo.py").replace(os.sep, "/")
            rest = new[FULL:]
            lines.append("jokbo — this also writes %d more file(s): %s%s — `%s \"%s\" file PATH` for any of them"
                         % (len(rest), ", ".join(rest[:8]), " …" if len(rest) > 8 else "", jokbo.PY, engine))
        jokbo.save(shown_path, shown + new)
    except SystemExit:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
