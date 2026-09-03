from typing import Any

import diskcache

cache = diskcache.Cache("./.cache")


def _irradiance_key(lat: float, lng: float) -> str:
    return f"irr:{lat:.4f}:{lng:.4f}"


def get_cached_irradiance(lat: float, lng: float) -> Any:
    return cache.get(_irradiance_key(lat, lng))


def set_cached_irradiance(
    lat: float,
    lng: float,
    data: Any,
    ttl_days: int = 30,
    ttl_hours: float | None = None,
) -> None:
    ttl_seconds = ttl_hours * 3600 if ttl_hours is not None else ttl_days * 86400
    cache.set(_irradiance_key(lat, lng), data, expire=ttl_seconds)
