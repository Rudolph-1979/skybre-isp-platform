"""#185: FNB Integration Channel -- authorisation code connection, reading
FNB's transaction lines, CSV/API duplicates, and a sync that can't be
knocked over by one bad line or one bad account."""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import jwt
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from bankfeeds import tokens
from bankfeeds.fnb_client import (
    FNBClient, FNBClientError, FNBReconnectNeeded, extract_balance, normalise_transaction,
    parse_bank_date, redirect_uri_allowed,
)
from bankfeeds.ingest import create_transaction_if_new
from bankfeeds.models import BankAccount, BankTransaction
from bankfeeds.sync import sync_all_active_accounts, sync_bank_account

User = get_user_model()
SECRET = "client-secret-xyz-long-enough-for-hs256-0123456789"
CALLBACK = "https://portal.skybre.co.za/bank-feeds/fnb-callback"


def _resp(status, json_data=None, text="", headers=None):
    r = mock.Mock()
    r.status_code = status
    r.text = text
    r.headers = headers or {}
    r.history = ()
    r.url = "https://bank.example/x"
    r.is_redirect = False
    r.is_permanent_redirect = False
    if json_data is None:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = json_data
    return r


def _account(**kw):
    defaults = dict(name="FNB Cheque", account_number="62000000000", api_base_url="https://bank.example",
                    api_client_id="skybre", api_client_secret=SECRET)
    defaults.update(kw)
    return BankAccount.objects.create(**defaults)


