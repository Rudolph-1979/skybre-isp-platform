"""Finance -> Dashboard.

The figures here are the ones somebody will act on -- chase a debtor,
question a month, forecast a bank balance -- so the tests pin the
arithmetic rather than just checking the endpoint answers 200.

The two that matter most:

  * the ageing buckets SUM to the overdue total. An ageing table whose
    parts don't add up is worse than no ageing table, and off-by-one
    boundaries are the classic way that happens.
  * the dashboard and the Invoices page agree about what "overdue"
    means. They are two readings of one rule (billing/overdue.py) and
    the point of that module is that they can't drift.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import CreditRequest, Invoice, Payment, Service, Tariff
from customers.models import Customer, Partner
from accounts.testing import ALL_SECTIONS


def tariff(name="20Mbps", price="379.00"):
    return Tariff.objects.create(
        name=name, price=Decimal(price), speed_download_kbps=20480, speed_upload_kbps=20480
    )


class FinanceDashboardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="boss", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(self.admin)
        self.today = timezone.localdate()
        self.tariff = tariff()
        self.customer = Customer.objects.create(full_name="Real Customer", email="real@x.com")

    def _invoice(self, status, total, days_due_offset=0, paid=0, created=None):
        invoice = Invoice.objects.create(
            customer=self.customer,
            status=status,
            date_due=self.today + timedelta(days=days_due_offset),
            total=Decimal(total),
            paid_amount=Decimal(paid),
        )
        if created is not None:
            # date_created is auto_now_add, so it has to be written after
            # the fact to place an invoice in a past month.
            Invoice.objects.filter(pk=invoice.pk).update(date_created=created)
            invoice.refresh_from_db()
        return invoice

    def _get(self, query=""):
        res = self.client.get(f"/api/finance-dashboard/{query}")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    # --- ageing ----------------------------------------------------------

    def test_ageing_buckets_sum_to_the_total(self):
        self._invoice(Invoice.Status.UNPAID, "100.00", days_due_offset=-5)
        self._invoice(Invoice.Status.UNPAID, "200.00", days_due_offset=-45)
        self._invoice(Invoice.Status.OVERDUE, "300.00", days_due_offset=-75)
        self._invoice(Invoice.Status.UNPAID, "400.00", days_due_offset=-200)
        # Not overdue, and must not appear anywhere in the table.
        self._invoice(Invoice.Status.UNPAID, "999.00", days_due_offset=+5)
        self._invoice(Invoice.Status.PAID, "888.00", days_due_offset=-30)

        overdue = self._get()["overdue"]
        by_label = {b["label"]: b for b in overdue["buckets"]}

        self.assertEqual(Decimal(by_label["0-30 days overdue"]["amount"]), Decimal("100.00"))
        self.assertEqual(Decimal(by_label["30-60 days overdue"]["amount"]), Decimal("200.00"))
        self.assertEqual(Decimal(by_label["60-90 days overdue"]["amount"]), Decimal("300.00"))
        self.assertEqual(Decimal(by_label["90+ days overdue"]["amount"]), Decimal("400.00"))

        self.assertEqual(Decimal(overdue["total"]["amount"]), Decimal("1000.00"))
        self.assertEqual(overdue["total"]["count"], 4)
        self.assertEqual(
            sum(Decimal(b["amount"]) for b in overdue["buckets"]),
            Decimal(overdue["total"]["amount"]),
        )

    def test_a_boundary_invoice_lands_in_exactly_one_bucket(self):
        """Exactly 30 days overdue belongs to 0-30, not to 30-60. Without
        a convention it falls in both or neither, and the table stops
        adding up."""
        self._invoice(Invoice.Status.UNPAID, "100.00", days_due_offset=-30)

        buckets = {b["label"]: b["count"] for b in self._get()["overdue"]["buckets"]}

        self.assertEqual(buckets["0-30 days overdue"], 1)
        self.assertEqual(buckets["30-60 days overdue"], 0)

    def test_dashboard_and_invoices_page_agree_on_overdue(self):
        """Both read billing/overdue.py. If someone reimplements either
        one by hand, this fails."""
        for offset in (-5, -20, -45, -100):
            self._invoice(Invoice.Status.UNPAID, "100.00", days_due_offset=offset)

        from_dashboard = self._get()["overdue"]["buckets"][0]["count"]
        res = self.client.get("/api/invoices/?overdue_within_days=30")
        from_invoices_page = res.json()["count"]

        self.assertEqual(from_dashboard, from_invoices_page)

    # --- month figures ---------------------------------------------------

    def test_total_income_is_invoiced_revenue_net_of_credits(self):
        """paid + unpaid - approved credits. Accrual, not cash -- the
        Payments figure beside it is the cash view."""
        self._invoice(Invoice.Status.PAID, "1000.00")
        self._invoice(Invoice.Status.UNPAID, "500.00", days_due_offset=+5)
        credit = CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("200.00"), reason="goodwill",
            status=CreditRequest.Status.APPROVED, decided_at=timezone.now(),
        )
        self.assertIsNotNone(credit.decided_at)

        current = self._get()["current_month"]

        self.assertEqual(Decimal(current["paid_invoices"]["amount"]), Decimal("1000.00"))
        self.assertEqual(Decimal(current["unpaid_invoices"]["amount"]), Decimal("500.00"))
        self.assertEqual(Decimal(current["credit_notes"]["amount"]), Decimal("200.00"))
        self.assertEqual(Decimal(current["total_income"]), Decimal("1300.00"))

    def test_pending_and_rejected_credits_are_not_money_off_a_bill(self):
        CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("50.00"), reason="pending",
            status=CreditRequest.Status.PENDING,
        )
        CreditRequest.objects.create(
            customer=self.customer, amount=Decimal("70.00"), reason="no",
            status=CreditRequest.Status.REJECTED, decided_at=timezone.now(),
        )

        self.assertEqual(Decimal(self._get()["current_month"]["credit_notes"]["amount"]), Decimal("0.00"))

    def test_last_month_does_not_include_this_month(self):
        first_this = self.today.replace(day=1)
        in_last_month = first_this - timedelta(days=1)
        self._invoice(Invoice.Status.PAID, "600.00", created=in_last_month)
        self._invoice(Invoice.Status.PAID, "900.00")

        data = self._get()
        self.assertEqual(Decimal(data["last_month"]["paid_invoices"]["amount"]), Decimal("600.00"))
        self.assertEqual(Decimal(data["current_month"]["paid_invoices"]["amount"]), Decimal("900.00"))

    def test_a_proforma_counts_as_paid_only_once_it_is_covered(self):
        self._invoice(Invoice.Status.PROFORMA, "100.00", paid="100.00")
        self._invoice(Invoice.Status.PROFORMA, "100.00", paid="40.00")

        tiles = self._get()["tiles"]
        self.assertEqual(tiles["paid_proformas"]["count"], 1)
        self.assertEqual(tiles["unpaid_proformas"]["count"], 1)

    # --- outstanding vs billed -------------------------------------------

    def test_unpaid_tile_shows_what_is_still_owed_not_the_full_invoice(self):
        """A R1000 invoice with R900 banked leaves R100 collectable. Summing
        the full total would report R1000 outstanding and overstate the
        debtors book by everything already part-paid."""
        self._invoice(Invoice.Status.UNPAID, "1000.00", days_due_offset=+5, paid="900.00")

        unpaid = self._get()["tiles"]["unpaid_invoices"]

        self.assertEqual(Decimal(unpaid["amount"]), Decimal("100.00"))
        self.assertEqual(Decimal(unpaid["invoiced"]), Decimal("1000.00"))

    def test_total_income_still_counts_what_was_billed(self):
        """Part-paying an invoice must not appear to reduce the revenue
        that was earned -- income is accrual, the Payments row is the cash
        view."""
        self._invoice(Invoice.Status.UNPAID, "1000.00", days_due_offset=+5, paid="900.00")

        current = self._get()["current_month"]

        self.assertEqual(Decimal(current["unpaid_invoices"]["amount"]), Decimal("100.00"))
        self.assertEqual(Decimal(current["total_income"]), Decimal("1000.00"))

    def test_an_overpaid_invoice_cannot_subtract_from_what_others_owe(self):
        """paid_amount CAN exceed total -- a duplicate EFT, a misallocated
        payment. Without a floor at zero that negative leaks into the
        book-wide figure and quietly reduces everyone else's debt."""
        self._invoice(Invoice.Status.UNPAID, "100.00", days_due_offset=+5, paid="500.00")
        self._invoice(Invoice.Status.UNPAID, "300.00", days_due_offset=+5)

        self.assertEqual(Decimal(self._get()["tiles"]["unpaid_invoices"]["amount"]), Decimal("300.00"))

    # --- periods ---------------------------------------------------------

    def test_month_year_and_lifetime_cover_widening_windows(self):
        this_month = self._invoice(Invoice.Status.PAID, "100.00")
        self.assertIsNotNone(this_month)
        # Earlier in the same year, and a prior year.
        earlier_this_year = self.today.replace(month=1, day=15)
        if earlier_this_year >= self.today.replace(day=1):
            earlier_this_year = self.today.replace(day=1) - timedelta(days=1)
        self._invoice(Invoice.Status.PAID, "200.00", created=earlier_this_year)
        self._invoice(Invoice.Status.PAID, "400.00", created=self.today.replace(year=self.today.year - 2))

        month = Decimal(self._get("?period=month")["tiles"]["paid_invoices"]["amount"])
        year = Decimal(self._get("?period=year")["tiles"]["paid_invoices"]["amount"])
        lifetime = Decimal(self._get("?period=lifetime")["tiles"]["paid_invoices"]["amount"])

        self.assertEqual(month, Decimal("100.00"))
        self.assertEqual(year, Decimal("300.00"))
        self.assertEqual(lifetime, Decimal("700.00"))

    def test_period_defaults_to_month_and_is_echoed_back(self):
        self.assertEqual(self._get()["period"], "month")
        self.assertEqual(self._get("?period=lifetime")["period"], "lifetime")

    def test_an_unknown_period_falls_back_to_month_rather_than_erroring(self):
        """It only widens or narrows a date window, so a typo showing the
        month is harmless -- and the response says which period was used."""
        self.assertEqual(self._get("?period=fortnight")["period"], "month")

    def test_the_month_panels_stay_months_whatever_the_period(self):
        """Last month and This month are named after months. The period
        toggle governs the tiles only."""
        self._invoice(Invoice.Status.PAID, "100.00")
        self._invoice(Invoice.Status.PAID, "400.00", created=self.today.replace(year=self.today.year - 2))

        data = self._get("?period=lifetime")

        self.assertEqual(Decimal(data["tiles"]["paid_invoices"]["amount"]), Decimal("500.00"))
        self.assertEqual(Decimal(data["current_month"]["paid_invoices"]["amount"]), Decimal("100.00"))

    # --- recurring -------------------------------------------------------

    def test_mrr_and_arpu_come_from_active_services(self):
        other = Customer.objects.create(full_name="Second", email="second@x.com")
        Service.objects.create(customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE)
        Service.objects.create(customer=other, tariff=self.tariff, status=Service.Status.ACTIVE)
        # Terminated: pays nothing, and must not drag ARPU down either.
        Service.objects.create(
            customer=Customer.objects.create(full_name="Gone", email="gone@x.com"),
            tariff=self.tariff, status=Service.Status.TERMINATED,
        )

        recurring = self._get()["recurring"]

        self.assertEqual(Decimal(recurring["mrr"]), Decimal("758.00"))
        self.assertEqual(recurring["paying_customers"], 2)
        self.assertEqual(Decimal(recurring["arpu"]), Decimal("379.00"))

    def test_arpu_is_zero_rather_than_a_crash_with_no_customers(self):
        self.assertEqual(Decimal(self._get()["recurring"]["arpu"]), Decimal("0.00"))

    def test_planned_income_matches_what_recurring_billing_would_charge(self):
        Service.objects.create(customer=self.customer, tariff=self.tariff, status=Service.Status.ACTIVE)
        Service.objects.create(customer=self.customer, tariff=tariff("50Mbps", "599.00"),
                               status=Service.Status.ACTIVE)

        next_month = self._get()["next_month"]

        self.assertEqual(next_month["services"], 2)
        self.assertEqual(next_month["customers"], 1)
        self.assertEqual(Decimal(next_month["planned_income"]), Decimal("978.00"))


