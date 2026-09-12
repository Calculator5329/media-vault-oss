"""The provenance and rebuild primitives Media Vault builds on.

They come from local-index-kit, vendored under ``src/localindexkit`` so a
clone of this repository is self-contained. Everything else in ``src`` imports
these names from here, so there is a single line to change if the kit ever
ships as a real distribution.
"""

from .localindexkit import (
    PROVENANCE_COLUMNS,
    VALID_TIERS,
    ProvenanceError,
    RebuildDivergence,
    Rebuilder,
    TierError,
    assert_rebuild_equivalent,
    create_fact_table,
    insert_fact,
    select_facts,
    validate_row,
)

# content_hash and the rebuilder's state table are not in the kit's public
# __init__, but imports need both: the first to group duplicates before the
# rebuild starts, the second to invalidate a duplicate group whose membership
# changed. Import them from the submodule rather than copying either.
from .localindexkit.rebuild import STATE_TABLE, content_hash

__all__ = [
    "STATE_TABLE",
    "PROVENANCE_COLUMNS",
    "VALID_TIERS",
    "ProvenanceError",
    "RebuildDivergence",
    "Rebuilder",
    "TierError",
    "assert_rebuild_equivalent",
    "content_hash",
    "create_fact_table",
    "insert_fact",
    "select_facts",
    "validate_row",
]
