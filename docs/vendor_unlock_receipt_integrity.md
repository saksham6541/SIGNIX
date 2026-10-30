# Installer unlock receipt integrity

## Defect and behavior

The former project deletion route hard-deleted `Project`. The ORM relationship and
SQLite foreign key then cascaded that deletion into `vendor_lead_unlocks`, destroying
the installer’s paid receipt even though the UI promised it would remain. The related
credit ledger row survived only because its project foreign key used `SET NULL`.

The corrected behavior is:

- A project with no installer unlocks can still be deleted.
- A project with one or more unlock receipts is closed/archived instead. It remains in
  the owner’s All and Closed project views and its detail route remains available.
- The owning installer retains the receipt, ledger transaction, outcome history and
  consent-controlled contact access. Other installers gain no access.
- The project foreign key on `vendor_lead_unlocks` uses `ON DELETE RESTRICT`. This is
  the final database guard against accidental deletion outside the normal route.
- Unlock and archive operations serialize on the project row. A purchase cannot race
  the deletion decision, and retrying an existing unlock remains credit-neutral.

No contact data is copied into the receipt or ledger. Referential integrity is
preserved by retaining the archived project rather than creating an orphan or a PII
snapshot.

## Explicit SQLite migration

Fresh databases receive the protected foreign key from the SQLAlchemy model. Existing
SQLite databases require an explicit migration because SQLite cannot alter a foreign
key in place. Application startup validates the SQLite delete action and refuses to
operate on the old cascading schema; it never migrates automatically.

Stop all application writers and identify the exact configured database file. Do not
assume a repository default or demo database is the active database. Inspection is
read-only:

```powershell
.\.venv312\Scripts\python.exe scripts\migrate_vendor_unlock_receipts.py --database "D:\confirmed\path\database.sqlite"
```

After reviewing the output, apply it explicitly:

```powershell
.\.venv312\Scripts\python.exe scripts\migrate_vendor_unlock_receipts.py --database "D:\confirmed\path\database.sqlite" --apply
```

The migration:

1. takes a SQLite write lock and verifies current integrity, foreign keys, columns,
   indexes, uniqueness and absence of unreviewed triggers;
2. creates and verifies a unique online backup under
   `instance/migration_backups/` (or the supplied `--backup-dir`);
3. transactionally rebuilds only `vendor_lead_unlocks`, changing the project action
   from `CASCADE` to `RESTRICT` while preserving every row and ID;
4. recreates the lookup indexes and unique vendor/project constraint;
5. fingerprints all original values, runs integrity/foreign-key checks, records
   version `20261030_02_preserve_vendor_unlock_receipts`, then commits.

Repeating the command returns `already_applied` without creating another backup.
Only the canonical schema produced by this application is migrated automatically.
Unexpected indexes, triggers, constraints, generated columns, extra foreign keys,
`STRICT`, or `WITHOUT ROWID` options are rejected before backup or table rebuild.
The error names the unsupported object where possible and leaves the database
unchanged for manual review.

## PostgreSQL scope

Application-level archival remains active for every dialect: a project with an
installer unlock receipt is archived rather than hard-deleted. At startup, a
non-SQLite database whose unlock foreign key is still cascading emits a warning
instead of preventing the application from starting.

The database-level PostgreSQL constraint migration is deliberately deferred from
this localhost-focused phase and has not been verified against a PostgreSQL server.
Do not run the SQLite migration against PostgreSQL. Until a reviewed PostgreSQL
migration is available, avoid direct database-level project deletion and use only
the application workflow, which preserves receipts and ledger history.

## Recovery

Any error rolls the DDL transaction back and reports the verified backup path. Keep
the application stopped. Inspect both files read-only and run SQLite integrity and
foreign-key checks. Never overwrite the active database in place. Restore the backup
to a separate recovery filename, verify its schema and record counts, reconcile any
writes made after the backup, and only then explicitly point the application at the
recovery file. The backup retains the old `CASCADE` action, so the migration must be
successfully applied before that recovery database is used with this application
version.
