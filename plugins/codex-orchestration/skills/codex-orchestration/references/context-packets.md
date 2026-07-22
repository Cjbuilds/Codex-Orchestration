# Stateless context packets

Codex-Orchestration deliberately starts routed children with `fork_turns="none"`
and calls the official sealed GLM adapter without provider-side session state. A
new call or retry therefore receives only the packet supplied for that call.

## `CONTEXT_PACKET_V1`

New GLM orchestration calls use the strict JSON schema
`codex-orchestration.context/v1`:

```json
{
  "schema": "codex-orchestration.context/v1",
  "role": "advisor",
  "phase": "advisor_review",
  "round": 1,
  "source_version": "plan-v1",
  "objective": "Review the complete current plan.",
  "context": "Repository facts, evidence, dependencies, and known risks.",
  "constraints": ["Review only; do not release execution."],
  "current_artifact": {
    "version": "plan-v1",
    "content": "The complete canonical current plan."
  },
  "findings_ledger": [],
  "open_finding_ids": [],
  "expected_output": "PLAN_APPROVED|PLAN_REVISE"
}
```

`planner_draft` requires `PLAN_DRAFT`; `planner_revision` requires
`PLAN_REVISION`; `advisor_review` requires `PLAN_APPROVED|PLAN_REVISE`.
Planner revision and Advisor review require the complete current artifact, whose
version must equal `source_version`. Finding IDs are unique, every rejected finding
has a concrete disposition, and `open_finding_ids` must exactly equal the findings
whose status is `open`.

The adapter rejects duplicate or unknown JSON keys, malformed types, mismatched
roles/phases, stale internal artifact versions, incomplete ledgers, and unsupported
output protocols. These checks establish required-field completeness. They cannot
prove that prose fields contain every fact the root should have supplied.

## Sealed official GLM calls

Validate and fingerprint the private packet without credentials or network access:

```bash
python3 <skill-dir>/scripts/zai_glm_roles.py context \
  --context-envelope-file <private-packet.json>
```

Use the returned `source_version` and `sha256` as external caller bindings:

```bash
python3 <skill-dir>/scripts/zai_glm_roles.py call \
  --role advisor \
  --context-envelope-file <private-packet.json> \
  --expected-source-version plan-v1 \
  --expected-context-sha256 <sha256>
```

The adapter compares both bindings before credential lookup or network access. It
canonicalizes the validated packet, sends one `CONTEXT_PACKET_V1` user message, and
requires the model's final nonempty line to acknowledge the exact packet digest and
source version. The result reports only the safe schema, digest, version, and
`ACK_CONFIRMED` state in addition to the existing provider/model/usage evidence.
Packets and outputs are never persisted.

The legacy `--task-file` call remains available for existing manual integrations
and preserves its response shape. New Codex-Orchestration GLM calls use the
structured mode.

## Direct routed children

When the active sub-agent surface explicitly accepts direct model
`zai/glm-5.2`, that selectable route is separate from the sealed official adapter.
The root still uses `fork_turns="none"` and sends the same complete
`CONTEXT_PACKET_V1` fields in the spawn message. The host tool does not expose a
plugin runtime hook that can validate the child message, so this boundary is
policy-enforced: the root must compare `source_version`, current artifact, ledger,
open findings, and the requested acknowledgement before accepting the handoff.

A retry is a fresh call. Resend the complete current authoritative packet, not a
shortened delta. If the authoritative context changes, increment `source_version`,
regenerate the packet and digest, and validate the new handoff independently.

## Context budget

Before credential lookup, the sealed adapter conservatively treats the complete
serialized request body's UTF-8 byte length as a prompt-token upper bound, adds the
configured maximum output tokens, and compares the sum with the manifest context
window. This intentionally over-counts JSON framing but avoids an online tokenizer
or an underestimated chat-template overhead. Over-budget input fails closed; the
adapter never truncates, summarizes, or reduces the output allowance automatically.
