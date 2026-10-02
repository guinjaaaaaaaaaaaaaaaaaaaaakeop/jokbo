"""jokbo self-checks — standard library only: `python3 test_jokbo.py`."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jokbo  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1))


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = jokbo.main(list(argv))
        except SystemExit as err:
            code = err.code if isinstance(err.code, int) else 1
            out.write(str(err) + "\n")
    return code, out.getvalue()


def hook(name, payload, **env):
    done = subprocess.run([sys.executable, os.path.join(HERE, "hooks", name)], input=json.dumps(payload), capture_output=True, text=True,
                          encoding="utf-8", env=dict({k: v for k, v in os.environ.items() if k != "AGENT_WORKER"}, **env))
    return done.returncode, done.stdout


class Repo:
    """A project with a plan, a README, code and two commits — the backend's contract written after the code it names."""
    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="jokbo-test-")
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        write(self.p("plan/PLAN.md"), "# Plan\n\nThe site.\n\n## Q-feed — the front page\n\nEvery post, newest first, by its `written` time. "
              "A short post shows its whole body; a long one shows its title.\n\n```\n# not a heading\n```\n\n## Q-tags — tagging\n\n"
              "Tags live in `content/tags.jsonl`: post, tag, action (added or removed), at, why.\n")
        write(self.p("README.md"), "# Site\n\nRun `npm run build`.\n")
        write(self.p("src/feed.ts"), "/** The front page: posts newest first by written time. */\nexport function feedPosts(posts) {\n"
              "  return posts.sort((a, b) => b.written.localeCompare(a.written)).map(p => p.short ? p.body : p.title);\n}\n")
        write(self.p("src/tags.ts"), "export function tagRows(rows) {\n  // action is added or removed\n  return rows.filter(r => r.action === 'added');\n}\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "the plan and the code", env_date="2026-09-01T00:00:00Z")
        write(self.p("src/feed.ts"), self.read("src/feed.ts") + "export const LIMIT = 10;\n")
        self.git("commit", "-qam", "feed: a limit", env_date="2026-09-02T00:00:00Z")
        return self

    def __exit__(self, *a):
        shutil.rmtree(self.dir, ignore_errors=True)

    def p(self, rel):
        return os.path.join(self.dir, rel)

    def read(self, rel):
        return io.open(self.p(rel), encoding="utf-8").read()

    def git(self, *a, env_date=None):
        env = dict(os.environ)
        if env_date:
            env.update(GIT_AUTHOR_DATE=env_date, GIT_COMMITTER_DATE=env_date)
        return subprocess.run(["git", *a], cwd=self.dir, check=True, capture_output=True, text=True, env=env).stdout


def test_words_split_identifiers_and_keep_hangul():
    w = jokbo.words("feedPosts written_time 관리 화면 a tagRows HTTPServer")
    for t in ("feed", "posts", "written", "time", "관리", "화면", "tag", "rows", "http", "server"):
        assert t in w, (t, w)
    assert "a" not in w


def test_sections_cut_at_headings_not_inside_fenced_code():
    with Repo() as r:
        secs = jokbo.sections_of("plan/PLAN.md", r.read("plan/PLAN.md"))
        ids = [s["id"] for s in secs]
        assert ids == ["plan/PLAN.md#Plan", "plan/PLAN.md#Q-feed — the front page", "plan/PLAN.md#Q-tags — tagging"], ids
        feed = secs[1]
        assert "# not a heading" in feed["text"] and feed["start"] == 5, feed


def test_file_puts_the_section_that_speaks_of_it_first_and_says_it_changed_after():
    """The front page's section is the one for src/feed.ts; the file changed after that section was written, so the
    reader is told to read it against the code. The last commits that changed the file are named. Nobody confirmed the
    ranking, and the output says so."""
    with Repo() as r:
        code, out = run("file", "src/feed.ts", "--target", r.dir)
        assert code == 0, out
        lines = out.splitlines()
        first = next(l for l in lines if l.startswith("  plan/") or l.startswith("  README"))
        assert "Q-feed" in first and "the file changed after (2026-09-02 00:00Z)" in first and "written 2026-09-01 00:00Z" in first, out
        assert "nobody confirmed" in out and "feed: a limit" in out, out
        code, out = run("file", "src/tags.ts", "--target", r.dir)
        first = next(l for l in out.splitlines() if l.startswith("  plan/"))
        assert "Q-tags" in first and "changed after" not in first, out


def test_a_section_being_edited_says_so_and_skip_sets_documents_aside():
    with Repo() as r:
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md").replace("newest first", "newest first, ten at most"))
        out = run("file", "src/feed.ts", "--target", r.dir)[1]
        assert "being edited (uncommitted)" in out, out
        write(r.p("hunsu.json"), {"settings": {"jokbo": {"skip": ["plan/"]}}})
        out = run("file", "src/feed.ts", "--target", r.dir)[1]
        assert "plan/PLAN.md" not in out, out


def test_records_the_lock_declares_are_not_documents():
    with Repo() as r:
        write(r.p("notes/run.md"), "# a run record\n\nfeed posts written newest first front page body title\n")
        write(r.p("hunsu.lock.json"), {"record-paths": {"someplugin": ["notes/"]}})
        out = run("file", "src/feed.ts", "--target", r.dir)[1]
        assert "notes/run.md" not in out, out
        assert not any(f.startswith("notes/") for f in jokbo.index(r.dir)["files"])


def test_since_names_commits_and_the_sections_that_changed():
    with Repo() as r:
        base = r.git("rev-parse", "HEAD").strip()
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md").replace("action (added or removed)", "action (added, removed or renamed)"))
        r.git("commit", "-qam", "tags: renames")
        code, out = run("since", base, "--target", r.dir)
        assert code == 0 and "1 commit(s) since %s" % base[:7] in out and "tags: renames" in out, out
        assert "plan/PLAN.md#Q-tags — tagging" in out and "Q-feed" not in out.split("document sections changed:")[1], out


def test_find_reaches_documents_the_codes_own_words_and_commits():
    with Repo() as r:
        out = run("find", "front", "page", "newest", "--target", r.dir)[1]
        assert "plan/PLAN.md#Q-feed — the front page" in out, out
        assert "src/feed.ts:1 — The front page" in out, out
        out = run("find", "limit", "--target", r.dir)[1]
        assert "feed: a limit" in out, out


def test_a_declared_read_is_run_and_labelled_and_a_missing_plugin_is_said():
    """The overlay: the lock's `reads` (hunsu.json's on top) are run with their placeholders and printed under the plugin's
    name. jokbo knows no product: the plugin here is a script that echoes what it was given."""
    with Repo() as r:
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            write(os.path.join(plug, "echo.py"), "import sys\nprint('relations on ' + sys.argv[1])\nprint('  concept:feed — the front page')\n")
            write(r.p("hunsu.local.json"), {"links": {"netty": plug}})
            write(r.p("hunsu.lock.json"), {"reads": {"netty": {"file": ["python3", "{plugin:netty}/echo.py", "{path}"],
                                                                "decision": ["python3", "{plugin:netty}/echo.py", "all"]},
                                                      "gone": {"file": ["python3", "{plugin:gone}/x.py", "{path}"]}}})
            out = run("file", "src/feed.ts", "--target", r.dir)[1]
            assert "netty (declared `file` read):" in out and "relations on src/feed.ts" in out, out
            assert "gone (declared `file` read): not read — plugin gone is not installed or linked here" in out, out
            out = run("find", "front", "--target", r.dir)[1]
            assert "netty (declared `decision` read)" in out and "concept:feed — the front page" in out, out
            out = run("index", "--target", r.dir)[1]
            assert "declared reads: gone(file), netty(decision,file)" in out, out
            write(os.path.join(plug, "echo.py"), "import sys\n")   # a read with nothing on this file
            out = run("file", "src/feed.ts", "--target", r.dir)[1]
            assert "netty" not in out, "nothing to say: no empty heading"
        finally:
            shutil.rmtree(plug, ignore_errors=True)


def test_the_cache_is_rebuilt_when_the_tree_moves_and_ignores_itself():
    with Repo() as r:
        a = jokbo.index(r.dir)
        assert jokbo.index(r.dir)["built"] == a["built"]
        assert io.open(r.p(".jokbo/.gitignore")).read() == "*\n"
        assert r.git("status", "--porcelain") == "", "the cache leaves the tree clean"
        write(r.p("plan/NEW.md"), "# New\n\nsomething new about tags and feed\n")
        b = jokbo.index(r.dir)
        assert any(s["file"] == "plan/NEW.md" for s in b["sections"]), "an untracked document is read too"


def test_the_edit_hook_speaks_once_per_file_per_session_and_never_decides():
    with Repo() as r:
        payload = {"cwd": r.dir, "session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": r.p("src/feed.ts")}}
        code, out = hook("pre_write.py", payload)
        doc = json.loads(out)["hookSpecificOutput"]
        assert code == 0 and doc["hookEventName"] == "PreToolUse" and "Q-feed" in doc["additionalContext"], out
        assert "permissionDecision" not in doc, "context only: the permission flow is the host's"
        assert hook("pre_write.py", payload) == (0, ""), "the same file again in the same session: said already"
        assert "Q-feed" in hook("pre_write.py", dict(payload, session_id="s2"))[1], "a new session hears it again"
        assert hook("pre_write.py", dict(payload, session_id="s3"), AGENT_WORKER="1") == (0, ""), "a worker session: silent"
        assert hook("pre_write.py", dict(payload, tool_input={"file_path": "/elsewhere/x.py"})) == (0, ""), "outside the project"
        assert hook("pre_write.py", dict(payload, tool_input={"file_path": r.p(".jokbo/index.json")})) == (0, ""), "its own cache"


