# Learnings

## 2026-09-02

- Claude Fable 5.1 is schema 6/current and requires Claude Code 2.1.257 or newer; schemas 1–5 retain the reviewed `claude-fable-5` policy.
- Moving, replacing, or removing any bundled Claude route requires `--disable --apply` before a fresh setup, including legacy Fable upgrades.

## 2026-09-05

- Validator adds schema/policy 7 with only a nullable direct model route. Keep legacy schema fixtures genuinely legacy by removing the future `validator` field when constructing them.
- Validator receipts check shape, revisions, coverage, and decision consistency; the root still verifies actual invocation, current artifact content, and evidence quality. A direct child is read-only by instruction, not by an installer-controlled sandbox.
- Preserve the original restore snapshot during seat changes. Native status/catalog readiness is separate from a live model call; rerun from the target host and workspace.
- Documents/iCloud checkouts can block Git on dataless file hydration. Use a fresh non-iCloud clone for execution and preserve the old checkout.
- The sealed Claude planner permits 600 seconds; callers need a longer timeout. A shorter outer timeout can interrupt a healthy Extra High planning call.
- Validator setup and status must reject missing, empty, or malformed effort capability lists; a model name alone does not prove the requested effort is supported.
