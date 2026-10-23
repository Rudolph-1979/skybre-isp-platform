"""FNB Integration Channel client -- Transaction History API (#185).

What FNB publishes (fnb.co.za -> Integration Channel -> API Transaction
History, read 16 Oct 2026):

* REST + JSON, described by an OpenAPI (Swagger) document that FNB sends
  after the application is approved. Polling, not push.
* Client authentication with JWT-signed tokens, using the client ID and
  client secret issued on Integration Channel.
* OAuth 2.0 AUTHORISATION CODE flow: the code arrives at our redirect URL
  (or is shown on Integration Channel to paste), is exchanged at the token
  endpoint for an access token + refresh token; the refresh token renews
  the access token; when it expires, the connection is made again.
* Each transaction has a transaction ID, value date, booking date,
  transaction details, reference, amount, currency and a debit/credit
  indicator -- and the response carries the account balances.

STILL A GUESS until the Swagger document arrives, all in this file: the
token / transactions paths, the query parameter names, the exact JSON
field names and how paging works. The normaliser below accepts the common
spellings of each field (and both a signed amount and amount + indicator)
so the first real answer is more likely to work than not -- check it
against the spec and the sandbox before production.

Nothing else in bankfeeds (sync.py, the views, the frontend) cares how
these answers are shaped; they only see the normalised dicts.
"""
from datetime import date as date_cls, datetime, timedelta, timezone as dt_timezone
import hashlib
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urljoin, urlparse

import requests
from django.conf import settings
from django.utils import timezone

from . import tokens


class FNBClientError(Exception):
    """Raised for any failure talking to FNB's API -- auth failure,
    network error, an account with no api_base_url configured, or an
    unexpected response shape. sync.py catches this and logs it on a
    BankFeedSyncLog rather than letting one bad account take down the
    whole hourly run across all 4 accounts."""


def _own_hostnames() -> set:
    """Hostnames that belong to this platform rather than to a bank.

    ALLOWED_HOSTS is the list the deployment already maintains to answer
    exactly this question ("is this host us?"), so it is reused rather
    than duplicated into another setting staff would have to keep in
    sync. The loopback names are added because a base URL pointing at the
    machine the sync runs on is the same mistake wearing a different hat.
    """
    hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    for entry in getattr(settings, "ALLOWED_HOSTS", None) or []:
        # A leading dot is ALLOWED_HOSTS' subdomain wildcard; "*" matches
        # everything and would make every URL on earth look like ours.
        entry = (entry or "").strip().lstrip(".").lower()
        if entry and entry != "*":
            hosts.add(entry)
    site_host = urlparse(getattr(settings, "SITE_URL", "") or "").hostname
    if site_host:
        hosts.add(site_host.lower())
    return hosts


def validate_api_base_url(value: str) -> str:
    """Return `value` trimmed, or raise ValueError saying why it cannot be
    a bank API base URL.

    Blank is allowed here and means "CSV import only" -- FNBClient refuses
    that separately, with its own message pointing at CSV import.

    This exists because of a real failure: the field was filled in with
    this platform's own address, so every hourly sync POSTed the client
    credentials to Skybre's own web server and collected a 405 that named
    no cause. Nothing in the request path could catch it -- the
    cross-host guard in _resolve_token_redirect only inspects REDIRECTS,
    and a base URL that is wrong from the very first request never
    redirects anywhere. So it is caught here instead, at the point the
    value is typed and again before any request is sent.

    ValueError rather than FNBClientError or DRF's ValidationError, so
    the serializer and the client can each wrap it in the exception their
    own caller expects.
    """
    url = (value or "").strip()
    if not url:
        return url

    parsed = urlparse(url)
    if not parsed.scheme:
        # requests' own words for this are "Invalid URL ...: No scheme
        # supplied", which is accurate but arrives after a sync has
        # already been attempted and logged as a failure.
        raise ValueError(
            f"'{url}' does not start with http:// or https://. "
            f"If that is the right address, enter it as https://{url}"
        )
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"'{url}' is not an http:// or https:// address.")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"'{url}' has no hostname in it.")

    if host in _own_hostnames():
        raise ValueError(
            f"'{url}' is this platform's own address, not a bank's. Pointing the bank feed "
            "at it makes every sync send this account's client credentials to Skybre's own "
            "web server, which answers 405 Not Allowed because it has no token endpoint -- "
            "FNB is never contacted at all. Leave this blank and use CSV import until FNB "
            "confirms API access and gives you their real base URL."
        )
    return url


