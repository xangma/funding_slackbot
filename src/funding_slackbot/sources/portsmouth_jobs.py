from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

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
    to_serializable_dict,
)
from .base import Source
from .registry import register_source

_PORTSMOUTH_WVID = re.compile(
    r'name="WVID\.STD_HID_FLDS\.ET_BASE\.[^"]*" value="([^"]+)"',
    re.IGNORECASE,
)
_PORTSMOUTH_SESSION = re.compile(
    r'name="SESSION\.STD_HID_FLDS\.ET_BASE\.[^"]*" value="([^"]+)"',
    re.IGNORECASE,
)
_DEFAULT_PORTSMOUTH_INCLUDE_KEYWORDS = (
    "research software engineer",
    "research software",
    "postdoctoral",
    "post-doc",
    "lecturer",
    "senior lecturer",
    "computing",
    "computer science",
    "software engineering",
    "physics",
    "astrophysics",
)
_DEFAULT_PORTSMOUTH_EXCLUDE_KEYWORDS = (
    "intern",
    "placement",
    "studentship",
    "phd",
    "doctoral",
    "undergraduate",
)


class PortsmouthJobsSource(Source):
    def __init__(self, settings: SourceSettings) -> None:
        super().__init__(source_id=settings.id)
        self.url = settings.url
        (
            self.timeout_seconds,
            self.retry_attempts,
            self.retry_backoff_seconds,
        ) = http_options(settings)
        self.lang = normalize_whitespace(str(settings.options.get("lang", "USA"))) or "USA"
        per_page_raw = settings.options.get("results_per_page", 100)
        self.results_per_page = max(
            1,
            int(per_page_raw) if per_page_raw is not None else 100,
        )
        self.include_keywords = [
            item.lower() for item in _DEFAULT_PORTSMOUTH_INCLUDE_KEYWORDS
        ]
        self.exclude_keywords = [
            item.lower() for item in _DEFAULT_PORTSMOUTH_EXCLUDE_KEYWORDS
        ]

    def fetch(self) -> list[Opportunity]:
        headers = default_headers()
        response = get_with_retries(
            self.url,
            timeout_seconds=self.timeout_seconds,
            headers=headers,
            max_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )
        response.raise_for_status()
        search_url, page_text = response.url, response.text
        if _extract_portsmouth_tokens(page_text) == (None, None):
            entry = get_with_retries(
                urljoin(search_url, "wrd/run/etrec002gf.open"),
                timeout_seconds=self.timeout_seconds,
                headers=headers,
                max_attempts=self.retry_attempts,
                retry_backoff_seconds=self.retry_backoff_seconds,
            )
            entry.raise_for_status()
            moved = re.search(
                r'href="([^"]*etrec179gf\.open\?[^"]+)"',
                entry.text,
                re.IGNORECASE,
            )
            if moved is not None:
                response = get_with_retries(
                    urljoin(entry.url, moved.group(1)),
                    timeout_seconds=self.timeout_seconds,
                    headers=headers,
                    max_attempts=self.retry_attempts,
                    retry_backoff_seconds=self.retry_backoff_seconds,
                )
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

            title = normalize_whitespace(str(record.get("job_title", "Untitled job")))
            vacancy_id = normalize_whitespace(str(record.get("vacancy_id", "")))
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
            summary = html_to_text(str(record.get("job_description") or ""))
            opportunities.append(
                Opportunity(
                    source_id=self.source_id,
                    external_id=external_id,
                    title=title,
                    url=job_url,
                    published_at=published_at,
                    summary=summary,
                    raw=to_serializable_dict(record),
                    closing_date=closing_date,
                    opening_date=published_at,
                    funder="University of Portsmouth",
                    funding_type=normalize_whitespace(str(record.get("basis_id", "")))
                    or "Job posting",
                    total_fund=normalize_whitespace(str(record.get("salary", "")))
                    or None,
                )
            )

        opportunities.sort(
            key=lambda item: item.closing_date
            or datetime.max.replace(tzinfo=timezone.utc)
        )
        return opportunities

    def _fetch_job_records(
        self,
        run_base: str,
        wvid: str,
        usession: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        endpoint = urljoin(run_base, "etrec106gf.json")
        params = (
            f"WVID={wvid}&USESSION={usession}&LANG={self.lang}"
            f"&JOB_TITLE=&KEYWORDS=&LOCATION_ID=&VAC_TYPES=&SALARY_BAND="
            f"&ORDER_BY=APP_CLOSE_D&RESULTS_PP={self.results_per_page}"
        )
        request_headers = dict(headers)
        request_headers["mhrParams"] = params
        response = get_with_retries(
            endpoint,
            timeout_seconds=self.timeout_seconds,
            headers=request_headers,
            max_attempts=self.retry_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )
        response.raise_for_status()
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Portsmouth jobs source returned invalid JSON payload"
            ) from exc
        return [item for item in payload.get("results", []) if isinstance(item, dict)]

    def _is_relevant(self, record: dict[str, Any]) -> bool:
        searchable = normalize_whitespace(
            html_to_text(
                f"{record.get('job_title', '')}\n{record.get('job_description', '')}"
            )
        ).lower()
        if any(keyword in searchable for keyword in self.exclude_keywords):
            return False
        return any(keyword in searchable for keyword in self.include_keywords)


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


@register_source("portsmouth_jobs")
def _build_portsmouth_jobs_source(settings: SourceSettings) -> Source:
    return PortsmouthJobsSource(settings)
