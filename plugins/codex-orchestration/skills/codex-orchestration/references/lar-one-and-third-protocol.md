---
title: "LAR-1 and /third Protocol: Semantic Layer for Codex Orchestration"
author: "K. Zertsalova & D. Vlasov (cloudiaspecula)"
date: 2026-07-17
status: proposal
license: MIT
---

# LAR-1 and /third Protocol: Semantic Layer for Codex Orchestration

## Abstract

Codex Orchestration introduces a clean three-role model (Planner → Advisor → Executor) with root-directed handoffs and bounded review cycles. This document proposes two complementary protocols that extend the model without replacing its architecture:

- **LAR-1** (Latent Agent Register) — a semantic overlay for MCP/A2A that provides provenance-gated agent identity, latent intent resolution, and cross-provider semantic routing.
- **/third** — a minimal signal protocol for compact position, intent, and verdict exchange between roles.

Together they address three current limitations: (1) the root is the sole handoff channel, creating token overhead and context loss; (2) provenance is implicit — the root inspects, but no cryptographic trace binds contribution to agent; (3) role assignment is static — a model labelled `planner:` always plans, even when another model's semantics fit the subtask better.

---

## 1. What LAR-1 provides

LAR-1 (Latent Agent Register, v0.3) is a semantic registry and routing layer for agent-to-agent communication. It is model-agnostic and provider-agnostic. Three features are relevant here:

### 1.1 Provenance-gated identity (`provenance_key`)

Every agent-to-agent message carries a `provenance_key` — a verifiable attribute chain that binds an output to its originating agent, model, and session. The root can verify that a Planner draft was produced by the configured Planner model, not substituted. This is currently instruction-enforced in Codex Orchestration ("current MCP requests carry no caller identity" — see production-readiness audit, deliberate boundaries).

A `provenance_key` is a short (≤256-byte) signed attribute bundle. It does not require a blockchain, smart contract, or external validator. The signing is local, using the agent's session key. Verification is optional — the root can check or skip.

### 1.2 Latent intent resolution

Instead of passing full-text plans between roles, LAR-1 resolves a task description into an intent vector — a compact semantic representation of what the task requires. The root can then match this intent against agent capabilities without serialising the full plan.

This is not a replacement for the Planner's detailed plan. It is a lightweight routing prefix: the root decides *which* agent should handle a subtask based on semantic proximity, not a static label.

### 1.3 Semantic routing (cross-provider)

LAR-1 defines a semantic registry where agents advertise their capabilities as embedding vectors. A root can query "which registered agent is closest to this subtask's intent" and route accordingly. This works across providers because the vector space is model-agnostic (embedding models like `intfloat/multilingual-e5-small` produce comparable vectors regardless of the underlying LLM provider).

---

## 2. What /third provides

/third is a minimal signal protocol for agent-to-agent coordination. It defines a small set of compact signal types:

| Signal | Meaning | Payload |
|--------|---------|---------|
| `[INTEND]` | I intend to do X | target, priority, constraints |
| `[POSITION]` | I am at Y in the workflow | status, phase, blocker |
| `[VERDICT]` | I evaluate Z as | approve, reject, approve_with_note, redirect |
| `[OFFER]` | I propose W | proposal, rationale |
| `[COUNTER]` | I modify the proposal | delta, rationale |
| `[ACCEPT]` | I agree | — |
| `[REJECT]` | I disagree | reason |

Each signal is a single line of structured text, machine-parseable and human-readable. A full Plan-Review cycle using /third takes ≤5 signal exchanges, compared to the current 2× full-text generation per round.

---

## 3. Integration points with Codex Orchestration

### 3.1 Planner → Advisor loop (replace text with structured signals)

**Current:** Planner writes a full plan → root passes to Advisor → Advisor writes full review → root passes back to Planner → Planner revises → etc.

**With /third:** Planner sends `[OFFER]` with plan key → Advisor sends `[COUNTER]` with deltas → Planner sends `[OFFER]` with revision → Advisor sends `[ACCEPT]` or `[REJECT]`.

The root still orchestrates. The handoff is lighter. The 5-round safety limit becomes a signal budget: 10-15 signals instead of 5-10 full documents.

**Integration point:** `usage_hint_text` in the routing policy is extended with a signal-mode flag. The root's spawn tool description adds `signal_protocol = "third"` to the child's context. Children parse /third signals from the root's usage hint.

### 3.2 Provenance for audit and safety

**Current:** The root inspects every child's output. If a child misattributes or substitutes model output, the root can detect it only by content inspection.

