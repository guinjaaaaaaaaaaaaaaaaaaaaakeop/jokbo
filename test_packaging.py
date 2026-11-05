"""Packaging contract for jokbo: the installable payload lives in plugin/.

Run from the repository root:  python3 -m unittest test_packaging.py

Standard library only.  Nothing here imports the repository; the engine is
reached only through subprocess.  Nothing is written under ROOT: copies and
fixtures live in fresh temporary directories outside any git work tree.

Test names carry the contract item they decide: C1 payload, C2 root layout,
C3 root shim, C4 manifests and catalogs, C5 standalone copy, C6 real fixture
workflow from the copy, C7 {plugin:NAME} following a linked repository's
catalog in a real declared read.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin")

EXPECTED_PAYLOAD = sorted([
    "plugin/plugin.json",
    "plugin/.claude-plugin/plugin.json",
    "plugin/.codex-plugin/plugin.json",
    "plugin/jokbo.py",
    "plugin/README.md",
    "plugin/LICENSE",
    "plugin/commands/file.md",
    "plugin/commands/find.md",
    "plugin/commands/since.md",
    "plugin/skills/read/SKILL.md",
    "plugin/hooks/hooks.json",
    "plugin/hooks/codex.json",
    "plugin/hooks/_common.py",
    "plugin/hooks/session_start.py",
    "plugin/hooks/prompt.py",
    "plugin/hooks/pre_tool.py",
    "plugin/hooks/post_tool.py",
    "plugin/hooks/stop.py",
])

FORBIDDEN_COMPONENTS = {
    "AGENTS.md", "CLAUDE.md", "hunsu.json", "hunsu.lock.json",
    "hunsu.local.json", "hunsu-judgments.json", "hunsu-conflicts.md",
    "marketplace.json", ".chongdae", ".dwitbuk", ".jokbo",
    ".hunsu-bootstrap", ".git", ".claude", ".codex", ".agents", "reviews",
    "__pycache__",
}

POISON = "raise SystemExit('imported from outside the payload')\n"
POISON_MSG = "imported from outside the payload"

README_TEXT = "# Kestrel site\n\nRun the build with make.\n"
FEED_TEXT = "export const feed = 1;\n"
PLAN_TEXT = ("# Plan\n\n## Feed\n\nThe kestrel feed lists every post newest "
             "first: see `src/feed.ts`.\n")
FIRST_SUBJECT = "first: the kestrel feed"
SECOND_SUBJECT = "plan: say how the kestrel feed orders posts"

GIT_ID = ["-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture"]

TIMEOUT = 60


# --------------------------------------------------------------- helpers

def child_env():
    env = dict(os.environ)
    for key in ("AGENT_WORKER", "HUNSU_SURVEY", "GIT_DIR", "GIT_WORK_TREE",
                "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES", "PYTHONPATH",
                "PYTHONSTARTUP", "PYTHONHOME"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run(argv, cwd, stdin=None):
    return subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True,
                          text=True, env=child_env(), timeout=TIMEOUT)


def py(argv, cwd, stdin=None):
    return run([sys.executable] + list(argv), cwd, stdin)


def fresh_dir(test):
    path = os.path.realpath(tempfile.mkdtemp(prefix="jokbo-pkg-"))
    test.addCleanup(shutil.rmtree, path, True)
    return path


def in_work_tree(path):
    r = run(["git", "rev-parse", "--show-toplevel"], path)
    return r.returncode == 0


def payload_files():
    """Paths (relative to ROOT, 'plugin/...') git would ship, existing on disk."""
    r = run(["git", "ls-files", "-z", "--cached", "--others",
             "--exclude-standard", "--", "plugin"], ROOT)
    if r.returncode != 0:
        return None
    out = []
    for p in r.stdout.split("\0"):
        if p and os.path.lexists(os.path.join(ROOT, p)):
            out.append(p)
    return sorted(set(out))


def load_json(path):
    """Return (data, error) — never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001 - reported as assertion
        return None, "%s: %r" % (path, exc)


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except Exception:  # noqa: BLE001
        return None


