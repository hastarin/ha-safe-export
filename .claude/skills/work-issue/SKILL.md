---
name: work-issue
description: "Work a single GitHub issue in this repo end-to-end and open a PR that closes it. Use when asked to work/tackle/implement a specific issue number (e.g. \"/work-issue 12\", \"work issue 12\", \"do #12\"). Designed for one issue per session — especially cloud sessions launched from the Claude Desktop app against hastarin/ha-safe-export."
argument-hint: "<issue-number>"
---

# Work a GitHub issue → PR

Work a single GitHub issue in this repo (`hastarin/ha-safe-export`) end-to-end, then open a single pull request that closes it. Do exactly one issue; do not batch several into one session or one PR.

## Resolve the issue number first

The issue number is passed as `$1`. Normalise it before doing anything else:

- Strip a leading `#` if present — `#17` and `17` both mean issue **17**. Use the bare number everywhere below (the auto-close keyword only fires on a single `#`, so `Closes ##17` would silently fail to close the issue).
- If `$1` is empty or is not a positive integer after stripping, **stop immediately and ask which issue to work** — do not guess, and do not proceed with a malformed `#`/`Closes #` line. List the repo's open issues (`gh issue list`) to help the user pick.

Treat the resolved bare number as **N** in the rest of this skill (e.g. issue **#N**, `Closes #N`).

## Before writing any code

1. Read `CLAUDE.md` in full and follow its conventions. In particular: commit style (no AI co-author, no agent mentions); one full sentence per line in Markdown; the `ruff` / `pytest` bar; and the "When in doubt, ask" list of changes that need discussion (schema of `daily_observations`, source-sensor swaps, column formulas, new convenience columns, window boundaries). Sensor names come from `config.yaml` — never hardcode them.
2. Fetch issue **#N** yourself with `npx -y gh-axi issue view N --full` (**always pass `--full` on the first fetch** — the default view truncates the body, and re-fetching without it just doubles the round-trip). Read it in full — the body carries the audit context, `file:line` evidence, a task checklist, acceptance criteria, and known pitfalls. **Treat the acceptance criteria as the definition of done.**
3. Check the issue's **"blocked by"** relationships (GitHub native dependencies). **If any blocker is still open, stop and report that** — post a short note on the issue naming the open blocker, and do not work around it or reimplement the blocker's work.
4. Check the issue's **labels**. If it carries the **`hitl`** label (human-in-the-loop: a live-system change that needs Jon's review + a Node-RED redeploy before 6pm), **stop before writing any code and surface this to the user.** Spell out concretely what they will have to do by hand once the PR merges — at minimum: re-import `tools/nodered-flow.json` into Node-RED (editing the repo file does **not** update the running flow), and confirm the change is live **before the 6pm export runs**. Name any specific manual steps the issue body calls out (e.g. HA `recorder`/`customize` config, verifying a sensor reaches long-term `statistics`). **In an interactive session, wait for the user to acknowledge before continuing.** In a non-interactive/cloud session where you cannot prompt, proceed but make these manual steps unmissable — repeat them verbatim in the PR description under a **"⚠️ Human-in-the-loop steps required before this goes live"** heading.

## Constraints

- **Stay strictly within the issue's scope.** If you notice an unrelated defect, mention it in the PR description (or file a follow-up issue); do not fix it in this PR.
- **This environment has no gitignored personal data.** There is no `data/home-assistant_v2.db`, no `data/dataset.db`, and no `config/config.yaml` (all gitignored). Tests that depend on them must **skip cleanly** — skipped is expected, errored is not. Never invent, stub, or commit substitute data or config to make a test run.
- **Model coefficients live in multiple synced copies** (`config.yaml` / `tests/conftest.py`, `tools/nodered-flow.json`, and the ladder in `src/model.py`). If the issue touches any of them, change all of them together — `tests/test_sync.py` enforces this and will fail on drift.
- Add a `CHANGELOG.md` entry under `## [Unreleased]` if the issue calls for one. Be precise in the wording (this project has been bitten by imprecise time/bucket descriptions before).

## Running lint and tests — any time, not just at the end

Whenever you need to run `ruff check` and/or `pytest` — mid-task to check progress, after a fix, or as the final gate — keep the raw output out of context instead of letting a full passing run dump hundreds of lines. Both tools are exit-code-driven and put their actual summary at the end of the output, so a plain shell pipeline handles this without needing a subagent:

```bash
.venv/Scripts/python -m ruff check . ; echo "ruff exit: $?"
.venv/Scripts/python -m pytest -q 2>&1 | tail -30
```

- Check the exit code / tail first. If it's clean, that's all you need — don't re-run without `tail` "just to see".
- If something failed and `tail -30` doesn't show all of it (e.g. many failures), follow up with `grep -E "FAILED|ERROR"` against the same run to get the complete list of failing test names, rather than re-running with a larger `tail`.
- Scope the run to what's relevant: the whole suite when you want a full gate, or a narrower path (e.g. `pytest tests/test_model.py -q`) when you're mid-task and only care about the area you're touching.
- Fix any reported failures yourself, then re-run the same way rather than assuming it's fixed.

## Before finishing

Run both from the repo root as a final gate, using the same exit-code-first / tail approach above:

```bash
.venv/Scripts/python -m ruff check . ; echo "ruff exit: $?"
.venv/Scripts/python -m pytest -q 2>&1 | tail -30
```

Both must be clean — **failures are not acceptable; skips are.**

## Deliverable

A single PR from your branch, whose description:

- Summarises **what changed and why**, tied back to the issue's acceptance criteria.
- **Explicitly lists anything that could not be verified in this environment** (e.g. "the regenerated backtest report can only be diffed against the real dataset locally", or golden-fixture extraction tests that require the HA DB) so it can be checked on the maintainer's machine before merge.
- Includes the literal line: `Closes #N` (the bare number, single `#`).
- If the issue was labelled **`hitl`**, carries the **"⚠️ Human-in-the-loop steps required before this goes live"** section from step 4 — the Node-RED re-import, the before-6pm deadline, and any HA-config steps the issue named — so merging does not silently skip the manual live-system work.

If, partway through, the issue turns out to require one of the "When in doubt, ask" changes in `CLAUDE.md`, or a decision only the maintainer can make, stop and say so on the issue rather than guessing.
