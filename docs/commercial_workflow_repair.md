# Commercial workflow repair checkpoint

Scope: confirmed findings from commit `27a004ce9941197e836c71d6b28a916875b52877`,
reviewed against `8873f4aacffa2bb8f295012fc7051943a928a61f`.
Existing staged work, actual databases and unrelated local changes were preserved.
No staging, commits, deployment, live provider requests or full-suite run were performed.

## Implemented corrections

1. **Database compatibility.** Reused the existing versioned additive user-column
   migration. Startup previously altered schemas and could drop a legacy locations
   table. It now validates existing schemas and stops with an actionable error,
   without ALTER/DROP operations. Fresh empty databases retain normal initialization.
2. **Assessment and geometry.** The disconnected assessment had no map and an
   unusable confirmation control. It now inherits the actual survey template and
   map/estimation JavaScript, retaining business and finance fields. Requests use
   the existing Leaflet coordinate arrays. JSON and serialized form coordinates
   are validated by the existing geometry service. The control requires geometry
   and usage input and is disabled while submitting.
3. **Generation and persistence.** Removed gross-area capacity guesses,
   consumption-as-generation and placeholder zero saves. The authoritative
   estimation service supplies roof/obstruction areas, sizing, irradiance,
   orientation, generation, costs and financial projections. Calculation,
   comparison and persistence succeed or roll back together. Reports and the
   commercial result use the same saved outputs. Old placeholder assessments are
   explicitly unavailable on the commercial result/detail pages; they are not
   silently recalculated or rewritten.
4. **Optional PPA rate.** Blank/None means a quote is needed. Supplied non-finite,
   malformed, zero or negative rates are rejected rather than conflated with absence.
5. **Buy/Loan consistency.** Both use the same authoritative annual lifecycle
   cash flows, including O&M and the existing self-consumption/export policy.
   Loan deducts the established EMI schedule. A genuine zero-percent loan is
   preserved. Escalation comes from the saved lifecycle, not a currency tariff.
   Custom loan terms survive orientation recalculation and scenario cloning.
6. **PPA correctness.** Grid energy value and PPA energy costs are separate
   streams. Negative benefits are retained. PPA cost escalates independently;
   generation follows the saved lifecycle. Savings stop at contract end and
   longer-horizon totals are unavailable. With no upfront investment, investment
   payback is explicitly not applicable. No residential subsidy is imported.
7. **Admin review.** Both admin entry points use one working review template.
   Approval/rejection call validated transitions before mutation, recording the
   actual previous state. Rejection is an explicit allowed admin transition.
   Missing datetime imports used elsewhere in admin handling are restored.
8. **CSRF.** Create/close/delete forms use the application's standard token.
   Regression requests enable real CSRF enforcement, including assessment JSON.
9. **Project comparison.** Replaced annual-savings multiplication repeated across
   three models with summaries derived from saved model inputs/lifecycle cash
   flows. Labels distinguish upfront investment, operating benefit, financing
   payments and cumulative net cash flow. Missing projections show unavailable.
10. **Vendor outcomes.** A vendor's won/lost outcome is recorded separately using
    the existing audit-log table, scoped to their own unlock and user ID. Repeated
    identical outcomes are idempotent. They cannot close/reopen the global project
    or bypass its lifecycle. Closed projects also cannot be reopened by resubmitting
    the create form. No new database columns were necessary.

## Files changed during this repair

- `app/auth.py`, `app/database.py`, `app/models.py`, `app/pages.py`
- `app/services/commercial_calculation_service.py`, `app/services/estimation_service.py`
- `app/solar_logic.py` (only the zero-interest loan fallback)
- `app/static/js/main.js`, `app/static/js/map.js`
- `app/templates/business_estimate.html`, `app/templates/business_estimate_result.html`
- `app/templates/index.html`, `app/templates/project_detail.html`, `app/templates/report.html`
- `app/templates/vendor_board.html`
- New: `app/templates/admin_projects.html`, `app/templates/_commercial_comparison.html`
- `tests/test_routes.py`, `tests/test_sqlite_user_migration.py`
- New: `tests/test_commercial_repair.py`, `tests/commercial_assessment.test.js`
- `docs/sqlite_user_schema_migration.md`, this checkpoint

`scripts/migrate_sqlite_users.py` already existed from the previous authorized
repair and was reviewed and reused without modification.

## Validation

- Initial commercial/route subset: 29 passed, 1 failed. The new legacy-preservation
  test incorrectly expected NULL; the existing model default is zero. It now
  compares before/after values and verifies that no existing record was changed.
- Commercial repair + SQLite migration + project status + routes + calculation
  tracks: **78 passed**, exit 0, 105.40 seconds (before final focused additions).
- Commercial repair + existing financial reference cases: **43 passed**, exit 0,
  40.26 seconds (before the final CSRF and cross-track assertions).
- Final targeted run (commercial repair, financial reference cases, and business
  route cases): **48 passed, 21 deselected**, exit 0, 47.12 seconds.
- Node assessment regression: passed, exit 0. Exercises actual main.js readiness,
  geometry payload, endpoint selection, CSRF header, failed-response recovery and
  Home compatibility using a minimal DOM fixture.
- Changed application JavaScript syntax and Python compilation: passed.
- Translation validator: existing 1,612-message Hindi catalog passed coverage,
  placeholders and compiled parity. Newly introduced gettext messages may use
  English fallback until Hindi translations are supplied; no existing catalog
  entry was removed or changed.
- Scoped `git diff --check -- app tests docs scripts`: passed.
- Global `git diff --check`: unrelated trailing whitespace at
  `tmp_unlock_test_out.txt:64`; that concurrent change was preserved.
- No browser visual verification or live-provider verification was performed.
  SQLAlchemy Query.get and ReportLab deprecation warnings remain.

## Migration and recovery

No actual user/demo database was migrated during this implementation. Tests use
throwaway legacy databases and a copied fixture, verify backup contents,
credentials and saved records, and exercise rollback and idempotence.

See [the migration procedure](sqlite_user_schema_migration.md). Read-only inspection:

```powershell
.\.venv312\Scripts\python.exe scripts/migrate_sqlite_users.py --database .\solar_app.db
.\.venv312\Scripts\python.exe scripts/migrate_sqlite_users.py --database .\demo_video\demo.sqlite
```

For a database that still needs this migration, an operator explicitly repeats
its command with `--apply`. The tool verifies an online backup under
`instance/migration_backups/` before any DDL and rolls back on failure. It rejects
other missing tables/columns rather than guessing a schema or rebuilding tables.
Do not overwrite the original database during recovery; inspect the retained
backup and reconcile any newer writes as described in the migration document.

## Remaining decisions and manual checks

- No verified depreciation/tax schedule exists in the current architecture. The
  former cost/10 adjustment applied indefinitely was not defensible. Tax benefits
  are now unmodelled, with a visible CA/eligibility disclosure; a business-approved
  schedule is required before enabling this calculation.
- PPA follows the existing all-generated-energy purchase and zero-upfront model.
  No post-contract transfer value, extra fees or maintenance allocation is invented.
  Actual installer contract terms must be checked before relying on the estimate.
- Generation retains existing solar-resource fallback/provenance and geometric
  limitations. No measured load profile is synthesized from a representative bill.
- In the browser, visit `/business/estimate`, draw a roof and an internal obstruction,
  enter usage, submit, open the result/report, and compare Buy/Loan/PPA. Verify
  desktop/mobile map sizing, negative PPA values, unavailable long-contract totals,
  project creation, and the approval/rejection and vendor-outcome flows with the
  appropriate accounts. Do this in a dedicated test environment.
