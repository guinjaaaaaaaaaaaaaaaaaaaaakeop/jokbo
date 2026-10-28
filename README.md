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
    "map":      ["python3", "{plugin:<plugin>}/<engine>.py", "<command>", "--brief"],
    "hands":    ["python3", "{plugin:<plugin>}/<engine>.py", "<command>", "--after", "{after}", "{paths}"]
  }
}
```

Placeholders: `{plugin:NAME}` (that plugin's root here: a hunsu.local.json link, else its install), `{target}`,
`{path}`, `{since}`; for `hands`, `{paths}` (one argument per path) and `{after}` (when the session's last call left the
tree, ISO UTC). A `hands` read prints a JSON object from path to whose hands its records say changed it (`{"src/a.ts":
"the implementer of build (run-…)"}`), for the paths it knows; nothing else of it is shown. jokbo runs the read and prints its output under the plugin's name; a plugin that is not here, or a
read that fails, is said. jokbo knows no product.

**Ranking** is by shared words (BM25 over identifiers split at case and underscores, and Hangul/CJK words) — the same
answer every time, confirmed by nobody, and every output says so. A section that names the file outright, and documents
changed in the same commits as the file, rank higher. A live document comes before one that says at its top it is retired
(below, the map), and every quote from a retired one says so — `— retired document (says so at line 16)`: on guin-site
`find admin` quoted plan/BACKEND.md's admin contract as plainly as the plan that retired it, and 29 first-look notes
quoted it with nothing said (now 4, marked: no live sentence spoke of those files). Retired documents are not dropped —
`find` and `file` keep one when none made the top, as history. Two-letter acronyms (D1, R2) are words to `find`. On guin-site, literal file names linked 19% of related
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

`map` and `note PATH` print what the session-start and read hooks say. `index [--rebuild]` builds the cache and says what it holds; every command refreshes it when the tree moved — by
the files' content, not git's state of them: a second edit to an edited file moves it — and only for the files that
changed (a document is blamed again only when it, or the last commit that touched it, changed; four at a time).

## Hooks — a map, a line, a change

What jokbo puts in front of the agent is small and said once; what it answers is computed when asked. Three guin-site
cycles of pasting ranked section lists and other products' whole output into the context (~130 k characters) changed
nothing the agent did. Other harnesses that get used keep two shapes — something small and curated always present
(Aider's map, Hermes' frozen memory snapshot, Cursor's always-apply rules), and the rest pulled by the agent when the
work relates to it (Devin's knowledge triggers, Cursor's agent-requested rules) — and jokbo follows them.

- **SessionStart — the map.** What this project holds and how to ask, not the content: the documents with their
  sections' headings, line ranges and the date of each one's last change (`10-12`; a lone top heading is the title; a
  long document shows its higher levels and, when they are many, its most recently changed ones; ~40 heading lines and
  about 3,000 characters in all, the oldest headings dropped first — guin-site's ATPROTO.md grew at its end, and the map
  showed its first fourteen sections, not the ones being worked on). The documents come in the order of their last
  change's day, then of how much that change wrote (the lines it left, or the lines being edited) — never of how many
  sections they have: on guin-site a retired BACKEND.md, changed in README.md's rewrite commit, took twelve lines on its
  section count and README.md none. A document that says at its top it no longer holds (its title, or a line before its
  first section starting with retired, deprecated, superseded, obsolete, 은퇴 or 폐기) gets one line saying so and where.
  A long line is cut at an item or a word, a list giving up its last items first (`… +2 ; 3 open question(s)`). This is
  the index a session built itself with `grep -n "^#"` at its first question and then read by `sed -n` ranges (guin-site
  10-08) — a line from each product that declares a `map` read (e.g. the
  net's concepts and open questions), the three questions jokbo answers. A workspace that keeps repositories inside it
  (a directory with its own `.git` that the project's git ignores or leaves untracked) gets one line naming each, where it
  keeps its documents and how many, with the `map --target DIR` that reads them: their documents are not this index's
  (sagoforce 10-26: the map listed a README and a vendored reference; the rules asked about were in the repositories'
  specs). Then, in brief, what changed since this machine
  last looked. Frozen for the session; said again after a compaction (the summary keeps none of it) — without the
  "what changed" part then: a compaction comes mid-answer, and what was committed since is the session's own.
- **UserPromptSubmit — what the project already says.** When a prompt asks about something the documents say, up to
  three of their sentences, quoted, with section and age, before the session reads the prompt — the moment a question
  arises (guin-site 10-08 → 10-11: about 70 prompts, the reader never asked; the session grepped or answered from memory).
  A prompt that is the host's report — `<task-notification>`, another session's `<agent-message>` — asks nothing and gets
  no quotes (sagoforce 10-26: three sub-agent reports were quoted to, the person's own three questions were not).
  Strict, so mostly silent: the sentence shares three of the prompt's words, two of them rare in the documents (in at
  most 15% of the sections), three rare ones or 40% of the prompt's rare words, never under 20% of them. Korean words are
  matched without their particles and one verbal ending (만들어 / 만들기), never cut to one syllable (화면, 로고 are
  nouns); two-letter acronyms (OG, S3) count. Pasted blocks, fenced code, a backquoted command and a command line typed
  inline (a pipe, a $VAR, a --flag) are not the question — a backquoted single name is; talk (지금, 혹시, 어떻게 …) and "ㄱㄱ" say nothing. Each section once per session. On that session's prompts, against
  the tree as it was at each: 2 of 71 answered, both right (an error the owner pasted — the contract's row for it; the
  post types the owner listed — the plan's sentence on them); a question asked in words the documents do not use is missed.
- **PostToolUse — a line at the first look at a file.** When the session first reads a file (Read; cat, head, tail, sed,
  awk, grep, rg, less, nl on it), a line per fact that bears on it: what each product that declares a `note` read says
  (the meaning of the concept it realizes, the last run that touched it, an open finding on it); with none, the one
  document sentence that speaks of it most directly, quoted, with its section and how current it is. A few hundred
  characters. This arrives before the next call is decided — before an edit. A line is told once per session whichever
  file it comes with; a product's line that starts with the file's own path says its standing there (`<file>: not
  registered — …`), the same for every file in that state, and is told once per session in that form (guin-site 10-15:
  with every file the session opened, never acted on) — and it does not stand in for the document sentence, which still
  comes; a line naming something committed since the session started (a commit's hash, a name with four
  digits or more that is not a date, such as a run id) is left out — it
  is the session's own work, or the change line said it; and a file the documents name outright gets one pointer to
  `file` (the command spelled out the first time), unless the sections that name it are the ones the session was quoted.
  On guin-site's 10-08 → 10-11 notes: 107 lines → 43.
- **PostToolUse — a search in the shell is answered.** `git log … [--] PATH`, `git blame PATH` (git's own options such
  as `-C DIR` skipped; the path taken from where the command ran — `cd`, `-C`, the shell's directory), a recursive grep or
  rg over a path that holds documents — the project root, `plan/`, a document named, `--include=*.md`; a path counts
  only inside the project, and not when it holds what jokbo cannot expand: `$M/README.md`, `$(…)`, backticks; guin-site
  10-15: a grep of a plugin's README was answered with the project's own sections — (quotes read
  before pipes: `grep -E "a|b"` is one pattern); a grep through code only (`src/`) is a code search and is not answered
  (guin-site 10-12: eleven such answers, none cited), the host's Grep tool: what the documents say of that file, or where the project speaks of
  those words (two sections, quoted, and for words the commit whose subject says most) — once per target per session,
  two per command. A command that cannot be a search loads nothing. A grep after a pipe filters output and searches nothing; a pattern with no words (`at-31`) is left
  to grep. (guin-site 10-10: `git log -- wrangler.worker.build.jsonc` to learn why the file exists; the plan's sentence
  and the commit that say so come back.)
- **PreToolUse — a file written without being read** gets the same line (it arrives with the write's result).
- **Changes by another hand.** What the session does inside its own calls is its own. What changes between them — the
  owner, another session, a worker — is said once, in a line, at the next prompt (UserPromptSubmit) or call: commits,
  and paths grouped by whose records they are (the lock's `record-paths`), documents, and the rest. A plugin whose engine
  the session's call runs (`…/NAME.py`, `NAME` as a command, the Skill `NAME:…`) works for the session: its records, and
  a commit that writes them, are the session's own from that call until 30 minutes after its last such call ended — and,
  for a call put in the background, until the host notifies its completion (guin-site 10-15: the session's backgrounded
  `chongdae run`, and the provider it left running, wrote the run's task records; told as another hand's at two prompts).
  A hired worker's edits to the code are still said — the session did not write them — and, when a product declares a
  `hands` read that names them, as whose: `changed by the implementer of cache-build (run-20261015-054352-a0ec):
  src/worker/index.ts, src/worker/read.ts` (guin-site 10-15 05:53:57, told as `files:` — the session's own hired builder);
  a path no read names stays another hand's. Another session running the same plugin within that window is not said.
- **Stop** marks where this machine last looked and where the session's calls left the tree.
- A call that fails (PostToolUseFailure) is still the session's own: what it wrote is not told back as another hand's.
  While a sub-agent runs (the host's Agent tool), the change line is silent — siblings' writes are the session's hands,
  and what they did becomes the session's when the Agent call ends. A sub-agent the host launched into the background
  (its call answers at once, `isAsync`) runs until the `<task-notification>` naming the call (sagoforce 10-26: eight fix
  batches launched so, and their edits were told to the session as another hand's six times).

Each file once per session; a restart, or a compaction, starts over (a compaction keeps where the session started).
Hook cost on guin-site (73 sections, 500 commits): a prompt ~0.1 s, a search answer 0.1–0.5 s; on 4,868 sections the
prompt match 0.44 s (a section's match keys are kept in the cache). An older cache is brought up to date without blame — but 1.7.0's, whose sections' last change stopped at the last committed line, is blamed once again. Paths follow the shell's directory; the cache is the project's, at
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
| `.jokbo/` | never (it ignores itself) | the cache: the index (`index.json`), where this machine last looked (`seen.json`), per session where it started and what it was told (files, lines, quoted sections, answered searches) |

## Limits

- Ranking finds shared words, not meaning: a section that speaks of a file only in other words is missed — and a
  question asked in other words than the documents' ("why does the site rebuild pages?" against "읽기 경로 — 페이지를
  어떻게 만드나") gets nothing at the prompt. Whether that
  gap needs embeddings or a wiki is measured in use, not assumed.
- `find` over a declared `decision` read keeps the lines that share a word and their neighbours; the product's own
  command prints the whole.
- Sections are Markdown headings only; other document formats are not read yet.

## Self-check

`python3 test_jokbo.py` (standard library only).

Parallel hooks serialize updates to the shared sub-agent count and each session’s state. Atomic file replacement
keeps readers from seeing partial JSON; the locks also keep simultaneous launches, completions and async call IDs
from overwriting one another. Compaction retains the outstanding async calls until their notifications arrive.
