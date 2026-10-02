"""Stop hook — where this machine last looked is where the session's last answer left the tree. Marked here, not at session
start: a session resumed after its own work was told its own commits back (guin-site, 10-02, twice). Never blocks,
says nothing, writes only `.jokbo/seen.json`. Silent in a worker session and under hunsu's probe."""
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
    if jokbo.git(cwd, "rev-parse", "--is-inside-work-tree") is None:
        return 0
    try:
        jokbo.mark_seen(jokbo.project_root(cwd))
    except (SystemExit, OSError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
