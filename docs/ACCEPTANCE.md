# Connected Paper acceptance record

| Area | Implemented evidence | Status |
|---|---|---|
| Public exposure | No public demo router or demo session startup; connected operator routes require authentication | Pass on branch; route regression test added |
| Authentication | Supabase JWT verifier, server-side user sync, admin bootstrap, invitation-gated registration | Preserved |
| Isolation | Workspace-scoped broker, intent, Guardian, credentials, opportunities, audit, AI, outbox, and NATS helpers | Preserved |
| Encrypted BYOK | AES-256-GCM, unique nonce, workspace/provider AAD, key version, write-only status responses, test/save/delete audit | Preserved |
| Connection supervision | Per-enabled-workspace adapter and stream, reconciliation, backoff, suspension state, connection cap | Preserved |
| Real opportunities | 50-symbol additive watchlist, explicit review, real Alpaca stock/news/options evidence, no fixture fallback | Preserved |
| Confirmed execution | Immutable review, explicit acknowledgement, fresh deterministic preflight, stable client-order ID uncertainty reconciliation | Preserved |
| Connected UI | Single Connected Paper shell, scanner, approvals, positions, settings, admin, and Guardian views | Updated on branch |
| Historical schema | Existing demo-session migration/model retained only for database compatibility; no demo runtime is mounted | Intentional |

## Branch verification requirements

- Root page presents only Connected Paper access and contains no workspace chooser.
- `/demo` and legacy demo aliases return not-found rather than exposing or redirecting to a public demo.
- `/api/v1/demo/*` is not mounted.
- Authenticated Connected Paper pages retain navigation, tenant status, paper-only labels, and sign-out behavior.
- Invitation registration, admin provisioning, credential settings, scanner, approvals, Guardian, and broker reconciliation remain available behind authentication.
- No paper order or approval is submitted during this cleanup.
