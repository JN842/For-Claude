"""Versioned, effective-dated market-rules registry.

Every rule is a YAML record with an effective-date range and a verification
status.  A lookup returns the rule *together with* how trustworthy it is for
the requested date, so callers (the execution simulator, reports) can count
how many simulated decisions relied on unverified or historically-assumed
rules instead of silently treating today's rulebook as if it had always been
in force.

Verification statuses
---------------------
verified_official     Read on an official exchange web page or rulebook (the
                      source id and access date are recorded).
secondary_unverified  Taken from secondary knowledge (vendor docs, memory of
                      circulars, broker notes).  Must be confirmed before use
                      in any real-money context.
assumption            A modelling assumption made because no rule could be
                      verified (e.g. a commission rate, which is negotiated).
unknown               The rule is known to exist but its value could not be
                      established.  Lookups return ``value=None``.

Historical coverage
-------------------
``effective_from: null`` means the start date is *not known*.  The registry
returns such a rule for earlier dates but flags ``historically_assumed=True``
whenever the requested date is earlier than ``verified_as_of`` – i.e. the
rule was only observed to be in force on ``verified_as_of`` and we are
assuming it also applied before.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

VERIFICATION_STATUSES = ("verified_official", "secondary_unverified", "assumption", "unknown")
REGISTRY_DIR = Path(__file__).resolve().parent / "registry_data"


class RuleNotFound(KeyError):
    pass


def _to_date(x) -> dt.date | None:
    if x is None or x == "":
        return None
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x))


@dataclass(frozen=True)
class Source:
    id: str
    url: str
    title: str
    accessed: dt.date | None
    fetched_ok: bool
    notes: str = ""


@dataclass(frozen=True)
class RuleVersion:
    market: str
    key: str
    value: Any
    status: str
    effective_from: dt.date | None
    effective_to: dt.date | None  # inclusive; None = open-ended
    verified_as_of: dt.date | None
    source: str | None
    notes: str = ""

    def covers(self, d: dt.date) -> bool:
        if self.effective_from is not None and d < self.effective_from:
            return False
        if self.effective_to is not None and d > self.effective_to:
            return False
        return True


@dataclass(frozen=True)
class RuleLookup:
    """Result of a registry lookup for one (market, key, date)."""

    rule: RuleVersion
    date: dt.date
    historically_assumed: bool

    @property
    def value(self) -> Any:
        return self.rule.value

    @property
    def status(self) -> str:
        return self.rule.status

    @property
    def trusted(self) -> bool:
        """Verified on an official source AND known to apply on this date."""
        return self.rule.status == "verified_official" and not self.historically_assumed

    def label(self) -> str:
        lab = self.rule.status
        if self.historically_assumed:
            lab += "+historically_assumed"
        return lab


@dataclass
class MarketRules:
    market: str
    exchange_name: str
    timezone: str
    currency: str
    registry_version: str
    sources: dict[str, Source] = field(default_factory=dict)
    rules: dict[str, list[RuleVersion]] = field(default_factory=dict)

    def versions(self, key: str) -> list[RuleVersion]:
        if key not in self.rules:
            raise RuleNotFound(f"{self.market}: no rule '{key}'")
        return self.rules[key]

    def lookup(self, key: str, date: dt.date | str) -> RuleLookup:
        d = _to_date(date)
        matches = [r for r in self.versions(key) if r.covers(d)]
        if not matches:
            raise RuleNotFound(f"{self.market}: rule '{key}' has no version effective on {d}")
        if len(matches) > 1:
            raise ValueError(f"{self.market}: rule '{key}' has overlapping versions on {d}")
        r = matches[0]
        assumed = (
            r.status != "assumption"
            and r.effective_from is None
            and (r.verified_as_of is None or d < r.verified_as_of)
        )
        return RuleLookup(rule=r, date=d, historically_assumed=assumed)

    def get(self, key: str, date: dt.date | str, default: Any = None) -> Any:
        try:
            return self.lookup(key, date).value
        except RuleNotFound:
            return default

    def keys(self) -> list[str]:
        return sorted(self.rules)


def _parse_market(doc: dict) -> MarketRules:
    mr = MarketRules(
        market=doc["market"],
        exchange_name=doc["exchange_name"],
        timezone=doc["timezone"],
        currency=doc["currency"],
        registry_version=str(doc["registry_version"]),
    )
    for s in doc.get("sources", []):
        mr.sources[s["id"]] = Source(
            id=s["id"], url=s["url"], title=s.get("title", ""),
            accessed=_to_date(s.get("accessed")), fetched_ok=bool(s.get("fetched_ok", False)),
            notes=s.get("notes", ""),
        )
    for r in doc.get("rules", []):
        status = r["status"]
        if status not in VERIFICATION_STATUSES:
            raise ValueError(f"{mr.market}/{r['key']}: bad status {status}")
        src = r.get("source")
        if src is not None and src not in mr.sources:
            raise ValueError(f"{mr.market}/{r['key']}: unknown source {src}")
        if status == "verified_official":
            if src is None or not mr.sources[src].fetched_ok:
                raise ValueError(
                    f"{mr.market}/{r['key']}: verified_official requires a successfully fetched source"
                )
        value = r.get("value")
        if status == "unknown":
            value = None
        rv = RuleVersion(
            market=mr.market, key=r["key"], value=value, status=status,
            effective_from=_to_date(r.get("effective_from")),
            effective_to=_to_date(r.get("effective_to")),
            verified_as_of=_to_date(r.get("verified_as_of")),
            source=src, notes=r.get("notes", ""),
        )
        if rv.effective_from and rv.effective_to and rv.effective_to < rv.effective_from:
            raise ValueError(f"{mr.market}/{rv.key}: effective_to before effective_from")
        mr.rules.setdefault(rv.key, []).append(rv)
    for key, vs in mr.rules.items():
        _check_no_overlap(mr.market, key, vs)
    return mr


def _check_no_overlap(market: str, key: str, versions: Iterable[RuleVersion]) -> None:
    lo = dt.date.min
    hi = dt.date.max
    ivs = sorted(((v.effective_from or lo), (v.effective_to or hi)) for v in versions)
    for (a0, a1), (b0, _b1) in zip(ivs, ivs[1:]):
        if b0 <= a1:
            raise ValueError(f"{market}/{key}: overlapping effective ranges")


class RuleRegistry:
    """All markets' rules, loaded from ``registry_data/*.yaml``."""

    def __init__(self, directory: Path | str = REGISTRY_DIR):
        self.directory = Path(directory)
        self.markets: dict[str, MarketRules] = {}
        for path in sorted(self.directory.glob("*.yaml")):
            with open(path, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            mr = _parse_market(doc)
            self.markets[mr.market] = mr

    def __getitem__(self, market: str) -> MarketRules:
        return self.markets[market]

    def lookup(self, market: str, key: str, date) -> RuleLookup:
        return self.markets[market].lookup(key, date)

    def audit_rows(self) -> list[dict]:
        rows = []
        for m, mr in self.markets.items():
            for key in mr.keys():
                for v in mr.rules[key]:
                    src = mr.sources.get(v.source) if v.source else None
                    rows.append({
                        "market": m, "key": key, "status": v.status,
                        "effective_from": v.effective_from, "effective_to": v.effective_to,
                        "verified_as_of": v.verified_as_of,
                        "source": v.source, "source_url": src.url if src else None,
                        "value": v.value, "notes": v.notes,
                    })
        return rows

    def version_hash(self) -> str:
        import hashlib
        h = hashlib.sha256()
        for path in sorted(self.directory.glob("*.yaml")):
            h.update(path.read_bytes())
        return h.hexdigest()[:12]


_DEFAULT: RuleRegistry | None = None


def default_registry() -> RuleRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = RuleRegistry()
    return _DEFAULT
