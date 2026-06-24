"""
Landd — AI Rental Intelligence for Dutch Expats
Autonomous scraper: scrapes Dutch rental platforms, extracts decision-critical
fields with AI, computes transit distance, and stores everything in Supabase.

Platforms: Kamernet, HousingAnywhere (modular — more can be added)
Stack: Playwright + BeautifulSoup (scraping), Groq/Llama (AI extraction),
       OpenStreetMap Overpass (transit), Supabase (database)
"""

import os
import re
import json
import time
import asyncio

from supabase import create_client
from groq import Groq
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import requests

# ----------------------------------------------------------------------------
# CONFIG — all secrets come from environment variables (set in GitHub Secrets)
# ----------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)
geolocator = Nominatim(user_agent="landd_rental_scout")

HEADERS = {"User-Agent": "landd-rental-scout/1.0"}


# ----------------------------------------------------------------------------
# 1. SCRAPING — fetch full text of a listing detail page
# ----------------------------------------------------------------------------
async def fetch_detail(url):
    """Open a listing detail page, wait for JS to load, return full text."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        try:
            await page.click("text=Show more", timeout=2000)
            await page.wait_for_timeout(800)
        except Exception:
            pass
        content = await page.content()
        await browser.close()
    soup = BeautifulSoup(content, "html.parser")
    return soup.get_text(separator=" ", strip=True)


async def scrape_kamernet():
    """Scrape Kamernet Rotterdam listing detail links."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://kamernet.nl/en/for-rent/rooms-rotterdam",
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        content = await page.content()
        await browser.close()
    soup = BeautifulSoup(content, "html.parser")
    links = soup.find_all("a", href=True)
    detail = [a["href"] for a in links
              if "/en/for-rent/" in a["href"] and "rooms-rotterdam" not in a["href"]]
    return ["https://kamernet.nl" + l for l in set(detail)]


async def scrape_housinganywhere():
    """Scrape HousingAnywhere Rotterdam listing links + basic info."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://housinganywhere.com/s/Rotterdam--Netherlands",
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)
        content = await page.content()
        await browser.close()
    soup = BeautifulSoup(content, "html.parser")
    cards = soup.find_all("div", class_="css-71zbny-main")
    links = []
    for card in cards:
        parent = card
        for _ in range(6):
            parent = parent.parent
            if parent is None:
                break
            a = parent.find("a", href=True)
            if a and "/room/" in a["href"]:
                links.append(a["href"])
                break
    return list(set(links))


# ----------------------------------------------------------------------------
# 2. AI EXTRACTION — parse unstructured Dutch/English text into structured JSON
# ----------------------------------------------------------------------------
def ai_extract(text):
    """Use Groq/Llama to extract decision-critical fields from listing text."""
    prompt = f"""You are a Dutch rental listing analyzer. Extract fields and return ONLY valid JSON.
Text may be in Dutch or English.

Listing text:
{text[:3000]}

For pets: "huisdieren toegestaan"/"pets allowed" = true; "geen huisdieren"/"no pets" = false; "in overleg" = unknown.

