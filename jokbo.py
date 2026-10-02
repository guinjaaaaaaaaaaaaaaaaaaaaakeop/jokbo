"""jokbo — the reader of the LLM-development era: when a question arises, where to read, and how current each source is.

  file PATH [--target DIR]        before editing a file: the document sections that speak of it (ranked by shared words),
                                  the commits that last changed it, and what the declared reads say of it
  since [REV] [--target DIR]      what changed since REV (default: since this machine last looked): commits, document
                                  sections changed, uncommitted work, and what the declared reads say since then
  find WORDS... [--target DIR]    where the project speaks of something: document sections, the code's own words
                                  (docstrings, leading comments), commit subjects, and the declared `decision` reads
  index [--target DIR]            build the cache now and say what it holds (every other command builds it when stale)

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
INDEX_FORMAT = 2
PY = "python3" if shutil.which("python3") else "python"
DOC_EXT = (".md", ".markdown")
CODE_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".astro", ".vue", ".svelte", ".css", ".scss", ".html", ".sql",
            ".sh", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".h", ".cpp", ".swift", ".toml", ".yaml", ".yml", ".graphql",
            ".prisma", ".proto")
STOP = {"src", "lib", "index", "the", "and", "for", "with", "this", "that", "from", "return", "const", "function", "import",
        "export", "default", "true", "false", "null", "none", "self", "def", "class", "let", "var", "new", "async", "await",
        "test", "tests", "not", "are", "was", "but", "else", "elif", "then", "string", "number", "type", "json", "html", "css",
        "assets", "public", "components", "pages", "scripts", "mjs", "md"}
SHOW = 3             # sections put in front of an edit


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
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def git(target, *a):
    try:
        done = subprocess.run(["git", *a], cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


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
        done = subprocess.run(out, cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              env=dict(os.environ, AGENT_WORKER=os.environ.get("AGENT_WORKER", "")))
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
    out = git(target, "ls-files", "--cached", "--others", "--exclude-standard", "--", ".")
    if out is None:
        return None
    rp = record_paths(target) + tuple(p for p in settings(target).get("skip") or [] if isinstance(p, str) and p)
    return sorted({l.strip() for l in out.splitlines() if l.strip() and not l.strip().startswith(rp) and os.path.isfile(os.path.join(target, l.strip()))})


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
    return hashlib.sha1(("%s\n%s\n%s\n%d" % (version(), head, status, INDEX_FORMAT)).encode("utf-8")).hexdigest()


def build_index(target):
    files = listed_files(target)
    if files is None:
        raise SystemExit("jokbo: %s is not a git work tree — the base layer is its documents, code and history" % target)
    sections, said = [], []
    for f in files:
        if f.lower().endswith(DOC_EXT):
            text = read_text(target, f)
            times = blame_times(target, f)
            last = max(times) if times else 0
            dirty_doc = bool((git(target, "status", "--porcelain", "--", f) or "").strip())
            for s in sections_of(f, text):
                lt = [times.get(i) for i in range(s["start"], min(s["end"], last) + 1)]
                s["changed"] = None if dirty_doc and any(t is None for t in lt) else max([t for t in lt if t] or [0]) or None
                s["words"] = words(s["heading"] + " " + s["text"])
                sections.append(s)
        elif f.endswith(CODE_EXT):
            for w in code_words(f, read_text(target, f)):
                w["words"] = words(w["text"])
                said.append(w)
    base = collections.Counter(os.path.basename(f) for f in files)
    for s in sections:
        # the files it names outright — by path, or by a file name only one file has
        s["names"] = sorted(f for f in files if f in s["text"] or (base[os.path.basename(f)] == 1 and len(os.path.basename(f)) > 4
                                                                   and re.search(r"(?<![\w/.-])" + re.escape(os.path.basename(f)) + r"(?![\w-])", s["text"])))
        del s["text"]   # read from the file when shown: the cache keeps positions, the document keeps its words
    return {"format": INDEX_FORMAT, "key": tree_key(target), "built": int(time.time()), "by": "jokbo %s" % version(),
            "files": files, "sections": sections, "said": said, "commits": recent_commits(target)}


def index(target, rebuild=False):
    path = os.path.join(cache_dir(target), "index.json")
    idx = load(path)
    if rebuild or idx.get("format") != INDEX_FORMAT or idx.get("key") != tree_key(target):
        idx = build_index(target)
        save(path, idx)
    return idx


def section_text(target, s):
    lines = read_text(target, s["file"]).split("\n")
    return "\n".join(lines[s["start"] - 1:s["end"]])


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


# ---------------------------------------------------------------- commands

def relpath(target, path):
    p = os.path.relpath(os.path.abspath(path), os.path.abspath(target)) if os.path.isabs(path) else os.path.normpath(path)
    return p.replace(os.sep, "/")


def file_report(target, path, brief=False):
    """The lines for one file. brief: what the edit hook injects (overlay output cut, with the command that prints it whole)."""
    idx = index(target)
    path = relpath(target, path)
    lines = []
    exists = os.path.isfile(os.path.join(target, path))
    changed = file_changed(target, path) if exists else None
    query = words(read_text(target, path) + " " + path) if exists else words(path)
    secs = rank_sections(idx, query, about=path)
    head = "jokbo — %s" % path + ("" if exists else " (not in the tree: ranked by its name only)")
    lines.append(head)
    if secs:
        lines.append("documents that speak of it (ranked by shared words — nobody confirmed these; open the ones that matter):")
        for s in secs:
            lines.append("  %s  (lines %d-%d) — %s" % (s["id"], s["start"], s["end"], freshness(s, changed)))
    else:
        lines.append("documents: no section shares its words")
    hist = [c for c in idx["commits"] if path in c["files"]][:2 if brief else 5]
    if hist:
        lines.append("last changed by:")
        for c in hist:
            lines.append("  %s %s %s — %s" % (when(c["at"]), c["sha"][:7], c["author"], c["subject"]))
    for plugin, text, why in overlay(target, "file", {"path": path}):
        if why:
            lines.append("%s (declared `file` read): not read — %s" % (plugin, why))
            continue
        body = text.split("\n")
        if brief and len(body) > 12:
            body = body[:12] + ["  … %d more line(s) — `%s \"%s\" file %s` prints them all" % (len(body) - 12, PY, os.path.join(HERE, "jokbo.py").replace(os.sep, "/"), path)]
        lines.append("%s (declared `file` read):" % plugin)
        lines.extend("  " + l for l in body)
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
            lines.append("%s (declared `since` read):" % plugin if not why else "%s (declared `since` read): not read — %s" % (plugin, why))
            if text:
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


def find_report(target, terms):
    idx = index(target)
    q = words(" ".join(terms))
    lines = ["jokbo — where the project speaks of: %s (ranked by shared words, confirmed by nobody)" % " ".join(terms)]
    secs = rank_sections(idx, q, top=6)
    lines.append("documents:" if secs else "documents: nothing shares these words")
    for s in secs:
        lines.append("  %s  (lines %d-%d) — written %s" % (s["id"], s["start"], s["end"], when(s.get("changed"))))
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


def mark_seen(target):
    head = (git(target, "rev-parse", "HEAD") or "").strip()
    if head:
        save(last_seen_path(target), {"head": head, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jokbo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("file", "since", "find", "index"):
        p = sub.add_parser(name)
        p.add_argument("--target", default=".")
        if name == "file":
            p.add_argument("path", help="a file, relative to --target")
        if name == "since":
            p.add_argument("rev", nargs="?", default=None, help="a revision (default: where this machine last looked)")
            p.add_argument("--mark", action="store_true", help="then record HEAD as where this machine last looked")
        if name == "find":
            p.add_argument("terms", nargs="+")
        if name == "index":
            p.add_argument("--rebuild", action="store_true")
    args = ap.parse_args(argv)
    target = os.path.abspath(args.target)
    if args.cmd == "file":
        print("\n".join(file_report(target, args.path)))
    elif args.cmd == "since":
        print("\n".join(since_report(target, args.rev)))
        if args.mark:
            mark_seen(target)
    elif args.cmd == "find":
        print("\n".join(find_report(target, args.terms)))
    elif args.cmd == "index":
        idx = index(target, rebuild=args.rebuild)
        docs = sorted({s["file"] for s in idx["sections"]})
        print("jokbo index (%s, %s): %d file(s); %d document(s), %d section(s); %d docstring(s)/leading comment(s); %d commit(s); declared reads: %s"
              % (idx["by"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(idx["built"])), len(idx["files"]), len(docs), len(idx["sections"]),
                 len(idx["said"]), len(idx["commits"]), ", ".join("%s(%s)" % (p, ",".join(sorted(k))) for p, k in sorted(declared_reads(target).items())) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
