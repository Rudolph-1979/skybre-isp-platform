"""What the FNB client must tell you when it fails.

The endpoint paths in fnb_client are unverified guesses -- the module
docstring says so -- so failures are expected and the only thing that
matters is whether the failure explains itself. It used to not:

    raise FNBClientError(f"Could not authenticate with FNB's API: {exc}")

`exc` was requests' generic text, so a 405 reached the screen with no
status code, no URL, no redirect chain, and none of the bank's own error
body.

The 405 also had a specific cause worth catching: requests follows
redirects by default and DOWNGRADES a POST to a GET on 301/302/303, so a
base URL stored as http:// where the bank serves https:// sent a correct
POST that arrived as a GET. The token call no longer follows redirects,
so that surfaces as a message naming the Location header instead.
"""
from datetime import date
from unittest import mock

from django.test import TestCase

from bankfeeds.fnb_client import FNBClient, FNBClientError
from bankfeeds.models import BankAccount

SECRET = "super-secret-client-credential"


def _response(status_code, *, text="", json_data=None, headers=None, history=(), url="https://bank.example/oauth/token"):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    resp.history = history
    resp.url = url
    resp.is_redirect = status_code in (301, 302, 303, 307, 308) and "Location" in (headers or {})
    resp.is_permanent_redirect = status_code in (301, 308) and "Location" in (headers or {})
    if json_data is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_data
    return resp


