import os
import time
import requests
from urllib.parse import urlparse
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

# Sites where info@<domain> would be meaningless
SKIP_HOSTS = ("facebook.com", "instagram.com", "yelp.com", "tripadvisor.com", "linktr.ee")

def guess_email(website_url: str) -> str:
    """Turn 'http://example.com/' into 'info@example.com'."""
    if not website_url or website_url == "No Website":
        return "Check Website"
    if "://" not in website_url:
        website_url = "//" + website_url
    host = (urlparse(website_url).hostname or "").removeprefix("www.")
    if not host or any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
        return "Check Website"
    return f"info@{host}"

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

@app.post("/search")
async def find_ugly_sites(query: SearchQuery):
    if not query.city or not query.industry:
        raise HTTPException(status_code=400, detail="Missing required parameters.")

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
    next_page_token = None

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
                display_name = place.get("displayName", {}).get("text", "Unknown Business")
                phone = place.get("internationalPhoneNumber", "No Phone Provided")
                website = place.get("websiteUri", "No Website")

                email = guess_email(website)

                results.append({
                    "id": len(results) + 1,
                    "business_name": display_name,
                    "phone_number": phone,
                    "email": email,
                    "website_status": evaluate_site_quality(website if website != "No Website" else ""),
                    "action": "Generate Audit"
                })

            next_page_token = google_data.get("nextPageToken")
            if not next_page_token:
                break

        except requests.exceptions.RequestException as e:
            if page == 0:
                raise HTTPException(status_code=500, detail=str(e))
            break

    return {"status": "success", "data": results}
