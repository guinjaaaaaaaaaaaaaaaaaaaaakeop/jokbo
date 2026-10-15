"""jokbo self-checks — standard library only: `python3 test_jokbo.py`."""
import calendar
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
        code, out = hook("pre_tool.py", payload)
        doc = json.loads(out)["hookSpecificOutput"]
        assert code == 0 and doc["hookEventName"] == "PreToolUse" and "Q-feed" in doc["additionalContext"], out
        assert "permissionDecision" not in doc, "context only: the permission flow is the host's"
        assert hook("pre_tool.py", payload) == (0, ""), "the same file again in the same session: said already"
        assert "Q-feed" in hook("pre_tool.py", dict(payload, session_id="s2"))[1], "a new session hears it again"
        assert hook("pre_tool.py", dict(payload, session_id="s3"), AGENT_WORKER="1") == (0, ""), "a worker session: silent"
        assert hook("pre_tool.py", dict(payload, tool_input={"file_path": "/elsewhere/x.py"})) == (0, ""), "outside the project"
        assert hook("pre_tool.py", dict(payload, tool_input={"file_path": r.p(".jokbo/index.json")})) == (0, ""), "its own cache"


def test_the_session_start_line_is_a_map_and_what_changed_since_this_machine_looked():
    """The map: what the project holds and how to ask — not the content. Then, in brief, what changed since this machine
    last looked."""
    with Repo() as r:
        code, out = hook("session_start.py", {"cwd": r.dir})
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "what this project holds, and how to ask" in ctx and "find WORDS" in ctx, ctx
        # the documents as a reader scans them: headings with line ranges, a lone top heading as the title (guin-site 10-08)
        # … and each one's last change, compact (10-15: "newest" could not be seen)
        d = jokbo.day(calendar.timegm((2026, 9, 1, 0, 0, 0)))
        assert "plan/PLAN.md — Plan (3 section(s), last %s):\n    5-12  Q-feed — the front page  %s\n    13-16  Q-tags — tagging  %s" % (d, d, d) in ctx, ctx
        assert "commit(s) since" not in json.loads(hook("session_start.py", {"cwd": r.dir})[1])["hookSpecificOutput"]["additionalContext"], "nothing new"
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
        ctx = json.loads(hook("pre_tool.py", payload)[1])["hookSpecificOutput"]["additionalContext"]
        assert "jokbo — src/feed.ts:" in ctx and "\u201cEvery post, newest first, by its `written` time.\u201d" in ctx, ctx
        assert hook("pre_tool.py", dict(payload, tool_input={"command": "ls src"})) == (0, ""), "writes nothing: silent"
        for n in range(3):
            write(r.p("docs/n%d.md" % n), "# note %d\n\nThe front page shows every post newest first by written time, note %d.\n" % (n, n))
        out = hook("pre_tool.py", dict(payload, session_id="b2", tool_input={"command": "git mv docs notes"}))[1]
        assert out.count("jokbo — docs/") <= 2, out


def test_under_hunsus_probe_the_hooks_say_their_standing_text_and_write_nothing():
    """hunsu runs a SessionStart hook to judge what it says (HUNSU_SURVEY=1). In guin-site that run marked the machine as
    having looked before any session had. Under the probe: the standing text, and nothing written."""
    with Repo() as r:
        code, out = hook("session_start.py", {"cwd": r.dir}, HUNSU_SURVEY="1")
        assert "a map of what this project holds" in json.loads(out)["hookSpecificOutput"]["additionalContext"], out
        assert not os.path.exists(r.p(".jokbo")), "the probe wrote nothing"
        payload = {"cwd": r.dir, "session_id": "p1", "tool_name": "Edit", "tool_input": {"file_path": r.p("src/feed.ts")}}
        assert hook("pre_tool.py", payload, HUNSU_SURVEY="1") == (0, "")
        assert "how to ask" in json.loads(hook("session_start.py", {"cwd": r.dir})[1])["hookSpecificOutput"]["additionalContext"]


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


def test_the_first_look_at_a_file_is_when_it_is_told_and_its_edit_is_not_told_again():
    """Context added to an edit arrives with the edit's result; context added to a read arrives before the next call. The
    read hook tells a file when the session first looks at it (Read, or cat/sed/grep in a shell); its edit then says
    nothing more."""
    with Repo() as r:
        read = {"cwd": r.dir, "session_id": "r1", "tool_name": "Bash", "tool_input": {"command": "sed -n '1,5p' src/feed.ts"}}
        doc = json.loads(hook("post_tool.py", read)[1])["hookSpecificOutput"]
        assert doc["hookEventName"] == "PostToolUse" and "jokbo — src/feed.ts" in doc["additionalContext"] and "Q-feed" in doc["additionalContext"], doc
        edit = {"cwd": r.dir, "session_id": "r1", "tool_name": "Edit", "tool_input": {"file_path": r.p("src/feed.ts")}}
        assert hook("pre_tool.py", edit) == (0, ""), "told at the read: not again at the edit"
        assert "Q-tags" in json.loads(hook("post_tool.py", dict(read, tool_input={"file_path": r.p("src/tags.ts")}, tool_name="Read"))[1])["hookSpecificOutput"]["additionalContext"]
        assert hook("post_tool.py", dict(read, tool_input={"command": "ls src && git status"})) == (0, ""), "no file shown: silent"
        assert hook("post_tool.py", dict(read, session_id="r2", tool_input={"command": "grep -rn feed src"})) == (0, ""), \
            "a search through code is a code search: no answer, and not a look at one file (guin-site 10-12: 11 such answers, none cited)"
        searched = json.loads(hook("post_tool.py", dict(read, session_id="r3", tool_input={"command": "grep -rn feed plan"}))[1])["hookSpecificOutput"]["additionalContext"]
        assert "jokbo — plan/" not in searched and "where the project speaks of: feed" in searched, "a search of the documents is a question"