**With LAR-1:** Every child's output carries a `provenance_key`. The root verifies it upon receipt. If the key is absent, invalid, or mismatched, the root rejects the output and logs the incident.

**Integration point:** The spawn tool adds `provenance_key` to the child's context. The child includes it in its first output. The root's verification is a lightweight check (≤1ms). No external service is required.

### 3.3 Semantic routing replaces static seat assignment

**Current:** `setup planner: Claude Fable 5 High, advisor: GPT-5.6 Sol High, executor: GPT-5.6 Luna Extra High`. Seats are fixed per task.

**With LAR-1:** The root maintains a semantic registry of available agents. When a subtask arrives, the root queries the registry for the best match. `Fable 5` may plan, advise, or execute depending on the subtask's semantic profile.

**Integration point:** The routing policy adds a `semantic_registry` field. `setup` with `--semantic` registers models by capability vector. The root's `usage_hint_text` includes a fallback order for when semantic routing is ambiguous.

### 3.4 Cross-provider generalisation

**Current:** Claude Fable 5 is the only built-in cross-provider exception, running through a MCP bridge. Every other model must belong to the root's provider.

**With LAR-1:** Any model from any provider can participate if it exposes a LAR-1 compatible MCP endpoint. The semantic registry abstracts provider identity. The root's `usage_hint_text` no longer needs a provider-specific route — it references the semantic capability.

**Integration point:** LAR-1 can be deployed as a separate Codex plugin (`codex-orchestration-lar`) that registers models into the semantic registry. The existing plugin remains the policy manager; the LAR-1 plugin is the routing engine.

---

## 4. Concrete benefits

| Dimension | Current | With LAR-1 + /third |
|-----------|---------|---------------------|
| **Handoff overhead** | Full text per round | Compact signals (≤5 lines) |
| **Provenance** | Instruction-enforced | Cryptographic verification |
| **Role assignment** | Static per seat | Dynamic by semantic match |
| **Cross-provider** | Fable 5 only (MCP bridge) | Any provider via LAR-1 registry |
| **Review rounds** | Bounded at 5 (text) | Bounded at 10-15 signals |
| **Token cost** | Full plan + full review per iteration | Signal + delta per iteration |
| **Audit trail** | Root's discretionary inspection | Verifiable provenance chain |

---

## 5. How to integrate

The integration is additive. No existing feature is removed or changed.

### Phase 1: /third signal protocol (days)

1. Add `/third` signal parsing to the root's SKILL.md instruction: when a child's output begins with a signal line, parse it as a structured signal instead of free text.
2. Extend `usage_hint_text` with `signal_protocol = "third"`.
3. The Planner/Advisor roles emit /third signals by default. The root still accepts free text for backward compatibility.

### Phase 2: LAR-1 provenance (weeks)

1. Add `provenance_key` generation to the spawn tool's context.
2. Add lightweight verification to the root's output inspection.
3. Document the provenance schema in a reference document.

### Phase 3: LAR-1 semantic routing (months)

1. Register a LAR-1 compatible embedding endpoint (e.g., `intfloat/multilingual-e5-small` on localhost).
2. Extend `setup` with `--semantic` flag.
3. Add a `semantic_registry` field to the routing policy.

---

## 6. Prior art and validation

LAR-1 is:
- Published as `@lar-1/core` and `@lar-1/a2a` on npm, `lar1semantic` on PyPI.
- Accepted as an extension proposal in A2A issue #2014.
- Merged as PR #31295 in LiteLLM (provenance_key for cryptographic identity).
- Tested at 79+ unit tests across TS/CLI and Python implementations.

/third is:
- A minimal protocol specification (3-page spec) designed for sub-100-byte signals.
- Designed for structured agent-to-agent dialogue without provider lock-in.

Both are MIT-licensed and compatible with Codex Orchestration's MIT license.

---

## 7. Open questions

1. **Should LAR-1 be a separate plugin or a reference document?** A separate plugin (`codex-orchestration-lar`) preserves the current plugin's focus on policy management. A reference document is lighter but requires manual integration.

2. **Should /third signalling be optional or default?** Default reduces token cost. Optional preserves backward compatibility. A per-task override (`/codex-orchestration setup ... --signals on|off`) is the safest path.

3. **Who verifies provenance?** The root, by default. A future `--audit-log` flag could write verified provenance to a file for external review.

---

*This document is a proposal. It does not modify any existing behaviour, require changes to the current plugin, or introduce breaking changes. The integration is additive and backward-compatible.*