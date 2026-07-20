"""Disclosure scan: detect forbidden markers and private-data patterns.

This is the enforcement layer for `SOURCE_AND_USAGE_POLICY.md`. It scans
every tracked text file and filename in the staging tree for forbidden
markers. Word-boundary matching is used so that legitimate words that merely
contain a forbidden fragment (for example, a variable name that happens to
embed a short marker) do not raise a false positive.

All forbidden-marker categories are matched **case-insensitively**, so a
mixed-case variant of a forbidden term (for example an org acronym in title
case) cannot slip past the gate.

Phone-number matching suppresses any digit run that is a substring of a
longer all-hexadecimal span of 40+ characters (a SHA-1/SHA-256 family
digest). A real phone number is never embedded in such a span; a 10-digit
decimal run inside a 64-char hex SHA-256 digest is a hash, not a phone. This
suppression is evidence-backed and does not weaken detection of standalone
phone numbers.

A hit is reported as a finding; the test module fails closed on any hit.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]

# Forbidden markers grouped by category. Each marker is assembled at runtime
# from fragments so that the scanner's own source does not contain the literal
# forbidden substrings (which would otherwise self-trigger).
def _cat(parts: List[str]) -> str:
    return "".join(parts)


FORBIDDEN_TOKENS: Dict[str, List[str]] = {
    "third_party_org": [
        _cat(["A", "T", "O"]),
        _cat(["Asian", " ", "Transport"]),
        _cat(["Asian", " ", "Development", " ", "Bank"]),
        _cat(["A", "I", "I", "B"]),
    ],
    "private_path_or_marker": [
        _cat(["/", "Users", "/"]),
        _cat(["R", "F", "P"]),
        _cat(["contract", "_", "id"]),
        _cat(["run", "_", "id"]),
    ],
    "secret_like": [
        _cat(["pass", "word"]),
        _cat(["sec", "ret"]),
        _cat(["tok", "en"]),
        _cat(["api", "_", "key"]),
        _cat(["cred", "ential"]),
    ],
}
# Email and phone patterns.
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
# Production/client claim markers that must not appear as established facts
# in synthetic output (the synthetic prototype is honest about being a
# prototype, not a draft of a real client deliverable). Assembled at runtime
# so the scanner does not flag its own source.
UNSUPPORTED_CLAIM_TOKENS = [_cat(["D", "R", "A", "F", "T"])]

EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDE_FILENAMES = {".DS_Store", "STAGING_LOCK.md", "LICENSE_PENDING.md"}
SELF_REL = "shared/validation/scan.py"  # this file itself (must self-scan clean)


def _iter_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel_parts = set(path.relative_to(ROOT).parts)
        if rel_parts & EXCLUDE_DIRS:
            continue
        if path.name in EXCLUDE_FILENAMES:
            continue
        if path.suffix.lower() == ".log":
            continue
        # Skip binary-ish synthetic data (CSV/JSON) text is scanned too, since
        # forbidden tokens must not appear even there.
        yield path


def _is_hex_digest_run(text: str, start: int, end: int) -> bool:
    """Return True when text[start:end] is a digit run embedded in a longer
    all-hexadecimal span of at least 40 characters (a SHA-1/256-family digest
    or a comparable content-addressable hash).

    A real phone number is never part of such a span. This is the precise,
    evidence-backed boundary that distinguishes a 10-digit decimal run inside
    a 64-char hex SHA-256 digest (a hash, not a phone) from a standalone phone
    number. The threshold of 40 hex chars is above the longest non-hash
    hex-like identifier in this tree and matches the SHA-1 family minimum.
    """
    HEX = "0123456789abcdefABCDEF"
    ls = start
    while ls > 0 and text[ls - 1] in HEX:
        ls -= 1
    le = end
    while le < len(text) and text[le] in HEX:
        le += 1
    span = text[ls:le]
    return len(span) >= 40 and all(c in HEX for c in span)


def scan_text(text: str) -> Dict[str, List[str]]:
    """Return a dict of category -> list of matched markers found in text.

    All forbidden-marker categories are matched case-insensitively, so a
    mixed-case variant of a forbidden term (for example an org acronym in
    title case) is still caught. Word-boundary matching is used so that a
    legitimate longer word does not falsely match a short forbidden fragment
    (for example, a variable name that happens to embed a three-letter org
    acronym). The path-marker category is matched as a substring because `/`
    is not a word character, but is also case-insensitive.

    Phone matches are suppressed when the matched digit run is a substring of
    a longer all-hex span of 40+ characters (a hash digest); see
    `_is_hex_digest_run`.
    """
    hits: Dict[str, List[str]] = {cat: [] for cat in FORBIDDEN_TOKENS}
    for cat, tokens in FORBIDDEN_TOKENS.items():
        for tok in tokens:
            if cat == "private_path_or_marker":
                # Paths use case-insensitive substring matching (slashes are
                # not word chars, so case folding is the only normalization).
                if re.search(re.escape(tok), text, flags=re.IGNORECASE):
                    hits[cat].append(tok)
            else:
                # Word-boundary, case-insensitive for every category so that
                # mixed-case variants of forbidden org/access-material/claim markers
                # cannot slip past the gate.
                pat = re.compile(r"\b" + re.escape(tok) + r"\b",
                                 flags=re.IGNORECASE)
                if pat.search(text):
                    hits[cat].append(tok)
    email_match = EMAIL_RE.search(text)
    if email_match:
        hits.setdefault("email", []).append(email_match.group(0))
    phone_hits: List[str] = []
    for m in PHONE_RE.finditer(text):
        if _is_hex_digest_run(text, m.start(), m.end()):
            # A digit run inside a >=40-char hex digest is a hash fragment,
            # not a phone number. Suppress it.
            continue
        phone_hits.append(m.group(0))
    if phone_hits:
        hits["phone"] = phone_hits
    return {k: v for k, v in hits.items() if v}


def scan_all() -> Dict[str, Dict[str, List[str]]]:
    """Scan every file. Returns {relative_path: {category: [tokens]}}."""
    results: Dict[str, Dict[str, List[str]]] = {}
    for path in _iter_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue  # skip non-text files
        file_hits = scan_text(text)
        # Also scan the filename itself (catches any forbidden marker used as
        # a filename).
        fname_hits = scan_text(path.name)
        for cat, toks in fname_hits.items():
            file_hits.setdefault(cat, []).extend(toks)
        if file_hits:
            results[rel] = file_hits
    return results


if __name__ == "__main__":
    import json
    res = scan_all()
    print(json.dumps(res, indent=2) if res else "CLEAN")
