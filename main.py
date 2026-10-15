from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random

app = FastAPI(title="Ugly Site Scraper Engine")

# CRITICAL: This allows your Repaint.com website to talk to this backend safely
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # When live, change to your exact Repaint domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define what data the dashboard will send us
class SearchQuery(BaseModel):
    city: str
    industry: str

# Helper function to simulate checking if a site is "ugly" or outdated
def analyze_website_status():
    statuses = [
        "⚠️ Bad SEO (No H1 tags)",
        "🛑 Outdated (Built 2014)",
        "⚠️ Slow load speed (4.2s)",
        "🛑 Missing SSL Certificate",
        "✅ Clean & Modern"
    ]
    return random.choice(statuses)

@app.post("/search")
async def find_ugly_sites(query: SearchQuery):
    if not query.city or not query.industry:
        raise HTTPException(status_code=400, detail="Missing City or Industry input fields.")
    
    # --- GOOGLE MAPS API PIPELINE GOES HERE ---
    # In production, you will call the Google Places API textSearch endpoint:
    # URL: https://googleapis.com{industry}+in+{city}&key=YOUR_KEY
    # ------------------------------------------
    
    # Generating clean, realistic mock structure that mirrors what your Repaint dashboard expects
    results = [
        {
            "id": 1,
            "business_name": f"Elite {query.industry.capitalize()} Co.",
            "phone_number": "(555) 019-2831",
            "email": f"info@elite{query.industry.lower()}.com",
            "website_status": analyze_website_status(),
            "action": "Generate Audit"
        },
        {
            "id": 2,
            "business_name": f"{query.city.capitalize()} Premier Services",
            "phone_number": "(555) 014-4920",
            "email": f"contact@{query.city.lower()}premier.com",
            "website_status": analyze_website_status(),
            "action": "Generate Audit"
        },
        {
            "id": 3,
            "business_name": "Apex Local Business",
            "phone_number": "(555) 017-8833",
            "email": "management@apexlocal.com",
            "website_status": "🛑 Outdated (Built 2012)",
            "action": "Generate Audit"
        }
    ]
    
    return {"status": "success", "data": results}
