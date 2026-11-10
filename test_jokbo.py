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
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "plugin")
sys.path.insert(0, PLUGIN)
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
    done = subprocess.run([sys.executable, os.path.join(PLUGIN, "hooks", name)], input=json.dumps(payload), capture_output=True, text=True,
                          encoding="utf-8", env=dict({k: v for k, v in os.environ.items() if k != "AGENT_WORKER"}, **env))
    return done.returncode, done.stdout


@contextlib.contextmanager
def pinned_host(host):
    """os.environ pinned for a call: AGENT_HOST, empty temp Claude and Codex homes, a temp HOME, and none of the other
    home variables; restored after. Yields the base directory (claude-home, codex-home, home inside it)."""
    base = os.path.realpath(tempfile.mkdtemp(prefix="jokbo-host-"))
    saved = dict(os.environ)
    try:
        for d in ("claude-home", "codex-home", "home"):
            os.makedirs(os.path.join(base, d))
        for key in ("CLAUDE_CONFIG_DIR", "AGENT_CODEX_HOME", "CODEX_HOME"):
            os.environ.pop(key, None)
        os.environ.update(AGENT_HOST=host, HUNSU_CLAUDE_DIR=os.path.join(base, "claude-home"),
                          HUNSU_CODEX_DIR=os.path.join(base, "codex-home"), HOME=os.path.join(base, "home"))
        yield base
    finally:
        os.environ.clear()
        os.environ.update(saved)
        shutil.rmtree(base, ignore_errors=True)


PROBE_MARK = "probe ran from "


def probe_copy(d, version, manifest=".claude-plugin"):
    """A plugin copy that prints where it runs from; its manifest says `version`."""
    write(os.path.join(d, "probe.py"), "import os\nprint(%r + os.path.realpath(os.path.dirname(os.path.abspath(__file__))))\n"
          % PROBE_MARK)
    write(os.path.join(d, manifest, "plugin.json"), {"name": "probe", "version": version})
    return d


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
    with Repo() as r, pinned_host("claude-code"):
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            write(os.path.join(plug, "echo.py"), "import sys\nprint('relations on ' + sys.argv[1])\nprint('  concept:feed — the front page')\n")
            write(r.p("hunsu.local.json"), {"links": {"netty": plug}})
            write(r.p("hunsu.lock.json"), {"reads": {"netty": {"file": ["python3", "{plugin:netty}/echo.py", "{path}"],
                                                                "decision": ["python3", "{plugin:netty}/echo.py", "all"]},
                                                      "gone": {"file": ["python3", "{plugin:gone}/x.py", "{path}"]}}})
            out = run("file", "src/feed.ts", "--target", r.dir)[1]
            assert "netty (declared `file` read):" in out and "relations on src/feed.ts" in out, out
            assert "gone (declared `file` read): not read — plugin gone is not linked or installed here on claude-code" in out.splitlines(), out
            out = run("find", "front", "--target", r.dir)[1]
            assert "netty (declared `decision` read)" in out and "concept:feed — the front page" in out, out
            out = run("index", "--target", r.dir)[1]
            assert "declared reads: gone(file), netty(decision,file)" in out, out
            write(os.path.join(plug, "echo.py"), "import sys\n")   # a read with nothing on this file
            out = run("file", "src/feed.ts", "--target", r.dir)[1]
            assert "netty" not in out, "nothing to say: no empty heading"
        finally:
            shutil.rmtree(plug, ignore_errors=True)


