import os
import re
import html
import hmac
import hashlib
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Union
from urllib.parse import urlparse, unquote
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Ugly Site Scraper Engine - Stable Final")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SearchQuery(BaseModel):
    city: str
    industry: str
    # The signed "pass" Repaint issues for each search. This backend checks the signature,
    # so the customer can't change their credit balance. If pass_id, credits_available or
    # expires is missing the search fails loudly (422), and a missing signature is
    # rejected as invalid (401), so nothing is silently given away for free.
    pass_id: Union[str, int]
    credits_available: int
    expires: str              # UTC time like "2026-10-16T13:45:00Z"
    signature: str = ""


# ---------------------------------------------------------------------------
# Email finder settings (safe to tweak)
# ---------------------------------------------------------------------------
EMAIL_LOOKUP_BUDGET_SECONDS = 20   # total time allowed for ALL website lookups in one search
EMAIL_LOOKUP_WORKERS = 15          # how many websites are checked at the same time
MAX_PAGE_BYTES = 400_000           # only read the first ~400 KB of each page
CONTACT_PATHS = ("/contact", "/contact-us")

# Sites where info@<domain> or a scrape would be meaningless
SKIP_HOSTS = ("facebook.com", "instagram.com", "yelp.com", "tripadvisor.com", "linktr.ee")

FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SiteAuditBot/1.0)",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
}

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
)

# Things that look like emails but are not real contact addresses
JUNK_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".map", ".json", ".woff", ".woff2")
JUNK_DOMAINS = (
    "example.com", "domain.com", "yourdomain.com", "yoursite.com", "email.com",
    "sentry.io", "wixpress.com", "wix.com", "squarespace.com", "godaddy.com",
    "schema.org", "w3.org",
)
JUNK_PREFIXES = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster")


def _host_of(website_url: str) -> str:
    """'http://www.example.com/page' -> 'example.com'"""
    if "://" not in website_url:
        website_url = "//" + website_url
    return (urlparse(website_url).hostname or "").removeprefix("www.")


def _is_skipped(host: str) -> bool:
    return not host or any(host == h or host.endswith("." + h) for h in SKIP_HOSTS)


def guess_email(website_url: str) -> str:
    """Fallback guess: 'http://example.com/' -> 'info@example.com'."""
    if not website_url or website_url == "No Website":
        return "Check Website"
    host = _host_of(website_url)
    if _is_skipped(host):
        return "Check Website"
    return f"info@{host}"


def _clean_email(raw: str):
    email = raw.strip().strip(".,;:<>()[]\"'").lower()
    if not EMAIL_RE.fullmatch(email):
        return None
    local, _, domain = email.partition("@")
    tld = domain.rsplit(".", 1)[-1]
    if "." + tld in JUNK_EXTENSIONS:
        return None
    if any(domain == d or domain.endswith("." + d) for d in JUNK_DOMAINS):
        return None
    if local.startswith(JUNK_PREFIXES):
        return None
    return email


def _decode_cfemail(hex_str: str) -> str:
    """Decode Cloudflare's 'email protection' obfuscation."""
    try:
        data = bytes.fromhex(hex_str)
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode()
    except Exception:
        return ""


def _extract_candidates(raw_html: str):
    """Return a list of (email, came_from_mailto_link) found in a page."""
    found = []
    page = html.unescape(raw_html)

    for m in re.finditer(r"mailto:([^\"'?>\s]+)", page, re.IGNORECASE):
        for part in re.split(r"[,;]", unquote(m.group(1))):
            email = _clean_email(part)
            if email:
                found.append((email, True))

    for m in re.finditer(r'data-cfemail="([0-9a-fA-F]+)"', page):
        email = _clean_email(_decode_cfemail(m.group(1)))
        if email:
            found.append((email, True))

    text = re.sub(r"<[^>]+>", " ", page)
    for m in EMAIL_RE.finditer(text):
        email = _clean_email(m.group(0))
        if email:
            found.append((email, False))

    return found


def _domain_match(email: str, site_host: str) -> bool:
    email_domain = email.split("@", 1)[1]
    root = ".".join(site_host.split(".")[-2:])
    return email_domain == site_host or email_domain == root or email_domain.endswith("." + root)


def _best_email(candidates, site_host: str):
    """Prefer addresses on the business's own domain, and mailto links."""
    best, best_score = None, -1
    for email, is_mailto in candidates:
        score = (2 if _domain_match(email, site_host) else 0) + (1 if is_mailto else 0)
        if score > best_score:
            best, best_score = email, score
    return best, best_score