def test_the_shell_directory_is_followed_and_the_project_root_kept():
    """A hook's cwd is the shell's: in tests/ a command naming `test_bots.py` once became a file of its own and a second
    cache appeared in tests/. Paths are read against the shell's directory (and its `cd`s); the cache is the project's."""
    with Repo() as r:
        files = jokbo.index(r.dir)["files"]
        root = jokbo.project_root(r.p("src"))
        assert root == os.path.realpath(r.dir)
        assert jokbo.shell_files(root, r.p("src"), "sed -i '' 's/a/b/' feed.ts", files)[0] == ["src/feed.ts"]
        assert jokbo.shell_files(root, r.dir, "cd src && cat tags.ts > /tmp/x", files) == ([], ["src/tags.ts"])
        payload = {"cwd": r.p("src"), "session_id": "c1", "tool_name": "Bash", "tool_input": {"command": "echo // x >> feed.ts"}}
        assert "jokbo — src/feed.ts" in json.loads(hook("pre_tool.py", payload)[1])["hookSpecificOutput"]["additionalContext"]
        assert not os.path.exists(r.p("src/.jokbo")), "one cache, at the project's root"


def test_what_a_shell_command_writes_is_read_closely():
    with Repo() as r:
        files = jokbo.index(r.dir)["files"]
        w = lambda c: jokbo.shell_files(r.dir, r.dir, c, files)[0]
        assert w("cp -r src /tmp/copy") == [], "a copy's sources are read, not written"
        assert w("cp plan/PLAN.md README.md") == ["README.md"]
        assert w("git add src && git commit -qm 'feed > tags: src/feed.ts'") == [], "a > inside quotes is not a redirect"
        script = "python3 - <<'E'\nfrom pathlib import Path\nsrc = Path('plan/PLAN.md').read_text()\np = Path('README.md')\np.write_text(src)\nE"
        assert w(script) == ["README.md"], "the file it reads is not written"
        loop = "python3 - <<'E'\nfrom pathlib import Path\nfiles = {'src/feed.ts': 'a', 'src/tags.ts': 'b'}\nfor path, text in files.items():\n    Path(path).write_text(text)\nE"
        assert w(loop) == ["src/feed.ts", "src/tags.ts"], "a dict of paths written in a loop"


def test_a_new_file_and_a_file_nothing_speaks_of_say_nothing():
    with Repo() as r:
        write(r.p("src/new.ts"), "export const x = 1;\n")
        payload = {"cwd": r.dir, "session_id": "n1", "tool_name": "Write", "tool_input": {"file_path": r.p("src/brand-new.ts")}}
        assert hook("pre_tool.py", payload) == (0, ""), "not in the tree yet: nothing to say"
        write(r.p("zz/qq.bin"), "\x00\x01")
        assert hook("post_tool.py", dict(payload, tool_name="Read", tool_input={"file_path": r.p("zz/qq.bin")})) == (0, "")


def test_a_declared_read_is_told_in_brief_within_a_size():
    with Repo() as r:
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            write(os.path.join(plug, "long.py"), "for i in range(40):\n    print('run-%d ' % i + 'x' * 500)\n")
            write(r.p("hunsu.local.json"), {"links": {"big": plug}})
            write(r.p("hunsu.lock.json"), {"reads": {"big": {"file": ["python3", "{plugin:big}/long.py", "{path}"]}}})
            text = "\n".join(jokbo.file_report(r.dir, "src/feed.ts", brief=True))
            part = text.split("big (declared `file` read):")[1]
            assert len(part) < jokbo.BRIEF_CHARS + 600 and "prints all 40 line(s)" in part, len(part)
        finally:
            shutil.rmtree(plug, ignore_errors=True)


def test_where_the_machine_last_looked_is_marked_when_an_answer_ends():
    """A resumed session was told its own commits back. The session-start line counts from where the last answer ended."""
    with Repo() as r:
        hook("session_start.py", {"cwd": r.dir})
        write(r.p("src/feed.ts"), r.read("src/feed.ts") + "// mine\n"); r.git("commit", "-qam", "my own work")
        assert hook("stop.py", {"cwd": r.dir}) == (0, "")
        assert "my own work" not in hook("session_start.py", {"cwd": r.dir})[1], "the session's own commit is not news"
        write(r.p("src/tags.ts"), r.read("src/tags.ts") + "// theirs\n"); r.git("commit", "-qam", "someone else's work")
        ctx = json.loads(hook("session_start.py", {"cwd": r.dir})[1])["hookSpecificOutput"]["additionalContext"]
        assert "someone else's work" in ctx and "my own work" not in ctx, ctx


def test_what_another_hand_changes_between_the_sessions_calls_is_said_once():
    """What the session does inside its own calls is its own; what changes between them — the owner, another session, a
    worker — is said in a line at the next prompt or call, grouped by whose records the paths are (the lock's)."""
    with Repo() as r:
        write(r.p("hunsu.lock.json"), {"record-paths": {"netty": ["net/"]}})
        r.git("add", "-A"); r.git("commit", "-qm", "lock")
        hook("session_start.py", {"cwd": r.dir, "session_id": "x1"})
        call = {"cwd": r.dir, "session_id": "x1", "tool_name": "Bash", "tool_input": {"command": "echo // mine >> src/tags.ts"}}
        assert hook("pre_tool.py", call) == (0, "") or "changed by another hand" not in hook("pre_tool.py", call)[1]
        write(r.p("src/tags.ts"), r.read("src/tags.ts") + "// mine\n")   # what the call itself did
        hook("post_tool.py", call)
        assert "changed by another hand" not in hook("prompt.py", {"cwd": r.dir, "session_id": "x1"})[1], "its own change is not news"
        write(r.p("net/concepts/feed.json"), {"means": "the front page"})   # the owner, between prompts
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\n## Q-about\n\nOne page.\n")
        r.git("add", "-A"); r.git("commit", "-qm", "owner: about page and the feed concept")
        ctx = json.loads(hook("prompt.py", {"cwd": r.dir, "session_id": "x1"})[1])["hookSpecificOutput"]["additionalContext"]
        assert "1 commit(s): " in ctx and "owner: about page" in ctx and "netty's records: net/concepts/feed.json" in ctx and "documents: plan/PLAN.md" in ctx, ctx
        assert "changed by another hand" not in hook("prompt.py", {"cwd": r.dir, "session_id": "x1"})[1], "said once"


