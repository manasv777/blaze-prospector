"""Step 2: read each public homepage politely and pull out simple regex signals."""

import ipaddress
import json
import re
import socket
import time
import zlib
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpcore
import httpx
from bs4 import BeautifulSoup

from . import config
from .store import write_jsonl

MAX_CHARS = 8000
MAX_BYTES = 2_000_000  # stop reading a response after 2 MB of decoded content
MAX_RAW_BYTES = 4_000_000  # ...or 4 MB on the wire
PAGE_DEADLINE = 20  # seconds per response, total (TIMEOUT only bounds the gap between reads)
TIMEOUT = 10
DELAY = 1.0  # seconds between requests (1 req/sec)

SOFTWARE = {
    "AppFolio": r"appfolio",
    "Buildium": r"buildium|managebuilding\.com",
    "Yardi": r"yardi|rentcafe\.com",
    "Rent Manager": r"rent ?manager|rmresident\.com",
    "Propertyware": r"propertyware",
    "DoorLoop": r"doorloop",
}
OWNER_PORTAL = re.compile(r"owner(?:'s)?\s+(?:statements?|portal)", re.I)
PAY_ONLINE = re.compile(r"pay (?:your )?rent online|online rent (?:payments?|pay)|pay rent\b", re.I)
PAY_CHECK = re.compile(r"(?:pay(?:ment)?s? (?:by|via|with) (?:personal )?check|checks? or money orders?|money orders?|cashier'?s check)", re.I)
ACCOUNTING_ROLE = re.compile(r"bookkeep\w*|staff accountant|accounting (?:assistant|clerk|specialist|manager|coordinator)|accounts (?:payable|receivable) (?:clerk|specialist)", re.I)
HIRING = re.compile(r"hiring|careers?|join our team|open positions?|job opening|apply now|we'?re looking for", re.I)
UNITS = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*\+?\s*(?:units|doors|homes|properties|rentals|residences|apartments)", re.I)


def public_ip(host: str, port: int) -> str:
    """Resolve once and return an IP only if every address for the host is public (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise RuntimeError(f"DNS lookup failed for {host}")
    ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    bad = next((ip for ip in ips if not ip.is_global), None)
    if bad or not ips:
        raise RuntimeError(f"refused non-public address {bad} for {host}")
    return str(ips[0])


def check_public_url(url: str) -> None:
    """Refuse non-http(s) URLs and hosts that resolve to private, loopback, or link-local addresses."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RuntimeError(f"refused non-http URL: {url}")
    public_ip(u.hostname, u.port or (443 if u.scheme == "https" else 80))


