# Installer lead discovery and saved leads — Part 1

The existing approved-vendor gate, commercial projects, estimates, unlock records,
credit policy and vendor outcomes are reused. This phase adds discovery and private
bookmarks. It does not alter estimation, credit deductions or project transitions.

## Routes and behavior

| Control | Route | Behavior |
| --- | --- | --- |
| Lead Board | `GET /vendor/board` | Eligible commercial projects, SQL filtering and paging |
| Saved Leads | `GET /vendor/saved-leads` | Only the authenticated vendor's bookmarks |
| Open lead | `GET /vendor/leads/<project_id>` | Rechecks the same eligibility rules |
| Save / Unsave | `POST /vendor/leads/<project_id>/saved` | Explicit `saved=true` or `saved=false`, standard CSRF token |

GET filters: `city`, `locality`, `pincode`, `capacity_min`, `capacity_max`,
`business_type`, `preferred_model`, `bill_min`, `bill_max`, `posted`, `sort`, `page`.
Ranges are inclusive. The bill controls allow arbitrary non-negative minimum and
maximum rupee values rather than inventing screenshot-specific bands. City and
locality use escaped, case-insensitive substring matches; PIN is an exact six-digit
match. Categories follow the existing assessment choices. Finance options are
`buy`, `loan`, `ppa`, only when a preference was actually persisted. Posting presets
are all, 7 days, and 30 days (rolling UTC windows, excluding future dates).
Sorts are newest/oldest, capacity ascending/descending and bill ascending/descending.
Unknown values sort last. Ordering has a stable project-ID tie-breaker. Pages contain
12 rows, counts are computed in SQL, and out-of-range pages clamp to the last page.
Clear Filters returns to the current list's unfiltered first page.

Malformed, duplicate or contradictory filter values return HTTP 400 with field
errors and retained input. Native selects follow the existing `DESIGN.md` ownership
decision: OS popup geometry is accepted for bilingual and keyboard support. Jinja
renders both lists from shared card/navigation partials. CSS consumes the existing
`style.css` variables; it creates no new shared design tokens. Native GET/POST forms
work without JavaScript; bookmark enhancement adds a 15-second timeout, pending
state, duplicate-click protection, live feedback and retry. Save state changes only
after server confirmation. Existing unlocked-contact/outcome markup and the existing
unlock action now live on the detail screen; their backend implementation is unchanged.

## Data, eligibility and privacy

- The query joins `Project` to `UserLocation` and selects an allowlist of public
  columns. Lists never fetch/pass customer name, owner, email, phone, exact address,
  report link, full estimate JSON or project ORM objects to their templates.