def test_a_note_is_what_the_products_say_of_the_file_and_the_map_what_they_hold():
    with Repo() as r:
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            write(os.path.join(plug, "note.py"), "import sys\nif sys.argv[1] == 'src/feed.ts':\n    print('concept:feed — the front page, newest first')\n")
            write(os.path.join(plug, "map.py"), "print('2 concepts: feed, tag ; 1 open question')\n")
            write(r.p("hunsu.local.json"), {"links": {"netty": plug}})
            write(r.p("hunsu.lock.json"), {"reads": {"netty": {"note": ["python3", "{plugin:netty}/note.py", "{path}"], "map": ["python3", "{plugin:netty}/map.py"]}}})
            assert jokbo.note_report(r.dir, "src/feed.ts") == ["jokbo — src/feed.ts:", "  netty: concept:feed — the front page, newest first"]
            tags = jokbo.note_report(r.dir, "src/tags.ts")
            assert tags and "Q-tags" in tags[1] and "\u201c" in tags[1], "no product speaks of it: the document sentence"
            assert "  netty: 2 concepts: feed, tag ; 1 open question" in jokbo.map_report(r.dir)
        finally:
            shutil.rmtree(plug, ignore_errors=True)


def test_a_prompt_about_what_the_documents_say_is_answered_and_talk_is_not():
    """guin-site, 10-08 -> 10-11: three days, ~70 prompts, and the reader was never asked — the session grepped or answered
    from memory. The prompt hook quotes what the documents already say when a prompt asks about it — strictly: the
    sentence shares three of the prompt's words, two of them rare; "go on", a pasted log and a musing say nothing; a
    section is quoted once per session."""
    with Repo() as r:
        write(r.p("plan/BACKEND.md"), "# Backend\n\n## Storage\n\nDrafts are kept in the database; nothing is published until the owner presses publish.\n\n"
              "## Backup\n\nA nightly snapshot goes to the bucket at three in the morning.\n\n## Deploy\n\nPush to main and the worker is deployed.\n")
        r.git("add", "-A")
        r.git("commit", "-qm", "backend plan")
        said, ids = jokbo.prompt_report(r.dir, "when does the nightly snapshot go to the bucket?")
        assert ids == ["plan/BACKEND.md#Backup"] and "\u201cA nightly snapshot goes to the bucket at three in the morning.\u201d" in said[1], said
        assert "find WORDS" in said[0]
        for quiet in ("ㄱㄱ", "go ahead", "<pasted_content id=\"1\">nightly snapshot bucket morning</pasted_content id=\"1\"> what is this?",
                      "tags feed posts written body title action"):
            assert jokbo.prompt_report(r.dir, quiet) == ([], []), quiet
        assert jokbo.prompt_report(r.dir, "when does the nightly snapshot go to the bucket?", told=ids) == ([], []), "once per session"
        payload = {"cwd": r.dir, "session_id": "p1", "prompt": "when does the nightly snapshot go to the bucket?"}
        hook("session_start.py", {"cwd": r.dir, "session_id": "p1", "source": "startup"})
        assert "nightly snapshot" in json.loads(hook("prompt.py", payload)[1])["hookSpecificOutput"]["additionalContext"]
        assert hook("prompt.py", payload) == (0, ""), "the same section is not quoted twice in a session"
        assert jokbo.stem("페이지를") == "페이지" and jokbo.stem("만들어") == jokbo.stem("만들기") == "만들"


def test_a_note_leaves_out_what_the_session_saw_happen_and_what_it_was_told_already():
    """guin-site, 10-08 -> 10-11: 50 notes; 14 "last changed in run …" lines named a run the session had just closed, and
    one concept's sentence came with eight files. A line naming what was committed since the session started is left out
    (whatever product wrote it), a line is told once per session whichever file it comes with, and a file the documents
    name outright gets one pointer to `file`. A compaction keeps where the session started."""
    with Repo() as r:
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            write(os.path.join(plug, "note.py"), "import sys, subprocess\nrun = open(sys.argv[2]).read().strip()\n"
                  "print('last changed in ' + run + ' (2026-10-11), task feed')\nprint('concept:feed — the front page, newest first')\n"
                  "print('only for ' + sys.argv[1])\n")
            write(os.path.join(plug, "run.txt"), "run-20260901-000000-0000")
            write(r.p("hunsu.local.json"), {"links": {"netty": plug}})
            write(r.p("hunsu.lock.json"), {"reads": {"netty": {"note": ["python3", "{plugin:netty}/note.py", "{path}", os.path.join(plug, "run.txt")]}}})
            write(r.p("README.md"), r.read("README.md") + "\nThe front page is `src/feed.ts`.\n")
            r.git("add", "-A")
            r.git("commit", "-qm", "readme names the feed")
            hook("session_start.py", {"cwd": r.dir, "session_id": "d1", "source": "startup"})
            write(os.path.join(plug, "run.txt"), "run-20261011-010203-abcd")   # the session's own run, closed after it started
            write(r.p("src/feed.ts"), r.read("src/feed.ts") + "// newest first\n")
            r.git("commit", "-qam", "run-20261011-010203-abcd: close — feed comment")
            read = lambda f, sid="d1": hook("post_tool.py", {"cwd": r.dir, "session_id": sid, "tool_name": "Read", "tool_input": {"file_path": r.p(f)}})[1]
            feed = json.loads(read("src/feed.ts"))["hookSpecificOutput"]["additionalContext"]
            assert "last changed in" not in feed, "the session's own run is not news"
            assert "concept:feed" in feed and "only for src/feed.ts" in feed and "more: `" in feed and "file src/feed.ts` — 1 document section(s) name it" in feed, feed
            tags = json.loads(read("src/tags.ts"))["hookSpecificOutput"]["additionalContext"]
            assert "concept:feed" not in tags and "only for src/tags.ts" in tags, "a line is told once per session"
            hook("session_start.py", {"cwd": r.dir, "session_id": "d1", "source": "compact"})
            again = json.loads(read("src/feed.ts"))["hookSpecificOutput"]["additionalContext"]
            assert "concept:feed" in again and "last changed in" not in again, "after a compaction: told again, and the start kept"
            other = json.loads(read("src/feed.ts", sid="d2"))["hookSpecificOutput"]["additionalContext"]
            assert "last changed in run-20261011-010203-abcd" in other, "a session that never started here has no start: nothing left out"
        finally:
            shutil.rmtree(plug, ignore_errors=True)


