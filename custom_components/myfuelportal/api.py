"""HTTP client + HTML scrapers for the MyFuelPortal web portal.

WHY THIS FILE EXISTS
--------------------
The upstream integration does all of its scraping inline inside the coordinator,
and *duplicates* the login logic again in the config flow. That makes the parsing
impossible to unit-test and means every new page (delivery history, equipment …)
would bloat the coordinator further.

This module pulls all of that out into one place. It is deliberately free of any
`homeassistant` imports, so it can be:
  * unit-tested on its own (feed it saved HTML, assert the parsed dict), and
  * reused by both the coordinator AND the config flow (one login routine).

The portal is a white-label ASP.NET MVC app served per provider at
`https://{provider}.myfuelportal.com`. Every form carries a hidden anti-forgery
(CSRF) token that must be echoed back on POST. We log in fresh on each refresh
rather than persisting cookies, so an expired session can never wedge us.

Each public `get_*` method maps to one portal page and returns typed data
(`TankData`, …). Add a new page = add a new `get_*` method + a `_parse_*` helper;
nothing else in the integration needs to know how the HTML is shaped.

TYPING NOTE: this module is fully type-annotated. `beautifulsoup4` ships inline
types, so the bs4 lines check cleanly; `requests` does NOT ship stubs, so a few
lines will read as "unknown" under Pylance-strict until `types-requests` is
installed in the dev environment (see the repo's dev-setup notes). The code
itself is fully typed.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TypedDict

import requests
from bs4 import BeautifulSoup, Tag

_LOGGER = logging.getLogger(__name__)

# Portal paths (relative to the provider base URL).
LOGIN_PATH = "/Account/Login?ReturnUrl=%2F"
TANK_PATH = "/Tank"
DELIVERY_HISTORY_PATH = "/Delivery/History"
ACCOUNT_PATH = "/"  # the post-login home page carries account-level info

# Network timeout for every request, in seconds.
TIMEOUT = 15

# Matches the first number in a string, tolerating thousands separators and a
# decimal part: "Approximately 1,234.5 gallons" -> "1,234.5".
_NUMBER_RE = re.compile(r"[\d,]+(?:\.\d+)?")


class TankData(TypedDict):
    """One monitored tank, as scraped from /Tank.

    A TypedDict (rather than a bare ``dict``) so the *shape* is known to the type
    checker end-to-end: the coordinator and the sensors get real key/type info
    instead of ``dict[Unknown, Unknown]``.
    """

    name: str
    percent: float | None
    gallons: float | None
    capacity: float | None
    reading_date: str | None
    last_delivery: str | None


class DeliveryData(TypedDict):
    """One delivery row, as scraped from /Delivery/History.

    `price_per_gallon` is DERIVED (cost ÷ gallons) — the portal shows the line
    total, not a unit price. `detail_id` is the id from the row's
    /Delivery/Details/{id} link, for fetching line items later (M3).
    """

    date: str | None
    ticket: str | None
    tank: str | None
    product: str | None
    gallons: float | None
    cost: float | None
    price_per_gallon: float | None
    detail_id: str | None


def group_deliveries_by_tank(
    deliveries: list[DeliveryData],
) -> dict[str, list[DeliveryData]]:
    """Group delivery rows by their own ``tank`` name.

    Each delivery row carries the tank name it belongs to (the same string the
    /Tank page uses), so statistics can be built straight from the delivery list
    — independent of whether the /Tank scrape returned any rows this poll. That
    keeps historical spend/price (and estimated consumption) flowing even when a
    user "has deliveries but no tank feed" (e.g. the monitor is offline, or the
    account momentarily reports no tank rows).

    Rows with no tank attribution are skipped (they can't be keyed to a series).
    Insertion order is preserved within each tank so callers don't have to assume
    a sort.
    """
    by_tank: dict[str, list[DeliveryData]] = {}
    for delivery in deliveries:
        # Skip rows the portal didn't attribute to a tank — nothing to key on.
        name = delivery.get("tank")
        if name:
            by_tank.setdefault(name, []).append(delivery)
    return by_tank


class AccountData(TypedDict):
    """Account-level info scraped from the home page (/)."""

    customer_since: str | None  # ISO YYYY-MM-DD
    account_balance: float | None  # USD
    status: str | None


# --- Exceptions --------------------------------------------------------------
# Distinct types let the coordinator/config-flow react differently: a bad
# password (AuthError) is permanent and should surface to the user; a transient
# parse failure (ScrapeError) is worth retrying on the next poll.
class MyFuelPortalError(Exception):
    """Base class for any error talking to the portal."""


class AuthError(MyFuelPortalError):
    """Login was rejected — almost always bad credentials."""


class ScrapeError(MyFuelPortalError):
    """A page loaded but its expected content could not be parsed."""


class MyFuelPortalClient:
    """Logs into one provider's MyFuelPortal site and scrapes its pages.

    Stateless between calls except for the immutable credentials/provider; each
    public method opens a fresh authenticated session. All methods are
    synchronous (they use `requests`); the coordinator runs them in the executor
    so they never block Home Assistant's event loop.
    """

    def __init__(self, base_url: str, username: str, password: str) -> None:
        # `base_url` is the full origin, e.g. "https://myprovider.myfuelportal.com"
        # or a vanity host like "https://fuel.example.com". Turning user
        # input (a bare subdomain OR a full URL) into this form is the config
        # flow's job (`_to_base_url`), so the client stays dumb and reusable.
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password

    # -- session / auth -------------------------------------------------------
    def _authenticated_session(self) -> requests.Session:
        """Return a `requests.Session` that has completed the login handshake."""
        session = requests.Session()
        self._login(session)
        return session

    def _login(self, session: requests.Session) -> None:
        """Perform the ASP.NET form login on `session`, raising on failure."""
        login_url = self._base + LOGIN_PATH
        _LOGGER.debug("Logging in to %s", self._base)

        # 1) GET the login page so we (a) get a session cookie and (b) can read
        #    the per-request anti-forgery token out of the form.
        page = session.get(login_url, timeout=TIMEOUT)
        page.raise_for_status()
        token = _find_csrf_token(page.text)
        if token is None:
            # No token usually means the URL points somewhere that isn't a
            # MyFuelPortal login page at all — give the user a pointed hint.
            raise ScrapeError(
                f"No anti-forgery token on the login page at {self._base} — the "
                "provider/URL may be wrong, or the portal layout changed."
            )

        # 2) POST the credentials + token. The field names (EmailAddress,
        #    Password, RememberMe) are the portal's form field names.
        resp = session.post(
            login_url,
            data={
                "EmailAddress": self._username,
                "Password": self._password,
                "RememberMe": "false",
                "__RequestVerificationToken": token,
            },
            timeout=TIMEOUT,
        )

        # 3) The portal does NOT return an error status on bad creds — it just
        #    re-renders the login page. So "are we still on /Account/Login?" is
        #    our failure signal. (A sturdier check would look for an
        #    authenticated marker like a logout link; left as a future hardening.)
        if _is_login_page(resp):
            raise AuthError(
                f"Login to {self._base} was rejected — check the username/password."
            )
        _LOGGER.debug("Login to %s succeeded", self._base)

    def _get(self, session: requests.Session, path: str) -> requests.Response:
        """GET `path`, raising ScrapeError on a non-200 status.

        Returns the raw Response (unparsed) so the caller can check the final URL
        for an auth bounce *before* trying to parse the body.
        """
        resp = session.get(self._base + path, timeout=TIMEOUT)
        if resp.status_code != 200:
            raise ScrapeError(f"{path} returned HTTP {resp.status_code}")
        return resp

    def test_credentials(self) -> None:
        """Log in and discard the session — used by the config flow to validate
        credentials at setup time. Raises AuthError/ScrapeError on failure."""
        self._authenticated_session()

    # -- public scrapers (one per portal page) --------------------------------
    def get_tanks(self) -> list[TankData]:
        """Scrape `/Tank` → a list of per-tank dicts.

        Auth and "no data" are handled differently on purpose:
          * If the request bounces back to the login page, the session is NOT
            authenticated → raise AuthError (a real failure worth surfacing).
          * If we ARE authenticated but find no tank rows, log a clear WARNING and
            return an empty list — that's a legitimately tank-less account or a
            portal markup change, and erroring on every poll would be worse than
            simply having no tank entities.
        """
        session = self._authenticated_session()
        resp = self._get(session, TANK_PATH)

        # Auth confirmation: an unauthenticated request to /Tank gets redirected
        # to the login page. This catches an expired/rejected session even when
        # the earlier login heuristic passed.
        if _is_login_page(resp):
            raise AuthError(
                f"Loading /Tank at {self._base} redirected to the login page — "
                "the session was not authenticated."
            )

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("div.tank-row")
        if not rows:
            _LOGGER.warning(
                "No tanks found on %s/Tank. This is expected if the account has "
                "no monitored tanks. If you DO have tanks, the portal markup may "
                "have changed and the integration needs updating — please open an "
                "issue. Returning an empty tank list for now.",
                self._base,
            )
            return []

        tanks: list[TankData] = [
            parsed for row in rows if (parsed := _parse_tank_row(row)) is not None
        ]
        # If some rows were present but unparseable, say so — it points at a
        # partial markup change rather than a wholesale break.
        skipped = len(rows) - len(tanks)
        if skipped:
            _LOGGER.warning(
                "Skipped %d of %d tank row(s) on %s/Tank that were missing an "
                "expected field (name/level/gallons).",
                skipped,
                len(rows),
                self._base,
            )
        _LOGGER.debug("Parsed %d tank(s) from %s/Tank", len(tanks), self._base)
        return tanks

    def get_deliveries(
        self,
        date_from: str,
        date_to: str,
        tank: str = "",
        product: str = "",
    ) -> list[DeliveryData]:
        """Fetch deliveries in the date range [date_from, date_to].

        `date_from`/`date_to` are MM/DD/YYYY strings. The page defaults to only the
        last month, and filtering is a FORM POST to /Delivery/History (no query
        params): the SelectedTank / SelectedTankProduct dropdowns (empty = all)
        plus a single `DateRange` text field. The response contains ALL matching
        rows in one page (oldest-first; the grid's "10 per page" is client-side
        display only), so one wide-range call yields the complete history.

        Auth bounce → AuthError; no rows → WARNING + empty list.
        """
        session = self._authenticated_session()
        resp = session.post(
            self._base + DELIVERY_HISTORY_PATH,
            data={
                "SelectedTankProduct": product,
                "SelectedTank": tank,
                "DateRange": f"{date_from} - {date_to}",
            },
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise ScrapeError(
                f"{DELIVERY_HISTORY_PATH} POST returned HTTP {resp.status_code}"
            )
        if _is_login_page(resp):
            raise AuthError(
                f"POSTing {DELIVERY_HISTORY_PATH} at {self._base} redirected to the "
                "login page — the session was not authenticated."
            )

        soup = BeautifulSoup(resp.text, "html.parser")
        deliveries = _parse_deliveries(soup)
        if not deliveries:
            _LOGGER.warning(
                "No deliveries in %s..%s at %s%s — no history in that range, or the "
                "portal markup changed.",
                date_from,
                date_to,
                self._base,
                DELIVERY_HISTORY_PATH,
            )
        _LOGGER.debug(
            "Parsed %d delivery record(s) from %s (%s..%s)",
            len(deliveries),
            self._base,
            date_from,
            date_to,
        )
        # The POST returns ALL rows in the range in a single response (no server
        # pagination — the grid pages client-side), so callers get the complete
        # history and can total over it directly.
        return deliveries

    def get_account(self) -> AccountData:
        """Scrape the home page (/) for account-level info.

        Customer-since / balance / status live in labeled text on the home page,
        so we parse by label (markup-independent). Auth bounce → AuthError.
        """
        session = self._authenticated_session()
        resp = self._get(session, ACCOUNT_PATH)
        if _is_login_page(resp):
            raise AuthError(
                f"Loading {ACCOUNT_PATH} at {self._base} redirected to the login "
                "page — the session was not authenticated."
            )
        account = _parse_account(BeautifulSoup(resp.text, "html.parser"))
        _LOGGER.debug(
            "Account: customer_since=%s balance=%s status=%s",
            account["customer_since"],
            account["account_balance"],
            account["status"],
        )
        return account

    # NOTE (later): /Equipment and /Location/Details slot in here the same way —
    # a get_* method + a _parse_* helper + a TypedDict for the row shape.


# --- Parsing helpers (pure functions over HTML) ------------------------------
# These take BeautifulSoup nodes / strings and return plain values. Keeping them
# as free functions (not methods) makes them trivial to unit-test with a saved
# HTML fixture and no network.

def _is_login_page(resp: requests.Response) -> bool:
    """True if the response landed on the portal login page.

    The portal redirects unauthenticated requests to `/Account/Login`, so this is
    our signal for "not (or no longer) authenticated" — used both right after the
    login POST and when fetching data pages.
    """
    return "/Account/Login" in resp.url


def _find_csrf_token(html: str) -> str | None:
    """Pull the value of the hidden __RequestVerificationToken input, or None."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("input", {"name": "__RequestVerificationToken"})
    # `find` can return a Tag, a NavigableString, or None — narrow to Tag first.
    if not isinstance(el, Tag):
        return None
    # An attribute can be str or list[str] (for multi-valued attrs); we only want
    # the simple string case.
    value = el.get("value")
    return value if isinstance(value, str) else None


def _parse_tank_row(div: Tag) -> TankData | None:
    """Turn one `div.tank-row` into a TankData, or None if it has no name."""
    name_tag = div.select_one(".text-larger")
    if name_tag is None:
        return None
    name = name_tag.get_text(strip=True)

    # Level %: text of the progress bar, minus the '%'.
    pct_tag = div.select_one(".progress-bar")
    percent = (
        _to_float(pct_tag.get_text(strip=True).replace("%", ""))
        if pct_tag is not None
        else None
    )

    # Gallons: "Approximately N gallons". Upstream did `.split()[1]`, which
    # breaks on comma-grouped numbers (e.g. "1,234"); a regex is robust to both
    # the wording and the formatting. `find(string=…)` returns a NavigableString
    # (a str subclass) or None.
    gal_node = div.find(string=lambda t: bool(t) and "Approximately" in t)
    gallons = _first_number(gal_node if isinstance(gal_node, str) else None)

    # Capacity isn't shown directly; the portal only gives gallons + percent, so
    # we back-calculate it. Guard against percent == 0 (division by zero / noise).
    capacity: float | None = None
    if gallons is not None and percent:
        capacity = round(gallons / (percent / 100), 1)

    return TankData(
        name=name,
        percent=percent,
        gallons=gallons,
        capacity=capacity,
        reading_date=_labeled_date(div, "Reading Date:"),
        last_delivery=_labeled_date(div, "Last Delivery:"),
    )


def _labeled_date(div: Tag, label: str) -> str | None:
    """Find text like '<label> 05/20/2026' under `div` and return ISO 'YYYY-MM-DD'.

    Returns the raw string if it doesn't match the expected M/D/Y format, and
    None if the label isn't present at all.
    """
    node = div.find(string=lambda t: bool(t) and label in t)
    if not isinstance(node, str):
        return None
    raw = node.replace(label, "").strip()
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return raw


def _first_number(text: str | None) -> float | None:
    """First number in `text`, ignoring thousands separators. None if absent."""
    if not text:
        return None
    match = _NUMBER_RE.search(text)
    return _to_float(match.group().replace(",", "")) if match else None


def _to_float(value: str | None) -> float | None:
    """float() that returns None instead of raising on bad/empty input."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _us_date_to_iso(text: str | None) -> str | None:
    """'M/D/YYYY' -> ISO 'YYYY-MM-DD', or None if it doesn't parse."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _parse_deliveries(soup: BeautifulSoup) -> list[DeliveryData]:
    """Parse the /Delivery/History table into DeliveryData rows.

    Cells carry no per-column class, so we build a {header-label: column-index}
    map from the <th> row and read each cell by MEANING — robust to the portal
    adding or reordering columns.
    """
    table = soup.find("table")
    if not isinstance(table, Tag):
        return []
    header_index = {
        th.get_text(strip=True).lower(): i
        for i, th in enumerate(table.find_all("th"))
    }
    deliveries: list[DeliveryData] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue  # the header row has <th>, not <td>
        parsed = _parse_delivery_row(cells, header_index)
        if parsed is not None:
            deliveries.append(parsed)
    return deliveries


def _parse_delivery_row(cells: list[Tag], header_index: dict[str, int]) -> DeliveryData | None:
    """Turn one delivery <tr>'s cells into a DeliveryData, or None if blank."""

    def cell(label: str) -> str | None:
        i = header_index.get(label)
        if i is None or i >= len(cells):
            return None
        return cells[i].get_text(strip=True)

    gallons = _first_number(cell("gallons"))
    cost = _first_number(cell("total"))
    date_iso = _us_date_to_iso(cell("delivery date"))
    # A row with none of the three core fields isn't a real delivery.
    if gallons is None and cost is None and date_iso is None:
        return None

    # Derived unit price — the portal only shows the line total, not $/gal.
    price_per_gallon = (
        round(cost / gallons, 4) if (cost is not None and gallons) else None
    )

    # Delivery id from the row's "Details" link (/Delivery/Details/{id}).
    detail_id: str | None = None
    for link in cells[-1].find_all("a", href=True):
        href = link.get("href")
        if isinstance(href, str):
            match = re.search(r"/Delivery/Details/(\d+)", href)
            if match:
                detail_id = match.group(1)
                break

    return DeliveryData(
        date=date_iso,
        ticket=cell("ticket #"),
        tank=cell("tank"),
        product=cell("product"),
        gallons=gallons,
        cost=cost,
        price_per_gallon=price_per_gallon,
        detail_id=detail_id,
    )


def _parse_account(soup: BeautifulSoup) -> AccountData:
    """Pull account-level fields from the home page by labeled text.

    The home page shows e.g. "Account Number: … Customer Since: MM/DD/YYYY
    Current Account Balance: $X,XXX.XX" and "Status: Active". We regex the page
    text by label so it survives markup changes.
    """
    text = soup.get_text(" ", strip=True)
    since = re.search(
        r"Customer\s+Since:?\s*(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE
    )
    balance = re.search(
        r"Current\s+Account\s+Balance:?\s*\$?([\d,]+(?:\.\d+)?)", text, re.IGNORECASE
    )
    status = re.search(r"Status:\s*([A-Za-z]+)", text)
    return AccountData(
        customer_since=_us_date_to_iso(since.group(1)) if since else None,
        account_balance=_first_number(balance.group(1)) if balance else None,
        status=status.group(1) if status else None,
    )
