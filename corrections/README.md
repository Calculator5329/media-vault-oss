# Identity corrections

`src.corrections` supplies an append-only JSONL store and the ordinary
local-index-kit rebuild callback. This is a consumer-local constraint module;
no face detector, model or shared entity registry is selected. A production
face pipeline remains behind owner G1.

Each event has a unique `id`, `op`, explicit `at`, and `reason`, plus:

- `merge`: `left` and `right` stable internal entity ids.
- `split`: `entity`, a fresh `new_entity`, and explicit `observations` ids.
- `not-her`: one `observation` and the rejected `entity`.
- `retract`: `target` names an earlier ordinary correction whose instruction
  the owner withdraws. The original line stays intact; append a new ordinary
  correction to replace it. Retractions cannot target other retractions.

The caller must retain stable observation and entity identities across rebuilds.
A changed detector cannot silently recycle ids: missing ids refuse publication
and need reconfirmation against the retained source evidence. Ids stay internal
until local-index-kit owner G1 settles the shared identity scheme. No actual
personal identity or face crop is committed by this implementation.

Use `append(path, event)` to serialize and validate appends. Never rewrite the
history. `ConstraintOverlay(path)` is passed to `Rebuilder.apply_corrections`.
It reads the entire external log on every full or incremental rebuild, retains
baseline observations, and publishes corrected assignments with the kit's seven
provenance columns. Split and negative constraints survive later merge attempts;
a contradiction refuses rather than silently erasing the earlier correction.

The synthetic acceptance uses the real kit Rebuilder. It first proves the
expected answers fail with constraint application disabled, then applies all
three operations, archives the derived database, rebuilds from source plus the
unchanged log, and verifies identical answers. No production face model runs.
