"""What every jokbo hook does first: read the payload, step aside where it must, find the project."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import jokbo  # noqa: E402,F401


def payload():
    """(payload, target) — or (None, None) when the hook has nothing to do: no payload, a worker session (AGENT_WORKER=1:
    what it may read is its hirer's to say), hunsu's probe (HUNSU_SURVEY=1), or no git work tree."""
    try:
        p = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return None, None
    if os.environ.get("AGENT_WORKER") or os.environ.get("HUNSU_SURVEY"):
        return None, None
    cwd = os.path.realpath(p.get("cwd") or os.getcwd())
    if jokbo.git(cwd, "rev-parse", "--is-inside-work-tree") is None:
        return None, None
    p["cwd"] = cwd
    return p, jokbo.project_root(cwd)


def say(event, lines):
    if lines:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(lines)}}))