- Visibility requires an approved vendor, an open project, recorded consent and
  a persisted business track (or the model's established commercial legacy types).
  Service-area matching uses only city/locality/PIN. The existing maximum unlock
  rule applies, with the existing exception for a vendor who already unlocked it.
- Location is read from structured `extras` or `location_context`, never guessed
  from an exact street address. A legacy lead without public area fields cannot
  match a location/service-area filter. No geocoding/provider calls are made.
- Capacity uses persisted system size, then an explicit legacy `system_size_kw`.
  It is never inferred from roof area. Zero placeholder capacity is unknown.
- A units-only business assessment's derived sizing bill is not an actual bill;
  explicit `monthly_bill_rs=None` stays unavailable. Actual zero bills remain zero.
  Malformed/non-finite/negative numeric metadata cannot qualify for ranges.
- Missing business preferences and timelines are displayed as not provided.
  Existing assessment workflows do not always collect structured city/locality/PIN,
  preferred model or installation timeline. This phase does not backfill them.
- New `VendorSavedLead` rows have user/project foreign keys, cascade deletion,
  a unique vendor/project pair, lookup indexes and a creation timestamp. Vendor ID
  comes from the authenticated session, never request data. Repeated saves/unsaves
  are idempotent; the database constraint handles concurrent saves. A busy database
  returns a safe retryable 503, without internal SQL or exceptions.
- Ineligible saved projects remain as masked, removable bookmarks. Opening or saving
  an ineligible project returns 404. Unsave cannot affect another vendor's record.
  Saving never creates an unlock, changes project status or deducts credits.

## Explicit schema upgrade

**No existing user/demo database was migrated during development.** The new table
must be added to each existing SQLite deployment before starting this version.
Fresh installations create it through the normal empty-database initialization.
Startup continues to validate schemas and refuses automatic migrations.

Stop app writers and identify the actual configured SQLite file first. Run with
the application's environment, substituting its confirmed absolute database path:

```powershell
.\.venv312\Scripts\python.exe scripts/migrate_vendor_saved_leads.py --database "D:\confirmed\path\database.sqlite"
.\.venv312\Scripts\python.exe scripts/migrate_vendor_saved_leads.py --database "D:\confirmed\path\database.sqlite" --apply
```

The first command is read-only. `--apply` takes a write lock, verifies database
integrity, creates and verifies a uniquely named online backup under
`instance/migration_backups/`, adds only `vendor_saved_leads` and its indexes, and
records version `20261030_01_vendor_saved_leads` in the existing migration history.
It fingerprints all original records before/after, verifies foreign keys and
integrity, then commits. Repeating it returns `already_applied`. Schema drift is
rejected rather than silently repaired. `--backup-dir` selects another backup folder.
For installations also missing old user columns, add this table first and follow
the [separate user-column procedure](sqlite_user_schema_migration.md); neither runs
automatically. Never assume the normal and demo apps use the same database file.

On any failure the DDL transaction rolls back; output identifies the retained
backup. Keep the app stopped, inspect the database and backup read-only, and fix
the reported prerequisite before retrying. Do not overwrite a current database
with a backup: restore to a separate recovery file, verify it, and explicitly
reconcile any newer writes before selecting that recovery copy for the app.

## Focused checks

```powershell
.\.venv312\Scripts\python.exe -m pytest tests/test_vendor_leads.py tests/test_project_status.py tests/test_commercial_repair.py -q
node --test tests/vendor_leads.test.js
.\.venv312\Scripts\python.exe scripts/validate_translations.py
.\.venv312\Scripts\python.exe -m pytest tests -q
git diff --check
```

Migration tests use newly created temporary databases or a copy of the temporary
test database only. Browser preview also uses a separate database populated with
test fixtures, never existing customer data. Production performance and
PostgreSQL migration behavior are not certified by SQLite fixture tests.


## File inventory

- Backend: `app/auth.py`, `app/models.py`, `app/database.py`,
  `app/services/vendor_lead_service.py`.
- Templates: `app/templates/base.html`, `app/templates/vendor_board.html`,
  `app/templates/vendor_lead_detail.html`, `app/templates/_vendor_lead_nav.html`,
  `app/templates/_vendor_lead_card.html`, `app/templates/_vendor_unlocked.html`.
- Assets: `app/static/css/vendor-leads.css`, `app/static/js/vendor-leads.js`.
- Migration: `scripts/migrate_vendor_saved_leads.py`.
- Tests: `tests/test_vendor_leads.py`, `tests/vendor_leads.test.js`.
- Localization: `translations/messages.pot`,
  `translations/hi/LC_MESSAGES/messages.po`, `translations/hi/LC_MESSAGES/messages.mo`.
- Documentation: `docs/installer_leads.md`.


## Verification notes

- Browser verification used an isolated app on localhost with a new temporary
  database and an approved test vendor. Observed: login, combined city/capacity
  filters, sorting, invalid-range feedback and focus, save, refresh persistence,
  Saved Leads, opening a saved lead, unsave/empty state, Hindi labels and messages,
  keyboard navigation, and 390-pixel responsive layout without horizontal overflow.
  Browser console showed no errors during the successful flows. A deliberately
  stopped preview server verified visible retry feedback and a re-enabled bookmark
  button without falsely changing saved state. The temporary server was then left
  stopped. No credit/unlock action was exercised in this browser check.
- Seven Node interaction checks cover confirmed saves, duplicate clicks, network,
  session, malformed response, server failure, timeout and saved-list refresh.
- Python compilation, all 11 discovered application JavaScript syntax checks,
  Hindi catalog validation (1,694 messages), and `git diff --check` passed.
- The optional strict frontend auditor is not clean: it reports existing shared
  form/template issues and missing machine-readable ownership metadata (81 findings).
  The lead forms declare server-owned validation; the native select choice follows
  the existing `DESIGN.md` rule and is documented above. No unrelated UI migration
  or tooling-manifest rewrite was performed to silence the global audit.


### Observed Python results

- Initial integration run: 95 passed, 2 failed. The new anonymous-JSON test was
  corrected to the established HTTP 401 contract. The failed-commit regression
  exposed SQLite savepoint release committing before the outer transaction; save
  now uses one normal commit with unique-conflict rollback/recovery instead.
- Both targeted failure reruns: 2 passed, exit 0.
- New feature suite before the last two edge cases: 52 passed, exit 0.
- Final SQL-filter/concurrency/malformed-metadata subset: 16 passed, 38 deselected,
  exit 0. There are 54 new Python cases in total.
- Final full command:
  `.\.venv312\Scripts\python.exe -m pytest tests -q --tb=short -p no:cacheprovider`
  — **466 passed, 22 warnings, exit 0, 534.40 seconds**. Warnings are existing
  SQLAlchemy Query.get, ReportLab AST and datetime.utcnow deprecations.

The branch remains `main` at `7519db8`; changes are unstaged. No commits, pushes,
real-database migrations, stash changes or existing database edits were performed.
