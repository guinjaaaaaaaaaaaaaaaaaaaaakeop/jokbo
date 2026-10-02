# jokbo

The reader. Records are written faster than anyone reads them, and the next agent has no memory; what fails is not
finding but knowing what to read, when, and how far to trust it. jokbo (족보: the digest of past material handed to
whoever comes next — and a lineage, where a fact came from) answers with where to read, at the moment the question
arises, and says how current each source is. It never decides and never becomes a source.

Measured before it was built (guin-site, five agent sessions): the products' read commands were almost never used, the
net was read only after an edit, and run records were read with `cat`. Retrieval did not fail — the moment, the fit and
the trust did.

## Install

```
claude plugin marketplace add guinjaaaaaaaaaaaaaaaaaaaaakeop/jokbo
claude plugin install jokbo@jokbo
```

Codex: `codex plugin marketplace add guinjaaaaaaaaaaaaaaaaaaaaakeop/jokbo`, `codex plugin add jokbo@jokbo` (the skill
is `$jokbo:read`).

## What it reads

**The base layer — what every project has.** Its documents (Markdown, cut at every heading), its code (as the files
things point at, and its docstrings and leading comments: the only statements code makes in words) and its git history.
A run's records or a net may live outside the project, or not exist; the base layer does not need them.

**The overlay — what the lock declares.** A product's records are read only through a declared read: `reads` in
hunsu.lock.json, or in hunsu.json (the project's own declaration, on top):

```json
"reads": {
  "<plugin>": {
    "file":     ["python3", "{plugin:<plugin>}/<engine>.py", "<command>", "{path}"],
    "since":    ["python3", "{plugin:<plugin>}/<engine>.py", "<command>", "--since", "{since}"],
    "decision": ["python3", "{plugin:<plugin>}/<engine>.py", "<command>"]
  }
}
```

Placeholders: `{plugin:NAME}` (that plugin's root here: a hunsu.local.json link, else its install), `{target}`,
`{path}`, `{since}`. jokbo runs the read and prints its output under the plugin's name; a plugin that is not here, or a
read that fails, is said. jokbo knows no product.

**Ranking** is by shared words (BM25 over identifiers split at case and underscores, and Hangul/CJK words) — the same
answer every time, confirmed by nobody, and every output says so. A section that names the file outright, and documents
changed in the same commits as the file, rank higher. On guin-site, literal file names linked 19% of related
document/code pairs; shared words put a right section in the top three for 83% of files, and found the backend's
contracts for files no relation covered.

**Freshness** is git's: each section's last change (by blame) against the file's. A section written before the file last
changed is marked "the file changed after — read it against the code"; a section with uncommitted lines is "being
edited".

## Commands

`jokbo.py --help` for arguments.

### `/jokbo:file`

Before editing a file: the document sections that speak of it with how current each is, the commits that last changed
it, and what the declared `file` reads say of it. For a document: the *other* documents that say the same things, and
the files it names with when each last changed ("after this document" when one changed since) — its own sections are
open already. A picture or other binary is matched by its name

### `/jokbo:since`

What changed since a revision — by default since this machine last looked: commits, the document sections they changed,
uncommitted work, and the declared `since` reads. `--mark` records HEAD as looked at

### `/jokbo:find`

Where the project speaks of something: document sections, the code's own words, commit subjects, and the lines of the
declared `decision` reads that share the words

`index [--rebuild]` builds the cache and says what it holds; every command refreshes it when the tree moved, and only
for the files that changed (a document is blamed again only when it, or the last commit that touched it, changed).

## Hooks

- **PostToolUse on Read and on Bash — the first look at a file.** When the session first reads a file (Read, or `cat`,
  `head`, `tail`, `sed`, `awk`, `grep`, `rg`, `less`, `nl` on it in a shell — files, never a whole directory), `file` in
  brief. This is the moment before an edit: an agent looks at a file before changing it, and context added to a read
  arrives before its next call is decided.
- **PreToolUse on Edit/Write and on Bash — a file written without being read.** The host delivers a PreToolUse context
  with the tool's result, so it informs the next edit, not this one; it covers what the read hook did not. A shell command
  is read for the files it writes — redirect targets (quotes honoured: a `>` in a commit message is no redirect); the
  operands of mv, rm, touch, tee, `git mv/rm`, `sed -i`, `perl -pi` (a directory stands for its files); a copy's
  destination; what an inline script writes (the first argument of its writing calls, through the names they use).
- Each file is told once per session, by whichever hook comes first; at most two in full per call, the rest named; a
  new file, or one nothing speaks of, says nothing. A declared read is told in brief (ten lines, each cut, about 1.2 KB).
  Paths are read against the shell's directory, which follows `cd`; the cache is the project's, at its root.
- **SessionStart**: what changed since this machine last looked — commits and document sections, in brief; nothing new,
  nothing said. Never a product's state (each product says its own) and never an instruction.
- **Stop**: marks where this machine last looked, when an answer ends — so a resumed session is not told its own commits.

All are silent in a worker session (`AGENT_WORKER=1`), outside a git work tree, and for record paths. Under hunsu's probe
(`HUNSU_SURVEY=1`) the session-start hook answers its standing text and the others say and write nothing. Context only:
never a permission decision, never a block.

Measured on guin-site's two cycles of 10-02 (the session edited only through Bash): of the files each cycle changed, the
write reading found 45/46 and 26/30 (the four missed include two a hired worker wrote), naming 0 and 1 file that was not
changed; 16 of the second cycle's 30 changed files were read before their first edit — told before it. On Claude Code
2.1.287 the context reaches the model after a read and after a Bash edit.

## Settings

`settings.jokbo` in hunsu.json (this machine's hunsu.local.json on top): `skip` — path prefixes that are not documents
of the project, e.g. `["content/"]` for a site's posts.

## Files in your project

| path | committed | what |
|---|---|---|
| `.jokbo/` | never (it ignores itself) | the cache: the index (`index.json`), where this machine last looked (`seen.json`), which files the edit hook already spoke of per session |

## Limits

- Ranking finds shared words, not meaning: a section that speaks of a file only in other words is missed. Whether that
  gap needs embeddings or a wiki is measured in use, not assumed.
- `find` over a declared `decision` read keeps the lines that share a word and their neighbours; the product's own
  command prints the whole.
- Sections are Markdown headings only; other document formats are not read yet.

## Self-check

`python3 test_jokbo.py` (standard library only).