class PublicOnlyBackend(httpcore.SyncBackend):
    """Connects to the exact IP that passed the check, so DNS can't change between check and connect.
    TLS still verifies the real hostname: httpcore passes it as server_hostname after connecting."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return super().connect_tcp(public_ip(host, port), port, timeout, local_address, socket_options)


class Fetcher:
    """Polite fetcher: robots.txt, 1 request/second, 10s timeout, public hosts only (every connection, every redirect)."""

    def __init__(self):
        transport = httpx.HTTPTransport()
        transport._pool._network_backend = PublicOnlyBackend()  # httpx has no public hook for a custom backend
        self.client = httpx.Client(timeout=TIMEOUT, follow_redirects=True, transport=transport, trust_env=False,
                                   headers={"User-Agent": config.USER_AGENT},
                                   event_hooks={"request": [lambda req: check_public_url(str(req.url))]})
        self.robots: dict[str, RobotFileParser | None] = {}
        self._last = 0.0

    def _wait(self):
        gap = time.monotonic() - self._last
        if gap < DELAY:
            time.sleep(DELAY - gap)
        self._last = time.monotonic()

    def allowed(self, url: str) -> bool:
        host = urlparse(url)
        base = f"{host.scheme}://{host.netloc}"
        if base not in self.robots:
            self._wait()
            rp = None
            try:
                text = self._read(urljoin(base, "/robots.txt"), raise_status=False)
                if text:
                    rp = RobotFileParser()
                    rp.parse(text.splitlines())
            except (httpx.HTTPError, RuntimeError):
                pass  # no readable robots.txt: treat as allowed (the page fetch re-checks the host)
            self.robots[base] = rp
        rp = self.robots[base]
        return rp is None or rp.can_fetch(config.USER_AGENT, url)

    def get(self, url: str) -> str:
        self._wait()
        return self._read(url, raise_status=True)

    def _read(self, url: str, raise_status: bool) -> str:
        # Ask for gzip or nothing, so decompression stays under our control (see read_capped).
        with self.client.stream("GET", url, headers={"Accept-Encoding": "gzip, identity"}) as r:
            if raise_status:
                r.raise_for_status()
            elif r.status_code != 200:
                return ""
            body = read_capped(r.iter_raw(), r.headers.get("content-encoding", ""))
            return body.decode(r.encoding or "utf-8", errors="replace")


def read_capped(raw_chunks, content_encoding: str = "", deadline_s: float = PAGE_DEADLINE) -> bytes:
    """Read at most MAX_BYTES of decoded content within deadline_s, inflating gzip with a bounded output size."""
    enc = (content_encoding or "identity").strip().lower()
    if enc not in ("identity", "gzip", "x-gzip"):
        raise RuntimeError(f"unsupported content-encoding: {enc}")
    inflate = zlib.decompressobj(16 + zlib.MAX_WBITS) if "gzip" in enc else None
    deadline = time.monotonic() + deadline_s
    out, raw = bytearray(), 0
    for chunk in raw_chunks:
        raw += len(chunk)
        if time.monotonic() > deadline:
            raise RuntimeError(f"response took longer than {deadline_s:g}s")
        if inflate:
            try:
                chunk = inflate.decompress(chunk, MAX_BYTES - len(out))  # never inflates past the cap
            except zlib.error:
                raise RuntimeError("corrupt gzip body")
        out += chunk
        if len(out) >= MAX_BYTES or raw >= MAX_RAW_BYTES:
            break
    return bytes(out[:MAX_BYTES])


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    desc = meta.get("content", "") if meta else ""
    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe", "head"]):
        tag.decompose()
    body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return re.sub(r"\s+", " ", f"{title}. {desc}. {body}").strip(" .")[:MAX_CHARS]


def detect_signals(text: str, html: str = "") -> dict:
    haystack = f"{text}\n{html}"
    software = [name for name, pat in SOFTWARE.items() if re.search(pat, haystack, re.I)]

    hiring_accounting = False
    for m in ACCOUNTING_ROLE.finditer(text):
        window = text[max(0, m.start() - 250): m.end() + 250]
        if HIRING.search(window):
            hiring_accounting = True
            break

    units = None
    for m in UNITS.finditer(text):
        window = text[max(0, m.start() - 80): m.end() + 80].lower()
        if re.search(r"manag|overse|portfolio", window):
            n = int(m.group(1).replace(",", ""))
            if 5 <= n <= 100_000 and not (1900 <= n <= 2100 and "," not in m.group(1)):
                units = max(units or 0, n)

    return {
        "software": software,
        "owner_portal": bool(OWNER_PORTAL.search(text)),
        "pay_rent_online": bool(PAY_ONLINE.search(text)),
        "pay_by_check": bool(PAY_CHECK.search(text)),
        "bookkeeper_hiring": hiring_accounting,
        "units": units,
    }


def enrich(leads: list[dict], mock: bool = False, log=print) -> list[dict]:
    out = []
    mock_pages = {}
    if mock:
        mock_pages = {c["website"]: c for c in json.loads(config.MOCK_COMPANIES.read_text())}
    fetcher = None if mock else Fetcher()

    for lead in leads:
        row = {**lead, "fetch_status": "ok", "fetch_error": "", "page_text": "", "signals": {}}
        url = lead["website"]
        if not url.startswith("http"):
            url = "https://" + url
        try:
            if mock:
                page = mock_pages.get(lead["website"], {})
                if page.get("simulate_error"):
                    raise RuntimeError(page["simulate_error"])
                html = page.get("html", "")
            else:
                if not fetcher.allowed(url):
                    row.update(fetch_status="skipped", fetch_error="disallowed by robots.txt")
                    out.append(row)
                    log(f"  - {lead['name']}: robots.txt disallows, skipped")
                    continue
                html = fetcher.get(url)
            row["page_text"] = visible_text(html)
            row["signals"] = detect_signals(row["page_text"], html)
            log(f"  ✓ {lead['name']}: {len(row['page_text'])} chars, software={row['signals']['software'] or '-'}")
        except (httpx.HTTPError, RuntimeError) as e:
            reason = f"HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else (str(e) or e.__class__.__name__)
            row.update(fetch_status="error", fetch_error=reason)
            log(f"  ✗ {lead['name']}: {reason}")
        out.append(row)

    write_jsonl(config.ENRICHED, out)
    return out
