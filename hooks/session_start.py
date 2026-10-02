"""SessionStart hook — what changed since this machine last looked, as facts: commits and document sections. Never a
product's state (each product says its own at session start); never an instruction. Nothing new: silent. A worker session
(AGENT_WORKER=1): silent — what it may read is its hirer's to say. Writes only the cache (`.jokbo/`, ignored)."""
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
    if os.environ.get("AGENT_WORKER"):
        return 0
    target = os.path.abspath(payload.get("cwd") or os.getcwd())
    if jokbo.git(target, "rev-parse", "--is-inside-work-tree") is None:
        return 0
    try:
        seen = jokbo.load(jokbo.last_seen_path(target))
        head = (jokbo.git(target, "rev-parse", "HEAD") or "").strip()
        engine = os.path.join(os.path.dirname(HERE), "jokbo.py").replace(os.sep, "/")
        if not seen.get("head"):
            msg = ("jokbo: first look on this machine. Before editing a file, `%s \"%s\" file PATH` says which documents speak of it "
                   "and how current they are (the edit hook says it too); `find WORDS` says where the project speaks of something; "
                   "`since [REV]` what changed." % (jokbo.PY, engine))
        elif seen["head"] == head:
            msg = None
        else:
            msg = "\n".join(jokbo.since_report(target, seen["head"], brief=True)) + "\n(`%s \"%s\" since %s` for all of it)" % (jokbo.PY, engine, seen["head"][:7])
        jokbo.mark_seen(target)
    except SystemExit:
        return 0
    if msg:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