def test_the_resolver_is_the_vendored_file_beside_the_engine():
    """J1: jokbo holds plugin/pluginroot.py as its module-level `pluginroot`, the file beside jokbo.py, byte-identical to the
    family's resolver."""
    import hashlib
    path = os.path.join(PLUGIN, "pluginroot.py")
    assert os.path.isfile(path), "plugin/pluginroot.py does not exist"
    with open(path, "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == "6318838886010a99b209728af9368d4a7c816bd7b4266fb69bef3d36e23eb35e"
    assert hasattr(jokbo, "pluginroot"), "jokbo has no module-level pluginroot"
    assert os.path.realpath(jokbo.pluginroot.__file__) == os.path.realpath(path), jokbo.pluginroot.__file__


def test_plugin_root_is_the_resolvers_dict_for_the_selected_host():
    """J1: jokbo.plugin_root(target, name) is pluginroot.resolve's dict unchanged — a Codex-only install is found on codex
    and is not, with the resolver's reason, on claude-code."""
    target = os.path.realpath(tempfile.mkdtemp(prefix="jokbo-target-"))
    try:
        write(os.path.join(target, "hunsu.json"), {"plugins": {"probe": {"version": "1.0.0", "marketplace": "pm"}}})
        with pinned_host("codex") as base:
            home = os.path.join(base, "codex-home")
            write(os.path.join(home, "config.toml"), '[plugins."probe@pm"]\nenabled = true\n')
            copy = probe_copy(os.path.join(home, "plugins", "cache", "pm", "probe", "1.0.0"), "1.0.0", ".codex-plugin")
            got = jokbo.plugin_root(target, "probe")
            assert got == {"name": "probe", "root": copy, "host": "codex", "via": "install", "marketplace": "pm",
                           "declared": "1.0.0", "version": "1.0.0", "note": None, "reason": None}, got
            os.environ["AGENT_HOST"] = "claude-code"
            got = jokbo.plugin_root(target, "probe")
            assert got == {"name": "probe", "root": None, "host": "claude-code", "via": None, "marketplace": "pm",
                           "declared": "1.0.0", "version": None, "note": None,
                           "reason": "plugin probe@pm is declared for this project but not installed for it here on claude-code"
                                     " — `hunsu install`"}, got
            assert hasattr(jokbo, "pluginroot") and got == jokbo.pluginroot.resolve(target, "probe"), got
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_run_read_gives_its_text_why_and_the_resolvers_notes():
    """J1: run_read returns (text, why, notes): a copy whose version differs from the pin is run and its note given once;
    a plugin not found is not run, with the resolver's reason and no notes; a read naming no plugin has no notes; overlay
    gives (plugin, text, why, notes)."""
    target = os.path.realpath(tempfile.mkdtemp(prefix="jokbo-target-"))
    try:
        write(os.path.join(target, "hunsu.json"), {"plugins": {"probe": {"version": "1.0.0", "marketplace": "pm"}}})
        write(os.path.join(target, "README.md"), "# Site\n")
        with pinned_host("claude-code") as base:
            copy = probe_copy(os.path.join(base, "installs", "probe", "1.5.0"), "1.5.0")
            write(os.path.join(base, "claude-home", "plugins", "installed_plugins.json"),
                  {"version": 2, "plugins": {"probe@pm": [{"scope": "user", "installPath": copy}]}})
            note = "hunsu.json declares probe 1.0.0; the claude-code copy at %s is 1.5.0" % copy
            values = {"path": "README.md", "target": target}
            got = jokbo.run_read(target, "probe", ["python3", "{plugin:probe}/probe.py", "{path}"], values)
            assert isinstance(got, tuple) and len(got) == 3, got
            text, why, notes = got
            assert why is None and text is not None and PROBE_MARK + os.path.realpath(copy) in text, got
            assert notes == [note], notes
            got = jokbo.run_read(target, "probe", ["python3", "{plugin:probe}/probe.py", "{plugin:probe}"], values)
            assert len(got) == 3 and got[2] == [note], ("each note once", got)
            got = jokbo.run_read(target, "gone", ["python3", "{plugin:gone}/x.py"], values)
            assert got == (None, "plugin gone is not linked or installed here on claude-code", []), got
            got = jokbo.run_read(target, "plain", ["python3", "-c", "print('plain read')"], values)
            assert got == ("plain read", None, []), got
            write(os.path.join(target, "hunsu.json"), {"plugins": {"probe": {"version": "1.0.0", "marketplace": "pm"}},
                                                       "reads": {"probe": {"file": ["python3", "{plugin:probe}/probe.py"]}}})
            rows = jokbo.overlay(target, "file", {"path": "README.md"})
            assert len(rows) == 1 and len(rows[0]) == 4, rows
            assert rows[0][0] == "probe" and rows[0][2] is None and rows[0][3] == [note], rows
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_freshness_weighs_a_sections_last_change_against_its_files():
    """provenance-and-freshness, called directly: a section's age against the file's — uncommitted lines are "being
    edited", a file changed after the section says to read it against the code, uncommitted changes to the file are
    said, and a section as new as its file is only dated."""
    t0, t1 = 1756684800, 1756771200   # 2025-09-01 00:00Z, 2025-09-02 00:00Z
    assert jokbo.freshness({"changed": None}, t1) == "being edited (uncommitted)"
    assert jokbo.freshness({"changed": t0}, t1) == \
        "written 2025-09-01 00:00Z; the file changed after (2025-09-02 00:00Z) — read it against the code"
    assert jokbo.freshness({"changed": t0}, "uncommitted") == \
        "written 2025-09-01 00:00Z; the file has uncommitted changes since"
    assert jokbo.freshness({"changed": t1}, t0) == "written 2025-09-02 00:00Z"
    assert jokbo.freshness({"changed": t1}, t1) == "written 2025-09-02 00:00Z"


def test_blame_times_dates_each_committed_line_and_leaves_uncommitted_ones_out():
    """provenance-and-freshness, called directly: blame_times gives each committed line the time of the commit that last
    changed it, and no time to a line not yet committed — what freshness weighs."""
    with Repo() as r:
        t1 = calendar.timegm(time.strptime("2026-09-01T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
        t2 = calendar.timegm(time.strptime("2026-09-02T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
        times = jokbo.blame_times(r.dir, "src/feed.ts")
        lines = r.read("src/feed.ts").rstrip("\n").split("\n")
        assert sorted(times) == list(range(1, len(lines) + 1)), times
        assert times[len(lines)] == t2, ("the line the second commit added", times)
        assert all(times[i] == t1 for i in range(1, len(lines))), times
        write(r.p("src/feed.ts"), r.read("src/feed.ts") + "export const PAGE = 2;\n")
        times = jokbo.blame_times(r.dir, "src/feed.ts")
        assert len(lines) + 1 not in times and times[len(lines)] == t2, ("an uncommitted line has no time", times)
        assert jokbo.blame_times(r.dir, "no/such/file.md") == {}


def test_retired_mark_is_carried_by_a_retired_documents_quote_only():
    """provenance-and-freshness, called directly: retired_mark is what every quote from a document that says at its top
    it is retired carries, with the line that says so; a live document's quote carries nothing."""
    with Repo() as r:
        write(r.p("plan/OLD.md"), "# Old plan\n\n**Retired (2026-09-06):** no longer true; see plan/PLAN.md.\n\n## Feed\n\n"
              "The feed was built nightly from D1 tables.\n")
        r.git("add", "-A"); r.git("commit", "-qm", "the old plan, retired", env_date="2026-09-06T00:00:00Z")
        idx = jokbo.index(r.dir)
        assert jokbo.retired_mark(idx, "plan/OLD.md") == " — retired document (says so at line 3)"
        assert jokbo.retired_mark(idx, "plan/PLAN.md") == ""
        assert jokbo.retired_mark(idx, "no/such.md") == ""


def test_the_cache_is_rebuilt_when_the_tree_moves_and_ignores_itself():
    with Repo() as r:
        a = jokbo.index(r.dir)
        assert jokbo.index(r.dir)["built"] == a["built"]
        assert io.open(r.p(".jokbo/.gitignore")).read() == "*\n"
        assert r.git("status", "--porcelain") == "", "the cache leaves the tree clean"
        write(r.p("plan/NEW.md"), "# New\n\nsomething new about tags and feed\n")
        b = jokbo.index(r.dir)
        assert any(s["file"] == "plan/NEW.md" for s in b["sections"]), "an untracked document is read too"


def test_a_second_edit_to_an_edited_file_rebuilds_its_entry():
    """`git status` says a state, not a content: an edited file stays ` M` through every later edit. 1.7.0 keyed the
    cache on it, so the second edit was answered from the first one's index."""
    with Repo() as r:
        jokbo.index(r.dir)
        plan = r.read("plan/PLAN.md")
        write(r.p("plan/PLAN.md"), plan + "\nAlpha rule.\n")
        assert "alpha" in jokbo.index(r.dir)["sections"][-1]["words"]
        write(r.p("plan/PLAN.md"), plan + "\nOmega rule.\n")
        words = jokbo.index(r.dir)["sections"][-1]["words"]
        assert "omega" in words and "alpha" not in words, words


def test_an_edit_during_a_build_is_not_labelled_as_read():
    """The key is taken before the files are read: a file edited while the index is built makes the next read build
    again. 1.7.0 took it after, so the entry read before the edit carried the key of the tree after it."""
    with Repo() as r:
        read, edited = jokbo.read_text, []

        def read_then_edit(target, path, *a, **k):
            text = read(target, path, *a, **k)
            if path == "plan/PLAN.md" and not edited:
                edited.append(path)
                write(r.p("plan/PLAN.md"), text + "\nLate rule.\n")
            return text
        jokbo.read_text = read_then_edit
        try:
            first = jokbo.index(r.dir)
        finally:
            jokbo.read_text = read
        assert edited and first["key"] != jokbo.tree_key(r.dir), "the index says the tree it read, not the tree after"
        assert "late" in jokbo.index(r.dir)["sections"][-1]["words"]


def test_text_appended_to_a_section_is_its_change():
    """A section's last change counts every line it holds: lines past the last committed one are new text, so the section
    is uncommitted. 1.7.0 stopped at the last committed line and dated the section by its old text."""
    with Repo() as r:
        jokbo.index(r.dir)
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\nA new rule at the end.\n")
        tags = [s for s in jokbo.index(r.dir)["sections"] if s["id"] == "plan/PLAN.md#Q-tags — tagging"]
        assert tags and tags[0]["changed"] is None, tags


def test_an_edit_elsewhere_leaves_the_last_section_dated():
    """A section another edit did not touch keeps its last change — the last one too: the empty line after a file's final
    newline has no blame, and counting it said the last section of any edited document was uncommitted."""
    with Repo() as r:
        before = {s["id"]: s["changed"] for s in jokbo.index(r.dir)["sections"] if s["file"] == "plan/PLAN.md"}
        text = r.read("plan/PLAN.md")
        head, rest = text.split("\n", 1)
        write(r.p("plan/PLAN.md"), head + " (edited)\n" + rest)
        after = [s for s in jokbo.index(r.dir)["sections"] if s["file"] == "plan/PLAN.md"]
        last = after[-1]
        assert len(after) > 1 and text.endswith("\n") and last["changed"] == before[last["id"]] is not None, (last, before)


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
        # sagoforce 10-26: a sub-agent's report arriving as a prompt got the quotes the person's own questions did not
        note = {"cwd": r.dir, "session_id": "p2", "prompt": "<task-notification>\n<task-id>x</task-id>\n<summary>when does the "
                "nightly snapshot go to the bucket?</summary>\n</task-notification>"}
        hook("session_start.py", {"cwd": r.dir, "session_id": "p2", "source": "startup"})
        assert hook("prompt.py", note) == (0, ""), "a host's report asks nothing"
        assert jokbo.host_notification('Another Claude session sent a message:\n<agent-message from="a1">done</agent-message>')
        assert not jokbo.host_notification("what does <agent-message> mean here?")
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
        # sagoforce 10-26: an Agent call the host launched into the background answers at once; its sub-agent works until
        # the notification naming the call — until then its edits are the session's, not another hand's
        bg = dict(agent, tool_use_id="toolu_bg1")
        hook("pre_tool.py", bg)
        hook("post_tool.py", dict(bg, hook_event_name="PostToolUse", tool_response={"isAsync": True, "status": "async_launched"}))
        assert jokbo.agents_in_flight(r.dir) == 1, "launched, not ended"
        hook("session_start.py", {"cwd": r.dir, "session_id": "f1", "source": "compact"})
        assert "toolu_bg1" in jokbo.session_state(r.dir, "f1")[0]["async-agents"]
        write(r.p("src/feed.ts"), r.read("src/feed.ts") + "// a background sub-agent's\n")
        assert "another hand" not in hook("prompt.py", {"cwd": r.dir, "session_id": "f1", "prompt": "how is it going?"})[1]
        done = "<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>toolu_bg1</tool-use-id>\n<status>completed</status>\n</task-notification>"
        assert "another hand" not in hook("prompt.py", {"cwd": r.dir, "session_id": "f1", "prompt": done})[1]
        assert jokbo.agents_in_flight(r.dir) == 0, "its notification ends it"
        jokbo.agents_in_flight(r.dir, "lost", +1)
        assert jokbo.agents_in_flight(r.dir) == 1
        jokbo.save(r.p(".jokbo/agents.json"), {"lost": {"n": 1, "at": time.time() - jokbo.AGENT_STALE - 1}})
        assert jokbo.agents_in_flight(r.dir) == 0, "an Agent call with no end for hours does not silence the change line"


def test_the_map_names_the_repositories_inside_a_workspace():
    """sagoforce 10-26: a workspace of six repositories, all in its `.gitignore`; its map listed the README and a vendored
    reference, and the rules the person asked about — in the repositories' specs — were read by nothing."""
    with Repo() as r:
        write(r.p(".gitignore"), "/engine/\n")
        eng = r.p("engine")
        os.makedirs(eng)
        subprocess.run(["git", "init", "-q"], cwd=eng, check=True)
        write(os.path.join(eng, "docs", "contract.md"), "# Contract\n\nA cell keeps its rev when the meaning holds.\n")
        write(os.path.join(eng, "CLAUDE.md"), "# Rules\n")
        subprocess.run(["git", "add", "-A"], cwd=eng, check=True)
        m = "\n".join(jokbo.map_report(r.dir))
        assert "repositories inside, each its own map" in m and "engine (" in m and "docs/" in m and "; 2)" in m, m
        assert jokbo.nested_line(eng) is None


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


def test_parallel_hooks_keep_every_agent_count_and_async_call():
    """Hold one hook after reading state while another enters: updates must read after the first commits."""
    worker = r'''import sys
sys.path.insert(0, sys.argv[1])
import jokbo
original = jokbo.load
mode, target, sid, call = sys.argv[2:]
def gated(path, default=None):
    result = original(path, default)
    if path.endswith("agents.json") and mode == "held":
        print("loaded", flush=True)
        sys.stdin.readline()
    return result
jokbo.load = gated
print("entering", flush=True)
if mode in ("held", "count"):
    jokbo.agents_in_flight(target, sid, +1)
else:
    jokbo.agent_answered(target, sid, {"tool_use_id": call, "tool_input": {"run_in_background": True}})
print("done", flush=True)
'''
    for same_session in (False, True):
        with Repo() as r:
            def start(mode, sid, call=""):
                return subprocess.Popen([sys.executable, "-c", worker, PLUGIN, mode, r.dir, sid, call],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            a = start("held", "A")
            jobs = [a]
            try:
                assert a.stdout.readline().strip() == "entering"
                assert a.stdout.readline().strip() == "loaded"
                b = start("count", "A" if same_session else "B")
                jobs.append(b)
                assert b.stdout.readline().strip() == "entering"
                finished = threading.Event()
                def read_done():
                    b.stdout.readline()
                    finished.set()
                reader = threading.Thread(target=read_done, daemon=True)
                reader.start()
                assert not finished.wait(.2), "the second update must wait until the first has saved"
                out, err = a.communicate("continue\n", timeout=10)
                assert a.returncode == 0, (out, err)
                reader.join(timeout=10)
                assert finished.is_set()
                out, err = b.communicate(timeout=10)
                assert b.returncode == 0, (out, err)
                assert jokbo.agents_in_flight(r.dir) == 2
                jokbo.agents_in_flight(r.dir, "A" if same_session else "B", -1)
                assert jokbo.agents_in_flight(r.dir) == 1
                jokbo.agents_in_flight(r.dir, "A", -1)
                assert jokbo.agents_in_flight(r.dir) == 0
                # Parallel asynchronous completions retain every call ID in the shared session file.
                jobs += [start("async", "A", str(i)) for i in range(8)]
                for proc in jobs[2:]:
                    out, err = proc.communicate(timeout=10)
                    assert proc.returncode == 0, (out, err)
                assert set(jokbo.session_state(r.dir, "A")[0]["async-agents"]) == {str(i) for i in range(8)}
            finally:
                for proc in jobs:
                    if proc.poll() is None:
                        proc.kill()
                    proc.communicate()


def ctx_of(out):
    """The additionalContext of a hook's output, "" when it said nothing."""
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


def test_an_edit_command_is_not_a_question_whatever_it_also_searches():
    """A sed that fixes a typo, then a grep that checks it, got a FIND line on the fixed word; so did a heredoc script that
    rewrote README.md followed by a grep checking the edit. A shell command that writes a file inside the project is an
    edit command: `searched` answers none of its searches and records none of them as answered (the same search run alone
    afterwards is answered). A command whose only writes leave the project is still a search. What the pre-tool hook
    counts as the session's own writes is unchanged."""
    with Repo() as r:
        outside = os.path.realpath(tempfile.mkdtemp(prefix="jokbo-outside-"))
        try:
            edits = [
                ("sed -i 's/newest first/newest at the top/' plan/PLAN.md && grep -n \"newest at the top\" plan/PLAN.md",
                 'grep -n "newest at the top" plan/PLAN.md', r.dir),
                ("echo More. >> plan/PLAN.md; grep -n tags plan/PLAN.md", "grep -n tags plan/PLAN.md", r.dir),
                ("python3 - <<'EOF'\nfrom pathlib import Path\np = Path(\"README.md\"); s = p.read_text()\n"
                 "p.write_text(s.replace(\"Run\", \"Use\"))\nEOF\ngrep -rn \"Use\" README.md plan", None, r.dir),
                ("python3 - <<'EOF'\nfrom pathlib import Path\ndef edit(path, old, new):\n    p = Path(path); s = p.read_text(); "
                 "p.write_text(s.replace(old, new))\nedit(\"plan/PLAN.md\", \"tags\", \"labels\")\nEOF\ngrep -rn tags plan",
                 "grep -rn tags plan", r.dir),
                ("cat > plan/NEW.md <<'EOF'\n# New\nEOF\ngrep -rn tags plan", "grep -rn tags plan", r.dir),
                ("cd plan && sed -i 's/newest first/newest at the top/' PLAN.md && grep -n \"newest at the top\" PLAN.md",
                 'grep -n "newest at the top" plan/PLAN.md', r.dir),
            ]
            for i, (command, alone, cwd) in enumerate(edits):
                sid = "e%d" % i
                got = jokbo.searched(r.dir, sid, command, cwd=cwd)
                assert got == [], ("an edit command is not a question", command, got)
                if alone:
                    assert jokbo.searched(r.dir, sid, alone, cwd=r.dir), ("the same search run alone afterwards is answered: "
                                                                          "the edit command recorded it", command, alone)
            bash = lambda c, sid: ctx_of(hook("post_tool.py", {"cwd": r.dir, "session_id": sid, "tool_name": "Bash",
                                                               "tool_input": {"command": c}})[1])
            out = bash(edits[0][0], "eh")
            assert "where the project speaks of" not in out, ("the hook answers no search of an edit command", out)
            for i, c in enumerate(("grep -rn tags --include=*.md . 2>/dev/null | head", "grep -rn tags --include=*.md . > /dev/null",
                                   "grep -rn tags --include=*.md . > %s/hits.txt" % outside)):
                assert jokbo.searched(r.dir, "o%d" % i, c, cwd=r.dir), ("a write that leaves the project: still a search", c)
            files = set(jokbo.index(r.dir)["files"])
            assert jokbo.bash_targets(r.dir, edits[0][0], files) == ["plan/PLAN.md"], "the session's own writes are unchanged"
            assert jokbo.bash_targets(r.dir, edits[2][0], files) == ["README.md"], "the session's own writes are unchanged"
        finally:
            shutil.rmtree(outside, ignore_errors=True)


def test_a_heredoc_body_is_data_and_an_inline_program_is_not_a_search():
    """A heredoc given to cat or to a commit message is data: a grep written inside it is not run. A heredoc given to a
    shell is a script and its search counts. An inline program (python -c, awk, sed -n) searches nothing the hook can
    answer."""
    with Repo() as r:
        files = set(jokbo.index(r.dir)["files"])
        t = lambda c: jokbo.history_targets(r.dir, c, files)
        for data in ("cat <<'EOF'\ngrep -rn \"nightly snapshot\" plan\nEOF", "git commit -q -F - <<'EOF'\ngrep -rn tags plan\nEOF"):
            assert t(data) == [], ("a heredoc body is data, not commands", data, t(data))
        assert t("bash <<'EOF'\ngrep -rn tags plan\nEOF") == [("terms", "tags")], "a heredoc fed to a shell is a script"
        for program in ("python3 -c \"import subprocess; subprocess.run(['grep', '-rn', 'tags', 'plan'])\"",
                        "awk '/tags/ {print}' plan/PLAN.md", "sed -n '/^## Q-tags/,/^## /p' plan/PLAN.md"):
            assert t(program) == [], ("an inline program is not a search", program)


def test_regex_syntax_is_not_a_word():
    """A grep pattern's bracket expressions (POSIX classes too), letter or digit escapes with their argument, inline flag
    groups and quantifier braces are syntax: `^[[:space:]]*-[[:space:]]+tags` searched for tags, not for "space tags"."""
    cases = [("^[[:space:]]*-[[:space:]]+tags", "tags"), ("tags[[:alpha:]]*", "tags"), ("[[:upper:]]tags", "tags"),
             ("\\tremoved", "removed"), ("tags\\x20removed", "tags removed"), ("\\Stags", "tags"), ("(tags)\\1", "tags"),
             ("\\p{L}tags", "tags"), ("(?i)tags", "tags"), ("tags{2,3}", "tags")]
    wrong = [(p, jokbo.pattern_terms(p), want) for p, want in cases if jokbo.pattern_terms(p) != want]
    assert not wrong, ("regex syntax is not a word: (pattern, got, want)", wrong)
    with Repo() as r:
        files = set(jokbo.index(r.dir)["files"])
        got = jokbo.history_targets(r.dir, 'grep -rnE "^[[:space:]]*-[[:space:]]+tags" plan', files)
        assert got == [("terms", "tags")], got


def test_the_terms_are_the_searched_words_as_the_command_wrote_them():
    """A grep for `item-for\\|item-count\\|data-wide-only` was answered on "item count data wide only": identifiers cut
    into fragments. The terms are runs of word characters joined by single hyphens, whole and in their own case,
    deduplicated ignoring case, joined by one space; a run counts when it holds a word; no shell-special character enters
    them, so the printed `find` hint can be pasted."""
    cases = [("item-for\\|item-count\\|API 목록\\|data-wide-only\\|메뉴 1\\|data-sums",
              "item-for item-count API 목록 data-wide-only 메뉴 data-sums"),
             ("MAX_ITEMS\\s*=", "MAX_ITEMS"), ("updatedAt</td>", "updatedAt"), ("at-31", ""),
             ("tags\\|removed", "tags removed"), ("action", "action"), ("Tags\\|tags\\|TAGS", "Tags")]
    wrong = [(p, jokbo.pattern_terms(p), want) for p, want in cases if jokbo.pattern_terms(p) != want]
    assert not wrong, ("the searched words as written: (pattern, got, want)", wrong)
    with Repo() as r:
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\n## Q-items — the item list\n\nThe item-count shows on every page; "
              "data-wide-only marks a column shown on wide screens.\n")
        r.git("add", "-A"); r.git("commit", "-qm", "plan: the item list")
        out = ctx_of(hook("post_tool.py", {"cwd": r.dir, "session_id": "w1", "tool_name": "Bash",
                                           "tool_input": {"command": 'grep -rn "item-count\\|data-wide-only" --include=*.md .'}})[1])
        assert "where the project speaks of: item-count data-wide-only (" in out, out
        assert "find item-count data-wide-only`" in out, out


def notes_fixture(r, plug):
    """A declared `note` read through a hunsu.local.json link that says, of every file, the run that last changed it, the
    concept it realizes and a line naming the file — and a README that names src/feed.ts."""
    write(os.path.join(plug, "note.py"), "import sys\nrun = open(sys.argv[2]).read().strip()\n"
          "print('last changed in ' + run + ' (2026-10-11), task feed')\nprint('concept:feed — the front page, newest first')\n"
          "print('only for ' + sys.argv[1])\n")
    write(os.path.join(plug, "run.txt"), "run-20260901-000000-0000")
    write(r.p("hunsu.local.json"), {"links": {"netty": plug}})
    write(r.p("hunsu.lock.json"), {"reads": {"netty": {"note": ["python3", "{plugin:netty}/note.py", "{path}", os.path.join(plug, "run.txt")]}}})
    write(r.p("README.md"), r.read("README.md") + "\nThe front page is `src/feed.ts`.\n")
    r.git("add", "-A")
    r.git("commit", "-qm", "readme names the feed")


def test_a_resumed_session_is_not_told_again_what_it_was_told():
    """A resume restores the conversation that still holds what jokbo told it: the file read before it says nothing when
    read again, a line told before it does not come again with another file, and where the session started is kept (a
    line naming a run the session committed before the resume is still left out). A startup or a clear with the same
    session id starts over."""
    with Repo() as r:
        plug = tempfile.mkdtemp(prefix="jokbo-plugin-")
        try:
            notes_fixture(r, plug)
            start = lambda src: hook("session_start.py", {"cwd": r.dir, "session_id": "r1", "source": src})
            read = lambda f: ctx_of(hook("post_tool.py", {"cwd": r.dir, "session_id": "r1", "tool_name": "Read", "tool_input": {"file_path": r.p(f)}})[1])
            start("startup")
            write(os.path.join(plug, "run.txt"), "run-20261011-010203-abcd")   # the session's own run, closed after it started
            write(r.p("src/feed.ts"), r.read("src/feed.ts") + "// newest first\n")
            r.git("commit", "-qam", "run-20261011-010203-abcd: close — feed comment")
            feed = read("src/feed.ts")
            assert "concept:feed" in feed and "last changed in" not in feed, feed
            start("resume")
            again = read("src/feed.ts")
            assert again == "", ("a file told before the resume is not told again after it", again)
            tags = read("src/tags.ts")
            assert "only for src/tags.ts" in tags, tags
            assert "concept:feed" not in tags, ("a line told before the resume does not come again with another file", tags)
            assert "last changed in" not in tags, ("a resume keeps where the session started", tags)
            start("startup")
            assert "concept:feed" in read("src/feed.ts"), "a startup with the same session id starts over"
            start("clear")
            assert "concept:feed" in read("src/feed.ts"), "a clear with the same session id starts over"
        finally:
            shutil.rmtree(plug, ignore_errors=True)


def test_a_resume_answers_no_search_again_and_quotes_no_section_again_but_keeps_the_change_line():
    """After a resume, a shell search answered before it is not answered again and a section quoted to a prompt before it
    is not quoted again. What a resume does not keep: the agent count is reset (an unfinished Agent call does not silence
    the change line), and a commit by another hand between calls is said at the next prompt, once."""
    with Repo() as r:
        write(r.p("plan/BACKEND.md"), "# Backend\n\n## Backup\n\nA nightly snapshot goes to the bucket at three in the morning.\n\n"
              "## Deploy\n\nPush to main and the worker is deployed.\n")
        r.git("add", "-A"); r.git("commit", "-qm", "backend plan")
        sid = "r2"
        bash = lambda c: ctx_of(hook("post_tool.py", {"cwd": r.dir, "session_id": sid, "tool_name": "Bash", "tool_input": {"command": c}})[1])
        ask = lambda q: ctx_of(hook("prompt.py", {"cwd": r.dir, "session_id": sid, "prompt": q})[1])
        question = "when does the nightly snapshot go to the bucket?"
        hook("session_start.py", {"cwd": r.dir, "session_id": sid, "source": "startup"})
        assert "where the project speaks of: tags removed" in bash("grep -rn 'tags\\|removed' ."), "answered before the resume"
        assert "nightly snapshot" in ask(question), "quoted before the resume"
        hook("pre_tool.py", {"cwd": r.dir, "session_id": sid, "tool_name": "Agent", "tool_input": {"prompt": "do it"}})
        assert jokbo.agents_in_flight(r.dir) == 1
        hook("session_start.py", {"cwd": r.dir, "session_id": sid, "source": "resume"})
        out = bash("grep -rn 'tags\\|removed' .")
        assert "where the project speaks of" not in out, ("a search answered before the resume is not answered again", out)
        out = ask(question)
        assert "nightly snapshot" not in out, ("a section quoted before the resume is not quoted again", out)
        assert jokbo.agents_in_flight(r.dir) == 0, "a resume resets the agent count"
        write(r.p("plan/PLAN.md"), r.read("plan/PLAN.md") + "\n## Q-about\n\nOne page.\n")
        r.git("commit", "-qam", "owner: about page")
        out = ask("go on")
        assert "changed by another hand" in out and "owner: about page" in out, ("the change line still works after a resume", out)
        assert "changed by another hand" not in ask("go on"), "said once"


def engine_fixture(r, base, record=True, with_engine=True):
    """hunsu.json declares jokbo 9.9.0 from marketplace jm; a copy elsewhere holds jokbo.py (when `with_engine`) and a
    manifest saying 9.9.0, recorded (when `record`) in the Claude install records. Returns the copy's directory."""
    write(r.p("hunsu.json"), {"plugins": {"jokbo": {"version": "9.9.0", "marketplace": "jm"}}})
    copy = os.path.join(base, "installs", "jokbo", "9.9.0")
    write(os.path.join(copy, ".claude-plugin", "plugin.json"), {"name": "jokbo", "version": "9.9.0"})
    if with_engine:
        write(os.path.join(copy, "jokbo.py"), "# the copy the lock pins\n")
    if record:
        write(os.path.join(base, "claude-home", "plugins", "installed_plugins.json"),
              {"version": 2, "plugins": {"jokbo@jm": [{"scope": "user", "installPath": copy}]}})
    return copy.replace(os.sep, "/")


def engine_hints(r, sid):
    """Every hint that spells out the engine, as the hooks (subprocesses with os.environ) and prompt_report give them:
    {name: text}."""
    out = {"map": ctx_of(hook("session_start.py", {"cwd": r.dir, "session_id": sid, "source": "startup"})[1])}
    out["more"] = ctx_of(hook("post_tool.py", {"cwd": r.dir, "session_id": sid, "tool_name": "Read",
                                               "tool_input": {"file_path": r.p("src/feed.ts")}})[1])
    bash = lambda c: ctx_of(hook("post_tool.py", {"cwd": r.dir, "session_id": sid, "tool_name": "Bash", "tool_input": {"command": c}})[1])
    out["terms"] = bash("grep -rn tags --include=*.md .")
    out["path"] = bash("git log -3 -- src/tags.ts")
    out["prompt"] = "\n".join(jokbo.prompt_report(r.dir, "when does the nightly snapshot go to the bucket?")[0])
    return out


def engine_repo(r):
    """The Repo with what each hint needs: a README naming src/feed.ts (a note's `more:`), a backup section (a prompt's
    quote), a repository inside the workspace (the map's nested line)."""
    write(r.p("README.md"), r.read("README.md") + "\nThe front page is `src/feed.ts`.\n")
    write(r.p("plan/BACKEND.md"), "# Backend\n\n## Storage\n\nDrafts are kept in the database; nothing is published until the owner "
          "presses publish.\n\n## Backup\n\nA nightly snapshot goes to the bucket at three in the morning.\n\n## Deploy\n\nPush to main "
          "and the worker is deployed.\n")
    write(r.p(".gitignore"), "/engine/\n")
    r.git("add", "-A"); r.git("commit", "-qm", "readme names the feed; backend plan")
    eng = r.p("engine")
    os.makedirs(eng)
    subprocess.run(["git", "init", "-q"], cwd=eng, check=True)
    write(os.path.join(eng, "docs", "contract.md"), "# Contract\n\nA rule.\n")
    subprocess.run(["git", "add", "-A"], cwd=eng, check=True)


def test_the_hints_name_the_engine_the_lock_resolves_not_the_loaded_copy():
    """A session that loaded an older copy than the one the project's lock now pins was pointed at the old copy by every
    hint that spells out the engine. The path is the jokbo.py in the root the resolver gives for jokbo for this project and
    host, whatever its version note: the map's ask line and nested line, a note's `more:`, both search answer headers and
    the prompt quote header."""
    loaded = os.path.join(PLUGIN, "jokbo.py").replace(os.sep, "/")
    with Repo() as r, pinned_host("claude-code") as base:
        engine_repo(r)
        copy = engine_fixture(r, base)
        want = '"%s/jokbo.py"' % copy
        hints = engine_hints(r, "k1")
        assert "repositories inside" in hints["map"] and "more: `" in hints["more"] and "find tags" in hints["terms"] \
            and "file src/tags.ts" in hints["path"] and "find WORDS" in hints["prompt"], hints
        for name, text in hints.items():
            assert want in text, ("the %s hint names the engine the lock resolves" % name, want, text)
            assert loaded not in text, ("the %s hint does not name the loaded copy" % name, text)
        nested = next(l for l in hints["map"].split("\n") if "repositories inside" in l)
        assert want in nested, nested
        assert jokbo.engine_path(r.dir) == copy + "/jokbo.py", jokbo.engine_path(r.dir)


def test_a_linked_engine_is_named_and_without_a_resolved_engine_the_loaded_copy_is():
    """A hunsu.local.json link to a directory holding jokbo.py makes the hints name it. With no install record, with
    nothing declared or installed, or with a resolved root that holds no jokbo.py, the hints name the loaded copy, as
    before."""
    loaded = os.path.join(PLUGIN, "jokbo.py").replace(os.sep, "/")
    with Repo() as r, pinned_host("claude-code") as base:
        engine_repo(r)
        linked = os.path.join(base, "linked")
        write(os.path.join(linked, "jokbo.py"), "# a local checkout\n")
        write(r.p("hunsu.local.json"), {"links": {"jokbo": linked}})
        want = '"%s/jokbo.py"' % linked.replace(os.sep, "/")
        for name, text in engine_hints(r, "l1").items():
            assert want in text and loaded not in text, ("the %s hint names the linked engine" % name, want, text)
        assert jokbo.engine_path(r.dir) == linked.replace(os.sep, "/") + "/jokbo.py", jokbo.engine_path(r.dir)
        os.remove(r.p("hunsu.local.json"))
        cases = [("no install record", dict(record=False)), ("a root without jokbo.py", dict(with_engine=False))]
        for i, (case, kw) in enumerate(cases):
            shutil.rmtree(os.path.join(base, "installs"), ignore_errors=True)
            shutil.rmtree(os.path.join(base, "claude-home", "plugins"), ignore_errors=True)
            copy = engine_fixture(r, base, **kw)
            for name, text in engine_hints(r, "f%d" % i).items():
                assert '"%s"' % loaded in text and copy not in text, (case, "the %s hint names the loaded copy" % name, text)
            assert jokbo.engine_path(r.dir) == jokbo.engine_path(), (case, jokbo.engine_path(r.dir))
        os.remove(r.p("hunsu.json"))
        shutil.rmtree(os.path.join(base, "claude-home", "plugins"), ignore_errors=True)
        for name, text in engine_hints(r, "f9").items():
            assert '"%s"' % loaded in text, ("nothing declared or installed: the %s hint names the loaded copy" % name, text)
        assert jokbo.engine_path(r.dir) == jokbo.engine_path()


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
