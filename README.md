# LedgerMatch

Bank reconciliation for capital calls. A GP imports a bank statement CSV, the app suggests which LP's capital call line item each payment belongs to (with a reason), and the GP approves, rejects or ignores each suggestion. Approved matches update how much each LP has paid.

Stack: Django + Django REST Framework, PostgreSQL, React (Vite).

Full details (data model, endpoints, assumptions, trade-offs) are in [ASSESSMENT_NOTES.md](ASSESSMENT_NOTES.md).

## Quick start

You need Python 3.12+, Node 20.19+ and a running PostgreSQL where your user can create databases.

**Backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then set DJANGO_SECRET_KEY (see below)
createdb ledgermatch
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Generate a secret key:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

**Frontend** (second terminal)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` to Django on port 8000.

**Try it:** pick an organization, upload `backend/recon/tests/data/sample_bank_statement.csv`, then approve or reject suggestions.

## Tests

```bash
cd backend && python manage.py test
```