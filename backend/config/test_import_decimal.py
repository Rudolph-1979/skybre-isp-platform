"""Money cells from a spreadsheet, in both conventions a real export uses.

The cleaner used to be `Decimal(re.sub(r"[^0-9.\\-]", "", raw))`, which
stripped the comma -- and in the locale this importer's own date parser
invokes ("this is a South African system"), the comma IS the decimal
separator and the space is the thousands separator. So every
comma-decimal cell imported a hundred times too large, with no error and
a green preview.

That is not hypothetical: PROJECT_STATUS records 1,592 real customers
imported through this function with Splynx's "Account balance" column
mapped to Customer.balance.

The parenthesised-negative case is the same bug in the other direction:
`(150.00)` is the accounting notation for a credit, and the old regex
turned it into +150.00 -- a R300 swing.
"""
from decimal import Decimal

from django.test import TestCase

from config.csv_import import AmbiguousNumber, parse_import_decimal


class DotDecimalTests(TestCase):
    """The convention that already worked. It has to keep working."""

    def test_a_plain_dot_decimal(self):
        self.assertEqual(parse_import_decimal("1234.56"), Decimal("1234.56"))

    def test_a_currency_symbol_is_stripped(self):
        self.assertEqual(parse_import_decimal("R975.00"), Decimal("975.00"))

    def test_a_negative_with_a_symbol(self):
        self.assertEqual(parse_import_decimal("-R0.19"), Decimal("-0.19"))

    def test_a_whole_number(self):
        self.assertEqual(parse_import_decimal("500"), Decimal("500"))

    def test_a_blank_cell_is_zero(self):
        self.assertEqual(parse_import_decimal(""), Decimal("0"))
        self.assertEqual(parse_import_decimal(None), Decimal("0"))

    def test_a_zar_prefix_is_stripped(self):
        self.assertEqual(parse_import_decimal("ZAR 1250.00"), Decimal("1250.00"))


class CommaDecimalTests(TestCase):
    """The convention that imported 100x too large."""

    def test_a_comma_decimal(self):
        self.assertEqual(parse_import_decimal("1234,56"), Decimal("1234.56"))

    def test_the_small_case_that_hurt_most(self):
        """12,50 became 1250.00 -- a hundredfold on every cent-bearing row."""
        self.assertEqual(parse_import_decimal("12,50"), Decimal("12.50"))

    def test_a_space_thousands_separator_with_a_comma_decimal(self):
        """The canonical en-ZA rendering, and what an Excel export on a
        local machine produces."""
        self.assertEqual(parse_import_decimal("1 234,56"), Decimal("1234.56"))
        self.assertEqual(parse_import_decimal("R1 234,56"), Decimal("1234.56"))
        self.assertEqual(parse_import_decimal("R 2 500,00"), Decimal("2500.00"))

    def test_a_non_breaking_space_thousands_separator(self):
        """Excel emits U+00A0 rather than a plain space."""
        self.assertEqual(parse_import_decimal("1 847,50"), Decimal("1847.50"))

    def test_the_balance_from_the_finding(self):
        self.assertEqual(parse_import_decimal("1 847,50"), Decimal("1847.50"))

    def test_a_comma_decimal_with_one_decimal_place(self):
        self.assertEqual(parse_import_decimal("99,5"), Decimal("99.5"))


class BothSeparatorsTests(TestCase):
    """Unambiguous: the rightmost separator is the decimal one."""

    def test_dot_thousands_comma_decimal(self):
        self.assertEqual(parse_import_decimal("1.234,56"), Decimal("1234.56"))

    def test_comma_thousands_dot_decimal(self):
        self.assertEqual(parse_import_decimal("1,234.56"), Decimal("1234.56"))

    def test_millions_either_way(self):
        self.assertEqual(parse_import_decimal("1.234.567,89"), Decimal("1234567.89"))
        self.assertEqual(parse_import_decimal("1,234,567.89"), Decimal("1234567.89"))

    def test_a_repeated_separator_alone_is_thousands(self):
        self.assertEqual(parse_import_decimal("1,234,567"), Decimal("1234567"))
        self.assertEqual(parse_import_decimal("1.234.567"), Decimal("1234567"))


