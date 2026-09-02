# Product Requirements Document (PRD)

## Rooftop Solar Estimator — Incremental Improvement Plan

**Document Version:** 2.0 (revised from v1.0)
**Date:** September 2, 2026
**Status:** In Planning
**Owner:** Saksham Kaushik

**Revision note:** v1.0 of this PRD proposed a full microservice rewrite (FastAPI + React + PostgreSQL + Redis + Celery + Docker + CI/CD, 14 weeks, 3.8 FTE, $105–275/month infra). That plan is sized for a production app with real concurrent traffic and a multi-person team. The actual codebase today is a single-developer Flask app (~2,000 lines across `routes.py`, `solar_logic.py`, `models.py`, `report_generator.py`) with a 24 KB SQLite database and no auth or multi-user features. This version keeps the original's useful ideas but rescopes effort, timeline, and infrastructure to match that reality — and treats the bigger migration as an *optional later phase*, not the starting point.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Goals & Objectives](#goals--objectives)
3. [Current State Analysis](#current-state-analysis)
4. [Approach](#approach)
5. [Phased Plan](#phased-plan)
6. [Timeline](#timeline)
7. [Resources & Cost](#resources--cost)
8. [Risks](#risks)
9. [Success Metrics](#success-metrics)
10. [Rollback Strategy](#rollback-strategy)
11. [Optional Future Phase: Full Stack Migration](#optional-future-phase-full-stack-migration)

---

## Executive Summary

### Project Overview

Improve the Rooftop Solar Estimator's performance, code quality, and UX **incrementally**, inside the current Flask/SQLite/Vanilla JS stack, before considering any larger rewrite. Each phase should ship something usable and be individually justifiable — not depend on the rest of the plan being finished to deliver value.

### Business Drivers (revised)

The original drivers (real-time UI, 3–5x performance, 100+ concurrent users, mobile app foundation) assumed production-scale usage that doesn't exist yet. The drivers that actually apply right now:

- **Portfolio/demo quality**: the app should run smoothly and look polished for interviews, demos, or a resume link.
- **Maintainability**: `routes.py` (600 lines) and `solar_logic.py` (834 lines) are large enough to benefit from being split up, even without a framework change.
- **Correctness**: no automated tests currently exist for the solar/financial calculations, which are the core value of the app.
- **Optionality**: don't paint yourself into a corner — keep the door open to scaling later without over-building now.

### Scope (v2.0)

- Profile the app to find *actual* bottlenecks (don't assume them)
- Add a test suite for `solar_logic.py` and the estimation API
- Split `routes.py` into blueprints; move business logic out of route handlers
- Add a lightweight caching layer for external API calls (in-process or SQLite-backed — Redis only if profiling shows it's needed)
- Polish the frontend map/dashboard UX incrementally, in vanilla JS or a small framework — full React rewrite deferred
- Containerize with a single Dockerfile for easy local run / free-tier deployment
- Defer: PostgreSQL, Redis, Celery, WebSockets, microservices, load balancer, paid monitoring — until there's evidence (real users, real latency numbers) that justifies them

### Expected Outcomes

- A documented, tested, easier-to-extend codebase
- Faster perceived performance where profiling shows it matters
- A deployable app (free-tier hosting) with basic monitoring
- A clear, evidence-based trigger list for when to revisit the bigger migration

---

## Goals & Objectives

### Primary Goals

| Goal | Metric | Target |
|---|---|---|
| **Understand current performance** | Profiled response times for `/estimate` and PDF generation | Baseline measured, not assumed |
| **Reliability of calculations** | Test coverage on `solar_logic.py` | ≥ 70% on core calculation functions |
| **Code organization** | `routes.py` size | Split into ≤150-line blueprint modules |
| **External API resilience** | Cache hit rate on repeated lat/lng lookups | Cache added and measured |
| **Deployability** | One-command local run | `docker compose up` works from a clean clone |

### Secondary Goals (nice-to-have, not blocking)

- Basic API docs (Flask-Smorest or manual OpenAPI, no need for a framework switch)
- A simple admin/dashboard view for recent estimations (already partially exists)
- Batch estimation as a script/CLI tool, not a distributed job queue

### Explicitly deferred goals

Role-based access control, multi-property workflows, and a React Native mobile app are removed from scope until there's a user base that needs them. Building for hypothetical future users before real ones exist is a common way small projects stall.

---

## Current State Analysis

### What's actually true about the current codebase

```
✅ Flask app works and has real solar/financial logic (pvlib, shapely-based area calc)
✅ Report generation (WeasyPrint) and PDF export already implemented
✅ SQLite is fine at current scale — it becomes a real problem only under
   concurrent writes, which this app doesn't have evidence of yet
❓ No profiling has been done — the "1500ms API response" and "3-5x
   improvement" figures from v1.0 were not measured, they were assumed
❓ No automated tests exist for solar_logic.py, which is the highest-value
   code in the app (get this wrong and the whole estimate is wrong)
❌ routes.py mixes request handling, business logic, and formatting in one
   600-line file — this is a real maintainability cost independent of framework
❌ No containerization — makes it harder to deploy or share
```

### What to do before trusting any performance claim

Before optimizing anything, measure it:

```bash
# Simple baseline: time the estimate endpoint locally
python -m cProfile -o profile.out run.py
# or, quicker: wrap the route handler with time.perf_counter() and log it
```

Only once there's a real number (e.g. "estimate endpoint takes 900ms, 700ms of which is the NASA POWER API call") does a caching layer or async rewrite become justified — and at that point the fix is usually much smaller than a full framework migration (e.g., caching irradiance lookups, since lat/lng → irradiance rarely changes).

---

## Approach

Rather than a stack swap, this plan treats the current Flask app as the foundation and applies targeted, individually-shippable improvements:

1. **Measure first.** Add lightweight timing/logging before optimizing anything.
2. **Test the logic that matters most.** `solar_logic.py`'s calculations are the product — cover them with unit tests before refactoring around them.
3. **Refactor for size, not for framework.** Split `routes.py` into Flask blueprints (`estimate.py`, `locations.py`, `reports.py`) and move business logic into a `services/` layer — this is a FastAPI-independent improvement the original PRD bundled unnecessarily with the framework switch.
4. **Cache what's expensive, where it's expensive.** If profiling shows the NASA POWER/PVGIS calls dominate latency, cache lat/lng → irradiance results — first in SQLite or `functools.lru_cache`/`diskcache`, not Redis, unless traffic justifies running a separate service.
5. **Containerize for portability**, not for scale — a single `Dockerfile` + `docker-compose.yml` (app + optionally Postgres if you outgrow SQLite) is enough.
6. **Keep the migration option open.** Section 11 below preserves the original FastAPI/React/Postgres/Redis plan as a reference for *when* the evidence (real concurrent users, measured latency, a team to build it) actually supports it.

---

## Phased Plan

### Phase 1: Baseline & Tests — ~1 week (part-time)

**Objectives**
- Measure real performance of `/estimate` and PDF generation
- Add a test suite for `solar_logic.py`'s core functions (area calc, irradiance lookup fallback, financial projections)
- Add basic error logging

**Deliverables**
```
tests/
├── test_solar_logic.py     # polygon area, orientation factor, payback calc
├── test_routes.py          # happy-path + invalid-polygon cases
└── conftest.py
PERFORMANCE_BASELINE.md     # measured numbers, not assumptions
```

**Success criteria**
- ✅ `pytest` runs and passes locally
- ✅ Core `solar_logic.py` functions have test coverage
- ✅ A written baseline: actual measured response times for the main endpoints

---

### Phase 2: Code Organization — ~1 week (part-time)

**Objectives**
- Split `routes.py` into Flask blueprints by concern
- Move business logic (currently mixed into route handlers, e.g. in `report()`) into a thin `services/` layer that both the routes and tests can call directly

**Target structure (Flask, not FastAPI)**
```
app/
├── __init__.py
├── config.py
├── models.py
├── solar_logic.py            # unchanged core logic
├── report_generator.py
├── services/
│   ├── estimation_service.py # wraps solar_logic, keeps routes thin
│   └── location_service.py
├── routes/
│   ├── __init__.py
│   ├── pages.py              # index, dashboard, report views
│   ├── estimate.py           # estimation API
│   └── locations.py          # location CRUD
└── static/ / templates/      # unchanged
```

**Success criteria**
- ✅ No route handler exceeds ~50 lines
- ✅ All existing routes still pass their tests from Phase 1

---

### Phase 3: Caching (evidence-driven) — ~3–5 days

**Objectives**
- Only proceed with this phase if Phase 1's baseline shows external API calls are the actual bottleneck
- Add a cache for lat/lng → irradiance lookups

**Approach**
```python
# app/services/cache.py — start simple, upgrade only if needed
import diskcache

cache = diskcache.Cache("./.cache")

def get_cached_irradiance(lat: float, lng: float):
    key = f"irr:{lat:.4f}:{lng:.4f}"
    return cache.get(key)

def set_cached_irradiance(lat: float, lng: float, data: dict, ttl_days: int = 30):
    key = f"irr:{lat:.4f}:{lng:.4f}"
    cache.set(key, data, expire=ttl_days * 86400)
```
`diskcache` needs no separate server, unlike Redis — appropriate until there's a reason (multiple app instances, need for shared cache across processes) to run Redis.

**Success criteria**
- ✅ Repeated estimates for the same rooftop skip the external API call
- ✅ Cache hit rate logged and visible

---

### Phase 4: Frontend Polish — ~1–2 weeks

**Objectives**
- Address specific UX friction (e.g., full page refresh on map interaction) with targeted fixes, not a framework rewrite
- Options, roughly in order of effort:
  1. Use `fetch()` + partial DOM updates in the existing vanilla JS to avoid full reloads on map edits (addresses the main complaint from v1.0 without React)
  2. If the JS is getting hard to maintain, consider a small reactive layer (e.g., Alpine.js) rather than a full React/TypeScript/Vite toolchain
  3. Full React rewrite only if the app grows enough features that vanilla JS state management genuinely becomes the bottleneck

**Success criteria**
- ✅ Map polygon edits update the estimate without a full page reload
- ✅ No new build toolchain required unless justified in the retro below

---

### Phase 5: Containerize & Deploy — ~3–5 days

**Objectives**
- Make the app runnable with one command
- Deploy somewhere free/cheap for demo purposes

**Deliverables**
```dockerfile
# Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "run:app"]
```
```yaml
# docker-compose.yml — SQLite is fine to start; add postgres service only if you outgrow it
services:
  web:
    build: .
    ports: ["8000:8000"]
    volumes: ["./solar_app.db:/app/solar_app.db"]
```

Deployment target: a free tier (Render, Railway free tier, Fly.io) rather than a load-balanced multi-instance setup.

**Success criteria**
- ✅ `docker compose up` runs the full app from a clean clone
- ✅ App is reachable at a public URL for demos

---

## Timeline

| Phase | Duration | Depends on |
|---|---|---|
| 1. Baseline & Tests | ~1 week | — |
| 2. Code Organization | ~1 week | Phase 1 tests (as a safety net) |
| 3. Caching | ~3–5 days | Phase 1 baseline showing it's needed |
| 4. Frontend Polish | ~1–2 weeks | — (can run in parallel with 2–3) |
| 5. Containerize & Deploy | ~3–5 days | Phases 1–2 |

**Total: roughly 4–6 weeks part-time**, for one person, working around a full-time course load — versus the original's 14 weeks for a 3.8-person team. Phases 3 and 4 aren't strictly sequential and can be reordered based on what profiling in Phase 1 actually shows.

---

## Resources & Cost

### Team

One person (you), part-time around coursework. No dedicated DevOps/QA/PM roles needed at this scale.

### Infrastructure cost

```
Free-tier hosting (Render/Railway/Fly.io free tier): $0
SQLite: $0 (file-based, no hosting cost)
diskcache: $0 (local, no server)
Domain (optional): ~$10-15/year if you want a custom domain
```

Total: **$0–15/year**, versus the original's $105–275/*month*. Revisit paid infra only when free-tier limits actually get hit.

---

## Risks

| Risk | Mitigation |
|---|---|
| **Refactor breaks existing behavior** | Phase 1 tests exist *before* Phase 2's refactor — run them after every change |
| **Scope creep back toward the full rewrite** | Each phase must ship independently; don't start Phase N+1 work before Phase N's success criteria are met |
| **Over-caching without evidence** | Phase 3 is explicitly gated on Phase 1's measured baseline, not assumption |
| **Time pressure from coursework** | Timeline is padded for part-time work; phases are independently useful if you have to pause after any one of them |

---

## Success Metrics

- Test coverage on `solar_logic.py` core functions: ≥ 70%
- `routes.py` split into blueprint modules, none over ~150 lines
- A written, measured performance baseline exists (even if the answer is "it's already fast enough")
- App runs via `docker compose up` from a clean clone
- App is deployed and reachable at a public URL

---

## Rollback Strategy

Because each phase is a small, independent change on top of the working Flask app (not a parallel rewrite), rollback is just standard git practice:

```
1. Each phase = its own branch/PR
2. Merge only after that phase's tests pass
3. If a phase causes regressions, revert that branch — the rest of the app
   (and earlier phases) are unaffected, since there's no parallel stack to
   keep in sync
```

No database migration rollback is needed in this plan, since SQLite is retained unless/until Phase 11 (below) is actually triggered.

---

## Optional Future Phase: Full Stack Migration

The original v1.0 plan (FastAPI + React/TypeScript + PostgreSQL + Redis + Celery + Docker + CI/CD, 14 weeks, 3.8 FTE) is preserved as a reference, **not a current commitment**. Revisit it if and when most of these become true:

- Real concurrent users causing measured SQLite lock contention (not hypothetical)
- A measured need for sub-second response times that caching alone can't meet
- Multi-user features (accounts, roles, saved history) that the current single-table schema can't reasonably support
- A team larger than one person, or enough runway to justify a 2–3 month rewrite
- Budget for $100+/month infrastructure

If that point arrives, the original v1.0 document's architecture diagram, technology stack breakdown, and phase-by-phase FastAPI/React tasks are still a reasonable starting point — ask for it to be restored/expanded at that time.

---

## Document Control

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | Sept 2, 2026 | Copilot | Initial PRD — full microservice migration plan |
| 2.0 | Sept 2, 2026 | Claude (reviewed with Saksham) | Rescoped to match actual project size; original plan preserved as optional future phase |