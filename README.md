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
    "decision": ["python3", "{plugin:<plugin>}/<engine>.py", "<command>"],
    "note":     ["python3", "{plugin:<plugin>}/<engine>.py", "<command>", "{path}", "--brief"],
    "map":      ["python3", "{plugin:<plugin>}/<engine>.py", "<command>", "--brief"]
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

`map` and `note PATH` print what the session-start and read hooks say. `index [--rebuild]` builds the cache and says what it holds; every command refreshes it when the tree moved, and only
for the files that changed (a document is blamed again only when it, or the last commit that touched it, changed).

## Hooks — a map, a line, a change

What jokbo puts in front of the agent is small and said once; what it answers is computed when asked. Three guin-site
cycles of pasting ranked section lists and other products' whole output into the context (~130 k characters) changed
nothing the agent did. Other harnesses that get used keep two shapes — something small and curated always present
(Aider's map, Hermes' frozen memory snapshot, Cursor's always-apply rules), and the rest pulled by the agent when the
work relates to it (Devin's knowledge triggers, Cursor's agent-requested rules) — and jokbo follows them.

- **SessionStart — the map.** What this project holds and how to ask, not the content: the documents and their section
  counts, a line from each product that declares a `map` read (e.g. the net's concepts and open questions), the three
  questions jokbo answers. Then, in brief, what changed since this machine last looked. Frozen for the session.
- **PostToolUse — a line at the first look at a file.** When the session first reads a file (Read; cat, head, tail, sed,
  awk, grep, rg, less, nl on it), a line per fact that bears on it: what each product that declares a `note` read says
  (the meaning of the concept it realizes, the last run that touched it, an open finding on it); with none, the one
  document sentence that speaks of it most directly, quoted, with its section and how current it is. A few hundred
  characters. This arrives before the next call is decided — before an edit.
- **PreToolUse — a file written without being read** gets the same line (it arrives with the write's result).
- **Changes by another hand.** What the session does inside its own calls is its own. What changes between them — the
  owner, another session, a worker — is said once, in a line, at the next prompt (UserPromptSubmit) or call: commits,
  and paths grouped by whose records they are (the lock's `record-paths`), documents, and the rest.
- **Stop** marks where this machine last looked and where the session's calls left the tree.

Each file once per session; a restart starts over. Paths follow the shell's directory; the cache is the project's, at
its root. All silent in a worker session (`AGENT_WORKER=1`) and outside a git work tree; under hunsu's probe
(`HUNSU_SURVEY=1`) the session-start hook answers its standing text and nothing is written. Context only: never a
permission decision, never a block.

A shell command is read for the files it writes and reads (`shell_files`): redirect targets (quotes honoured); operands
of mv, rm, touch, tee, `git mv/rm`, `sed -i`, `perl -pi` (a directory stands for its files); a copy's destination; the
first argument of an inline script's writing calls, through the names they use. Measured on guin-site's 10-02 cycles:
45/46 and 26/30 of the changed files found, naming 0 and 1 not changed.

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
