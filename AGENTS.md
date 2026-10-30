# Working on jokbo

This is the shared source-maintenance guide for Claude and Codex. Read [README.md](README.md) for the
reader's behavior, hooks and limits. Installed operation is defined in [skills/read/SKILL.md](skills/read/SKILL.md);
this checkout guide is not injected into a consumer's project by plugin installation.

- Keep retrieval and cache behavior in `jokbo.py`. The reader supplies source text, provenance and freshness;
  its ranking does not decide which source is true. Preserve those distinctions in output and documentation.
- Read other products through declared `reads` argv. Do not import sibling plugins or hardcode their
  internal records. This repository must work as an independent checkout.
- Keep `skills/` common to both hosts. `commands/`, `hooks/` and host manifests adapt entry points to the
  host; do not duplicate the shared workflow there. `CLAUDE.md` only imports this file.
- `.jokbo/` is disposable local cache, not a committed source. Preserve atomic writes and locking for
  shared session/sub-agent state, including outstanding asynchronous calls across compaction.
- From this repository root run `python3 test_jokbo.py` for behavior changes (standard library only).
  Use temporary projects for experiments rather than changing a consumer's records.
- For documentation-only changes, verify links and CLI examples against `python3 jokbo.py --help`.
  Keep version metadata consistent across `plugin.json`, both host manifests and marketplace entries
  when preparing a release; follow [Versioning](README.md#versioning) for bump and adoption rules.
  Report host integration testing separately from engine tests.
