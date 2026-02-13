from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests

from funding_slackbot.config import SourceSettings
from funding_slackbot.models import Opportunity
from funding_slackbot.utils.datetime_utils import parse_datetime_utc
from funding_slackbot.utils.url_utils import canonicalize_url, derive_external_id

from .base import Source
from .registry import register_source

logger = logging.getLogger(__name__)

_BREAK_TAGS = re.compile(r"</?(?:br|p|li|div|tr|h\d|ul|ol|table)[^>]*>", re.IGNORECASE)
_HTML_TAGS = re.compile(r"<[^>]+>")
_MULTISPACE = re.compile(r"\s+")
_NEXT_DATA_SCRIPT = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_COMPETITION_CARD = re.compile(
    r"<li>\s*<h2[^>]*>.*?<a[^>]+href=\"(?P<href>/competition/[^\"]+)\"[^>]*>"
    r"(?P<title>.*?)</a>.*?<div class=\"wysiwyg-styles[^>]*>(?P<summary>.*?)</div>"
    r".*?<dl class=\"date-definition-list[^>]*>(?P<dates>.*?)</dl>",
    re.IGNORECASE | re.DOTALL,
)
_DATE_PAIR = re.compile(
    r"<dt>\s*(?P<label>Opened|Opens|Closes)\s*:\s*</dt>\s*<dd[^>]*>(?P<value>.*?)</dd>",
    re.IGNORECASE | re.DOTALL,
)
_COMPETITION_ID = re.compile(r"/competition/(?P<id>\d+)/")
_TITLE_CLEAN = re.compile(r"[^a-z0-9]+")
_PORTSMOUTH_WVID = re.compile(
    r'name="WVID\.STD_HID_FLDS\.ET_BASE\.[^"]*" value="([^"]+)"',
    re.IGNORECASE,
)
_PORTSMOUTH_SESSION = re.compile(
    r'name="SESSION\.STD_HID_FLDS\.ET_BASE\.[^"]*" value="([^"]+)"',
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}
_DEFAULT_UKRI_FEED_URL = "https://www.ukri.org/opportunity/feed/"
_DEFAULT_PORTSMOUTH_INCLUDE_KEYWORDS = ("research software engineer", "research software", "postdoctoral", "post-doc", "lecturer", "senior lecturer", "computing", "computer science", "software engineering", "physics", "astrophysics")
_DEFAULT_PORTSMOUTH_EXCLUDE_KEYWORDS = ("intern", "placement", "studentship", "phd", "doctoral", "undergraduate")


class RssSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        timeout_raw = settings.options.get("timeout_seconds", 30)
        self.timeout_seconds = int(timeout_raw) if timeout_raw is not None else 30

    def fetch(self) -> list[Opportunity]:
        headers = {"User-Agent": "funding-slackbot/0.1 (+https://github.com/)"}
        response = requests.get(self.url, timeout=self.timeout_seconds, headers=headers)
        response.raise_for_status()

        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", False):
            logger.warning("Feed parsing bozo exception for %s: %s", self.url, parsed.bozo_exception)

        opportunities: list[Opportunity] = []
        for entry in parsed.entries:
            opportunity = self._entry_to_opportunity(entry)
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(
            key=lambda item: item.published_at
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return opportunities

    def _entry_to_opportunity(self, entry: Any) -> Opportunity | None:
        title = _normalize_whitespace(str(entry.get("title", "Untitled opportunity")))
        raw_link = str(entry.get("link", "")).strip()
        url = canonicalize_url(raw_link)

        raw_identifier = (
            str(entry.get("id", "")).strip()
            or str(entry.get("guid", "")).strip()
            or None
        )

        fallback_seed = url or f"{self.source_id}:{title}"
        external_id = derive_external_id(raw_identifier, fallback_seed)

        published_at = (
            parse_datetime_utc(entry.get("published"))
            or parse_datetime_utc(entry.get("updated"))
            or parse_datetime_utc(entry.get("pubDate"))
            or parse_datetime_utc(entry.get("published_parsed"))
            or parse_datetime_utc(entry.get("updated_parsed"))
        )

        summary_html = (
            entry.get("summary")
            or entry.get("description")
            or entry.get("subtitle")
            or ""
        )
        summary = _html_to_text(str(summary_html))

        extracted = _extract_optional_fields(summary)
        tags = _extract_tags(entry)
        funder = extracted["funder"]
        if not funder and tags:
            funder = ", ".join(tags)

        raw_data = _to_serializable_dict(entry)

        return Opportunity(
            source_id=self.source_id,
            external_id=external_id,
            title=title,
            url=url,
            published_at=published_at,
            summary=summary,
            raw=raw_data,
            closing_date=extracted["closing_date"],
            opening_date=extracted["opening_date"],
            funder=funder,
            funding_type=extracted["funding_type"],
            total_fund=extracted["total_fund"],
        )


class WellcomeSchemesSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        timeout_raw = settings.options.get("timeout_seconds", 30)
        self.timeout_seconds = int(timeout_raw) if timeout_raw is not None else 30

    def fetch(self) -> list[Opportunity]:
        headers = {"User-Agent": "funding-slackbot/0.1 (+https://github.com/)"}
        response = requests.get(self.url, timeout=self.timeout_seconds, headers=headers)
        response.raise_for_status()

        listings = _extract_wellcome_listings(response.text)
        opportunities: list[Opportunity] = []
        for listing in listings:
            opportunity = self._listing_to_opportunity(listing)
            if opportunity is not None:
                opportunities.append(opportunity)

        opportunities.sort(key=lambda item: item.title.lower())
        return opportunities

    def _listing_to_opportunity(self, listing: dict[str, Any]) -> Opportunity | None:
        application_status = _normalize_whitespace(
            str(listing.get("scheme_accepting_applications", ""))
        )
        if application_status.lower() != "open to applications":
            return None

        title = _normalize_whitespace(
            html_lib.unescape(str(listing.get("title", "Untitled opportunity")))
        )
        raw_url = _normalize_whitespace(str(listing.get("url", "")))
        if raw_url and not raw_url.startswith(("http://", "https://")):
            raw_url = urljoin(self.url, raw_url)
        url = canonicalize_url(raw_url)

        raw_identifier = _normalize_whitespace(str(listing.get("id", ""))) or None
        fallback_seed = url or f"{self.source_id}:{title}"
        external_id = derive_external_id(raw_identifier, fallback_seed)

        summary = _html_to_text(str(listing.get("listing_summary") or ""))
        closing_date = parse_datetime_utc(listing.get("scheme_closes_for_applications"))
        opening_date = parse_datetime_utc(listing.get("scheme_opens_for_applications"))
        total_fund = _html_to_text(str(listing.get("level_of_funding") or ""))

        return Opportunity(
            source_id=self.source_id,
            external_id=external_id,
            title=title,
            url=url,
            published_at=None,
            summary=summary,
            raw=_to_serializable_dict(listing),
            closing_date=closing_date,
            opening_date=opening_date,
            funder="Wellcome",
            funding_type=_normalize_whitespace(str(listing.get("frequency", ""))) or None,
            total_fund=total_fund or None,
        )


class InnovationFundingSearchSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        timeout_raw = settings.options.get("timeout_seconds", 30)
        self.timeout_seconds = int(timeout_raw) if timeout_raw is not None else 30
        raw_ukri_url = str(settings.options.get("ukri_feed_url", _DEFAULT_UKRI_FEED_URL)).strip()
        self.ukri_feed_url = raw_ukri_url or _DEFAULT_UKRI_FEED_URL

    def fetch(self) -> list[Opportunity]:
        headers = {"User-Agent": "funding-slackbot/0.1 (+https://github.com/)"}
        response = requests.get(self.url, timeout=self.timeout_seconds, headers=headers)
        response.raise_for_status()

        cards = _extract_innovation_competition_cards(response.text)
        ukri_title_keys = self._fetch_ukri_title_keys()

        opportunities: list[Opportunity] = []
        for card in cards:
            title_key = _normalize_competition_title(card["title"])
            if title_key and _matches_ukri_title(title_key, ukri_title_keys):
                continue

            raw_url = urljoin(self.url, card["url"])
            url = canonicalize_url(raw_url)
            competition_id = _extract_competition_id(card["url"])
            external_id = (
                f"innovation-competition:{competition_id}"
                if competition_id
                else derive_external_id(None, url)
            )

            closing_date = parse_datetime_utc(card.get("closes"))
            opening_date = parse_datetime_utc(card.get("opens"))
            summary = _html_to_text(card.get("summary", ""))

            opportunities.append(
                Opportunity(
                    source_id=self.source_id,
                    external_id=external_id,
                    title=card["title"],
                    url=url,
                    published_at=opening_date,
                    summary=summary,
                    raw=_to_serializable_dict(card),
                    closing_date=closing_date,
                    opening_date=opening_date,
                    funder="Innovate UK",
                    funding_type="Competition",
                    total_fund=None,
                )
            )

        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _fetch_ukri_title_keys(self) -> list[str]:
        headers = {"User-Agent": "funding-slackbot/0.1 (+https://github.com/)"}
        try:
            response = requests.get(
                self.ukri_feed_url, timeout=self.timeout_seconds, headers=headers
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch UKRI feed for dedupe: %s", exc)
            return []

        parsed = feedparser.parse(response.content)
        title_keys: list[str] = []
        for entry in parsed.entries:
            title = _normalize_competition_title(str(entry.get("title", "")))
            if title:
                title_keys.append(title)
        return title_keys


class PortsmouthJobsSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        timeout_raw = settings.options.get("timeout_seconds", 30)
        self.timeout_seconds = int(timeout_raw) if timeout_raw is not None else 30
        self.lang = _normalize_whitespace(str(settings.options.get("lang", "USA"))) or "USA"
        per_page_raw = settings.options.get("results_per_page", 100)
        self.results_per_page = max(1, int(per_page_raw) if per_page_raw is not None else 100)
        self.include_keywords = [item.lower() for item in _DEFAULT_PORTSMOUTH_INCLUDE_KEYWORDS]
        self.exclude_keywords = [item.lower() for item in _DEFAULT_PORTSMOUTH_EXCLUDE_KEYWORDS]

    def fetch(self) -> list[Opportunity]:
        headers = {"User-Agent": "funding-slackbot/0.1 (+https://github.com/)"}
        response = requests.get(self.url, timeout=self.timeout_seconds, headers=headers)
        response.raise_for_status()
        search_url, page_text = response.url, response.text
        if _extract_portsmouth_tokens(page_text) == (None, None):
            entry = requests.get(urljoin(search_url, "wrd/run/etrec002gf.open"), timeout=self.timeout_seconds, headers=headers)
            entry.raise_for_status()
            moved = re.search(r'href="([^"]*etrec179gf\.open\?[^"]+)"', entry.text, re.IGNORECASE)
            if moved is not None:
                response = requests.get(urljoin(entry.url, moved.group(1)), timeout=self.timeout_seconds, headers=headers)
                response.raise_for_status()
                search_url, page_text = response.url, response.text

        wvid, usession = _extract_portsmouth_tokens(page_text)
        if not wvid or not usession:
            raise RuntimeError("Portsmouth jobs source missing WVID/USESSION tokens")

        run_base = _derive_portsmouth_run_base(search_url)
        job_records = self._fetch_job_records(run_base, wvid, usession, headers)
        opportunities: list[Opportunity] = []
        for record in job_records:
            if not self._is_relevant(record):
                continue

            title = _normalize_whitespace(str(record.get("job_title", "Untitled job")))
            vacancy_id = _normalize_whitespace(str(record.get("vacancy_id", "")))
            job_url = canonicalize_url(
                f"{run_base}etrec179gf.open?WVID={wvid}&LANG={self.lang}&VACANCY_ID={vacancy_id}"
            )
            external_id = (
                f"portsmouth-job:{vacancy_id}"
                if vacancy_id
                else derive_external_id(None, f"{self.source_id}:{title}:{job_url}")
            )
            closing_date = parse_datetime_utc(record.get("app_close_d"))
            published_at = parse_datetime_utc(record.get("vacancy_d"))
            summary = _html_to_text(str(record.get("job_description") or ""))
            opportunities.append(
                Opportunity(
                    source_id=self.source_id,
                    external_id=external_id,
                    title=title,
                    url=job_url,
                    published_at=published_at,
                    summary=summary,
                    raw=_to_serializable_dict(record),
                    closing_date=closing_date,
                    opening_date=published_at,
                    funder="University of Portsmouth",
                    funding_type=_normalize_whitespace(str(record.get("basis_id", ""))) or "Job posting",
                    total_fund=_normalize_whitespace(str(record.get("salary", ""))) or None,
                )
            )

        opportunities.sort(key=lambda item: item.closing_date or datetime.max.replace(tzinfo=timezone.utc))
        return opportunities

    def _fetch_job_records(
        self, run_base: str, wvid: str, usession: str, headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        endpoint = urljoin(run_base, "etrec106gf.json")
        params = (
            f"WVID={wvid}&USESSION={usession}&LANG={self.lang}"
            f"&JOB_TITLE=&KEYWORDS=&LOCATION_ID=&VAC_TYPES=&SALARY_BAND="
            f"&ORDER_BY=APP_CLOSE_D&RESULTS_PP={self.results_per_page}"
        )
        request_headers = dict(headers)
        request_headers["mhrParams"] = params
        response = requests.get(endpoint, timeout=self.timeout_seconds, headers=request_headers)
        response.raise_for_status()
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Portsmouth jobs source returned invalid JSON payload") from exc
        return [item for item in payload.get("results", []) if isinstance(item, dict)]

    def _is_relevant(self, record: dict[str, Any]) -> bool:
        searchable = _normalize_whitespace(
            _html_to_text(
                f"{record.get('job_title', '')}\n{record.get('job_description', '')}"
            )
        ).lower()
        if any(keyword in searchable for keyword in self.exclude_keywords):
            return False
        return any(keyword in searchable for keyword in self.include_keywords)


def _extract_tags(entry: Any) -> list[str]:
    tags = entry.get("tags")
    if not isinstance(tags, list):
        return []

    terms: list[str] = []
    for tag in tags:
        if isinstance(tag, dict):
            value = str(tag.get("term", "")).strip()
            if value:
                terms.append(value)
    return terms


def _extract_optional_fields(summary: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "opening_date": None,
        "closing_date": None,
        "funder": None,
        "funding_type": None,
        "total_fund": None,
    }

    for line in summary.splitlines():
        normalized_line = _normalize_whitespace(line)
        if not normalized_line:
            continue

        lowered = normalized_line.lower()
        value = normalized_line.split(":", 1)[1].strip() if ":" in normalized_line else ""

        if lowered.startswith("opening date"):
            fields["opening_date"] = parse_datetime_utc(value)
        elif lowered.startswith("closing date"):
            fields["closing_date"] = parse_datetime_utc(value)
        elif lowered.startswith("funder") or lowered.startswith("council"):
            fields["funder"] = value or fields["funder"]
        elif lowered.startswith("funding type"):
            fields["funding_type"] = value
        elif lowered.startswith("total fund"):
            fields["total_fund"] = value

    return fields


def _html_to_text(value: str) -> str:
    with_breaks = _BREAK_TAGS.sub("\n", value)
    without_tags = _HTML_TAGS.sub(" ", with_breaks)
    unescaped = html_lib.unescape(without_tags)

    cleaned_lines = []
    for line in unescaped.splitlines():
        normalized = _normalize_whitespace(line)
        if normalized:
            cleaned_lines.append(normalized)
    return "\n".join(cleaned_lines)


def _normalize_whitespace(value: str) -> str:
    return _MULTISPACE.sub(" ", value).strip()


def _to_serializable_dict(value: Any) -> dict[str, Any]:
    serialized = _to_serializable(value)
    if isinstance(serialized, dict):
        return serialized
    return {"value": serialized}


def _to_serializable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _to_serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_serializable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_serializable(item) for item in value]
    if isinstance(value, time.struct_time):
        return list(value)
    return str(value)


def _extract_wellcome_listings(page_text: str) -> list[dict[str, Any]]:
    match = _NEXT_DATA_SCRIPT.search(page_text)
    if match is None:
        raise RuntimeError("Wellcome source response missing __NEXT_DATA__ payload")

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Wellcome source __NEXT_DATA__ payload is not valid JSON") from exc

    page_props = payload.get("props", {}).get("pageProps", {})
    if not page_props and isinstance(payload.get("pageProps"), dict):
        page_props = payload["pageProps"]

    listings = page_props.get("initialListings", [])
    if not isinstance(listings, list):
        return []
    return [item for item in listings if isinstance(item, dict)]


def _extract_innovation_competition_cards(page_text: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for match in _COMPETITION_CARD.finditer(page_text):
        dates = {
            item.group("label").lower(): _html_to_text(item.group("value"))
            for item in _DATE_PAIR.finditer(match.group("dates"))
        }
        cards.append(
            {
                "url": _normalize_whitespace(match.group("href")),
                "title": _html_to_text(match.group("title")),
                "summary": match.group("summary"),
                "opens": dates.get("opens") or dates.get("opened") or "",
                "closes": dates.get("closes") or "",
            }
        )
    return cards


def _extract_competition_id(url_value: str) -> str | None:
    match = _COMPETITION_ID.search(url_value)
    if match is None:
        return None
    return match.group("id")


def _normalize_competition_title(value: str) -> str:
    normalized = _html_to_text(value).lower()
    normalized = normalized.replace("eoi", "expression of interest")
    for word, digit in _NUMBER_WORDS.items():
        normalized = re.sub(rf"\b{word}\b", digit, normalized)
    normalized = _TITLE_CLEAN.sub(" ", normalized)
    return _normalize_whitespace(normalized)


def _extract_portsmouth_tokens(page_text: str) -> tuple[str | None, str | None]:
    wvid_match = _PORTSMOUTH_WVID.search(page_text)
    session_match = _PORTSMOUTH_SESSION.search(page_text)
    return (
        wvid_match.group(1).strip() if wvid_match else None,
        session_match.group(1).strip() if session_match else None,
    )


def _derive_portsmouth_run_base(url_value: str) -> str:
    marker = "/wrd/run/"
    if marker in url_value:
        return f"{url_value.split(marker, maxsplit=1)[0]}{marker}"
    return urljoin(url_value, "wrd/run/")


def _matches_ukri_title(candidate: str, ukri_titles: list[str]) -> bool:
    for ukri_title in ukri_titles:
        if candidate == ukri_title:
            return True
        if SequenceMatcher(None, candidate, ukri_title).ratio() >= 0.92:
            return True
    return False


@register_source("rss")
def _build_rss_source(settings: SourceSettings) -> Source:
    return RssSource(settings)


@register_source("wellcome_schemes")
def _build_wellcome_schemes_source(settings: SourceSettings) -> Source:
    return WellcomeSchemesSource(settings)


@register_source("innovation_funding_search")
def _build_innovation_funding_search_source(settings: SourceSettings) -> Source:
    return InnovationFundingSearchSource(settings)


@register_source("portsmouth_jobs")
def _build_portsmouth_jobs_source(settings: SourceSettings) -> Source:
    return PortsmouthJobsSource(settings)
