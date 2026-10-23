"""What the lead pipeline has to get right.

The interesting cases are not "can you save a lead" but the ones that
make a pipeline lie: a lost deal with no recorded reason, a duplicate
customer from two people clicking Convert, one reseller seeing another's
prospects, and an overdue follow-up quietly dropping off the list it was
supposed to stay on.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import Tariff
from customers.models import Customer, Partner
from sales.models import Lead, LeadNote


class LeadRuleTests(TestCase):
    def setUp(self):
        self.tariff = Tariff.objects.create(
            name="20 Mbps", price=Decimal("899.00"),
            speed_download_kbps=20480, speed_upload_kbps=20480,
        )

    def _lead(self, **kwargs):
        return Lead.objects.create(full_name="Enquiring Person", **kwargs)

    def test_losing_a_lead_requires_a_reason(self):
        """The reason is the whole point of recording the loss -- it is
        the difference between "our pricing is wrong" and "we need a
        tower there"."""
        lead = self._lead()
        lead.status = Lead.Status.LOST
        with self.assertRaises(ValidationError):
            lead.full_clean()

    def test_a_reason_without_a_loss_is_rejected_too(self):
        lead = self._lead()
        lead.lost_reason = Lead.LostReason.PRICE
        with self.assertRaises(ValidationError):
            lead.full_clean()

    def test_closing_stamps_the_time_and_reopening_clears_it(self):
        """Otherwise "how long do deals take" measures the first time
        somebody closed it by mistake."""
        lead = self._lead()
        self.assertIsNone(lead.closed_at)
        lead.status = Lead.Status.LOST
        lead.lost_reason = Lead.LostReason.WENT_QUIET
        lead.save()
        self.assertIsNotNone(lead.closed_at)

        lead.status = Lead.Status.QUALIFIED
        lead.lost_reason = ""
        lead.save()
        self.assertIsNone(lead.closed_at)

    def test_value_prefers_the_override_then_the_tariff_then_zero(self):
        self.assertEqual(self._lead().value, Decimal("0.00"))
        self.assertEqual(self._lead(interested_tariff=self.tariff).value, Decimal("899.00"))
        self.assertEqual(
            self._lead(interested_tariff=self.tariff, estimated_monthly_value=Decimal("1200.00")).value,
            Decimal("1200.00"),
        )

    def test_a_zero_override_is_respected_not_treated_as_unset(self):
        """0 is a real answer -- a free trial, a goodwill install. Only
        None means nobody has said."""
        lead = self._lead(interested_tariff=self.tariff, estimated_monthly_value=Decimal("0.00"))
        self.assertEqual(lead.value, Decimal("0.00"))


class FollowUpTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

    def _lead(self, **kwargs):
        return Lead.objects.create(full_name="Chase Me", **kwargs)

    def test_overdue_counts_as_due(self):
        """A rep needs one list. Splitting "today" from "late" makes the
        late one the list that stops being opened."""
        lead = self._lead(next_follow_up=self.today - datetime.timedelta(days=9))
        self.assertTrue(lead.follow_up_is_due)

    def test_today_counts_as_due(self):
        self.assertTrue(self._lead(next_follow_up=self.today).follow_up_is_due)

    def test_tomorrow_does_not(self):
        self.assertFalse(
            self._lead(next_follow_up=self.today + datetime.timedelta(days=1)).follow_up_is_due
        )

    def test_a_closed_lead_is_never_due(self):
        """Chasing somebody we already won, or who already told us no, is
        the fastest way to make staff stop trusting the list."""
        lead = self._lead(
            next_follow_up=self.today - datetime.timedelta(days=3),
            status=Lead.Status.LOST,
            lost_reason=Lead.LostReason.PRICE,
        )
        self.assertFalse(lead.follow_up_is_due)

    def test_no_date_is_not_due(self):
        self.assertFalse(self._lead().follow_up_is_due)


