# Claude model upgrade threat model — 0.10.0

Assets are the user's exact saved routes and restoration snapshots, first-party login isolation, model/effort identity, and the authority of a bounded planning/review result. Untrusted inputs include saved JSON, CLI capability advertisements, runtime usage metadata, ambient environment, and natural-language model labels.

Schema/policy 6 adds exact Fable 5.1 and Opus 5.5 identities. New routes accept only their own primary runtime ID; neither inherits legacy Fable's alias/helper allowance. Old schemas and schema-6 legacy routes keep their original identities. No wildcard, shared alias, model fallback, effort downgrade, or implicit state migration is allowed. The selected effort must be in the per-model sealed set and the installed CLI's advertised set. Fable's historical user-facing ultra alias means max, never a new capability. ultracode is not an effort and cannot enable tools.

A version change requires full disable followed by explicit complete setup. Disable restores exact saved hint/config snapshots; validation and status do not rewrite them. Explicit migration preserves an old effort only when it is passed again. New defaults are high for Fable 5.1 and medium for Opus 5.5. Unknown/malformed state, wrong model kind, incompatible schema, wrong runtime primary, helpers, and invalid usage counters fail closed. One bundled Claude seat remains the maximum.

The bridge continues to enforce first-party login, minimal platform environment, no tools, no session persistence, immutable saved model/effort, and strict Advisor decision schema. Setup/status perform capability/auth checks only. No model calls or real auth/config changes are used by local verification; lifecycle tests use isolated temporary homes. Existing legacy regression coverage remains alongside exact new-route and migration tests.

Residual qualification: local mocked tests cannot prove provider availability or live runtime metadata. Manual first-party qualification of both new models/seats remains required by RELEASE.md before release. Hosted protected checks and a fresh independent review attestation bound to the final exact commit remain authoritative. No loaded plugin cache or global configuration is changed by this patch.

## CLI 2.1.316 usage metadata in 0.10.1

An observed Fable 5.1 planning response included three identity fields alongside numeric usage. If any appears, the validator requires all three. `canonicalModel` must equal the enclosing model key, `provider` must be `firstParty`, and `costBasis` must be `list`. Every record still requires at least one nonnegative finite numeric counter. Numeric-only records retain their existing validation. Unknown strings, booleans, and conflicting or incomplete identity fields fail closed. The existing per-route model and helper allowlists also apply to the enclosing keys. The Opus counterpart in the regression suite is synthetic.
