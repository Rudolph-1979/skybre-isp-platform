"""Placeholder FNB bank-feed API client.

FNB (South Africa) doesn't have a confirmed, self-service developer API
for reading arbitrary business-account transaction history -- South
Africa has no open-banking regulatory mandate, and bank-side statement/
transaction APIs there tend to be partner-gated (a direct conversation
with FNB business banking, not a public signup form). This module is
deliberately isolated so that once real access is confirmed -- and the
actual base URL, auth flow, and response shape are known -- swapping in
the real implementation is a small, contained change to this one file.
Nothing else in bankfeeds (sync.py, the views, the frontend) cares how
fetch_transactions() got its answer, only that it returns a list of
normalized dicts.

Until that access is confirmed, use CSV import instead (see
csv_import.py) -- a real, usable path today that doesn't depend on this
file at all.

IMPORTANT: every endpoint path, auth flow, and field name below is a
REASONABLE GUESS at a typical bank client-credentials + REST transactions
API shape (this is NOT verified against FNB's actual API -- this
environment couldn't reach the internet to confirm it while this was
built). Update _fetch_access_token() and fetch_transactions() once real
FNB API docs/credentials are available; everything else in this app
should keep working unchanged.
"""
from datetime import date as date_cls
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings


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


class FNBClient:
    def __init__(self, account):
        """`account` is a bankfeeds.models.BankAccount row -- reads
        api_base_url/api_client_id/api_client_secret from it."""
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
        except ValueError as exc:
            raise FNBClientError(str(exc)) from exc
        self.account = account

    @staticmethod
    def _describe_response(action: str, method: str, url: str, resp) -> str:
        """A failure message that says what actually came back.

        This used to be `f"Could not authenticate with FNB's API: {exc}"`,
        where `exc` was requests' own generic text -- so a 405 arrived on
        screen with no status code, no URL, no redirect chain and, most
        importantly, none of the bank's own error body. The one piece of
        information that would explain the failure was the one piece being
        thrown away.

        Only the RESPONSE body is quoted. The token request's own payload
        carries api_client_secret, so it is never included here -- this
        message ends up in a BankFeedSyncLog row that staff can read.
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
                "wrong (every path in this module is an unverified guess -- see the module "
                "docstring) or the request was redirected and demoted; see the note in "
                "_fetch_access_token."
            )
        if body:
            parts.append(f"Response body: {body}")
        else:
            parts.append("The response had an empty body.")
        return " ".join(parts)

    # A redirected token request is followed by hand, not by requests, and
    # never more than this many times. Three is enough for the shapes that
    # actually occur (http -> https, then a trailing-slash or version-path
    # move) and short enough that a redirect loop fails fast.
    _MAX_TOKEN_REDIRECTS = 3

    def _post_token_once(self, url):
        """One POST of the client-credentials grant, redirects unfollowed."""
        try:
            return requests.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.account.api_client_id,
                    "client_secret": self.account.api_client_secret,
                },
                timeout=15,
                # Never let requests follow this one. See _fetch_access_token.
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FNBClientError(f"Could not reach FNB's API at {url}: {exc}") from exc

    def _resolve_token_redirect(self, url, resp):
        """Where to re-POST after a 3xx, or raise saying why we won't.

        requests follows redirects by default and, on a 301, 302 or 303,
        DOWNGRADES a POST to a GET -- that is Session.resolve_redirects'
        documented behaviour, inherited from what browsers do with form
        submissions. So a base URL that redirects at all (http:// stored
        where the bank serves https://, or a trailing-slash redirect on
        the token path) sent a correct POST that arrived as a GET, and
        came back 405 Method Not Allowed with nothing to suggest a
        redirect had happened.

        Following it here instead keeps the method: the grant is re-POSTed
        to the new location, which is what every one of these redirects
        actually means. Two things are refused rather than followed,
        because this request carries api_client_secret in its body:

          * a redirect to a DIFFERENT host, which would hand the client
            credential to whoever that host belongs to. api_base_url is
            staff-typed free text, so this is the difference between
            following a bank's own http -> https move and forwarding a
            secret to anywhere a typo points.
          * a redirect from https:// down to http://, which would put the
            credential on the wire in clear.
        """
        location = resp.headers.get("Location")
        if not location:
            raise FNBClientError(
                f"FNB's API returned HTTP {resp.status_code} for POST {url} with no Location "
                "header, so there is nothing to follow. Check this account's API base URL."
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
                f"API base URL to it directly so the credential is only ever sent somewhere "
                "chosen deliberately."
            )
        return target

    def _fetch_access_token(self) -> str:
        """PLACEHOLDER: assumes a standard OAuth2 client-credentials
        grant (the most common shape for this kind of bank API) -- update
        the path/payload/response field name once FNB's actual auth flow
        is confirmed.

        Redirects are resolved here rather than by requests, so that a
        redirected grant is re-POSTed instead of being silently turned
        into a GET and answered with a 405. See _resolve_token_redirect.
        """
        url = f"{self.account.api_base_url.rstrip('/')}/oauth/token"
        followed = []

        for _ in range(self._MAX_TOKEN_REDIRECTS + 1):
            resp = self._post_token_once(url)
            # 3xx is not an error to raise_for_status(), so it has to be
            # handled explicitly or the redirect body falls through to
            # .json() and surfaces as an unrelated parse error.
            if not (resp.is_redirect or resp.is_permanent_redirect):
                break
            followed.append(f"{resp.status_code} {url}")
            url = self._resolve_token_redirect(url, resp)
        else:
            raise FNBClientError(
                "FNB's API kept redirecting the token request "
                f"({self._MAX_TOKEN_REDIRECTS} hops without an answer): "
                + " -> ".join(followed)
                + f" -> {url}. That is usually a redirect loop; check this account's API base URL."
            )

        trail = ""
        if followed:
            # Worth saying out loud on a failure: the request that failed is
            # not at the URL anybody configured.
            trail = " Re-POSTed through: " + " -> ".join(followed) + f" -> {url}."

        if resp.status_code >= 400:
            raise FNBClientError(
                self._describe_response("authenticate with", "POST", url, resp) + trail
            )

        try:
            payload = resp.json()
        except ValueError:
            raise FNBClientError(
                self._describe_response("read a token from", "POST", url, resp)
                + " It was not JSON."
                + trail
            )

        token = payload.get("access_token")
        if not token:
            raise FNBClientError(
                "FNB's API responded to the token request, but the JSON contained no "
                f"'access_token' field. Keys returned: {sorted(payload)}."
            )
        return token

    def fetch_transactions(self, since_date: date_cls) -> list:
        """PLACEHOLDER: assumes
        GET {base_url}/accounts/{account_number}/transactions?from=YYYY-MM-DD
        returning {"transactions": [{"id": ..., "date": ..., "description": ..., "amount": ...}, ...]}.

        Returns a list of normalized dicts: {"external_id", "date",
        "description", "amount", "raw"}. Update this once FNB's real
        endpoint/response shape is confirmed -- callers (sync.py) only
        ever see this normalized shape, never the raw API response.
        """
        token = self._fetch_access_token()
        url = (
            f"{self.account.api_base_url.rstrip('/')}"
            f"/accounts/{self.account.account_number}/transactions"
        )
        try:
            # Redirects ARE followed here: a redirected GET stays a GET, so
            # it cannot be silently demoted the way the token POST could.
            resp = requests.get(
                url,
                params={"from": since_date.isoformat()},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise FNBClientError(f"Could not reach FNB's API at {url}: {exc}") from exc

        if resp.status_code >= 400:
            raise FNBClientError(
                self._describe_response("fetch transactions from", "GET", url, resp)
            )

        try:
            payload = resp.json()
        except ValueError:
            raise FNBClientError(
                self._describe_response("fetch transactions from", "GET", url, resp)
                + " It was not JSON."
            )
        raw_transactions = payload.get("transactions", [])
        normalized = []
        for raw in raw_transactions:
            try:
                normalized.append(
                    {
                        "external_id": str(raw["id"]),
                        "date": raw["date"],
                        "description": raw.get("description", ""),
                        "amount": raw["amount"],
                        "raw": raw,
                    }
                )
            except KeyError as exc:
                raise FNBClientError(f"Unexpected transaction shape from FNB's API (missing {exc}).") from exc
        return normalized
