# Connected Paper walkthrough

AlphaDesk now has one supported runtime: the authenticated, invitation-controlled Connected Paper Workspace.

## Access and provisioning

1. Open the web interface and sign in through `/login` using an invited identity.
2. An existing administrator can open `/admin`, create a Paper Workspace, and manage invitations.
3. An invited operator registers through `/register?code=...` and receives a tenant-scoped workspace after authentication.

## Connected Paper Workspace

1. In **Credential Settings**, test and save Alpaca paper credentials and the configured AI-provider key/model.
2. Confirm only masked fingerprints and verification state are returned after saving.
3. In **Market Scanner**, verify the Alpaca market clock, Eastern time, regular-session guidance, and additive watchlist behavior.
4. Click **Scan now**. Each row must show real provider provenance; after-hours stale evidence may correctly produce `UNAVAILABLE` or `NO_TRADE`.
5. Review an opportunity and run the read-only AI analysis. Provider failure must degrade only the AI panel.
6. If a fresh approved bounded-risk candidate exists, review every leg and risk check, acknowledge, and explicitly submit one Alpaca paper order.
7. Verify broker-confirmed state in **Positions & Orders** and tenant-specific controls in **Audit & Guardian**.

Paper submission is a real action against the operator's Alpaca paper account. Do not submit merely to make a failing scan appear successful.

## Troubleshooting

- **Registration unavailable:** check `/api/v1/auth/registration-status`, confirm public Supabase signup is disabled, and recreate the API after correcting server settings.
- **Admin has no workspace:** this is expected for a Dashboard-created bootstrap identity. Open `/admin` and use **Create my Paper Workspace**.
- **Scanner returns `UNAVAILABLE`:** inspect the visible market clock, quote age, data entitlements, and API/worker logs. Connected mode never substitutes fixtures.
- **No scan history:** run **Scan now** once to create the first explicit scan-run record.
- **AI says it needs more data:** verify the configured model and encrypted provider key. Structured-output failures are safely rejected and do not change deterministic risk or execution state.
- **Worker does not connect:** verify the Alpaca credential status is `VERIFIED`, the workspace is not suspended, and the same credential master-key version used to encrypt records is available to the worker.