class ConversionTests(TestCase):
    def setUp(self):
        self.rep = User.objects.create_user(username="rep", password="x", role=User.Role.SALES)
        self.partner = Partner.objects.create(name="Reseller A")

    def test_converting_creates_a_customer_carrying_the_details_over(self):
        lead = Lead.objects.create(
            full_name="Nomsa Dlamini", email="n@example.com", phone="0821234567",
            address="12 Main Rd", city="Polokwane", partner=self.partner,
            assigned_to=self.rep,
        )
        customer = lead.convert_to_customer(actor=self.rep)
        self.assertEqual(customer.full_name, "Nomsa Dlamini")
        self.assertEqual(customer.email, "n@example.com")
        self.assertEqual(customer.city, "Polokwane")
        self.assertEqual(customer.partner, self.partner)
        self.assertEqual(customer.assigned_staff, self.rep)

    def test_the_new_customer_is_NEW_not_active(self):
        """Nothing is installed yet. Marking them Active here would put
        somebody in the connected-customer count with no service and no
        router."""
        lead = Lead.objects.create(full_name="Not Yet Connected")
        customer = lead.convert_to_customer()
        self.assertEqual(customer.status, Customer.Status.NEW)

    def test_converting_twice_does_not_make_two_customers(self):
        """Two people clicking Convert seconds apart is an ordinary
        Tuesday. A duplicate customer is expensive to unpick."""
        lead = Lead.objects.create(full_name="Clicked Twice")
        first = lead.convert_to_customer()
        second = lead.convert_to_customer()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Customer.objects.filter(full_name="Clicked Twice").count(), 1)

    def test_converting_wins_the_lead_and_stops_chasing_them(self):
        lead = Lead.objects.create(
            full_name="Signed Up", next_follow_up=timezone.localdate()
        )
        lead.convert_to_customer()
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.WON)
        self.assertIsNone(lead.next_follow_up)
        self.assertIsNotNone(lead.closed_at)

    def test_a_company_lead_becomes_a_business_customer(self):
        lead = Lead.objects.create(full_name="Buyer", company_name="Grunder Co")
        customer = lead.convert_to_customer()
        self.assertEqual(customer.customer_type, Customer.CustomerType.COMPANY)
        self.assertEqual(customer.category, Customer.Category.BUSINESS)

    def test_conversion_is_written_onto_the_timeline(self):
        lead = Lead.objects.create(full_name="Traceable")
        lead.convert_to_customer(actor=self.rep)
        note = lead.lead_notes.filter(kind=LeadNote.Kind.SYSTEM).first()
        self.assertIn("Converted to customer", note.body)


class ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username="boss", password="x", role=User.Role.ADMIN)
        self.rep = User.objects.create_user(
            username="rep", password="x", role=User.Role.SALES, allowed_sections=["sales"]
        )
        self.desk = User.objects.create_user(
            username="desk", password="x", role=User.Role.SUPPORT, allowed_sections=["customers"]
        )

    def test_sales_access_is_its_own_gate(self):
        """Support staff have no reason to see the pipeline, and a rep
        has no reason to see every customer's billing."""
        self.client.force_authenticate(self.desk)
        self.assertEqual(self.client.get("/api/leads/").status_code, 403)
        self.client.force_authenticate(self.rep)
        self.assertEqual(self.client.get("/api/leads/").status_code, 200)

    def test_a_new_lead_is_assigned_to_whoever_entered_it(self):
        """An unassigned lead is one nobody is chasing."""
        self.client.force_authenticate(self.rep)
        res = self.client.post("/api/leads/", {"full_name": "Walk In"}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["assigned_to"], self.rep.pk)

    def test_the_api_enforces_the_lost_reason_rule(self):
        """DRF does not call Model.clean(), so without the serializer
        wiring this rule would hold in the shell and quietly not hold on
        the screen everyone uses."""
        self.client.force_authenticate(self.rep)
        lead = Lead.objects.create(full_name="Going Nowhere")
        res = self.client.patch(f"/api/leads/{lead.pk}/", {"status": "lost"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("lost_reason", res.data)

    def test_a_patch_judges_the_reason_already_stored(self):
        """Setting the reason first and the status second must work --
        otherwise the rule is unsatisfiable in two steps."""
        self.client.force_authenticate(self.rep)
        lead = Lead.objects.create(full_name="Two Steps")
        self.client.patch(
            f"/api/leads/{lead.pk}/", {"status": "lost", "lost_reason": "price"}, format="json"
        )
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.LOST)

    def test_a_stage_change_is_written_onto_the_timeline(self):
        self.client.force_authenticate(self.rep)
        lead = Lead.objects.create(full_name="Moving Along")
        self.client.patch(f"/api/leads/{lead.pk}/", {"status": "qualified"}, format="json")
        self.assertTrue(lead.lead_notes.filter(body__icontains="Qualified").exists())

    def test_the_due_filter_includes_overdue_and_excludes_closed(self):
        self.client.force_authenticate(self.rep)
        today = timezone.localdate()
        Lead.objects.create(full_name="Late", next_follow_up=today - datetime.timedelta(days=5))
        Lead.objects.create(full_name="Today", next_follow_up=today)
        Lead.objects.create(full_name="Later", next_follow_up=today + datetime.timedelta(days=4))
        Lead.objects.create(
            full_name="Already Lost", next_follow_up=today,
            status=Lead.Status.LOST, lost_reason=Lead.LostReason.PRICE,
        )
        res = self.client.get("/api/leads/?due=true")
        names = {row["full_name"] for row in res.data["results"]}
        self.assertEqual(names, {"Late", "Today"})

    def test_partner_scoping_hides_another_resellers_pipeline(self):
        mine = Partner.objects.create(name="Mine")
        theirs = Partner.objects.create(name="Theirs")
        Lead.objects.create(full_name="Mine", partner=mine)
        Lead.objects.create(full_name="Theirs", partner=theirs)
        Lead.objects.create(full_name="Direct")
        self.rep.allowed_partners = [mine.pk]
        self.rep.save()
        self.client.force_authenticate(self.rep)
        res = self.client.get("/api/leads/")
        names = {row["full_name"] for row in res.data["results"]}
        # Direct enquiries belong to nobody in particular, exactly as
        # no-partner customers do.
        self.assertEqual(names, {"Mine", "Direct"})

    def test_the_pipeline_summary_values_leads_without_an_override(self):
        tariff = Tariff.objects.create(
            name="10 Mbps", price=Decimal("599.00"),
            speed_download_kbps=10240, speed_upload_kbps=10240,
        )
        Lead.objects.create(full_name="A", interested_tariff=tariff, status=Lead.Status.QUOTED)
        Lead.objects.create(
            full_name="B", estimated_monthly_value=Decimal("1500.00"), status=Lead.Status.QUOTED
        )
        self.client.force_authenticate(self.rep)
        res = self.client.get("/api/pipeline-summary/")
        quoted = next(s for s in res.data["stages"] if s["status"] == "quoted")
        self.assertEqual(quoted["count"], 2)
        self.assertEqual(Decimal(str(quoted["value"])), Decimal("2099.00"))

    def test_the_summary_counts_leads_nobody_scheduled_anything_for(self):
        """Not overdue -- invisible, which is worse, because an overdue
        list at least admits they exist."""
        Lead.objects.create(full_name="Forgotten")
        Lead.objects.create(full_name="Scheduled", next_follow_up=timezone.localdate())
        self.client.force_authenticate(self.rep)
        res = self.client.get("/api/pipeline-summary/")
        self.assertEqual(res.data["unscheduled_count"], 1)

    def test_convert_reports_when_it_was_already_converted(self):
        self.client.force_authenticate(self.rep)
        lead = Lead.objects.create(full_name="Double Click")
        first = self.client.post(f"/api/leads/{lead.pk}/convert/")
        second = self.client.post(f"/api/leads/{lead.pk}/convert/")
        self.assertFalse(first.data["already_converted"])
        self.assertTrue(second.data["already_converted"])
        self.assertEqual(first.data["customer_id"], second.data["customer_id"])

    def test_logging_a_call_can_set_the_next_follow_up_in_one_step(self):
        """A rep who has just agreed a call-back date will record it in
        the same keystroke or not at all."""
        self.client.force_authenticate(self.rep)
        lead = Lead.objects.create(full_name="Called Them")
        when = (timezone.localdate() + datetime.timedelta(days=3)).isoformat()
        res = self.client.post(
            f"/api/leads/{lead.pk}/notes/",
            {"kind": "call", "body": "Wants to think about it.", "next_follow_up": when},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        lead.refresh_from_db()
        self.assertEqual(lead.next_follow_up.isoformat(), when)


class AuditIntegrationTests(TestCase):
    def test_moving_a_lead_to_lost_lands_in_the_activity_log(self):
        from audit.context import acting_as
        from audit.models import AuditEvent

        user = User.objects.create_user(username="rep", password="x", role=User.Role.SALES)
        lead = Lead.objects.create(full_name="Recorded")
        AuditEvent.objects.all().delete()
        with acting_as(user):
            lead.status = Lead.Status.LOST
            lead.lost_reason = Lead.LostReason.COMPETITOR
            lead.save()
        event = AuditEvent.objects.filter(target_type="sales.Lead", action="updated").first()
        self.assertIsNotNone(event)
        self.assertIn("Lost", str(event.changes))
