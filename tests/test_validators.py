from blaze_prospector.enrich import detect_signals
from blaze_prospector.validators import (
    contains_evidence, find_placeholders, quote_in_text, unknown_numbers, validate_email, word_count,
)

PAGE = """Welcome!   We are a family-run team.
Owners receive a printed statement by mail along with their payout check by the 15th of each month."""
QUOTE = "Owners receive a printed statement by mail along with their payout check by the 15th of each month."


# --- quote check -----------------------------------------------------------

def test_quote_found_verbatim():
    assert quote_in_text(QUOTE, PAGE) == (True, "")


def test_quote_ignores_case_and_whitespace():
    ok, _ = quote_in_text("owners RECEIVE a printed\n statement   by mail", PAGE)
    assert ok


def test_quote_ignores_smart_quotes():
    ok, _ = quote_in_text("We’re hiring", "Careers: We're hiring a bookkeeper")
    assert not ok  # too short (2 words) -> still fails closed
    ok, _ = quote_in_text("We’re hiring a bookkeeper", "Careers: We're hiring a bookkeeper")
    assert ok


def test_paraphrased_quote_is_rejected():
    ok, why = quote_in_text("Owners get a paper statement in the mail each month.", PAGE)
    assert not ok and "not found" in why


def test_too_short_quote_is_rejected():
    ok, why = quote_in_text("family-run", PAGE)
    assert not ok and "shorter" in why


def test_empty_quote_is_rejected():
    assert quote_in_text("", PAGE)[0] is False


# --- word count ------------------------------------------------------------

def test_word_count():
    assert word_count("Hi there,\n\nthis is   five words") == 6
    assert word_count("") == 0


def test_long_email_fails():
    body = "Blaze " + " ".join(["word"] * 130) + " owners receive a printed statement"
    problems = validate_email("hello", body, QUOTE, [PAGE])
    assert any("words" in p for p in problems)


# --- placeholders ----------------------------------------------------------

def test_placeholders_detected():
    assert find_placeholders("Hi [First Name], thanks") == ["[First Name]"]
    assert find_placeholders("Hello {company} team")
    assert find_placeholders("Hi first name,")
    assert find_placeholders("Plain email with no gaps.") == []


# --- evidence + numbers ----------------------------------------------------

def test_contains_evidence_needs_four_consecutive_words():
    assert contains_evidence("I saw that owners receive a printed statement each month.", QUOTE)
    assert not contains_evidence("I saw something about owners and a statement.", QUOTE)


def test_unknown_numbers():
    assert unknown_numbers("by the 15th, a 15-minute call", [PAGE]) == []
    assert unknown_numbers("we save teams 20 hours", [PAGE]) == ["20"]


def test_good_email_passes():
    body = ("Hi Cedar Hollow team,\n\nI saw that owners receive a printed statement by mail with their payout check. "
            "Blaze drafts owner statements from your bank data so you just approve them.\n\n"
            "Open to a 15-minute call?\n\nManas")
    assert validate_email("cleaner owner statements", body, QUOTE, [PAGE, "a 15-minute call"]) == []


# --- signals ---------------------------------------------------------------

def test_signals():
    s = detect_signals("We manage over 250 doors. Pay rent online. We're hiring a bookkeeper! Owner portal login.",
                       '<a href="https://acme.appfolio.com/connect">Portal</a>')
    assert s["software"] == ["AppFolio"]
    assert s["units"] == 250 and s["bookkeeper_hiring"] and s["owner_portal"] and s["pay_rent_online"]


def test_year_is_not_a_unit_count():
    assert detect_signals("Managing homes since 2009 across 2009 rentals")["units"] is None


# --- security guards ---------------------------------------------------------

def test_csv_cells_neutralize_formulas(tmp_path):
    from blaze_prospector.store import write_csv
    p = tmp_path / "x.csv"
    write_csv(p, [{"a": "=HYPERLINK(\"http://evil\")", "b": "Hi team"}], ["a", "b"])
    text = p.read_text()
    assert "'=HYPERLINK" in text and "Hi team" in text


def test_fetcher_refuses_private_hosts():
    import pytest
    from blaze_prospector.enrich import check_public_url
    for bad in ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://localhost:8000", "file:///etc/passwd"]:
        with pytest.raises(RuntimeError):
            check_public_url(bad)


def test_fetcher_never_connects_to_private_hosts():
    """Even if the pre-check were bypassed, the connection layer refuses non-public IPs."""
    import pytest
    from blaze_prospector.enrich import PublicOnlyBackend
    with pytest.raises(RuntimeError):
        PublicOnlyBackend().connect_tcp("localhost", 80)


def test_dashboard_data_cannot_break_out_of_script(tmp_path, monkeypatch):
    from blaze_prospector import config, dashboard
    monkeypatch.setattr(config, "DASHBOARD_HTML", tmp_path / "d.html")
    evil = "</script><!--<script>alert(1)</script>&"
    html = dashboard.write_dashboard({"x": evil}).read_text()
    data = html.split('<script id="state" type="application/json">')[1].split("</script>")[0]
    assert "<" not in data and ">" not in data and "&" not in data


def test_gzip_bomb_is_capped():
    import gzip
    from blaze_prospector.enrich import MAX_BYTES, read_capped
    bomb = gzip.compress(b"A" * 50_000_000)  # ~50 KB on the wire, 50 MB inflated
    body = read_capped([bomb[i:i + 8192] for i in range(0, len(bomb), 8192)], "gzip")
    assert len(body) == MAX_BYTES


def test_slow_drip_hits_deadline():
    import time
    import pytest
    from blaze_prospector.enrich import read_capped

    def drip():
        while True:
            time.sleep(0.02)
            yield b"x"
    with pytest.raises(RuntimeError, match="longer than"):
        read_capped(drip(), "", deadline_s=0.1)


def test_plain_and_gzip_bodies_decode():
    import gzip
    from blaze_prospector.enrich import read_capped
    assert read_capped([b"<html>hi</html>"]) == b"<html>hi</html>"
    assert read_capped([gzip.compress(b"<html>hi</html>")], "gzip") == b"<html>hi</html>"


def test_report_escapes_untrusted_text():
    from blaze_prospector.report import _link, _md
    out = _md('<img src=x onerror=alert(1)> [click](javascript:alert(1)) ![i](http://x/t.png)')
    assert "<" not in out and "](" not in out and "![" not in out
    assert _link("Acme", "javascript:alert(1)") == "Acme"
    link = _link("Acme", "https://acme.example/a)(b) [x](javascript:alert(1))")
    assert link.startswith("[Acme](https://acme.example/a%29%28b%29") and "javascript:alert(1))" not in link.split("](", 1)[1][:-1]