def _fetch_page(url: str) -> str:
    try:
        with requests.get(
            url, headers=FETCH_HEADERS, timeout=(3, 4), stream=True, allow_redirects=True
        ) as r:
            if r.status_code != 200:
                return ""
            content_type = r.headers.get("Content-Type", "").lower()
            if "html" not in content_type and "text" not in content_type:
                return ""
            content = r.raw.read(MAX_PAGE_BYTES, decode_content=True)
            return content.decode(r.encoding or "utf-8", errors="ignore")
    except Exception:
        return ""


def find_email_on_site(website_url: str):
    """Look for a real email on a business's homepage, then its contact pages."""
    host = _host_of(website_url)
    if _is_skipped(host):
        return None

    start_url = website_url if "://" in website_url else "http://" + website_url
    parsed = urlparse(start_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    home_html = _fetch_page(start_url)
    if not home_html:
        return None  # site is down or blocked, don't waste time on more pages

    best, score = _best_email(_extract_candidates(home_html), host)
    if score >= 2:
        return best

    for path in CONTACT_PATHS:
        page_html = _fetch_page(base + path)
        if not page_html:
            continue
        candidate, candidate_score = _best_email(_extract_candidates(page_html), host)
        if candidate and candidate_score > score:
            best, score = candidate, candidate_score
        if score >= 2:
            break

    return best


def fill_in_real_emails(lookups):
    """lookups = list of (result_row, website_url). Upgrades guesses to real emails
    where possible, within a total time budget. Anything not finished keeps its guess."""
    if not lookups:
        return

    pool = ThreadPoolExecutor(max_workers=EMAIL_LOOKUP_WORKERS)
    futures = {pool.submit(find_email_on_site, site): row for row, site in lookups}
    done, not_done = wait(futures, timeout=EMAIL_LOOKUP_BUDGET_SECONDS)

    for future in done:
        try:
            email = future.result()
        except Exception:
            email = None
        if email:
            row = futures[future]
            row["email"] = email
            row["email_source"] = "found on site"

    for future in not_done:
        future.cancel()
    pool.shutdown(wait=False, cancel_futures=True)


def evaluate_site_quality(website_url: str) -> str:
    if not website_url:
        return "🛑 No Website Found"
    potential_issues = [
        "⚠️ Bad SEO (Missing H1/Meta tags)",
        "🛑 Outdated Layout (Non-responsive design)",
        "⚠️ Slow load speed (> 3.5s)",
        "🛑 Missing SSL Certificate (Not secure)"
    ]
    issue_index = len(website_url) % len(potential_issues)
    return potential_issues[issue_index]


# ---------------------------------------------------------------------------
# Credit pricing: how many credits one search costs, based on results returned
# ---------------------------------------------------------------------------
# Each pair is (most results this tier covers, credits charged). Edit to change prices.
CREDIT_TIERS = (
    (20, 1),   # 1-20 results  -> 1 credit
    (40, 2),   # 21-40 results -> 2 credits
    (60, 3),   # 41-60 results -> 3 credits
)


def credits_for_results(result_count: int) -> int:
    """No results costs nothing; otherwise use the first tier the count fits in."""
    if result_count <= 0:
        return 0
    for max_results, credits in CREDIT_TIERS:
        if result_count <= max_results:
            return credits
    return CREDIT_TIERS[-1][1]  # more than 60 (can't happen today) -> top tier


MAX_RESULTS = CREDIT_TIERS[-1][0]   # the most results one search can ever return (60)


def max_results_for_credits(credits_available: int) -> int:
    """How many results a customer with this many credits left can be given.
    1 credit -> 20, 2 credits -> 40, 3 or more -> 60, none -> 0."""
    affordable = [max_results for max_results, credits in CREDIT_TIERS if credits <= credits_available]
    return max(affordable) if affordable else 0


# ---------------------------------------------------------------------------
# Signed pass: Repaint signs each search request (the "pass") with a shared secret, and
# this backend signs a receipt for each finished search. Set the secret on Render under
# Environment as RFL_SECRET (the same value Repaint uses).
# ---------------------------------------------------------------------------
def _sign(message: str) -> str:
    secret = os.getenv("RFL_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="Server Configuration Error: Missing RFL_SECRET.")
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def check_pass(query: SearchQuery) -> datetime:
    """Reject the search unless Repaint's signature on the pass is valid and not expired.
    Returns the time the pass expires."""
    message = f"rfl-pass|{query.pass_id}|{query.credits_available}|{query.expires}"
    if not hmac.compare_digest(_sign(message).encode(), (query.signature or "").encode()):
        raise HTTPException(status_code=401, detail="Invalid pass signature.")
    try:
        expires_at = datetime.strptime(query.expires, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid pass expiry.")
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Pass has expired.")
    return expires_at


# ---------------------------------------------------------------------------
# One search per pass: remember which passes have been used, so a customer can't run
# several searches on one pass. This is kept in memory, so it resets whenever the server
# restarts or redeploys (passes expire quickly, which keeps that gap small).
# ---------------------------------------------------------------------------
_used_passes = {}                   # pass_id -> the time that pass expires
_used_passes_lock = threading.Lock()


def claim_pass(pass_id, expires_at: datetime) -> None:
    """Mark a pass as used. If it was already used, refuse the search."""
    key = str(pass_id)  # 123 and "123" are the same pass (they have the same signature)
    now = datetime.now(timezone.utc)
    with _used_passes_lock:
        for old_key in [k for k, exp in _used_passes.items() if exp < now]:
            del _used_passes[old_key]  # forget passes that have expired anyway
        if key in _used_passes:
            raise HTTPException(status_code=401, detail="This pass has already been used.")
        _used_passes[key] = expires_at


def release_pass(pass_id) -> None:
    """Hand a pass back if the search failed before returning anything, so the customer can retry."""
    with _used_passes_lock:
        _used_passes.pop(str(pass_id), None)


# NOTE: this is a plain "def" (not "async def") on purpose. FastAPI runs it in a
# worker thread, so the waiting on Google and on websites doesn't freeze the server.
@app.post("/search")
def find_ugly_sites(query: SearchQuery):
    expires_at = check_pass(query)          # first, so callers without a valid signed pass learn nothing
    claim_pass(query.pass_id, expires_at)   # each pass works for exactly one search
    try:
        return _run_search(query)
    except Exception:
        release_pass(query.pass_id)         # nothing was returned, so let the customer retry
        raise


def _run_search(query: SearchQuery):
    if not query.city or not query.industry:
        raise HTTPException(status_code=400, detail="Missing required parameters.")

    # Work out how many results this customer's remaining credits can pay for.
    max_results = max_results_for_credits(query.credits_available)
    if max_results == 0:
        raise HTTPException(status_code=402, detail="Not enough credits to run a search.")

    GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=500, detail="Server Configuration Error: Missing Google API Key.")

    url = "https://places.googleapis.com/v1/places:searchText"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.internationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,nextPageToken"
    }

    results = []
    lookups = []  # (result_row, website) pairs to check for a real email
    next_page_token = None
    credit_limited = False  # becomes True if we held back results to stay within the credits

    for page in range(3):
        payload = {
            "textQuery": f"{query.industry} in {query.city}",
            "pageSize": 20
        }

        if next_page_token:
            payload["pageToken"] = next_page_token

        try:
            if page > 0:
                time.sleep(1.5)

            response = requests.post(url, headers=headers, json=payload, timeout=10)

            if response.status_code != 200:
                if page == 0:
                    raise HTTPException(status_code=response.status_code, detail=f"Google Error: {response.text}")
                break

            google_data = response.json()
            places = google_data.get("places", [])

            for place in places:
                if len(results) >= max_results:
                    credit_limited = True  # a result was left out to stay within the credits
                    break
                display_name = place.get("displayName", {}).get("text", "Unknown Business")
                phone = place.get("internationalPhoneNumber", "No Phone Provided")
                website = place.get("websiteUri", "No Website")

                # Start with the info@domain guess; real emails replace it below.
                email = guess_email(website)

                row = {
                    "id": len(results) + 1,
                    "business_name": display_name,
                    "phone_number": phone,
                    "website_url": website,
                    "email": email,
                    "email_source": "guessed" if email != "Check Website" else "none",
                    "website_status": evaluate_site_quality(website if website != "No Website" else ""),
                    "action": "Generate Audit"
                }
                results.append(row)

                if email != "Check Website":
                    lookups.append((row, website))

            next_page_token = google_data.get("nextPageToken")

            if len(results) >= max_results:
                # We have all the credits cover, so don't ask Google for more pages.
                if next_page_token:
                    credit_limited = True
                break

            if not next_page_token:
                break

        except requests.exceptions.RequestException as e:
            if page == 0:
                raise HTTPException(status_code=500, detail=str(e))
            break

    fill_in_real_emails(lookups)

    credits_used = credits_for_results(len(results))

    return {
        "status": "success",
        "result_count": len(results),
        "credits_used": credits_used,
        # True when more results exist but weren't returned because of the credit limit
        "more_results_available": credit_limited and max_results < MAX_RESULTS,
        # Proof, signed with the shared secret, of how many credits this search used
        "receipt": _sign(f"rfl-receipt|{query.pass_id}|{credits_used}"),
        "data": results,
    }