def test_a_history_search_in_the_shell_is_answered_once_per_target():
    """guin-site, 10-10: the session ran `git log -- wrangler.worker.build.jsonc` and a grep to learn why a file existed;
    `find` puts the commit that says so first. A search of the project's history or documents gets what the documents say
    of that file or those words, once per target per session; a grep over another command's output searches nothing."""
    with Repo() as r:
        files = set(jokbo.index(r.dir)["files"])
        assert jokbo.history_targets(r.dir, "git log --oneline -3 -- src/feed.ts | cat", files) == [("path", "src/feed.ts")]
        assert jokbo.history_targets(r.dir, "git blame src/tags.ts", files) == [("path", "src/tags.ts")]
        assert jokbo.history_targets(r.dir, "grep -rn \"tags\\|removed\" --include=*.md .", files) == [("terms", "tags removed")]
        assert jokbo.history_targets(r.dir, "grep -n action plan/PLAN.md", files) == [("terms", "action")], "a grep over a document"
        for nothing in ("git status | grep feed", "grep -rln 'at-31' .", "ls src", "grep -n feed src/feed.ts"):
            assert jokbo.history_targets(r.dir, nothing, files) == [], nothing
        bash = lambda c: hook("post_tool.py", {"cwd": r.dir, "session_id": "h1", "tool_name": "Bash", "tool_input": {"command": c}})[1]
        out = json.loads(bash("grep -rn 'tags\\|removed' ."))["hookSpecificOutput"]["additionalContext"]
        assert "where the project speaks of: tags removed" in out and "Tags live in `content/tags.jsonl`" in out, out
        assert "what the documents say of src/feed.ts" in json.loads(bash("git log -3 -- src/feed.ts"))["hookSpecificOutput"]["additionalContext"]
        assert bash("git log -5 -- src/feed.ts") == "", "once per target per session"


def test_a_search_is_read_as_the_shell_reads_it_from_where_it_ran():
    """Quotes come before pipes (`grep -E "a|b"` is one pattern; split first, it was skipped), git's own options are not
    its subcommand, a blame's -L is not a path, and a path is where the command ran (`cd`, `git -C`, the shell's cwd: from
    plan/, `../wrangler…` is the root's file). A command that cannot be a search loads no index; the host's Grep is one."""
    with Repo() as r:
        files = set(jokbo.index(r.dir)["files"])
        t = lambda c, cwd=None: jokbo.history_targets(r.dir, c, files, cwd)
        assert t('grep -rnE "tags|removed" .') == [("terms", "tags removed")]
        assert t('rg "tags|removed"') == [("terms", "tags removed")]
        assert t("git -C plan --no-pager log -- ../src/feed.ts") == [("path", "src/feed.ts")]
        assert t("git -c core.pager=cat log -p src/feed.ts") == [("path", "src/feed.ts")], "git log P without --"
        assert t("git blame src/tags.ts -L 1,5") == [("path", "src/tags.ts")]
        assert t("git log -- ../src/feed.ts", cwd=r.p("plan")) == [("path", "src/feed.ts")]
        assert t("cd plan && git log -- PLAN.md") == [("path", "plan/PLAN.md")]
        assert t("git log -- README.md", cwd=r.p("plan")) == [], "plan/README.md is not the root's"
        assert t("git log -n 5 main") == [] and t('grep -rn "unterminated') == []
        os.remove(r.p(".jokbo/index.json"))
        assert jokbo.searched(r.dir, "g0", "ls -la && cat README.md") == [] and not os.path.exists(r.p(".jokbo/index.json")), \
            "a command that cannot be a search costs no index"
        grep = {"cwd": r.dir, "session_id": "g1", "tool_name": "Grep", "tool_input": {"pattern": "tags|removed", "path": r.dir}}
        out = json.loads(hook("post_tool.py", grep)[1])["hookSpecificOutput"]["additionalContext"]
        assert "where the project speaks of: tags removed" in out, out


def test_a_note_line_is_dated_by_run_ids_and_commits_not_by_any_token_with_a_digit():
    """A commit subject holding python3, a /Users/someone-3/ path or 2026-10-11 once dropped every note line that held the
    same token. A line is dated by a commit's hash and by names with four digits or more that are not a plain date."""
    names = jokbo.names_in("run-20261010-132224-0b9a: close; python3 /Users/someone-3/x 2026-10-11 2026-10-11T09:30Z 1.16.0 at-59")
    assert names == {"run-20261010-132224-0b9a"}, names


def test_a_wrapped_line_stays_in_its_sentence():
    """A continuation that starts with **bold**, -1 or 2026.10.11 is not a list item: cut there, the quote began mid-sentence
    with a stray `*every**`. A table row is still its own sentence."""
    text = "The page is rebuilt when\n**every** post changes, and the\n-1 offset is kept until\n2026.10.11 when it stops.\n\n| a | row that says a thing |\n- an item that is a list item"
    got = jokbo.sentences(text)
    assert got[0] == "The page is rebuilt when **every** post changes, and the -1 offset is kept until 2026.10.11 when it stops.", got
    assert "| a | row that says a thing |" in got and "an item that is a list item" in got, got


def test_a_prompt_is_matched_on_its_prose_not_a_pasted_command_and_nouns_keep_their_stem():
    """A trial prompt quoted a command (`python3 … | head -3`) and asked about the read path; the test runner's sentence
    was quoted on python3/head. A command line typed inline is not the question; a backquoted name is. 화면 and 로고 are
    nouns (cut to 화·로 they matched nothing), and OG/S3 are words of two letters."""
    w = jokbo.prompt_words("이 명령 실행해: C=~/.cache; python3 $C/x/*/x.py --help | head -3 그리고 `site-build` 화면 로고 OG 이미지")
    for gone in ("python3", "head", "cache", "help"):
        assert gone not in w, (gone, w)
    for kept in ("site", "build", "화면", "로고", "og"):
        assert kept in w, (kept, w)
    assert jokbo.prompt_words("run `python3 tests/run.py --all` please") == ["run", "please"]
    with Repo() as r:
        write(r.p("plan/META.md"), "# Meta\n\n## Preview\n\nThe OG image is drawn per post at build time with the title.\n\n## Other\n\nNothing here about it.\n")
        r.git("add", "-A")
        r.git("commit", "-qm", "meta")
        said, ids = jokbo.prompt_report(r.dir, "how is the OG image drawn per post?")
        assert ids == ["plan/META.md#Preview"], said


