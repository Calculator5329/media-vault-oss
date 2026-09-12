# Your corrections

`organization.jsonl` is written here when you name people, tag photos, label places,
build albums, or fix dates in the viewer. It is an append-only log keyed by content
hash, so it survives renames, a rebuilt catalog, and a moved library. Removing a
tag appends an event; nothing is ever rewritten.

Back this file up with your photos. Everything under `.catalog/` and `models/` can be
regenerated from your originals; this file cannot.

It is ignored by git so your names never end up in a commit.
