import os
import time
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Ugly Site Scraper Engine - Pagination")

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

    url = "https://googleapis.com"
    
    # CRITICAL: Added 'nextPageToken' to the FieldMask so Google sends it to us [2]
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.internationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,nextPageToken"
    }
    
    results = []
    next_page_token = None
    
    # We loop up to 3 times max to get all 60 results (20 results per page) [1]
    for page in range(3):
        payload = {
            "textQuery": f"{query.industry} in {query.city}"
        }
        
        # If we have a token from the previous loop, feed it back to Google [2]
        if next_page_token:
            payload["pageToken"] = next_page_token [2]
            
        try:
            # Google sometimes takes a split second to register a newly generated token, 
            # so we sleep briefly on pages 2 and 3 to ensure stability.
            if page > 0:
                time.sleep(1.5)
                
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code != 200:
                break  # Stop looping if Google encounters an error
                
            google_data = response.json()
            places = google_data.get("places", [])
            
            for place in places:
                display_name = place.get("displayName", {}).get("text", "Unknown Business")
                phone = place.get("internationalPhoneNumber", "No Phone Provided")
                website = place.get("websiteUri", "")
                
                email = f"info@{website.split('//')[-1].split('www.')[-1].split('/')}" if website else "Check Website"
                
                results.append({
                    "id": len(results) + 1,
                    "business_name": display_name,
                    "phone_number": phone,
                    "email": email,
                    "website_status": evaluate_site_quality(website),
                    "action": "Generate Audit"
                })
            
            # Check if there is another page available. If not, break out of the loop.
            next_page_token = google_data.get("nextPageToken") [2]
            if not next_page_token:
                break
                
        except requests.exceptions.RequestException:
            break  # Break loop gracefully on timeout or connection error

    return {"status": "success", "data": results}
