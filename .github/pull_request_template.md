## Change summary

Describe the change and its user-visible effect.

## Validation

Run `python3 scripts/preflight.py full` locally and record any relevant focused tests. Local results are PARTIAL; the required hosted checks are authoritative.

## Review attestation

Replace every placeholder before merge. Every tier binds review to the exact head SHA. For `docs`, reviewer fields, findings disposition, and threat model may be `not-required`, and negative evidence may be empty. `behavior` and `security-state` require real reviewer identity and route, test evidence, and a findings disposition. `security-state` also requires a short threat model plus malformed and negative-path evidence. If the head changes, obtain a fresh final-tree review and update this attestation. Required `quality` validates this block from the actual pull request event. This is an attestation, never proof.

<!-- codex-review-attestation:start -->
{
  "schema": 1,
  "risk_tier": "docs",
  "repository": "Cjbuilds/Codex-Orchestration",
  "base_branch": "main",
  "reviewed_head_sha": "0000000000000000000000000000000000000000",
  "reviewer_identity": "not-required",
  "reviewer_route": "not-required",
  "threat_model": "not-required",
  "negative_test_evidence": [],
  "findings_disposition": "not-required"
}
<!-- codex-review-attestation:end -->
