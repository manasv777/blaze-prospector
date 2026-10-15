"""Step 1: source leads from the official Google Places API, a seed CSV, or the mock fixture."""

import json

import httpx

from . import config
from .store import read_csv, write_csv

LEAD_FIELDS = ["name", "website", "city", "address", "rating", "review_count", "source"]
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"


def from_places(city: str, limit: int = 20) -> list[dict]:
    """Google Places API (New) Text Search. Official API only, no Maps scraping."""
    headers = {
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.websiteUri,places.formattedAddress,"
                            "places.rating,places.userRatingCount",
    }
    body = {"textQuery": f"property management company in {city}", "pageSize": min(limit, 20)}
    r = httpx.post(PLACES_URL, json=body, headers=headers, timeout=15)
    r.raise_for_status()
    leads = []
    for p in r.json().get("places", []):
        if not p.get("websiteUri"):
            continue
        leads.append({
            "name": p.get("displayName", {}).get("text", ""),
            "website": p["websiteUri"],
            "city": city,
            "address": p.get("formattedAddress", ""),
            "rating": p.get("rating", ""),
            "review_count": p.get("userRatingCount", ""),
            "source": "google_places",
        })
    return leads


def from_seed(path) -> list[dict]:
    rows = read_csv(path)
    return [{**{f: "" for f in LEAD_FIELDS}, **r, "source": "seed_csv"} for r in rows
            if r.get("website", "").lower().startswith(("http://", "https://"))]


def from_mock() -> list[dict]:
    companies = json.loads(config.MOCK_COMPANIES.read_text())
    return [{f: c.get(f, "") for f in LEAD_FIELDS} | {"source": "mock_fixture"} for c in companies]


def find(city: str = "", seed=None, mock: bool = False) -> tuple[list[dict], list[str]]:
    """Returns (leads, notes). Notes explain which sources were used or skipped."""
    notes, leads = [], []
    if mock:
        leads = from_mock()
        notes.append(f"mock fixture: {len(leads)} fake companies")
    else:
        if config.GOOGLE_PLACES_API_KEY and city:
            try:
                got = from_places(city)
                leads += got
                notes.append(f"Google Places: {len(got)} with websites")
            except httpx.HTTPError as e:
                notes.append(f"Google Places failed, skipped ({e.__class__.__name__})")
        else:
            notes.append("Google Places skipped (no GOOGLE_PLACES_API_KEY)")
        if seed:
            got = from_seed(seed)
            leads += got
            notes.append(f"seed CSV {seed}: {len(got)}")

    seen, unique = set(), []
    for lead in leads:
        key = lead["website"].lower().rstrip("/").removeprefix("https://").removeprefix("http://").removeprefix("www.")
        if key not in seen:
            seen.add(key)
            unique.append(lead)
    write_csv(config.LEADS_CSV, unique, LEAD_FIELDS)
    return unique, notes
