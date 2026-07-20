"""Shared deterministic primitives for the synthetic-data portfolio.

All entity pools (cities, countries, source codes, etc.) are FICTIONAL and are
defined only in this module. No name, code, or identifier is copied from any
real dataset. The shared seed protocol guarantees byte-identical regeneration.

Design rules enforced here:
- A single root seed is the only source of entropy.
- Every project derives its own sub-seed deterministically from the root.
- Entity pools are bounded and small enough to demonstrate joins, filters,
  parameters, and negative cases without pretending to be a real corpus.
- No date, name, or code in this module references a real organization.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List

# Root seed protocol -------------------------------------------------------
# The root seed is a documented constant. Changing it changes every output.
# Sub-seeds are derived by hashing (root_seed || project_id || purpose).
ROOT_SEED = "portfolio-synthetic-root-2026-07-17"


def derive_seed(project_id: str, purpose: str) -> int:
    """Derive a deterministic integer sub-seed from the root seed.

    The derivation is content-addressable: the same (project_id, purpose)
    always yields the same integer, independent of process or machine.
    """
    material = f"{ROOT_SEED}|{project_id}|{purpose}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def make_rng(project_id: str, purpose: str) -> random.Random:
    """Return a deterministic, independent `random.Random` for one purpose."""
    return random.Random(derive_seed(project_id, purpose))


def snapshot_id(project_id: str) -> str:
    """A stable snapshot identifier for a project's current synthetic release.

    Format: 'syn-<project_id>-<short_hash_of_root>'. Not a timestamp, so
    regeneration is reproducible across runs.
    """
    short = hashlib.sha256(ROOT_SEED.encode("utf-8")).hexdigest()[:12]
    return f"syn-{project_id}-{short}"


# Shared fictional entity pools -------------------------------------------
# These pools are deliberately generic. Cities and countries use synthetic
# ISO-like codes that do not collide with real ISO 3166 codes (real codes are
# 2-3 uppercase letters from A-Z; we use a prefix that cannot be a real code).

FICTIONAL_COUNTRY_PREFIX = "Z"  # No real ISO 3166-1 alpha-2 code starts with Z
                                # and is in ordinary use for the domains here;
                                # codes below are 3-letter and prefixed.

COUNTRIES: List[Dict[str, str]] = [
    {"country_code": "ZNA", "country_name": "Northland",   "subregion": "Coastal North", "income_tier": "high"},
    {"country_code": "ZSA", "country_name": "Southmark",   "subregion": "Coastal South", "income_tier": "upper-middle"},
    {"country_code": "ZEA", "country_name": "Eastvale",    "subregion": "Inland East",   "income_tier": "lower-middle"},
    {"country_code": "ZWR", "country_name": "Westreach",   "subregion": "Coastal West",  "income_tier": "high"},
    {"country_code": "ZCA", "country_name": "Capetop",     "subregion": "Highland",      "income_tier": "upper-middle"},
    {"country_code": "ZPL", "country_name": "Plainsford",  "subregion": "Inland East",   "income_tier": "lower-middle"},
]


def fictional_cities(n: int = 30, project_id: str = "shared") -> List[Dict[str, Any]]:
    """Return `n` deterministic fictional cities distributed across COUNTRIES.

    City codes use the prefix 'CTY-' + a zero-padded integer so they cannot be
    mistaken for real IATA/UN/LOCODE codes. City names are generated from a
    small fictional name pool, extended deterministically.
    """
    rng = make_rng(project_id, "fictional_cities")
    name_stems = ["Aren", "Bela", "Cori", "Duna", "Elms", "Faro", "Glen",
                  "Hale", "Iris", "Jora", "Kell", "Lorn", "Mira", "Ness",
                  "Orin", "Pell", "Quil", "Rona", "Sola", "Tarn", "Ulia",
                  "Vesh", "Wick", "Xan", "Yale", "Zara", "Bran", "Cael",
                  "Dova", "Esha"]
    suffixes = ["ville", "port", "ford", "ton", "haven", "field", "burg", "stead"]
    cities: List[Dict[str, Any]] = []
    # Deterministic assignment of cities to countries, then to subregions/tiers
    for i in range(n):
        country = COUNTRIES[i % len(COUNTRIES)]
        name = f"{name_stems[i % len(name_stems)]}{suffixes[(i // len(name_stems)) % len(suffixes)]}"
        cities.append({
            "city_id": f"CTY-{i + 1:03d}",
            "city_name": name,
            "country_code": country["country_code"],
            "country_name": country["country_name"],
            "subregion": country["subregion"],
            "income_tier": country["income_tier"],
        })
    return cities


# Generic enum vocabularies (shared across projects) -----------------------
RECORD_FAMILY_ENUM = ["observed", "estimated", "projected", "composite", "reference"]
UNIT_ENUM = ["ratio", "percent", "integer_count", "amount", "mixed", "not_applicable"]
OBSERVATION_TYPE_ENUM = ["observed", "projected", "reference", "not_applicable", "mixed"]
SCENARIO_ENUM = ["baseline", "diversified", "core"]
SURFACE_ENUM = [
    "peer_map_and_table",
    "why_this_peer",
    "closest_vs_diversified",
    "context_after_matching",
]
STABILITY_BADGE_ENUM = ["stable under audited variants", "unstable scenario - inspect alternatives"]
DISPOSITION_ENUM = ["open_current_snapshot", "remediated_in_typed_pipeline",
                    "corrected_source_contract", "verified_guard"]


# IO helpers ---------------------------------------------------------------
def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    """Write rows to CSV in a deterministic order: sorted by fieldnames list.

    Uses temp-file-then-rename so partial writes are never observed.
    Floats are formatted with fixed precision to keep bytes stable across runs.
    """
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n",
                                extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt_cell(k, row.get(k)) for k in fieldnames})
    tmp.replace(path)


def _fmt_cell(key: str, value: Any) -> Any:
    if isinstance(value, float):
        # Stable float formatting: 6 decimals, trim trailing zeros minimally
        if value != value:  # NaN guard
            return ""
        return f"{value:.6f}"
    if value is None:
        return ""
    return value


def write_json(path: Path, obj: Any) -> None:
    """Write JSON deterministically (sort_keys, no NaN)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True, indent=2, ensure_ascii=False,
                  allow_nan=False)
        f.write("\n")
    tmp.replace(path)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    """Write JSON Lines deterministically. Returns row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False,
                               allow_nan=False))
            f.write("\n")
            n += 1
    tmp.replace(path)
    return n


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
