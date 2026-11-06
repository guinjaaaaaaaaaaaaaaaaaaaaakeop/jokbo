# jokbo

The reader. Records are written faster than anyone reads them, and the next agent has no memory; what fails is not
finding but knowing what to read, when, and how far to trust it. jokbo (족보: the digest of past material handed to
whoever comes next — and a lineage, where a fact came from) answers with where to read, at the moment the question
arises, and says how current each source is. It never decides and never becomes a source.

Navigation: [Install](#install) · [Developing jokbo](#developing-jokbo) ·
[Shared guidance](#shared-claude-and-codex-guidance) · [Versioning](#versioning) · [Self-check](#self-check)

## Install

Python **3.11 or later** is required on both hosts. Ensure `python3` on the host's PATH meets this floor;
the engine, command adapters and hooks use that executable.

```
claude plugin marketplace add guinjaaaaaaaaaaaaaaaaaaaaakeop/jokbo
claude plugin install jokbo@jokbo
```

Codex: `codex plugin marketplace add guinjaaaaaaaaaaaaaaaaaaaaakeop/jokbo`, `codex plugin add jokbo@jokbo` (the skill
is `$jokbo:read`).

Marketplace installation needs no clone. Both marketplaces install the `plugin/` directory.
Its [README](plugin/README.md) documents the reader's behavior, commands, hooks, settings and limits.

## Developing jokbo

Cloning is for working on jokbo itself. `plugin/` is the single runtime and the whole installable payload:
`plugin/jokbo.py`, hooks, commands, skills, the three manifests, README and LICENSE.
The root holds the tests, AGENTS.md and CLAUDE.md, the development environment `hunsu.json`,
`hunsu.lock.json` and `hunsu-judgments.json`, and the run and review records.
The root catalogs `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json` select `./plugin`.

`python3 jokbo.py --help` and `import jokbo` at the root go through the root `jokbo.py` alias, which runs or imports
`plugin/jokbo.py` itself (one module, not a copy) and is not installed. Run the tests in [Self-check](#self-check) from the root.

To try the working payload in a host, a local marketplace added from the checkout installs `./plugin`.
A hunsu link or `dev` trial names that marketplace directory (the checkout), and hunsu follows its catalog
to `./plugin`, as jokbo's own `{plugin:NAME}` does. The development environment and its preparation are in [AGENTS.md](AGENTS.md).

## Shared Claude and Codex guidance

[AGENTS.md](AGENTS.md) is the common guide for maintaining this source checkout. [CLAUDE.md](CLAUDE.md)
imports that file for Claude compatibility; edit the shared file when a maintenance rule changes.
Installing this plugin does not automatically load its source-checkout `AGENTS.md` into the consuming project.

The installed workflow for both hosts is [plugin/skills/read/SKILL.md](plugin/skills/read/SKILL.md).

## Versioning

Semver, and a version names one content: changes to code, skills, commands, hooks or documentation
(including README.md, AGENTS.md and CLAUDE.md) require a version bump before distribution or installation.
**patch**: fixes or wording changes with existing interfaces unchanged. **minor**: an additive command,
setting, hook or output field that preserves existing consumers. **major**: incompatible changes to CLI,
output or declared read contracts consumed by projects or other tools, and dropping a supported Python or host version
— raising `engines.python` in the manifests, or no longer supporting a Claude Code or Codex version that a published
release supported. The disposable `.jokbo/` cache
is not a public record contract; rebuild or migrate it when its internal format changes.

2.0.0 is such a major release: it raised the Python floor of `plugin.json` and `.claude-plugin/plugin.json`
from 3.9 to 3.11, the floor `.codex-plugin/plugin.json` had declared since 1.0.0, dropping Python 3.9 and 3.10
(those manifests were then at the repository root).

2.1.0 is minor: it moved the installable payload into `plugin/` (catalogs select `./plugin`) with the
installed interfaces unchanged.

2.2.0 is minor: `{plugin:NAME}` in a declared read is resolved by the plugin family's host-aware resolver, vendored
byte-identical as `plugin/pluginroot.py` — the host the process runs on (Codex when `AGENT_HOST` is `codex`, else
Claude Code) and the marketplace hunsu.json declares, never another host's copy or another marketplace's plugin of that
name — and a `file`, `since` or `decision` read says, in a line of its own, when the copy it ran differs from
hunsu.json's pinned version. A read whose plugin is installed only for the other host, or only from a marketplace other
than the declared one, is not run and says why; 2.1.0 ran whatever install of that name Claude Code's records held.

### Release and adoption

Documentation and shared instruction edits also need a patch bump when interfaces stay unchanged.
Keep the version equal in `plugin/plugin.json`, `plugin/.claude-plugin/plugin.json`, `plugin/.codex-plugin/plugin.json`,
and `.claude-plugin/marketplace.json` (both `metadata.version` and this product's `plugins[].version`).
`.agents/plugins/marketplace.json` currently has no version field; verify its product name and source path,
and verify both catalogs select `./plugin`; do not invent a version field there.

Before publication, run the relevant checks in **Self-check** for behavior changes; for documentation-only
changes, verify links, command examples, imports and manifest consistency, plus `git diff --check`.
Publish the checked commit using this repository's existing release convention. Publication does not update
consumer installations: update the plugin through the host's supported workflow, reconcile the consuming
project's declared environment and lock if managed, and start a new host session to apply the installation.
That reconciliation, and checking that the environment is compatible (worker hosts, reporters, coherence,
plugin fingerprints), belongs to the consuming project's environment manager and its workflow. jokbo is not
that manager: it reads and indexes the sources the project declares — documents, history, and the `reads` in
its lock or `hunsu.json` — and says how current each is; it cannot tell whether an environment matches its lock.
Report publication, project adoption and live-host verification separately. Refreshing a locally linked
plugin is not an update procedure for every remote installation.

## Self-check

Run `python3 test_jokbo.py` and `python3 -m unittest test_packaging.py` from the root (standard library only).
The packaging test copies only `plugin/` to a temporary directory outside the repository and runs the CLI
and hooks there on a fixture.

Parallel hooks serialize updates to the shared sub-agent count and each session’s state. Atomic file replacement
keeps readers from seeing partial JSON; the locks also keep simultaneous launches, completions and async call IDs
from overwriting one another. Compaction retains the outstanding async calls until their notifications arrive.
