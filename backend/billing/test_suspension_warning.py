"""Warning a customer before the line goes off.

The gap this closes: the payment reminder is keyed to an invoice's DUE
date and says only "to avoid any interruption to your service". The
suspension notice arrives once the line is already dead. A customer on a
14-day blocking period was reminded, heard nothing for two weeks, and
then found out by not being able to browse.

Three things are pinned here, all of them about an email reaching the
wrong person or the wrong day:

  * it is a DAILY COUNTDOWN through the notice period -- 3 days, 2 days,
    1 day -- and the number falls as the date approaches;
  * it never goes to somebody already being suspended by this same run,
    who would otherwise receive a warning and a suspension notice in the
    same minute;
  * it is sent once per customer per DAY, so a Run and the scheduled
    command on the same date do not both send it;
  * it never goes out on the due date itself, when nothing is late yet.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    CustomerBillingConfig, Invoice, Service, SuspensionSettings, Tariff,
)
from billing.preview_export import build_csvs, project_run
from billing.recurring import run_recurring_billing
from customers.models import Customer
from notifications.models import EmailTemplate


class SuspensionWarningTests(TestCase):
    def setUp(self):
        # PINNED, not timezone.localdate(), and that is the whole fix.
        #
        # Every date in this file is an offset from here, and cut-offs are
        # now deferred off weekends and public holidays (see
        # billing.business_days). With a moving anchor the same test passes
        # on a Tuesday and fails on a Thursday, because a block date that
        # landed on a working day last week lands on a Saturday this week
        # and shifts to the Monday. A suite whose colour depends on the day
        # you run it is not telling you anything about the code.
        #
        # A MONDAY specifically, so that today+1, today+3, today+4 and
        # today+7 are all working days and every scenario below means what
        # its name says rather than what the calendar allows this week.
        self.today = datetime.date(2026, 6, 1)
        for key in ("suspension_warning", "suspension", "payment_reminder", "invoice"):
            EmailTemplate.objects.get_or_create(
                key=key, defaults={"name": key, "subject": key, "body_html": "<p>x</p>"},
            )
        self.settings = SuspensionSettings.load()
        self.settings.auto_suspend_enabled = True
        self.settings.warning_enabled = True
        self.settings.warning_days = 3
        self.settings.save()

        self.tariff = Tariff.objects.create(
            name="Home 20", price=Decimal("500.00"), tax_rate_pct=Decimal("0"),
            speed_download_kbps=20480, speed_upload_kbps=10240,
        )

    def _customer(self, name, *, overdue_days, blocking_period=7, balance="500.00"):
        """A customer with one unpaid invoice `overdue_days` past due."""
        customer = Customer.objects.create(
            full_name=name, email=f"{name.split()[0].lower()}@example.com",
            status=Customer.Status.ACTIVE, balance=Decimal(balance),
        )
        Service.objects.create(
            customer=customer, tariff=self.tariff, status=Service.Status.ACTIVE,
            start_date=self.today - datetime.timedelta(days=90),
        )
        config = CustomerBillingConfig.for_customer(customer)
        config.billing_enabled = True
        config.auto_create_invoices = False   # keep this about warnings only
        config.send_billing_notifications = True
        config.blocking_period_days = blocking_period
        config.minimum_balance = Decimal("0")
        config.next_billing_date = self.today + datetime.timedelta(days=30)
        config.save()
        Invoice.objects.create(
            customer=customer, status=Invoice.Status.UNPAID, total=Decimal(balance),
            date_due=self.today - datetime.timedelta(days=overdue_days),
        )
        return customer

    def _run(self, on_date=None, commit=True):
        # captureOnCommitCallbacks because every email in this engine is
        # queued with transaction.on_commit -- SMTP has no rollback, so a
        # send that happened inside a transaction that then failed would
        # be a notice about something that does not exist. Without this
        # the callbacks are simply discarded and nothing would be
        # asserted.
        with mock.patch("billing.recurring.send_customer_email") as send:
            send.return_value = mock.Mock(status="sent")
            with self.captureOnCommitCallbacks(execute=True):
                result = run_recurring_billing(on_date or self.today, commit=commit)
        return result, send

    def _sent_keys(self, send):
        return [call.args[0] for call in send.call_args_list]

    # --- when it fires ----------------------------------------------------

    def test_a_customer_three_days_from_cut_off_is_warned(self):
        """Blocking period 7, invoice 4 days overdue: they are blocked on
        day 7, which is 3 days from today."""
        self._customer("Warned Wanda", overdue_days=4)

        result, send = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 1)
        self.assertIn(EmailTemplate.Key.SUSPENSION_WARNING, self._sent_keys(send))

    def test_a_customer_one_day_from_cut_off_is_warned(self):
        """The case the first version missed. Warning only on the exact day
        the cut-off was `warning_days` away meant anyone already closer than
        that -- because this was switched on late, or a day was missed --
        was never warned at all."""
        self._customer("Tomorrow Thabo", overdue_days=6)

        result, send = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 1)
        self.assertIn(EmailTemplate.Key.SUSPENSION_WARNING, self._sent_keys(send))

    def test_the_email_names_that_customers_own_cut_off_date(self):
        """Two people warned on the same day can be cut off on different
        ones, so the date cannot be "today plus the notice period"."""
        self._customer("Tomorrow Thabo", overdue_days=6)

        _, send = self._run()

        context = next(
            c for c in send.call_args_list
            if c.args[0] == EmailTemplate.Key.SUSPENSION_WARNING
        ).kwargs["extra_context"]
        self.assertEqual(context["suspend_date"], self.today + datetime.timedelta(days=1))
        self.assertEqual(context["days_until_suspension"], 1)

    def test_the_countdown_falls_day_by_day(self):
        """The whole shape of it: 3 days, 2 days, 1 day, then the line goes
        off. A single email naming a date is too easy to delete.

        Run against PAST dates, walking forward to today. The engine refuses
        a committed run on a future date -- rightly, since it would apply
        every cancellation and tariff change up to then -- so the way to
        watch several days pass is to start earlier, not to run ahead.
        """
        # Due 7 days ago, blocking period 7: cut-off is today, so the three
        # days before it are today-3, today-2, today-1.
        self._customer("Warned Wanda", overdue_days=7)

        counts = []
        for days_back in (3, 2, 1):
            _, send = self._run(on_date=self.today - datetime.timedelta(days=days_back))
            call = next(
                (c for c in send.call_args_list
                 if c.args[0] == EmailTemplate.Key.SUSPENSION_WARNING),
                None,
            )
            counts.append(call.kwargs["extra_context"]["days_until_suspension"] if call else None)

        self.assertEqual(counts, [3, 2, 1])

    def test_it_does_not_warn_on_the_due_date_itself(self):
        """Nothing is late on the day it falls due, and "your line will be
        suspended" is not a thing to say to somebody who is not overdue."""
        self._customer("Due Today Dries", overdue_days=0, blocking_period=3)

        result, send = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)
        self.assertNotIn(EmailTemplate.Key.SUSPENSION_WARNING, self._sent_keys(send))

    def test_a_customer_further_out_is_not_warned_yet(self):
        """Four days from the cut-off with a 3-day notice period: outside
        the window. A warning that goes out early is one the customer has
        forgotten by the time it matters.

        Four rather than five: at five, this customer's cut-off falls on a
        Saturday and is deferred to the Monday, which tests the weekend
        rule instead of the notice period. One assertion, one subject."""
        self._customer("Early Elsie", overdue_days=3)

        result, send = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)
        self.assertNotIn(EmailTemplate.Key.SUSPENSION_WARNING, self._sent_keys(send))

    def test_the_notice_period_is_configurable(self):
        """Four days out: outside a 3-day notice period, inside a 5-day one.

        The pair with the test above -- same customer, same cut-off, and
        the only thing that changes is the setting."""
        self.settings.warning_days = 5
        self.settings.save()
        self._customer("Early Elsie", overdue_days=3)

        result, _ = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 1)

    def test_a_customer_being_suspended_today_is_not_warned(self):
        """They would otherwise get a warning and a suspension notice in the
        same minute -- the warning telling them about something that had
        already happened."""
        self._customer("Cutoff Charl", overdue_days=8)

        result, send = self._run()

        self.assertEqual(result["counts"]["suspensions_applied"], 1)
        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)
        self.assertNotIn(EmailTemplate.Key.SUSPENSION_WARNING, self._sent_keys(send))

    def test_a_customer_in_credit_is_never_warned(self):
        """Blocking needs the balance to be worse than the minimum, so a
        customer who owes nothing is not heading for a cut-off however old
        an invoice looks."""
        self._customer("Paid-up Pieter", overdue_days=4, balance="0.00")

        result, _ = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)

    def test_a_customer_with_no_blocking_period_is_never_warned(self):
        """They can never be suspended, so there is nothing to warn about."""
        self._customer("Unblockable Una", overdue_days=4, blocking_period=None)

        result, _ = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)

    # --- the switches -----------------------------------------------------

    def test_warnings_can_be_turned_off(self):
        self.settings.warning_enabled = False
        self.settings.save()
        self._customer("Warned Wanda", overdue_days=4)

        result, _ = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)

    def test_warnings_still_run_while_auto_suspension_is_off(self):
        """Deliberate: warning people is not the destructive half of this,
        and running warnings for a cycle before arming the cut-off is a
        reasonable way to start."""
        self.settings.auto_suspend_enabled = False
        self.settings.save()
        self._customer("Warned Wanda", overdue_days=4)

        result, _ = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 1)
        self.assertEqual(result["counts"]["suspensions_applied"], 0)

    def test_a_customer_with_notifications_off_is_not_emailed(self):
        """Somebody billed by hand, or an account whose address belongs to a
        third party. The invoice and suspension emails already respect this
        switch; the warning did not, and sent to them anyway."""
        customer = self._customer("Quiet Quinn", overdue_days=4)
        config = CustomerBillingConfig.for_customer(customer)
        config.send_billing_notifications = False
        config.save()

        result, send = self._run()

        self.assertNotIn(EmailTemplate.Key.SUSPENSION_WARNING, self._sent_keys(send))
        # And not counted as warned either: the only thing this does is send
        # an email, so a customer who cannot be emailed was never going to
        # be warned, and the preview must not promise one.
        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)

    # --- once, and only once ---------------------------------------------

    def test_re_running_the_same_day_does_not_warn_twice(self):
        """Re-running a Run must be a no-op, not a way to send a customer
        the same threat again."""
        self._customer("Warned Wanda", overdue_days=4)

        self._run()
        result, send = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)
        self.assertNotIn(EmailTemplate.Key.SUSPENSION_WARNING, self._sent_keys(send))

    def test_the_twelfth_scenario_end_to_end(self):
        """The scenario as asked for: invoice due the 12th, three-day
        blocking period, nobody pays. Warned on the 13th and the 14th, cut
        off on the 15th, and nothing at all on the 12th.

        Dated backwards from today so every run date is in the past -- the
        engine refuses a committed run on a future date, and rightly so.
        "The 12th" here is simply three days before the cut-off.
        """
        # Due 3 days ago with a 3-day blocking period: cut-off is today.
        self._customer("Demo Dumisani", overdue_days=3, blocking_period=3)
        due = self.today - datetime.timedelta(days=3)

        timeline = []
        for offset in range(0, 4):
            day = due + datetime.timedelta(days=offset)
            result, send = self._run(on_date=day)
            warning = next(
                (c for c in send.call_args_list
                 if c.args[0] == EmailTemplate.Key.SUSPENSION_WARNING),
                None,
            )
            timeline.append((
                offset,
                warning.kwargs["extra_context"]["days_until_suspension"] if warning else None,
                result["counts"]["suspensions_applied"],
            ))

        self.assertEqual(
            timeline,
            [
                (0, None, 0),   # the 12th -- due today, not late yet
                (1, 2, 0),      # the 13th -- "2 days"
                (2, 1, 0),      # the 14th -- "1 day"
                (3, None, 1),   # the 15th -- cut off, no warning
            ],
        )

    def test_the_email_carries_the_cut_off_date_and_days_left(self):
        """The whole point of the email. Without these two values it says
        no more than the payment reminder already did."""
        self._customer("Warned Wanda", overdue_days=4)

        _, send = self._run()

        call = next(
            c for c in send.call_args_list if c.args[0] == EmailTemplate.Key.SUSPENSION_WARNING
        )
        context = call.kwargs["extra_context"]
        self.assertEqual(context["suspend_date"], self.today + datetime.timedelta(days=3))
        self.assertEqual(context["days_until_suspension"], 3)

    def test_somebody_already_past_their_cut_off_is_not_warned(self):
        """"Your line will be suspended on <a date last week>" reads as a
        mistake. They are a suspension case, and the command that sends
        these reports them separately so they are not lost."""
        self._customer("Overdue Ockert", overdue_days=20)
        self.settings.auto_suspend_enabled = False
        self.settings.save()

        result, send = self._run()

        self.assertEqual(result["counts"]["suspension_warnings_sent"], 0)
        self.assertNotIn(EmailTemplate.Key.SUSPENSION_WARNING, self._sent_keys(send))

    # --- it goes through Preview first ------------------------------------

    def test_a_preview_projects_warnings_without_sending_them(self):
        self._customer("Warned Wanda", overdue_days=4)

        projection = project_run(self.today)

        self.assertEqual(projection["counts"]["suspension_warnings_sent"], 1)
        self.assertEqual(len(projection["warnings"]), 1)
        self.assertEqual(
            projection["warnings"][0]["suspend_date"], self.today + datetime.timedelta(days=3)
        )

    def test_the_preview_and_the_engine_agree(self):
        """The counts the preview shows are compared against the engine's
        own on every Preview, and a disagreement is reported to the screen.
        They are computed by the same function here, and this is what keeps
        that true."""
        self._customer("Warned Wanda", overdue_days=4)
        self._customer("Cutoff Charl", overdue_days=8)

        projection = project_run(self.today)
        engine = run_recurring_billing(self.today, commit=False)["counts"]

        self.assertEqual(projection["counts"], engine)

    def test_the_bundle_has_a_warning_sheet(self):
        """Finance signs off the CSV bundle. An email to a customer belongs
        in it as much as an invoice does."""
        self._customer("Warned Wanda", overdue_days=4)

        sheets = build_csvs(project_run(self.today))

        self.assertIn("warning", sheets)
        self.assertIn("Warned Wanda", sheets["warning"])
        self.assertIn("Suspension date", sheets["warning"])


