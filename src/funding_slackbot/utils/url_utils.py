from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_QUERY_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "source",
}


def canonicalize_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return value

    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    filtered_query = []
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_"):
            continue
        if lowered in _TRACKING_QUERY_PARAMS:
            continue
        filtered_query.append((key, query_value))

    filtered_query.sort(key=lambda item: (item[0], item[1]))
    query = urlencode(filtered_query, doseq=True)

    return urlunsplit((scheme, netloc, path, query, ""))


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def derive_external_id(raw_id: str | None, canonical_url: str) -> str:
    if raw_id:
        normalized_raw = raw_id.strip()
        if normalized_raw.startswith("http://") or normalized_raw.startswith("https://"):
            return canonicalize_url(normalized_raw)
        return normalized_raw

    return f"urlhash:{stable_hash(canonical_url)}"
