# Landd — AI Rental Intelligence for Expats in the Netherlands

An autonomous agent that aggregates rental listings from Dutch platforms and uses AI to surface what actually matters to internationals: **registration eligibility, utilities, tenant requirements, pets policy, and transit access** — all in one structured feed.

> Renting in the Netherlands is hard, especially for expats. The information you need most — *"Can I register my address here?"*, *"Are bills included?"*, *"Do they accept students?"* — is scattered across platforms and buried in Dutch free text. Landd extracts it automatically.

## What it does

```
Scrape  →  AI Extract  →  Infer  →  Score  →  Store
```

1. **Scrape** — collects listings from Kamernet and HousingAnywhere (modular; more platforms can be added)
2. **AI Extract** — uses Groq / Llama 3.3 to parse unstructured Dutch/English listing text into structured fields
3. **Infer** — derives registration likelihood from lease duration when landlords don't state it explicitly
4. **Score** — ranks listings against user preferences with a transparent, explainable weighted model
5. **Store** — writes everything to a Supabase (PostgreSQL) database, deduplicated by URL

## Data dimensions extracted

| Field | Method |
|-------|--------|
| Monthly rent | Scraped |
| Registration policy (inschrijving) | AI + lease-duration inference |
| Utilities (all-in vs kale huur) | AI |
| Tenant type (student / working / both) | AI |
| Furnished | AI |
| Pets allowed | AI |
| Lease duration | AI |
| Transit distance | OpenStreetMap Overpass API |

## Tech stack

- **Scraping:** Python, Playwright (JS-rendered pages), BeautifulSoup
- **AI extraction:** Groq API (Llama 3.3 70B)
- **Transit data:** OpenStreetMap Overpass API
- **Database:** Supabase (PostgreSQL)
- **Automation:** GitHub Actions (scheduled runs)

## Design decisions

- **Registration inference from lease duration** — Dutch municipalities require a minimum lease (~4 months) to register an address. When a landlord doesn't state the policy, Landd infers a likelihood from lease length, giving users an actionable signal instead of "unknown".
- **Rule-based scoring over ML** — with no user behaviour data yet, a transparent weighted model is more honest and explainable than a trained model.
- **Depth over breadth** — two platforms processed deeply (full AI extraction) rather than many platforms scraped shallowly.

## Running locally

```bash
pip install -r requirements.txt
playwright install chromium

export SUPABASE_URL="your-url"
export SUPABASE_KEY="your-key"
export GROQ_API_KEY="your-key"

python scraper.py
```

## Roadmap

- Additional platforms (Roofz, Plaza, Rentslam, WoningHuren)
- Email / push notifications for matched listings
- Community verification of registration outcomes
- Amsterdam & Den Haag coverage

---

*Built as a portfolio project demonstrating end-to-end AI agent development — scraping, LLM extraction, geospatial enrichment, and autonomous scheduling.*
