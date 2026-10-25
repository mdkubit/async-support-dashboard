#!/usr/bin/env python3
"""Fetch text-oriented remote support candidates. Python 3.11+, stdlib only.

Run: python update_jobs.py [--output jobs.json] [--max-age-days 45]
Exit 1 leaves the previous output untouched when any source fails.
Source attribution is included in tags; display it beside source_link.
Keyword screening is conservative, not independent verification of employers.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


LOG = logging.getLogger("update_jobs")
UTC = timezone.utc
WWR = "https://weworkremotely.com/categories/remote-customer-support-jobs.rss"
REMOTIVE = "https://remotive.com/api/remote-jobs?category=customer-support"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

# Require a support role, not a developer job mentioning support tooling.
SUPPORT = re.compile(
    r"\b(?:customer\s+(?:support|service|care|experience|happiness)|support|help\s*desk|service\s+desk)\b",
    re.I,
)
POSITIVE = {
    "chat": r"\b(?:live\s+)?chat\b",
    "email": r"\be-?mails?\b",
    "tickets": r"\btickets?\b|\bticketing\b",
    "zendesk": r"\bzendesk\b",
    "intercom": r"\bintercom\b",
    "slack": r"\bslack\b",
    "discord": r"\bdiscord\b",
    "written": r"\bwritten\b",
    "asynchronous": r"\basync(?:hronous(?:ly)?)?\b",
}
# A single explicit heavy-duty phrase is sufficient; ordinary phone mentions
# and occasional calls do not disqualify a job under this discovery policy.
HEAVY_VOICE_PATTERN = (
    r"(?:heavy\s+phone\s+volume|high[ -]volume\s+(?:phone\s+)?calls?|"
    r"cold[ -]calling|inbound\s+call\s+queues?|telemarketing)"
)
HEAVY_VOICE = re.compile(r"\b" + HEAVY_VOICE_PATTERN + r"\b", re.I)
NEGATED_HEAVY_VOICE = re.compile(
    r"\b(?:no|without|does\s+not\s+involve)\s+" + HEAVY_VOICE_PATTERN + r"\b",
    re.I,
)
CALL_CENTER_TITLE = re.compile(
    r"\bcall[ -]cent(?:er|re)\s+agent\b", re.I,
)


class PlainText(HTMLParser):
    """Discard markup, scripts, and styles while keeping word boundaries."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(str(value or ""))
    value = unicodedata.normalize("NFKC", "".join(parser.parts))
    value = re.sub(r"[\u2010-\u2015\u2212]", "-", value)
    return " ".join(value.split())