def tree_hashes(base):
    out = {}
    for dirpath, dirnames, filenames in os.walk(base):
        for d in dirnames:
            rel = os.path.relpath(os.path.join(dirpath, d), base)
            out[rel + "/"] = "dir"
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, base)
            with open(full, "rb") as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def make_fixture(test):
    """Fixture repository F in a fresh temp dir; returns (F, error)."""
    base = fresh_dir(test)
    if in_work_tree(base):
        return None, "fixture temp dir %s is inside a git work tree" % base
    f = os.path.join(base, "site")
    os.makedirs(os.path.join(f, "src"))
    with open(os.path.join(f, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README_TEXT)
    with open(os.path.join(f, "src", "feed.ts"), "w", encoding="utf-8") as fh:
        fh.write(FEED_TEXT)
    steps = [
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git"] + GIT_ID + ["commit", "-q", "-m", FIRST_SUBJECT],
    ]
    for argv in steps:
        r = run(argv, f)
        if r.returncode != 0:
            return None, "fixture %s failed: %s" % (argv, r.stderr)
    os.makedirs(os.path.join(f, "plan"))
    with open(os.path.join(f, "plan", "PLAN.md"), "w", encoding="utf-8") as fh:
        fh.write(PLAN_TEXT)
    for argv in (["git", "add", "-A"],
                 ["git"] + GIT_ID + ["commit", "-q", "-m", SECOND_SUBJECT]):
        r = run(argv, f)
        if r.returncode != 0:
            return None, "fixture %s failed: %s" % (argv, r.stderr)
    return f, None


def make_copy(test):
    """Copy only the payload into T/payload/jokbo, poison T and T/payload.

    Returns (COPY, error).  Never raises for a missing plugin/.
    """
    if not os.path.isdir(PLUGIN):
        return None, "plugin/ does not exist at %s" % PLUGIN
    files = payload_files()
    if not files:
        return None, "git lists no payload files under plugin/"
    t = fresh_dir(test)
    if in_work_tree(t):
        return None, "temp dir %s is inside a git work tree" % t
    copy = os.path.join(t, "payload", "jokbo")
    for rel in files:
        src = os.path.join(ROOT, rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(copy, os.path.relpath(rel, "plugin"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    for d in (t, os.path.join(t, "payload")):
        for name in ("jokbo.py", "_common.py"):
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(POISON)
    return copy, None


# ---- Markdown links

_FENCE = re.compile(r"^\s{0,3}(```|~~~).*?^\s{0,3}\1[^\n]*$", re.M | re.S)
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_INLINE_LINK = re.compile(
    r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
_REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)", re.M)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")


def strip_code(text):
    text = _COMMENT.sub("", text)
    text = _FENCE.sub("", text)
    return _INLINE_CODE.sub("", text)


def md_links(text):
    body = strip_code(text)
    out = []
    for m in _INLINE_LINK.finditer(body):
        out.append(m.group(1))
    for m in _REF_DEF.finditer(body):
        out.append(m.group(1))
    return [t[1:-1] if t.startswith("<") and t.endswith(">") else t
            for t in out]


def gh_slug(heading):
    s = heading.strip().lower()
    s = "".join(ch for ch in s if ch.isalnum() or ch in " -_")
    return s.replace(" ", "-")


def md_slugs(text):
    slugs, seen = set(), {}
    for line in _FENCE.sub("", text).splitlines():
        m = _HEADING.match(line)
        if not m:
            continue
        title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(2))
        slug = gh_slug(title)
        slugs.add(slug)
        n = seen.get(slug, 0)
        if n:
            slugs.add("%s-%d" % (slug, n))
        seen[slug] = n + 1
    return slugs


def link_problems(md_path, base):
    """Problems with relative links of md_path that must stay inside base."""
    problems = []
    text = read_text(md_path)
    if text is None:
        return ["%s cannot be read" % md_path]
    base = os.path.realpath(base)
    for target in md_links(text):
        if _SCHEME.match(target):
            continue
        path_part, _, anchor = target.partition("#")
        path_part = urllib.parse.unquote(path_part)
        anchor = urllib.parse.unquote(anchor)
        if path_part == "":
            dest = md_path
        elif path_part.startswith("/"):
            dest = os.path.join(base, path_part.lstrip("/"))
        else:
            dest = os.path.join(os.path.dirname(md_path), path_part)
        dest = os.path.normpath(dest)
        real = os.path.realpath(dest)
        where = "%s: link %r" % (os.path.relpath(md_path, base), target)
        if os.path.commonpath([base, dest]) != base or \
                os.path.commonpath([base, real]) != base:
            problems.append(where + " leaves %s" % base)
            continue
        if not os.path.exists(dest):
            problems.append(where + " does not resolve (%s)" % dest)
            continue
        if anchor:
            if not (os.path.isfile(dest) and dest.endswith(".md")):
                problems.append(where + " has an anchor on a non-Markdown target")
                continue
            if anchor.lower() not in md_slugs(read_text(dest) or ""):
                problems.append(where + " anchor #%s matches no heading" % anchor)
    return problems


def hook_commands(data):
    """[(event, command)] from a hooks adapter file."""
    out = []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        for group in groups or []:
            for h in (group or {}).get("hooks", []) or []:
                if isinstance(h, dict) and "command" in h:
                    out.append((event, h["command"]))
    return out


# --------------------------------------------------------------- C1

class C1Payload(unittest.TestCase):

    def test_C1_payload_is_exactly_the_listed_files(self):
        self.assertTrue(os.path.isdir(PLUGIN), "plugin/ does not exist")
        files = payload_files()
        self.assertIsNotNone(files, "git ls-files failed at ROOT")
        missing = sorted(set(EXPECTED_PAYLOAD) - set(files))
        extra = sorted(set(files) - set(EXPECTED_PAYLOAD))
        self.assertEqual((missing, extra), ([], []),
                         "payload differs: missing %s, extra %s" % (missing, extra))

    def test_C1_payload_has_no_development_state_or_test(self):
        self.assertTrue(os.path.isdir(PLUGIN), "plugin/ does not exist")
        files = payload_files()
        self.assertTrue(files, "git lists no payload files under plugin/")
        for path in files:
            for comp in path.split("/"):
                self.assertNotIn(comp, FORBIDDEN_COMPONENTS,
                                 "payload path %s carries %s" % (path, comp))
                self.assertFalse(comp.startswith("test_") and comp.endswith(".py"),
                                 "payload path %s is a test" % path)
                self.assertFalse(comp.endswith(".pyc"),
                                 "payload path %s is bytecode" % path)

    def test_C1_nothing_under_plugin_is_a_symlink(self):
        self.assertTrue(os.path.isdir(PLUGIN), "plugin/ does not exist")
        self.assertFalse(os.path.islink(PLUGIN), "plugin/ itself is a symlink")
        for dirpath, dirnames, filenames in os.walk(PLUGIN):
            for name in dirnames + filenames:
                full = os.path.join(dirpath, name)
                self.assertFalse(os.path.islink(full),
                                 "%s is a symlink" % os.path.relpath(full, ROOT))

    def test_C1_plugin_license_is_byte_identical_to_root_license(self):
        lic = os.path.join(PLUGIN, "LICENSE")
        self.assertTrue(os.path.isfile(lic), "plugin/LICENSE does not exist")
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "LICENSE")),
                        "ROOT/LICENSE does not exist")
        with open(lic, "rb") as a, open(os.path.join(ROOT, "LICENSE"), "rb") as b:
            self.assertEqual(a.read(), b.read(),
                             "plugin/LICENSE differs from ROOT/LICENSE")

    def test_C1_plugin_readme_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(PLUGIN, "README.md")),
                        "plugin/README.md does not exist")


