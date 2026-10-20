# LedgerMatch: Assessment Notes

**App name:** LedgerMatch
> I completed this assessment without using AI assistance.

## Setup from a fresh clone

Prerequisites: Python 3.12+ (developed on 3.13), Node 20.19+, and PostgreSQL running locally with a role that can create databases.

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # set DJANGO_SECRET_KEY; adjust DATABASE_URL if needed
createdb ledgermatch
python manage.py migrate
python manage.py seed_demo           # safe to run repeatedly
python manage.py runserver           # http://localhost:8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                          # http://localhost:5173 (proxies /api to :8000)
```

Generate a secret key with:
`python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`

## Run the tests

```bash
cd backend && python manage.py test
```

103 tests. Django creates and drops its own `test_ledgermatch` database, so the Postgres role needs permission to create databases. The two concurrency tests use real threads (`TransactionTestCase`) against committed data.

## `.env.example` (in `backend/`)

```
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=True
DATABASE_URL=postgres://postgres:postgres@localhost:5432/ledgermatch
```

## Data model

| Model | Key fields | Constraints |
|---|---|---|
| `Organization` | `slug`, `name` | `slug` unique |
| `CapitalCall` | `organization`, `number`, `call_date`, `due_date` | unique `(organization, number)` |
| `LineItem` | `organization`, `capital_call`, `investor_name`, `reference`, `amount_called`, `amount_funded`, `status` | unique `(organization, reference)`; **check `0 <= amount_funded <= amount_called`** |
| `BankTransaction` | `organization`, `date`, `description`, `amount`, `type`, `bank_reference`, `status`, `reason` | **unique `(organization, bank_reference)`**; check: credits positive, debits negative; index `(organization, date)`; default order `-date, -id` |
| `TransactionMatch` | `transaction`, `line_item`, `status`, `reason` | unique `(transaction, line_item)`; **at most one `approved` match per transaction** (partial unique constraint) |

- All money fields are `DecimalField(max_digits=14, decimal_places=2)`. Nothing uses floats, and the API sends amounts as strings.
- `organization` is stored directly on `LineItem` and `BankTransaction` so every query can filter on it without a join.
- `remaining` is a property (`amount_called - amount_funded`). `LineItem.status` is stored and is only ever derived in one function (`_refresh_line_status`).

## API

Every endpoint needs the `X-Organization` header. Missing gives 400 and unknown gives 404. Errors use DRF's `{"detail": "..."}` shape.

| Method and path | Purpose | Errors |
|---|---|---|
| `GET /api/line-items/` | Line items: called, funded, remaining, status | 400, 404 |
| `GET /api/transactions/` | Transactions newest first, each with its matches and reasons | 400, 404 |
| `POST /api/transactions/import/` | Upload a CSV (multipart, field `file`); returns the summary | 400 (bad file or missing column), 404 |
| `POST /api/matches/<id>/approve/` | Approve a proposed match | 404, 409 |
| `POST /api/matches/<id>/reject/` | Reject a proposed match | 404, 409 |
| `POST /api/transactions/<id>/ignore/` | Ignore a transaction | 404, 409 |

The three action endpoints take no body and return the updated transaction with its matches. Another organization's record always gives 404, never 403.

Import response:

```json
{
  "imported": 11,
  "duplicates": 1,
  "duplicate_rows": [{"line": 11, "bank_reference": "FT20260403A"}],
  "errors": [{"line": 13, "message": "Invalid date '2026-04-31': not a real calendar date."}],
  "by_status": {"suggested": 5, "needs_review": 5, "ignored": 1}
}
```

## Architecture

- **Thin views, logic in services.** `matching.py` is pure (no Django, no database) and works on snapshot dataclasses. `csv_parser.py` is pure too. `csv_import.py` and `reconciliation.py` do the database work. Services raise their own exceptions and `api/errors.py` is the one place that turns them into 404 and 409.
- **Org scoping** is an `OrgScopedMixin` on each view. It resolves the header in DRF's `initial()` hook, and every lookup filters by organization in the query itself, which is why another org's record is simply "not found".
- **Matching** follows the spec exactly: eligible items (same org, remaining > 0, call date on or before the transaction date), then the reference check, and only if no reference was found, the name-and-amount check. Suggestions are computed once at import.

## How the data-integrity guarantees work

- **Atomic approval.** `approve_match` runs in one `transaction.atomic()` block. It locks the transaction row (`select_for_update`), then the line item row, in that fixed order. Two identical simultaneous approvals queue on the transaction lock, and the second finds it already `reconciled` and gets 409. Two different payments racing for one line item are handled by the line item lock, and the remaining amount is re-read under it.
- **Amount re-check at approval time** returns 409 if the payment no longer fits.
- **Database backstops.** The `funded <= called` check and the one-approved-match-per-transaction constraint mean a bug in the service still cannot overfund a line item.
- **Duplicates** are blocked by the unique constraint on `(organization, bank_reference)`. The importer also pre-checks for friendly reporting, and it locks the organization row so two simultaneous uploads cannot race.
- **Import is all-or-nothing for the file, row-by-row for the data.** The file is parsed before any database work, so a missing column saves nothing. Valid rows then import in one transaction using two `bulk_create` calls, so the query count stays flat for thousands of rows (there is a test for that).

## Expected result for the sample file

Uploaded to `northwind`: 11 imported (5 suggested, 5 needs_review, 1 ignored), 1 duplicate (line 11), and 2 errors (line 13: impossible date 2026-04-31; line 14: negative amount on a credit).

Worth noting in a demo:
- Line 9 (`cc1001`, 7,500.00) is a partial payment against CC-1001.
- Line 8 (Oakridge) proposes both CC-1005 and CC-1006, so it is `needs_review`.
- Line 10 (13,000.00 against CC-1003) is an overpayment, with no match.
- Line 15 references CC-1006, which has not been called yet on that date, so it is `needs_review`.
- Line 12 (Cedar Grove) is `needs_review` under northwind and `suggested` under bluebird.

## Assumptions

**Organization header**
- The slug is trimmed and compared exactly. `Northwind` is a 404.

**CSV parsing**
- Header names are trimmed and case-insensitive, column order does not matter, and extra columns are ignored.
- Each invalid row reports its first problem only. A row with the wrong number of fields (usually an unquoted comma) is an error, not a silent column shift.
- Blank lines and rows of only commas are skipped, but still count for line numbers.
- Amounts must match a strict pattern (digits, optional correctly grouped thousands commas, optional decimals), so `NaN`, `1e5` and `$5` are rejected. **More than two decimal places is rejected** rather than rounded.
- Dates must be strictly `YYYY-MM-DD` and a real calendar date.
- Bank references are trimmed and compared **exactly** (case-sensitive), which matches the database constraint.
- Uploads over 10 MB are rejected with a 400.

**Matching**
- Reference matching is a plain substring test on the normalised strings, as the spec words it. The known edge case is that a reference like `CC100` would match inside `CC1001`. No seeded reference triggers it.
- Name matching looks only at the description, as whole words. A name that normalises to empty never matches.
- If several references are found, all are proposed, even if the payment is too large for some of them (approval re-checks).
- "Remaining" changes only on approval, never during import. So two payments can both be suggested against the same line item, for example sample lines 3 and 9 against CC-1001, and the approval re-check settles it.
- Debits are stored as `ignored` with the reason "Debit; only credits can pay a capital call."

**Actions**
- Ignoring an already-ignored transaction returns 409, since the spec allows ignoring only `suggested` and `needs_review`.
- Approving or rejecting a match that is not `proposed`, or whose transaction is already `reconciled` or `ignored`, returns 409.
- Rejecting one of several proposals leaves the transaction `needs_review`. It does not go back to `suggested`, because suggestions are computed only once. Rejecting the last proposal sets the reason "All suggested matches were rejected."
- A reconciled transaction's own reason is cleared. The approved match keeps its reason.

**Seed**
- `seed_demo` uses `get_or_create`, so re-running never overwrites funding progress. The consequence is that editing a value in the seed file does not change a row that already exists.

## Trade-offs

- The organization list in the React app is a constant in `frontend/src/config.js`. There is no organization-list endpoint, because the spec does not ask for one.
- No pagination. The transaction list returns everything for the org, which is fine for the page described but not for very large histories.
- No CORS setup. In development the Vite proxy makes the browser see one origin. A deployment would need either a same-origin reverse proxy or `django-cors-headers`.
- `LineItem.status` is stored, not derived, because approval updates it. It can only drift if code bypasses `_refresh_line_status`, which is the single place it is set.
- Import runs synchronously inside the request. That is fine for a few thousand rows, which is the stated size, and the spec rules out background workers.
- The frontend has no automated tests. Backend tests carry the correctness requirements.

## Next steps

- Pagination and filtering on the transaction list.
- An endpoint for the organization list, and real authentication with per-user organization access.
- A way to recompute suggestions on demand, for example after new line items are added.
- Partial approvals (approving less than the full payment amount).
- Frontend component tests, and a deployed demo.