"""Privacy tier enforcement at query time.

Tiers govern agents, not the owner. The rule enforced
here: a query cannot return a sealed row unless the caller explicitly
escalates. As a column plus a query-time check it is enforceable; as
documentation it would never actually be enforced.
"""


class TierError(ValueError):
    pass


def select_facts(conn, table, where="1=1", params=(), allow_sealed=False,
                 _enforce=True):
    """Query a fact table with tier enforcement.

    Without allow_sealed=True, sealed rows are excluded by the SQL itself,
    so a sealed body cannot reach the caller. _enforce exists only so the
    test suite can demonstrate the gate failing when the check is removed;
    production callers never pass it.
    """
    sql = f"SELECT * FROM {table} WHERE {where}"
    if _enforce and not allow_sealed:
        sql += " AND tier != 'sealed'"
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