def test_an_older_cache_is_brought_up_to_date_without_blame():
    """INDEX_FORMAT 6 added a heading's level and a section's match keys, 7 a document's last change and retired line; a
    full rebuild blames every document and, on a large project, outran the hooks' timeout. An older cache keeps its blame
    and gains the rest from the files."""
    with Repo() as r:
        built = jokbo.index(r.dir)
        old = json.load(io.open(r.p(".jokbo/index.json"), encoding="utf-8"))
        old["format"] = 5
        old.pop("keyed")
        for e in old["entries"].values():
            e.pop("keyed", None)
            for sec in e.get("sections", []):
                sec.pop("level")
                sec.pop("keys")
                sec["changed"] = 12345   # blame's answer, kept: not recomputed
        jokbo.save(r.p(".jokbo/index.json"), old)
        up = jokbo.index(r.dir)
        assert up["format"] == jokbo.INDEX_FORMAT and all(x["changed"] == 12345 for x in up["sections"]), "blame was not run again"
        assert [(x["level"], x["keys"]) for x in up["sections"]] == [(x["level"], x["keys"]) for x in built["sections"]]
        docs = [e for e in up["entries"].values() if e.get("sections")]
        assert docs and all(e["size"] > 0 and "retired" in e for e in docs), "7: a document's last change, from its last commit"


def test_a_compaction_does_not_tell_the_session_its_own_commits():
    """A compaction comes mid-answer, before the Stop that marks where this machine looked: what was committed since is
    the session's own work, not news."""
    with Repo() as r:
        hook("session_start.py", {"cwd": r.dir, "session_id": "c1", "source": "startup"})
        hook("stop.py", {"cwd": r.dir, "session_id": "c1"})
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\nMore.\n")
        r.git("commit", "-qam", "the session's own commit")
        said = lambda sid, src: json.loads(hook("session_start.py", {"cwd": r.dir, "session_id": sid, "source": src})[1])["hookSpecificOutput"]["additionalContext"]
        out = said("c1", "compact")
        assert "the session's own commit" not in out and "jokbo — what this project holds" in out, out
        assert "the session's own commit" in said("c2", "startup")


def test_find_answers_with_the_sentence_not_only_where():
    with Repo() as r:
        out = run("find", "tags", "removed", "--target", r.dir)[1]
        assert "plan/PLAN.md#Q-tags — tagging" in out and "\u201cTags live in `content/tags.jsonl`: post, tag, action (added or removed), at, why.\u201d" in out, out


