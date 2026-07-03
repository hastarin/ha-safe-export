---
name: work-issue
description: "Work a single GitHub issue in this repo end-to-end and open a PR that closes it. Use when asked to work/tackle/implement a specific issue number (e.g. \"/work-issue 12\", \"work issue 12\", \"do #12\"). Designed for one issue per session — especially cloud sessions launched from the Claude Desktop app against hastarin/ha-safe-export."
argument-hint: "<issue-number>"
---

# Work a GitHub issue → PR

Work GitHub issue **#$1** in this repo (`hastarin/ha-safe-export`) end-to-end, then open a single pull request that closes it. Do exactly one issue; do not batch several into one session or one PR.

## Before writing any code

1. Read `CLAUDE.md` in full and follow its conventions. In particular: commit style (no AI co-author, no agent mentions); one full sentence per line in Markdown; the `ruff` / `pytest` bar; and the "When in doubt, ask" list of changes that need discussion (schema of `daily_observations`, source-sensor swaps, column formulas, new convenience columns, window boundaries). Sensor names come from `config.yaml` — never hardcode them.
2. Read issue **#$1** in full — the body carries the audit context, `file:line` evidence, a task checklist, acceptance criteria, and known pitfalls. **Treat the acceptance criteria as the definition of done.**
3. Check the issue's **"blocked by"** relationships (GitHub native dependencies). **If any blocker is still open, stop and report that** — post a short note on the issue naming the open blocker, and do not work around it or reimplement the blocker's work.

## Constraints

- **Stay strictly within the issue's scope.** If you notice an unrelated defect, mention it in the PR description (or file a follow-up issue); do not fix it in this PR.
- **This environment has no gitignored personal data.** There is no `data/home-assistant_v2.db`, no `data/dataset.db`, and no `config/config.yaml` (all gitignored). Tests that depend on them must **skip cleanly** — skipped is expected, errored is not. Never invent, stub, or commit substitute data or config to make a test run.
- **Model coefficients live in multiple synced copies** (`config.yaml` / `tests/conftest.py`, `tools/nodered-flow.json`, and the ladder in `src/model.py`). If the issue touches any of them, change all of them together — `tests/test_sync.py` enforces this and will fail on drift.
- Add a `CHANGELOG.md` entry under `## [Unreleased]` if the issue calls for one. Be precise in the wording (this project has been bitten by imprecise time/bucket descriptions before).

## Before finishing

Run both, from the repo root:

```bash
ruff check .
python -m pytest
```

Both must be clean — **failures are not acceptable; skips are** (see the personal-data note above).

## Deliverable

A single PR from your branch, whose description:

- Summarises **what changed and why**, tied back to the issue's acceptance criteria.
- **Explicitly lists anything that could not be verified in this environment** (e.g. "the regenerated backtest report can only be diffed against the real dataset locally", or golden-fixture extraction tests that require the HA DB) so it can be checked on the maintainer's machine before merge.
- Includes the literal line: `Closes #$1`

If, partway through, the issue turns out to require one of the "When in doubt, ask" changes in `CLAUDE.md`, or a decision only the maintainer can make, stop and say so on the issue rather than guessing.
