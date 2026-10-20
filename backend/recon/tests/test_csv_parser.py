import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from recon.services.csv_parser import FileRejected, parse_csv

HEADER = "date,description,amount,type,reference\n"
SAMPLE = Path(__file__).parent / "data" / "sample_bank_statement.csv"


def line(date_="2026-04-03", description="WIRE IN", amount="100.00",
         type_="credit", reference="R1"):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(
        [date_, description, amount, type_, reference]
    )
    return buf.getvalue()


def parse(body, header=HEADER):
    return parse_csv((header + body).encode("utf-8"))


class ParserTestCase(SimpleTestCase):
    def assert_one_error(self, body, line_no, fragment):
        result = parse(body)
        self.assertEqual(result.rows, [])
        self.assertEqual(len(result.errors), 1, result.errors)
        self.assertEqual(result.errors[0].line, line_no)
        self.assertIn(fragment.lower(), result.errors[0].message.lower())


class FileLevelTests(ParserTestCase):
    def test_missing_column_rejects_the_file(self):
        with self.assertRaises(FileRejected) as ctx:
            parse_csv(b"date,description,amount,type\n2026-04-03,x,1.00,credit\n")
        self.assertIn("reference", str(ctx.exception))

    def test_empty_file_is_rejected(self):
        for raw in (b"", b"  \n\n"):
            with self.subTest(raw=raw), self.assertRaises(FileRejected):
                parse_csv(raw)

    def test_non_utf8_file_is_rejected(self):
        with self.assertRaises(FileRejected):
            parse_csv(HEADER.encode() + b"2026-04-03,caf\xe9,1.00,credit,R1\n")

    def test_header_is_trimmed_and_case_insensitive(self):
        result = parse(line(), header="Date, Description ,AMOUNT,Type,Reference\n")
        self.assertEqual(len(result.rows), 1)

    def test_extra_and_reordered_columns_are_fine(self):
        raw = (
            "reference,notes,type,amount,description,date\n"
            "R9,x,credit,5.00,hello,2026-04-03\n"
        )
        row = parse_csv(raw.encode()).rows[0]
        self.assertEqual(
            (row.reference, row.amount, row.description, row.line),
            ("R9", Decimal("5.00"), "hello", 2),
        )

    def test_header_only_file_is_valid_and_empty(self):
        result = parse("")
        self.assertEqual((result.rows, result.errors), ([], []))

    def test_bom_is_ignored(self):
        raw = ("\ufeff" + HEADER + line()).encode("utf-8")
        self.assertEqual(len(parse_csv(raw).rows), 1)

    def test_crlf_line_endings(self):
        raw = b"date,description,amount,type,reference\r\n2026-04-03,WIRE,100.00,credit,R1\r\n"
        self.assertEqual(len(parse_csv(raw).rows), 1)


class LineNumberTests(ParserTestCase):
    def test_blank_lines_still_count(self):
        body = line(reference="R1") + "\n" + "   \n" + ",,,,\n" + line(date_="bad", reference="R2")
        result = parse(body)
        self.assertEqual([r.line for r in result.rows], [2])
        self.assertEqual([e.line for e in result.errors], [6])

    def test_quoted_newline_keeps_following_line_numbers_right(self):
        body = (
            '2026-04-03,"WIRE\nIN",100.00,credit,R1\n'
            "2026-04-04,Next,oops,credit,R2\n"
        )
        result = parse(body)
        self.assertEqual(result.rows[0].line, 2)
        self.assertEqual(result.rows[0].description, "WIRE\nIN")
        self.assertEqual([e.line for e in result.errors], [4])


class FieldTests(ParserTestCase):
    def test_all_fields_are_trimmed_and_type_is_lowercased(self):
        row = parse(
            line(date_=" 2026-04-03 ", description="  wire in  ",
                 amount=" 100.00 ", type_=" CREDIT ", reference=" R1 ")
        ).rows[0]
        self.assertEqual(row.date, date(2026, 4, 3))
        self.assertEqual(row.description, "wire in")
        self.assertEqual(row.type, "credit")
        self.assertEqual(row.reference, "R1")

    def test_wrong_field_count_is_an_error(self):
        self.assert_one_error("2026-04-03,desc,100.00,credit\n", 2, "expected 5 fields")
        # Unquoted comma in the description shifts every column.
        self.assert_one_error(
            "2026-04-03,WIRE, ACME,100.00,credit,R1\n", 2, "found 6"
        )

    def test_required_text_fields(self):
        cases = [
            (line(description=""), "description"),
            (line(description="   "), "description"),
            (line(reference=""), "reference"),
            (line(type_=""), "type"),
        ]
        for body, fragment in cases:
            with self.subTest(body=body):
                self.assert_one_error(body, 2, fragment)

    def test_length_limits(self):
        self.assertEqual(len(parse(line(description="x" * 500)).rows), 1)
        self.assertEqual(len(parse(line(reference="r" * 100)).rows), 1)
        self.assert_one_error(line(description="x" * 501), 2, "description")
        self.assert_one_error(line(reference="r" * 101), 2, "reference")

    def test_nul_character_is_an_error_not_a_crash(self):
        self.assert_one_error(line(description="a\x00b"), 2, "nul")

    def test_invalid_type(self):
        self.assert_one_error(line(type_="wire"), 2, "credit or debit")