def test_a_failed_call_and_a_sub_agents_work_are_the_sessions_own():
    """A call that wrote and then failed fires PostToolUseFailure; its writes were told back as another hand's. While a
    sub-agent runs, siblings' writes are the session's hands too — no change line until the Agent call ends."""
    with Repo() as r:
        hook("session_start.py", {"cwd": r.dir, "session_id": "f1"})
        call = {"cwd": r.dir, "session_id": "f1", "tool_name": "Bash", "tool_input": {"command": "echo // x >> src/tags.ts; false"}}
        hook("pre_tool.py", call)
        write(r.p("src/tags.ts"), r.read("src/tags.ts") + "// x\n")
        hook("post_tool.py", dict(call, hook_event_name="PostToolUseFailure"))
        assert "another hand" not in hook("prompt.py", {"cwd": r.dir, "session_id": "f1"})[1], "a failed call's write is its own"
        agent = {"cwd": r.dir, "session_id": "f1", "tool_name": "Agent", "tool_input": {"prompt": "do it"}}
        hook("pre_tool.py", agent)
        write(r.p("src/feed.ts"), r.read("src/feed.ts") + "// a sub-agent's\n")
        sub = {"cwd": r.dir, "session_id": "sub-2", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        assert "another hand" not in hook("pre_tool.py", sub)[1], "a sibling's write while sub-agents run is not another hand's"
        hook("post_tool.py", dict(agent, hook_event_name="PostToolUse"))
        assert "another hand" not in hook("prompt.py", {"cwd": r.dir, "session_id": "f1"})[1], "the sub-agent's work is the session's"
        assert jokbo.agents_in_flight(r.dir) == 0
        jokbo.agents_in_flight(r.dir, "lost", +1)
        assert jokbo.agents_in_flight(r.dir) == 1
        jokbo.save(r.p(".jokbo/agents.json"), {"lost": {"n": 1, "at": time.time() - jokbo.AGENT_STALE - 1}})
        assert jokbo.agents_in_flight(r.dir) == 0, "an Agent call with no end for hours does not silence the change line"


def test_the_map_keeps_the_newest_sections_and_its_size():
    """A document that grows at its end (guin-site's ATPROTO.md) showed its first fourteen sections and hid where the work
    was. The map keeps each document's most recently changed sections, the most recent documents first, within a size."""
    with Repo() as r:
        body = "# Log\n\n" + "".join("## Step %02d\n\nold work number %d.\n\n" % (i, i) for i in range(1, 21))
        write(r.p("plan/LOG.md"), body); r.git("add", "-A"); r.git("commit", "-qm", "log", env_date="2026-09-05T00:00:00Z")
        write(r.p("plan/LOG.md"), body + "## Step 21 — the newest\n\nwhat the session works on now.\n")
        r.git("commit", "-qam", "newest step", env_date="2026-09-06T00:00:00Z")
        m = "\n".join(jokbo.map_report(r.dir))
        assert "Step 21 — the newest" in m and "Step 01" not in m, m
        assert m.index("plan/LOG.md") < m.index("plan/PLAN.md"), "the document worked on last comes first"
        assert len(m) <= jokbo.MAP_CHARS + 400, len(m)


def test_the_map_orders_documents_by_their_last_change_and_gives_a_retired_one_a_line():
    """guin-site 10-13: README.md (rewritten) and BACKEND.md (a retirement note at its top) changed in one commit; the tie
    went to the document with more sections, so BACKEND.md got twelve heading lines and README.md one mention. Documents
    are ordered by the day of their last change and how much it wrote; one that says at its top it is retired gets a line."""
    with Repo() as r:
        old = "# Backend\n\nThe old structure.\n\n" + "".join("## B%02d\n\nold part %d.\n\n" % (i, i) for i in range(1, 15))
        write(r.p("plan/BACKEND.md"), old)
        write(r.p("GUIDE.md"), "# Guide\n\n## How to write\n\nShort.\n\n## How to publish\n\nShort.\n")
        r.git("add", "-A"); r.git("commit", "-qm", "both", env_date="2026-09-05T00:00:00Z")
        write(r.p("plan/BACKEND.md"), old.replace("The old structure.\n", "The old structure.\n\n**Retired (2026-09-06):** no longer true.\n"))
        write(r.p("GUIDE.md"), "# Guide\n\n" + "".join("## Step %d — rewritten\n\nA new step %d, said at length.\nAnd more.\n\n" % (i, i) for i in range(1, 6)))
        r.git("commit", "-qam", "guide rewritten; backend retired", env_date="2026-09-06T00:00:00Z")
        m = "\n".join(jokbo.map_report(r.dir))
        d = jokbo.day(calendar.timegm((2026, 9, 6, 0, 0, 0)))
        assert m.index("GUIDE.md — Guide (6 section(s), last %s)" % d) < m.index("plan/BACKEND.md"), m
        assert "    3-7  Step 1 — rewritten  %s" % d in m, "each heading with its last change"
        assert "plan/BACKEND.md — Backend — retired (says so at line 5), %s; 15 section(s)" % d in m and "B01" not in m, m
        assert m.index("plan/BACKEND.md") < m.index("plan/PLAN.md"), "a retired document keeps its place, in one line"
    assert jokbo.retired_at("# 뒷단 구조\n\n**은퇴 (2026-10-12):** 더 이상 사실이 아니다.\n") == 3
    assert jokbo.retired_at("# Old design (superseded)\n") == 1
    assert jokbo.retired_at("# Plan\n\nThe deprecated API is gone.\n\n## Deprecated flags\n") is None, "a mention or a section is not the document's state"


def test_a_long_line_is_cut_at_an_item_or_a_word_never_inside_one():
    """guin-site's map line ended "ui-language ; 3 ope …": the open questions, the line's point, cut mid-word."""
    line = "22 concept(s): " + ", ".join("concept-%02d" % i for i in range(22)) + " ; 3 open question(s)"
    short = jokbo.cut(line, 240)
    assert len(short) <= 240 and short.endswith("; 3 open question(s)") and ", … +" in short, short
    words_line = "The snapshot sits in isolate memory and in the Cache API by rev, thirty days, and a stale one is served on error"
    short = jokbo.cut(words_line, 60)
    assert len(short) <= 60 and short.endswith(" …") and words_line.startswith(short[:-2]) and words_line[len(short) - 2] in " ,", short
    assert jokbo.cut("short", 60) == "short"
    with Repo() as r:
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            write(os.path.join(plug, "map.py"), "print(%r)\n" % line)
            write(r.p("hunsu.local.json"), {"links": {"netty": plug}})
            write(r.p("hunsu.lock.json"), {"reads": {"netty": {"map": ["python3", "{plugin:netty}/map.py"]}}})
            netty = [l for l in jokbo.map_report(r.dir) if l.startswith("  netty: ")][0]
            assert netty.endswith("; 3 open question(s)"), netty
        finally:
            shutil.rmtree(plug, ignore_errors=True)


def test_a_line_on_a_files_standing_is_said_once_and_the_document_sentence_still_comes():
    """guin-site 10-15: every first look got "mangsang: <file>: not registered …" and a pointer to `file`, and the session
    used none of them. A product's line that starts with the file's path says its standing — told once per session in its
    form; it does not stand in for the document sentence; and the pointer goes when the sections naming the file were
    the ones said."""
    with Repo() as r:
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            write(os.path.join(plug, "note.py"), "import sys\nprint(sys.argv[1] + ': not registered — the net does not see it')\n")
            write(r.p("hunsu.local.json"), {"links": {"netty": plug}})
            write(r.p("hunsu.lock.json"), {"reads": {"netty": {"note": ["python3", "{plugin:netty}/note.py", "{path}"]}}})
            write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\n## Q-feed-code\n\nThe front page is `src/feed.ts`: posts newest first by written time.\n")
            r.git("add", "-A"); r.git("commit", "-qm", "the plan names the feed")
            hook("session_start.py", {"cwd": r.dir, "session_id": "s1", "source": "startup"})
            read = lambda f: (json.loads(hook("post_tool.py", {"cwd": r.dir, "session_id": "s1", "tool_name": "Read",
                                                               "tool_input": {"file_path": r.p(f)}})[1] or "{}").get("hookSpecificOutput") or {}).get("additionalContext", "")
            tags = read("src/tags.ts")
            assert "netty: src/tags.ts: not registered" in tags and "Q-tags" in tags and "\u201c" in tags, "the standing, and what the file is about"
            feed = read("src/feed.ts")
            assert "not registered" not in feed, "said once in its form, not with every file: " + feed
            assert "Q-feed-code" in feed and "\u201c" in feed, feed
            assert "more:" not in feed, "the one section naming it was the one quoted: " + feed
        finally:
            shutil.rmtree(plug, ignore_errors=True)
    with Repo() as r:
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\n## Q-one\n\nSee `src/tags.ts`.\n\n## Q-two\n\nAlso `src/tags.ts` and `src/feed.ts`.\n")
        r.git("add", "-A"); r.git("commit", "-qm", "two sections name the code")
        hook("session_start.py", {"cwd": r.dir, "session_id": "s2", "source": "startup"})
        read = lambda f: (json.loads(hook("post_tool.py", {"cwd": r.dir, "session_id": "s2", "tool_name": "Read",
                                                           "tool_input": {"file_path": r.p(f)}})[1] or "{}").get("hookSpecificOutput") or {}).get("additionalContext", "")
        first, second = read("src/tags.ts"), read("src/feed.ts")
        assert "more: `" in first and "jokbo.py\" file src/tags.ts`" in first, first
        assert "more: jokbo `file src/feed.ts`" in second, "the command spelled out once: " + second


def test_the_session_start_map_stays_within_its_size():
    """A five-second session (guin-site 10-12, a translation) got the whole session-start text: the map is bounded, retired
    documents and long product lines included."""
    with Repo() as r:
        for i in range(30):
            body = "# Doc %d\n\n" % i + ("Retired: kept for history.\n\n" if i % 3 == 0 else "") + \
                   "".join("## A heading that runs long, number %d of document %d\n\ntext.\n\n" % (k, i) for k in range(20))
            write(r.p("docs/d%02d.md" % i), body)
        r.git("add", "-A"); r.git("commit", "-qm", "many documents")
        m = "\n".join(jokbo.map_report(r.dir))
        assert len(m) <= jokbo.MAP_CHARS + len(jokbo.engine_path()), len(m)
        assert sum(1 for l in m.split("\n") if l.startswith("    ")) <= jokbo.MAP_HEADINGS


def test_a_document_path_counts_only_inside_the_project_and_as_the_shell_would_expand_it():
    """guin-site 10-15: `M=<mangsang's install>; grep -n … $M/README.md` was answered with the project's own sections —
    any argument ending in .md was a document of the project. A path counts when it resolves inside the project (from
    where the command ran; /var and /private/var are one place) and holds nothing jokbo cannot expand."""
    with Repo() as r:
        files = set(jokbo.index(r.dir)["files"])
        t = lambda c, cwd=None: jokbo.history_targets(r.dir, c, files, cwd)
        outside = tempfile.mkdtemp(prefix="jokbo-other-")
        try:
            write(os.path.join(outside, "README.md"), "# Other\n\nproposals file\n")
            for c in ("M=/x/mangsang; grep -n -A25 -i 'proposals file' $M/README.md | head -60",
                      "grep -n tags ${M}/README.md", "grep -n tags $(dirname x)/README.md", "grep -n tags `pwd`/README.md",
                      "grep -n tags %s/README.md" % outside, "grep -n tags ../README.md", "grep -n tags ~nobody-here/README.md"):
                assert t(c) == [], c
            assert t("grep -rn tags %s" % outside) == [], "a recursive grep elsewhere is not the project's"
            assert t("grep -n tags %s" % r.p("plan/PLAN.md")) == [("terms", "tags")], "an absolute path inside counts"
            real = os.path.realpath(r.dir)
            assert t("grep -n tags %s" % os.path.join(real, "plan/PLAN.md")) == [("terms", "tags")], "/private/var is /var"
            assert t("grep -n tags ../plan/PLAN.md", cwd=r.p("src")) == [("terms", "tags")]
        finally:
            shutil.rmtree(outside, ignore_errors=True)


def test_records_a_plugin_this_session_ran_writes_are_the_sessions_own():
    """guin-site 10-15 06:15:38 and 06:20:42: the session's backgrounded `chongdae run` (and the provider it left running)
    wrote the run's task records; jokbo told them as another hand's. A plugin whose engine the session's call runs works
    for the session — until its background call's completion is notified, and OWN_WINDOW after its last call — so its
    records, and a commit that writes them, are the session's own. Another plugin's, and the rest, are still told."""
    with Repo() as r:
        write(r.p("hunsu.lock.json"), {"record-paths": {"chongdae": [".chongdae/"], "netty": ["net/"]}})
        r.git("add", "-A"); r.git("commit", "-qm", "lock")
        hook("session_start.py", {"cwd": r.dir, "session_id": "b1"})
        bg = {"cwd": r.dir, "session_id": "b1", "tool_name": "Bash", "tool_use_id": "toolu_bg1",
              "tool_input": {"command": "while kill -0 1 2>/dev/null; do sleep 10; done; python3 /p/chongdae/1.20.0/chongdae.py run 2>&1 | tail -12",
                             "run_in_background": True}}
        hook("pre_tool.py", bg)
        hook("post_tool.py", dict(bg, hook_event_name="PostToolUse"))
        write(r.p(".chongdae/run-1/tasks/build.json"), {"status": "todo"})   # the background run, after the call returned
        ls = {"cwd": r.dir, "session_id": "b1", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        assert "another hand" not in hook("pre_tool.py", ls)[1], "the background run's records are the session's"
        hook("post_tool.py", ls)
        jokbo.save(r.p(".jokbo/sessions/b1.json"), dict(jokbo.session_state(r.dir, "b1")[0],
                                                       working={"chongdae": time.time() - jokbo.OWN_WINDOW - 1}))
        write(r.p(".chongdae/run-1/tasks/build.json"), {"status": "done"})
        r.git("add", "-A"); r.git("commit", "-qm", "run-1: build done")   # the engine's commit, records with the work
        note = "<task-notification>\n<task-id>b1x</task-id>\n<tool-use-id>toolu_bg1</tool-use-id>\n<status>completed</status>\n</task-notification>"
        out = hook("prompt.py", {"cwd": r.dir, "session_id": "b1", "prompt": note})[1]
        assert "another hand" not in out, "still open until its notification: " + out
        write(r.p(".chongdae/run-1/tasks/tests.json"), {"status": "todo"})   # the provider it left, after the notification
        assert "another hand" not in hook("prompt.py", {"cwd": r.dir, "session_id": "b1", "prompt": "next"})[1], "within OWN_WINDOW"
        write(r.p("net/concepts/feed.json"), {"means": "x"})
        write(r.p(".chongdae/run-1/tasks/other.json"), {"status": "todo"})
        ctx = hook("prompt.py", {"cwd": r.dir, "session_id": "b1", "prompt": "again"})[1]
        assert "netty's records: net/concepts/feed.json" in ctx and ".chongdae" not in ctx, ctx
        jokbo.save(r.p(".jokbo/sessions/b1.json"), dict(jokbo.session_state(r.dir, "b1")[0], working={}, background={}))
        write(r.p(".chongdae/run-2/tasks/x.json"), {"status": "todo"})   # another session's run, this one not calling chongdae
        assert "chongdae's records: .chongdae/run-2/tasks/x.json" in hook("prompt.py", {"cwd": r.dir, "session_id": "b1", "prompt": "q"})[1]
        names = lambda c: jokbo.engine_called(r.dir, "Bash", {"command": c})
        assert names("cat .chongdae/run-1/tasks/x.json") == set() and names("grep -rn chongdae README.md") == set()
        assert names("C=/p/chongdae.py; python3 $C run") == {"chongdae"} and names("chongdae status") == {"chongdae"}
        assert jokbo.engine_called(r.dir, "Skill", {"skill": "chongdae:run"}) == {"chongdae"}



def test_a_retired_documents_quote_says_so_and_a_live_one_answers_first():
    """guin-site 10-15: plan/BACKEND.md says at line 16 that it is retired, yet `find`, `file`, the note at first look and
    the prompt quoted its D1/R2/admin structure as plainly as the plan that replaced it. Every quote from a retired
    document says so; a live document that answers the same question comes first; history is still there when asked."""
    with Repo() as r:
        write(r.p("plan/BACKEND.md"), "# Backend\n\n**Retired (2026-09-06):** no longer true; see plan/PLAN.md.\n\n"
              "## Storage\n\nThe posts live in D1 tables and the admin screen writes the front page feed rows, newest first.\n\n"
              "## Backup\n\nA nightly backup copies the D1 tables to S3.\n")
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\n## Q-store — where posts live\n\nThe posts live in the PDS; the old D1 "
              "tables are gone and the front page feed reads the records.\n")
        r.git("add", "-A"); r.git("commit", "-qm", "backend retired; posts in the PDS", env_date="2026-09-06T00:00:00Z")
        mark = "retired document (says so at line 3)"
        out = run("find", "D1", "--target", r.dir)[1]
        assert "Q-store" in out and "plan/BACKEND.md#Storage" in out, "two letters (D1) are a word to ask for: " + out
        assert out.index("Q-store") < out.index("plan/BACKEND.md#Storage"), "the live document first"
        assert all(mark in l for l in out.split("\n") if l.startswith("  plan/BACKEND.md")), out
        assert not any(mark in l for l in out.split("\n") if "Q-store" in l), "a live one is not marked"
        hist = run("find", "nightly", "backup", "--target", r.dir)[1]
        assert "plan/BACKEND.md#Backup" in hist and mark in hist, "history is still asked for, and said as history: " + hist
        f = run("file", "src/feed.ts", "--target", r.dir)[1]
        assert all(mark in l for l in f.split("\n") if l.startswith("  plan/BACKEND.md")), f
        own = run("file", "plan/BACKEND.md", "--target", r.dir)[1]
        assert "says at line 3 that it is retired" in own.split("\n")[0], own
        note = jokbo.note_report(r.dir, "src/feed.ts")
        assert note and "BACKEND" not in "\n".join(note), "a live document's sentence at first look: %s" % note
        idx = jokbo.index(r.dir)
        old = [x for x in idx["sections"] if x["file"] == "plan/BACKEND.md" and x["heading"] == "Storage"][0]
        assert jokbo.rank_sections(idx, jokbo.words("admin screen writes"), top=1)[0] is old, "only the retired one speaks of it"
        said, ids = jokbo.prompt_report(r.dir, "does the admin screen write the feed rows into D1 tables?")
        assert any("plan/BACKEND.md" in l and mark in l for l in said), said
        assert all(mark in l for l in said if "plan/BACKEND.md" in l), said
        since = "\n".join(jokbo.since_report(r.dir, "HEAD~1"))
        assert "plan/BACKEND.md#Storage — " + mark in since and "plan/PLAN.md#Q-store — where posts live\n" in since + "\n", since


def test_a_hired_workers_edits_are_told_as_its_own_when_a_products_records_say_whose():
    """guin-site 10-15 05:53:57: "changed by another hand — files: src/worker/index.ts, src/worker/read.ts" was the
    session's own hired builder (task cache-build, run-20261015-054352-a0ec). Still told — the session did not write
    them — but as whose the declared `hands` read says ({paths}: one argument each; {after}: the last call's time); a
    path it does not name, a read that fails or says nothing parseable, stays another hand's."""
    with Repo() as r:
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            write(os.path.join(plug, "hands.py"), "import json, sys\nafter, paths = sys.argv[1], sys.argv[2:]\nassert after.endswith('Z'), after\n"
                  "print(json.dumps({p: 'the implementer of feed-build (run-1)' for p in paths if p.startswith('src/feed')}))\n")
            write(r.p("hunsu.local.json"), {"links": {"crew": plug}})
            write(r.p("hunsu.lock.json"), {"record-paths": {"crew": [".crew/"]},
                                           "reads": {"crew": {"hands": ["python3", "{plugin:crew}/hands.py", "{after}", "{paths}"]}}})
            r.git("add", "-A"); r.git("commit", "-qm", "lock")
            old = jokbo.snapshot(r.dir)
            write(r.p("src/feed.ts"), r.read("src/feed.ts") + "// the worker's\n")
            write(r.p("src/tags.ts"), r.read("src/tags.ts") + "// the owner's\n")
            write(r.p(".crew/run-1/tasks/feed-build.json"), {"status": "running"})
            lines = jokbo.change_report(r.dir, old, jokbo.snapshot(r.dir))
            assert "  changed by the implementer of feed-build (run-1): src/feed.ts" in lines, lines
            assert "  files: src/tags.ts" in lines and "  crew's records: .crew/run-1/tasks/feed-build.json" in lines, lines
            write(os.path.join(plug, "hands.py"), "print('not json')\n")
            lines = jokbo.change_report(r.dir, old, jokbo.snapshot(r.dir))
            assert "  files: src/feed.ts, src/tags.ts" in lines, "no source says whose: another hand's — %s" % lines
            write(r.p("hunsu.lock.json"), {"record-paths": {"crew": [".crew/"], "hunsu": ["hunsu.lock.json"]}})
            assert "  files: src/feed.ts, src/tags.ts" in jokbo.change_report(r.dir, old, jokbo.snapshot(r.dir)), "none declared"
            assert jokbo.change_report(r.dir, old, dict(old, at="later")) == [], "the time alone is not a change"
        finally:
            shutil.rmtree(plug, ignore_errors=True)

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
