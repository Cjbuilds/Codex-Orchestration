# Validator state and output review

The 0.11.0 change adds schema/policy 7 and a nullable direct-model Validator. It
uses the existing routing hints and state transaction. It adds no scheduler,
credential store, custom agent file, or execution hook.

| Asset / failure | Mitigation and boundary |
| --- | --- |
| Saved configuration and original restore snapshot | Accept exact schema 7 shape; keep schemas 1–6 valid. Preserve existing `previous`, MCP, scalar conversion, optimistic concurrency, rollback, repair, and disable behavior. Reject unknown keys and wrong types. |
| Requested model/effort | Resolve exact destination-host catalog entries. Runtime direct routing must match root provider. Config/catalog readiness does not prove a live invocation. |
| Approval of the wrong revision | Echo the exact batch and artifact revisions; checker rejects mismatches. Root verifies current content and invalidates approval after changes. |
| Forged or copied approval | Root binds the actual receipt to its own fresh Validator invocation. Model self-description is not attestation. The checker checks structure, not authorship. |
| Prompt injection in an artifact | Treat reviewed content as data; read-only instructions prohibit edits, test execution, other-seat contact, and descendants. No mechanical sandbox claim is made. |
| Incomplete or contradictory review | Require complete criteria/artifact coverage, nonempty evidence, consistent decisions, and actionable revision findings. Root judges evidence truth and quality. |
| Malformed input or parser resource abuse | Strict JSON keys/types, duplicate/nonfinite rejection, bounded input size, and blocked errors including excessive nesting. |
| Recursive or never-ending validation | Exclude control chatter, raw evidence logs, and review reports; use coherent output batches and a fixed three-review bound. A configured failed/unavailable validator blocks acceptance. |
| User override mistaken for approval | Honor explicit overrides such as no-subagents, disclose omitted validation, and never synthesize an approved receipt. |

Regression and negative coverage lives in `test_routing_state.py`,
`test_native_routing.py`, `test_fable_advisor_mcp.py`, `test_validate_output.py`,
and the packaged plugin lifecycle smoke test. Final source review is bound to
the exact commit SHA in a separate review record; any later edit invalidates it.
Local and remote-machine preflight remain PARTIAL; hosted required GitHub checks
are reported separately.