# --------------------------------------------------------------- C2

class C2RootLayout(unittest.TestCase):

    def test_C2_runtime_is_gone_from_the_root(self):
        for rel in ("plugin.json", "hooks", "commands", "skills",
                    ".codex-plugin", ".claude-plugin/plugin.json"):
            self.assertFalse(os.path.lexists(os.path.join(ROOT, rel)),
                             "%s is still at the root" % rel)

    def test_C2_development_files_stay_at_the_root(self):
        self.assertTrue(os.path.isdir(PLUGIN), "plugin/ does not exist")
        for rel in ("test_jokbo.py", "AGENTS.md", "CLAUDE.md", "README.md",
                    "LICENSE", "hunsu.json", "hunsu.lock.json",
                    ".claude-plugin/marketplace.json",
                    ".agents/plugins/marketplace.json"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)),
                            "%s is missing at the root" % rel)
            if rel not in ("README.md", "LICENSE"):
                self.assertFalse(os.path.lexists(os.path.join(PLUGIN, rel)),
                                 "%s was moved into plugin/" % rel)

    def test_C2_claude_md_is_exactly_the_agents_include(self):
        self.assertTrue(os.path.isdir(PLUGIN), "plugin/ does not exist")
        path = os.path.join(ROOT, "CLAUDE.md")
        self.assertTrue(os.path.isfile(path), "CLAUDE.md missing")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), b"@AGENTS.md\n")

    def test_C2_root_readme_links_resolve_inside_root(self):
        self.assertTrue(os.path.isdir(PLUGIN), "plugin/ does not exist")
        path = os.path.join(ROOT, "README.md")
        self.assertTrue(os.path.isfile(path), "README.md missing")
        self.assertEqual(link_problems(path, ROOT), [])

    def test_C2_agents_md_links_resolve_inside_root(self):
        self.assertTrue(os.path.isdir(PLUGIN), "plugin/ does not exist")
        path = os.path.join(ROOT, "AGENTS.md")
        self.assertTrue(os.path.isfile(path), "AGENTS.md missing")
        self.assertEqual(link_problems(path, ROOT), [])


# --------------------------------------------------------------- C3

class C3RootShim(unittest.TestCase):

    def _shim_text(self):
        path = os.path.join(ROOT, "jokbo.py")
        self.assertTrue(os.path.isfile(path), "ROOT/jokbo.py does not exist")
        return read_text(path) or ""

    def test_C3_root_jokbo_is_a_thin_shim(self):
        text = self._shim_text()
        self.assertLess(len(text.splitlines()), 40,
                        "ROOT/jokbo.py has %d lines" % len(text.splitlines()))
        self.assertNotIn("argparse", text)
        self.assertNotIn("def ", text)
        self.assertIn("plugin", text)

    def _same(self, argv):
        self._shim_text()
        engine = os.path.join(PLUGIN, "jokbo.py")
        self.assertTrue(os.path.isfile(engine), "plugin/jokbo.py does not exist")
        f, err = make_fixture(self)
        self.assertIsNone(err, err)
        argv = [a.replace("{F}", f) for a in argv]
        a = py([os.path.join(ROOT, "jokbo.py")] + argv, f)
        b = py([engine] + argv, f)
        self.assertEqual((a.returncode, a.stdout), (b.returncode, b.stdout),
                         "shim and engine differ for %s\nshim stderr: %s"
                         % (argv, a.stderr))
        return a, b

    def test_C3_shim_help_equals_engine(self):
        a, _ = self._same(["--help"])
        self.assertEqual(a.returncode, 0, a.stderr)

    def test_C3_shim_map_equals_engine(self):
        a, _ = self._same(["map", "--target", "{F}"])
        self.assertEqual(a.returncode, 0, a.stderr)

    def test_C3_shim_file_without_argument_equals_engine(self):
        self._same(["file"])

    def test_C3_fresh_root_import_is_the_plugin_engine_module(self):
        self._shim_text()
        engine = os.path.join(PLUGIN, "jokbo.py")
        self.assertTrue(os.path.isfile(engine), "plugin/jokbo.py does not exist")
        v = shared_version(self)
        code = (
            "import json, os, sys\n"
            "try:\n"
            "    import jokbo\n"
            "    out = {}\n"
            "    out['file'] = os.path.realpath(jokbo.__file__)\n"
            "    out['is_sys_module'] = sys.modules['jokbo'] is jokbo\n"
            "    out['globals_are_dict'] = ("
            "jokbo.plugin_root.__globals__ is jokbo.__dict__ and "
            "jokbo.main.__globals__ is jokbo.__dict__)\n"
            "    out['version'] = jokbo.version()\n"
            "    jokbo.load = lambda path, default=None: "
            "{'links': {'zz': '/nonexistent-jokbo-zz'}} "
            "if str(path).endswith('hunsu.local.json') else {}\n"
            "    out['patched'] = jokbo.plugin_root("
            "'/nonexistent-jokbo-target', 'zz')\n"
            "except BaseException as e:\n"
            "    print('C3-PROBE-FAILED:' + type(e).__name__ + ':' + str(e))\n"
            "    sys.exit(5)\n"
            "print(json.dumps(out))\n"
        )
        r = py(["-c", code], ROOT)
        self.assertEqual(r.returncode, 0,
                         "bare 'import jokbo' at the checkout root failed: %s %s"
                         % (r.stdout, r.stderr))
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines, "probe printed nothing: %s" % r.stderr)
        try:
            out = json.loads(lines[-1])
        except ValueError:
            out = None
        self.assertIsInstance(out, dict, "probe output is not JSON: %r" % r.stdout)
        self.assertEqual(out.get("file"), os.path.realpath(engine))
        self.assertIs(out.get("is_sys_module"), True,
                      "sys.modules['jokbo'] is not the imported module")
        self.assertIs(out.get("globals_are_dict"), True,
                      "the engine's functions do not use jokbo.__dict__ as globals")
        self.assertEqual(out.get("version"), v)
        self.assertEqual(out.get("patched"), "/nonexistent-jokbo-zz",
                         "the engine's plugin_root did not see the patched load")


