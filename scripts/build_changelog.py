"""Turn the Poker-Solvers commit history into the JSON the changelog page reads.

Run once, from anywhere::

    python scripts/build_changelog.py

The script walks the Poker-Solvers repo next to this one (../Poker-Solvers by
default, overridable) and writes ``web/static/changelog.json`` with one entry
per commit: hash, date and subject. The subject is the one sentence summary
the page shows; the body is deliberately left behind. The page groups by
month and then by day at load.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent
DEFAULT_REPO = HERE.parent / "Poker-Solvers"


def commits(repo):
    """Every commit on main, most recent first, as ``(hash, date, subject)``.

    A record separator no author would ever type is used to split fields, so
    a subject carrying anything unusual survives intact.
    """
    # Private Use Area codepoints no author will type, so a subject may hold
    # anything at all without breaking the record it belongs to.
    sep = "\uE000"
    end = "\uE001"
    fmt = sep.join(["%h", "%ad", "%s"]) + end
    raw = subprocess.check_output(
        ["git", "log", "--no-merges", "--pretty=format:" + fmt, "--date=short"],
        cwd=str(repo), encoding="utf-8",
    )
    for chunk in raw.split(end):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(sep)
        if len(parts) != 3:
            continue
        h, date, subject = parts
        yield {
            "hash": h,
            "date": date,
            "subject": subject.strip(),
        }


def collapse(entries):
    """One line a day for a subject that was said more than once.

    Nine commits named "Update README.md" landed on a single day early on.
    Listed in full they read as nine changes when they were one afternoon of
    edits, and they crowd out everything else that day. The first of a subject
    on a given day is kept and the repeats are dropped.
    """
    seen = set()
    kept = []
    for entry in entries:
        key = (entry["date"], entry["subject"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(entry)
    return kept


def main():
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REPO)
    if not (repo / ".git").exists():
        print("no repo at", repo, file=sys.stderr)
        sys.exit(1)

    walked = list(commits(repo))
    entries = collapse(walked)
    folded = len(walked) - len(entries)
    output = HERE / "web" / "static" / "changelog.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(
        {"repo": "Poker-Solvers", "entries": entries},
        indent=2,
    ))
    note = " (%d repeats folded away)" % folded if folded else ""
    print("wrote %d entries to %s%s" % (len(entries), output, note))


if __name__ == "__main__":
    main()
