"""jokbo — the reader of the LLM-development era: when a question arises, where to read, and how current each source is.

  file PATH [--target DIR]        before editing a file: the document sections that speak of it (ranked by shared words),
                                  the commits that last changed it, and what the declared reads say of it
  since [REV] [--target DIR]      what changed since REV (default: since this machine last looked): commits, document
                                  sections changed, uncommitted work, and what the declared reads say since then
  find WORDS... [--target DIR]    where the project speaks of something: document sections, the code's own words
                                  (docstrings, leading comments), commit subjects, and the declared `decision` reads
  index [--target DIR]            build the cache now and say what it holds (every other command builds it when stale)
  map [--target DIR]              what the session-start hook says: what this project holds, and how to ask
  note PATH [--target DIR]        what the read hook says at the first look at a file: a line per fact that bears on it

The base layer is what every project has: its documents (Markdown, by heading), its code (as the targets things point at,
and its docstrings and leading comments) and its git history. Products' records are an overlay, read only through what the
lock declares (`reads` in hunsu.lock.json, or hunsu.json: {plugin: {kind: argv}}, kinds `file`, `since`, `decision`, `note`,
`map`, `hands`, with `{plugin:NAME}`, `{target}`, `{path}`, `{since}`, `{paths}`, `{after}`) — jokbo knows no product. Ranking is
by shared words (BM25), the same answer every time, confirmed by nobody: it says so; a live document before a retired one,
and a retired one's quote says so. Freshness is git's: a section written before the file last changed is marked. jokbo never decides and never becomes a source: `.jokbo/` is a cache (ignored, rebuilt), never committed.
"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import functools
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = ".jokbo"
INDEX_FORMAT = 8     # 7 clipped a section at its last committed line — its `changed` is rebuilt, with blame
MIGRATES = (5, 6)  # an index of these formats is brought up to date entry by entry, without blame (5 lacked `level`, `keys`;
                   # 6 a document's `size` and `retired`)
KEYED = 2          # how a section's match keys are cut (stem, section_keys): a change re-keys the cache, without blame
PY = "python3" if shutil.which("python3") else "python"
DOC_EXT = (".md", ".markdown")
CODE_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".astro", ".vue", ".svelte", ".css", ".scss", ".html", ".sql",
            ".sh", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".h", ".cpp", ".swift", ".toml", ".yaml", ".yml", ".graphql",
            ".prisma", ".proto")
STOP = {"src", "lib", "index", "the", "and", "for", "with", "this", "that", "from", "return", "const", "function", "import",
        "export", "default", "true", "false", "null", "none", "self", "def", "class", "let", "var", "new", "async", "await",
        "test", "tests", "not", "are", "was", "but", "else", "elif", "then", "string", "number", "type", "json", "html", "css",
        "assets", "public", "components", "pages", "scripts", "mjs", "md"}
SHOW = 3             # sections `file` names
BRIEF_LINES, BRIEF_WIDTH, BRIEF_CHARS = 10, 200, 1200   # a declared read, told in brief


def version():
    try:
        return json.load(io.open(os.path.join(HERE, "plugin.json"), encoding="utf-8")).get("version", "?")
    except (OSError, ValueError):
        return "?"


def load(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if default is None else default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())   # hooks of parallel tool calls write at once: each its own temp, then one rename
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def git(target, *a):
    try:
        done = subprocess.run(["git", "-c", "core.quotepath=false", *a], cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def project_root(path):
    """The project a path belongs to: its git work tree's top, whatever directory the shell has moved to (a hook's cwd is
    the shell's — `cd tests` once made jokbo keep a second cache in tests/ and read `test_bots.py` as a file of its own)."""
    top = git(path, "rev-parse", "--show-toplevel") if os.path.isdir(path) else None
    return os.path.realpath(top.strip()) if top and top.strip() else os.path.realpath(path)


def cache_dir(target):
    d = os.path.join(target, CACHE)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    gi = os.path.join(d, ".gitignore")
    if not os.path.exists(gi):
        with io.open(gi, "w", encoding="utf-8") as fh:
            fh.write("*\n")   # the directory ignores itself: a cache of this machine, rebuilt from the record, never committed
    return d


# ---------------------------------------------------------------- words

def words(text):
    """Tokens for ranking: identifiers split at case and underscores, lowercased, three letters or more; runs of Hangul or
    CJK of two or more. Data and field names are the vocabulary documents and code share (measured on guin-site: literal
    file names linked 19% of related pairs, shared words ranked a right section into the top three for 83% of files)."""
    out = []
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[ㄱ-ㆎ가-힣]{2,}|[一-鿿]{2,}", text):
        for p in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])|[ㄱ-ㆎ가-힣一-鿿]{2,}", w):
            p = p.lower()
            if (len(p) >= 3 or not p.isascii()) and p not in STOP:
                out.append(p)
    return out


class Ranker:
    """BM25 over a list of token lists."""
    def __init__(self, docs, k1=1.2, b=0.75):
        self.docs = [collections.Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.n = len(docs)
        self.avg = (sum(self.lens) / self.n) if self.n else 1
        self.df = collections.Counter(t for d in self.docs for t in d)
        self.k1, self.b = k1, b

    def score(self, query, i):
        tf, L, s = self.docs[i], self.lens[i], 0.0
        for t in set(query):
            if t in tf:
                idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * L / (self.avg or 1)))
        return s


# ---------------------------------------------------------------- what the lock declares (the overlay)

def manifest_and_lock(target):
    return load(os.path.join(target, "hunsu.json")), load(os.path.join(target, "hunsu.lock.json"))


def settings(target):
    """hunsu.json `settings.jokbo`, with this machine's hunsu.local.json `settings.jokbo` on top — read where it is written.
    `skip`: path prefixes that are not documents of the project (a site's posts, vendored docs)."""
    manifest = load(os.path.join(target, "hunsu.json"))
    local = load(os.path.join(target, "hunsu.local.json"))
    out = dict((manifest.get("settings") or {}).get("jokbo") or {})
    out.update((local.get("settings") or {}).get("jokbo") or {})
    return out


def record_paths(target):
    """Paths that are records, not documents or code: every plugin's `records` as the lock carries them (`record-paths`),
    and this cache. jokbo names no product's directory; without a lock, nothing else is set aside."""
    _, lock = manifest_and_lock(target)
    declared = lock.get("record-paths")
    paths = {p for ps in declared.values() for p in ps} if isinstance(declared, dict) else set()
    return tuple(sorted(paths | {CACHE + "/"}))


def declared_reads(target):
    """{plugin: {kind: argv}} — the lock's `reads`, with hunsu.json's on top (a project's own declaration, read where it is
    written, as every product reads settings)."""
    manifest, lock = manifest_and_lock(target)
    out = {}
    for src in (lock.get("reads"), manifest.get("reads")):
        if isinstance(src, dict):
            for plugin, kinds in src.items():
                if isinstance(kinds, dict):
                    out.setdefault(plugin, {}).update({k: v for k, v in kinds.items() if isinstance(v, list) and v})
    return out


def install_entry(entries, target):
    want = os.path.realpath(target)
    for e in entries:
        if e.get("scope") == "project" and e.get("projectPath") and os.path.realpath(e["projectPath"]) == want:
            return e
    for e in entries:
        if e.get("scope") == "user":
            return e
    return entries[0] if entries else {}


def plugin_root(target, name):
    """`{plugin:NAME}` -> that plugin's root on this machine: a link in hunsu.local.json, else the host's install record for
    the marketplace the manifest declares, else any install of that name. None when it is not here."""
    local = load(os.path.join(target, "hunsu.local.json"))
    links = local.get("links", {})
    if name in links:
        root = links[name]
        return os.path.join(root, name) if os.path.isdir(os.path.join(root, name)) else root
    home = os.environ.get("HUNSU_CLAUDE_DIR") or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    installed = load(os.path.join(home, "plugins", "installed_plugins.json")).get("plugins", {})
    market = local.get("dev", {}).get(name) or (load(os.path.join(target, "hunsu.json")).get("plugins", {}).get(name) or {}).get("marketplace")
    if market:
        src = (load(os.path.join(home, "plugins", "known_marketplaces.json")).get(market) or {}).get("source") or {}
        if src.get("source") == "directory" and os.path.isdir(os.path.join(src.get("path", ""), name)):
            return os.path.join(src["path"], name)
        entries = installed.get("%s@%s" % (name, market)) or []
        if entries:
            return install_entry(entries, target).get("installPath")
    for key, entries in installed.items():
        if key.split("@")[0] == name and entries:
            return install_entry(entries, target).get("installPath")
    return None