# --------------------------------------------------------------- C4

def manifest_versions(test):
    paths = [os.path.join(PLUGIN, "plugin.json"),
             os.path.join(PLUGIN, ".claude-plugin", "plugin.json"),
             os.path.join(PLUGIN, ".codex-plugin", "plugin.json")]
    out = []
    for p in paths:
        test.assertTrue(os.path.isfile(p), "%s does not exist" % p)
        data, err = load_json(p)
        test.assertIsNone(err, err)
        test.assertIsInstance(data, dict, p)
        out.append((p, data))
    return out


def shared_version(test):
    ms = manifest_versions(test)
    versions = {d.get("version") for _, d in ms}
    test.assertEqual(len(versions), 1, "manifest versions differ: %s" % versions)
    v = versions.pop()
    test.assertIsInstance(v, str)
    test.assertTrue(v, "empty version")
    return v


class C4Manifests(unittest.TestCase):

    def test_C4_three_manifests_share_name_version_and_python(self):
        ms = manifest_versions(self)
        for p, d in ms:
            self.assertEqual(d.get("name"), "jokbo", p)
            self.assertEqual((d.get("engines") or {}).get("python"), ">=3.11", p)
        shared_version(self)

    def test_C4_claude_marketplace_selects_plugin_with_version(self):
        v = shared_version(self)
        path = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
        self.assertTrue(os.path.isfile(path), path)
        data, err = load_json(path)
        self.assertIsNone(err, err)
        self.assertEqual((data.get("metadata") or {}).get("version"), v)
        entries = [e for e in data.get("plugins", []) if e.get("name") == "jokbo"]
        self.assertEqual(len(entries), 1, "no single jokbo entry")
        self.assertEqual(entries[0].get("version"), v)
        self.assertEqual(entries[0].get("source"), "./plugin")
        self._source_is_plugin(entries[0].get("source"))

    def test_C4_agents_marketplace_selects_plugin_without_version(self):
        self.assertTrue(os.path.isdir(PLUGIN), "plugin/ does not exist")
        path = os.path.join(ROOT, ".agents", "plugins", "marketplace.json")
        self.assertTrue(os.path.isfile(path), path)
        data, err = load_json(path)
        self.assertIsNone(err, err)
        self.assertNotIn("version", data, "top-level version key present")
        entries = [e for e in data.get("plugins", []) if e.get("name") == "jokbo"]
        self.assertEqual(len(entries), 1, "no single jokbo entry")
        self.assertEqual(entries[0].get("source"),
                         {"source": "local", "path": "./plugin"})
        self.assertNotIn("version", entries[0], "entry has a version key")
        self._source_is_plugin(entries[0]["source"]["path"])

    def _source_is_plugin(self, rel):
        self.assertIsInstance(rel, str)
        joined = os.path.normpath(os.path.join(ROOT, rel))
        self.assertEqual(joined, PLUGIN)
        self.assertEqual(os.path.commonpath([ROOT, joined]), ROOT)
        self.assertTrue(os.path.isfile(os.path.join(joined, ".claude-plugin", "plugin.json")))
        self.assertTrue(os.path.isfile(os.path.join(joined, ".codex-plugin", "plugin.json")))

    def test_C4_engine_from_copy_reports_the_manifest_version(self):
        v = shared_version(self)
        copy, err = make_copy(self)
        self.assertIsNone(err, err)
        f, err = make_fixture(self)
        self.assertIsNone(err, err)
        code = ("import sys; sys.path.insert(0, %r); import jokbo; "
                "print(jokbo.version())") % copy
        r = py(["-c", code], f)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), v)
        r = py([os.path.join(copy, "jokbo.py"), "index", "--target", f], f)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(("jokbo " + v) in line for line in r.stdout.splitlines()),
                        "index stdout has no 'jokbo %s': %s" % (v, r.stdout))


# --------------------------------------------------------------- C5