def test_the_session_start_line_says_what_changed_since_this_machine_looked():
    with Repo() as r:
        code, out = hook("session_start.py", {"cwd": r.dir})
        assert "first look on this machine" in json.loads(out)["hookSpecificOutput"]["additionalContext"], out
        assert hook("session_start.py", {"cwd": r.dir}) == (0, ""), "nothing new: silent"
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\n## Q-about\n\nOne page about the author.\n")
        r.git("commit", "-qam", "about page")
        ctx = json.loads(hook("session_start.py", {"cwd": r.dir})[1])["hookSpecificOutput"]["additionalContext"]
        assert "1 commit(s) since" in ctx and "about page" in ctx and "plan/PLAN.md#Q-about" in ctx, ctx
        assert hook("session_start.py", {"cwd": r.dir}, AGENT_WORKER="1") == (0, "")


def test_not_a_git_tree_is_said_not_guessed():
    d = tempfile.mkdtemp(prefix="jokbo-nogit-")
    try:
        code, out = run("file", "x.py", "--target", d)
        assert code != 0 and "not a git work tree" in out, out
        assert hook("session_start.py", {"cwd": d}) == (0, "")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_shell_command_that_writes_is_an_edit_too():
    """guin-site, 10-02: 102 Bash calls and no Edit — the edit hook never fired. A shell command is read for the project
    files it is about to write: redirects, mv/cp/rm/tee/`git mv`/`sed -i` operands, and the files an inline script that
    writes names. Reads and writes outside the project say nothing; a moved directory tells two files and names the rest."""
    with Repo() as r:
        files = jokbo.index(r.dir)["files"]
        bt = lambda c: jokbo.bash_targets(r.dir, c, files)
        assert bt("cat src/feed.ts > /tmp/x") == [] and bt("grep -rn feed src | head") == [] and bt("npm run build > /dev/null 2>&1") == []
        assert bt("sed -i '' 's/10/20/' src/feed.ts") == ["src/feed.ts"]
        assert bt("python3 - <<'E'\nopen('plan/PLAN.md', 'w').write('x')\nE") == ["plan/PLAN.md"]
        assert bt("echo x >> README.md && git add -A") == ["README.md"]
        assert bt("git mv src lib") == ["src/feed.ts", "src/tags.ts"]
        payload = {"cwd": r.dir, "session_id": "b1", "tool_name": "Bash", "tool_input": {"command": "sed -i '' 's/10/20/' src/feed.ts"}}
        ctx = json.loads(hook("pre_write.py", payload)[1])["hookSpecificOutput"]["additionalContext"]
        assert "jokbo — src/feed.ts" in ctx and "Q-feed" in ctx, ctx
        assert hook("pre_write.py", dict(payload, tool_input={"command": "ls src"})) == (0, ""), "writes nothing: silent"
        for n in range(3):
            write(r.p("docs/n%d.md" % n), "# note %d\n\nabout feed\n" % n)
        ctx = json.loads(hook("pre_write.py", dict(payload, session_id="b2", tool_input={"command": "git mv docs notes"}))[1])["hookSpecificOutput"]["additionalContext"]
        assert ctx.count("jokbo — docs/") == 2 and "this also writes 1 more file(s): docs/n2.md" in ctx, ctx


