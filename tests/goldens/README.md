# Estimate golden contract

These fixtures compare the complete `run_full_estimation` output, including
provenance and fingerprints. Do not ignore metadata keys or hash differences.
Irradiance is mocked; no provider calls are needed.

## Orientation provenance update — 30 October 2026

Commit `65446cd1d5f345b6e97748084bb22b3ac28d9311` intentionally added the public
`orientation_provenance` field and the calculation assumption
`surface_orientation_model_version: "1.1"`, but did not update these goldens.
The [orientation audit](../../docs/rooftop_orientation_audit.md) explains the
flat-roof and unknown-plane corrections that require this version marker.
`app/estimate.py` copies the estimation result into the JSON response without
filtering out the field. Saved generation provenance also retains the same evidence.

The three cases have no accepted roof-plane metadata. Bangalore uses automatic
footprint orientation (90° east, estimated); both Delhi cases explicitly request
180° south (user specified, unverified). Roof-face azimuth, roof pitch and installed
panel tilt correctly remain unknown. Their factors still use the existing
azimuth-only planning approximation, not a physical tilted-plane yield model.

The exact assumption-hash change is:

- Before: `04e4aad82cb9dc1326dfef0c72fa6469e6a4a77bf2ac08c6cf9cc6970b165acd`
- After: `52165417702c8f7276f1b4c0247957001171c4f296fd21c57758acf0dccd1b92`

Removing **only** `surface_orientation_model_version` from the current assumption
snapshot reproduces the old hash exactly. Replacing **only** that nested hash in
each old `financial_provenance.inputs` dictionary reproduces the new financial
input fingerprint. Both digests use SHA-256 of UTF-8 JSON with sorted keys and
compact separators. No financial input, tariff, cost, subsidy or numerical
assumption changed; the financial hash correctly includes model-version evidence.

Before updating, every other output value was checked for exact equality, beyond
the golden comparator's float tolerance. This included geometry and obstructions,
orientation/factor, monthly and annual generation, battery sizing, savings,
subsidy, O&M, payback, financing and all 25-year projections.

| Case | Capacity kWp | Generation kWh/year | Annual gross savings ₹ | Annual net benefit ₹ | Subsidy ₹ | Net investment ₹ | Payback years |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| bangalore_mid_with_obstruction | 5.5 | 5010.0 | 21492.9 | 17202.9 | 78000 | 279500 | 12.6 |
| delhi_backup_battery | 2.0 | 2789.5 | 23342.54 | 21782.54 | 60000 | 120040 | 5.1 |
| delhi_small_roof | 1.17 | 1631.8 | 8974.9 | 8062.3 | 35100 | 40950 | 4.7 |

These are unchanged deterministic fixture results, not live offers or approved subsidies.
The fixture update adds only `orientation_provenance` and changes the two hash
strings. The full strict comparison remains intact. Additional tests independently
check provenance meanings, JSON serialization, hash reconstruction, and version
changes propagating through both hashes without changing these numerical results.

Focused verification:

```powershell
.\.venv312\Scripts\python.exe -m pytest tests/test_estimate_goldens.py tests/test_surface_generation.py tests/test_financial_realism.py -q
```
