# Minimal evidence packs for the two 422 codes

These are NOT evaluation cards. Nothing grades against them, they are never in
`scenarios/`, and `readme_check` does not count them. They exist so
`POST /runs` can be shown to distinguish its two 422s (修正 C):

  fixture-leak-pack     task.json carries a `ground_truth` key -> LeakError
                        -> 422 evidence_leak. A card that must never run.
  fixture-missing-pack  no task.json at all -> FileNotFoundError
                        -> 422 evidence_missing. A card that cannot run yet.

Collapsing the two would be wrong in a specific way: the first is a correctness
emergency and the second is an ops chore. A client that cannot tell them apart
retries both or neither.

The leak pack is deliberately the smallest thing that trips
scripts/agent/leak_check.py check #1 (forbidden key). It is not a realistic pack
and must not be used as a template for one.
