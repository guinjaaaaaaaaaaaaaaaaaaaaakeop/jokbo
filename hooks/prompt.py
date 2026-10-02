"""UserPromptSubmit — what another hand changed while the session waited (the owner, another session, a worker): a line,
before the session reads the prompt. Nothing changed: nothing said."""
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
        lines = jokbo.change_report(target, st.get("last"), now) if st.get("last") else []
        jokbo.remember(target, p.get("session_id"), now)
    except SystemExit:
        return 0
    _common.say("UserPromptSubmit", lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
