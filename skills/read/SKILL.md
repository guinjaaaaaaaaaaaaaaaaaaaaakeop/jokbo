---
name: read
description: Use when you need to know something about this project before acting — before editing a file you have not read the documents for, when asked what was decided about something, how something works or why it is the way it is, or when catching up on what changed. Says where to read and how current each source is; it does not answer for you.
---

The engine is `jokbo.py` at this plugin's root: `python3 <plugin root>/jokbo.py <command> --target <project>` (`python` where there is no `python3`).

1. Before editing a file: `file PATH`. The edit hook already puts this in front of the first edit of each file in a session; run it by hand when you plan a change across files. Open the sections it names that bear on the change and read them before you edit. A section marked "the file changed after" may describe the code as it was — read it against the code.
2. "What did we decide about X / where is X described": `find WORDS` (the project's own words work best: a field name, a page name, a term from the plan). Read the sections and commits it names; quote them when you answer, and say when two of them disagree instead of choosing.
3. Catching up: `since` (from where this machine last looked; the session-start line says the same in brief) or `since REV`.
4. What a declared read prints (a product's own record: relations, runs, findings) is that product's word; jokbo's ranking is shared words, confirmed by nobody. Say which one you relied on.

jokbo reads; it never edits the project. `.jokbo/` is a cache: never commit it.
