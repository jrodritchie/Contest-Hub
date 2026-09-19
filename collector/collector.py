import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "ContestHubPublicListingCollector/1.0 "
    "(+https://github.com/jrodritchie/Contest-Hub)"
)

SOURCES = [
    {
        "name": "Contest Girl",
        "url": "https://www.contestgirl.com/contests/contests.pl?ar=na&b=nb&c=ca&f=d&s=_&sort=p",
    },
    {
        "name": "Daily Contests Canada",
        "url": "https://dailycontests.ca/province-territory/on/",
    },
    {
        "name": "Contest Scoop",
        "url": "https://www.contestscoop.com/canadian-contests/",
    },
    {
        "name": "Contest Canada",
        "url": "https://www.contestcanada.net/",
    },
]


def allowed_by_robots(url):
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()

        return rp.can_fetch(USER_AGENT, url)

    except Exception:
        return False


def fetch_page(url):
    if not allowed_by_robots(url):
        raise RuntimeError("Blocked by robots.txt or robots.txt could not be checked.")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=25,
    )

    response.raise_for_status()

    return response.text


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def parse_prize(text):
    matches = re.findall(
        r"\$\s?([\d,]+(?:\.\d{2})?)",
        text,
    )

    values = []

    for value in matches:
        try:
            values.append(float(value.replace(",", "")))
        except ValueError:
            pass

    return max(values) if values else 0


def parse_expiry(text):
    patterns = [
        r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
        r"\b\d{1,2}[-/]\d{1,2}[-/]20\d{2}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            return match.group(0)

    return ""


def guess_frequency(text):
    lower = text.lower()

    if any(word in lower for word in [
        "daily",
        "every day",
        "once per day",
        "enter daily",
    ]):
        return "Daily"

    if any(word in lower for word in [
        "weekly",
        "every week",
        "once per week",
    ]):
        return "Weekly"

    if any(word in lower for word in [
        "monthly",
        "every month",
        "once per month",
    ]):
        return "Monthly"

    return "Single"


def guess_eligibility(text):
    lower = text.lower()

    if "ontario" in lower:
        return "Ontario"

    if "canada" in lower or "canadian" in lower:
        return "Canada"

    return "Canada"


def extract_items(source_name, source_url, html):
    soup = BeautifulSoup(html, "html.parser")

    results = []

    selectors = [
        "article",
        ".contest",
        ".contest-item",
        ".contest-entry",
        ".contest-card",
        ".post",
        "li",
    ]

    candidates = []

    for selector in selectors:
        candidates.extend(soup.select(selector))

    seen = set()

    for item in candidates:
        text = clean_text(item.get_text(" ", strip=True))

        if len(text) < 30:
            continue

        link = item.find("a", href=True)

        if not link:
            continue

        title = clean_text(link.get_text(" ", strip=True))

        if len(title) < 5:
            heading = item.find(["h1", "h2", "h3", "h4"])

            if heading:
                title = clean_text(heading.get_text(" ", strip=True))

        if len(title) < 5:
            continue

        url = urljoin(source_url, link["href"])

        key = (title.lower(), url)

        if key in seen:
            continue

        seen.add(key)

        prize = parse_prize(text)
        expiry = parse_expiry(text)
        frequency = guess_frequency(text)
        eligibility = guess_eligibility(text)

        results.append(
            {
                "id": "",
                "title": title,
                "sponsor": "",
                "prize": prize,
                "expiry": expiry,
                "frequency": frequency,
                "eligibility": eligibility,
                "source": source_name,
                "url": url,
                "note": text[:400],
            }
        )

    return results


def normalize_title(title):
    title = title.lower()
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def make_id(title, url):
    value = normalize_title(title) + "|" + url.lower()

    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def deduplicate(contests):
    combined = {}

    for contest in contests:
        key = normalize_title(contest["title"])

        if not key:
            continue

        if key not in combined:
            contest["id"] = make_id(
                contest["title"],
                contest["url"],
            )
            combined[key] = contest

        else:
            existing = combined[key]

            sources = set(
                s.strip()
                for s in existing.get("source", "").split(",")
                if s.strip()
            )

            sources.add(contest["source"])

            existing["source"] = ", ".join(sorted(sources))

            if not existing.get("url") and contest.get("url"):
                existing["url"] = contest["url"]

            if contest.get("prize", 0) > existing.get("prize", 0):
                existing["prize"] = contest["prize"]

            if not existing.get("expiry") and contest.get("expiry"):
                existing["expiry"] = contest["expiry"]

    return list(combined.values())


def sort_contests(contests):
    def expiry_key(contest):
        value = contest.get("expiry", "")

        if not value:
            return "9999-99-99"

        return value

    return sorted(
        contests,
        key=lambda contest: (
            expiry_key(contest),
            -float(contest.get("prize", 0)),
        ),
    )


def main():
    all_contests = []
    errors = []
    sources_checked = []

    for source in SOURCES:
        print(f"Checking {source['name']}...")

        try:
            html = fetch_page(source["url"])

            contests = extract_items(
                source["name"],
                source["url"],
                html,
            )

            print(f"  Found {len(contests)} possible listings.")

            all_contests.extend(contests)

            sources_checked.append(source["name"])

        except Exception as exc:
            message = f"{source['name']}: {exc}"

            print("  ERROR:", message)

            errors.append(message)

        time.sleep(2)

    contests = deduplicate(all_contests)
    contests = sort_contests(contests)

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sources_checked": sources_checked,
        "errors": errors,
        "contests": contests,
    }

    with open("contests.json", "w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(f"Wrote {len(contests)} contests.")


if __name__ == "__main__":
    main()
