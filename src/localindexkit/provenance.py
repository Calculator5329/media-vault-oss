"""The provenance column contract: every derived row carries its origin.

A derived fact with no provenance cannot be trusted, selectively re-derived,
or improved. The seven columns are mandatory on every fact table in every
consuming index; a row missing any of them is rejected, never defaulted.
"""

import sqlite3

PROVENANCE_COLUMNS = (
    "source_path",
    "source_span",
    "extractor",
    "extractor_version",
    "confidence",
    "derived_at",
    "tier",
)

VALID_TIERS = ("open", "personal", "sealed")


class ProvenanceError(ValueError):
    pass


def validate_row(table, row):
    """Validate one fact row against the contract. Returns the row.

    Raises ProvenanceError naming the offending column and table.
    """
    for col in PROVENANCE_COLUMNS:
        if col not in row or row[col] is None:
            raise ProvenanceError(
                f"missing provenance column {col!r} on table {table!r}"
            )
    conf = row["confidence"]
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        raise ProvenanceError(
            f"confidence on table {table!r} must be a number in [0, 1], "
            f"got {conf!r}"
        )
    if row["tier"] not in VALID_TIERS:
        raise ProvenanceError(
            f"tier on table {table!r} must be one of {VALID_TIERS}, "
            f"got {row['tier']!r}"
        )
    return row


def create_fact_table(conn, table, extra_columns):
    """Create a fact table carrying the provenance contract plus
    consumer-specific columns. extra_columns maps name -> SQL type."""
    overlap = set(extra_columns) & set(PROVENANCE_COLUMNS)
    if overlap:
        raise ProvenanceError(
            f"extra columns {sorted(overlap)} collide with the provenance "
            f"contract on table {table!r}"
        )
    cols = [f"{name} {sqltype}" for name, sqltype in extra_columns.items()]
    cols += [
        "source_path TEXT NOT NULL",
        "source_span TEXT NOT NULL",
        "extractor TEXT NOT NULL",
        "extractor_version TEXT NOT NULL",
        "confidence REAL NOT NULL",
        "derived_at TEXT NOT NULL",
        "tier TEXT NOT NULL",
    ]
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)})")


def insert_fact(conn, table, row):
    """Validate and insert one fact row."""
    validate_row(table, row)
    cols = sorted(row)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})"
    )
    try:
        conn.execute(sql, [row[c] for c in cols])
    except sqlite3.OperationalError as exc:
        raise ProvenanceError(f"insert into table {table!r} failed: {exc}")
