"""SessionStart hook — what changed since this machine last looked, as facts: commits and document sections. Never a
product's state (each product says its own at session start); never an instruction. Nothing new: silent. Where the machine last looked is marked when a session's answer ends (stop.py), so a resumed session
is not told its own commits back. A worker session
(AGENT_WORKER=1): silent — what it may read is its hirer's to say. Under hunsu's probe (HUNSU_SURVEY=1) it answers its
standing text and writes nothing: the probe ran it in guin-site and marked the machine as having looked, before any
session had. Otherwise writes only the cache (`.jokbo/`, ignored)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import jokbo  # noqa: E402

STANDING = ("jokbo: what changed since this machine last looked — commits and the document sections they changed, in brief; "
            "nothing when nothing changed. Before the first edit of each file, the edit hook names the documents that speak of it "
            "and how current each is. `jokbo.py file PATH`, `find WORDS`, `since [REV]` by hand.")


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if os.environ.get("AGENT_WORKER"):
        return 0
    if os.environ.get("HUNSU_SURVEY"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": STANDING}}))
        return 0
    cwd = os.path.realpath(payload.get("cwd") or os.getcwd())
    if jokbo.git(cwd, "rev-parse", "--is-inside-work-tree") is None:
        return 0
    target = jokbo.project_root(cwd)
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
        if not seen.get("head"):
            jokbo.mark_seen(target)   # the first look; after it, where it last looked is marked when an answer ends (stop.py)
    except SystemExit:
        return 0
    if msg:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
