# SIGNIX Integrated QA Report

## Summary

The integrated Parts 1, 2, 3, and 5 workflows were exercised using fictitious accounts and a disposable SQLite database. The focused automated checks passed, and the tested browser workflows behaved as expected. No confirmed product defects were found in the tested scope.

This was a QA-only pass: no application source, tests, configuration, or database migrations were changed or committed. The only repository file created for this task is this report.

## Checkout and database safeguards

- Expected branch: `feature/public-commercial-landing`.
- Actual branch: `qa/integration-audit`, at integrated commit `6909821` (`Merge commercial workspace and public landing`).
- The worktree was clean before this audit. No branch switch, commit, or push was performed.
- The `.env` fallback points to `solar_app.db`; the process `DATABASE_URL` was explicitly set before app initialization. The teammate database was not used.
- `solar_app_team_dev.db` was inspected and schema-validated: SQLite integrity was `ok`, with 16 tables and no users, projects, or consultation requests. The table set remained unchanged.
- Browser testing used `C:\Users\DELL\AppData\Local\Temp\SIGNIX_QA\signix_qa_6272f9e06dad4418b351c73205b3ed25.db`, initialized with fictitious accounts and projects. It was retained after the run. No migrations were run against either `solar_app_team_dev.db` or `solar_app.db`.
- Migration tests used temporary databases.
- The disposable application server on port 5052 was stopped after testing.

## Results

### Public commercial experience

- `/`, `/business`, `/commercial`, and `/consultation` returned HTTP 200.
- `/business/estimate` redirected unauthenticated visitors to login while preserving `/business/estimate` as the destination.
- “Start Commercial Estimate” reached the existing assessment route. “Get Started” linked to the existing signup/assessment workflow; login/signup destination preservation was also covered by focused tests.
- Consultation messaging correctly described a request submission, not a booked appointment.
- The landing page showed the configured reference value **₹10.00/kWh** for India’s commercial planning default, attributed it to `DEFAULT_COMMERCIAL_TARIFF`, stated that it was not an official published tariff, and indicated that no effective period was provided. It did not present the default as a user-confirmed or utility-specific tariff.
- English and Hindi page rendering and navigation were verified. The automated Hindi catalog validation reported 1,879 active messages passing.

### Consultation workflow and administration

- Browser submission persisted a fictitious request, and the authorized admin workflow displayed and updated it from `pending` to `contacted` with an admin note.
- The public confirmation did not expose the submitted business details.
- Isolated form checks rejected invalid phone, email, PIN, and requirements values with HTTP 400 and field messages. A POST without a CSRF token also returned HTTP 400.
- Valid residential and industrial requests persisted. Reposting the same submission token did not create a duplicate.
- Focused tests also verified persistence-after-commit behavior, failed-write messaging without success, duplicate protection, and consultation administration authorization.

### Business workspace and installer workflows

- Fictitious business owners saw their own project data; a cross-owner project-detail request returned 404. Project status and portfolio filtering were exercised; totals remained consistent with the filters, and planned capacity remained distinct from installed capacity.
- Vendor A saw masked lead contact details, saved a lead, unlocked it for one credit, and then saw contact details. Its follow-up outcome persisted after refresh.
- Vendor B saw no contact details and an empty saved-leads view. Consultation records were not exposed to customers.
- The intentional cross-owner 404 in the browser log was an expected authorization result, not a defect.
- The QA business fixture explicitly used `monthly_savings=0`; its displayed ₹0 estimate is fixture data, not a product-calculation finding.

### Responsive visual check

- The public commercial page was inspected in the integrated browser at desktop and 390px mobile widths. At 1280px, the document width was 1270px; at 390px, it was 380px. Neither viewport had horizontal overflow.
- Desktop navigation, the mobile menu control, hero image, content sections, and CTA presentation were visually inspected. Screenshots were captured inline in the QA browser session; no screenshot files were retained in the repository.

## Automated checks

| Check | Result |
|---|---:|
| Focused pytest batch covering consultation, auth, routes, estimate auth, project status, vendor leads, and credits | **168 passed** in 486.75s |
| User-schema and saved-lead migration test selection, using temporary databases | **11 passed, 51 deselected** |
| Node browser-side commercial/lead tests | **8 passed** |
| Hindi catalog validation | **1,879 active messages passed** |
| Python compilation and JavaScript syntax checks | **Passed** |
| Additional isolated consultation checks | **Passed**: validation, CSRF, residential/industrial persistence, duplicate-token idempotency |

No full 466+ test suite or repeated browser E2E suite was run.

## Findings and limitations

1. **Checkout branch mismatch:** QA ran on `qa/integration-audit`, not the requested `feature/public-commercial-landing` branch. The checkout was already at the stated integrated commit `6909821`. To preserve the no-switch/no-merge safeguards, the branch was not changed. This is a process limitation, not an application defect.
2. The commercial assessment route and authentication handoff were checked, but the assessment/calculation engine was not revalidated as part of this audit; that remains with its separate implementation phase.
3. Commercial tariff display remains a configurable reference default rather than an official state/utility-specific tariff schedule. Users must confirm their applicable tariff.
4. Testing used fictitious local data and does not establish production behavior or integration with external scheduling services. No scheduling integration was represented as active.
5. No screenshot exports are included with this report; the desktop and mobile captures were reviewed inline in the QA session.