def test_under_hunsus_probe_the_hooks_say_their_standing_text_and_write_nothing():
    """hunsu runs a SessionStart hook to judge what it says (HUNSU_SURVEY=1). In guin-site that run marked the machine as
    having looked before any session had. Under the probe: the standing text, and nothing written."""
    with Repo() as r:
        code, out = hook("session_start.py", {"cwd": r.dir}, HUNSU_SURVEY="1")
        assert "what changed since this machine last looked" in json.loads(out)["hookSpecificOutput"]["additionalContext"], out
        assert not os.path.exists(r.p(".jokbo")), "the probe wrote nothing"
        payload = {"cwd": r.dir, "session_id": "p1", "tool_name": "Edit", "tool_input": {"file_path": r.p("src/feed.ts")}}
        assert hook("pre_write.py", payload, HUNSU_SURVEY="1") == (0, "")
        assert "first look" in json.loads(hook("session_start.py", {"cwd": r.dir})[1])["hookSpecificOutput"]["additionalContext"]


def test_a_korean_document_name_is_read_as_itself():
    with Repo() as r:
        write(r.p("plan/계획.md"), "# 관리 화면\n\n초안 게시 관리 화면 feed posts written\n")
        r.git("add", "-A"); r.git("commit", "-qm", "계획")
        idx = jokbo.index(r.dir)
        assert any(s["id"] == "plan/계획.md#관리 화면" for s in idx["sections"]), [s["id"] for s in idx["sections"]]
        assert any("plan/계획.md" in c["files"] for c in idx["commits"])