Return exactly:
{{
  "registration": "true/false/unknown",
  "utilities": "true/false/unknown",
  "tenant_type": "student/working/both/unknown",
  "furnished": "true/false/unknown",
  "pets_allowed": "true/false/unknown",
  "lease_months": "number or unknown",
  "confidence": "0.0 to 1.0"
}}"""
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.choices[0].message.content
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return json.loads(raw)


def infer_registration(ai_says, lease_months):
    """Infer registration policy from explicit statement or lease length."""
    if ai_says == "true":
        return "likely_yes", "Landlord explicitly allows registration"
    if ai_says == "false":
        return "no", "Landlord explicitly does not allow registration"
    try:
        m = int(lease_months)
        if m < 4:
            return "likely_no", f"Lease only {m} months — usually too short to register"
        elif m >= 6:
            return "likely_yes", f"Lease {m} months — long enough to likely qualify"
        else:
            return "maybe", f"Lease {m} months — depends on municipality"
    except (ValueError, TypeError):
        return "unknown", "No registration info and lease length unclear"


# ----------------------------------------------------------------------------
# 3. TRANSIT — nearest public transport stop via OpenStreetMap Overpass
# ----------------------------------------------------------------------------
def get_nearest_transit(address):
    """Return (station_name, type, walk_minutes) for the nearest transit stop."""
    try:
        loc = geolocator.geocode(f"{address}, Netherlands", timeout=10)
        if not loc:
            return None, None, None
        lat, lon = loc.latitude, loc.longitude

        query = f"""
        [out:json][timeout:25];
        (
          node["railway"="station"](around:800,{lat},{lon});
          node["railway"="tram_stop"](around:800,{lat},{lon});
          node["highway"="bus_stop"](around:800,{lat},{lon});
          node["station"="subway"](around:800,{lat},{lon});
          node["public_transport"="station"](around:800,{lat},{lon});
        );
        out body;
        """
        resp = requests.post("https://overpass-api.de/api/interpreter",
                             data={"data": query}, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return None, None, None
        elements = resp.json().get("elements", [])
        if not elements:
            return None, None, None

        nearest, nearest_type, min_dist = None, None, 9999
        for el in elements:
            d = geodesic((lat, lon), (el["lat"], el["lon"])).km
            if d < min_dist:
                min_dist = d
                tags = el.get("tags", {})
                nearest = tags.get("name", "Unnamed stop")
                if tags.get("station") == "subway":
                    nearest_type = "metro"
                elif tags.get("railway") == "tram_stop":
                    nearest_type = "tram"
                elif tags.get("railway") == "station":
                    nearest_type = "train"
                elif tags.get("highway") == "bus_stop":
                    nearest_type = "bus"
                else:
                    nearest_type = "transit"
        walk_min = round((min_dist * 1.3) / 5 * 60)
        return nearest, nearest_type, walk_min
    except Exception:
        return None, None, None


# ----------------------------------------------------------------------------
# 4. PIPELINE — tie it all together per listing
# ----------------------------------------------------------------------------
async def process_listing(url, source):
    """Full pipeline for one listing: fetch -> AI extract -> infer -> store."""
    text = await fetch_detail(url)
    fields = ai_extract(text)
    reg_status, reg_reason = infer_registration(
        fields["registration"], fields["lease_months"])

    try:
        lease = int(fields["lease_months"])
    except (ValueError, TypeError):
        lease = None

    # extract a usable title/address
    if source == "Kamernet":
        title = url.split("/")[-1].replace("-", " ")
    else:
        title = url.split("/Rotterdam/")[-1].replace("-", " ")[:60] \
            if "/Rotterdam/" in url else "Rotterdam listing"

    record = {
        "source": source,
        "title": title,
        "url": url,
        "registration": reg_status,
        "utilities": fields["utilities"] == "true",
        "tenant_type": fields["tenant_type"],
        "furnished": fields["furnished"] == "true",
        "pets_allowed": fields["pets_allowed"],
        "duration_min": lease,
        "tenant_note": reg_reason,
        "raw_description": text[:1000],
    }
    supabase.table("listings").upsert(record, on_conflict="url").execute()
    return record


# ----------------------------------------------------------------------------
# 5. MAIN — run the whole agent
# ----------------------------------------------------------------------------
async def main():
    print("=== Landd scraper run started ===")

    # Scrape both platforms
    kamernet_links = await scrape_kamernet()
    print(f"Kamernet: found {len(kamernet_links)} listings")

    ha_links = await scrape_housinganywhere()
    print(f"HousingAnywhere: found {len(ha_links)} listings")

    # Process (limit per run to stay within rate limits)
    processed = 0
    for url in kamernet_links[:10]:
        try:
            r = await process_listing(url, "Kamernet")
            print(f"  ✓ {r['title'][:35]} | reg:{r['registration']}")
            processed += 1
        except Exception as e:
            print(f"  ✗ {url[:50]} — {e}")

    for url in ha_links[:10]:
        try:
            r = await process_listing(url, "HousingAnywhere")
            print(f"  ✓ {r['title'][:35]} | reg:{r['registration']}")
            processed += 1
        except Exception as e:
            print(f"  ✗ {url[:50]} — {e}")

    print(f"=== Run complete: {processed} listings processed ===")


if __name__ == "__main__":
    asyncio.run(main())
