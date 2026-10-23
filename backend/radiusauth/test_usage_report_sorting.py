"""Sorting the usage report, and exporting it.

Two decisions are pinned here because both are invisible until they are
wrong.

THE ROW LIMIT IS APPLIED BEFORE THE ORDERING. The table caps at 200 rows.
If that cut were taken after an arbitrary sort, clicking "Customer" would
change WHICH customers the page is about -- the top 200 becomes "A
through D", and the heaviest users, the entire reason for the report,
drop off the page without a word. The cut decides who matters; the
ordering only arranges them.

THE EXPORT HAS NO LIMIT. A CSV that silently stopped at 200 rows would
be worse than no CSV: the file looks complete, and the customers missing
from it are missing silently. It is also the reason the export exists
server-side rather than being built from the loaded rows in the browser.
"""
import csv
import datetime
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Service, Tariff
from customers.models import Customer, Partner
from radiusauth import usage
from radiusauth.views import UsageReportExportView, UsageReportView

User = get_user_model()
MB = 1024 * 1024
WHEN = datetime.date(2026, 8, 26)


def at(y, m, d, h=0):
    return timezone.make_aware(datetime.datetime(y, m, d, h), timezone.get_current_timezone())


class UsageReportSortingTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="boss", password="x", role="admin")
        self.capped = Tariff.objects.create(
            name="Capped 1GB", price=Decimal("1"), speed_download_kbps=4096,
            speed_upload_kbps=4096, data_cap_gb=1,
        )
        self.uncapped = Tariff.objects.create(
            name="Uncapped", price=Decimal("1"), speed_download_kbps=4096, speed_upload_kbps=4096,
        )
        # Deliberately not in alphabetical order of weight: heaviest is last
        # alphabetically, so "sorted by name" and "sorted by usage" can never
        # accidentally agree.
        self._customer("Zelda Zulu", "zelda", self.uncapped, down=900 * MB, up=100 * MB)
        self._customer("Anna Able", "anna", self.capped, down=500 * MB, up=20 * MB)
        self._customer("Mo Mokoena", "mo", self.capped, down=10 * MB, up=1 * MB)

    def _customer(self, name, login, tariff, down, up):
        customer = Customer.objects.create(
            full_name=name, email=f"{login}@example.com",
        )
        Service.objects.create(
            customer=customer, tariff=tariff, status=Service.Status.ACTIVE,
            start_date="2026-08-01", radius_username=login,
        )
        usage.bank_usage(login, at(2026, 8, 26, 9), download=down, upload=up)
        return customer

    def _get(self, view, params, user=None):
        request = APIRequestFactory().get("/usage-report/", params)
        force_authenticate(request, user=user or self.staff)
        return view.as_view()(request)

    def _names(self, params):
        response = self._get(UsageReportView, {"period": "month", "date": "2026-08-26", **params})
        return [row["full_name"] for row in response.data["results"]]

    # --- ordering ---------------------------------------------------------

    def test_the_default_is_still_heaviest_first(self):
        self.assertEqual(self._names({}), ["Zelda Zulu", "Anna Able", "Mo Mokoena"])

    def test_sorting_by_name(self):
        self.assertEqual(
            self._names({"ordering": "full_name"}), ["Anna Able", "Mo Mokoena", "Zelda Zulu"]
        )
        self.assertEqual(
            self._names({"ordering": "-full_name"}), ["Zelda Zulu", "Mo Mokoena", "Anna Able"]
        )

    def test_sorting_by_upload_is_not_the_same_as_by_total(self):
        """Otherwise the column is decoration: it has to be able to produce an
        order the total column would not."""
        self.assertEqual(
            self._names({"ordering": "upload_bytes"}), ["Mo Mokoena", "Anna Able", "Zelda Zulu"]
        )

    def test_names_sort_case_insensitively(self):
        """"van Wyk" filing after "Zulu" is a bug to a human reader, whatever
        ASCII thinks -- and South African surnames are full of them."""
        self._customer("van Wyk Jayden", "jayden", self.uncapped, down=MB, up=0)

        self.assertEqual(self._names({"ordering": "full_name"})[-1], "Zelda Zulu")

    def test_an_unknown_ordering_falls_back_instead_of_erroring(self):
        """It arrives from a URL. A stale bookmark or a typo should show the
        report, not an error page."""
        response = self._get(
            UsageReportView, {"period": "month", "date": "2026-08-26", "ordering": "drop table"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [r["full_name"] for r in response.data["results"]],
            ["Zelda Zulu", "Anna Able", "Mo Mokoena"],
        )

    def test_uncapped_customers_sort_below_capped_ones_in_both_directions(self):
        """"Cap" answers "who is closest to their limit". A customer who has
        no limit is not an answer to it, either way up -- sorting them as 0%
        would bury the capped ones on one click and fill the top with rows the
        column does not apply to."""
        for ordering in ("cap_used_pct", "-cap_used_pct"):
            names = self._names({"ordering": ordering})
            self.assertEqual(names[-1], "Zelda Zulu", ordering)

    # --- the limit is taken on weight, not on the sort --------------------

    def test_the_limit_selects_the_heaviest_then_the_ordering_arranges_them(self):
        """The one that would be a silent disaster: cut after sorting by name
        and the report is about the alphabet, not about the network."""
        names = self._names({"ordering": "full_name", "limit": 2})

        # The heaviest two, alphabetised -- NOT the alphabetically first two.
        self.assertEqual(names, ["Anna Able", "Zelda Zulu"])

    def test_the_response_says_how_many_rows_there_really_were(self):
        response = self._get(
            UsageReportView, {"period": "month", "date": "2026-08-26", "limit": 2}
        )

        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["total_rows"], 3)

    def test_the_ordering_is_echoed_back(self):
        response = self._get(
            UsageReportView,
            {"period": "month", "date": "2026-08-26", "ordering": "upload_bytes"},
        )
        self.assertEqual(response.data["ordering"], "upload_bytes")

    # --- the CSV ----------------------------------------------------------

    def _export(self, params=None, user=None):
        response = self._get(
            UsageReportExportView,
            {"period": "month", "date": "2026-08-26", **(params or {})},
            user=user,
        )
        if response.status_code != 200:
            return response, []
        text = response.content.decode()
        return response, list(csv.reader(io.StringIO(text)))

    def test_the_export_is_a_csv_named_after_the_period_it_covers(self):
        response, rows = self._export()

        self.assertEqual(response["Content-Type"], "text/csv")
        # August, and the name ends on the 31st -- not the 1st of September,
        # which is the exclusive end and contains none of this data.
        self.assertIn("usage_month_2026-08-01_to_2026-08-31.csv", response["Content-Disposition"])
        self.assertEqual(rows[0][0], "Customer reference")

    def test_every_customer_is_in_the_file_even_past_the_table_limit(self):
        """The table caps; the file must not. A CSV that stops early looks
        complete and is not."""
        _, rows = self._export({"limit": 1})

        self.assertEqual(len(rows) - 1, 3)

    def test_it_carries_bytes_to_add_up_and_gigabytes_to_read(self):
        _, rows = self._export()
        header, first = rows[0], rows[1]

        self.assertEqual(first[1], "Zelda Zulu")
        self.assertEqual(first[header.index("Total (bytes)")], str(1000 * MB))
        self.assertEqual(first[header.index("Total (GB)")], "0.98")

    def test_an_uncapped_customer_leaves_the_cap_columns_blank_not_zero(self):
        """A blank reads as "does not apply". A 0 in a cap column reads as a
        customer with no allowance left."""
        _, rows = self._export()
        header = rows[0]
        zelda = next(r for r in rows[1:] if r[1] == "Zelda Zulu")

        self.assertEqual(zelda[header.index("Cap (GB)")], "")
        self.assertEqual(zelda[header.index("Cap used (%)")], "")

    def test_the_export_follows_the_ordering_it_was_asked_for(self):
        _, rows = self._export({"ordering": "full_name"})

        self.assertEqual([r[1] for r in rows[1:]], ["Anna Able", "Mo Mokoena", "Zelda Zulu"])

    def test_the_export_is_partner_scoped_like_the_table(self):
        """The easiest place in the platform for a scoping rule to be
        forgotten: a download that quietly contains every reseller's
        customers."""
        mine = Partner.objects.create(name="Mine")
        theirs = Partner.objects.create(name="Theirs")
        Customer.objects.filter(full_name="Zelda Zulu").update(partner=theirs)
        scoped = User.objects.create_user(username="reseller", password="x", role="support")
        scoped.allowed_partners = [mine.id]
        scoped.save()

        _, rows = self._export(user=scoped)

        self.assertNotIn("Zelda Zulu", [r[1] for r in rows[1:]])

    def test_the_export_is_staff_only(self):
        customer_user = User.objects.create_user(username="cust", password="x", role="customer")

        response, _ = self._export(user=customer_user)

        self.assertEqual(response.status_code, 404)

    def test_a_bad_period_is_refused_by_the_export_too(self):
        response, _ = self._export({"period": "fortnight"})
        self.assertEqual(response.status_code, 400)
