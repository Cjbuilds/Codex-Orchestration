# Repository workflow

Use `python3 scripts/preflight.py quick` for fast local feedback and `python3 scripts/preflight.py full` before handoff. These commands are the source of truth; local results are PARTIAL, while required hosted checks are authoritative.

Add an exact regression test for every behavior fix. Any plugin payload change requires a strictly greater semantic version. Security or state changes require a threat model, malformed and negative-path tests, and a fresh final-tree review. Bind the review attestation to the exact head SHA; any later head change invalidates it.

Keep protected check names stable, preserve pinned automation dependencies, and fail closed when repository state or authority is ambiguous.