@override_settings(ALLOWED_HOSTS=["portal.skybre.co.za", "localhost", "testserver"], SITE_URL="https://skybre.co.za", FNB_REDIRECT_URI="")
class ConnectTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="boss", password="x", role="admin")
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.account = _account()

    def _token_answer(self, **extra):
        return _resp(200, {"access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 600, **extra})

    def test_auth_code_is_exchanged_and_tokens_stored_encrypted(self):
        with mock.patch("bankfeeds.fnb_client.requests.post", return_value=self._token_answer()) as post:
            r = self.api.post(f"/api/bank-accounts/{self.account.id}/fnb-connect/",
                              {"code": "abc", "redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        data = post.call_args.kwargs["data"]
        self.assertEqual(data["grant_type"], "authorization_code")
        self.assertEqual(data["code"], "abc")
        self.assertEqual(data["redirect_uri"], CALLBACK)
        self.account.refresh_from_db()
        self.assertNotIn("acc-1", self.account.api_access_token_enc)
        self.assertEqual(tokens.decrypt(self.account.api_access_token_enc), "acc-1")
        self.assertEqual(tokens.decrypt(self.account.api_refresh_token_enc), "ref-1")
        self.assertEqual(self.account.connection_status, "connected")
        self.assertEqual(r.data["connection_status"], "connected")
        self.assertEqual(r.data["api_connected_by_name"], "boss")

    def test_tokens_never_appear_in_the_api(self):
        self.account.api_access_token_enc = tokens.encrypt("acc-1")
        self.account.save()
        body = str(self.api.get(f"/api/bank-accounts/{self.account.id}/").data)
        self.assertNotIn("acc-1", body)
        self.assertNotIn("token_enc", body)
        self.assertNotIn(SECRET, body)

    def test_only_our_own_callback_is_accepted_as_redirect_uri(self):
        self.assertTrue(redirect_uri_allowed(CALLBACK))
        for bad in ("https://evil.example/bank-feeds/fnb-callback", "http://portal.skybre.co.za/bank-feeds/fnb-callback",
                    "https://portal.skybre.co.za/elsewhere", CALLBACK + "?x=1", ""):
            self.assertFalse(redirect_uri_allowed(bad), bad)
        r = self.api.post(f"/api/bank-accounts/{self.account.id}/fnb-connect/",
                          {"code": "abc", "redirect_uri": "https://evil.example/bank-feeds/fnb-callback"}, format="json")
        self.assertEqual(r.status_code, 400)

    @override_settings(FNB_REDIRECT_URI="https://portal.skybre.co.za/bank-feeds/fnb-callback/")
    def test_a_fixed_redirect_uri_setting_must_match_exactly(self):
        self.assertTrue(redirect_uri_allowed("https://portal.skybre.co.za/bank-feeds/fnb-callback/"))
        self.assertFalse(redirect_uri_allowed(CALLBACK))

    def test_authorize_url_and_callback_with_state(self):
        self.account.api_authorize_url = "https://bank.example/authorize"
        self.account.api_scope = "transactions"
        self.account.save()
        r = self.api.post(f"/api/bank-accounts/{self.account.id}/fnb-authorize/", {"redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        url = r.data["url"]
        self.assertTrue(url.startswith("https://bank.example/authorize?"))
        self.assertIn("response_type=code", url)
        self.assertIn("client_id=skybre", url)
        self.assertIn("scope=transactions", url)
        state = url.split("state=")[1].split("&")[0]
        from urllib.parse import unquote

        with mock.patch("bankfeeds.fnb_client.requests.post", return_value=self._token_answer()):
            r = self.api.post("/api/bank-accounts/fnb-callback/",
                              {"code": "abc", "state": unquote(state), "redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["id"], self.account.id)

    def test_callback_state_from_someone_else_or_forged_is_refused(self):
        from django.core import signing

        other = User.objects.create_user(username="other", password="x", role="admin")
        state = signing.dumps({"a": self.account.pk, "u": other.pk}, salt="bankfeeds-fnb-state")
        r = self.api.post("/api/bank-accounts/fnb-callback/", {"code": "a", "state": state, "redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.api.post("/api/bank-accounts/fnb-callback/", {"code": "a", "state": "junk", "redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_callback_without_state_needs_the_account(self):
        r = self.api.post("/api/bank-accounts/fnb-callback/", {"code": "a", "redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 400)
        with mock.patch("bankfeeds.fnb_client.requests.post", return_value=self._token_answer()):
            r = self.api.post("/api/bank-accounts/fnb-callback/",
                              {"code": "a", "account": self.account.id, "redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

    def test_connecting_is_admin_only(self):
        acct = User.objects.create_user(username="acc", password="x", role="accounts", allowed_sections=["accountant"])
        api = APIClient()
        api.force_authenticate(acct)
        r = api.post(f"/api/bank-accounts/{self.account.id}/fnb-connect/", {"code": "a", "redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_a_refused_code_is_reported_without_secrets(self):
        with mock.patch("bankfeeds.fnb_client.requests.post", return_value=_resp(400, text='{"error":"invalid_grant"}')):
            r = self.api.post(f"/api/bank-accounts/{self.account.id}/fnb-connect/",
                              {"code": "abc", "redirect_uri": CALLBACK}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("invalid_grant", r.data["detail"])
        self.assertNotIn(SECRET, r.data["detail"])

    def test_disconnect_clears_tokens(self):
        self.account.api_refresh_token_enc = tokens.encrypt("r")
        self.account.save()
        r = self.api.post(f"/api/bank-accounts/{self.account.id}/fnb-disconnect/")
        self.assertEqual(r.data["connection_status"], "not_connected")

    def test_changing_the_client_id_drops_the_old_connection(self):
        self.account.api_refresh_token_enc = tokens.encrypt("r")
        self.account.save()
        r = self.api.patch(f"/api/bank-accounts/{self.account.id}/", {"api_client_id": "new-client"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["connection_status"], "not_connected")
        r = self.api.patch(f"/api/bank-accounts/{self.account.id}/", {"name": "Renamed"}, format="json")
        self.assertEqual(r.data["connection_status"], "not_connected")

    def test_token_url_is_validated_like_the_base_url(self):
        r = self.api.patch(f"/api/bank-accounts/{self.account.id}/", {"api_token_url": "https://portal.skybre.co.za/oauth/token"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("api_token_url", r.data)


class TokenLifecycleTests(TestCase):
    def setUp(self):
        self.account = _account(api_access_token_enc=tokens.encrypt("acc-old"),
                                api_refresh_token_enc=tokens.encrypt("ref-old"),
                                api_token_expires_at=timezone.now() + timedelta(minutes=10))

    def test_a_valid_access_token_is_reused_without_a_request(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            self.assertEqual(FNBClient(self.account)._fetch_access_token(), "acc-old")
        post.assert_not_called()

    def test_an_expired_token_is_refreshed_and_rotation_kept(self):
        self.account.api_token_expires_at = timezone.now() - timedelta(seconds=1)
        self.account.save()
        with mock.patch("bankfeeds.fnb_client.requests.post",
                        return_value=_resp(200, {"access_token": "acc-new", "refresh_token": "ref-new", "expires_in": 300})) as post:
            self.assertEqual(FNBClient(self.account)._fetch_access_token(), "acc-new")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(post.call_args.kwargs["data"]["refresh_token"], "ref-old")
        self.account.refresh_from_db()
        self.assertEqual(tokens.decrypt(self.account.api_refresh_token_enc), "ref-new")

    def test_a_refused_refresh_marks_reconnect_needed(self):
        self.account.api_token_expires_at = None
        self.account.save()
        with mock.patch("bankfeeds.fnb_client.requests.post", return_value=_resp(400, text='{"error":"invalid_grant"}')):
            with self.assertRaises(FNBReconnectNeeded):
                FNBClient(self.account)._fetch_access_token()
        self.account.refresh_from_db()
        self.assertTrue(self.account.api_needs_reconnect)
        self.assertEqual(self.account.connection_status, "reconnect")

    def test_not_connected_says_how_to_connect(self):
        acc = _account(name="Two", account_number="1")
        with self.assertRaises(FNBReconnectNeeded) as caught:
            FNBClient(acc)._fetch_access_token()
        self.assertIn("Connect to FNB", str(caught.exception))

    def test_a_changed_secret_key_means_reconnect_not_a_crash(self):
        self.account.api_token_expires_at = None
        self.account.save()
        with override_settings(SECRET_KEY="a-different-key-entirely-1234567890"):
            with self.assertRaises(FNBReconnectNeeded):
                FNBClient(self.account)._fetch_access_token()

    def test_jwt_client_authentication(self):
        self.account.api_client_auth = "jwt"
        self.account.api_token_expires_at = None
        self.account.save()
        with mock.patch("bankfeeds.fnb_client.requests.post",
                        return_value=_resp(200, {"access_token": "a"})) as post:
            FNBClient(self.account)._fetch_access_token()
        data = post.call_args.kwargs["data"]
        self.assertNotIn("client_secret", data)
        claims = jwt.decode(data["client_assertion"], SECRET, algorithms=["HS256"], audience="https://bank.example/oauth/token")
        self.assertEqual(claims["iss"], "skybre")

    def test_basic_client_authentication(self):
        self.account.api_client_auth = "basic"
        self.account.api_token_expires_at = None
        self.account.save()
        with mock.patch("bankfeeds.fnb_client.requests.post", return_value=_resp(200, {"access_token": "a"})) as post:
            FNBClient(self.account)._fetch_access_token()
        self.assertEqual(post.call_args.kwargs["auth"], ("skybre", SECRET))
        self.assertNotIn("client_secret", post.call_args.kwargs["data"])

    def test_client_credentials_fallback(self):
        acc = _account(name="CC", account_number="2", api_grant="client_credentials")
        self.assertEqual(acc.connection_status, "connected")
        with mock.patch("bankfeeds.fnb_client.requests.post", return_value=_resp(200, {"access_token": "cc"})) as post:
            self.assertEqual(FNBClient(acc)._fetch_access_token(), "cc")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "client_credentials")

    def test_custom_token_url(self):
        self.account.api_token_url = "https://auth.bank.example/oauth2/token"
        self.account.api_token_expires_at = None
        self.account.save()
        with mock.patch("bankfeeds.fnb_client.requests.post", return_value=_resp(200, {"access_token": "a"})) as post:
            FNBClient(self.account)._fetch_access_token()
        self.assertEqual(post.call_args.args[0], "https://auth.bank.example/oauth2/token")


class NormaliseTests(TestCase):
    def test_fnb_fields_with_indicator(self):
        row = normalise_transaction({
            "transactionId": "T1", "bookingDate": "2026-10-15", "valueDate": "2026-10-16",
            "transactionDetails": "FNB APP PAYMENT FROM", "reference": "SW1057",
            "amount": "599.00", "currency": "ZAR", "creditDebitIndicator": "CRDT",
        })
        self.assertEqual(row["external_id"], "fnb:T1")
        self.assertEqual(row["date"], date(2026, 10, 15))
        self.assertEqual(row["amount"], Decimal("599.00"))
        self.assertEqual(row["reference"], "SW1057")

    def test_the_indicator_decides_the_sign(self):
        for amount, ind, want in (("35.50", "D", "-35.50"), ("-35.50", "DBIT", "-35.50"), ("-10", "C", "10.00"),
                                  ("10", "DEBIT", "-10.00"), ("-7.25", "", "-7.25")):
            row = normalise_transaction({"id": 1, "date": "2026-10-01", "amount": amount, "creditDebitIndicator": ind})
            self.assertEqual(row["amount"], Decimal(want), (amount, ind))

    def test_amount_shapes(self):
        for raw, want in (({"amount": "1200.00", "currency": "ZAR"}, "1200.00"), ("R 1 200,50", "1200.50"),
                          ("1,200.50", "1200.50"), (99, "99.00"), (12.5, "12.50")):
            row = normalise_transaction({"id": 1, "date": "2026-10-01", "transactionAmount": raw})
            self.assertEqual(row["amount"], Decimal(want), raw)

    def test_date_shapes(self):
        for raw in ("2026-10-16", "2026-10-16T08:00:00Z", "20261016", "16/10/2026", 1792108800000):
            self.assertEqual(parse_bank_date(raw), date(2026, 10, 16), raw)
        self.assertIsNone(parse_bank_date("rubbish"))

    def test_unreadable_lines_raise_with_the_reason(self):
        with self.assertRaises(ValueError):
            normalise_transaction({"id": 1, "date": "nope", "amount": "1"})
        with self.assertRaises(ValueError):
            normalise_transaction({"id": 1, "date": "2026-10-01", "amount": "abc"})
        with self.assertRaises(ValueError):
            normalise_transaction({"id": 1, "date": "2026-10-01", "amount": "1", "currency": "USD"})

    def test_lines_without_an_id_get_stable_distinct_ids(self):
        line = {"date": "2026-10-01", "amount": "599", "details": "EFT"}
        seen = {}
        a, b = normalise_transaction(dict(line), seen), normalise_transaction(dict(line), seen)
        self.assertNotEqual(a["external_id"], b["external_id"])
        seen2 = {}
        self.assertEqual(normalise_transaction(dict(line), seen2)["external_id"], a["external_id"])

    def test_balance_extraction(self):
        self.assertEqual(extract_balance({"balances": [{"type": "available", "amount": "10"},
                                                       {"type": "closingBooked", "amount": {"amount": "1500.25"}}]}), Decimal("1500.25"))
        self.assertEqual(extract_balance({"closingBalance": "99.10"}), Decimal("99.10"))
        self.assertIsNone(extract_balance({"transactions": []}))


class FetchTests(TestCase):
    def setUp(self):
        self.account = _account(api_access_token_enc=tokens.encrypt("acc"),
                                api_token_expires_at=timezone.now() + timedelta(hours=1), api_account_id="ACC-9")

    def test_paging_and_skipping_and_balance(self):
        page1 = {"transactions": [{"transactionId": "1", "bookingDate": "2026-10-01", "amount": "10", "creditDebitIndicator": "C"},
                                  {"transactionId": "2", "bookingDate": "bad", "amount": "5"}],
                 "balances": [{"type": "closing", "amount": "500"}], "links": {"next": "/accounts/ACC-9/transactions?page=2"}}
        page2 = {"transactions": [{"transactionId": "3", "bookingDate": "2026-10-02", "amount": "4", "creditDebitIndicator": "D"}]}
        with mock.patch("bankfeeds.fnb_client.requests.get", side_effect=[_resp(200, page1), _resp(200, page2)]) as get:
            result = FNBClient(self.account).fetch_transactions(date(2026, 10, 1), date(2026, 10, 16))
        self.assertEqual([r["external_id"] for r in result["transactions"]], ["fnb:1", "fnb:3"])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["balance"], Decimal("500.00"))
        self.assertEqual(result["pages"], 2)
        first = get.call_args_list[0]
        self.assertIn("/accounts/ACC-9/transactions", first.args[0])
        self.assertEqual(first.kwargs["params"], {"fromDate": "2026-10-01", "toDate": "2026-10-16"})
        self.assertEqual(first.kwargs["headers"]["Authorization"], "Bearer acc")

    def test_page_tokens(self):
        with mock.patch("bankfeeds.fnb_client.requests.get", side_effect=[
            _resp(200, {"transactions": [], "nextPageToken": "p2"}), _resp(200, {"transactions": []}),
        ]) as get:
            FNBClient(self.account).fetch_transactions(date(2026, 10, 1))
        self.assertEqual(get.call_args_list[1].kwargs["params"]["pageToken"], "p2")

    def test_a_next_link_to_another_host_is_refused(self):
        with mock.patch("bankfeeds.fnb_client.requests.get",
                        return_value=_resp(200, {"transactions": [], "links": {"next": "https://evil.example/p2"}})):
            with self.assertRaises(FNBClientError):
                FNBClient(self.account).fetch_transactions(date(2026, 10, 1))

    def test_a_401_refreshes_once(self):
        self.account.api_refresh_token_enc = tokens.encrypt("ref")
        self.account.save()
        with mock.patch("bankfeeds.fnb_client.requests.get", side_effect=[_resp(401, text="expired"), _resp(200, {"transactions": []})]), \
             mock.patch("bankfeeds.fnb_client.requests.post", return_value=_resp(200, {"access_token": "acc2"})):
            result = FNBClient(self.account).fetch_transactions(date(2026, 10, 1))
        self.assertEqual(result["pages"], 1)


class DuplicateTests(TestCase):
    def setUp(self):
        self.account = _account(opening_balance=Decimal("0"), opening_balance_date=date(2026, 10, 1))

    def _csv(self, ext, amount="599.00", desc="EFT SW1057", d=date(2026, 10, 5)):
        return create_transaction_if_new(account=self.account, source="csv_import", external_id=ext,
                                         date=d, description=desc, amount=Decimal(amount))

    def _api(self, ext, amount="599.00", desc="EFT", d=date(2026, 10, 5), ref="SW1057"):
        return create_transaction_if_new(account=self.account, source="api", external_id=ext,
                                         date=d, description=desc, amount=Decimal(amount), reference=ref)

    def test_api_line_already_imported_by_csv_is_a_duplicate(self):
        csv_txn, _, _ = self._csv("csv:1")
        api_txn, created, matched = self._api("fnb:1")
        self.assertTrue(created)
        self.assertFalse(matched)
        self.assertEqual(api_txn.duplicate_of, csv_txn)
        self.assertEqual(api_txn.status, "ignored")
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal("599.00"))

    def test_csv_after_api_is_a_duplicate_too(self):
        api_txn, _, _ = self._api("fnb:1")
        csv_txn, _, _ = self._csv("csv:1")
        self.assertEqual(csv_txn.duplicate_of, api_txn)

    def test_two_real_payments_of_the_same_amount_pair_one_to_one(self):
        c1, _, _ = self._csv("csv:1", desc="EFT SW1057")
        c2, _, _ = self._csv("csv:2", desc="EFT SR2427")
        a1, _, _ = self._api("fnb:1", ref="SR2427")
        a2, _, _ = self._api("fnb:2", ref="SW1057")
        a3, _, _ = self._api("fnb:3", ref="SW9999")
        self.assertEqual(a1.duplicate_of, c2)
        self.assertEqual(a2.duplicate_of, c1)
        self.assertIsNone(a3.duplicate_of)
        self.assertEqual(self.account.current_balance, Decimal("1797.00"))

    def test_same_source_lines_are_never_paired(self):
        self._csv("csv:1")
        second, _, _ = self._csv("csv:2")
        self.assertIsNone(second.duplicate_of)

    def test_reference_is_used_for_matching(self):
        from customers.models import Customer

        c = Customer.objects.create(full_name="United Funeral Home", email="u@x.com", customer_id="SW1057")
        txn, _, matched = self._api("fnb:9", desc="FNB APP PAYMENT", ref="SW1057", d=date(2026, 10, 9))
        self.assertTrue(matched)
        self.assertEqual(txn.matched_customer, c)

    def test_a_duplicate_cannot_be_confirmed_and_unmatch_unpairs_it(self):
        admin = User.objects.create_user(username="boss", password="x", role="admin")
        api = APIClient()
        api.force_authenticate(admin)
        self._csv("csv:1")
        dup, _, _ = self._api("fnb:1")
        r = api.post(f"/api/bank-transactions/{dup.id}/confirm/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("duplicate", r.data["detail"])
        r = api.post(f"/api/bank-transactions/{dup.id}/unmatch/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        dup.refresh_from_db()
        self.assertIsNone(dup.duplicate_of)
        self.assertEqual(dup.status, "unmatched")
        self.assertEqual(self.account.current_balance, Decimal("1198.00"))


class SyncTests(TestCase):
    def test_csv_only_accounts_are_skipped_by_the_hourly_run(self):
        _account(name="CSV", account_number="1", api_base_url="")
        self.assertEqual(sync_all_active_accounts(), [])

    def test_an_unexpected_error_is_logged_not_raised(self):
        a = _account(api_access_token_enc=tokens.encrypt("x"), api_token_expires_at=timezone.now() + timedelta(hours=1))
        b = _account(name="Second", account_number="2", api_access_token_enc=tokens.encrypt("x"),
                     api_token_expires_at=timezone.now() + timedelta(hours=1))
        with mock.patch("bankfeeds.sync.FNBClient.fetch_transactions", side_effect=[KeyError("boom"), {
            "transactions": [], "skipped": [], "balance": Decimal("12.00"), "pages": 1}]):
            logs = sync_all_active_accounts()
        self.assertEqual([l.status for l in logs], ["failed", "success"])
        self.assertIn("KeyError", logs[0].status_message)
        b.refresh_from_db()
        self.assertEqual(b.bank_balance, Decimal("12.00"))

    def test_sync_stores_lines_reports_skips_and_overlaps(self):
        a = _account(api_access_token_enc=tokens.encrypt("x"), api_token_expires_at=timezone.now() + timedelta(hours=1),
                     last_synced_at=timezone.now() - timedelta(days=1))
        row = normalise_transaction({"transactionId": "1", "bookingDate": "2026-10-10", "amount": "5", "creditDebitIndicator": "D",
                                     "transactionDetails": "BANK FEE"})
        before = a.last_synced_at
        with mock.patch("bankfeeds.sync.FNBClient.fetch_transactions", return_value={
                "transactions": [row], "skipped": ["7: no readable date"], "balance": None, "pages": 1}) as fetch:
            log = sync_bank_account(a)
        since = fetch.call_args.args[0]
        self.assertEqual(since, timezone.localdate(before) - timedelta(days=3))
        self.assertEqual(log.status, "success")
        self.assertEqual(log.transactions_new, 1)
        self.assertIn("couldn't be read", log.status_message)
        t = BankTransaction.objects.get(account=a)
        self.assertEqual(t.amount, Decimal("-5.00"))
        self.assertEqual(t.source, "api")
