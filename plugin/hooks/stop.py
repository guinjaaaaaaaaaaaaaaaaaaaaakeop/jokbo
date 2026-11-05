"""Stop — an answer ended: where this machine last looked is here (a resumed session is not told its own commits), and
so is where this session's calls left the tree (what changes before the next prompt is another hand's). Says nothing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
from _common import jokbo  # noqa: E402


def main():
    p, target = _common.payload()
    if not p:
        return 0
    try:
        jokbo.mark_seen(target)
        jokbo.remember(target, p.get("session_id"))
    except (SystemExit, OSError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
