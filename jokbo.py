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
lock declares (`reads` in hunsu.lock.json, or hunsu.json: {plugin: {kind: argv}}, kinds `file`, `since`, `decision`, with
`{plugin:NAME}`, `{target}`, `{path}`, `{since}`) — jokbo knows no product. Ranking is by shared words (BM25), the same
answer every time, confirmed by nobody: it says so. Freshness is git's: a section written before the file last changed is
marked. jokbo never decides and never becomes a source: `.jokbo/` is a cache (ignored, rebuilt), never committed.
"""
import argparse
import collections
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
INDEX_FORMAT = 5
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
        for m in set(re.findall(r"\{plugin:([\w.-]+)\}", a)):
            root = plugin_root(target, m)
            if not root:
                return None, "plugin %s is not installed or linked here" % m
            a = a.replace("{plugin:%s}" % m, root.replace(os.sep, "/"))
        for key in ("target", "path", "since"):
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


def overlay(target, kind, values):
    """[(plugin, text | None, why | None)] for every plugin that declares this kind of read."""
    return [(p, *run_read(target, p, kinds[kind], dict(values, target=target))) for p, kinds in sorted(declared_reads(target).items()) if kind in kinds]


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
            cur = {"file": path, "heading": h.group(2).strip(), "start": i, "lines": [line]}
        else:
            if cur is None:
                cur = {"file": path, "heading": "", "start": 1, "lines": []}
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
    return hashlib.sha1(("%s\n%s\n%s\n%d" % (version(), head, status, INDEX_FORMAT)).encode("utf-8")).hexdigest()


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


def build_index(target, previous=None):
    """The cache: sections of every document (with each one's last change, by blame), the code's own words, recent
    commits. A file whose key is unchanged keeps its previous entry — blame runs only for documents that changed, so an
    edit to one file costs one file's work, not the project's."""
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
    entries = {}
    for f in files:
        is_doc, is_code = f.lower().endswith(DOC_EXT), f.endswith(CODE_EXT)
        if not (is_doc or is_code):
            continue
        key = content_key(target, f, last.get(f), f in dirty) + (" " + names_key if is_doc else "")
        if old.get(f, {}).get("key") == key:
            entries[f] = old[f]
            continue
        text = read_text(target, f)
        if is_doc:
            times = blame_times(target, f) if f not in dirty or last.get(f) else {}
            end = max(times) if times else 0
            secs = []
            for sec in sections_of(f, text):
                lt = [times.get(i) for i in range(sec["start"], min(sec["end"], end) + 1)]
                sec["changed"] = None if f in dirty and any(t is None for t in lt) else max([t for t in lt if t] or [0]) or None
                sec["words"] = words(sec["heading"] + " " + sec["text"])
                # the files it names outright — by path, or by a file name only one file has
                sec["names"] = sorted(g for g in files if g != f and (g in sec["text"] or (base[os.path.basename(g)] == 1 and len(os.path.basename(g)) > 4
                                      and re.search(r"(?<![\w/.-])" + re.escape(os.path.basename(g)) + r"(?![\w-])", sec["text"]))))
                del sec["text"]   # read from the file when shown: the cache keeps positions, the document keeps its words
                secs.append(sec)
            entries[f] = {"key": key, "sections": secs}
        else:
            said = code_words(f, text)
            for w in said:
                w["words"] = words(w["text"])
            entries[f] = {"key": key, "said": said}
    return {"format": INDEX_FORMAT, "key": tree_key(target), "built": int(time.time()), "by": "jokbo %s" % version(),
            "files": files, "entries": entries, "commits": commits}


def index(target, rebuild=False):
    path = os.path.join(cache_dir(target), "index.json")
    idx = load(path)
    if rebuild or idx.get("format") != INDEX_FORMAT or idx.get("key") != tree_key(target):
        idx = build_index(target, None if rebuild or idx.get("format") != INDEX_FORMAT else idx)
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

def rank_sections(idx, query, about=None, top=SHOW):
    secs = idx["sections"]
    if about and about.lower().endswith(DOC_EXT):
        secs = [s for s in secs if s["file"] != about]   # its own sections are what is being edited: the reader has them open
    if not secs or not query:
        return []
    r = Ranker([s["words"] for s in secs])
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
        scored.append((sc, i))
    scored.sort(reverse=True)
    return [secs[i] for _, i in scored[:top]]


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
    secs = rank_sections(idx, query, about=path)
    head = "jokbo — %s" % path + ("" if exists else " (not in the tree: ranked by its name only)")
    lines.append(head)
    if secs:
        lines.append(("other documents that say the same things" if doc else "documents that speak of it")
                     + " (ranked by shared words — nobody confirmed these; open the ones that matter):")
        for s in secs:
            fresh = ("written %s" % stamp(s["changed"]) if s.get("changed") else "being edited (uncommitted)") if doc else freshness(s, changed)
            lines.append("  %s  (lines %d-%d) — %s" % (s["id"], s["start"], s["end"], fresh))
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
                l = l if len(l) <= BRIEF_WIDTH else l[:BRIEF_WIDTH] + " …"
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
            for sid in (changed[:6] if brief else changed):
                lines.append("  " + sid)
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
    for block in re.split(r"\n\s*\n|\n(?=\s*(?:[-*+]|\d+\.)\s)", text):
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
        shared = q & set(words(sent))
        if len(shared) < least:
            continue
        sc = sum(math.log(1 + n / (1 + df[t])) for t in shared)
        if sc > score:
            best, score = sent, sc
    if best and len(best) > QUOTE:
        best = best[:QUOTE].rstrip() + " …"
    return best


def find_report(target, terms):
    """Where the project speaks of something — each section with the sentence that says it, the code's own words, the
    commits, and the lines of the declared `decision` reads that share the words. An answer to read, not a list to open."""
    idx = index(target)
    q = words(" ".join(terms))
    lines = ["jokbo — where the project speaks of: %s (ranked by shared words, confirmed by nobody)" % " ".join(terms)]
    secs = rank_sections(idx, q, top=6)
    lines.append("documents:" if secs else "documents: nothing shares these words")
    for s in secs:
        lines.append("  %s  (lines %d-%d, written %s)" % (s["id"], s["start"], s["end"], when(s.get("changed"))))
        said_here = quote(target, idx, s, q, least=1)
        if said_here:
            lines.append("    \u201c%s\u201d" % said_here)
    said = rank_items(idx["said"], q, 4)
    if said:
        lines.append("the code's own words:")
        for w in said:
            first = w["text"].strip().split("\n")[0]
            lines.append("  %s:%d — %s" % (w["file"], w["line"], first))
    commits = [dict(c, words=words(c["subject"])) for c in idx["commits"]]
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


def map_report(target):
    """What this project holds and how to ask — not the content. Said once at session start and frozen for the session (a
    map rewritten mid-session breaks the prompt cache and says things twice): the documents and how many sections each
    has, a line from each product that declares a `map` read, and the three questions jokbo answers."""
    idx = index(target)
    per_doc = collections.Counter(s["file"] for s in idx["sections"])
    lines = ["jokbo — what this project holds, and how to ask:"]
    if per_doc:
        docs = ["%s (%d)" % (f, n) for f, n in per_doc.most_common(6)]
        more = len(per_doc) - len(docs)
        lines.append("  documents (sections): %s%s" % (", ".join(docs), ", +%d more" % more if more > 0 else ""))
    for plugin, text, why in overlay(target, "map", {}):
        if text and text.strip():
            first = [l.strip() for l in text.strip().split("\n") if l.strip()][:2]
            lines.append("  %s: %s" % (plugin, " · ".join(l if len(l) <= 240 else l[:240] + " …" for l in first)))
    lines.append("  ask: `%s \"%s\" find WORDS` — what was decided and where it is said, quoted; `since [REV]` — what changed; "
                 "`file PATH` — all that stands on a file" % (PY, engine_path()))
    return lines


def note_report(target, path):
    """A line or two per fact that bears on a file, at the session's first look at it: what each product that declares a
    `note` read says of it (the meaning of the concept it realizes, the last run that touched it, an open finding); with
    none saying anything, the one document sentence that speaks of it most directly, with its section and how current it
    is. [] when nothing does — a hook says nothing rather than "nothing"."""
    path = relpath(target, path)
    lines = []
    for plugin, text, why in overlay(target, "note", {"path": path}):
        if text and text.strip():
            for l in [l.strip() for l in text.strip().split("\n") if l.strip()][:NOTE_LINES]:
                lines.append("  %s: %s" % (plugin, l if len(l) <= NOTE_WIDTH else l[:NOTE_WIDTH] + " …"))
    if not lines:
        idx = index(target)
        exists = os.path.isfile(os.path.join(target, path))
        query = words(read_text(target, path) + " " + path) if exists and is_text(target, path) else words(path)
        top = rank_sections(idx, query, about=path, top=1)
        said = quote(target, idx, top[0], query, least=3) if top else None
        if said:
            s = top[0]
            fresh = ("written %s" % stamp(s["changed"]) if s.get("changed") else "being edited") if path.lower().endswith(DOC_EXT) \
                else freshness(s, file_changed(target, path) if exists else None)
            lines.append("  %s (%s): \u201c%s\u201d" % (s["id"], fresh, said))
    return ["jokbo — %s:" % path] + lines if lines else []


def session_state(target, session_id):
    sid = re.sub(r"[^\w.-]", "_", str(session_id or "no-session"))
    path = os.path.join(cache_dir(target), "sessions", sid + ".json")
    st = load(path, default={})
    return (st if isinstance(st, dict) else {}), path   # a list was 1.1/1.2's shown-files: a new format starts over


def snapshot(target):
    """The tree as a session sees it between its calls: HEAD, and every path that differs from it with its status."""
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
    return {"head": head, "status": status}


def change_report(target, old, new):
    """What changed between two snapshots that this session did not do — another session, a hired worker, the owner:
    commits, and the paths changed, grouped by whose records they are (the lock's `record-paths`; jokbo names no
    product), documents, and the rest. [] when nothing did."""
    if not old or old == new:
        return []
    paths = {p for p in set(old.get("status", {})) | set(new.get("status", {})) if old.get("status", {}).get(p) != new.get("status", {}).get(p)}
    commits = []
    if old.get("head") and new.get("head") and old["head"] != new["head"]:
        log = git(target, "log", "--format=%h %s", "%s..%s" % (old["head"], new["head"]), "--") or ""
        commits = [l for l in log.splitlines() if l.strip()]
        names = git(target, "diff", "--name-only", "-z", old["head"], new["head"], "--") or ""
        paths |= {p for p in names.split("\0") if p}
    paths = {p for p in paths if not p.startswith((CACHE + "/", ".git/"))}
    if not commits and not paths:
        return []
    _, lock = manifest_and_lock(target)
    owners = lock.get("record-paths") if isinstance(lock.get("record-paths"), dict) else {}
    groups = collections.OrderedDict()
    for p in sorted(paths):
        who = next((plugin for plugin, ps in sorted(owners.items()) for q in ps if p.startswith(q) or p == q.rstrip("/")), None)
        key = ("%s's records" % who) if who else ("documents" if p.lower().endswith(DOC_EXT) else "files")
        groups.setdefault(key, []).append(p)
    lines = ["jokbo — changed by another hand since this session's last call:"]
    if commits:
        lines.append("  %d commit(s): %s%s" % (len(commits), "; ".join(commits[:3]), " …" if len(commits) > 3 else ""))
    for key, ps in groups.items():
        lines.append("  %s: %s%s" % (key, ", ".join(ps[:4]), " +%d more" % (len(ps) - 4) if len(ps) > 4 else ""))
    return lines


def tell(target, rels, session_id, why):
    """What a hook says about files the session reads or writes: each file once per session (whichever hook comes first),
    at most FULL told, the rest named; a note, not a report (see note_report)."""
    st, path = session_state(target, session_id)
    shown = st.get("shown", [])
    rp = record_paths(target) + (".git/",)
    new = [r for r in rels if r not in shown and not r.startswith(rp) and os.path.isfile(os.path.join(target, r))]
    if not new:
        return []
    lines, told = [], []
    for rel in new[:FULL]:
        lines.extend(note_report(target, rel))
        told.append(rel)
    st["shown"] = shown + told
    save(path, st)
    return lines


def remember(target, session_id, snap=None, reset=False):
    """Keep where the session's last call left the tree (so what changes between its calls is another hand's)."""
    st, path = session_state(target, session_id)
    if reset:
        st = {}
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
