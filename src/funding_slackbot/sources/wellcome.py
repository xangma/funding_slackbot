from __future__ import annotations

import html as html_lib
import json
import logging
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import parse_datetime_utc
from funding_slackbot.utils.url_utils import canonicalize_url, derive_external_id

from ._common import (
    default_headers,
    get_with_retries,
    html_to_text,
    http_options,
    normalize_whitespace,
    positive_int_option,
    to_serializable_dict,
)
from .base import Source
from .registry import register_source

logger = logging.getLogger(__name__)

_NEXT_DATA_SCRIPT = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_PAGE_TITLE = re.compile(
    r'<h1[^>]+class=["\'][^"\']*\bpage-title\b[^"\']*["\'][^>]*>(?P<title>.*?)</h1>',
    re.IGNORECASE | re.DOTALL,
)
_DRUPAL_FIELD = re.compile(
    r'<div[^>]+class=["\'][^"\']*\bfield--name-(?P<name>[a-z0-9-]+)\b[^"\']*["\'][^>]*>',
    re.IGNORECASE,
)
_FIELD_ITEM = re.compile(
    r'<div[^>]+class=["\'][^"\']*\bfield__item\b[^"\']*["\'][^>]*>(?P<value>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_TIME_DATETIME = re.compile(
    r'<time[^>]+datetime=["\'](?P<datetime>[^"\']+)["\']',
    re.IGNORECASE,
)
_DRUPAL_CURRENT_NODE = re.compile(r'currentPath["\']?\s*:\s*["\']node\\?/(?P<id>\d+)')
_DEFAULT_WELLCOME_CMS_BASE_URL = "https://cms.wellcome.org"
_WELLCOME_SCHEME_PATH_PREFIXES = (
    "/research-funding/schemes/",
    "/grant-funding/schemes/",
)


class WellcomeSchemesSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        (
            self.timeout_seconds,
            self.retry_attempts,
            self.retry_backoff_seconds,
        ) = http_options(settings)

    def fetch(self) -> list[Opportunity]:
        headers = default_headers()
        response = get_with_retries(
            self.url,
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            max_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
            accept_retry_statuses={202},
        )
        response.raise_for_status()
        if response.status_code == 202 and not response.text.strip():
            logger.warning(
                "Wellcome source returned an empty 202 response after %d attempts; skipping",
                self.retry_attempts,
            )
            return []

        listings = _extract_wellcome_listings(response.text)
        opportunities: list[Opportunity] = []
        for listing in listings:
            opportunity = self._listing_to_opportunity(listing)
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(key=lambda item: item.title.lower())
        return opportunities

    def _listing_to_opportunity(self, listing: dict[str, Any]) -> Opportunity | None:
        application_status = normalize_whitespace(
            str(listing.get("scheme_accepting_applications", ""))
        )
        if application_status.lower() != "open to applications":
            return None

        title = normalize_whitespace(
            html_lib.unescape(str(listing.get("title", "Untitled opportunity")))
        )
        raw_url = normalize_whitespace(str(listing.get("url", "")))
        if raw_url and not raw_url.startswith(("http://", "https://")):
            raw_url = urljoin(self.url, raw_url)
        url = canonicalize_url(raw_url)

        raw_identifier = normalize_whitespace(str(listing.get("id", ""))) or None
        fallback_seed = url or f"{self.source_id}:{title}"
        external_id = derive_external_id(raw_identifier, fallback_seed)

        summary = html_to_text(str(listing.get("listing_summary") or ""))
        closing_date = parse_datetime_utc(listing.get("scheme_closes_for_applications"))
        opening_date = parse_datetime_utc(listing.get("scheme_opens_for_applications"))
        total_fund = html_to_text(str(listing.get("level_of_funding") or ""))

        return Opportunity(
            source_id=self.source_id,
            external_id=external_id,
            title=title,
            url=url,
            published_at=None,
            summary=summary,
            raw=to_serializable_dict(listing),
            closing_date=closing_date,
            opening_date=opening_date,
            funder="Wellcome",
            funding_type=normalize_whitespace(str(listing.get("frequency", ""))) or None,
            total_fund=total_fund or None,
        )


class WellcomeCmsSchemesSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        self.cms_base_url = str(
            settings.options.get("cms_base_url", _DEFAULT_WELLCOME_CMS_BASE_URL)
        ).rstrip("/")
        self.max_schemes = positive_int_option(
            settings.options.get("max_schemes", 100),
            field_name=f"sources.{settings.id}.max_schemes",
        )
        self.max_workers = positive_int_option(
            settings.options.get("max_workers", 4),
            field_name=f"sources.{settings.id}.max_workers",
        )
        (
            self.timeout_seconds,
            self.retry_attempts,
            self.retry_backoff_seconds,
        ) = http_options(settings)

    def fetch(self) -> list[Opportunity]:
        headers = default_headers()
        scheme_urls = self._discover_scheme_urls(headers)
        opportunities: list[Opportunity] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url = {
                executor.submit(self._fetch_scheme, public_url, headers): public_url
                for public_url in scheme_urls[: self.max_schemes]
            }
            for future in as_completed(future_to_url):
                public_url = future_to_url[future]
                try:
                    opportunity = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "failed to fetch Wellcome CMS scheme %s: %s",
                        public_url,
                        exc,
                    )
                    continue
                if opportunity is not None:
                    opportunities.append(opportunity)

        opportunities.sort(key=lambda item: item.title.lower())
        return opportunities

    def _discover_scheme_urls(self, headers: dict[str, str]) -> list[str]:
        response = self._fetch_url(self.url, headers)
        response.raise_for_status()
        sitemap_kind, locations = _parse_sitemap_locations(response.text)

        page_locations: list[str] = []
        if sitemap_kind == "sitemapindex":
            for sitemap_url in locations:
                page_response = self._fetch_url(
                    _to_wellcome_cms_url(sitemap_url, self.cms_base_url),
                    headers,
                )
                page_response.raise_for_status()
                _, page_locations_for_sitemap = _parse_sitemap_locations(
                    page_response.text
                )
                page_locations.extend(page_locations_for_sitemap)
        else:
            page_locations = locations

        seen: set[str] = set()
        scheme_urls: list[str] = []
        for location in page_locations:
            canonical_url = canonicalize_url(location)
            if canonical_url in seen or not _is_wellcome_scheme_url(canonical_url):
                continue
            seen.add(canonical_url)
            scheme_urls.append(canonical_url)
        return scheme_urls

    def _fetch_scheme(
        self,
        public_url: str,
        headers: dict[str, str],
    ) -> Opportunity | None:
        cms_url = _to_wellcome_cms_url(public_url, self.cms_base_url)
        response = self._fetch_url(cms_url, headers)
        response.raise_for_status()

        fields = _extract_wellcome_cms_fields(response.text)
        accepting = fields.get("scheme-accepting-applications", "")
        status = fields.get("scheme-status", "")
        if accepting.lower() != "open to applications" and status.lower() != "open":
            return None

        title = _extract_wellcome_cms_title(response.text) or _title_from_url(public_url)
        summary = (
            fields.get("listing-summary")
            or fields.get("meta-description")
            or fields.get("standfirst")
            or ""
        )
        node_id = _extract_wellcome_cms_node_id(response.text)
        closing_date = parse_datetime_utc(
            _extract_wellcome_cms_datetime(
                response.text,
                "scheme-closes-for-applications",
            )
        )
        opening_date = parse_datetime_utc(
            _extract_wellcome_cms_datetime(
                response.text,
                "scheme-opens-for-applications",
            )
        )
        if closing_date is not None and closing_date < datetime.now(timezone.utc):
            return None

        return Opportunity(
            source_id=self.source_id,
            external_id=derive_external_id(node_id, public_url),
            title=title,
            url=public_url,
            published_at=None,
            summary=summary,
            raw={
                "node_id": node_id,
                "public_url": public_url,
                "cms_url": cms_url,
                "scheme_status": status,
                "accepting_applications": accepting,
            },
            closing_date=closing_date,
            opening_date=opening_date,
            funder="Wellcome",
            funding_type=fields.get("scheme-frequency-ref") or None,
            total_fund=fields.get("level-of-funding") or None,
        )

    def _fetch_url(self, url: str, headers: dict[str, str]) -> requests.Response:
        return get_with_retries(
            url,
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            max_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )


