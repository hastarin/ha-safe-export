---
name: fix-markdown
description: "Fix markdownlint errors in markdown files, including MD060 table alignment which markdownlint-cli2 --fix cannot repair. Use when asked to fix markdown lint, markdownlint, or table alignment errors, or when a task's own edits introduce markdown lint warnings (e.g. from IDE diagnostics or CI). Tables are realigned by a deterministic script — never by hand."
---

# fix-markdown

Fix markdownlint errors with tools, not hand-editing.
The core rule: **never hand-align a table** — models miscount characters, and markdownlint's MD060 "aligned" style compares exact character columns.
Table alignment is a rendering problem, not an editing problem: re-render the table programmatically.

## Workflow

Work only on the files in scope (the ones named, edited, or failing) — never `markdownlint-cli2 "**/*.md" --fix` repo-wide unless explicitly asked; it churns unrelated files.

1. **Diagnose.** `npx --yes markdownlint-cli2 <files>` to get the current error list.
   Respect the project's config (`.markdownlint-cli2.jsonc` / `.markdownlint.jsonc`) — don't second-guess disabled rules.
2. **Auto-fix.** `npx --yes markdownlint-cli2 --fix <files>` handles most whitespace/blank-line/list rules.
3. **Tables (MD060 or any misaligned pipes).** Run the bundled script — do not edit pipes or padding by hand under any circumstances:

   ```bash
   python <skill-dir>/scripts/align_tables.py <files>
   ```

   It re-renders every table in the file with columns padded to the widest cell, preserving alignment colons, indentation, cell content, and the file's line-ending convention (CRLF/LF) exactly.
   `--check` mode previews without writing.
   Note it aligns **all** tables in the file — including uniformly-compact ones that MD060's "consistent" mode tolerated — so the diff can be larger than the error list; review it before committing.
   If a table needs *content* changes to be reasonable (e.g. one enormous cell dominating the layout), shorten the cell text first, then run the script — never compensate with manual padding.
4. **Remaining style rules** (not auto-fixable): make targeted edits matching the file's existing convention, not your own default.
   - MD049/MD050 (emphasis/strong style): grep the file for which marker it already uses (`_x_` vs `*x*`) and match it.
   - MD029 (ordered-list numbering): usually caused by under-indented content *between* list items breaking list continuity — fix the indentation (typically to 4 spaces), not the numbers.
5. **Verify.** Re-run `npx --yes markdownlint-cli2 <files>` and confirm zero errors.
   If errors remain that predate your changes and are out of scope, report them explicitly rather than expanding the change.

## Why the script instead of Prettier

`npx prettier --write` also aligns tables, but it reformats the *whole file* (bullets, escapes, emphasis), producing diff noise in carefully formatted docs.
The script touches only table padding — byte-identical everywhere else, and idempotent.
Fall back to Prettier only if the script declines a table (it skips blocks that aren't strictly `| ... |` rows with a delimiter line).

## Notes

- The script measures width with `len()`, deliberately not display width — markdownlint compares character columns, so this is exactly what the linter checks. Tables with CJK/emoji will lint clean but may look uneven in an editor.
- Escaped pipes (`\|`) are treated as cell content; unescaped pipes inside code spans delimit cells, matching GitHub's own parser.
