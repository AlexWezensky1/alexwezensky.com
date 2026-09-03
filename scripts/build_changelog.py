"""Turn the Poker-Solvers commit history into the JSON the changelog page reads.

Run once, from anywhere::

    python scripts/build_changelog.py

The script walks the Poker-Solvers repo next to this one (../Poker-Solvers by
default, overridable) and writes ``web/static/changelog.json`` with one entry
per commit: hash, date, subject and body. The page groups by month at load.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent
DEFAULT_REPO = HERE.parent / "Poker-Solvers"


def commits(repo):
    """Every commit on main, most recent first, as ``(hash, date, subject, body)``.

    A record separator no author would ever type is used to split fields, so
    a multi-paragraph body with tabs and newlines survives intact.
    """
    # Private Use Area codepoints no author will type. \x1e and \x1f would work
    # too but Python's str.strip treats them as whitespace, so a commit with an
    # empty body would lose its trailing delimiter and read as three fields.
    sep = "\uE000"
    end = "\uE001"
    fmt = sep.join(["%h", "%ad", "%s", "%b"]) + end
    raw = subprocess.check_output(
        ["git", "log", "--no-merges", "--pretty=format:" + fmt, "--date=short"],
        cwd=str(repo), encoding="utf-8",
    )
    for chunk in raw.split(end):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(sep)
        if len(parts) != 4:
            continue
        h, date, subject, body = parts
        yield {
            "hash": h,
            "date": date,
            "subject": subject.strip(),
            "body": body.strip(),
        }


def main():
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REPO)
    if not (repo / ".git").exists():
        print("no repo at", repo, file=sys.stderr)
        sys.exit(1)

    entries = list(commits(repo))
    output = HERE / "web" / "static" / "changelog.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(
        {"repo": "Poker-Solvers", "entries": entries},
        indent=2,
    ))
    print("wrote %d entries to %s" % (len(entries), output))


if __name__ == "__main__":
    main()