class DateTests(ParserTestCase):
    def test_valid_dates(self):
        for raw, expected in [("2026-04-03", date(2026, 4, 3)), ("2024-02-29", date(2024, 2, 29))]:
            with self.subTest(raw=raw):
                self.assertEqual(parse(line(date_=raw)).rows[0].date, expected)

    def test_not_a_real_calendar_date(self):
        for raw in ("2026-04-31", "2026-13-01", "2026-02-29", "0000-01-01"):
            with self.subTest(raw=raw):
                self.assert_one_error(line(date_=raw), 2, "calendar date")

    def test_wrong_format(self):
        for raw in ("04/03/2026", "2026-4-3", "20260403", "2026-04-03T00:00", "٢٠٢٦-٠٤-٠٣"):
            with self.subTest(raw=raw):
                self.assert_one_error(line(date_=raw), 2, "YYYY-MM-DD")

    def test_empty_date(self):
        self.assert_one_error(line(date_=""), 2, "date is required")


class AmountTests(ParserTestCase):
    def test_valid_amounts_become_two_place_decimals(self):
        cases = {
            "25,000.00": "25000.00",
            "20000.00": "20000.00",
            "100": "100.00",
            "1,234,567.89": "1234567.89",
            "+5.5": "5.50",
            "0.01": "0.01",
            "12.500": "12.50",
            "999999999999.99": "999999999999.99",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                amount = parse(line(amount=raw)).rows[0].amount
                self.assertIsInstance(amount, Decimal)
                self.assertEqual(str(amount), expected)

    def test_invalid_amounts(self):
        bad = [
            "", "abc", "NaN", "Infinity", "-Infinity", "1e5", "1,00", "1,2345",
            "1234,567", "$5", "5.", ".5", "1.234", "1 000", "0", "0.00", "-0.00",
            "0,500.00", "1234567890123.00",
        ]
        for raw in bad:
            with self.subTest(raw=raw):
                self.assert_one_error(line(amount=raw), 2, "amount")

    def test_zero_and_decimal_place_messages(self):
        self.assert_one_error(line(amount="0.00"), 2, "must not be zero")
        self.assert_one_error(line(amount="1.234"), 2, "two decimal places")

    def test_credit_must_be_positive(self):
        self.assert_one_error(line(amount="-15000.00", type_="credit"), 2, "credit amount must be positive")

    def test_debit_must_be_negative(self):
        self.assert_one_error(line(amount="5.00", type_="debit"), 2, "debit amount must be negative")

    def test_debit_with_negative_amount_is_valid(self):
        row = parse(line(amount="-25.00", type_="DEBIT")).rows[0]
        self.assertEqual((row.type, row.amount), ("debit", Decimal("-25.00")))

    def test_long_bad_values_are_truncated_in_messages(self):
        result = parse(line(amount="9" * 500 + "x"))
        self.assertLess(len(result.errors[0].message), 200)


class SampleFileTests(ParserTestCase):
    def test_sample_file(self):
        result = parse_csv(SAMPLE.read_bytes())
        # Duplicates are not the parser's concern, so line 11 is still a valid row here.
        self.assertEqual(
            [r.line for r in result.rows], [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15]
        )
        self.assertEqual([e.line for e in result.errors], [13, 14])
        self.assertIn("2026-04-31", result.errors[0].message)
        self.assertIn("Credit amount must be positive", result.errors[1].message)

        by_line = {r.line: r for r in result.rows}
        self.assertEqual(by_line[2].amount, Decimal("25000.00"))  # was "25,000.00"
        self.assertEqual(by_line[6].description, "wire incoming meridian partners")  # trimmed
        self.assertEqual(by_line[9].type, "credit")  # was CREDIT
        self.assertEqual(by_line[4].type, "debit")