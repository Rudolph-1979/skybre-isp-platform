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

    def _fetch_access_token(self) -> str:
        """PLACEHOLDER: assumes a standard OAuth2 client-credentials
        grant (the most common shape for this kind of bank API) -- update
        the path/payload/response field name once FNB's actual auth flow
        is confirmed."""
        try:
            resp = requests.post(
                f"{self.account.api_base_url.rstrip('/')}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.account.api_client_id,
                    "client_secret": self.account.api_client_secret,
                },
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise FNBClientError(f"Could not authenticate with FNB's API: {exc}") from exc

        token = resp.json().get("access_token")
        if not token:
            raise FNBClientError("FNB's API responded, but didn't include an access token.")
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
        try:
            resp = requests.get(
                f"{self.account.api_base_url.rstrip('/')}/accounts/{self.account.account_number}/transactions",
                params={"from": since_date.isoformat()},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise FNBClientError(f"Could not fetch transactions from FNB's API: {exc}") from exc

        payload = resp.json()
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
