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

## Integration and Part 4 follow-up

This addendum preserves the original QA record in this document and records later integration and browser work. It supersedes earlier broad statements where the later browser evidence below reproduces a defect.

### Environment and workflow evidence

- Part 4 was merged into `feature/public-commercial-landing` at merge commit `53801b6`. The QA report was merged at `31f47bd`. No merge to `main` or deployment was performed.
- Browser assessment and installer checks used the disposable database `C:\Users\admin\AppData\Local\Temp\SIGNIX_merge_qa_3215dbaf0f4645dd8a617891b971cd21.db` and fictitious owner/vendor accounts. The original `solar_app.db` was not used. No migration was run against the browser database.
- The commercial assessment accepted a manually drawn roof polygon and obstruction. The readout showed 931.10 m² total roof area, 90.52 m² obstructed area, and 840.58 m² usable area. The saved assessment used an explicitly test-only `test_fixture` irradiance source, low confidence, 8 kW sizing, and 11,131.3 kWh annual generation. These fixture-backed values are not real-world estimates.
- CAPEX and Loan estimates rendered. An entered PPA rate of ₹5/kWh produced an indicative projection; the unavailable 25-year PPA total remained unavailable. Browser review found the result wording could imply a quote despite the user-entered rate. The template now labels this value “Indicative PPA rate … not an installer quote”; a focused Python regression test verifies the exact output. The edited wording has not been rechecked in a restarted browser session.
- Saving the assessment with both required consent choices created a Business Project. The owner could transition it to Open to Vendors with a reason, and the status history recorded the change. A different owner received 404 for the project detail route.
- Before unlock, the installer saw masked contact details. After consented unlock, the authorized vendor’s balance decreased by one credit and contact details became visible. A second vendor could not see those details. These checks used fictitious accounts and test-only location/service-area metadata in the disposable database.
- No screenshot files were retained in the repository.

### Reconciled findings

| Finding | Follow-up status | Evidence |
|---|---|---|
| Commercial tariff provenance | **Fixed; automated regression passed.** A location lookup had placed an unverified commercial default in the input, making it appear user-entered. The frontend now leaves that field blank unless the tariff is confirmed. | Browser testing exposed the provenance mismatch; `tests/commercial_assessment.test.js` now verifies an unconfirmed commercial default does not populate the input. The changed behavior was not rerun in a browser after the fix. |
| PPA rate wording | **Fixed in template; automated regression passed; browser recheck unverified.** | The result now calls a supplied rate indicative and explicitly says it is not an installer quote. `tests/test_commercial_repair.py` asserts the exact wording. |
| Business Projects mobile horizontal overflow | **Reproduced; unresolved.** | At 390 px, observed document widths were 524 px for the project listing; the commercial survey was 592 px and vendor lead detail was 452 px. The 1440 px desktop views did not overflow. |
| Project-card location inconsistency | **Reproduced; unresolved.** | The assessment saved a selected address, but the project card displayed “Not provided.” In the tested navigation sequence the assessment also fell back to “Selected rooftop”; location state did not carry over from the separate route. |
| Search empty-state wording / PIN search | **Reproduced; unresolved.** | Searching PIN `110004` returned no project, and the empty state incorrectly said “You have not created a project yet.” |
| Installer unlock and contact privacy | **Passed in disposable browser workflow.** | Contact was masked before unlock, revealed only to the unlocking vendor after consent, and not revealed to the second vendor. |
| Migration-order dependency | **Not reproduced; remains unverified.** | The full automated suite passed, including migration tests, but no ordered migration rehearsal was run against a disposable database during this follow-up. Do not treat the dependency as resolved. |
| Commercial PPA wording in Hindi | **Translation catalog updated and validated.** | Hindi translation validation reports 1,886 active messages with placeholder and compiled-catalog parity checks passing. |

### Follow-up automated checks

| Check | Result |
|---|---:|
| Focused commercial, project status, vendor lead, and credit tests | **136 passed** |
| Full Python suite: `python -m pytest tests -q` | **515 passed, 0 failed** (326 warnings) |
| Node suite: `node --test tests/*.test.js` | **12 passed, 0 failed** |
| Python compilation and JavaScript syntax checks | **Passed** |
| Hindi translation validation | **1,886 active messages passed** |

The Wtforms, SQLAlchemy legacy query, ReportLab, and `datetime.utcnow()` deprecation warnings remain non-failing pre-existing warnings.

## Initial findings and limitations (before Part 4 follow-up)

1. **Checkout branch mismatch:** QA ran on `qa/integration-audit`, not the requested `feature/public-commercial-landing` branch. The checkout was already at the stated integrated commit `6909821`. To preserve the no-switch/no-merge safeguards, the branch was not changed. This is a process limitation, not an application defect.
2. The commercial assessment route and authentication handoff were checked, but the assessment/calculation engine was not revalidated in that initial audit. The later Part 4 follow-up above documents its browser workflow.
3. Commercial tariff display remains a configurable reference default rather than an official state/utility-specific tariff schedule. Users must confirm their applicable tariff.
4. Testing used fictitious local data and does not establish production behavior or integration with external scheduling services. No scheduling integration was represented as active.
5. No screenshot exports are included with this report; the desktop and mobile captures were reviewed inline in the QA session.

