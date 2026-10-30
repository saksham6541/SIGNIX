# Installer credits and secure unlocking — Part 2

Built on the uncommitted [Part 1 implementation](installer_leads.md). No filters,
saved-lead persistence, calculations or real databases were reimplemented/modified.

## Reused records and repaired defects

- `User.vendor_credits_balance`, `VendorLeadUnlock` and its unique vendor/project
  constraint, `VendorCreditLedger`, `Project`, and the configured size-based credit
  prices remain authoritative. No second credit balance or unlock system exists.
- The previous unlock used a nested savepoint, had no explicit final commit and
  performed read/modify/write against a cached balance. The replacement commits
  balance, ledger and unlock together, or rolls all of them back.
- Approval formerly refilled an empty account on repeated approval. Starting
  credits now issue only on first pending-vendor approval, with existing approval
  history and grant records checked. Reapproval never replenishes spent credits.
- Outcomes reuse `AdminActionLog` with the vendor as actor and their unlock ID as
  target. Interested, follow-up, won and lost changes are timestamped; repeated
  identical outcomes add no duplicate event. No customer project status is changed.

## Transactions

All credit writers use one service. Authentication's read-only transaction is
discarded only after checking that the session has no pending changes. SQLite
`BEGIN IMMEDIATE` acquires the writer lock before fresh authorization/eligibility,
balance and slot-count reads. Other SQLAlchemy databases use row locks on the
vendor and project (and the estimate used for pricing). SQLite concurrency was
tested; PostgreSQL execution has not been verified in this phase.

The debit also uses a conditional atomic balance update. A unique unlock pair,
serialized availability checks and one commit prevent duplicate charges, negative
balances, oversold project slots and partial writes. Retries of an already-owned
unlock return the original receipt without a debit, including after project closure.
Failed/busy database operations roll back and return a safe retry response. Unknown,
non-finite, non-positive or unrepresentable costs cannot produce free unlocks.

The existing float credit representation is preserved. Running ledger balances
are checked with an absolute tolerance of 1e-8 for floating-point arithmetic, not
a relative tolerance that could conceal large discrepancies. An inconsistent
ledger blocks further credit writes pending administrator review; no historical
entry or persisted balance is silently rewritten.

For legacy accounts, opening balance is the first recorded balance minus its
delta, or the existing persisted balance if no ledger exists. This is displayed
as a historical opening balance, not fabricated income/payment. Opening balance
plus actual ledger deltas must reconcile to the current balance and each recorded
running balance. The UI displays transaction IDs, timestamps, deltas and balances.

## Routes and authorization

| Route | Purpose |
| --- | --- |
| `POST /vendor/board/<project_id>/unlock` | Atomic unlock; accepts standard CSRF-protected form submission; JSON response for JSON Accept |
| `GET /vendor/unlocked-leads` | Paginated, vendor-owned purchase history and permitted contacts |
| `GET /vendor/leads/<project_id>` | Existing detail route, now supports owned leads after closure |
| `POST /vendor/board/<project_id>/status` | Vendor-owned outcome/history only |
| `GET /vendor/credits` | Persisted balance, paginated ledger, configured costs and latest 50 credit requests |
| `POST /vendor/credits/request` | Submit a pending credit request; one pending request per vendor |
| `POST /admin/credit-requests/<request_id>/review` | Administrator approves or rejects a pending credit request exactly once |
| `POST /admin/vendors/<user_id>/approve` | Existing approval, now with serialized one-time starting grant |

All vendor pages require approval. Every credit mutation rechecks persisted role
and approval under the transaction lock. Vendor identity is taken exclusively
from the authenticated session. State-changing forms retain CSRF protection.
Existing public board/saved lists still never receive customer contact details.
Unlocked contacts require the current vendor's own unlock, a commercial estimate
and current customer consent. Closing a project stops new purchases but does not
erase a vendor's purchase history. Revoked consent hides contacts, even for an
owner; their purchase date/cost remain visible. No report-access bypass was added.

## Credit requests, not simulated payments

There is no payment integration. The existing `VendorRefundRequest` table supports
requests with amount, reason, status, reviewer and review timestamp; this phase
uses the distinct `issue_type='credit_request'` subtype. Existing refund records
cannot be processed through the new grant route. Submission creates only a pending
request and audit event. Only an administrator's approval credits the exact requested
amount, records the ledger transaction and finalizes the request atomically.
Rejection creates no credit. Repeat/concurrent review cannot grant again. Input
is finite, positive and bounded at 1,000,000 credits to reject abusive requests;
that input bound is not a price, payment, entitlement or automatic allowance.

## Files changed in Part 2

- `app/auth.py`
- `app/services/vendor_credit_service.py` (new)
- `app/services/vendor_lead_service.py` (owned-detail eligibility and invalid-cost guard only)
- `app/templates/base.html`, `app/templates/_vendor_lead_nav.html` (navigation)
- `app/templates/_vendor_unlocked.html` (receipt and outcome history)
- `app/templates/admin_vendors.html` (credit-request review)
- `app/templates/vendor_credits.html`, `app/templates/vendor_unlocked_leads.html` (new)
- `translations/messages.pot`, `translations/hi/LC_MESSAGES/messages.po`,
  `translations/hi/LC_MESSAGES/messages.mo` (additive Hindi coverage)
- `tests/test_vendor_credits.py` (new)
- `docs/installer_credits.md` (this report)

No Part 2 schema change/migration is required. The Part 1 saved-lead migration
still needs explicit application on installations that have not yet adopted it.
No real database or backup was changed in either phase's development checks.

## Verification

- Initial new credit suite: **19 passed**, exit 0.
- Expanded credit suite plus relevant existing project/vendor, privacy and
  eligibility tests: **42 passed**, exit 0, 65.79 seconds.
- Final precision guard and core success/concurrency/request-grant rerun:
  **6 passed**, exit 0.
- Existing saved-lead JavaScript interaction suite: **7 passed**, exit 0.
- Hindi validation: **1,745 messages**, coverage/placeholders/compiled parity pass.
- Changed Python compilation and `git diff --check`: passed.

All tests used temporary fixture databases. No browser/E2E verification or full
suite was run in Part 2, per the usage constraint; the full suite is deferred to
Part 6. No commits, pushes, deployments, migrations or stash operations were made.

The real-database historical ledger quality, PostgreSQL concurrency and new page
visual layout remain unverified. Historical discrepancies require operator review;
the implementation deliberately does not invent reconciliation transactions.
