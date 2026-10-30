# Public business landing and consultation requests

The public business entry page is served at `/business` and `/commercial`.
Its estimate actions enter the existing authenticated `/business/estimate`
assessment; the signup action carries that destination through authentication.
The map, roof/obstruction geometry, estimation engine, and Buy/Loan/PPA
comparison remain the existing commercial workflow.

The tariff panel calls `estimation_service.lookup_tariff_defaults` for the
business track. That service currently returns `DEFAULT_COMMERCIAL_TARIFF`
from application configuration and explicitly treats it as an unverified,
configurable planning default. It is not a state/utility tariff, has no
effective period, and must not be represented as an official published rate.
The visitor should confirm the applicable bill rate and operating assumptions.
Commercial calculations do not inherit residential subsidy eligibility.

## Consultation workflow

`GET /consultation` renders the public form. `POST /consultation` validates
the contact, location, energy-use, consent, and requirement fields on the
server, enforces the application CSRF token, and saves the request before
showing success. A unique per-form submission token makes an accidental
repeat idempotent. A database error is returned as a visible failure; it is
not reported as a successful request or appointment.

Administrators can review requests at `/admin/consultations` and update their
status to pending, contacted, scheduled, or completed with a staff note.
Both administration routes require the existing `admin` role. Consultation
details are not exposed through public routes or customer dashboards. Status
`scheduled` records staff workflow state; it does not itself send an invitation
or confirm a calendar booking.

## Database schema

The additive `consultation_requests` table is introduced by migration
`20261030_02_consultation_requests`. New empty databases receive the table
through normal initial schema creation. Existing databases are schema-validated
at startup and are not changed automatically. Before starting an existing
installation on this version, stop application writes and make an operator
backup, then inspect the exact approved database:

```powershell
python scripts\migrate_consultation_requests.py --database .\approved-existing.sqlite
```

Only after reviewing that inspection and verifying a separate backup should an
operator explicitly apply the migration:

```powershell
python scripts\migrate_consultation_requests.py --database .\approved-existing.sqlite --apply
```

The migration itself creates and verifies an online backup before adding the
new table, records its version, preserves existing records, and is repeatable.
Do not point these commands at an unrelated teammate or production database.

This migration and `migrate_vendor_saved_leads.py` share the
`signix_schema_migrations` table. Separate pristine SQLite rehearsals confirmed
that either migration can run first; there is no ordering dependency between
these two additive changes.