class FNBReconnectNeeded(FNBClientError):
    """FNB refused the refresh token (or none is stored): the account has
    to be connected again on Integration Channel."""


# How long before expiry an access token is renewed rather than used.
TOKEN_EARLY_REFRESH = timedelta(seconds=60)
# Assumed access-token life when FNB's answer doesn't say (expires_in).
DEFAULT_TOKEN_LIFE = timedelta(minutes=5)
# A page limit, so a paging loop that never ends fails instead of hanging.
MAX_PAGES = 100
CALLBACK_PATH = "/bank-feeds/fnb-callback"


def redirect_uri_allowed(uri: str) -> bool:
    """The redirect URL must be this platform's own callback page -- the
    one place an auth code is meant to arrive."""
    fixed = getattr(settings, "FNB_REDIRECT_URI", "") or ""
    if fixed:
        return uri == fixed
    parsed = urlparse(uri or "")
    if parsed.path.rstrip("/") != CALLBACK_PATH or parsed.query or parsed.fragment:
        return False
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1"):
        return parsed.scheme in ("http", "https")
    return parsed.scheme == "https" and host in _own_hostnames()


# ---- reading FNB's transaction lines ---------------------------------------
#
# The field names are FNB's published list (transaction ID, value date,
# booking date, transaction details, reference, amount, currency,
# debit/credit indicator, balances) in the spellings an OpenAPI document
# usually gives them. Confirm against the Swagger document.

_ID_KEYS = ("transactionId", "transactionID", "transaction_id", "id", "uniqueId", "transactionReferenceNumber")
_BOOKING_KEYS = ("bookingDate", "booking_date", "postingDate", "postedDate", "transactionDate", "date")
_VALUE_KEYS = ("valueDate", "value_date")
_DETAIL_KEYS = ("transactionDetails", "details", "description", "narrative", "transactionDescription", "remittanceInformation")
_REFERENCE_KEYS = ("reference", "transactionReference", "paymentReference", "ref", "customerReference")
_AMOUNT_KEYS = ("amount", "transactionAmount", "value")
_INDICATOR_KEYS = ("creditDebitIndicator", "debitCreditIndicator", "crDrIndicator", "drCrIndicator", "indicator", "transactionType")
_CURRENCY_KEYS = ("currency", "currencyCode")
_LIST_KEYS = ("transactions", "transactionHistory", "transactionList", "data", "items", "results", "records")

_CREDIT = {"C", "CR", "CRDT", "CREDIT"}
_DEBIT = {"D", "DR", "DBIT", "DEBIT"}


def _first(raw: dict, keys):
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