class C5StandaloneCopy(unittest.TestCase):

    def _copy(self):
        copy, err = make_copy(self)
        self.assertIsNone(err, err)
        self.assertTrue(os.path.isdir(copy), "copy %s not made" % copy)
        return copy

    def test_C5_copy_dir_is_in_no_work_tree(self):
        copy = self._copy()
        self.assertFalse(in_work_tree(copy), "%s is in a git work tree" % copy)
        self.assertEqual(sorted(os.path.join("plugin", p) for p in tree_hashes(copy)
                                if not p.endswith("/")), EXPECTED_PAYLOAD)

    def test_C5_manifest_paths_resolve_inside_copy(self):
        copy = self._copy()
        pj, err = load_json(os.path.join(copy, "plugin.json"))
        self.assertIsNone(err, err)
        hooks = ((pj.get("extensions") or {}).get("com.openai") or {}).get("hooks")
        self.assertEqual(hooks, "./hooks/codex.json")
        cx, err = load_json(os.path.join(copy, ".codex-plugin", "plugin.json"))
        self.assertIsNone(err, err)
        self.assertEqual(cx.get("skills"), "./skills/")
        self.assertTrue(os.path.isfile(os.path.normpath(os.path.join(copy, hooks))))
        skills = os.path.normpath(os.path.join(copy, cx["skills"]))
        self.assertTrue(os.path.isdir(skills))
        self.assertTrue(os.path.isfile(os.path.join(skills, "read", "SKILL.md")))
        # every relative path any manifest names
        for rel in ("plugin.json", ".claude-plugin/plugin.json",
                    ".codex-plugin/plugin.json"):
            data, err = load_json(os.path.join(copy, rel))
            self.assertIsNone(err, err)
            for s in _strings(data):
                if s.startswith("./") or s.startswith("../"):
                    self.assertNotIn("..", s.split("/"), "%s names %s" % (rel, s))
                    dest = os.path.normpath(os.path.join(copy, s))
                    self.assertEqual(os.path.commonpath([copy, dest]), copy,
                                     "%s: %s leaves the copy" % (rel, s))
                    self.assertTrue(os.path.exists(dest),
                                    "%s: %s does not exist in the copy" % (rel, s))

    def _hook_scripts(self, copy, adapter, placeholder):
        path = os.path.join(copy, "hooks", adapter)
        self.assertTrue(os.path.isfile(path), path)
        data, err = load_json(path)
        self.assertIsNone(err, err)
        cmds = hook_commands(data)
        self.assertTrue(cmds, "%s has no hook commands" % adapter)
        pairs = set()
        for event, cmd in cmds:
            self.assertIn(placeholder, cmd, "%s %s: %s" % (adapter, event, cmd))
            argv = shlex.split(cmd.replace(placeholder, copy))
            self.assertGreaterEqual(len(argv), 2, cmd)
            self.assertEqual(argv[0], "python3", cmd)
            script = os.path.normpath(argv[1])
            self.assertTrue(script.endswith(".py"), cmd)
            self.assertEqual(os.path.dirname(script), os.path.join(copy, "hooks"), cmd)
            self.assertTrue(os.path.isfile(script), "%s does not exist" % script)
            pairs.add((event, os.path.basename(script)))
        return pairs

    def test_C5_hook_commands_run_scripts_in_copy(self):
        copy = self._copy()
        claude = self._hook_scripts(copy, "hooks.json", "${CLAUDE_PLUGIN_ROOT}")
        codex = self._hook_scripts(copy, "codex.json", "${PLUGIN_ROOT}")
        self.assertEqual(claude, codex, "adapters differ")

    def test_C5_commands_run_the_copy_engine(self):
        copy = self._copy()
        self.assertTrue(os.path.isfile(os.path.join(copy, "jokbo.py")))
        for name in ("file", "find", "since"):
            path = os.path.join(copy, "commands", name + ".md")
            self.assertTrue(os.path.isfile(path), path)
            self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/jokbo.py" %s $ARGUMENTS --target .'
                          % name, read_text(path) or "")

    def test_C5_markdown_links_stay_inside_copy(self):
        copy = self._copy()
        mds = []
        for dirpath, _, filenames in os.walk(copy):
            mds += [os.path.join(dirpath, n) for n in filenames if n.endswith(".md")]
        self.assertTrue(any(p.endswith("README.md") for p in mds), "no README.md")
        problems = []
        for md in mds:
            problems += link_problems(md, copy)
        self.assertEqual(problems, [])


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


# --------------------------------------------------------------- C6

HOOKS = [
    ("session_start.py", "SessionStart", {"source": "startup"}),
    ("prompt.py", "UserPromptSubmit",
     {"prompt": "how does the kestrel feed order posts?"}),
    ("pre_tool.py", "PreToolUse",
     {"tool_name": "Read", "tool_input": {"file_path": "src/feed.ts"}}),
    ("post_tool.py", "PostToolUse",
     {"tool_name": "Read", "tool_input": {"file_path": "src/feed.ts"}}),
    ("stop.py", "Stop", {}),
]


