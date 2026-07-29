# Alembic — schema owner for the receipts domain

Alembic is the **sole owner** of the `receipts`, `receipt_fields`, and `receipt_line_items`
tables and the `extraction_status` enum. The application never creates or alters schema at
runtime (`Base.metadata.create_all()` is never called).

## Usage

Requires a configured database. Set `DATABASE_URL` (or `DB_USER`/`DB_HOST`/`DB_NAME` and
`DB_PASSWORD`) in the environment or a `.env` file, then from `backend/`:

```bash
alembic upgrade head        # apply all migrations (creates tables/indexes/FKs/enum)
alembic current             # show the current revision
alembic downgrade -1        # roll back the most recent migration
alembic history --verbose   # list revisions
```

## DB safety (critical)

- **Never** run destructive commands (`downgrade base`, manual `DROP`/`TRUNCATE`, `stamp`
  to force state) against a database with real data unless the owner has explicitly approved.
- If `alembic upgrade` fails because the actual schema disagrees with the recorded revision
  (e.g. a `receipts` table already exists that this project did not create):
  1. **STOP.** Do not `stamp`, drop, or truncate to "resolve" it.
  2. Capture expected vs. actual: `alembic current`, `alembic heads`, and inspect the live
     schema (`\d receipts` in psql, or an information_schema query).
  3. Report both states and the safest reconciliation strategy (e.g. author a corrective
     migration, or align the DB to the baseline manually) and wait for owner approval.
- Only manage the three tables + enum owned by this application. Do not touch other schemas.