class FinanceDashboardScopingTests(TestCase):
    """A reseller-scoped staff member must not learn another partner's
    totals just because they arrive as an aggregate rather than a list."""

    def setUp(self):
        self.client = APIClient()
        self.mine = Partner.objects.create(name="Mine")
        self.theirs = Partner.objects.create(name="Theirs")

        self.my_customer = Customer.objects.create(
            full_name="Mine A", email="a@x.com", partner=self.mine
        )
        self.their_customer = Customer.objects.create(
            full_name="Theirs B", email="b@x.com", partner=self.theirs
        )
        today = timezone.localdate()
        Invoice.objects.create(customer=self.my_customer, status=Invoice.Status.PAID,
                               date_due=today, total=Decimal("100.00"))
        Invoice.objects.create(customer=self.their_customer, status=Invoice.Status.PAID,
                               date_due=today, total=Decimal("900.00"))

    def test_scoped_staff_see_only_their_partners_totals(self):
        user = User.objects.create_user(username="reseller", password="x", role=User.Role.SUPPORT, allowed_sections=ALL_SECTIONS)
        user.allowed_partners = [self.mine.pk]
        user.save()
        self.client.force_authenticate(user)

        res = self.client.get("/api/finance-dashboard/")

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            Decimal(res.json()["current_month"]["paid_invoices"]["amount"]), Decimal("100.00")
        )

    def test_asking_for_a_partner_you_cannot_see_returns_nothing_not_their_numbers(self):
        user = User.objects.create_user(username="reseller2", password="x", role=User.Role.SUPPORT, allowed_sections=ALL_SECTIONS)
        user.allowed_partners = [self.mine.pk]
        user.save()
        self.client.force_authenticate(user)

        res = self.client.get(f"/api/finance-dashboard/?partner={self.theirs.pk}")

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            Decimal(res.json()["current_month"]["paid_invoices"]["amount"]), Decimal("0.00")
        )

    def test_several_partners_can_be_selected_at_once(self):
        """The filter means "these ones", not "this one"."""
        third = Partner.objects.create(name="Third")
        other = Customer.objects.create(full_name="Third C", email="c@x.com", partner=third)
        Invoice.objects.create(customer=other, status=Invoice.Status.PAID,
                               date_due=timezone.localdate(), total=Decimal("40.00"))
        admin = User.objects.create_user(username="boss3", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(admin)

        both = self.client.get(f"/api/finance-dashboard/?partner={self.mine.pk},{third.pk}")
        self.assertEqual(
            Decimal(both.json()["current_month"]["paid_invoices"]["amount"]), Decimal("140.00")
        )

        # Repeated params mean the same thing as a comma-separated list.
        repeated = self.client.get(
            f"/api/finance-dashboard/?partner={self.mine.pk}&partner={third.pk}"
        )
        self.assertEqual(repeated.json()["current_month"], both.json()["current_month"])
        self.assertEqual(sorted(repeated.json()["partners"]), sorted([self.mine.pk, third.pk]))

    def test_no_partner_given_means_all_of_them(self):
        admin = User.objects.create_user(username="boss4", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(admin)

        res = self.client.get("/api/finance-dashboard/")

        self.assertEqual(res.json()["partners"], [])
        self.assertEqual(
            Decimal(res.json()["current_month"]["paid_invoices"]["amount"]), Decimal("1000.00")
        )

    def test_a_nonsense_partner_is_refused_rather_than_ignored(self):
        admin = User.objects.create_user(username="boss5", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(admin)

        res = self.client.get("/api/finance-dashboard/?partner=abc")

        self.assertEqual(res.status_code, 400, res.content)

    def test_an_admin_sees_everything(self):
        admin = User.objects.create_user(username="boss2", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(admin)

        res = self.client.get("/api/finance-dashboard/")

        self.assertEqual(
            Decimal(res.json()["current_month"]["paid_invoices"]["amount"]), Decimal("1000.00")
        )

    def test_a_customer_cannot_reach_the_dashboard_at_all(self):
        customer_user = User.objects.create_user(
            username="client", password="x", role=User.Role.CUSTOMER
        )
        self.client.force_authenticate(customer_user)

        self.assertEqual(self.client.get("/api/finance-dashboard/").status_code, 403)
