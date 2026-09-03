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

import requests


class FNBClientError(Exception):
    """Raised for any failure talking to FNB's API -- auth failure,
    network error, an account with no api_base_url configured, or an
    unexpected response shape. sync.py catches this and logs it on a
    BankFeedSyncLog rather than letting one bad account take down the
    whole hourly run across all 4 accounts."""


class FNBClient:
    def __init__(self, account):
        """`account` is a bankfeeds.models.BankAccount row -- reads
        api_base_url/api_client_id/api_client_secret from it."""
        if not account.api_base_url:
            raise FNBClientError(
                "No API base URL configured for this account. Either use CSV import for now, or fill in "
                "the API connection details once FNB confirms API access for this account."
            )
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

    def _fetch_access_token(self) -> str:
        """PLACEHOLDER: assumes a standard OAuth2 client-credentials
        grant (the most common shape for this kind of bank API) -- update
        the path/payload/response field name once FNB's actual auth flow
        is confirmed.

        allow_redirects=False, deliberately. requests follows redirects by
        default and, on a 301, 302 or 303, DOWNGRADES a POST to a GET
        (Session.resolve_redirects does this). So a base URL that redirects
        at all -- http:// stored where the bank serves https://, or a
        trailing-slash redirect on the token path -- sent a correct POST
        that arrived as a GET, and came back 405 Method Not Allowed with
        nothing on screen to suggest a redirect had happened. Refusing to
        follow it turns that into a message naming the Location header.
        """
        url = f"{self.account.api_base_url.rstrip('/')}/oauth/token"
        try:
            resp = requests.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.account.api_client_id,
                    "client_secret": self.account.api_client_secret,
                },
                timeout=15,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FNBClientError(f"Could not reach FNB's API at {url}: {exc}") from exc

        # 3xx is not an error to raise_for_status(), so it has to be caught
        # here or the redirect body falls through to .json() and surfaces
        # as an unrelated parse error.
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location") or "(no Location header)"
            raise FNBClientError(
                f"FNB's API redirected the token request: POST {url} returned HTTP "
                f"{resp.status_code} pointing at {location}. Redirects are not followed on this "
                "call because a redirected POST becomes a GET, which is what produces a 405 here. "
                f"Set this account's API base URL so the token path resolves to {location} "
                "directly -- usually that means switching http:// to https://, or removing or "
                "adding a trailing slash."
            )

        if resp.status_code >= 400:
            raise FNBClientError(self._describe_response("authenticate with", "POST", url, resp))

        try:
            payload = resp.json()
        except ValueError:
            raise FNBClientError(
                self._describe_response("read a token from", "POST", url, resp)
                + " It was not JSON."
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