## Final QA remediation

This addendum preserves the earlier findings and records their final status after remediation on `feature/public-commercial-landing`, based on baseline `9163c0c`. Browser workflows used fictitious accounts and the disposable database `C:\Users\admin\AppData\Local\Temp\SIGNIX_final_qa_9163c0c_build.db`; the original `solar_app.db` was not used.

| Original finding | Code change | Regression test | Browser verification | Final status |
|---|---|---|---|---|
| Authenticated Business Projects pages overflow horizontally on mobile. | The authenticated sidebar is off-canvas below 720px; the toggle remains operable; cards, grids, text, financial details, and comparison content are constrained or locally scrollable. No global `overflow-x: hidden` was added. | No automated viewport-specific test; responsive behavior was measured in the browser. | `/business/projects` and `/business/projects/2` were checked at 390×844, 430×932, and 1440×900. Document `scrollWidth` equaled `clientWidth` at every size (380, 420, and 1430px respectively; the difference from viewport width is the scrollbar). The detail comparison table remained within its mobile parent. | **PASS** |
| Project cards could say “Not provided” while the detail page had an address. | Both views now use the same persisted address-first location label, with persisted city/PIN as the only fallback; missing metadata remains “Not provided.” | `test_business_project_location_matches_between_card_and_detail` covers populated and missing metadata. | A populated project address and structured city/PIN were confirmed on the card and detail view. | **PASS** |
| Empty search results were incorrectly described as an empty portfolio. | The listing distinguishes a genuinely empty portfolio, no search matches, no selected-status matches, and combined search/status misses; filtered empty states link to clear all criteria. | `test_business_project_empty_states_distinguish_portfolio_search_and_status` covers all four states. | With persisted projects, search, closed-status, and combined filters each rendered the matching message and `/business/projects` reset link. The truly empty portfolio branch was verified by the automated test, not a second browser account. | **PASS** |
| Consultation and vendor-saved-lead migrations might depend on order. | Migration history verification now allows each migration’s own history-row insertion while still detecting changes to preexisting records. Documentation records that no order is required. | `tests/test_migration_order.py` rehearses both orders on separate pristine SQLite fixtures, verifies both backups and preserved records/integrity, and induces rollback failures to verify schema rollback and retained backups. The focused migration batch passed. | Not applicable; this is a database migration check, exercised only on disposable SQLite fixtures. | **PASS** |
| Commercial tariff provenance, assumptions, and unavailable values needed rechecking. | No commercial estimation-engine change was made in this remediation. Existing result provenance distinguishes user-supplied tariff from the configurable default and labels financial estimates as planning assumptions. | `test_commercial_assessment_persists_tariff_provenance_and_creates_project` covers both tariff bases; the complete Python suite also passed. | A location lookup without a genuine tariff source left the input blank. A no-tariff assessment rendered the configured ₹10.00/kWh default as “SIGNIX configurable default; confirm against your utility bill.” An explicit tariff was labeled user-supplied. The fixture-backed resource source was `test_fixture` (low confidence), not real irradiance data. CAPEX/Loan/PPA output rendered; unavailable values remained unavailable. | **PASS** |
| PPA output could be mistaken for an installer quote or guaranteed long-term value. | Existing result wording identifies supplied PPA rates as indicative and not an installer quote; unavailable contract-period totals are not inferred. | `test_commercial_repair.py` asserts the exact PPA wording and unavailable-value behavior. | Browser results displayed “Indicative PPA rate: ₹5.00/kWh; not an installer quote”; unavailable 25-year PPA totals remained unavailable. | **PASS** |
| Installer save/unlock, credit deduction, privacy, and outcome isolation required rechecking. | No installer workflow code was changed in this remediation. | Existing vendor lead and credit tests ran in the full suite. | Installer A saved, refreshed, and unsaved the lead, then unlocked it: 10.0 credits became 8.0 for the 2.0-credit lead. A repeated unlock returned `already_unlocked` and created no second deduction; the ledger contained one -2.0 transaction. Contact appeared only after unlock. A’s “Follow-up needed” outcome survived refresh, while the project remained `open_to_vendors`. Installer B discovered the lead but had no saved-lead record, saw no contact details, and received “You do not have enough credits to unlock this lead”; balance remained 0.0 with no unlock or ledger row. | **PASS** |
| Project ownership must be enforced for details. | No ownership code was changed in this remediation. | Existing project ownership tests ran in the full Python suite. | A different business owner’s direct request for `/business/projects/2` returned 404. | **PASS** |

### Final remediation checks

| Check | Result |
|---|---:|
| Focused project and migration regression batch | **28 passed, 0 failed** |
| Full Python suite: `python -m pytest tests -q` | **521 passed, 0 failed** (328 warnings) |
| Node suite: `node --test tests/*.test.js` | **12 passed, 0 failed** |
| Hindi translation validation | **1,890 active messages; coverage, placeholders, and compiled-catalog parity passed** |
| Python compilation: `python -m compileall -q app tests scripts` | **Passed** |
| JavaScript syntax checks | **13 application JavaScript files passed** |

No unresolved defect was reproduced in the requested remediation scope. The estimate browser run used an explicit test fixture, and configurable commercial tariffs still require user verification against the applicable utility bill. Existing non-failing deprecation warnings remain.
