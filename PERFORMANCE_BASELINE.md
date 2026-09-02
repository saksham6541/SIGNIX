# Performance Baseline

Date: 2026-09-02
Environment: Local Windows development machine, Flask app running in `solar_app/`
Scope: Baseline request time for the `/api/estimate` endpoint using the same sample rooftop polygon repeated three times.

## Measurement method

- Started the Flask app from `solar_app/run.py`.
- Sent the same POST payload to `http://127.0.0.1:5000/api/estimate` three times.
- Recorded the elapsed wall-clock time on the client side for each run.
- The application has timing logs in the route handler and estimation engine (`[TIMING] ...`) to separate total request time from sub-steps.

## Observed runtime

| Run | Total request time | Notes                                                                          |
| --- | -----------------: | ------------------------------------------------------------------------------ |
| 1   |         8415.64 ms | first run, likely includes startup/warm-up and database initialization effects |
| 2   |         1839.46 ms | faster but still elevated                                                      |
| 3   |         1334.91 ms | near steady-state baseline                                                     |

Quick summary:

- Median run: ~1839 ms
- Most recent steady-state run: ~1335 ms
- First run was slower because the app/DB had not fully settled and there may have been cold import and initialization overhead.

## What the timing labels suggest

The code includes the following runtime checkpoints:

- `/api/estimate total elapsed`
- `/api/estimate DB save elapsed`
- `run_full_estimation roof area calc elapsed`
- `run_full_estimation irradiance fetch elapsed`
- `run_full_estimation monthly generation calc elapsed`
- `run_full_estimation financial projection elapsed`

The earlier local measurement captured a total estimate route time of approximately 1492 ms, which is consistent with the later steady-state requests around 1.3–1.8 seconds. The most likely dominant cost is not the database save; it is the irradiance fetch and/or the overall solar calculation pipeline, especially when a live API call is involved.

## Interpretation

This is a valid local baseline, not a production benchmark. It tells us that:

1. The app works end-to-end and returns a result in roughly 1–2 seconds during steady-state use.
2. The request is fast enough that a full architecture rewrite is not justified solely by this metric.
3. A cache or memoization layer becomes worth considering if the same location or surrounding climate data is requested repeatedly.
4. The next optimization step should be to measure the live solar-data fetch specifically and compare it to the in-memory generation and financial logic.

## Recommendation

Do not migrate to a larger stack yet based on this baseline alone. The current stack is acceptable for this stage, and the right next move is incremental optimization:

- cache repeated irradiance lookups by lat/lon bucket or month,
- reduce duplicate network calls,
- keep the Flask app for the MVP while improving the solar-data pipeline,
- only consider a FastAPI + React or larger architecture when the app needs higher concurrency, richer frontend complexity, or multi-user scale.

This baseline is enough to justify performance improvements, but not enough to justify an immediate full rewrite.
