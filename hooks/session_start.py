"""SessionStart — the map: what this project holds and how to ask (frozen for the session), and, in brief, what changed
since this machine last looked (marked when an answer ends, so a session's own work is not news). Starts the session's
state over: a file told before a restart is told again (1.2.0 carried 1.1.0's list over a restart and never spoke of the
files most edited). Also after a compaction — the summary keeps none of the map, and a session in guin-site (10-08 → 10-11)
compacted four times without it; a sub-agent can still be running then, so its count is kept, and so is where the
session started (the notes leave out what was committed since). Under hunsu's probe
(HUNSU_SURVEY=1): its standing text, nothing written."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _common  # noqa: E402
from _common import jokbo  # noqa: E402

STANDING = ("jokbo: at session start, a map of what this project holds (documents, and a line from each product that declares "
            "one) and how to ask; at the first look at a file, a line per fact that bears on it; when another hand changes the "
            "project between this session's calls, a line saying what. `jokbo.py find WORDS`, `since [REV]`, `file PATH` by hand.")


def main():
    if os.environ.get("HUNSU_SURVEY") and not os.environ.get("AGENT_WORKER"):
        _common.say("SessionStart", [STANDING])
        return 0
    p, target = _common.payload()
    if not p:
        return 0
    try:
        lines = jokbo.map_report(target)
        seen = jokbo.load(jokbo.last_seen_path(target))
        head = (jokbo.git(target, "rev-parse", "HEAD") or "").strip()
        if not seen.get("head"):
            jokbo.mark_seen(target)
        elif seen["head"] != head and p.get("source") != "compact":   # a compaction comes mid-answer: what changed is the session's own
            lines += jokbo.since_report(target, seen["head"], brief=True)
        st = jokbo.remember(target, p.get("session_id"), reset=True, keep=("start",) if p.get("source") == "compact" else ())
        if not st.get("start") and head:   # where this session started: what is committed after it is the session's own news
            st["start"] = head
            jokbo.save(jokbo.session_state(target, p.get("session_id"))[1], st)
        jokbo.agents_in_flight(target, p.get("session_id"), reset=p.get("source") != "compact")
    except (SystemExit, OSError):
        return 0
    _common.say("SessionStart", lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
