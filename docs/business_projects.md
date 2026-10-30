# Business projects workspace — Part 3

The workspace reuses the saved `Project`, `UserLocation`, commercial assessment
extras, `VendorLeadUnlock`, and `ProjectStatusLog` records. It does not run a
second assessment or financial calculation engine.

## Routes and authorization

| Route | Behavior |
| --- | --- |
| `GET /business/projects` | Lists only the signed-in user's persisted business projects; supports name, structured city/PIN search and lifecycle filters |
| `GET /business/projects/<project_id>` | Shows the owned project's saved assessment, available estimates and status history |
| `POST /business/projects/<project_id>/status` | Transitions an owned business project through `Project.transition_status` |
| `GET /business/estimate` | Existing commercial assessment route linked by New Assessment |

Business workspace pages allow user and administrator roles but always scope records
to the current owner's user ID; vendors are denied. A business detail also verifies
that its associated location belongs to that same owner and is a persisted business
assessment. Transition permissions come from the existing project lifecycle model.
The transition form requires a reason, includes the standard CSRF token, and the
status log retains actor role/ID, prior and new status, timestamp, and reason.

## Portfolio data

Project counts and status counts are computed from the owner's business projects,
not the active filter or search. The Vendor Connected filter includes `vendor_connected`,
`decision`, and `installation`; status counts still show each persisted lifecycle
status separately. Search uses the persisted project/site name and structured city
and PIN fields; it does not infer a city or PIN from a street address.

Planned capacity uses a positive persisted system size, or the explicit legacy
`system_size_kw` value. Annual savings use a persisted commercial annual net benefit,
or a persisted monthly savings value when no annual value exists. Installer interest
counts actual vendor unlock records. Missing or invalid values are shown as
unavailable, not as zero.

There is no canonical installed-capacity column or metadata written by the current
workflow. The portfolio therefore sums only an explicitly persisted
`installed_capacity_kw` value when present; it never equates planned capacity with
installed capacity. Commercial model comparisons continue to use the existing
read-only commercial calculation adapter, and residential calculations are not
used for business records.

## Verification

Focused checks:

```powershell
.\.venv312\Scripts\python.exe -m pytest tests\test_project_status.py tests\test_commercial_repair.py -q
git diff --check
```

These are fixture-database tests. Browser/E2E verification and the full test suite
are deferred to Part 6.
