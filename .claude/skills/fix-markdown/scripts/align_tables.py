"""Re-render GitHub-flavored markdown tables with aligned pipes (markdownlint MD060).

Deterministic fix for the "aligned" table-column style: parses each table, computes
every column's max cell width, and rewrites all rows padded to match. Never edits
cells — only padding between them.

Width is measured with plain len(), deliberately NOT display width (wcwidth):
markdownlint compares *character columns*, so len()-based padding is exactly what
the linter checks, and monospace editors render em-dashes/arrows as one cell anyway.
(Tables containing CJK/emoji will lint clean but may look off in an editor.)

Usage:
    python align_tables.py FILE [FILE...]          # rewrite in place
    python align_tables.py --check FILE [FILE...]  # exit 1 if changes would be made

Only blocks where every line starts and ends with `|` and the second line is a
delimiter row are treated as tables; anything else is left byte-identical.
Escaped pipes (\\|) are treated as literal cell content, matching GFM.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Split on pipes that are not backslash-escaped (GFM: \| is literal, all other
# pipes delimit cells — including inside code spans, matching GitHub's parser).
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_DELIMITER_CELL = re.compile(r"^:?-+:?$")


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) >= 2


def _split_cells(line: str) -> list[str]:
    cells = _UNESCAPED_PIPE.split(line.strip())
    return [c.strip() for c in cells[1:-1]]  # drop empties outside outer pipes


def _is_delimiter_row(line: str) -> bool:
    cells = _split_cells(line)
    return bool(cells) and all(_DELIMITER_CELL.match(c) for c in cells)


def _render_table(block: list[str]) -> list[str]:
    indent = block[0][: len(block[0]) - len(block[0].lstrip())]
    rows = [_split_cells(line) for line in block]
    ncols = max(len(r) for r in rows)

    widths = [3] * ncols  # delimiter rows need at least ---
    for i, row in enumerate(rows):
        if i == 1:
            continue  # delimiter row adapts to content widths, not vice versa
        for c, cell in enumerate(row):
            widths[c] = max(widths[c], len(cell))

    out = []
    for i, row in enumerate(rows):
        cells = []
        for c in range(ncols):
            cell = row[c] if c < len(row) else ""
            if i == 1:
                # Rebuild the delimiter preserving alignment colons.
                left = cell.startswith(":")
                right = cell.endswith(":") and len(cell) > 1
                dashes = widths[c] - left - right
                cells.append((":" if left else "") + "-" * dashes + (":" if right else ""))
            else:
                cells.append(cell.ljust(widths[c]))
        out.append(indent + "| " + " | ".join(cells) + " |")
    return out


def align_file(path: Path, check: bool) -> bool:
    """Realign all tables in path. Returns True if the file changed (or would)."""
    # newline="" keeps raw line endings — read_text would translate \r\n to \n
    # and make CRLF detection below impossible.
    with path.open(encoding="utf-8", newline="") as f:
        original = f.read()
    lines = original.splitlines()

    result: list[str] = []
    i = 0
    in_fence = False
    while i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            result.append(lines[i])
            i += 1
        elif in_fence:
            result.append(lines[i])
            i += 1
        elif _is_table_line(lines[i]):
            j = i
            while j < len(lines) and _is_table_line(lines[j]):
                j += 1
            block = lines[i:j]
            if len(block) >= 2 and _is_delimiter_row(block[1]):
                result.extend(_render_table(block))
            else:
                result.extend(block)  # pipe-ish lines but not a table — untouched
            i = j
        else:
            result.append(lines[i])
            i += 1

    # Preserve the file's existing line-ending convention (CRLF checkouts on
    # Windows must not be silently converted to LF — every line would diff).
    eol = "\r\n" if "\r\n" in original else "\n"
    trailing = eol if original.endswith("\n") else ""
    updated = eol.join(result) + trailing
    if updated == original:
        return False
    if not check:
        path.write_text(updated, encoding="utf-8", newline="")
    return True


def main(argv: list[str]) -> int:
    check = "--check" in argv
    paths = [Path(a) for a in argv if a != "--check"]
    if not paths:
        print(__doc__)
        return 2

    changed = []
    for path in paths:
        if align_file(path, check):
            changed.append(path)
            print(f"{'would realign' if check else 'realigned'}: {path}")
        else:
            print(f"already aligned: {path}")
    return 1 if (check and changed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