class C6Workflow(unittest.TestCase):

    def _setup(self):
        copy, err = make_copy(self)
        self.assertIsNone(err, err)
        engine = os.path.join(copy, "jokbo.py")
        self.assertTrue(os.path.isfile(engine), "%s does not exist" % engine)
        f, err = make_fixture(self)
        self.assertIsNone(err, err)
        return copy, f

    def _cli(self, copy, f, *argv):
        r = py([os.path.join(copy, "jokbo.py")] + list(argv), f)
        self.assertNotIn(POISON_MSG, r.stdout + r.stderr, argv)
        self.assertEqual(r.returncode, 0, "%s: %s" % (argv, r.stderr))
        return r.stdout

    def _hook(self, copy, f, script, event, extra):
        payload = {"cwd": f, "session_id": "pkg-1", "hook_event_name": event}
        payload.update(extra)
        r = py([os.path.join(copy, "hooks", script)], f, json.dumps(payload))
        self.assertNotIn(POISON_MSG, r.stdout + r.stderr, script)
        self.assertEqual(r.returncode, 0, "%s: %s" % (script, r.stderr))
        out = r.stdout.strip()
        if not out:
            return None
        try:
            obj = json.loads(out)
        except ValueError:
            self.fail("%s stdout is not one JSON object: %r" % (script, out))
        self.assertIsInstance(obj, dict, script)
        self.assertEqual((obj.get("hookSpecificOutput") or {}).get("hookEventName"),
                         event, "%s: %r" % (script, obj))
        return obj

    def _cli_steps(self, copy, f):
        out = {}
        out["help"] = self._cli(copy, f, "--help")
        out["index"] = self._cli(copy, f, "index", "--target", f)
        out["map"] = self._cli(copy, f, "map", "--target", f)
        out["find"] = self._cli(copy, f, "find", "kestrel", "feed", "--target", f)
        out["file"] = self._cli(copy, f, "file", "src/feed.ts", "--target", f)
        out["since"] = self._cli(copy, f, "since", "HEAD~1", "--target", f)
        out["note"] = self._cli(copy, f, "note", "src/feed.ts", "--target", f)
        return out

    def test_C6_help_names_the_commands(self):
        copy, f = self._setup()
        out = self._cli(copy, f, "--help")
        for name in ("file", "since", "find", "index", "map", "note"):
            self.assertRegex(out, r"\b%s\b" % name, "help lacks %s" % name)

    def test_C6_index_writes_the_fixture_index(self):
        copy, f = self._setup()
        self._cli(copy, f, "index", "--target", f)
        self.assertTrue(os.path.isfile(os.path.join(f, ".jokbo", "index.json")))

    def test_C6_map_names_documents_and_the_copy_engine(self):
        copy, f = self._setup()
        out = self._cli(copy, f, "map", "--target", f)
        self.assertIn("plan/PLAN.md", out)
        self.assertIn("README.md", out)
        self.assertIn('"' + copy + '/jokbo.py"', out)

    def test_C6_find_file_since_note(self):
        copy, f = self._setup()
        out = self._cli_steps(copy, f)
        self.assertIn("The kestrel feed lists every post newest first", out["find"])
        self.assertIn("plan/PLAN.md#Feed", out["file"])
        self.assertIn(SECOND_SUBJECT, out["since"])

    def test_C6_session_start_hook_points_at_the_plan(self):
        copy, f = self._setup()
        self._cli_steps(copy, f)
        obj = self._hook(copy, f, *HOOKS[0])
        self.assertIsNotNone(obj, "session_start.py printed nothing")
        ctx = (obj.get("hookSpecificOutput") or {}).get("additionalContext")
        self.assertIsInstance(ctx, str)
        self.assertIn("plan/PLAN.md", ctx)

    def test_C6_other_hooks_exit_zero_with_their_event(self):
        copy, f = self._setup()
        self._cli_steps(copy, f)
        self._hook(copy, f, *HOOKS[0])
        for script, event, extra in HOOKS[1:]:
            self._hook(copy, f, script, event, extra)

    def test_C6_copy_is_unchanged_after_the_whole_workflow(self):
        copy, f = self._setup()
        before = tree_hashes(copy)
        v = shared_version(self)
        code = ("import sys; sys.path.insert(0, %r); import jokbo; "
                "print(jokbo.version())") % copy
        r = py(["-c", code], f)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, v), r.stderr)
        self._cli_steps(copy, f)
        for script, event, extra in HOOKS:
            self._hook(copy, f, script, event, extra)
        self.assertEqual(tree_hashes(copy), before, "the workflow wrote into COPY")



# --------------------------------------------------------------- C7

C7_README = "# Site\n\nThe kestrel page.\n"
C7_MARK = "kestrel probe-ran-from "
C7_PROBE = ("import os\n"
            "print(%r + os.path.realpath(os.path.dirname("
            "os.path.abspath(__file__))))\n" % C7_MARK)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def write_runtime(d, manifest=".claude-plugin"):
    """A probe runtime (or a decoy) in directory d."""
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "probe.py"), "w", encoding="utf-8") as fh:
        fh.write(C7_PROBE)
    _write_json(os.path.join(d, manifest, "plugin.json"),
                {"name": "probe", "version": "1.0.0"})


def write_claude_catalog(r, source):
    _write_json(os.path.join(r, ".claude-plugin", "marketplace.json"),
                {"name": "probe-market",
                 "plugins": [{"name": "probe", "source": source}]})


def write_codex_catalog(r, source):
    _write_json(os.path.join(r, ".agents", "plugins", "marketplace.json"),
                {"name": "probe-market",
                 "plugins": [{"name": "probe", "source": source}]})