class SendSuspensionWarningsCommandTests(SuspensionWarningTests):
    """The scheduled sender, which is the only part of billing allowed to
    run unattended -- see the command's docstring for why it is warnings
    and nothing else."""

    def _command(self, *args):
        """captureOnCommitCallbacks, for the same reason `_run` needs it.

        Every email in this engine is queued with transaction.on_commit.
        In production this command runs in autocommit -- no surrounding
        atomic block -- so Django executes the callback immediately. A
        TestCase wraps each test in a transaction that never commits, so
        without this wrapper the callbacks are simply discarded: nothing
        would be sent and `last_suspension_warning_date` would never be
        recorded, which is what made these two tests fail rather than
        anything about the command itself.
        """
        from io import StringIO

        from django.core.management import call_command

        # PINNED to the same anchor as the fixtures, and this is the other
        # half of that fix. Without --date the command uses the real
        # localdate, which is now months away from the dates these
        # customers are built on -- so every one of them reads as long past
        # their cut-off and nobody is ever warned. A test whose subject is
        # the command has to run the command on the day its fixtures live
        # on. Tests that pass their own --date are left alone.
        if "--date" not in args:
            args = ("--date", str(self.today)) + args

        out = StringIO()
        with mock.patch("billing.recurring.send_customer_email") as send:
            send.return_value = mock.Mock(status="sent")
            with self.captureOnCommitCallbacks(execute=True):
                call_command("send_suspension_warnings", *args, stdout=out, stderr=out)
        return out.getvalue(), send

    def test_a_dry_run_names_who_would_be_warned_and_sends_nothing(self):
        self._customer("Warned Wanda", overdue_days=4)

        output, send = self._command()

        self.assertIn("Warned Wanda", output)
        self.assertIn("Dry run", output)
        send.assert_not_called()

    def test_apply_sends_them(self):
        self._customer("Warned Wanda", overdue_days=4)

        output, send = self._command("--apply")

        self.assertIn("Sent 1 warning", output)
        self.assertEqual(
            [c.args[0] for c in send.call_args_list], [EmailTemplate.Key.SUSPENSION_WARNING]
        )

    def test_running_it_twice_in_a_day_sends_nothing_the_second_time(self):
        """It is meant for cron. An overlapping job, a retry, or somebody
        running it by hand after the scheduled one must not double up."""
        self._customer("Warned Wanda", overdue_days=4)
        self._command("--apply")

        output, send = self._command("--apply")

        send.assert_not_called()
        self.assertIn("already warned today", output)

    def test_it_says_so_when_warnings_are_switched_off(self):
        self.settings.warning_enabled = False
        self.settings.save()
        self._customer("Warned Wanda", overdue_days=4)

        output, send = self._command("--apply")

        self.assertIn("switched off", output)
        send.assert_not_called()

    def test_it_warns_that_nobody_will_actually_be_cut_off(self):
        """Warnings run while auto-suspension is off -- deliberate, but the
        emails then name a deadline that passes without consequence, and
        nobody should have to work that out from an empty History."""
        self.settings.auto_suspend_enabled = False
        self.settings.save()
        self._customer("Warned Wanda", overdue_days=4)

        output, _ = self._command()

        self.assertIn("auto-suspension itself is OFF", output)

    def test_it_reports_customers_already_past_their_cut_off(self):
        """The line worth reading in a cron log: nothing has suspended
        these people, and nothing will until somebody presses Run."""
        self._customer("Overdue Ockert", overdue_days=20)

        output, send = self._command()

        self.assertIn("PAST their cut-off", output)
        self.assertIn("Overdue Ockert", output)
        send.assert_not_called()

    def test_a_future_date_can_be_checked_without_sending(self):
        """"Who will I be warning on Monday" -- asked on Friday, answered
        without anything going out."""
        # Four days out, so the cut-off lands on a working day and the
        # notice window on the preview date still contains it. At five it
        # would be deferred past the window and this test would be checking
        # the weekend rule rather than the --date flag.
        self._customer("Early Elsie", overdue_days=3)

        output, send = self._command("--date", str(self.today + datetime.timedelta(days=2)))

        self.assertIn("Early Elsie", output)
        send.assert_not_called()
