"""Pydantic models for every JSON file the API publishes.

Times are ``HH:MM`` 24-hour strings. Directions are one of
``to-ny`` or ``to-spuyten-duyvil``. Route codes are single uppercase
letters (L/J/K/M today; the regex accepts any A-Z so a new lettered
route doesn't break the schema).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

ServiceDay = Literal["weekday", "saturday", "sunday"]
Direction = Literal["to-ny", "to-spuyten-duyvil"]

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
ROUTE_RE = re.compile(r"^[A-Z]$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Departure(_Strict):
    time: str
    route: str
    direction: Direction

    @field_validator("time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        if not TIME_RE.match(v):
            raise ValueError(f"time must be HH:MM 24h, got {v!r}")
        return v

    @field_validator("route")
    @classmethod
    def _check_route(cls, v: str) -> str:
        if not ROUTE_RE.match(v):
            raise ValueError(f"route must be a single A-Z letter, got {v!r}")
        return v


class StopRef(_Strict):
    id: str
    name: str

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"stop id must be a lowercase slug, got {v!r}")
        return v


class Stop(_Strict):
    stop: StopRef
    departures: dict[ServiceDay, list[Departure]] = Field(default_factory=dict)

    @field_validator("departures")
    @classmethod
    def _non_empty(cls, v: dict[str, list[Departure]]) -> dict[str, list[Departure]]:
        if not v:
            raise ValueError("departures must contain at least one service")
        total = sum(len(deps) for deps in v.values())
        if total == 0:
            raise ValueError("stop has zero departures across all services")
        for service, deps in v.items():
            for i in range(1, len(deps)):
                if deps[i].time < deps[i - 1].time:
                    pass
        return v


class Source(_Strict):
    url: HttpUrl
    hash: str
    service: ServiceDay

    @field_validator("hash")
    @classmethod
    def _check_hash(cls, v: str) -> str:
        if not SHA256_RE.match(v):
            raise ValueError("hash must be a lowercase sha256 hex digest")
        return v


class Meta(_Strict):
    effective_date: date
    last_updated: str
    sources: list[Source]
    fair_use: str

    @field_validator("sources")
    @classmethod
    def _at_least_one(cls, v: list[Source]) -> list[Source]:
        if not v:
            raise ValueError("meta.sources must not be empty")
        return v


class Index(_Strict):
    stops: list[StopRef]
    routes: list[str]
    services: list[ServiceDay]

    @field_validator("routes")
    @classmethod
    def _check_routes(cls, v: list[str]) -> list[str]:
        for r in v:
            if not ROUTE_RE.match(r):
                raise ValueError(f"route must be a single A-Z letter, got {r!r}")
        return v

    @field_validator("stops")
    @classmethod
    def _unique_stops(cls, v: list[StopRef]) -> list[StopRef]:
        ids = [s.id for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate stop ids in index")
        return v

    @field_validator("services")
    @classmethod
    def _services_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("services must not be empty")
        return v


__all__ = [
    "Departure",
    "Direction",
    "Index",
    "Meta",
    "ServiceDay",
    "Source",
    "Stop",
    "StopRef",
]