class NegativeTests(TestCase):
    def test_a_leading_minus(self):
        self.assertEqual(parse_import_decimal("-150.00"), Decimal("-150.00"))

    def test_accounting_parentheses_are_a_credit(self):
        """The old regex discarded the brackets, so R150 the ISP OWED the
        customer imported as R150 the customer owed the ISP."""
        self.assertEqual(parse_import_decimal("(150.00)"), Decimal("-150.00"))

    def test_accounting_parentheses_with_a_symbol_and_comma_decimal(self):
        self.assertEqual(parse_import_decimal("(R1 250,00)"), Decimal("-1250.00"))

    def test_a_leading_plus_is_ignored(self):
        self.assertEqual(parse_import_decimal("+150.00"), Decimal("150.00"))


class AmbiguityTests(TestCase):
    """Where guessing would be wrong, it refuses instead.

    `1,500` is 1500 in en-US and 1.5 in en-ZA, and nothing in the cell
    distinguishes them. A hundredfold error on money is not something to
    resolve with a coin flip -- the same reasoning the date parser gives
    for having no month-first fallback.
    """

    def test_a_single_separator_with_three_trailing_digits_is_refused(self):
        for cell in ("1,500", "1.500", "12,345", "R2,750"):
            with self.subTest(cell=cell):
                with self.assertRaises(AmbiguousNumber):
                    parse_import_decimal(cell)

    def test_the_refusal_names_both_readings_and_how_to_fix_it(self):
        with self.assertRaises(AmbiguousNumber) as caught:
            parse_import_decimal("1,500")
        message = str(caught.exception)
        self.assertIn("1500", message)
        self.assertIn("1.500", message)
        self.assertIn("two decimal places", message)

    def test_an_unambiguous_thousands_value_is_not_refused(self):
        """Adding the cents makes it unambiguous, which is what the error
        message asks for."""
        self.assertEqual(parse_import_decimal("1 500,00"), Decimal("1500.00"))
        self.assertEqual(parse_import_decimal("1500"), Decimal("1500"))
        self.assertEqual(parse_import_decimal("1,500.00"), Decimal("1500.00"))


class MalformedTests(TestCase):
    def test_text_is_rejected_rather_than_becoming_zero(self):
        for cell in ("n/a", "unknown", "R", "-"):
            with self.subTest(cell=cell):
                with self.assertRaises(ValueError):
                    parse_import_decimal(cell)

    def test_a_separator_with_four_trailing_digits_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_import_decimal("1,2345")

    def test_a_thousands_separator_that_does_not_group_thousands_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_import_decimal("1,23,456.00")


class BooleanCoercionTests(TestCase):
    """Unrecognised boolean text used to become False with no error --
    unlike every other type in the importer, all of which report. A tariff
    sheet whose is_active column read "Active"/"Inactive" (the words the UI
    itself uses) silently deactivated every plan."""

    def _clean(self, raw):
        from config.csv_import import CSVImportMixin

        class _Importer(CSVImportMixin):
            import_fields = {"flag": {"type": "bool", "default": True}}

        return _Importer()._validate_row({"flag": raw})

    def test_the_words_the_ui_uses_are_understood(self):
        cleaned, errors = self._clean("Active")
        self.assertEqual(errors, [])
        self.assertIs(cleaned["flag"], True)
        cleaned, errors = self._clean("Inactive")
        self.assertEqual(errors, [])
        self.assertIs(cleaned["flag"], False)

    def test_the_usual_spellings_still_work(self):
        for raw, expected in [
            ("1", True), ("true", True), ("YES", True), ("y", True),
            ("0", False), ("FALSE", False), ("no", False), ("n", False),
        ]:
            with self.subTest(raw=raw):
                cleaned, errors = self._clean(raw)
                self.assertEqual(errors, [])
                self.assertIs(cleaned["flag"], expected)

    def test_a_typo_is_reported_rather_than_read_as_false(self):
        cleaned, errors = self._clean("ture")
        self.assertTrue(errors)
        self.assertIn("yes or no", errors[0])

    def test_a_blank_cell_still_takes_the_default(self):
        cleaned, errors = self._clean("")
        self.assertEqual(errors, [])
        self.assertIs(cleaned["flag"], True)
