# Working on jokbo

This is the shared source-maintenance guide for Claude and Codex. Read [README.md](README.md) for the
repository and [plugin/README.md](plugin/README.md) for the reader's behavior, hooks and limits.
Installed operation is defined in [plugin/skills/read/SKILL.md](plugin/skills/read/SKILL.md);
this checkout guide is not injected into a consumer's project by plugin installation.

- Keep retrieval and cache behavior in `plugin/jokbo.py`, the single runtime; root `jokbo.py` is only a shim.
  Nothing in `plugin/` imports from, links to or reads the repository around it. The reader supplies source text,
  provenance and freshness; its ranking does not decide which source is true. Preserve those distinctions.
- Read other products through declared `reads` argv. Do not import sibling plugins or hardcode their
  internal records. This repository must work as an independent checkout.
- Keep `plugin/skills/` common to both hosts. `plugin/commands/`, `plugin/hooks/` and the host manifests under
  `plugin/` adapt entry points to the host; do not duplicate the workflow. `CLAUDE.md` only imports this file.
- `.jokbo/` is disposable local cache, not a committed source. Preserve atomic writes and locking for
  shared session/sub-agent state, including outstanding asynchronous calls across compaction.
- From the root run `python3 test_jokbo.py` and `python3 -m unittest test_packaging.py` (standard library only).
  Use temporary projects for experiments rather than changing a consumer's records.
- For documentation-only changes, verify links and CLI examples against `python3 jokbo.py --help`.
  Keep the five version fields consistent per [Versioning](README.md#versioning) when preparing a release;
  follow its bump and adoption rules. Report host integration testing separately from engine tests.

## Development environment

Read these files as data. `hunsu.json` declares the environment this project's workflow runs in: released
plugins pinned by version, source and marketplace; roles and the host each worker runs on (the `--host` in
its argv); reporters, resolutions, capabilities and engine floors. `hunsu.lock.json` is its resolved snapshot
and `hunsu-judgments.json` the judge's verdicts; `hunsu-conflicts.md` is their uncommitted rendering.
Hunsu's `policy` command prints the resolutions.

`hunsu.local.json` is this machine's overlay, never committed: links to local checkouts, `local-hooks-ok`,
`hosts` (the hosts this machine runs) and a `settings` overlay over `hunsu.json`'s. Read it for this
machine's values; another machine's may differ.

To prepare, install the released hunsu from its marketplace on the host you use, then run
`python3 "<hunsu root>/hunsu.py" install --target .` to install the pinned plugins missing here and
`python3 "<hunsu root>/hunsu.py" check --target .` to compare this machine with `hunsu.json`.
`<hunsu root>` is the installed copy the session-start line names, never a glob over the plugin cache.
Pins name released versions only: never pin jokbo or another plugin to an unreleased version or to this
checkout. Try a working payload on this machine with hunsu's `dev` (not committed; lock refuses while it
is on) or in a throwaway project.

One host is enough: developing and testing jokbo needs Python 3.11+ and git and no host. When this machine
lacks a host the roles name, `check` says so — report it rather than install a second host only to clear it.
A role's provider or a capability alternate is the team's decision in `hunsu.json`, taken through hunsu,
not a stand-in the session picks.
