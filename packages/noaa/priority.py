"""
packages/noaa/priority.py

Source arbitration: determines which NOAA feed has canonical authority
when the same (station_id, observed_at) observation appears in multiple feeds.

Priority order (highest first):
  historical_annual   -> 3
  historical_monthly  -> 2
  realtime            -> 1

A higher-priority source always wins. A lower-priority source NEVER
overwrites an existing canonical row that came from a higher-priority source.
"""
from __future__ import annotations

from typing import Literal

SourceKind = Literal["historical_annual", "historical_monthly", "realtime"]

_PRIORITY: dict[str, int] = {
    "historical_annual": 3,
    "historical_monthly": 2,
    "realtime": 1,
}


def source_priority(source_kind: str) -> int:
    """Return the numeric priority for *source_kind* (higher = more authoritative)."""
    p = _PRIORITY.get(source_kind)
    if p is None:
        raise ValueError(f"Unknown source_kind {source_kind!r}; expected one of {list(_PRIORITY)}")
    return p


def should_replace(
    existing_source: str,
    candidate_source: str,
    existing_hash: str,
    candidate_hash: str,
) -> bool:
    """
    Decide whether the *candidate* row should replace the *existing* canonical row.

    Rules:
    1. If the measurement hash is identical, no replacement needed.
    2. If candidate priority < existing priority, refuse (no downgrade).
    3. If candidate priority >= existing priority and hash differs, replace.

    Returns True if replacement is warranted, False otherwise.
    """
    if existing_hash == candidate_hash:
        # Identical measurement — no need to replace, just bump last_seen_at
        return False

    ep = source_priority(existing_source)
    cp = source_priority(candidate_source)

    if cp < ep:
        # E.g. realtime tries to overwrite annual — not allowed
        return False

    # cp >= ep and content differs: replace (and trigger revision snapshot)
    return True


def arbitrate(
    existing_source: str,
    candidate_source: str,
    existing_hash: str,
    candidate_hash: str,
) -> tuple[bool, str]:
    """
    Return (replace: bool, reason: str) for the arbitration decision.
    Useful for logging / audit trails.
    """
    if existing_hash == candidate_hash:
        return False, "identical measurement hash — no change"

    ep = source_priority(existing_source)
    cp = source_priority(candidate_source)

    if cp < ep:
        return False, (
            f"downgrade refused: candidate {candidate_source!r} (priority {cp}) "
            f"cannot replace {existing_source!r} (priority {ep})"
        )

    return True, (
        f"replacement accepted: {candidate_source!r} (priority {cp}) "
        f">= {existing_source!r} (priority {ep}), hash changed"
    )
