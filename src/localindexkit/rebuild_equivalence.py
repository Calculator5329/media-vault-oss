"""The gate that keeps the whole discipline honest.

Deleting the index and rebuilding from sources plus corrections must
reproduce the same answers. If it does not, either derivation is
non-deterministic or state leaked into the index, and every downstream
consumer is now trusting an artifact that cannot be regenerated.
"""

import sqlite3
import tempfile
from pathlib import Path


class RebuildDivergence(AssertionError):
    pass


def assert_rebuild_equivalent(build_fn, queries, runs=3, workdir=None):
    """Build the index `runs` times from scratch and compare query answers.

    build_fn(index_path) builds a complete index at the given path from
    sources plus corrections. queries is a list of SQL strings; each is run
    against every build and the full ordered result sets must be identical.
    Raises RebuildDivergence naming the first query that diverged.
    """
    if runs < 2:
        raise ValueError("equivalence needs at least two builds")
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        answers = []
        for i in range(runs):
            path = Path(tmp) / f"build-{i}.sqlite"
            build_fn(path)
            conn = sqlite3.connect(path)
            try:
                answers.append(
                    [conn.execute(q).fetchall() for q in queries]
                )
            finally:
                conn.close()
        first = answers[0]
        for i, other in enumerate(answers[1:], start=2):
            for q, (a, b) in zip(queries, zip(first, other)):
                if a != b:
                    raise RebuildDivergence(
                        f"build 1 and build {i} disagree on {q!r}: "
                        f"{len(a)} vs {len(b)} rows or differing content"
                    )
