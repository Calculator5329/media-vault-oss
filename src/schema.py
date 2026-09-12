"""S1: the type-open item schema and its provenance-carrying fact tables.

One row in `items` per canonical media item, keyed by content hash, so the
same bytes at four paths are one item and not four. `kind` is `photo`,
`video` or `other`: the table is type-open by design, but only photos and
videos get deep facts here (repo CLAUDE.md, "what this explicitly does not
do"). A text file dropped in the vault gets an item row and nothing else,
which is the honest answer rather than a silent omission.

Every table below is created through the kit's `create_fact_table`, so all of
them carry the seven provenance columns and every row is validated on insert.
`items` is a fact table too: an item row is derived from a file walk, and
"derived by walking, at this time, from this path" is exactly the claim the
contract exists to record. It also means the kit's rebuilder recognises every
table here and purges it by `source_path` when a source changes.
"""

from .kit import create_fact_table

ITEMS = "items"
ITEM_PATHS = "item_paths"
MEDIA_INFO = "media_info"
TAKEN_AT = "taken_at"
LOCATION = "location"
PHASH = "phash"
VARIANT_GROUPS = "variant_groups"
THUMBNAILS = "thumbnails"

# Every table carrying the provenance contract. Ingest purges these by path
# when it invalidates a duplicate group; the kit's rebuilder discovers the
# same set by inspecting columns.
FACT_TABLES = (
    ITEMS,
    ITEM_PATHS,
    MEDIA_INFO,
    TAKEN_AT,
    LOCATION,
    PHASH,
    VARIANT_GROUPS,
    THUMBNAILS,
)

# Bookkeeping, not facts: the membership of each exact-duplicate group as of
# the last successful ingest. Deliberately carries no provenance columns, so
# the rebuilder does not mistake it for derived content.
GROUP_STATE = "mv_group_state"

_TABLES = {
    # One canonical item per content hash. The provenance `source_path` is
    # the canonical path, so there is no second column saying the same thing.
    ITEMS: {
        "content_hash": "TEXT PRIMARY KEY",
        "kind": "TEXT NOT NULL",
        "ext": "TEXT NOT NULL",
        "size_bytes": "INTEGER NOT NULL",
    },
    # Every path the bytes were found at, canonical included. Exact duplicates
    # collapse to one item and show up here as extra rows.
    ITEM_PATHS: {
        "content_hash": "TEXT NOT NULL",
        "path": "TEXT NOT NULL",
        "is_canonical": "INTEGER NOT NULL",
    },
    # Attribute/value shape on purpose: width, height, duration_s, codec and
    # whatever the next container teaches us, without a migration each time.
    MEDIA_INFO: {
        "content_hash": "TEXT NOT NULL",
        "attribute": "TEXT NOT NULL",
        "value_text": "TEXT",
        "value_num": "REAL",
    },
    TAKEN_AT: {
        "content_hash": "TEXT NOT NULL",
        "taken_at": "TEXT NOT NULL",
    },
    LOCATION: {
        "content_hash": "TEXT NOT NULL",
        "lat": "REAL NOT NULL",
        "lon": "REAL NOT NULL",
    },
    # 8x8 average hash, 64 bits as 16 hex chars. `gray_range` is the spread of
    # the 64 gray samples: a near-flat image hashes to noise that matches every
    # other near-flat image, so grouping skips those rather than merging a
    # blank wall with a blank sky.
    PHASH: {
        "content_hash": "TEXT NOT NULL",
        "phash": "TEXT NOT NULL",
        "gray_range": "INTEGER NOT NULL",
    },
    # Near-variant grouping: resized or re-encoded copies of one image.
    # `group_key` is the lowest content hash in the group, so the key is
    # stable across rebuilds and does not depend on walk order.
    VARIANT_GROUPS: {
        "content_hash": "TEXT NOT NULL",
        "group_key": "TEXT NOT NULL",
        "distance": "INTEGER NOT NULL",
    },
    THUMBNAILS: {
        "content_hash": "TEXT NOT NULL",
        "thumb_path": "TEXT NOT NULL",
        "width": "INTEGER NOT NULL",
        "height": "INTEGER NOT NULL",
    },
}

_INDEXES = (
    f"CREATE INDEX IF NOT EXISTS idx_item_paths_hash ON {ITEM_PATHS}(content_hash)",
    f"CREATE INDEX IF NOT EXISTS idx_media_info_hash ON {MEDIA_INFO}(content_hash)",
    f"CREATE INDEX IF NOT EXISTS idx_taken_at_hash ON {TAKEN_AT}(content_hash)",
    f"CREATE INDEX IF NOT EXISTS idx_location_hash ON {LOCATION}(content_hash)",
    f"CREATE INDEX IF NOT EXISTS idx_variant_group_key ON {VARIANT_GROUPS}(group_key)",
    f"CREATE INDEX IF NOT EXISTS idx_items_kind ON {ITEMS}(kind)",
)


def setup(conn):
    """Create every table and index. Idempotent, as the rebuilder requires."""
    for table, columns in _TABLES.items():
        create_fact_table(conn, table, columns)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {GROUP_STATE} ("
        "content_hash TEXT PRIMARY KEY, member_paths TEXT NOT NULL)"
    )
    for stmt in _INDEXES:
        conn.execute(stmt)