class C7LinkedCatalogs(unittest.TestCase):

    def _setup(self, link=None):
        """Returns (COPY, T, P, R).  P/hunsu.local.json only if link is given."""
        copy, err = make_copy(self)
        self.assertIsNone(err, err)
        self.assertTrue(os.path.isfile(os.path.join(copy, "jokbo.py")),
                        "the copy has no jokbo.py")
        t = fresh_dir(self)
        self.assertFalse(in_work_tree(t), "%s is inside a git work tree" % t)
        p = os.path.join(t, "project")
        os.makedirs(p)
        with open(os.path.join(p, "README.md"), "w", encoding="utf-8") as fh:
            fh.write(C7_README)
        for argv in (["git", "init", "-q"], ["git", "add", "README.md"],
                     ["git"] + GIT_ID + ["commit", "-q", "-m", "site"]):
            r = run(argv, p)
            self.assertEqual(r.returncode, 0, "%s: %s" % (argv, r.stderr))
        _write_json(os.path.join(p, "hunsu.json"), {
            "plugins": {"probe": {"version": "1.0.0", "source": "local",
                                  "marketplace": "probe-market"}},
            "reads": {"probe": {"decision": ["python3",
                                             "{plugin:probe}/probe.py"]}},
        })
        r_dir = os.path.join(t, "probe-repo")
        os.makedirs(os.path.join(t, "claude-home"))
        return copy, t, p, r_dir

    def _link(self, p, link):
        _write_json(os.path.join(p, "hunsu.local.json"), {"links": {"probe": link}})

    def _find(self, copy, t, p):
        env = child_env()
        env.pop("CLAUDE_CONFIG_DIR", None)
        env["HUNSU_CLAUDE_DIR"] = os.path.join(t, "claude-home")
        r = subprocess.run(
            [sys.executable, os.path.join(copy, "jokbo.py"), "find", "kestrel",
             "--target", p],
            cwd=p, capture_output=True, text=True, env=env, timeout=TIMEOUT)
        self.assertNotIn(POISON_MSG, r.stdout + r.stderr)
        return r

    def _ran_from(self, r, d):
        self.assertIn(C7_MARK + os.path.realpath(d), r.stdout,
                      "the probe did not run from %s\nstdout: %s\nstderr: %s"
                      % (d, r.stdout, r.stderr))

    def _never_ran_from(self, r, d):
        self.assertNotIn(C7_MARK + os.path.realpath(d), r.stdout,
                         "the probe ran from %s\nstdout: %s" % (d, r.stdout))

    def test_C7_1_claude_catalog_of_linked_repo_selects_plugin_dir(self):
        copy, t, p, r_dir = self._setup()
        write_runtime(os.path.join(r_dir, "plugin"))
        write_claude_catalog(r_dir, "./plugin")
        self._link(p, r_dir)
        self._ran_from(self._find(copy, t, p), os.path.join(r_dir, "plugin"))

    def test_C7_2_codex_catalog_of_linked_repo_selects_plugin_dir(self):
        copy, t, p, r_dir = self._setup()
        write_runtime(os.path.join(r_dir, "plugin"), ".codex-plugin")
        write_codex_catalog(r_dir, {"source": "local", "path": "./plugin"})
        self.assertFalse(os.path.exists(
            os.path.join(r_dir, ".claude-plugin", "marketplace.json")))
        self._link(p, r_dir)
        self._ran_from(self._find(copy, t, p), os.path.join(r_dir, "plugin"))

    def test_C7_3_directory_marketplace_follows_its_catalog(self):
        copy, t, p, r_dir = self._setup()
        write_runtime(os.path.join(r_dir, "plugin"))
        write_claude_catalog(r_dir, "./plugin")
        _write_json(os.path.join(t, "claude-home", "plugins",
                                 "known_marketplaces.json"),
                    {"probe-market": {"source": {"source": "directory",
                                                 "path": r_dir}}})
        self.assertFalse(os.path.exists(os.path.join(p, "hunsu.local.json")))
        self._ran_from(self._find(copy, t, p), os.path.join(r_dir, "plugin"))

    def test_C7_4_catalog_precedes_legacy_root_name(self):
        copy, t, p, r_dir = self._setup()
        write_runtime(os.path.join(r_dir, "plugin"))
        write_runtime(os.path.join(r_dir, "probe"))
        write_claude_catalog(r_dir, "./plugin")
        self._link(p, r_dir)
        r = self._find(copy, t, p)
        self._ran_from(r, os.path.join(r_dir, "plugin"))
        self._never_ran_from(r, os.path.join(r_dir, "probe"))

    def test_C7_5_explicit_payload_link_still_works(self):
        copy, t, p, r_dir = self._setup()
        write_runtime(os.path.join(r_dir, "plugin"))
        self._link(p, os.path.join(r_dir, "plugin"))
        self._ran_from(self._find(copy, t, p), os.path.join(r_dir, "plugin"))

    def test_C7_6_legacy_root_name_still_works(self):
        copy, t, p, _ = self._setup()
        m = os.path.join(t, "legacy-market")
        write_runtime(os.path.join(m, "probe"))
        self._link(p, m)
        self._ran_from(self._find(copy, t, p), os.path.join(m, "probe"))

    def test_C7_7_unsafe_catalog_sources_are_never_followed(self):
        cases = [
            ("claude", "../outside"),
            ("claude", "ABS_OUTSIDE"),
            ("claude", "https://example.invalid/probe"),
            ("claude", "./escape"),
            ("claude", "./missing"),
            ("codex", {"source": "github", "repo": "example/probe"}),
        ]
        for kind, source in cases:
            with self.subTest(kind=kind, source=source):
                copy, t, p, r_dir = self._setup()
                o = os.path.join(t, "outside")
                write_runtime(o)
                write_runtime(os.path.join(r_dir, "probe"))
                if source == "ABS_OUTSIDE":
                    source = o
                if source == "./escape":
                    os.symlink(o, os.path.join(r_dir, "escape"))
                if kind == "claude":
                    write_claude_catalog(r_dir, source)
                else:
                    write_codex_catalog(r_dir, source)
                self.assertFalse(os.path.exists(os.path.join(r_dir, "missing")))
                self._link(p, r_dir)
                r = self._find(copy, t, p)
                self._never_ran_from(r, o)
                self._ran_from(r, os.path.join(r_dir, "probe"))

    def test_C7_8_installed_selection_is_unaffected(self):
        copy, t, p, _ = self._setup()
        inst = os.path.join(t, "installed", "probe")
        write_runtime(inst)
        plugins = os.path.join(t, "claude-home", "plugins")
        _write_json(os.path.join(plugins, "known_marketplaces.json"),
                    {"probe-market": {"source": {"source": "github",
                                                 "repo": "example/probe"}}})
        _write_json(os.path.join(plugins, "installed_plugins.json"),
                    {"version": 2, "plugins": {"probe@probe-market": [
                        {"scope": "user", "installPath": inst}]}})
        self.assertFalse(os.path.exists(os.path.join(p, "hunsu.local.json")))
        self._ran_from(self._find(copy, t, p), inst)



