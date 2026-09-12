"""Vendored copy of local-index-kit: the three-tier local-index discipline.

Sources are authoritative and never written. Corrections are a tiny
authoritative text store. The index is derived, disposable SQLite: equal to
sources with corrections applied on top. If you cannot rm the database
without flinching, you have built a liability.

Only the four modules Media Vault uses are vendored here (provenance, rebuild,
rebuild equivalence, tier). The kit's entity registry is not part of this
program. Stdlib only.
"""

from .provenance import (
    PROVENANCE_COLUMNS,
    VALID_TIERS,
    ProvenanceError,
    create_fact_table,
    insert_fact,
    validate_row,
)
from .rebuild import Rebuilder
from .rebuild_equivalence import RebuildDivergence, assert_rebuild_equivalent
from .tier import TierError, select_facts

__all__ = [
    "PROVENANCE_COLUMNS",
    "VALID_TIERS",
    "ProvenanceError",
    "create_fact_table",
    "insert_fact",
    "validate_row",
    "Rebuilder",
    "RebuildDivergence",
    "assert_rebuild_equivalent",
    "TierError",
    "select_facts",
]
