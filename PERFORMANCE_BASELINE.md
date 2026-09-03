# Performance Baseline

Date: 2026-09-04
Environment: Local Windows development machine, Flask app running in `solar_app/`
Scope: Baseline request time for the `/api/estimate` endpoint using the same sample rooftop polygon repeated three times.

## Measurement method

- Started the Flask app from `solar_app/run.py`.
- Sent the same POST payload to `http://127.0.0.1:5000/api/estimate` three times.
- Recorded the elapsed wall-clock time on the client side for each run.
- The application has timing logs in the route handler and estimation engine (`[TIMING] ...`) to separate total request time from sub-steps.

## Observed runtime

The same sample payload was sent three times to the running local app. These are
the application timing logs for each request:

| Run     | Roof area calc | Irradiance fetch | Monthly generation calc | Financial projection |  DB save | Total elapsed |
| ------- | -------------: | ---------------: | ----------------------: | -------------------: | -------: | ------------: |
| 1       |        0.35 ms |      18375.09 ms |                 0.06 ms |              0.11 ms | 16.89 ms |   18393.94 ms |
| 2       |        0.09 ms |       1186.92 ms |                 0.04 ms |              0.15 ms | 15.91 ms |    1206.51 ms |
| 3       |        0.10 ms |       1272.70 ms |                 0.04 ms |              0.16 ms | 17.29 ms |    1294.32 ms |
| Average |        0.18 ms |       6944.90 ms |                 0.05 ms |              0.14 ms | 16.70 ms |    6964.92 ms |

The first run was substantially slower because the live irradiance request took
18.4 seconds. The next two runs completed in approximately 1.2–1.3 seconds.

## What the timing labels suggest

The code includes the following runtime checkpoints:

- `/api/estimate total elapsed`
- `/api/estimate DB save elapsed`
- `run_full_estimation roof area calc elapsed`
- `run_full_estimation irradiance fetch elapsed`
- `run_full_estimation monthly generation calc elapsed`
- `run_full_estimation financial projection elapsed`

Average timing-label shares of average total elapsed time are:

| Label                   | Average share |
| ----------------------- | ------------: |
| Roof area calc          |       0.0026% |
| Irradiance fetch        |      99.7126% |
| Monthly generation calc |       0.0007% |
| Financial projection    |       0.0020% |
| DB save                 |       0.2397% |

The **irradiance fetch** had the largest average share of total time at
**99.7126%**. This confirms that the external solar-data request, rather than
the database or in-memory calculations, is the dominant cost in this run.

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
