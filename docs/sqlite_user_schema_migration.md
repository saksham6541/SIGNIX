# SQLite user schema repair — 20261029_01

## Diagnosis and affected databases

The current `User` model adds `role`, `vendor_status`, `vendor_profile`, and
`vendor_credits_balance`. The former startup initializer only added the four preference columns
to an existing users table. Startup now validates existing schemas without
altering them; a mismatch requires an explicit operator-run migration. `db.create_all()` creates absent tables but does not
add these columns. There is no Alembic/Flask-Migrate setup in this project.
The ORM selects all mapped user columns during login, so the missing role column
caused the reported error before password checking.

At diagnosis on 29 October 2026, port 5000 belonged to the demo launcher process
(PID 26012, launched through `demo_video/start_demo.py`). That launcher explicitly
sets `DATABASE_URL` to the isolated demo file. Normal `run.py` configuration instead
resolved to the root database. SQLite `PRAGMA database_list`, `table_info` and
`sqlite_master` inspection confirmed both file schemas:

| Database (relative to repository root) | Users | Saved locations | Roof designs |
| --- | ---: | ---: | ---: |
| `solar_app.db` | 3 | 5 | 5 |
| `demo_video/demo.sqlite` | 2 | 4 | 3 |

Both had the same four missing columns. All other mapped columns and tables were
present. Existing preference columns were preserved, including existing NULLs.
With user authorization, both databases were migrated and all original column
values across all tables were verified unchanged.

Added columns:

| Column | SQLite declaration | Legacy-row value |
| --- | --- | --- |
| `role` | `VARCHAR(32) NOT NULL DEFAULT 'user'` | Ordinary user, the model default |
| `vendor_status` | `VARCHAR(32)` | NULL; no approval granted |
| `vendor_profile` | `JSON` | NULL |
| `vendor_credits_balance` | `FLOAT NOT NULL DEFAULT 0.0` | Zero; no credits granted |

The model's `ix_users_role` and `ix_users_vendor_status` indexes were also added.
The version is recorded in `signix_schema_migrations`. The migration can also add
the four preference columns to older databases, with NULL defaults except a newly
added `language_preference`, which defaults to the model's `en` value.

## Commands

Run from `D:\Ajay\OneDrive\Desktop\High_cortisol\SIGNIX`:

```powershell
# Read-only inspection; prints the exact selected file, metadata and row counts.
.\.venv312\Scripts\python.exe scripts/migrate_sqlite_users.py --database .\solar_app.db
.\.venv312\Scripts\python.exe scripts/migrate_sqlite_users.py --database .\demo_video\demo.sqlite

# Already applied here. Safe to repeat: reports already_applied, changes nothing.
.\.venv312\Scripts\python.exe scripts/migrate_sqlite_users.py --database .\solar_app.db --apply
.\.venv312\Scripts\python.exe scripts/migrate_sqlite_users.py --database .\demo_video\demo.sqlite --apply
```

Without `--database`, the tool resolves the current Flask configuration, including
environment overrides and Flask's handling of relative SQLite paths. It does not
discover another process's environment: use the explicit file when the server uses
a wrapper or a different environment. Nonexistent files and non-SQLite databases
are rejected instead of silently creating a new database.

## Backups and recovery

Backup directory (ignored by Git):

`D:\Ajay\OneDrive\Desktop\High_cortisol\SIGNIX\instance\migration_backups`

Verified pre-migration backups:

- `solar_app.db.20261029_01_user_roles_preferences.20261029T155658420048Z.1b7c8843.bak`
- `demo.sqlite.20261029_01_user_roles_preferences.20261029T155701946487Z.5330f832.bak`

The tool obtains a `BEGIN IMMEDIATE` write reservation, creates a consistent backup
through SQLite's backup API using a separate read connection, and verifies its
integrity, schema and record fingerprints before any DDL. All additions, indexes
and the version record commit together. Other writers are excluded during the
operation. Lock acquisition has a five-second timeout.

If migration fails, the transaction rolls back; the printed backup is retained.
Do not rerun database initialization, drop tables, delete databases or copy over a
live database. Correct the reported cause (for example backup storage or a locked
database), inspect again, and rerun the migration. Unknown missing tables/columns
are reported and refused; this version does not attempt a generic schema repair.

For recovery investigation, stop the affected server and preserve the current file
and any WAL/SHM sidecars. Copy the appropriate backup to a **new** path, leaving the
current database untouched. For example, for the normal database:

```powershell
$backup = Join-Path (Get-Location) 'instance\migration_backups\solar_app.db.20261029_01_user_roles_preferences.20261029T155658420048Z.1b7c8843.bak'
$recovery = Join-Path (Get-Location) 'instance\solar_app.recovery-review.db'
if (Test-Path -LiteralPath $recovery) { throw 'Choose a new recovery filename; do not overwrite it.' }
Copy-Item -LiteralPath $backup -Destination $recovery
.\.venv312\Scripts\python.exe scripts/migrate_sqlite_users.py --database $recovery
```

The backup intentionally has the old schema. After fixing the failure cause, apply
the migration to the recovery copy before using current application code. If a
switch to the recovered copy is necessary, set `DATABASE_URL` explicitly to
`sqlite:///` plus its absolute path (forward slashes), then start one server. Any
records written after the backup require reconciliation; do not discard them.

The legacy initializer still contains a destructive branch for a much older
`user_locations` table without `user_id`. This repair never calls that initializer;
such a schema is refused for separate ownership/data review.

## Login verification and validation performed

- Six focused regression tests passed, including successful login with a legacy
  password hash, preservation, repeat execution, failure rollback and backup failure.
- Both actual files passed full ORM `User` selection; no missing mapped tables or
  columns remained. Repeat migration returned `already_applied` for both.
- The live port-5000 login form and a CSRF-protected POST with a nonexistent test
  email returned HTTP 200 with the normal invalid-credentials message, not an SQL
  exception. No actual user credentials were requested or changed.
- Python compilation and `git diff --check` passed. No full test suite was run.

To check your own account, ensure you are using the intended server/database, reload
`http://127.0.0.1:5000/login`, and sign in with the existing password. The demo server
contains demo accounts, not the normal database's users. Stop the demo launcher and
any competing Flask instance before starting the normal app:

```powershell
.\.venv312\Scripts\python.exe run.py
```

This repair did not stop or switch running servers. Existing staged work was
preserved; the migration script, focused tests and this document are new files.

## Commercial repair verification

The commercial workflow repair does not apply migrations to either real database.
Regression checks use temporary legacy databases, including a copied fixture,
and verify credentials, locations, rollback, backup integrity and idempotence.
Startup no longer runs automatic ALTER TABLE or drops legacy user_locations.
The explicit user-column migration fails closed on other missing tables/columns;
those require a separate reviewed migration rather than an automatic rebuild.