def run_read(target, plugin, argv, values, timeout=20):
    """One declared read: (text, None) or (None, why not). Placeholders it names must have values; a missing plugin or a
    failing command is said, never guessed around."""
    out = []
    for a in argv:
        if a == "{paths}":   # a list: one argument per path
            if values.get("paths") is None:
                return None, "no {paths} to give it"
            out.extend(values["paths"])
            continue
        for m in set(re.findall(r"\{plugin:([\w.-]+)\}", a)):
            root = plugin_root(target, m)
            if not root:
                return None, "plugin %s is not installed or linked here" % m
            a = a.replace("{plugin:%s}" % m, root.replace(os.sep, "/"))
        for key in ("target", "path", "since", "after"):
            if "{%s}" % key in a:
                if values.get(key) is None:
                    return None, "no {%s} to give it" % key
                a = a.replace("{%s}" % key, values[key])
        out.append(a)
    if out and out[0] in ("python3", "python"):
        out[0] = PY
    try:
        done = subprocess.run(out, cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, "%s did not answer (%s)" % (plugin, type(err).__name__)
    text = (done.stdout or "").rstrip()
    if not text and done.returncode not in (0, 1):
        return None, "%s exited %d: %s" % (plugin, done.returncode, (done.stderr or "").strip()[-200:])
    return text, None


def overlay(target, kind, values, timeout=20):
    """[(plugin, text | None, why | None)] for every plugin that declares this kind of read."""
    return [(p, *run_read(target, p, kinds[kind], dict(values, target=target), timeout)) for p, kinds in sorted(declared_reads(target).items()) if kind in kinds]


HANDS_TIMEOUT = 5   # the change line waits this long, at most, for a product to say whose a change is


def hands(target, paths, after=None):
    """{path: whose} — what the declared `hands` reads say of who changed these paths: a JSON object from path to a phrase
    ("the implementer of cache-build (run-20261015-054352-a0ec)"), for the paths the product's records name; `{after}` is
    when the session's last call left the tree (ISO, UTC), `{paths}` one argument per path. The rest, and
    any read that fails or says nothing it can parse, stay another hand's. guin-site 10-15 05:53:57: "files:
    src/worker/index.ts, src/worker/read.ts" was the session's own hired builder (task cache-build), told as anyone's."""
    out, want = {}, set(paths)
    if not want:
        return out
    for plugin, text, why in overlay(target, "hands", {"paths": sorted(want), "after": after}, HANDS_TIMEOUT):
        try:
            said = json.loads(text) if text and text.strip() else {}
        except ValueError:
            continue
        for p, who in (said.items() if isinstance(said, dict) else ()):
            if p in want and isinstance(who, str) and who.strip() and p not in out:
                out[p] = re.sub(r"\s+", " ", who.strip())[:160]
    return out


# ---------------------------------------------------------------- the base layer: documents, code, git

def listed_files(target):
    """Tracked and untracked-but-not-ignored files, relative to target, records set aside."""
    out = git(target, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".")
    if out is None:
        return None
    rp = record_paths(target) + tuple(p for p in settings(target).get("skip") or [] if isinstance(p, str) and p)
    return sorted({l for l in out.split("\0") if l and not l.startswith(rp) and os.path.isfile(os.path.join(target, l))})


def read_text(target, path, limit=400000):
    try:
        with io.open(os.path.join(target, path), encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def sections_of(path, text):
    """A Markdown document cut at every heading (ATX, outside fenced code): [{id, file, heading, start, end, text}], lines
    1-based and inclusive. Text before the first heading is the document's own section."""
    lines = text.split("\n")
    out, cur, fence = [], None, None
    for i, line in enumerate(lines, 1):
        m = re.match(r"\s*(```|~~~)", line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
        h = None if fence or m else re.match(r"(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if h:
            if cur:
                cur["end"] = i - 1
                out.append(cur)
            cur = {"file": path, "heading": h.group(2).strip(), "level": len(h.group(1)), "start": i, "lines": [line]}
        else:
            if cur is None:
                cur = {"file": path, "heading": "", "level": 0, "start": 1, "lines": []}
            cur["lines"].append(line)
    if cur:
        cur["end"] = len(lines)
        out.append(cur)
    for s in out:
        s["text"] = "\n".join(s.pop("lines"))
        s["id"] = "%s#%s" % (path, s["heading"]) if s["heading"] else path
    return [s for s in out if s["text"].strip() and (s["heading"] or len(s["text"].strip()) > 40)]


def blame_times(target, path):
    """{line: unix time of the commit that last changed it}; uncommitted lines are absent."""
    out = git(target, "blame", "--line-porcelain", "--", path)
    if out is None:
        return {}
    times, line_no, sha_time = {}, None, {}
    cur = None
    for l in out.splitlines():
        m = re.match(r"([0-9a-f]{40}) \d+ (\d+)", l)
        if m:
            cur, line_no = m.group(1), int(m.group(2))
            continue
        if l.startswith("committer-time "):
            sha_time[cur] = int(l.split()[1])
        elif l.startswith("\t") and cur:
            if not cur.startswith("0000000"):
                times[line_no] = sha_time.get(cur)
    return times


def code_words(path, text):
    """What the code says about itself: docstrings and leading comment blocks — [{file, line, text}]. The statements a
    refactoring is most likely to lose, and the only ones the code makes in words."""
    out = []
    if path.endswith(".py"):
        for m in re.finditer(r'(?:^|\n)([ \t]*)(?:def|class)\s+\w+[^\n]*:\s*\n\s*("""|\'\'\')(.*?)\2', text, re.S):
            out.append((text[:m.start(3)].count("\n") + 1, m.group(3)))
        m = re.match(r'\s*(?:#![^\n]*\n)?\s*("""|\'\'\')(.*?)\1', text, re.S)
        if m:
            out.append((1, m.group(2)))
    for m in re.finditer(r"/\*\*(.*?)\*/", text, re.S):
        out.append((text[:m.start()].count("\n") + 1, re.sub(r"^\s*\* ?", "", m.group(1), flags=re.M)))
    head = re.match(r"((?:[ \t]*(?://|#(?!!)|--)[^\n]*\n){2,})", text)
    if head:
        out.append((1, re.sub(r"^[ \t]*(?://|#|--) ?", "", head.group(1), flags=re.M)))
    return [{"file": path, "line": ln, "text": t.strip()} for ln, t in out if len(t.strip()) > 20]


def recent_commits(target, n=500):
    out = git(target, "log", "-n", str(n), "--name-only", "--format=\x1e%H\x1f%ct\x1f%an\x1f%s", "--", ".")
    if out is None:
        return []
    commits = []
    for block in out.split("\x1e")[1:]:
        head, _, files = block.partition("\n")
        sha, ct, author, subject = (head.split("\x1f") + ["", "", "", ""])[:4]
        commits.append({"sha": sha, "at": int(ct or 0), "author": author, "subject": subject,
                        "files": [f.strip() for f in files.splitlines() if f.strip()]})
    return commits


def tree_key(target):
    head = (git(target, "rev-parse", "HEAD") or "").strip()
    status = git(target, "status", "--porcelain", "--untracked-files=all", "--", ".") or ""
    rp = record_paths(target)
    status = "\n".join(l for l in status.splitlines() if not l[3:].strip().startswith(rp))
    # Porcelain records state, not content: a second edit remains " M" (or "??"). Hash every indexed file rather than
    # trusting mtimes, which editors can preserve; the complete name set too — it decides unique-basename references.
    files = listed_files(target)
    contents = [(p, content_key(target, p, None, False)) for p in files or []
                if p.lower().endswith(DOC_EXT) or p.endswith(CODE_EXT)]
    state = [version(), head, status, INDEX_FORMAT, files, contents, settings(target), rp]
    return hashlib.sha1(json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8", "surrogateescape")).hexdigest()


def dirty_paths(target):
    out = git(target, "status", "--porcelain", "-z", "--untracked-files=all", "--", ".") or ""
    paths, parts, i = set(), out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) >= 4:
            paths.add(entry[3:])
            if entry[0] in "RC" or entry[1] in "RC":
                if i < len(parts) and parts[i]:
                    paths.add(parts[i])
                i += 1
    return paths


def content_key(target, path, last_commit, dirty):
    """What a file's entry depends on: its words (content) and, for blame, the last commit that changed it — HEAD moving
    past it changes neither, so a commit elsewhere rebuilds nothing here."""
    try:
        with io.open(os.path.join(target, path), "rb") as fh:
            h = hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        h = "gone"
    return "%s %s %s" % (h, last_commit or "-", "dirty" if dirty else "clean")


def blame_documents(target, paths):
    """Only the changed documents' blame, at most four git processes; input order kept, a git failure is blame_times'
    empty map, an unexpected exception propagates instead of becoming a successful read."""
    if len(paths) < 2:
        return {path: blame_times(target, path) for path in paths}
    with ThreadPoolExecutor(max_workers=min(4, len(paths))) as pool:
        return dict(zip(paths, pool.map(lambda path: blame_times(target, path), paths)))


def build_index(target, previous=None):
    """The cache: sections of every document (with each one's last change, by blame), the code's own words, recent
    commits. A file whose key is unchanged keeps its previous entry — blame runs only for documents that changed, so an
    edit to one file costs one file's work, not the project's."""
    # a concurrent edit must not make an older entry look current: a key taken after scanning could label pre-edit
    # entries with the post-edit fingerprint
    snapshot_key = tree_key(target)
    files = listed_files(target)
    if files is None:
        raise SystemExit("jokbo: %s is not a git work tree — the base layer is its documents, code and history" % target)
    commits = recent_commits(target)
    last = {}
    for c in commits:
        for f in c["files"]:
            last.setdefault(f, c["sha"])
    dirty = dirty_paths(target)
    old = (previous or {}).get("entries") or {}
    fileset = sorted(files)
    names_key = hashlib.sha1("\n".join(fileset).encode("utf-8", "surrogateescape")).hexdigest()
    base = collections.Counter(os.path.basename(f) for f in files)
    # compiled once per unique basename: re.search per section/file pair thrashes re's cache in a larger repository
    name_patterns = {f: re.compile(r"(?<![\w/.-])" + re.escape(os.path.basename(f)) + r"(?![\w-])")
                     for f in files if base[os.path.basename(f)] == 1 and len(os.path.basename(f)) > 4}
    keys = {f: content_key(target, f, last.get(f), f in dirty) + (" " + names_key if f.lower().endswith(DOC_EXT) else "")
            for f in files if f.lower().endswith(DOC_EXT) or f.endswith(CODE_EXT)}
    blames = blame_documents(target, [f for f in keys if f.lower().endswith(DOC_EXT)
                                      and old.get(f, {}).get("key") != keys[f] and (f not in dirty or last.get(f))])
    entries = {}
    for f in files:
        is_doc, is_code = f.lower().endswith(DOC_EXT), f.endswith(CODE_EXT)
        if not (is_doc or is_code):
            continue
        key = keys[f]
        if old.get(f, {}).get("key") == key:
            entries[f] = upgrade(target, f, old[f])
            continue
        text = read_text(target, f)
        if is_doc:
            times = blames.get(f, {})
            # the file's own lines, not the empty one after its last newline: blame has nothing there, so the last section
            # of any edited document read as uncommitted
            held = len(text.split("\n")) - text.endswith("\n")
            secs = []
            for sec in sections_of(f, text):
                # trailing lines without committed blame count: clipping at the last committed line hid new text
                # appended to an existing section
                lt = [times.get(i) for i in range(sec["start"], min(sec["end"], held) + 1)]
                sec["changed"] = None if f in dirty and any(t is None for t in lt) else max([t for t in lt if t] or [0]) or None
                sec["words"] = words(sec["heading"] + " " + sec["text"])
                sec["keys"] = section_keys(sec["words"], sec["text"])
                # the files it names outright — by path, or by a file name only one file has
                sec["names"] = sorted(g for g in files if g != f and (g in sec["text"] or
                                      (g in name_patterns and name_patterns[g].search(sec["text"]))))
                del sec["text"]   # read from the file when shown: the cache keeps positions, the document keeps its words
                secs.append(sec)
            entries[f] = dict({"key": key, "sections": secs, "keyed": KEYED}, **doc_facts(text, times, f in dirty))
        else:
            said = code_words(f, text)
            for w in said:
                w["words"] = words(w["text"])
            entries[f] = {"key": key, "said": said}
    return {"format": INDEX_FORMAT, "keyed": KEYED, "key": snapshot_key, "built": int(time.time()), "by": "jokbo %s" % version(),
            "files": files, "entries": entries, "commits": commits}


MARKER = r"(?:retired|deprecated|superseded|obsolete|은퇴|폐기)"
RETIRED = re.compile(r"(?<![\w-])%s(?![\w-])" % MARKER, re.I)
RETIRED_LEAD = re.compile(r"\s*(?:>\s*)?(?:[-*]\s+)?(?:\*\*|__)?\s*(?:status\s*:\s*)?%s(?:\*\*|__)?\s*(?:[:(.—–-]|by\b|$)" % MARKER, re.I)
RETIRED_WITHIN = 20   # a document says it is retired at its top: in its title, or a line under it that starts with the word


def retired_at(text):
    """The line where a document says, at its top, that it no longer holds (guin-site's BACKEND.md: "**은퇴 (2026-10-12 …):**
    이 문서의 구조는 더 이상 사실이 아니다" at line 16): its title holding a marker (retired, deprecated, superseded, obsolete,
    은퇴, 폐기), or a line of its first ones, before its first section, starting with one. None for a document that speaks of
    something retired further on, or in a section of its own ("## Deprecated flags")."""
    heads = 0
    for i, l in enumerate(text.split("\n")[:RETIRED_WITHIN], 1):
        h = re.match(r"\s*#{1,6}\s+(.*)", l)
        if h:
            heads += 1
            if heads > 1:
                return None
            if RETIRED.search(h.group(1)):
                return i
        elif RETIRED_LEAD.match(l):
            return i
    return None


def doc_facts(text, times, dirty):
    """What the map orders documents by (guin-site 10-13: README.md, rewritten on 10-12, lost its place to the retired
    BACKEND.md of the same commit, which had more sections): how much the last change wrote — the lines blame gives to the
    document's newest commit, or its uncommitted lines while it is being edited — and whether it says it is retired."""
    n = len(text.split("\n"))
    if dirty and len(times) < n:
        size = n - len(times)
    else:
        newest = max([t for t in times.values() if t] or [0])
        size = sum(1 for t in times.values() if t == newest) if newest else 0
    return {"size": size, "retired": retired_at(text)}


def upgrade(target, f, entry):
    """An entry of an older index format, its file unchanged since: what the new format adds, from the file as it is —
    a heading's level, a section's match keys, a document's retired line and the size of its last change (from the last
    commit's numstat). Blame, the costly part, is kept (a full rebuild of a large project outran the hooks' timeout, so
    every hook would have been killed until one finished)."""
    if not entry.get("sections") or (entry.get("keyed") == KEYED and "size" in entry):
        return entry
    text = read_text(target, f)
    if "size" not in entry:
        stat = (git(target, "log", "-1", "--format=", "--numstat", "--", f) or "").split()
        entry["size"] = int(stat[0]) if stat and stat[0].isdigit() else 0
        entry["retired"] = retired_at(text)
    if entry.get("keyed") == KEYED:
        return entry
    lines = text.split("\n")
    for sec in entry["sections"]:
        if "level" not in sec:
            h = re.match(r"(#{1,6})\s", lines[sec["start"] - 1]) if sec.get("heading") and 0 < sec["start"] <= len(lines) else None
            sec["level"] = len(h.group(1)) if h else (1 if sec.get("heading") else 0)
        sec["keys"] = section_keys(sec.get("words") or [], "\n".join(lines[sec["start"] - 1:sec["end"]]))
    entry["keyed"] = KEYED
    return entry


def index(target, rebuild=False):
    path = os.path.join(cache_dir(target), "index.json")
    idx = load(path)
    if rebuild or idx.get("format") != INDEX_FORMAT or idx.get("keyed") != KEYED or idx.get("key") != tree_key(target):
        idx = build_index(target, None if rebuild or idx.get("format") not in (INDEX_FORMAT,) + MIGRATES else idx)
        save(path, idx)
    entries = idx["entries"]   # the flat views every query ranks over, derived on load, never stored twice
    idx["sections"] = [x for f in sorted(entries) for x in entries[f].get("sections", [])]
    idx["said"] = [x for f in sorted(entries) for x in entries[f].get("said", [])]
    return idx


def when(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t)) if t else "uncommitted"


def file_changed(target, path):
    """When the file last changed: 'uncommitted' if it differs from HEAD now, else its last commit's time."""
    if (git(target, "status", "--porcelain", "--", path) or "").strip():
        return "uncommitted"
    t = (git(target, "log", "-1", "--format=%ct", "--", path) or "").strip()
    return int(t) if t else None


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(t)) if t else "uncommitted"


def freshness(s, changed):
    """A section's age against the file's: what a reader should trust it for."""
    if s.get("changed") is None:
        return "being edited (uncommitted)"
    if changed == "uncommitted":
        return "written %s; the file has uncommitted changes since" % stamp(s["changed"])
    if changed and changed > s["changed"]:
        return "written %s; the file changed after (%s) — read it against the code" % (stamp(s["changed"]), stamp(changed))
    return "written %s" % stamp(s["changed"])


# ---------------------------------------------------------------- ranking

def retired_of(idx, f):
    """The line where document f says at its top that it no longer holds (retired_at, kept in the index), or None."""
    return ((idx.get("entries") or {}).get(f) or {}).get("retired")


def retired_mark(idx, f):
    """What every quote from a retired document carries: guin-site's plan/BACKEND.md (retired 10-12) describes the D1, R2 and
    admin structure the site no longer has, and `find admin` quoted it as plainly as the plan that replaced it."""
    at = retired_of(idx, f)
    return " — retired document (says so at line %d)" % at if at else ""


def rank_sections(idx, query, about=None, top=SHOW, history=0):
    """The sections that share the query's words, best first — a live document's before a retired one's whatever their
    scores (what holds answers before what was once decided); `history`: up to this many retired ones kept after the top
    when none made it — an answer that is asked for still shows that the old design spoke of it."""
    secs = idx["sections"]
    if about and about.lower().endswith(DOC_EXT):
        secs = [s for s in secs if s["file"] != about]   # its own sections are what is being edited: the reader has them open
    if not secs or not query:
        return []
    r = Ranker([s["words"] + acronyms_of(s) for s in secs])
    co = collections.Counter()
    if about:
        for c in idx["commits"]:
            if about in c["files"]:
                co.update({f for f in c["files"] if f.lower().endswith(DOC_EXT)})
    scored = []
    for i, s in enumerate(secs):
        sc = r.score(query, i)
        if sc <= 0:
            continue
        if about and about in s.get("names", ()):
            sc *= 1.5   # it names this file outright
        if co.get(s["file"]):
            sc *= 1 + 0.25 * min(co[s["file"]], 4)   # documents changed in the same commits as this file
        scored.append((bool(retired_of(idx, s["file"])), -sc, i))
    scored.sort()
    out = [secs[i] for _, _, i in scored[:top]]
    if history and not any(old for old, _, _ in scored[:top]):
        out += [secs[i] for old, _, i in scored[top:] if old][:history]
    return out


def acronyms_of(section):
    """A section's two-letter acronyms (D1, R2, OG), kept in its match keys: `words` leaves them out, and `find D1` on
    guin-site found nothing."""
    return [k for k in section.get("keys") or () if len(k) == 2 and k.isascii() and k.isalnum()]


def query_terms(text):
    """What an asked question is matched on: its words and its two-letter acronyms."""
    return words(text) + [a.lower() for a in ACRONYM.findall(text)]


def rank_items(items, query, top):
    if not items or not query:
        return []
    r = Ranker([it["words"] for it in items])
    scored = sorted(((r.score(query, i), i) for i in range(len(items))), reverse=True)
    return [items[i] for sc, i in scored[:top] if sc > 0]


# ---------------------------------------------------------------- what a shell command reads and writes

SCRIPT_WRITE = re.compile(r"open\([^)]*['\"][wax]b?\+?['\"]|write_text|write_bytes|writeFileSync|writeFile\(|json\.dump\(|shutil\.(?:move|copy)|os\.(?:rename|replace|remove)|unlink\(")
SHELL_WRITE = re.compile(r"(?<![0-9&<])>>?|\bsed\b[^|;&]*\s-i|\bperl\b[^|;&]*\s-p?i|\btee\b|\bgit\s+(?:mv|rm)\b|(?:^|[\s;&|(])(?:mv|cp|rm|touch)\s")
SHELL_READ = re.compile(r"(?:^|[\s;&|(])(?:cat|head|tail|sed|awk|less|more|nl|bat|grep|egrep|rg)\s")
READERS = {"cat": 0, "head": 0, "tail": 0, "less": 0, "more": 0, "nl": 0, "bat": 0, "sed": 1, "awk": 1, "grep": 1, "egrep": 1, "rg": 1}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)


def shell_words(text):
    """Words of a shell command with its operators apart (`>`, `>>`, `&&`, `|`, `;`), quotes honoured: a `>` inside a
    commit message is not a redirect (guin-site, 10-02: one was read as writing the files a `git add` named)."""
    import shlex
    try:
        lx = shlex.shlex(text, posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        return list(lx)
    except ValueError:
        return text.split()


def script_targets(script):
    """The paths an inline script writes: the first argument of each writing call — a string, or a name whose assignment
    holds strings (`p = ROOT / "plan" / "PLAN.md"`, `for p in ["a.md", "b.md"]`). A path the script only reads is not
    written: on guin-site a script that wrote PLAN.md and read three others was taken for writing all four."""
    calls = [r"open\(\s*([^,()]+?)\s*,\s*['\"][wax]", r"([\w.\[\]'\"/ ()]+?)\.write_(?:text|bytes)\(", r"writeFile(?:Sync)?\(\s*([^,()]+?)\s*,",
             r"json\.dump\([^,]+,\s*open\(\s*([^,()]+?)\s*,", r"shutil\.(?:move|copy\w*)\([^,]+,\s*([^,()]+?)\s*[,)]",
             r"os\.(?:rename|replace)\([^,]+,\s*([^,()]+?)\s*[,)]", r"os\.remove\(\s*([^,()]+?)\s*\)", r"([\w.]+)\.unlink\("]
    out, unresolved = set(), False

    def literals(expr):
        lits = re.findall(r"['\"]([^'\"]+)['\"]", expr)
        return ["/".join(lits)] if lits else []

    for pat in calls:
        for m in re.finditer(pat, script):
            arg = m.group(1).strip()
            lits = literals(arg)
            if lits:
                out.update(lits)
                continue
            name = re.match(r"[A-Za-z_]\w*", arg)
            if not name:
                unresolved = True
                continue
            n = re.escape(name.group(0))
            found = set()
            for a in re.finditer(r"(?m)^[ \t]*%s\s*=\s*(.+)$" % n, script):
                found.update(literals(a.group(1)))
            for a in re.finditer(r"for\s+%s\s+in\s+[\[(]([^\])]*)[\])]" % n, script):
                found.update(re.findall(r"['\"]([^'\"]+)['\"]", a.group(1)))
            out |= found
            unresolved = unresolved or not found
    if unresolved:
        # a name bound some other way (`for path, text in files.items()`): the path-like strings it could hold, less those
        # the script only reads — guin-site's eight new endpoint files were written from a dict this way
        reads = set(re.findall(r"(?:Path\(\s*|open\(\s*)['\"]([^'\"]+)['\"]\s*\)?\s*(?:\.read_(?:text|bytes)\(|\)|,\s*['\"]r)", script))
        out |= {p for p in re.findall(r"['\"]([\w.\[\]-]+(?:/[\w.\[\]-]+)*\.\w+)['\"]", script) if p not in reads}
    return out


def shell_files(target, cwd, command, files):
    """(written, read): the project files a shell command writes and reads, relative to the project root. Paths are read
    against the shell's directory, which follows `cd` (the hook's cwd is the shell's: a command run in tests/ named
    `test_bots.py`, and the file was taken for another). Writes: redirect targets; operands of mv, cp, rm, touch, tee,
    `git mv/rm`, `sed -i`, `perl -pi` (a directory stands for its files); paths an inline script writes. Reads: operands
    of cat, head, tail, sed, awk, grep, rg, less, nl — files only, never a whole directory."""
    root = os.path.realpath(target)
    fileset = set(files)
    cwd = os.path.realpath(cwd or root)
    written, read = set(), set()

    def rel(tok, here):
        tok = os.path.expanduser(tok)
        full = os.path.normpath(tok if os.path.isabs(tok) else os.path.join(here, tok))
        if full != root and not full.startswith(root + os.sep):
            return None
        return os.path.relpath(full, root).replace(os.sep, "/")

    def as_files(tok, here, dirs=True):
        if not tok or tok.startswith(("-", "$")) or any(c in tok for c in "*?{"):
            return set()
        r = rel(tok, here)
        if r is None:
            return set()
        if r in fileset:
            return {r}
        return {f for f in files if f.startswith(r.rstrip("/") + "/")} if dirs and r != "." else set()

    # inline scripts first: their bodies are data to the shell, and what they write is in their own words
    for m in HEREDOC.finditer(command):
        line = command[command.rfind("\n", 0, m.start()) + 1:m.start()]
        if re.search(r"\b(?:python3?|node|ruby|perl)\b", line):
            for p in script_targets(m.group(3)):
                written |= as_files(p, cwd, dirs=False)
    for m in re.finditer(r"\b(?:python3?|node)\s+-[ce]\s+(['\"])(.*?)\1", command, re.S):
        for p in script_targets(m.group(2)):
            written |= as_files(p, cwd, dirs=False)
    body = HEREDOC.sub("", command)
    here = cwd
    for line in body.split("\n"):
        words_ = shell_words(line)
        cmds, cur = [], []
        for w in words_:
            if w in ("&&", "||", ";", "|", "&", "(", ")"):
                cmds.append(cur)
                cur = []
            else:
                cur.append(w)
        cmds.append(cur)
        for w in cmds:
            i = 0
            while i < len(w):   # redirects first, wherever they stand
                if w[i] in (">", ">>", ">|", "&>") and i + 1 < len(w):
                    if not (i > 0 and w[i - 1].isdigit() and w[i + 1].startswith("&")):
                        written |= as_files(w[i + 1], here, dirs=False)
                    del w[i:i + 2]
                    if i > 0 and w[i - 1].isdigit():
                        del w[i - 1]
                        i -= 1
                    continue
                if w[i] in ("<", ">&", "<<", "<<<") and i + 1 < len(w):
                    del w[i:i + 2]
                    continue
                i += 1
            while w and re.match(r"^\w+=", w[0]):
                w = w[1:]
            if not w:
                continue
            if w[0] == "cd":
                if len(w) > 1:
                    nxt = os.path.normpath(os.path.join(here, os.path.expanduser(w[1])))
                    here = nxt if os.path.isdir(nxt) else here
                continue
            args = [a for a in w[1:] if not a.startswith("-")]
            if w[0] == "git" and len(w) > 1 and w[1] in ("mv", "rm"):
                for a in w[2:]:
                    if not a.startswith("-"):
                        written |= as_files(a, here)
            elif w[0] == "cp" and len(args) >= 2:
                written |= as_files(args[-1], here)   # the copy is written; its sources are read
                for a in args[:-1]:
                    read |= as_files(a, here, dirs=False)
            elif w[0] in ("mv", "rm", "touch", "tee"):
                for a in args:
                    written |= as_files(a, here)
            elif w[0] in ("sed", "perl") and any(re.match(r"-[a-z]*i", a) for a in w[1:]):
                ops = args[1:] if not any(a in ("-e", "-f") for a in w[1:]) else args
                for a in ops:
                    written |= as_files(a, here, dirs=False)
            elif w[0] in READERS:
                skip = READERS[w[0]] if not any(a in ("-e", "-f") for a in w[1:]) else 0
                for a in args[skip:]:
                    read |= as_files(a, here, dirs=False)
    return sorted(written), sorted(read - written)


def bash_targets(target, command, files, cwd=None):
    """The project files a shell command is about to write — an edit is an edit whichever tool makes it (guin-site, 10-02:
    102 Bash calls, no Edit, and the edit hook never fired)."""
    if not (SHELL_WRITE.search(command) or SCRIPT_WRITE.search(command)):
        return []
    return shell_files(target, cwd or target, command, files)[0]


# ---------------------------------------------------------------- commands

def relpath(target, path):
    p = os.path.relpath(os.path.abspath(path), os.path.abspath(target)) if os.path.isabs(path) else os.path.normpath(path)
    return p.replace(os.sep, "/")


def is_text(target, path):
    if path.lower().endswith(DOC_EXT + CODE_EXT):
        return True
    try:
        with io.open(os.path.join(target, path), "rb") as fh:
            return b"\0" not in fh.read(8192)
    except OSError:
        return False


def file_report(target, path, brief=False):
    """The lines for one file. brief: what the edit hook injects (overlay output cut, with the command that prints it whole).
    A document being edited is told the other documents that say the same things, and the files it names with when each
    last changed — documents disagreeing with each other or with the code are what the verifier rejected (guin-site, 10-02);
    its own sections are open in front of the reader already."""
    idx = index(target)
    path = relpath(target, path)
    lines = []
    exists = os.path.isfile(os.path.join(target, path))
    doc = path.lower().endswith(DOC_EXT)
    changed = file_changed(target, path) if exists else None
    if exists and is_text(target, path):
        query = words(read_text(target, path) + " " + path)
    else:
        query = words(path)   # a picture's bytes are not words: its name is all that can be matched
    secs = rank_sections(idx, query, about=path, history=1)
    head = "jokbo — %s" % path + ("" if exists else " (not in the tree: ranked by its name only)")
    if doc and retired_of(idx, path):
        head += " — this document says at line %d that it is retired: what it describes is history" % retired_of(idx, path)
    lines.append(head)
    if secs:
        lines.append(("other documents that say the same things" if doc else "documents that speak of it")
                     + " (ranked by shared words — nobody confirmed these; open the ones that matter):")
        for s in secs:
            fresh = ("written %s" % stamp(s["changed"]) if s.get("changed") else "being edited (uncommitted)") if doc else freshness(s, changed)
            lines.append("  %s  (lines %d-%d) — %s%s" % (s["id"], s["start"], s["end"], fresh, retired_mark(idx, s["file"])))
    else:
        lines.append("documents: no %ssection shares its words" % ("other " if doc else ""))
    named = []
    if doc:
        named = sorted({g for s in (idx.get("entries", {}).get(path) or {}).get("sections", []) for g in s.get("names", [])})
        if named:
            mine = changed if isinstance(changed, int) else None
            lines.append("files it names (%d):" % len(named))
            for g in named[:8 if brief else len(named)]:
                gc = file_changed(target, g) if os.path.exists(os.path.join(target, g)) else None
                note = "gone from the tree" if gc is None else ("uncommitted changes" if gc == "uncommitted" else "changed %s" % stamp(gc))
                if mine and isinstance(gc, int) and gc > mine:
                    note += ", after this document — read it against them"
                lines.append("  %s — %s" % (g, note))
            if brief and len(named) > 8:
                lines.append("  … %d more" % (len(named) - 8))
    hist = [c for c in idx["commits"] if path in c["files"]][:2 if brief else 5]
    if hist:
        lines.append("last changed by:")
        for c in hist:
            lines.append("  %s %s %s — %s" % (when(c["at"]), c["sha"][:7], c["author"], c["subject"]))
    said_something = bool(secs or hist or (doc and named))
    for plugin, text, why in overlay(target, "file", {"path": path}):
        if why:
            lines.append("%s (declared `file` read): not read — %s" % (plugin, why))
            continue
        if not text.strip():
            continue   # it has nothing on this file: nothing to say
        body = text.split("\n")
        if brief:
            # a declared read is told in brief: its first lines, each cut, under a size a context can carry (two long run
            # briefs once made 20 KB, and the host put it in a file the agent never opened)
            kept, size = [], 0
            for l in body[:BRIEF_LINES]:
                l = cut(l, BRIEF_WIDTH)
                if size + len(l) > BRIEF_CHARS:
                    break
                kept.append(l)
                size += len(l)
            if len(kept) < len(body):
                kept.append("  … `%s \"%s\" file %s` prints all %d line(s)" % (PY, os.path.join(HERE, "jokbo.py").replace(os.sep, "/"), path, len(body)))
            body = kept
        lines.append("%s (declared `file` read):" % plugin)
        lines.extend("  " + l for l in body)
        said_something = True
    if brief and not said_something:
        return []   # nothing speaks of it: a hook says nothing rather than "nothing"
    return lines


def last_seen_path(target):
    return os.path.join(cache_dir(target), "seen.json")


def since_report(target, rev=None, brief=False):
    idx = index(target)
    head = (git(target, "rev-parse", "HEAD") or "").strip()
    seen = load(last_seen_path(target))
    origin = "given"
    if not rev:
        rev, origin = seen.get("head"), "this machine last looked %s" % (seen.get("at") or "")
    if rev and git(target, "rev-parse", "--verify", "-q", rev + "^{commit}") is None:
        return ["jokbo: %s is not a commit here" % rev]
    lines = []
    if not rev:
        commits = idx["commits"][:10]
        lines.append("jokbo: no earlier look recorded on this machine — the last %d commit(s):" % len(commits))
    else:
        shas = set((git(target, "rev-list", "%s..HEAD" % rev, "--", ".") or "").split())
        commits = [c for c in idx["commits"] if c["sha"] in shas]
        if not commits and rev == head:
            lines.append("jokbo: nothing committed since %s (%s)" % (rev[:7], origin.strip()))
        else:
            lines.append("jokbo: %d commit(s) since %s (%s):" % (len(commits), rev[:7], origin.strip()))
    shown = commits[:5] if brief else commits
    for c in shown:
        lines.append("  %s %s %s — %s (%d file(s))" % (when(c["at"]), c["sha"][:7], c["author"], c["subject"], len(c["files"])))
    if len(shown) < len(commits):
        lines.append("  … %d more" % (len(commits) - len(shown)))
    if rev:
        changed = changed_sections(target, idx, rev)
        if changed:
            lines.append("document sections changed:")
            doc_of = {x["id"]: x["file"] for x in idx["sections"]}
            for sid in (changed[:6] if brief else changed):
                lines.append("  " + sid + retired_mark(idx, doc_of.get(sid)))
            if brief and len(changed) > 6:
                lines.append("  … %d more" % (len(changed) - 6))
    dirty = [l for l in (git(target, "status", "--porcelain", "--", ".") or "").splitlines() if not l[3:].strip().startswith(record_paths(target))]
    if dirty:
        lines.append("uncommitted here: %d file(s)" % len(dirty))
    if not brief:
        for plugin, text, why in overlay(target, "since", {"since": rev}):
            if why:
                lines.append("%s (declared `since` read): not read — %s" % (plugin, why))
            elif text.strip():
                lines.append("%s (declared `since` read):" % plugin)
                lines.extend("  " + l for l in text.split("\n"))
    return lines


def changed_sections(target, idx, rev):
    """Sections of the current documents whose lines differ from REV (by the diff's new-side hunks)."""
    out = git(target, "diff", "-U0", rev, "--", *[f for f in idx["files"] if f.lower().endswith(DOC_EXT)]) if idx["files"] else None
    if not out:
        return []
    hit, cur = [], None
    for l in out.splitlines():
        if l.startswith("+++ "):
            cur = l[6:] if l.startswith("+++ b/") else None
        m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", l)
        if m and cur:
            a = int(m.group(1)); n = int(m.group(2) or 1)
            for s in idx["sections"]:
                if s["file"] == cur and s["start"] <= a + max(n, 1) - 1 and a <= s["end"] and s["id"] not in hit:
                    hit.append(s["id"])
    return hit


def sentences(text):
    """A section's sentences: Markdown marks stripped, cut at sentence ends and line breaks."""
    out = []
    for block in re.split(r"\n\s*\n|\n(?=\s*(?:(?:[-*+]|\d+\.)\s|\|))", text):
        if block.strip().startswith("|"):   # a table row says one thing: joined with its neighbours it was a blob nobody could quote
            if not re.fullmatch(r"[|\s:-]+", block.strip()) and len(block.strip()) > 15:
                out.append(re.sub(r"\s+", " ", block.strip()))
            continue
        block = re.sub(r"^\s*(?:#+|[-*+]|\d+\.)\s*", "", block.strip()).replace("\n", " ")
        for sent in re.split(r"(?<=[.!?。])\s+(?=\S)|(?<=다\.)\s+", block):
            sent = sent.strip()
            if len(sent) > 15:
                out.append(sent)
    return out


def quote(target, idx, section, query, least=2):
    """The sentence of a section that shares the most (and rarest) words with the query — what the section says about it,
    in its own words; None when no sentence shares `least` words. Pointers alone were never opened (guin-site: three
    cycles, ~130 k characters of section ids, none followed); a sentence is in front of the reader already."""
    lines = read_text(target, section["file"]).split("\n")[section["start"] - 1:section["end"]]
    df = collections.Counter(t for s in idx["sections"] for t in set(s["words"]))
    n = max(len(idx["sections"]), 1)
    q = set(query)
    best, score = None, 0.0
    for sent in sentences("\n".join(lines[1:] if section.get("heading") else lines)):
        shared = q & set(query_terms(sent))
        if len(shared) < least:
            continue
        sc = sum(math.log(1 + n / (1 + df[t])) for t in shared)
        if sc > score:
            best, score = sent, sc
    if best and len(best) > QUOTE:
        best = cut(best, QUOTE)
    return best


def find_report(target, terms):
    """Where the project speaks of something — each section with the sentence that says it, the code's own words, the
    commits, and the lines of the declared `decision` reads that share the words. An answer to read, not a list to open."""
    idx = index(target)
    q = query_terms(" ".join(terms))
    lines = ["jokbo — where the project speaks of: %s (ranked by shared words, confirmed by nobody)" % " ".join(terms)]
    secs = rank_sections(idx, q, top=6, history=1)
    lines.append("documents:" if secs else "documents: nothing shares these words")
    for s in secs:
        lines.append("  %s  (lines %d-%d, written %s)%s" % (s["id"], s["start"], s["end"], when(s.get("changed")), retired_mark(idx, s["file"])))
        said_here = quote(target, idx, s, q, least=1)
        if said_here:
            lines.append("    \u201c%s\u201d" % said_here)
    said = rank_items(idx["said"], q, 4)
    if said:
        lines.append("the code's own words:")
        for w in said:
            first = w["text"].strip().split("\n")[0]
            lines.append("  %s:%d — %s" % (w["file"], w["line"], first))
    commits = [dict(c, words=query_terms(c["subject"])) for c in idx["commits"]]
    hits = rank_items(commits, q, 5)
    if hits:
        lines.append("commits:")
        for c in hits:
            lines.append("  %s %s — %s" % (when(c["at"]), c["sha"][:7], c["subject"]))
    for plugin, text, why in overlay(target, "decision", {}):
        if why:
            lines.append("%s (declared `decision` read): not read — %s" % (plugin, why))
            continue
        body = text.split("\n")
        keep = [i for i, l in enumerate(body) if set(words(l)) & set(q)]
        if keep:
            lines.append("%s (declared `decision` read), the lines that share these words:" % plugin)
            seen = set()
            for i in keep:
                for j in (i - 1, i, i + 1) if i > 0 and body[i].startswith(" ") else (i, i + 1):
                    if 0 <= j < len(body) and j not in seen:
                        seen.add(j)
                        lines.append("  " + body[j])
    return lines


# ---------------------------------------------------------------- what a hook says: a map, a line, a change

FULL = 2      # files told per hook call; the rest are named
QUOTE = 220   # a quoted sentence, at most
NOTE_LINES, NOTE_WIDTH = 3, 200   # a declared `note` read: its first lines, each cut


def engine_path():
    return os.path.join(HERE, "jokbo.py").replace(os.sep, "/")


def cut(text, n):
    """A line shortened to at most n characters (and " …") where a reader can still read it: a list in it gives up its last
    items, so what follows the list stays (guin-site's map line "22 concept(s): about, …, ui-language ; 3 ope …" lost its
    open questions mid-word); otherwise it ends at an item's or a word's boundary, never inside a word."""
    if len(text) <= n:
        return text
    for m in sorted(re.finditer(r"[^\s,;·:()]+(?:, [^\s,;·:()]+){3,}", text), key=lambda m: -len(m.group(0))):
        items = m.group(0).split(", ")
        for k in range(1, len(items) - 1):
            short = text[:m.start()] + ", ".join(items[:-k]) + ", … +%d" % k + text[m.end():]
            if len(short) <= n:
                return short
    head = text[:max(1, n - 2)]
    floor = int(n * 0.6)
    at = max(head.rfind(sep) for sep in (", ", "; ", " · ", " — ", " | "))
    if at < floor:
        at = max(head.rfind(" "), head.rfind("\u3000"))
    if at < floor:
        at = len(head)   # one long word (a path, a hash): nothing to keep whole
    return head[:at].rstrip(" ,;:·—–-|(") + " …"


def day(t, now=None):
    """A change's date, compact: "10-12" this year, "2025-03-02" before it, "editing" for uncommitted lines (UTC, as git's
    stamps elsewhere)."""
    if t is None:
        return "editing"
    if not t:
        return "?"
    return time.strftime("%m-%d" if time.gmtime(t).tm_year == time.gmtime(now or time.time()).tm_year else "%Y-%m-%d", time.gmtime(t))


MAP_HEADINGS = 40   # heading lines the map gives, across documents; the rest are counted
MAP_PER_DOC = 14    # a long document shows its top-level sections only, at most this many
MAP_CHARS = 3000    # the whole map, at most: it is said at every session start, also to a one-prompt session


def outline(secs, budget):
    """A document's sections as a reader scans them — heading, line range and the date of its last change — the shape a
    session built itself at its first question (`grep -n "^#" plan/BACKEND.md plan/PLAN.md`, guin-site 10-08) and then read
    by `sed -n` ranges. A lone top heading is the document's title, not a section. A long document shows its higher levels
    only (each range runs to the next heading as high), within `budget` lines. ([lines], title, how many headings were left
    out)."""
    heads = [x for x in secs if x.get("heading")]
    if not heads:
        return [], None, 0
    end = max(x["end"] for x in secs)
    lv = lambda x: x.get("level") or 1
    title = None
    tops = [x for x in heads if lv(x) == min(lv(y) for y in heads)]
    if len(tops) == 1 and tops[0] is heads[0] and len(heads) > 1:
        title, heads = heads[0]["heading"], heads[1:]
    levels = sorted({lv(x) for x in heads})
    top = levels[0]
    for L in levels:
        if sum(1 for x in heads if lv(x) <= L) <= MAP_PER_DOC:
            top = L
    room = max(0, min(budget, MAP_PER_DOC))
    cands = [x for x in heads if lv(x) <= top]
    if len(cands) > room:
        # the newest sections are where the work is (guin-site 10-12: ATPROTO.md grew at its end, and the map showed its
        # first fourteen): keep the most recently changed, in document order
        recent = sorted(cands, key=lambda x: (x.get("changed") is None, x.get("changed") or 0, x["start"]), reverse=True)[:room]
        shown = sorted(recent, key=lambda x: x["start"])
    else:
        shown = cands
    out = []
    for x in shown:
        nxt = next((y["start"] for y in heads if y["start"] > x["start"] and lv(y) <= lv(x)), end + 1)
        # a range's date is its newest part's: a top section's subsections are inside it
        inside = [y for y in secs if x["start"] <= y["start"] < nxt]
        t = None if any(y.get("changed") is None for y in inside) else max(y.get("changed") or 0 for y in inside)
        out.append("    %d-%d  %s  %s" % (x["start"], nxt - 1, cut(x["heading"], 70), day(t)))
    return out, title, len(heads) - len(shown)


def doc_recency(secs):
    """When a document last changed: its newest section (one being edited counts as now)."""
    return max((time.time() if x.get("changed") is None else x["changed"]) for x in secs)


def doc_order(entries, by_doc):
    """Where the work is, first: by the day of a document's last change, then by how much that change wrote (the lines it
    left, or the lines being edited) — never by how many sections it has (guin-site 10-13: README.md and BACKEND.md changed
    in one commit; README.md was rewritten, BACKEND.md got a retirement note, and BACKEND.md's fourteen sections won)."""
    return sorted(by_doc, key=lambda f: (-int(doc_recency(by_doc[f]) // 86400), -(entries.get(f, {}).get("size") or 0), f))


def nested_repos(target):
    """The repositories inside the project that its own git does not hold — a directory with its own `.git` that the
    project's status names as one untracked or ignored entry (`!! engine/`), as a workspace of sibling repositories keeps
    them. Their documents are not this index's: each is a project of its own."""
    out = []
    for entry in (git(target, "status", "--porcelain", "-z", "--ignored=matching", "--untracked-files=normal", "--", ".") or "").split("\0"):
        if entry[:3] in ("?? ", "!! ") and entry.endswith("/"):
            path = entry[3:].rstrip("/")
            if not path.startswith(CACHE) and os.path.exists(os.path.join(target, path, ".git")):
                out.append(path)
    return sorted(out)


def nested_line(target):
    """One line for the repositories inside a workspace: where each keeps its documents and how many, and the map that reads
    them. sagoforce 10-26: the workspace's map listed its README and a vendored reference only, while the rules the person
    asked about lived in its repositories' specs and contracts — read by nothing until the person asked."""
    parts = []
    for n in nested_repos(target):
        docs = [f for f in (git(os.path.join(target, n), "ls-files", "-z") or "").split("\0") if f.lower().endswith(DOC_EXT)]
        if not docs:
            continue
        where = collections.Counter(f.split("/", 1)[0] + "/" if "/" in f else f for f in docs)
        parts.append("%s (%s; %d)" % (n, ", ".join(w for w, _ in where.most_common(3)), len(docs)))
    if not parts:
        return None
    return "  repositories inside, each its own map (`%s \"%s\" map --target DIR`): %s" % (PY, engine_path(), ", ".join(parts))


def map_report(target):
    """What this project holds and how to ask — not the content. Said at session start (and after a compaction) and frozen
    for the session (a map rewritten mid-session breaks the prompt cache and says things twice): the documents with their
    sections' headings, line ranges and dates, a line from each product that declares a `map` read, and the three questions
    jokbo answers. A document that says at its top it is retired gets one line, not a share of the headings."""
    idx = index(target)
    by_doc = collections.OrderedDict()
    for x in idx["sections"]:
        by_doc.setdefault(x["file"], []).append(x)
    entries = idx["entries"]
    lines = ["jokbo — what this project holds, and how to ask:"]
    budget, rest, spare = MAP_HEADINGS, [], []
    for f in doc_order(entries, by_doc):
        secs, last = by_doc[f], day(None if any(x.get("changed") is None for x in by_doc[f]) else doc_recency(by_doc[f]))
        at = entries.get(f, {}).get("retired")
        if at:
            title = next((x["heading"] for x in secs if x.get("heading")), "")
            lines.append("  %s%s — retired (says so at line %d), %s; %d section(s)" % (f, " — " + cut(title, 70) if title else "", at, last, len(secs)))
            spare.append(lines[-1])
            continue
        shown, title, hidden = outline(secs, budget) if budget > 0 else ([], None, 0)
        if not shown:
            rest.append("%s (%d, %s)" % (f, len(secs), last))
            continue
        lines.append("  %s%s (%d section(s), last %s%s):" % (f, " — " + cut(title, 70) if title else "", len(secs), last,
                                                            "; %d deeper or older not shown" % hidden if hidden else ""))
        lines.extend(shown)
        budget -= len(shown)
    if rest:
        lines.append("  other documents (sections, last change): %s" % ", ".join(rest[:8]) + (", +%d more" % (len(rest) - 8) if len(rest) > 8 else ""))
    inside = nested_line(target)
    if inside:
        lines.append(inside)
    for plugin, text, why in overlay(target, "map", {}):
        if text and text.strip():
            first = [l.strip() for l in text.strip().split("\n") if l.strip()][:2]
            lines.append("  %s: %s" % (plugin, " · ".join(cut(l, 240) for l in first)))
    ask = ("  ask: `%s \"%s\" find WORDS` — what was decided and where it is said, quoted; `since [REV]` — what changed; "
           "`file PATH` — all that stands on a file" % (PY, engine_path()))
    while sum(len(l) + 1 for l in lines) + len(ask) > MAP_CHARS:
        heads = [i for i, l in enumerate(lines) if l.startswith("    ")] or [i for i, l in enumerate(lines) if l in spare]
        if not heads:
            break
        del lines[heads[-1]]   # the least recent document's last heading goes first; a retired document's line after them
    lines.append(ask)
    return lines


def status_form(plugin_line, path):
    """A product's line that starts with the file's own path ("src/read.ts: not registered — the net does not see it …") says
    the file's standing in that product, the same words for every file in its state: its form, with the path taken out, or
    None for a line that says something of this file only (a concept's meaning, a run, a finding)."""
    body = plugin_line.split(": ", 1)[1] if ": " in plugin_line else plugin_line
    return plugin_line.split(": ", 1)[0] + ": {path}" + body[len(path):] if body.startswith(path) else None


def note_report(target, path, keep=None):
    """A line or two per fact that bears on a file, at the session's first look at it: what each product that declares a
    `note` read says of it (the meaning of the concept it realizes, the last run that touched it, an open finding); with
    none saying anything of the file itself, the one document sentence that speaks of it most directly, with its section
    and how current it is. A product's line on the file's standing ("not registered") is not what the file is about, so the
    sentence still comes. `keep(line, form, section)`: whether the session should be told the line (tell's once-per-session rules).
    [] when nothing does — a hook says nothing rather than "nothing"."""
    path = relpath(target, path)
    keep = keep or (lambda line, form, section=None: True)
    lines, about = [], False
    for plugin, text, why in overlay(target, "note", {"path": path}):
        if text and text.strip():
            for l in [l.strip() for l in text.strip().split("\n") if l.strip()][:NOTE_LINES]:
                line = "  %s: %s" % (plugin, cut(l, NOTE_WIDTH))
                form = status_form(line.strip(), path)
                about = about or form is None
                if keep(line, form):
                    lines.append(line)
    if not about:
        idx = index(target)
        exists = os.path.isfile(os.path.join(target, path))
        query = words(read_text(target, path) + " " + path) if exists and is_text(target, path) else words(path)
        # a live document's sentence first; a retired one's only when no live one speaks of the file, and marked
        ranked = rank_sections(idx, query, about=path, top=len(idx["sections"]))
        live = next((x for x in ranked if not retired_of(idx, x["file"])), None)
        old = next((x for x in ranked if retired_of(idx, x["file"])), None)
        s, said = None, None
        for x in (live, old):
            said = quote(target, idx, x, query, least=3) if x else None
            if said:
                s = x
                break
        if said:
            fresh = ("written %s" % stamp(s["changed"]) if s.get("changed") else "being edited") if path.lower().endswith(DOC_EXT) \
                else freshness(s, file_changed(target, path) if exists else None)
            line = "  %s (%s)%s: \u201c%s\u201d" % (s["id"], fresh, retired_mark(idx, s["file"]), said)
            if keep(line, None, s["id"]):
                lines.append(line)
    return ["jokbo — %s:" % path] + lines if lines else []


# ---------------------------------------------------------------- what a prompt asks that the project already says

JOSA = sorted("에서는 으로는 에게서 이라는 이라고 라는 라고 에서 에게 으로 까지 부터 처럼 보다 이나 이랑 하고 에는 과는 와는 "
              "은 는 이 가 을 를 의 에 로 와 과 도 만".split(), key=len, reverse=True)
ENDINGS = sorted("었는데 았는데 습니다 니다 어요 아요 해요 했다 한다 하는 하게 하면 해서 했어 해야 어서 아서 으면 는데 지만 었다 았다 "
                 "던 어 아 기 고 면 게 는 을 다 야 요 함 음 됨".split(), key=len, reverse=True)
TALK = set("""것 것이 것은 것도 가지 지금 이제 그러 그러면 그렇다 그런데 혹시 어떻 어떤 무슨 있 없 하 되 같 같이 다른 이런 그런 저런
이건 그건 아니 좋 진행 필요 가능 정도 경우 때문 그냥 아직 이미 많이 조금 너무 우선 결과 현재 내가 나는 우리 여기 거기 저기 위에 중에
전에 다음 부분 전체 하나 생각 말고 거의 굳이 완전히 방향 문제 설명 이해 알아 알려 해줘 해주 보여 보여주 사용 쓰 쓰는 쓰면 어차피 아무것
그리 그대 있는 없는 되는 있다 없다 된다 있어 없어 되어 됐다 하고 해도 했던 하던 있고 없고 되고 보고 쓰고 보는 했는 하는지
평소 이상 정말 진짜 사실 일단 바로 먼저 나중 계속 항상 자꾸 얼마 언제 어디 누가 무엇 어느 이거 저거 그거 이게 그게 저게 아무 모든 여러
모두 전부 뭔가 어쩌면 아마 혹은 또는 그리고 그래서 하지만 그러나 근데 그럼 그러니까 따라서 이번 지난 오늘 내일 어제 방금 아까 방법 com
www http https what how why when where which there their about would could should""".split())
PROMPT_RARE = 0.15   # a word in at most this share of the sections tells them apart
PROMPT_SHARED = 3    # words a quoted sentence shares with the prompt, at least
PROMPT_TOLD = 3      # sentences told for one prompt, at most


@functools.lru_cache(maxsize=None)
def stem(t):
    """A Korean word without its particles and one verbal ending — the key the prompt and the documents are matched on (a
    question says 만들어·페이지를, a plan 만들기·페이지). Both sides are cut the same way, so a rough cut still meets. A cut
    that would leave one syllable is not made: 화면, 로고 and 사고 are nouns, not 화·로·사 with an ending (a one-syllable key
    said nothing and was dropped, so the noun never matched). Other scripts are left as they are. Cached: a large project's
    sections hold millions of tokens over a few thousand words."""
    if t.isascii():
        return t
    while True:
        j = next((j for j in JOSA if t.endswith(j) and len(t) - len(j) >= 2), None)
        if not j:
            break
        t = t[:-len(j)]
    e = next((e for e in ENDINGS if t.endswith(e) and len(t) - len(e) >= 2), None)
    return t[:-len(e)] if e else t


ACRONYM = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9](?![A-Za-z0-9])")   # OG, S3: two letters `words` leaves out


def section_keys(ws, text):
    """What a prompt is matched on in a section, kept in the index: its words' stems and its two-letter acronyms."""
    return sorted({stem(t) for t in ws} | {a.lower() for a in ACRONYM.findall(text)})


SHELL_STRONG = re.compile(r"\$|^(?:\|\|?|&&|>>?|2>&1|<)$|^--\w|^\w+=\S|^~/")   # a token only a command line has
SHELL_WEAK = re.compile(r";$|^-\w")


def prose(text):
    """What the person wrote, without what they pasted: <pasted_content> blocks, fenced code, a backquoted command, and a
    command line typed inline (a run of ASCII tokens holding a pipe, a $VAR, a --flag, VAR=…) — the words of a command are
    not the question (a trial prompt that asked about the read path and quoted a `python3 … | head` line was answered with
    the test runner's sentence). A backquoted single name (`site-build`) is the person's word and stays."""
    text = re.sub(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", " ", text or "", flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`\n]*)`", lambda m: " " if re.search(r"\s", m.group(1).strip()) else " %s " % m.group(1), text)
    out = []
    for line in text.split("\n"):
        keep, run = [], []
        for t in line.split() + [None]:
            if t is not None and t.isascii():
                run.append(t)
                continue
            if not (any(SHELL_STRONG.search(x) for x in run) or sum(bool(SHELL_WEAK.search(x)) for x in run) >= 2):
                keep += run
            run = []
            if t is not None:
                keep.append(t)
        out.append(" ".join(keep))
    return "\n".join(out)


def prompt_words(text):
    """The words of what the person typed (prose: what they pasted or quoted as a command is not the question); talk (지금, 혹시, 어떻게 …) and one-syllable remainders say nothing about the project."""
    text = prose(text)
    out = []
    for t in words(text) + [a.lower() for a in ACRONYM.findall(text)]:
        t = stem(t)
        if t not in TALK and (t.isascii() or len(t) >= 2) and not re.fullmatch(r"[ㄱ-ㆎ]+", t) and t not in out:
            out.append(t)
    return out


def prompt_report(target, text, told=()):
    """When a prompt asks about something the project's documents already say: up to PROMPT_TOLD sentences, quoted, each
    with where it is and how current — said before the session reads the prompt, which is when a question arises (guin-site,
    10-08 → 10-11: the reader was never asked in three days; the session went to grep, or answered from memory). Strict:
    a sentence is told only when it shares PROMPT_SHARED of the prompt's words, two of them rare in the documents (in at
    most PROMPT_RARE of the sections), and three rare ones or 40% of the prompt's rare words, and never under 20% of them —
    so a long musing that brushes many sections, and "ㄱㄱ", say nothing. Silence is the common case. `told`: sections already quoted this
    session. ([lines], [section ids told])."""
    idx = index(target)
    secs = idx["sections"]
    if not secs:
        return [], []
    keys = [set(x.get("keys") or ()) for x in secs]
    df = collections.Counter(t for k in keys for t in k)
    n = len(secs)
    q = [t for t in prompt_words(text) if df.get(t)]
    rare = {t for t in q if df[t] / n <= PROMPT_RARE}
    if len(rare) < 2:
        return [], []
    cand = []
    for i, k in enumerate(keys):
        m = set(q) & k
        if len(m) >= PROMPT_SHARED and len(m & rare) >= 2 and secs[i]["id"] not in told:
            cand.append((sum(math.log(1 + n / (1 + df[t])) for t in m), i))
    out, ids = [], []
    for _, i in sorted(cand, key=lambda c: (bool(retired_of(idx, secs[c[1]]["file"])), -c[0]))[:8]:   # a live document's first
        x = secs[i]
        body = read_text(target, x["file"]).split("\n")[x["start"] - 1:x["end"]]
        best, key = None, (0, 0)
        for sent in sentences("\n".join(body[1:] if x.get("heading") else body)):
            sk = set(section_keys(words(sent), sent))
            shared, shared_rare = set(q) & sk, set(q) & sk & rare
            if len(shared) >= PROMPT_SHARED and len(shared_rare) >= max(2, 0.2 * len(rare)) and (len(shared_rare) >= 3 or len(shared_rare) >= 0.4 * len(rare)) \
                    and (len(shared_rare), len(shared)) > key:
                best, key = sent, (len(shared_rare), len(shared))
        if best:
            said = cut(best, QUOTE)
            out.append("  %s (lines %d-%d, written %s)%s: \u201c%s\u201d" % (x["id"], x["start"], x["end"], when(x.get("changed")),
                                                                            retired_mark(idx, x["file"]), said))
            ids.append(x["id"])
        if len(out) >= PROMPT_TOLD:
            break
    if not out:
        return [], []
    return (["jokbo — the project already says (ranked by shared words, confirmed by nobody; `%s \"%s\" find WORDS` for more):"
             % (PY, engine_path())] + out), ids


# ---------------------------------------------------------------- what a shell command searches for

OPS = {"|", "||", "&&", ";", "&", "(", ")", "|&", ";;"}
REDIRECT = {">", ">>", "<", ">&", "&>", "<&", "&>>", ">|"}
GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}   # git's own options that take a value
BLAME_VALUED = {"-L", "-S", "--contents", "--ignore-rev", "--ignore-revs-file", "--since", "-C", "-M"}
GREP_VALUED = {"-A", "-B", "-C", "-m", "--include", "--exclude", "--exclude-dir", "-g", "--glob", "-t", "--type", "-f", "--max-count",
               "--context", "--after-context", "--before-context", "--type-not", "-T", "--iglob"}
SEARCHING = re.compile(r"\bgit\b.*\b(?:log|blame)\b|(?:^|[\s;&|(])(?:e?grep|rg)\s")   # a command that may search: else no index is loaded


def commands_of(command):
    """A shell line as its simple commands: [(argv, piped)] — `piped` when it reads another command's output. Quotes are
    taken first (`grep -E "a|b"` is one pattern, not a pipe); redirections and leading VAR=… are dropped. [] when it does
    not parse."""
    import shlex
    try:
        lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return []
    out, argv, piped, skip = [], [], False, False
    for t in tokens + [";"]:
        if skip:
            skip = False
            continue
        if t in REDIRECT:
            if argv and argv[-1].isdigit():
                argv.pop()
            skip = True
            continue
        if t in OPS:
            while argv and re.match(r"^\w+=", argv[0]):
                argv = argv[1:]
            if argv:
                out.append((argv, piped))
            argv, piped = [], t in ("|", "|&")
            continue
        argv.append(t)
    return out


def history_targets(target, command, files, cwd=None):
    """What a shell command looks up in the project's history or documents: ("path", P) for `git log … -- P`, `git log P`,
    `git blame P` (P a tracked file; git's own options such as -C DIR skipped); ("terms", T) for a recursive grep / rg,
    or a grep over documents. A grep that filters another command's output (after a pipe) searches nothing of the
    project. Paths are taken from where the command ran (`cd` and `git -C` followed). Terms are the pattern's words; a
    pattern with none (`at-31`) is left to grep."""
    out = []
    here = cwd or target
    for argv, piped in commands_of(command or ""):
        if argv[0] == "cd":
            here = os.path.normpath(os.path.join(here, os.path.expanduser(argv[1]))) if len(argv) > 1 else target
            continue
        if argv[0] == "git":
            i, at = 1, here
            while i < len(argv) and argv[i].startswith("-"):
                if argv[i] == "-C" and i + 1 < len(argv):
                    at = os.path.normpath(os.path.join(at, argv[i + 1]))
                i += 2 if argv[i] in GIT_VALUED else 1
            if i >= len(argv) or argv[i] not in ("log", "blame"):
                continue
            sub, rest = argv[i], argv[i + 1:]
            if "--" in rest:
                cands = rest[rest.index("--") + 1:]
            else:
                cands, j = [], 0
                while j < len(rest):
                    a = rest[j]
                    if a.startswith("-"):
                        j += 2 if sub == "blame" and a in BLAME_VALUED else 1
                        continue
                    cands.append(a)
                    j += 1
            for c in cands:
                rel = relpath(target, os.path.join(at, c))
                if rel in files:
                    out.append(("path", rel))
        elif os.path.basename(argv[0]) in ("grep", "egrep", "rg") and not piped:
            opts = [a for a in argv[1:] if a.startswith("-")]
            rec = os.path.basename(argv[0]) == "rg" or any(re.match(r"^-[a-zA-Z]*[rR]", o) or o == "--recursive" for o in opts)
            args, pat, i = argv[1:], None, 0
            while i < len(args):
                a = args[i]
                if a in ("-e", "--regexp") and i + 1 < len(args):
                    pat = args[i + 1] if pat is None else pat + "|" + args[i + 1]
                    i += 2
                    continue
                if a in GREP_VALUED and i + 1 < len(args):
                    i += 2
                    continue
                if not a.startswith("-") and pat is None:
                    pat = a
                elif not a.startswith("-") and a.lower().endswith(DOC_EXT) and inside(target, here, a):
                    rec = True   # a grep over a document is a search of what the project says
                i += 1
            terms = pattern_terms(pat)
            # a search of what the project SAYS: a grep over documents (a document named, or --include of one). A recursive
            # grep through code is a code search — 11 answers to those went uncited (guin-site 10-12)
            ops = [a for a in args if not a.startswith("-") and a != pat]
            docs = [f for f in files if f.lower().endswith(DOC_EXT)]

            def holds_docs(a):
                rel = inside(target, here, a)
                return rel is not None and (rel.lower().endswith(DOC_EXT) or rel == "." or any(f.startswith(rel.rstrip("/") + "/") for f in docs))
            on_docs = any(holds_docs(a) for a in (ops or ["."])) or \
                any(re.search(r"--include=?\S*\.(?:md|markdown)\b", a) for a in args) or \
                any(a in ("--type=md", "-tmd") for a in args) or ("-t" in args and "md" in args)
            if rec and terms and on_docs:
                out.append(("terms", terms))
    return list(dict.fromkeys(out))


def inside(target, here, arg):
    """A path argument as the project's own (relative to the root), or None: a path outside the project, or one the shell
    would expand that jokbo cannot (`$M/README.md`, `${X}`, `$(…)`, backticks — `~` is the home). guin-site 10-15: `grep …
    $M/README.md`, $M a plugin's install, was answered with the project's own sections on the words it searched."""
    if re.search(r"[$`]", arg) or (arg.startswith("~") and os.path.expanduser(arg).startswith("~")):
        return None
    root = os.path.realpath(target)
    full = os.path.realpath(os.path.join(here, os.path.expanduser(arg)))   # /var and /private/var are one place
    if full != root and not full.startswith(root + os.sep):
        return None
    return relpath(root, full) if full != root else "."


def pattern_terms(pat):
    """A search pattern's words, as `find` takes them: regex syntax is not a word."""
    terms = [t for t in words(re.sub(r"\\[|bBwsdW]|[\^$.*+?()\[\]{}|\\]", " ", pat or "")) if t]
    return " ".join(dict.fromkeys(terms))


def history_report(target, kind, what):
    """Brief: what the documents say of a file or of the words a search looked for — two sections with the sentence that
    says it, and the commit whose subject says most (guin-site 10-10: `git log -- wrangler.worker.build.jsonc` was run to
    learn why the file exists; `find` puts the commit that says so first)."""
    idx = index(target)
    if kind == "path":
        q = words(what) + (words(read_text(target, what)) if is_text(target, what) else [])
        naming = sorted((x for x in idx["sections"] if what in x.get("names", ())), key=lambda x: bool(retired_of(idx, x["file"])))
        secs = naming[:2] or rank_sections(idx, q, about=what, top=2)
        head = "jokbo — what the documents say of %s (`%s \"%s\" file %s` for all):" % (what, PY, engine_path(), what)
        qq = q   # the sentence that shares the file's own words: its name alone is often not in the sentence that explains it
    else:
        q = qq = query_terms(what)
        secs = rank_sections(idx, q, top=2)
        head = "jokbo — where the project speaks of: %s (`%s \"%s\" find %s` for all):" % (what, PY, engine_path(), what)
    lines = []
    for x in secs:
        said = quote(target, idx, x, qq, least=2 if kind == "path" else 1)
        if said:
            lines.append("  %s (lines %d-%d, written %s)%s: \u201c%s\u201d" % (x["id"], x["start"], x["end"], when(x.get("changed")),
                                                                              retired_mark(idx, x["file"]), said))
    if kind == "terms":
        hits = rank_items([dict(c, words=query_terms(c["subject"])) for c in idx["commits"]], q, 1)
        lines += ["  commit %s %s — %s" % (when(c["at"]), c["sha"][:7], c["subject"][:200]) for c in hits]
    return [head] + lines if lines else []


SEARCHES = 2   # searches answered per command


def searched(target, session_id, command=None, cwd=None, pattern=None):
    """The history/document searches in a command (or the host's own Grep: `pattern`) that this session has not had
    answered yet: told once per target. A command that cannot be a search costs nothing (no index is loaded)."""
    if pattern is not None:
        terms = pattern_terms(pattern)
        found = [("terms", terms)] if terms else []
    elif command and SEARCHING.search(command):
        found = history_targets(target, command, set(index(target)["files"]), cwd)
    else:
        found = []
    if not found:
        return []
    st, path = session_state(target, session_id)
    done = st.get("searched", [])
    lines = []
    for kind, what in [f for f in found if "%s:%s" % f not in done][:SEARCHES]:   # the rest wait for the next search
        lines += history_report(target, kind, what)
        done.append("%s:%s" % (kind, what))
    st["searched"] = done
    save(path, st)
    return lines


def session_state(target, session_id):
    sid = re.sub(r"[^\w.-]", "_", str(session_id or "no-session"))
    path = os.path.join(cache_dir(target), "sessions", sid + ".json")
    st = load(path, default={})
    return (st if isinstance(st, dict) else {}), path   # a list was 1.1/1.2's shown-files: a new format starts over


def snapshot(target):
    """The tree as a session sees it between its calls: HEAD, every path that differs from it with its status, and when."""
    head = (git(target, "rev-parse", "HEAD") or "").strip()
    out = git(target, "status", "--porcelain", "-z", "--untracked-files=all", "--", ".") or ""
    status, parts, i = {}, out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) >= 4:
            status[entry[3:]] = entry[:2]
            if entry[0] in "RC" or entry[1] in "RC":
                i += 1
    return {"head": head, "status": status, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def record_owners(target):
    """{plugin: [record path prefixes]} — the lock's `record-paths`."""
    _, lock = manifest_and_lock(target)
    owners = lock.get("record-paths")
    return {k: [q for q in v if isinstance(q, str)] for k, v in owners.items() if isinstance(v, list)} if isinstance(owners, dict) else {}


def owner_of(owners, p):
    return next((plugin for plugin, ps in sorted(owners.items()) for q in ps if p.startswith(q) or p == q.rstrip("/")), None)


def change_report(target, old, new, own=()):
    """What changed between two snapshots that this session did not do — another session, a hired worker, the owner:
    commits, and the paths changed, grouped by whose records they are (the lock's `record-paths`; jokbo names no
    product), by whose hands a product's records say made them (the declared `hands` reads — a hired worker's code edits
    are still told, the session did not write them, but as that worker's), documents, and the rest. [] when nothing did. `own`: plugins working for this session (own_plugins) — their
    records, and a commit that writes them, are the session's own."""
    tree = lambda snap: {k: v for k, v in (snap or {}).items() if k != "at"}
    if not old or tree(old) == tree(new):
        return []
    owners = record_owners(target)
    mine = lambda p: owner_of(owners, p) in own
    paths = {p for p in set(old.get("status", {})) | set(new.get("status", {})) if old.get("status", {}).get(p) != new.get("status", {}).get(p)}
    commits = []
    if old.get("head") and new.get("head") and old["head"] != new["head"]:
        log = git(target, "log", "--format=%x01%h %s", "--name-only", "%s..%s" % (old["head"], new["head"]), "--") or ""
        for entry in log.split("\x01")[1:]:
            head, _, names = entry.partition("\n")
            files = [f for f in names.splitlines() if f.strip()]
            if own and any(mine(f) for f in files):
                continue   # the engine of a plugin working for the session committed it (its records with the work)
            commits.append(head.strip())
            paths |= set(files)
    paths = {p for p in paths if not p.startswith((CACHE + "/", ".git/")) and not (own and mine(p))}
    if not commits and not paths:
        return []
    whose = hands(target, [p for p in paths if not owner_of(owners, p)], old.get("at"))   # a product's records already say whose they are
    groups = collections.OrderedDict()
    for p in sorted(paths):
        who = owner_of(owners, p)
        key = ("%s's records" % who) if who else ("changed by %s" % whose[p]) if p in whose \
            else ("documents" if p.lower().endswith(DOC_EXT) else "files")
        groups.setdefault(key, []).append(p)
    lines = ["jokbo — changed by another hand since this session's last call:"]
    if commits:
        lines.append("  %d commit(s): %s%s" % (len(commits), "; ".join(commits[:3]), " …" if len(commits) > 3 else ""))
    for key, ps in groups.items():
        lines.append("  %s: %s%s" % (key, ", ".join(ps[:4]), " +%d more" % (len(ps) - 4) if len(ps) > 4 else ""))
    return lines


DATED = re.compile(r"[A-Za-z0-9][\w.-]*\d[\w.-]*")   # a run id, a commit, a review: what a line can be dated by
PLAIN_DATE = re.compile(r"\d{4}[-./]?\d{2}[-./]?\d{2}(?:[T ]?\d{2}(?::?\d{2}){0,2}Z?)?")


def names_in(text):
    """The names a line can be dated by: tokens of seven characters or more with four digits or more (run-20261010-132224-0b9a,
    a review id) that are not a plain date. python3 or a /Users/someone-3/ path is not a name (a commit subject that held one
    once dropped every line that did); a date is not one either (a subject with 2026-10-11 dropped every `written 2026-10-11`)."""
    return {t for t in DATED.findall(text) if len(t) >= 7 and sum(c.isdigit() for c in t) >= 4 and not PLAIN_DATE.fullmatch(t)}


def since_start(target, st):
    """The names (run ids, commit hashes …) of what was committed since this session started: a note line naming one tells
    the session what it did itself, or what the change line already said (guin-site 10-08 → 10-11: 14 of 45 "last
    changed in run …" lines named a run the session had just closed). Product-agnostic: a line is dated by the names in it."""
    start = st.get("start")
    if not start:
        return set()
    log = git(target, "log", "--format=%h %H%x00%s", "%s..HEAD" % start, "--") or ""
    out = set()
    for l in log.splitlines():
        shas, _, subject = l.partition("\0")
        out |= set(shas.split()) | names_in(subject)
    return out


def tell(target, rels, session_id, why):
    """What a hook says about files the session reads or writes: each file once per session (whichever hook comes first),
    at most FULL told, the rest named; a note, not a report (see note_report). A line is told once per session, whichever
    file it came with (the same concept's sentence came with eight files), and a product's line on a file's standing once
    in its form (guin-site 10-15: "… not registered" with every file the session opened); a line about what was committed
    since the session started is left out (since_start); and when documents name the file outright in sections the
    session was not shown, one pointer to `file` (spelled out the first time) — none when what it would add was said."""
    st, path = session_state(target, session_id)
    shown = st.get("shown", [])
    rp = record_paths(target) + (".git/",)
    new = [r for r in rels if r not in shown and not r.startswith(rp) and os.path.isfile(os.path.join(target, r))]
    if not new:
        return []
    told_lines = set(st.get("told", []))
    seen = set(st.get("quoted", [])) | set(st.get("noted", []))   # sections whose sentence the session was given
    recent = since_start(target, st)
    lines, told = [], []

    def keep(l, form, section=None):
        key = l.strip()
        if key in told_lines or (form and "form:" + form in told_lines) \
                or (recent and (names_in(l) | set(re.findall(r"\b[0-9a-f]{7,40}\b", l))) & recent):
            return False
        told_lines.add(key)
        if form:
            told_lines.add("form:" + form)
        if section:
            seen.add(section)
        return True

    for rel in new[:FULL]:
        told.append(rel)
        note = note_report(target, rel, keep)
        body = note[1:]
        if not body:
            continue
        naming = [x["id"] for x in index(target)["sections"] if rel in x.get("names", ())]
        if set(naming) - seen:
            how = "jokbo `file %s`" % rel if "pointer" in told_lines else "`%s \"%s\" file %s`" % (PY, engine_path(), rel)
            told_lines.add("pointer")   # the command is spelled out once; the map's ask line has it too
            body.append("  more: %s — %d document section(s) name it" % (how, len(naming)))
        lines += [note[0]] + body
    st["shown"] = shown + told
    st["told"] = sorted(told_lines)
    st["noted"] = sorted(seen - set(st.get("quoted", [])))
    save(path, st)
    return lines


AGENT_TOOLS = ("Agent", "Task")


AGENT_STALE = 3 * 3600   # an Agent call with no end after this long is taken as ended (the host died, or the hook missed)


def agents_in_flight(target, session_id=None, delta=0, reset=False):
    """How many sub-agent calls (the host's Agent tool) are running in this project, across sessions. While any is, the
    tree moves by the session's own hands — sub-agents often run side by side, and each would tell its sibling's writes
    as another hand's (guin-site, 10-06). Their work becomes the session's when the Agent call ends. A count with no end
    for AGENT_STALE seconds, or a session starting over, clears it, so a missed end cannot silence the change line."""
    path = os.path.join(cache_dir(target), "agents.json")
    st = load(path)
    st = {k: v for k, v in st.items() if isinstance(v, dict) and time.time() - v.get("at", 0) < AGENT_STALE}
    if session_id and (delta or reset):
        sid = str(session_id)
        n = 0 if reset else max(0, int(st.get(sid, {}).get("n", 0)) + delta)
        if n:
            st[sid] = {"n": n, "at": time.time()}
        else:
            st.pop(sid, None)
        save(path, st)
    return sum(int(v.get("n", 0)) for v in st.values())


def host_notification(prompt):
    """Is this prompt the host telling the session something — a background call or sub-agent finished, another session's
    message — rather than a person asking? Its words are a report, not a question: sagoforce 10-26, three sub-agent
    reports got "the project already says" quotes (a git config line, a headless-Chrome command, an upgrade's steps) while
    the person's own three questions got none."""
    head = (prompt or "").lstrip()[:200]
    return bool(re.match(r"<task-notification>|(?:Another Claude session sent a message:\s*)?<agent-message\b", head))


def launched_async(payload):
    """Did this Agent call put its sub-agent in the background? The host answers it at once (`isAsync`, "Async agent
    launched") and says the sub-agent ended in a later `<task-notification>` naming the call. sagoforce 10-26: eight fix
    batches launched so; the count fell to zero at launch, and their edits were told to the session as another hand's six
    times."""
    if (payload.get("tool_input") or {}).get("run_in_background"):
        return True
    r = payload.get("tool_response")
    if isinstance(r, dict) and (r.get("isAsync") or r.get("status") == "async_launched"):
        return True
    return r is not None and "Async agent launched" in json.dumps(r, ensure_ascii=False)


def agent_answered(target, session_id, payload):
    """An Agent call's PostToolUse: its sub-agent's work is now the session's — unless it was launched into the background,
    when it stays in flight until its notification (background_ended)."""
    if launched_async(payload) and payload.get("tool_use_id"):
        st, path = session_state(target, session_id)
        agents = st.get("async-agents") if isinstance(st.get("async-agents"), dict) else {}
        now = time.time()
        agents = {k: v for k, v in agents.items() if isinstance(v, (int, float)) and now - v < AGENT_STALE}
        agents[str(payload["tool_use_id"])] = now
        st["async-agents"] = agents
        save(path, st)
        return
    agents_in_flight(target, session_id, -1)


OWN_WINDOW = 30 * 60   # a plugin the session called is working for it this long after (its children outlive the call)


def engine_called(target, tool_name, tool_input):
    """The plugins with records (the lock's `record-paths`) that this call runs: a shell command naming the engine
    (`…/NAME.py`, `NAME` as a command) or the host's Skill tool on `NAME:…`. Reading their records (`cat .NAME/…`) is not
    running them."""
    names = record_owners(target)
    if not names or not isinstance(tool_input, dict):
        return set()
    if tool_name == "Skill":
        skill = str(tool_input.get("skill") or "")
        return {n for n in names if skill.startswith(n + ":")}
    command = tool_input.get("command")
    if not isinstance(command, str):
        return set()
    out = {n for n in names if re.search(r"(?<![\w.-])%s\.(?:py|mjs|cjs|js|ts|sh)\b" % re.escape(n), command)}
    for argv, _ in commands_of(command):
        out |= {n for n in names if os.path.basename(argv[0]) == n}
    return out


def note_call(target, session_id, payload, ended=False):
    """A call of this session that runs a plugin's engine: that plugin works for the session — from this call until
    OWN_WINDOW after its last such call ended, and, when the call was put in the background (Bash `run_in_background`),
    until the host's notification that it completed (UserPromptSubmit, `<task-notification>` with its tool-use id) or
    AGENT_STALE. guin-site 10-15: the session's backgrounded `chongdae run` and the provider chongdae left running wrote
    the run's task records between calls; told as another hand's at two prompts."""
    plugins = engine_called(target, payload.get("tool_name"), payload.get("tool_input"))
    if not plugins:
        return
    st, path = session_state(target, session_id)
    now = time.time()
    working = st.get("working") if isinstance(st.get("working"), dict) else {}
    working.update({n: now for n in plugins})
    st["working"] = working
    if not ended and (payload.get("tool_input") or {}).get("run_in_background") and payload.get("tool_use_id"):
        bg = st.get("background") if isinstance(st.get("background"), dict) else {}
        bg[str(payload["tool_use_id"])] = {"plugins": sorted(plugins), "at": now}
        st["background"] = bg
    save(path, st)


def own_plugins(st, now=None):
    """The plugins working for this session now (note_call): their record changes are its own."""
    now = now or time.time()
    working = st.get("working") if isinstance(st.get("working"), dict) else {}
    out = {n for n, t in working.items() if isinstance(t, (int, float)) and now - t < OWN_WINDOW}
    for b in (st.get("background") or {}).values():
        if isinstance(b, dict) and now - b.get("at", 0) < AGENT_STALE:
            out |= set(b.get("plugins") or ())
    return out


def background_ended(target, session_id, prompt):
    """A prompt that is the host's notification of the session's background calls completing: they end now, and what
    their plugins write from here is the session's for OWN_WINDOW (the notification's own report has counted them)."""
    ids = set(re.findall(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>", prompt or ""))
    st, path = session_state(target, session_id)
    agents = st.get("async-agents") if isinstance(st.get("async-agents"), dict) else {}
    if ids & set(agents):
        for i in ids & set(agents):   # a sub-agent put in the background ends here, not when its Agent call answered
            agents.pop(i)
            agents_in_flight(target, session_id, -1)
        st["async-agents"] = agents
        save(path, st)
    bg = st.get("background") if isinstance(st.get("background"), dict) else {}
    if not ids or not (ids & set(bg)):
        return
    now = time.time()
    working = st.get("working") if isinstance(st.get("working"), dict) else {}
    for i in ids & set(bg):
        working.update({n: now for n in bg.pop(i).get("plugins") or ()})
    st["working"], st["background"] = working, bg
    save(path, st)


def remember(target, session_id, snap=None, reset=False, keep=()):
    """Keep where the session's last call left the tree (so what changes between its calls is another hand's). reset: the
    session starts over (what it was told is told again), keeping `keep`."""
    st, path = session_state(target, session_id)
    if reset:
        st = {k: st[k] for k in keep if k in st}
    st["last"] = snap or snapshot(target)
    save(path, st)
    return st


def mark_seen(target):
    head = (git(target, "rev-parse", "HEAD") or "").strip()
    if head:
        save(last_seen_path(target), {"head": head, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jokbo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("file", "since", "find", "index", "map", "note"):
        p = sub.add_parser(name)
        p.add_argument("--target", default=".")
        if name == "file":
            p.add_argument("path", help="a file, relative to --target")
        if name == "since":
            p.add_argument("rev", nargs="?", default=None, help="a revision (default: where this machine last looked)")
            p.add_argument("--mark", action="store_true", help="then record HEAD as where this machine last looked")
        if name == "find":
            p.add_argument("terms", nargs="+")
        if name == "note":
            p.add_argument("path", help="a file: the lines the read hook gives at the first look at it")
        if name == "index":
            p.add_argument("--rebuild", action="store_true")
    args = ap.parse_args(argv)
    target = project_root(os.path.abspath(args.target))
    if args.cmd == "file":
        print("\n".join(file_report(target, args.path)))
    elif args.cmd == "since":
        print("\n".join(since_report(target, args.rev)))
        if args.mark:
            mark_seen(target)
    elif args.cmd == "find":
        print("\n".join(find_report(target, args.terms)))
    elif args.cmd == "map":
        print("\n".join(map_report(target)))
    elif args.cmd == "note":
        print("\n".join(note_report(target, args.path)))
    elif args.cmd == "index":
        idx = index(target, rebuild=args.rebuild)
        docs = sorted({s["file"] for s in idx["sections"]})
        print("jokbo index (%s, %s): %d file(s); %d document(s), %d section(s); %d docstring(s)/leading comment(s); %d commit(s); declared reads: %s"
              % (idx["by"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(idx["built"])), len(idx["files"]), len(docs), len(idx["sections"]),
                 len(idx["said"]), len(idx["commits"]), ", ".join("%s(%s)" % (p, ",".join(sorted(k))) for p, k in sorted(declared_reads(target).items())) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