class FNBTokenRequestTests(TestCase):
    def setUp(self):
        self.account = BankAccount.objects.create(
            name="FNB Business",
            account_number="62000000000",
            api_base_url="https://bank.example",
            api_client_id="skybre",
            api_client_secret=SECRET,
        )
        self.client_obj = FNBClient(self.account)

    # ---- the request itself ---------------------------------------------

    def test_the_token_request_is_a_post(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(200, json_data={"access_token": "tok"})
            self.assertEqual(self.client_obj._fetch_access_token(), "tok")
        post.assert_called_once()

    def test_the_token_request_does_not_follow_redirects(self):
        """The whole point: a followed redirect turns this POST into a GET."""
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(200, json_data={"access_token": "tok"})
            self.client_obj._fetch_access_token()
        self.assertIs(post.call_args.kwargs["allow_redirects"], False)

    # ---- the failures explain themselves --------------------------------

    # ---- a redirected grant is re-POSTed, not demoted to a GET ----------

    def test_a_same_host_redirect_is_followed_as_another_post(self):
        """The fix for the 405: requests would have turned hop two into a
        GET. Here it stays a POST and the token comes back."""
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.side_effect = [
                _response(302, headers={"Location": "/v2/oauth/token"}),
                _response(200, json_data={"access_token": "tok"}),
            ]
            self.assertEqual(self.client_obj._fetch_access_token(), "tok")

        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[1].args[0], "https://bank.example/v2/oauth/token")
        # Both hops carry the grant, and neither lets requests follow.
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["data"]["grant_type"], "client_credentials")
            self.assertIs(call.kwargs["allow_redirects"], False)

    def test_a_permanent_redirect_is_followed_too(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.side_effect = [
                _response(301, headers={"Location": "https://bank.example/oauth/token/"}),
                _response(200, json_data={"access_token": "tok"}),
            ]
            self.assertEqual(self.client_obj._fetch_access_token(), "tok")

    def test_two_hops_still_resolve(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.side_effect = [
                _response(301, headers={"Location": "/v2/oauth/token"}),
                _response(302, headers={"Location": "/v2/oauth/token/"}),
                _response(200, json_data={"access_token": "tok"}),
            ]
            self.assertEqual(self.client_obj._fetch_access_token(), "tok")

    def test_a_redirect_loop_fails_fast_rather_than_hanging(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(302, headers={"Location": "/oauth/token"})
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        self.assertIn("kept redirecting", str(caught.exception))
        self.assertLessEqual(post.call_count, 4)

    def test_a_failure_after_a_redirect_says_where_it_ended_up(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.side_effect = [
                _response(301, headers={"Location": "/v2/oauth/token"}),
                _response(401, text="bad client"),
            ]
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        message = str(caught.exception)
        self.assertIn("Re-POSTed through", message)
        self.assertIn("/v2/oauth/token", message)
        self.assertIn("bad client", message)

    def test_a_redirect_with_no_location_is_reported(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            resp = _response(302)
            resp.is_redirect = True
            post.return_value = resp
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        self.assertIn("no Location header", str(caught.exception))

    # ---- but the credential is never forwarded anywhere new -------------

    def test_a_cross_host_redirect_is_refused(self):
        """api_base_url is staff-typed free text and this request's body
        carries the client secret, so a redirect to a different host must
        not be followed."""
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(
                302, headers={"Location": "https://evil.example/collect"}
            )
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        message = str(caught.exception)
        self.assertIn("different host", message)
        self.assertIn("evil.example", message)
        # Refused before a second request is made, not after.
        self.assertEqual(post.call_count, 1)

    def test_an_https_to_http_downgrade_is_refused(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(
                302, headers={"Location": "http://bank.example/oauth/token"}
            )
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        self.assertIn("over the wire in clear", str(caught.exception))
        self.assertEqual(post.call_count, 1)

    def test_a_non_http_redirect_target_is_refused(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(302, headers={"Location": "file:///etc/passwd"})
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        self.assertIn("not http(s)", str(caught.exception))

    def test_an_http_base_url_may_still_be_upgraded_to_https(self):
        """The commonest real case: the base URL was stored as http:// and
        the bank redirects to https:// on the same host."""
        self.account.api_base_url = "http://bank.example"
        self.account.save(update_fields=["api_base_url"])
        client_obj = FNBClient(self.account)
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.side_effect = [
                _response(301, headers={"Location": "https://bank.example/oauth/token"}),
                _response(200, json_data={"access_token": "tok"}),
            ]
            self.assertEqual(client_obj._fetch_access_token(), "tok")
        self.assertEqual(post.call_args_list[1].args[0], "https://bank.example/oauth/token")

    def test_a_405_reports_the_status_the_url_and_the_body(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(
                405, text='{"error":"method_not_allowed","message":"Use /v2/auth"}'
            )
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        message = str(caught.exception)
        self.assertIn("405", message)
        self.assertIn("https://bank.example/oauth/token", message)
        self.assertIn("Use /v2/auth", message)
        self.assertIn("does not accept this method", message)

    def test_a_404_reports_the_body(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(404, text="no such endpoint")
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        self.assertIn("no such endpoint", str(caught.exception))

    def test_an_empty_body_says_so_rather_than_going_quiet(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(500, text="")
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        self.assertIn("empty body", str(caught.exception))

    def test_a_long_body_is_truncated(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(500, text="x" * 5000)
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        self.assertLess(len(str(caught.exception)), 1200)

    def test_a_non_json_200_is_reported_as_such(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(200, text="<html>login page</html>")
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        self.assertIn("not JSON", str(caught.exception))

    def test_json_without_a_token_lists_the_keys_returned(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.return_value = _response(200, json_data={"token": "t", "expires_in": 300})
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        message = str(caught.exception)
        self.assertIn("access_token", message)
        self.assertIn("expires_in", message)

    # ---- the secret must never appear in any of that --------------------

    def test_the_client_secret_never_reaches_the_error_message(self):
        """These messages land in a BankFeedSyncLog row staff can read.
        The token request's own payload carries api_client_secret, so only
        the RESPONSE is ever quoted."""
        hostile_body = f'{{"echo":"grant_type=client_credentials&client_secret={SECRET}"}}'
        for response in (
            _response(405, text=hostile_body),
            _response(302, headers={"Location": "https://elsewhere.example/t"}),
            _response(200, text="not json"),
        ):
            with mock.patch("bankfeeds.fnb_client.requests.post") as post:
                post.return_value = response
                with self.assertRaises(FNBClientError) as caught:
                    self.client_obj._fetch_access_token()
            # A body that echoes the secret back is the bank's problem, but
            # the message must never build it from our own credentials.
            if response.text != hostile_body:
                self.assertNotIn(SECRET, str(caught.exception))

    def test_a_network_error_names_the_url_it_could_not_reach(self):
        import requests as requests_module

        with mock.patch("bankfeeds.fnb_client.requests.post") as post:
            post.side_effect = requests_module.ConnectionError("dns failure")
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj._fetch_access_token()
        message = str(caught.exception)
        self.assertIn("https://bank.example/oauth/token", message)
        self.assertIn("dns failure", message)


class FNBTransactionFetchTests(TestCase):
    def setUp(self):
        self.account = BankAccount.objects.create(
            name="FNB Business 2",
            account_number="62000000001",
            api_base_url="https://bank.example",
            api_client_id="skybre",
            api_client_secret=SECRET,
        )
        self.client_obj = FNBClient(self.account)

    def test_a_failed_fetch_reports_status_url_and_body(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post, \
             mock.patch("bankfeeds.fnb_client.requests.get") as get:
            post.return_value = _response(200, json_data={"access_token": "tok"})
            get.return_value = _response(403, text="account not entitled")
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj.fetch_transactions(date(2026, 8, 1))
        message = str(caught.exception)
        self.assertIn("403", message)
        self.assertIn("account not entitled", message)
        self.assertIn("/accounts/62000000001/transactions", message)

    def test_a_redirect_chain_is_reported(self):
        hop = _response(301, url="https://bank.example/accounts/62000000001/transactions")
        with mock.patch("bankfeeds.fnb_client.requests.post") as post, \
             mock.patch("bankfeeds.fnb_client.requests.get") as get:
            post.return_value = _response(200, json_data={"access_token": "tok"})
            get.return_value = _response(
                404, text="gone", history=(hop,), url="https://api.bank.example/v2/transactions"
            )
            with self.assertRaises(FNBClientError) as caught:
                self.client_obj.fetch_transactions(date(2026, 8, 1))
        self.assertIn("Redirected:", str(caught.exception))

    def test_a_successful_fetch_still_normalises(self):
        with mock.patch("bankfeeds.fnb_client.requests.post") as post, \
             mock.patch("bankfeeds.fnb_client.requests.get") as get:
            post.return_value = _response(200, json_data={"access_token": "tok"})
            get.return_value = _response(200, json_data={"transactions": [
                {"id": 7, "date": "2026-08-02", "description": "EFT IN", "amount": "1200.00"},
            ]})
            rows = self.client_obj.fetch_transactions(date(2026, 8, 1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_id"], "7")
        self.assertEqual(rows[0]["amount"], "1200.00")

    def test_a_missing_base_url_is_refused_before_any_request(self):
        account = BankAccount.objects.create(
            name="No API", account_number="62000000002", api_base_url=""
        )
        with self.assertRaises(FNBClientError) as caught:
            FNBClient(account)
        self.assertIn("CSV import", str(caught.exception))
