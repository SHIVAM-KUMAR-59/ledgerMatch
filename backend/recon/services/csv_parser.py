"""Strict CSV parsing for bank statements.

Pure module: no Django and no database. `parse_csv` returns the valid rows and
one error per invalid row (first failure only). A problem with the file as a
whole raises FileRejected, and the caller must then save nothing.
"""

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass
from decimal import Decimal

REQUIRED_COLUMNS = ("date", "description", "amount", "type", "reference")
MAX_DESCRIPTION_LENGTH = 500
MAX_REFERENCE_LENGTH = 100
MAX_INTEGER_DIGITS = 12

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_AMOUNT_RE = re.compile(
    r"[+-]?(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
)
_CENTS = Decimal("0.01")


class FileRejected(Exception):
    """The whole file is unusable. The message is safe to show the user."""


class _RowInvalid(Exception):
    pass


@dataclass(frozen=True)
class ParsedRow:
    line: int
    date: dt.date
    description: str
    amount: Decimal
    type: str  # "credit" or "debit", lowercase
    reference: str


@dataclass(frozen=True)
class RowError:
    line: int
    message: str


@dataclass(frozen=True)
class ParseResult:
    rows: list
    errors: list


def parse_csv(raw: bytes) -> ParseResult:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileRejected("The file is not valid UTF-8 text.") from None
    if not text.strip():
        raise FileRejected("The file is empty.")

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader, [])
    except csv.Error as exc:
        raise FileRejected(f"Could not read the CSV header: {exc}") from None

    names = [h.strip().lower() for h in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in names]
    if missing:
        raise FileRejected(f"Missing required column(s): {', '.join(missing)}.")
    index = {name: names.index(name) for name in REQUIRED_COLUMNS}

    rows: list[ParsedRow] = []
    errors: list[RowError] = []
    while True:
        start_line = reader.line_num + 1
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise FileRejected(
                f"Could not read the CSV near line {start_line}: {exc}"
            ) from None

        if not any(f.strip() for f in fields):
            continue
        if len(fields) != len(names):
            errors.append(
                RowError(
                    start_line,
                    f"Expected {len(names)} fields but found {len(fields)}.",
                )
            )
            continue

        values = {name: fields[i].strip() for name, i in index.items()}
        try:
            rows.append(_validate(start_line, values))
        except _RowInvalid as exc:
            errors.append(RowError(start_line, str(exc)))

    return ParseResult(rows=rows, errors=errors)


def _validate(line: int, v: dict) -> ParsedRow:
    if any("\x00" in value for value in v.values()):
        raise _RowInvalid("Row contains a NUL character.")
    parsed_date = _parse_date(v["date"])

    description = v["description"]
    if not description:
        raise _RowInvalid("Description is required.")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise _RowInvalid(
            f"Description is longer than {MAX_DESCRIPTION_LENGTH} characters."
        )

    amount = _parse_amount(v["amount"])

    txn_type = v["type"].lower()
    if not txn_type:
        raise _RowInvalid("Type is required.")
    if txn_type not in ("credit", "debit"):
        raise _RowInvalid(
            f"Invalid type '{_shown(v['type'])}': expected credit or debit."
        )
    if txn_type == "credit" and amount < 0:
        raise _RowInvalid(f"Credit amount must be positive (got {amount}).")
    if txn_type == "debit" and amount > 0:
        raise _RowInvalid(f"Debit amount must be negative (got {amount}).")

    reference = v["reference"]
    if not reference:
        raise _RowInvalid("Reference is required.")
    if len(reference) > MAX_REFERENCE_LENGTH:
        raise _RowInvalid(
            f"Reference is longer than {MAX_REFERENCE_LENGTH} characters."
        )

    return ParsedRow(
        line=line,
        date=parsed_date,
        description=description,
        amount=amount,
        type=txn_type,
        reference=reference,
    )


def _parse_date(raw: str) -> dt.date:
    if not raw:
        raise _RowInvalid("Date is required.")
    if not _DATE_RE.fullmatch(raw):
        raise _RowInvalid(f"Invalid date '{_shown(raw)}': expected YYYY-MM-DD.")
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise _RowInvalid(
            f"Invalid date '{_shown(raw)}': not a real calendar date."
        ) from None


def _parse_amount(raw: str) -> Decimal:
    if not raw:
        raise _RowInvalid("Amount is required.")
    if not _AMOUNT_RE.fullmatch(raw):
        raise _RowInvalid(
            f"Invalid amount '{_shown(raw)}'. Use digits with an optional "
            "decimal part, e.g. 25,000.00."
        )
    cleaned = raw.replace(",", "")
    integer_part = cleaned.lstrip("+-").split(".")[0]
    if len(integer_part.lstrip("0")) > MAX_INTEGER_DIGITS:
        raise _RowInvalid(f"Invalid amount '{_shown(raw)}': too large.")

    value = Decimal(cleaned)
    cents = value.quantize(_CENTS)
    if value != cents:
        raise _RowInvalid(
            f"Invalid amount '{_shown(raw)}': at most two decimal places are allowed."
        )
    if cents == 0:
        raise _RowInvalid("Amount must not be zero.")
    return cents


def _shown(value: str, limit: int = 40) -> str:
    """Keep echoed user input short so a hostile file can't bloat the response."""
    return value if len(value) <= limit else value[:limit] + "..."