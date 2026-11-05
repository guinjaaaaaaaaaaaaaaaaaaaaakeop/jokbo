"""UserPromptSubmit — before the session reads the prompt: what another hand changed while it waited (the owner, another
session, a worker), a line; and, when the prompt asks about something the project's documents already say, up to three of
their sentences, quoted (prompt_report — strict: most prompts get nothing). Each section is quoted once per session.
Nothing to say: nothing said."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
from _common import jokbo  # noqa: E402


def main():
    p, target = _common.payload()
    if not p:
        return 0
    try:
        st, _ = jokbo.session_state(target, p.get("session_id"))
        now = jokbo.snapshot(target)
        lines = jokbo.change_report(target, st.get("last"), now, own=jokbo.own_plugins(st)) \
            if st.get("last") and not jokbo.agents_in_flight(target) else []
        jokbo.background_ended(target, p.get("session_id"), p.get("prompt") or "")   # after its report: what it wrote is its own
        st = jokbo.remember(target, p.get("session_id"), now)
        said, ids = ([], []) if jokbo.host_notification(p.get("prompt")) else \
            jokbo.prompt_report(target, p.get("prompt") or "", told=st.get("quoted", []))   # a host's report asks nothing
        if ids:
            with jokbo.state_lock(jokbo.session_path(target, p.get("session_id"))):
                st, path = jokbo.session_state(target, p.get("session_id"))   # read again: a sub-agent's hooks may have written meanwhile
                st["quoted"] = st.get("quoted", []) + [i for i in ids if i not in st.get("quoted", [])]
                jokbo.save(path, st)
            lines += said
    except (SystemExit, OSError):
        return 0
    _common.say("UserPromptSubmit", lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