def test_a_document_being_edited_is_told_the_other_documents_and_the_files_it_names():
    """Editing README: its own sections are open already; what it needs is where else the same things are said, and the
    files it names with when each changed — guin-site's rejects on 10-02 were documents disagreeing with each other and
    with the code. The README names src/feed.ts, which changed after the README was written."""
    with Repo() as r:
        write(r.p("README.md"), "# Site\n\nThe front page lists every post newest first by written time: see `src/feed.ts`.\n")
        r.git("commit", "-qam", "readme", env_date="2026-09-01T12:00:00Z")
        write(r.p("src/feed.ts"), r.read("src/feed.ts") + "export const MORE = 1;\n")
        r.git("commit", "-qam", "feed: more", env_date="2026-09-03T00:00:00Z")
        out = run("file", "README.md", "--target", r.dir)[1]
        assert "other documents that say the same things" in out and "plan/PLAN.md#Q-feed" in out, out
        assert "  README.md#" not in out, "its own sections are not offered back"
        assert "files it names (1):" in out and "src/feed.ts — changed 2026-09-03 00:00Z, after this document" in out, out


def test_a_picture_is_matched_by_its_name_not_its_bytes():
    with Repo() as r:
        os.makedirs(r.p("assets"))
        with io.open(r.p("assets/front-page.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n\x00\x00feedPosts written tagRows" * 50)
        assert not jokbo.is_text(r.dir, "assets/front-page.png")
        out = run("file", "assets/front-page.png", "--target", r.dir)[1]
        assert "Q-feed" in out, "the name 'front page' is what it is matched by"


def test_a_change_to_one_file_reblames_only_that_document():
    with Repo() as r:
        jokbo.index(r.dir)
        calls = []
        real = jokbo.blame_times
        jokbo.blame_times = lambda t, p: calls.append(p) or real(t, p)
        try:
            write(r.p("src/feed.ts"), r.read("src/feed.ts") + "// more\n")
            jokbo.index(r.dir)
            assert calls == [], calls
            write(r.p("README.md"), r.read("README.md") + "\nMore.\n")
            jokbo.index(r.dir)
            assert calls == ["README.md"], calls
        finally:
            jokbo.blame_times = real


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except (Exception, SystemExit) as err:
                failed += 1
                print("FAIL", name, "--", "%s: %s" % (type(err).__name__, err))
    print("all passed" if not failed else "%d failed" % failed)
    sys.exit(1 if failed else 0)
