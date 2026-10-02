---
name: read
description: Use when a question about this project comes up that its records may already answer — "what did we decide about X", "why is it this way", "where is X described", "what happened in that run / today", "what changed since I last looked", "what stands on this file before I change it", or the owner asks any of these. Answers with the project's own sentences, quoted, and where each is from and how current it is.
---

The engine is `jokbo.py` at this plugin's root: `python3 <plugin root>/jokbo.py <command> --target <project>` (`python` where there is no `python3`).

- **What was decided / where it is said / why it is so**: `find WORDS` — use the project's own words (a field name, a page name, a term from the plan). It returns each document section with the sentence that says it, the code's own words, the commits, and what the declared `decision` reads (the net's questions and meanings, the environment's resolutions) say. Answer from those sentences, quoting them and naming where each is from; when two disagree, say so instead of choosing.
- **What changed**: `since` (since this machine last looked) or `since REV`.
- **Everything that stands on a file**: `file PATH` — the sections that speak of it with how current each is, who last changed it, and what each declared `file` read says.

The session-start map and the one-line notes at a file's first look come from the hooks; they are pointers and facts, not the whole answer — ask when the question is yours. What a declared read prints is that product's word; jokbo's ranking is shared words, confirmed by nobody: say which you relied on.

jokbo reads; it never edits the project. `.jokbo/` is a cache: never commit it.