def parse_bank_date(value):
    """A date from '2026-10-16', '2026-10-16T08:00:00Z', '20261016',
    '16/10/2026' or an epoch-millisecond number. None if unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=dt_timezone.utc).date()
    text = str(value).strip()
    try:
        return date_cls.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%Y%m%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _to_amount(value):
    """(Decimal amount, currency or "") from a number, a string like
    'R 1 200,00' / '1,200.00' / '-35.50', or {"amount": .., "currency": ..}."""
    currency = ""
    if isinstance(value, dict):
        currency = str(_first(value, _CURRENCY_KEYS) or "")
        value = _first(value, ("amount", "value"))
    if value is None or isinstance(value, bool):
        raise InvalidOperation
    if isinstance(value, (int, float)):
        return Decimal(str(value)), currency
    text = str(value).strip().replace("R", "").replace(" ", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return Decimal(text), currency


def normalise_transaction(raw: dict, seen=None):
    """One FNB line -> {"external_id", "date", "description", "reference",
    "amount", "raw"}. Raises ValueError (with the reason) for a line that
    can't be read, so the caller can skip it and say so.

    The debit/credit indicator, when present, decides the sign -- banks
    differ on whether a debit amount is already negative, and trusting it
    would turn payments out into deposits. Without an indicator the amount
    is taken as signed.
    """
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    booked = parse_bank_date(_first(raw, _BOOKING_KEYS)) or parse_bank_date(_first(raw, _VALUE_KEYS))
    if booked is None:
        raise ValueError(f"no readable date (booking {_first(raw, _BOOKING_KEYS)!r}, value {_first(raw, _VALUE_KEYS)!r})")
    try:
        amount, currency = _to_amount(_first(raw, _AMOUNT_KEYS))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"no readable amount ({_first(raw, _AMOUNT_KEYS)!r})")
    if not amount.is_finite():
        raise ValueError("amount is not a number")
    indicator = str(_first(raw, _INDICATOR_KEYS) or "").strip().upper()
    if indicator in _CREDIT:
        amount = abs(amount)
    elif indicator in _DEBIT:
        amount = -abs(amount)
    currency = currency or str(_first(raw, _CURRENCY_KEYS) or "")
    if currency and currency.upper() not in ("ZAR", "R"):
        raise ValueError(f"currency {currency}, not ZAR")
    amount = amount.quantize(Decimal("0.01"))
    details = str(_first(raw, _DETAIL_KEYS) or "").strip()
    reference = str(_first(raw, _REFERENCE_KEYS) or "").strip()

    tx_id = _first(raw, _ID_KEYS)
    if tx_id is not None:
        external_id = f"fnb:{tx_id}"
    else:
        # No id on the line: a stable hash, numbered within the answer so
        # two identical lines on one day stay two lines.
        key = f"{booked.isoformat()}|{amount}|{details}|{reference}"
        n = 0
        if seen is not None:
            n = seen.get(key, 0)
            seen[key] = n + 1
        external_id = "fnb-hash:" + hashlib.sha256(f"{key}|{n}".encode()).hexdigest()[:32]
    return {
        "external_id": external_id[:255],
        "date": booked,
        "description": details[:500],
        "reference": reference[:255],
        "amount": amount,
        "raw": raw,
    }


def _extract_list(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for k in _LIST_KEYS:
        v = payload.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _extract_list(v)
            if inner:
                return inner
    return []


def extract_balance(payload):
    """The account balance FNB reported, or None. Prefers a closing /
    current / ledger balance over an available balance."""
    if not isinstance(payload, dict):
        return None
    candidates = []
    for container in (payload, payload.get("data") if isinstance(payload.get("data"), dict) else {}):
        bal = container.get("balances") or container.get("balance") or container.get("accountBalances")
        if isinstance(bal, list):
            candidates.extend(b for b in bal if isinstance(b, dict))
        elif isinstance(bal, dict):
            candidates.append(bal)
        elif bal is not None:
            candidates.append({"amount": bal})
        for k in ("closingBalance", "currentBalance", "ledgerBalance", "availableBalance"):
            if container.get(k) is not None:
                candidates.append({"type": k, "amount": container[k]})

    def rank(b):
        kind = str(b.get("type") or b.get("balanceType") or b.get("name") or "").lower()
        for i, word in enumerate(("closing", "current", "ledger", "book", "available")):
            if word in kind:
                return i
        return 9

    for b in sorted(candidates, key=rank):
        try:
            amount, _cur = _to_amount(b.get("amount") if "amount" in b else b.get("balanceAmount", b))
        except (InvalidOperation, ValueError, TypeError):
            continue
        indicator = str(_first(b, _INDICATOR_KEYS) or "").upper()
        if indicator in _DEBIT:
            amount = -abs(amount)
        return amount.quantize(Decimal("0.01"))
    return None


def _next_page(payload, current_url):
    """(url, params) for the next page, or None. Follows a `next` link on
    the same host, or a page token."""
    if not isinstance(payload, dict):
        return None
    links = payload.get("links") or payload.get("_links") or {}
    nxt = None
    if isinstance(links, dict):
        nxt = links.get("next")
        if isinstance(nxt, dict):
            nxt = nxt.get("href")
    elif isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and link.get("rel") == "next":
                nxt = link.get("href")
    nxt = nxt or (payload.get("next") if isinstance(payload.get("next"), str) else None)
    if nxt:
        target = urljoin(current_url, nxt)
        if urlparse(target).netloc != urlparse(current_url).netloc:
            raise FNBClientError(
                f"FNB's next-page link points at another host ({urlparse(target).netloc}). Not following it "
                "with the bank token."
            )
        return target, None
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    for k in ("nextPageToken", "pageToken", "continuationToken", "continuationKey", "nextCursor"):
        tok = payload.get(k) or meta.get(k)
        if tok:
            return current_url, {"pageToken": tok}
    return None


class FNBClient:
    def __init__(self, account):
        """`account` is a bankfeeds.models.BankAccount row."""
        if not account.api_base_url:
            raise FNBClientError(
                "No API base URL configured for this account. Either use CSV import for now, or fill in "
                "the API connection details once FNB confirms API access for this account."
            )
        # Checked here too, not only in BankAccountSerializer: rows saved
        # before that validation existed are still in the database, and
        # the hourly sync reads them straight from it without going
        # through a serializer at all.
        try:
            validate_api_base_url(account.api_base_url)
            if account.api_token_url:
                validate_api_base_url(account.api_token_url)
        except ValueError as exc:
            raise FNBClientError(str(exc)) from exc
        self.account = account

    @property
    def token_url(self):
        return self.account.api_token_url or f"{self.account.api_base_url.rstrip('/')}/oauth/token"

    @staticmethod
    def _describe_response(action: str, method: str, url: str, resp) -> str:
        """A failure message that says what actually came back.

        Only the RESPONSE body is quoted. The token request's own payload
        carries api_client_secret and the auth code / refresh token, so it
        is never included here -- this message ends up in a
        BankFeedSyncLog row that staff can read.
        """
        body = (resp.text or "").strip().replace("\n", " ")
        if len(body) > 500:
            body = body[:500] + "..."
        parts = [
            f"Could not {action} FNB's API.",
            f"{method} {url} returned HTTP {resp.status_code}.",
        ]
        if resp.history:
            hops = " -> ".join(
                f"{h.status_code} {h.url}" for h in resp.history
            )
            parts.append(f"Redirected: {hops} -> {resp.url}.")
        if resp.status_code == 405:
            parts.append(
                "405 means that URL exists but does not accept this method. Either the path is "
                "wrong (the paths in this module are unverified until FNB's spec arrives -- see the "
                "module docstring) or the request was redirected and demoted."
            )
        if body:
            parts.append(f"Response body: {body}")
        else:
            parts.append("The response had an empty body.")
        return " ".join(parts)

    # ---- token requests ---------------------------------------------------

    # A redirected token request is followed by hand, not by requests, and
    # never more than this many times.
    _MAX_TOKEN_REDIRECTS = 3

    def _client_auth(self, url, data):
        """Adds client authentication to a token request: (data, auth)."""
        acc = self.account
        method = acc.api_client_auth or "post"
        if method == "basic":
            return dict(data), (acc.api_client_id, acc.api_client_secret)
        if method == "jwt":
            import jwt

            now = int(time.time())
            assertion = jwt.encode(
                {"iss": acc.api_client_id, "sub": acc.api_client_id, "aud": url,
                 "jti": uuid.uuid4().hex, "iat": now, "exp": now + 300},
                acc.api_client_secret, algorithm="HS256",
            )
            return {
                **data,
                "client_id": acc.api_client_id,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            }, None
        return {**data, "client_id": acc.api_client_id, "client_secret": acc.api_client_secret}, None

    def _post_token_once(self, url, data):
        """One POST to the token endpoint, redirects unfollowed."""
        body, auth = self._client_auth(url, data)
        try:
            return requests.post(
                url,
                data=body,
                auth=auth,
                headers={"Accept": "application/json"},
                timeout=15,
                # Never let requests follow this one: on 301/302/303 it
                # turns a POST into a GET. See _resolve_token_redirect.
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FNBClientError(f"Could not reach FNB's API at {url}: {exc}") from exc

    def _resolve_token_redirect(self, url, resp):
        """Where to re-POST after a 3xx, or raise saying why we won't.

        Refused: a redirect to a DIFFERENT host (it would hand over the
        client secret / auth code / refresh token) and a redirect from
        https:// down to http:// (it would send them in clear).
        """
        location = resp.headers.get("Location")
        if not location:
            raise FNBClientError(
                f"FNB's API returned HTTP {resp.status_code} for POST {url} with no Location "
                "header, so there is nothing to follow. Check this account's token URL."
            )

        target = urljoin(url, location)
        here, there = urlparse(url), urlparse(target)

        if there.scheme not in ("http", "https"):
            raise FNBClientError(
                f"FNB's API redirected the token request to {target}, which is not http(s). "
                "Refusing to follow it."
            )
        if here.scheme == "https" and there.scheme == "http":
            raise FNBClientError(
                f"FNB's API redirected the token request from {url} down to {target}. Refusing "
                "to follow it: the client credential travels in this request's body and would "
                "go over the wire in clear."
            )
        if here.netloc and there.netloc != here.netloc:
            raise FNBClientError(
                f"FNB's API redirected the token request from {here.netloc} to {there.netloc}. "
                "Refusing to follow a redirect to a different host, because the client secret "
                "is in this request's body. If that host is genuinely FNB's, set this account's "
                f"token URL to it directly so the credential is only ever sent somewhere "
                "chosen deliberately."
            )
        return target

    def _token_request(self, data, action="authenticate with"):
        """POST a grant to the token endpoint; the JSON answer, or raise."""
        url = self.token_url
        followed = []
        for _ in range(self._MAX_TOKEN_REDIRECTS + 1):
            resp = self._post_token_once(url, data)
            if not (resp.is_redirect or resp.is_permanent_redirect):
                break
            followed.append(f"{resp.status_code} {url}")
            url = self._resolve_token_redirect(url, resp)
        else:
            raise FNBClientError(
                "FNB's API kept redirecting the token request "
                f"({self._MAX_TOKEN_REDIRECTS} hops without an answer): "
                + " -> ".join(followed)
                + f" -> {url}. That is usually a redirect loop; check this account's token URL."
            )
        trail = (" Re-POSTed through: " + " -> ".join(followed) + f" -> {url}.") if followed else ""
        if resp.status_code >= 400:
            message = self._describe_response(action, "POST", url, resp) + trail
            if data.get("grant_type") == "refresh_token" and resp.status_code in (400, 401):
                raise FNBReconnectNeeded(
                    "FNB refused the refresh token -- connect this account to FNB again "
                    "(Integration Channel). " + message
                )
            raise FNBClientError(message)
        try:
            payload = resp.json()
        except ValueError:
            raise FNBClientError(self._describe_response("read a token from", "POST", url, resp) + " It was not JSON." + trail)
        if not isinstance(payload, dict) or not payload.get("access_token"):
            keys = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
            raise FNBClientError(
                f"FNB's API responded to the token request, but the JSON contained no 'access_token' field. Keys returned: {keys}."
            )
        return payload

    def _store_tokens(self, payload, connected_by=None, connecting=False):
        acc = self.account
        now = timezone.now()
        try:
            life = timedelta(seconds=int(payload.get("expires_in")))
        except (TypeError, ValueError):
            life = DEFAULT_TOKEN_LIFE
        acc.api_access_token_enc = tokens.encrypt(payload["access_token"])
        if payload.get("refresh_token"):
            # Refresh tokens may rotate: always keep the newest.
            acc.api_refresh_token_enc = tokens.encrypt(payload["refresh_token"])
        acc.api_token_expires_at = now + life
        acc.api_needs_reconnect = False
        fields = ["api_access_token_enc", "api_refresh_token_enc", "api_token_expires_at", "api_needs_reconnect"]
        if connecting:
            acc.api_connected_at = now
            acc.api_connected_by = connected_by
            fields += ["api_connected_at", "api_connected_by"]
        acc.save(update_fields=fields)

    def authorize_url(self, redirect_uri, state):
        """The FNB page to send the browser to, when FNB gives one."""
        if not self.account.api_authorize_url:
            raise FNBClientError("No authorisation URL is set for this account -- paste the auth code instead.")
        try:
            validate_api_base_url(self.account.api_authorize_url)
        except ValueError as exc:
            raise FNBClientError(str(exc)) from exc
        params = {"response_type": "code", "client_id": self.account.api_client_id,
                  "redirect_uri": redirect_uri, "state": state}
        if self.account.api_scope:
            params["scope"] = self.account.api_scope
        sep = "&" if "?" in self.account.api_authorize_url else "?"
        return self.account.api_authorize_url + sep + urlencode(params)

    def exchange_code(self, code, redirect_uri, user=None):
        """Auth code -> access + refresh token, stored encrypted."""
        code = (code or "").strip()
        if not code:
            raise FNBClientError("No auth code given.")
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
        if self.account.api_scope:
            data["scope"] = self.account.api_scope
        payload = self._token_request(data, action="exchange the auth code with")
        self._store_tokens(payload, connected_by=user, connecting=True)
        return payload

    def _refresh(self):
        try:
            refresh_token = tokens.decrypt(self.account.api_refresh_token_enc)
        except tokens.TokenUnreadable as exc:
            self._mark_reconnect()
            raise FNBReconnectNeeded(str(exc)) from exc
        if not refresh_token:
            self._mark_reconnect()
            raise FNBReconnectNeeded("The FNB access token has expired and no refresh token is stored -- connect this account to FNB again.")
        try:
            payload = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token}, action="refresh the token with")
        except FNBReconnectNeeded:
            self._mark_reconnect()
            raise
        self._store_tokens(payload)
        return payload["access_token"]

    def _mark_reconnect(self):
        self.account.api_needs_reconnect = True
        self.account.save(update_fields=["api_needs_reconnect"])

    def _fetch_access_token(self) -> str:
        """A usable access token: the stored one while it is valid, else a
        refreshed one. Raises FNBReconnectNeeded when neither works."""
        acc = self.account
        expires = acc.api_token_expires_at
        if acc.api_grant == "client_credentials":
            if acc.api_access_token_enc and expires and expires - TOKEN_EARLY_REFRESH > timezone.now():
                try:
                    return tokens.decrypt(acc.api_access_token_enc)
                except tokens.TokenUnreadable:
                    pass
            data = {"grant_type": "client_credentials"}
            if acc.api_scope:
                data["scope"] = acc.api_scope
            payload = self._token_request(data)
            self._store_tokens(payload)
            return payload["access_token"]
        if not (acc.api_access_token_enc or acc.api_refresh_token_enc):
            raise FNBReconnectNeeded(
                "This account isn't connected to FNB yet. Use Connect to FNB (or paste the auth code from "
                "Integration Channel) on Accountant -> Bank feeds -> Accounts."
            )
        if acc.api_access_token_enc and expires and expires - TOKEN_EARLY_REFRESH > timezone.now():
            try:
                return tokens.decrypt(acc.api_access_token_enc)
            except tokens.TokenUnreadable:
                pass
        return self._refresh()

    def disconnect(self):
        acc = self.account
        acc.api_access_token_enc = ""
        acc.api_refresh_token_enc = ""
        acc.api_token_expires_at = None
        acc.api_connected_at = None
        acc.api_connected_by = None
        acc.api_needs_reconnect = False
        acc.save(update_fields=["api_access_token_enc", "api_refresh_token_enc", "api_token_expires_at",
                                "api_connected_at", "api_connected_by", "api_needs_reconnect"])

    # ---- transactions -----------------------------------------------------

    def fetch_transactions(self, since_date: date_cls, until_date: date_cls = None) -> dict:
        """PLACEHOLDER PATH / PARAMETERS until FNB's Swagger document arrives:
        GET {base}/accounts/{FNB account id or account number}/transactions
            ?fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD

        Returns {"transactions": [normalised dicts], "skipped": [reasons],
        "balance": Decimal or None, "pages": n}. A line that can't be read
        is skipped with its reason rather than failing the whole sync.
        """
        until_date = until_date or timezone.localdate()
        token = self._fetch_access_token()
        account_ref = self.account.api_account_id or self.account.account_number
        url = f"{self.account.api_base_url.rstrip('/')}/accounts/{account_ref}/transactions"
        params = {"fromDate": since_date.isoformat(), "toDate": until_date.isoformat()}
        rows, skipped, seen = [], [], {}
        balance = None
        pages = 0
        retried = False
        while url:
            pages += 1
            if pages > MAX_PAGES:
                raise FNBClientError(f"FNB's API kept returning more pages (stopped after {MAX_PAGES}). Check paging against the spec.")
            try:
                # Redirects ARE followed here: a redirected GET stays a GET.
                resp = requests.get(
                    url, params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise FNBClientError(f"Could not reach FNB's API at {url}: {exc}") from exc
            if resp.status_code == 401 and pages == 1 and not retried:
                # Token revoked early: renew once, then retry once.
                retried = True
                if self.account.api_grant == "client_credentials":
                    self.account.api_token_expires_at = None
                    token = self._fetch_access_token()
                else:
                    token = self._refresh()
                pages -= 1
                continue
            if resp.status_code >= 400:
                raise FNBClientError(self._describe_response("fetch transactions from", "GET", url, resp))
            try:
                payload = resp.json()
            except ValueError:
                raise FNBClientError(self._describe_response("fetch transactions from", "GET", url, resp) + " It was not JSON.")
            for raw in _extract_list(payload):
                try:
                    rows.append(normalise_transaction(raw, seen))
                except ValueError as exc:
                    ident = _first(raw, _ID_KEYS) if isinstance(raw, dict) else None
                    skipped.append(f"{ident or 'line'}: {exc}")
            if balance is None:
                balance = extract_balance(payload)
            nxt = _next_page(payload, url)
            if nxt is None:
                break
            url, page_params = nxt
            params = {**params, **page_params} if page_params else None
        return {"transactions": rows, "skipped": skipped, "balance": balance, "pages": pages}
