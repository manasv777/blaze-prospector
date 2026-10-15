"""Paths, env settings, and the facts we're allowed to say about Blaze."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
OUT = ROOT / "out"
FIXTURES = ROOT / "fixtures"

LEADS_CSV = DATA / "leads.csv"
ENRICHED = DATA / "enriched.jsonl"
SCORED = DATA / "scored.jsonl"
DRAFTS_CSV = DATA / "drafts.csv"
DRAFT_FAILURES = DATA / "draft_failures.jsonl"
USAGE = DATA / "usage.json"
META = DATA / "meta.json"
REPLIES_CSV = DATA / "replies.csv"

MOCK_COMPANIES = FIXTURES / "mock_companies.json"
MOCK_REPLIES = FIXTURES / "mock_replies.csv"

REPORT_MD = OUT / "report.md"
DASHBOARD_HTML = OUT / "dashboard.html"

MODEL = os.getenv("MODEL") or "claude-sonnet-5-5"
SENDER_NAME = os.getenv("SENDER_NAME") or "Manas"
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY") or ""

# USD per million tokens (input, output). Unknown models fall back to Sonnet pricing.
PRICING = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# Paraphrased from blaze.money. Drafts may only use numbers that appear here or in the lead's own data.
BLAZE_DESCRIPTION = (
    "Blaze (YC S24) is an AI CFO for property managers. It connects your bank, your books, "
    "and the platforms you run on, then categorizes and reconciles transactions, pays vendor bills "
    "on your approval (catching duplicates), flags late rent and drafts reminders, drafts owner "
    "statements from each owner's terms, and forecasts cash 30 days out. Money only moves when you approve it."
)

ANGLES = {
    "A": ("Time saved", "Hours spent each month on paying bills and owner payouts."),
    "B": ("Fewer errors", "Missed or late payments and reconciliation headaches."),
    "C": ("Owner experience", "Faster, cleaner owner distributions and statements."),
}

USER_AGENT = "BlazeProspector/0.1 (research prototype; contact via github)"


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICING.get(model, PRICING["claude-sonnet-5-5"])
    return input_tokens / 1e6 * inp + output_tokens / 1e6 * out