class C8FolderOfCheckouts(unittest.TestCase):
    """{plugin:NAME} for a link or directory marketplace naming a folder of
    checkouts M: M's catalog first, then the child M/NAME's own catalog
    (one level, contained), then the flat M/NAME and M fallbacks."""

    # C7's helpers, reached through its class without editing it.
    _setup = C7LinkedCatalogs._setup
    _link = C7LinkedCatalogs._link
    _find = C7LinkedCatalogs._find
    _ran_from = C7LinkedCatalogs._ran_from
    _never_ran_from = C7LinkedCatalogs._never_ran_from

    def _market(self):
        copy, t, p, _ = self._setup()
        m = os.path.join(t, "market")
        os.makedirs(m)
        return copy, t, p, m

    def _no_market_catalog(self, m):
        self.assertFalse(os.path.exists(
            os.path.join(m, ".claude-plugin", "marketplace.json")))
        self.assertFalse(os.path.exists(
            os.path.join(m, ".agents", "plugins", "marketplace.json")))

    def test_C8_1_link_to_folder_follows_child_claude_catalog(self):
        copy, t, p, m = self._market()
        child = os.path.join(m, "probe")
        write_runtime(os.path.join(child, "plugin"))
        write_claude_catalog(child, "./plugin")
        self.assertFalse(os.path.exists(os.path.join(child, "probe.py")))
        self._no_market_catalog(m)
        self._link(p, m)
        self._ran_from(self._find(copy, t, p), os.path.join(child, "plugin"))

    def test_C8_2_link_to_folder_follows_child_codex_catalog(self):
        copy, t, p, m = self._market()
        child = os.path.join(m, "probe")
        write_runtime(os.path.join(child, "plugin"), ".codex-plugin")
        write_codex_catalog(child, {"source": "local", "path": "./plugin"})
        self.assertFalse(os.path.exists(
            os.path.join(child, ".claude-plugin", "marketplace.json")))
        self.assertFalse(os.path.exists(os.path.join(child, "probe.py")))
        self._no_market_catalog(m)
        self._link(p, m)
        self._ran_from(self._find(copy, t, p), os.path.join(child, "plugin"))

    def test_C8_3_directory_marketplace_folder_follows_child_catalog(self):
        copy, t, p, m = self._market()
        child = os.path.join(m, "probe")
        write_runtime(os.path.join(child, "plugin"))
        write_claude_catalog(child, "./plugin")
        self._no_market_catalog(m)
        _write_json(os.path.join(t, "claude-home", "plugins",
                                 "known_marketplaces.json"),
                    {"probe-market": {"source": {"source": "directory",
                                                 "path": m}}})
        self.assertFalse(os.path.exists(os.path.join(p, "hunsu.local.json")))
        self._ran_from(self._find(copy, t, p), os.path.join(child, "plugin"))

    def test_C8_4_folder_catalog_precedes_child_catalog(self):
        copy, t, p, m = self._market()
        write_runtime(os.path.join(m, "probe-direct"))
        write_claude_catalog(m, "./probe-direct")
        child = os.path.join(m, "probe")
        write_runtime(os.path.join(child, "plugin"))
        write_claude_catalog(child, "./plugin")
        self._link(p, m)
        r = self._find(copy, t, p)
        self._ran_from(r, os.path.join(m, "probe-direct"))
        self._never_ran_from(r, os.path.join(child, "plugin"))

    def test_C8_5_flat_legacy_child_unchanged(self):
        copy, t, p, m = self._market()
        child = os.path.join(m, "probe")
        write_runtime(child)
        self._no_market_catalog(m)
        self._link(p, m)
        self._ran_from(self._find(copy, t, p), child)

    def test_C8_6_direct_payload_link_unchanged(self):
        copy, t, p, m = self._market()
        child = os.path.join(m, "probe")
        write_runtime(os.path.join(child, "plugin"))
        write_claude_catalog(child, "./plugin")
        self._link(p, os.path.join(child, "plugin"))
        self._ran_from(self._find(copy, t, p), os.path.join(child, "plugin"))

    def test_C8_7_child_lookup_is_one_level_and_contained(self):
        for case in ("nested", "sibling", "parent-escape", "symlink-escape",
                     "scheme"):
            with self.subTest(case=case):
                copy, t, p, m = self._market()
                child = os.path.join(m, "probe")
                write_runtime(child)  # the flat fallback, visible
                never = None
                if case == "nested":
                    inner = os.path.join(child, "inner")
                    write_runtime(os.path.join(inner, "plugin"))
                    write_claude_catalog(inner, "./plugin")
                    never = os.path.join(inner, "plugin")
                elif case == "sibling":
                    other = os.path.join(m, "other")
                    write_runtime(os.path.join(other, "plugin"))
                    write_claude_catalog(other, "./plugin")
                    never = os.path.join(other, "plugin")
                elif case == "parent-escape":
                    never = os.path.join(m, "outside-child")
                    write_runtime(never)
                    write_claude_catalog(child, "../outside-child")
                elif case == "symlink-escape":
                    never = os.path.join(t, "outside")
                    write_runtime(never)
                    os.symlink(never, os.path.join(child, "escape"))
                    write_claude_catalog(child, "./escape")
                else:
                    write_claude_catalog(child,
                                         "https://example.invalid/probe")
                self._no_market_catalog(m)
                self._link(p, m)
                r = self._find(copy, t, p)
                if never is not None:
                    self._never_ran_from(r, never)
                self._ran_from(r, child)

    def test_C8_8_child_symlinked_outside_folder_is_not_followed(self):
        copy, t, p, m = self._market()
        q = os.path.join(t, "elsewhere")
        write_runtime(os.path.join(q, "plugin"))
        write_claude_catalog(q, "./plugin")
        self.assertFalse(os.path.exists(os.path.join(q, "probe.py")))
        os.symlink(q, os.path.join(m, "probe"))
        self._no_market_catalog(m)
        self._link(p, m)
        r = self._find(copy, t, p)
        self._never_ran_from(r, os.path.join(q, "plugin"))
        self.assertEqual(r.returncode, 0,
                         "jokbo exited %d\nstderr: %s" % (r.returncode, r.stderr))


if __name__ == "__main__":
    unittest.main()