def parse_date(value):
    """Normalize RSS RFC 2822 and API ISO timestamps to UTC."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing publication date")
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        date = parsedate_to_datetime(value)
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return date.astimezone(UTC)


def fetch(url):
    """Bounded reads and timeouts; two attempts, at least 31 seconds apart.

    The spacing respects Remotive's documented two-requests/minute ceiling.
    Do not retry rate limits or other permanent HTTP errors.
    """
    for attempt in range(2):
        try:
            request = Request(url, headers={
                "User-Agent": "AsyncSupportDashboard/1.0 (public job feed reader)",
                "Accept": "application/json, application/rss+xml, application/xml",
            })
            with urlopen(request, timeout=30) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("response exceeds size limit")
            return body
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if isinstance(exc, HTTPError) and exc.code < 500:
                raise
            if attempt == 1:
                raise
            LOG.warning("Fetch failed for %s: %s; retrying in 31s", url, exc)
            time.sleep(31)
    raise RuntimeError("unreachable")


def read_wwr():
    body = fetch(WWR)
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("unexpected XML declaration")
    root = ET.fromstring(body)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("expected RSS channel")
    rows = []
    for item in root.findall("./channel/item"):
        label = plain(item.findtext("title"))
        company, separator, title = label.partition(":")
        if not separator:
            company, title = "", label
        description = " ".join(
            element.text or "" for element in item
            if element.tag.split("}")[-1] in {"description", "encoded"}
        )
        rows.append({
            "title": title.strip(), "company": company.strip(),
            "description": description, "date": item.findtext("pubDate"),
            "url": item.findtext("link"), "source": "We Work Remotely",
        })
    return rows


def read_remotive():
    payload = json.loads(fetch(REMOTIVE))
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("expected Remotive jobs array")
    rows = []
    for item in payload["jobs"]:
        if not isinstance(item, dict):
            raise ValueError("invalid Remotive job object")
        rows.append({
            "title": item.get("title"), "company": item.get("company_name"),
            "description": item.get("description"),
            "date": item.get("publication_date"), "url": item.get("url"),
            "source": "Remotive",
        })
    return rows


def screen(row, now, max_age_days):
    title, company = plain(row["title"]), plain(row["company"])
    description = plain(row["description"])
    link = row["url"]
    if not all((title, company, description, isinstance(link, str), link)):
        return None, "incomplete"
    parsed = urlsplit(link)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None, "invalid_link"
    # Both sources supply remote-only listings. Geography may still be restricted.
    if CALL_CENTER_TITLE.search(title):
        return None, "call_center_title"
    if not SUPPORT.search(title):
        return None, "not_support"
    text = f"{title} {description}"
    tags = [tag for tag, pattern in POSITIVE.items() if re.search(pattern, text, re.I)]
    if not tags:
        return None, "no_positive_keyword"
    if HEAVY_VOICE.search(NEGATED_HEAVY_VOICE.sub(" ", text)):
        return None, "heavy_voice_duties"
    try:
        date = parse_date(row["date"])
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_date"
    if date < now - timedelta(days=max_age_days) or date > now + timedelta(hours=24):
        return None, "outside_date_window"
    return {
        "title": title, "company": company,
        "date_posted": date.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_link": link.strip(),
        "tags": sorted(set(tags + ["remote", "text-oriented", row["source"]])),
    }, "accepted"


def sample_jobs():
    """Stable, fictional UI fixtures; never impersonate live vacancies.

    Keep the five-field schema. Frontends must show the Sample Listing label
    and disable application links for these reserved .invalid URLs.
    """
    fixtures = [
        ("Live Chat Customer Support Specialist", "Sample Co. - Harbor Desk",
         "live-chat", ["chat", "intercom", "remote"]),
        ("Email & Ticket Support Associate", "Sample Co. - Papertrail Tools",
         "email-support", ["email", "tickets", "zendesk", "remote"]),
        ("Async Customer Experience Specialist", "Sample Co. - Orbit Workspace",
         "async-support", ["asynchronous", "written", "slack", "remote"]),
    ]
    return [{
        "title": f"Sample Listing - {title}", "company": company,
        "date_posted": "2026-01-01T00:00:00Z",
        "source_link": f"https://example.invalid/sample-jobs/{slug}",
        "tags": sorted(tags + ["Sample Listing", "Fictional - not hiring"]),
    } for title, company, slug, tags in fixtures]


def write_atomic(path, jobs):
    content = json.dumps(jobs, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        LOG.info("No changes; output unchanged")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("jobs.json"))
    parser.add_argument("--max-age-days", type=int, default=45)
    args = parser.parse_args()
    if args.max_age_days < 1:
        parser.error("--max-age-days must be positive")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    rows, failures = [], []
    for name, reader in [("We Work Remotely", read_wwr), ("Remotive", read_remotive)]:
        try:
            source_rows = reader()
            LOG.info("%s: fetched %d jobs", name, len(source_rows))
            rows.extend(source_rows)
        except Exception:
            LOG.exception("Source failed: %s", name)
            failures.append(name)
    if failures:
        LOG.error("Output preserved: incomplete snapshot (%s)", ", ".join(failures))
        return 1
    now, counts, jobs = datetime.now(UTC), Counter(), []
    for row in rows:
        job, reason = screen(row, now, args.max_age_days)
        counts[reason] += 1
        if job:
            jobs.append(job)
    # Deterministic sorting and cross-source title/company deduplication.
    jobs.sort(key=lambda job: (job["date_posted"], job["source_link"]), reverse=True)
    unique, seen_links, seen_roles = [], set(), set()
    for job in jobs:
        role = (job["company"].casefold(), job["title"].casefold())
        if job["source_link"] in seen_links or role in seen_roles:
            continue
        seen_links.add(job["source_link"])
        seen_roles.add(role)
        unique.append(job)
    if not unique:
        LOG.warning("No active matches; writing three clearly labeled sample listings")
        unique = sample_jobs()
    write_atomic(args.output, unique)
    LOG.info("Wrote %d jobs to %s; screening: %s", len(unique), args.output, dict(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
