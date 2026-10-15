"""Deterministic checks that hold the LLM to hard constraints. Everything here fails closed."""

import re

MAX_WORDS = 120
MIN_QUOTE_WORDS = 3
EVIDENCE_NGRAM = 4  # an email must reuse at least this many consecutive words of the evidence quote

_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                              "–": "-", "—": "-", " ": " "})
PLACEHOLDER_RE = re.compile(r"\[[^\]]*\]|\{[^}]*\}|<[^>]*>|\bTODO\b|\bXX+\b|\b(?:first|last|company) name\b", re.I)
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def normalize(text: str) -> str:
    """Lowercase, straighten smart quotes/dashes, collapse whitespace."""
    return re.sub(r"\s+", " ", (text or "").translate(_QUOTE_CHARS)).strip().lower()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", normalize(text))


def quote_in_text(quote: str, page_text: str) -> tuple[bool, str]:
    """The evidence quote must appear verbatim in the page (after normalizing whitespace and case)."""
    q = normalize(quote).strip(" \"'.")
    if len(q.split()) < MIN_QUOTE_WORDS:
        return False, f"evidence quote shorter than {MIN_QUOTE_WORDS} words"
    if q not in normalize(page_text):
        return False, "evidence quote not found verbatim in page text"
    return True, ""


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def find_placeholders(text: str) -> list[str]:
    return [m.group(0) for m in PLACEHOLDER_RE.finditer(text or "")]


def contains_evidence(body: str, quote: str) -> bool:
    """True if the email reuses a run of EVIDENCE_NGRAM consecutive words from the quote (or the whole short quote)."""
    q, b = words(quote), " " + " ".join(words(body)) + " "
    n = min(EVIDENCE_NGRAM, len(q))
    if n == 0:
        return False
    return any(" " + " ".join(q[i:i + n]) + " " in b for i in range(len(q) - n + 1))


def numbers_in(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in NUMBER_RE.finditer(text or "")}


def unknown_numbers(email_text: str, allowed_sources: list[str]) -> list[str]:
    """Numbers in the email that don't appear in the lead's source data or in Blaze's own description."""
    allowed = set().union(*(numbers_in(s) for s in allowed_sources)) if allowed_sources else set()
    return sorted(numbers_in(email_text) - allowed)


def validate_email(subject: str, body: str, quote: str, allowed_sources: list[str]) -> list[str]:
    """Returns a list of failure reasons. Empty list means the draft passes."""
    problems = []
    n = word_count(body)
    if n > MAX_WORDS:
        problems.append(f"body is {n} words (max {MAX_WORDS})")
    if not contains_evidence(body, quote):
        problems.append("body does not reference the evidence quote")
    ph = find_placeholders(subject + "\n" + body)
    if ph:
        problems.append(f"placeholder left in draft: {ph[0]}")
    bad = unknown_numbers(subject + "\n" + body, allowed_sources)
    if bad:
        problems.append(f"number(s) not in source data: {', '.join(bad)}")
    if "blaze" not in normalize(body):
        problems.append("body never mentions Blaze")
    if not subject.strip():
        problems.append("empty subject")
    return problems