def _extract_wellcome_listings(page_text: str) -> list[dict[str, Any]]:
    match = _NEXT_DATA_SCRIPT.search(page_text)
    if match is None:
        raise RuntimeError("Wellcome source response missing __NEXT_DATA__ payload")

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Wellcome source __NEXT_DATA__ payload is not valid JSON"
        ) from exc

    page_props = payload.get("props", {}).get("pageProps", {})
    if not page_props and isinstance(payload.get("pageProps"), dict):
        page_props = payload["pageProps"]

    listings = page_props.get("initialListings", [])
    if not isinstance(listings, list):
        return []
    return [item for item in listings if isinstance(item, dict)]


def _parse_sitemap_locations(page_text: str) -> tuple[str, list[str]]:
    try:
        root = ET.fromstring(page_text)
    except ET.ParseError as exc:
        raise RuntimeError("Wellcome sitemap response is not valid XML") from exc

    kind = _xml_local_name(root.tag)
    locations = [
        html_lib.unescape(str(element.text or "")).strip()
        for element in root.iter()
        if _xml_local_name(element.tag) == "loc" and str(element.text or "").strip()
    ]
    return kind, locations


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _is_wellcome_scheme_url(url_value: str) -> bool:
    parsed = urlparse(url_value)
    if parsed.netloc not in {"wellcome.org", "www.wellcome.org"}:
        return False
    path = parsed.path.rstrip("/")
    return (
        any(path.startswith(prefix) for prefix in _WELLCOME_SCHEME_PATH_PREFIXES)
        and not path.endswith("-closed")
    )


def _to_wellcome_cms_url(public_url: str, cms_base_url: str) -> str:
    parsed = urlparse(public_url)
    cms_url = urljoin(f"{cms_base_url.rstrip('/')}/", parsed.path.lstrip("/"))
    if parsed.query:
        cms_url = f"{cms_url}?{parsed.query}"
    return cms_url


def _extract_wellcome_cms_fields(page_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    matches = list(_DRUPAL_FIELD.finditer(page_text))
    for index, match in enumerate(matches):
        field_name = match.group("name")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(page_text)
        segment = page_text[match.start() : end]
        value = _extract_wellcome_cms_field_text(segment)
        if value and field_name not in fields:
            fields[field_name] = value
    return fields


def _extract_wellcome_cms_field_text(segment: str) -> str:
    values = [
        html_to_text(match.group("value"))
        for match in _FIELD_ITEM.finditer(segment)
        if html_to_text(match.group("value"))
    ]
    return ", ".join(values)


def _extract_wellcome_cms_title(page_text: str) -> str:
    match = _PAGE_TITLE.search(page_text)
    if match is None:
        return ""
    return html_to_text(match.group("title"))


def _extract_wellcome_cms_node_id(page_text: str) -> str | None:
    match = _DRUPAL_CURRENT_NODE.search(page_text)
    return match.group("id") if match else None


def _extract_wellcome_cms_datetime(page_text: str, field_name: str) -> str:
    segment = _extract_wellcome_cms_field_segment(page_text, field_name)
    if not segment:
        return ""
    match = _TIME_DATETIME.search(segment)
    if match is None:
        return _extract_wellcome_cms_field_text(segment)
    return match.group("datetime")


def _extract_wellcome_cms_field_segment(page_text: str, field_name: str) -> str:
    matches = list(_DRUPAL_FIELD.finditer(page_text))
    for index, match in enumerate(matches):
        if match.group("name") != field_name:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(page_text)
        return page_text[match.start() : end]
    return ""


def _title_from_url(url_value: str) -> str:
    slug = urlparse(url_value).path.rstrip("/").rsplit("/", maxsplit=1)[-1]
    return normalize_whitespace(slug.replace("-", " ")).title()


@register_source("wellcome_schemes")
def _build_wellcome_schemes_source(settings: SourceSettings) -> Source:
    return WellcomeSchemesSource(settings)


@register_source("wellcome_cms_schemes")
def _build_wellcome_cms_schemes_source(settings: SourceSettings) -> Source:
    return WellcomeCmsSchemesSource(settings)
