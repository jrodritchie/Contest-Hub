import json
import re
import time
import hashlib
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
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
    {"name": "Gleam",
    "url": "https://gleamgiveaways.com/giveaways/platform/gleam/"
},
]

def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def robots_allowed(url):
    """
    Respect robots.txt when it is available.

    200 = read and obey the rules.
    404 = no robots.txt exists, so continue.
    Other errors = fail closed and skip that source.
    """

    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        response = requests.get(
            robots_url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )

        if response.status_code == 404:
            print(f"  robots.txt not found; continuing with conservative request rate.")
            return True

        if response.status_code != 200:
            print(
                f"  robots.txt returned HTTP {response.status_code}; "
                f"skipping source."
            )
            return False

        parser = RobotFileParser()
        parser.parse(response.text.splitlines())

        allowed = parser.can_fetch(USER_AGENT, url)

        if not allowed:
            print("  robots.txt disallows this collector.")
            return False

        return True

    except requests.RequestException as exc:
        print(f"  Could not retrieve robots.txt: {exc}")
        print("  Skipping source to avoid ignoring an unknown robots policy.")
        return False


def fetch_page(url):
    if not robots_allowed(url):
        raise RuntimeError(
            "Source was skipped because robots.txt could not be safely verified."
        )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-CA,en;q=0.9",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=25,
    )

    response.raise_for_status()

    return response.text


def parse_prize(text):
    values = []

    patterns = [
        r"\$\s?([\d,]+(?:\.\d{2})?)",
        r"([\d,]+)\s*dollars?",
    ]

    for pattern in patterns:
        for value in re.findall(pattern, text, re.I):
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

    if any(x in lower for x in [
        "daily",
        "every day",
        "once per day",
        "enter daily",
    ]):
        return "Daily"

    if any(x in lower for x in [
        "weekly",
        "every week",
        "once per week",
    ]):
        return "Weekly"

    if any(x in lower for x in [
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


def make_id(title, url):
    value = (title.lower().strip() + "|" + url.lower()).encode("utf-8")
    return hashlib.sha1(value).hexdigest()[:12]


def extract_items(source_name, source_url, html):
    soup = BeautifulSoup(html, "html.parser")

    results = []
    seen = set()

    selectors = [
        "article",
        ".contest",
        ".contest-item",
        ".contest-entry",
        ".contest-card",
        ".contest-listing",
        ".post",
        ".entry",
        "li",
    ]

    candidates = []

    for selector in selectors:
        candidates.extend(soup.select(selector))

    # Also inspect headings with nearby links.
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        parent = heading.parent

        if parent:
            candidates.append(parent)

    for item in candidates:
        text = clean_text(item.get_text(" ", strip=True))

        if len(text) < 25:
            continue

        links = item.find_all("a", href=True)

        if not links:
            continue

        # Find the most useful-looking link.
        link = None

        for candidate in links:
            candidate_text = clean_text(candidate.get_text(" ", strip=True))

            if len(candidate_text) >= 5:
                link = candidate
                break

        if link is None:
            continue

        title = clean_text(link.get_text(" ", strip=True))

        if len(title) < 5:
            heading = item.find(["h1", "h2", "h3", "h4"])

            if heading:
                title = clean_text(heading.get_text(" ", strip=True))

        if len(title) < 5:
            continue

        lower_title = title.lower()

        # Ignore navigation/general website links.
        ignored = [
            "home",
            "contact",
            "about",
            "privacy",
            "terms",
            "login",
            "register",
            "facebook",
            "instagram",
            "twitter",
            "next",
            "previous",
        ]

        if lower_title in ignored:
            continue

        url = urljoin(source_url, link["href"])

        key = (title.lower(), url.lower())

        if key in seen:
            continue

        seen.add(key)

        results.append(
            {
                "id": make_id(title, url),
                "title": title,
                "sponsor": "",
                "prize": parse_prize(text),
                "expiry": parse_expiry(text),
                "frequency": guess_frequency(text),
                "eligibility": guess_eligibility(text),
                "source": source_name,
                "url": url,
                "note": text[:500],
            }
        )

    return results


def normalize_title(title):
    title = title.lower()
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def deduplicate(contests):
    combined = {}

    for contest in contests:
        key = normalize_title(contest["title"])

        if not key:
            continue

        if key not in combined:
            combined[key] = contest
            continue

        existing = combined[key]

        sources = set(
            s.strip()
            for s in existing.get("source", "").split(",")
            if s.strip()
        )

        sources.add(contest["source"])

        existing["source"] = ", ".join(sorted(sources))

        if contest.get("prize", 0) > existing.get("prize", 0):
            existing["prize"] = contest["prize"]

        if not existing.get("expiry") and contest.get("expiry"):
            existing["expiry"] = contest["expiry"]

    return list(combined.values())


def sort_contests(contests):
    def expiry_key(contest):
        expiry = contest.get("expiry", "")

        if not expiry:
            return "9999-99-99"

        return expiry

    return sorted(
        contests,
        key=lambda contest: (
            expiry_key(contest),
            -float(contest.get("prize", 0)),
            contest.get("title", "").lower(),
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

            print(f"  ERROR: {message}")
            errors.append(message)

        # Conservative rate limiting.
        time.sleep(3)

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
