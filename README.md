# User Distribution

Streamlit app for distributing Snowflake lab accounts to hands-on event attendees.

## How it works

- **Admin** creates an event with a list of Snowflake accounts and usernames (or dynamic mode)
- **Attendees** visit a URL, enter their email, and receive credentials (account URL, username, password)
- Usernames are assigned based on the configured distribution mode
- Same email re-submitted returns the existing assignment (idempotent)

## Event Modes

| Mode | Description |
|------|-------------|
| **Static** | Admin provides a list of usernames upfront. All rows are pre-populated. |
| **Dynamic** | Usernames (USER1, USER2, ...) are generated on-the-fly when attendees claim. A Snowflake USER is created on the target account via the ADMIN user at claim time. |

## Distribution Modes

| Mode | Description |
|------|-------------|
| **sequential** | Fill one account completely before moving to the next |
| **round_robin** | Spread claims evenly across accounts |
| **random** | Assign a random available username from any account |

## URLs

| Path | Purpose |
|------|---------|
| `?event=EVENT_NAME` | Attendee claim page for a specific event |
| `?event=admin` | Password-protected admin panel |
| (no param) | "Ask your instructor" landing page |

## Admin Panel

Access at `?event=admin`. Features:

- **Create Event** — provide event name, account CSV, and username list
- **Manage Usernames** — filter by account/availability, select rows with checkboxes, assign/unassign
- **Event Management** — rename (with option to keep URL slug unchanged), delete

## Account CSV Format

Same format as `si_admin`:

```
Account ID,Status,Assigned To,URL
SFSEHOL_ABC123,ready,cameron.shimmin@snowflake.com,https://sfsehol-abc123.snowflakecomputing.com
SFSEHOL_DEF456,ready,Unassigned,https://sfsehol-def456.snowflakecomputing.com
```

## Backend

Snowflake tables in the `USER_DISTRIBUTION` database. Each event is a schema containing:

- `USERNAMES` — one row per username × account combination
- `EVENT_CONFIG` — event metadata

## Auth Priority (config.py)

1. **Streamlit Cloud secrets** (`st.secrets["snowflake"]`)
2. **SPCS OAuth token** (`/snowflake/session/token`)
3. **Key-pair auth** (`SNOWFLAKE_PRIVATE_KEY_PATH` env var)
4. **Password auth** (`SNOWFLAKE_PASSWORD` env var)
5. **Named connection** from `~/.snowflake/connections.toml` (local dev)

## Local Development

```bash
# Uses OAuth via product_demos connection in connections.toml
streamlit run attendee_app.py --server.port 8503
```

- Admin: http://localhost:8503/?event=admin (password: `admin`)
- Attendee: http://localhost:8503/?event=EVENT_NAME

## Deployment (Streamlit Community Cloud)

Deployed from this repo. Secrets configured in the Streamlit Cloud dashboard:

```toml
[snowflake]
account = "SFSENORTHAMERICA-PRODUCT_DEMOS"
user = "USER_DIST_SVC"
password = "..."
role = "ACCOUNTADMIN"
warehouse = "COMPUTE_WH"

[admin]
password = "your-admin-password"

[accounts]
admin_password = "sn0wf@ll"

[ses]
aws_access_key_id = "..."
aws_secret_access_key = "..."
region = "us-west-2"
sender = "developers@snowflake.com"
```

## Project Structure

```
├── attendee_app.py      # Main app (attendee + admin, routed by ?event= param)
├── config.py            # Snowflake connection helper with multi-auth support
├── requirements.txt     # Python dependencies
├── Dockerfile           # SPCS/container deployment (nginx + streamlit)
├── nginx.conf           # Path rewriting (/EVENT → ?event=EVENT)
├── start.sh             # Container entrypoint
├── deploy.sh            # SPCS build/push/deploy script
├── docker-compose.yml   # Local Docker dev
└── setup/               # Snowflake SQL setup scripts
    ├── 01_create_infrastructure.sql
    └── 02_create_service.sql
```

## Concurrency / Race Condition Handling

The app handles multiple attendees claiming at the same time without double-assigning:

1. **Fetch candidates** — SELECT fetches 10 unclaimed rows (not 1) as a batch
2. **Conditional UPDATE** — each claim attempt uses:
   ```sql
   UPDATE USERNAMES
   SET CLAIMER_EMAIL = :email, CLAIMED_AT = CURRENT_TIMESTAMP()
   WHERE USERNAME = :username AND ACCOUNT_ID = :account_id AND CLAIMER_EMAIL IS NULL
   ```
   The `AND CLAIMER_EMAIL IS NULL` clause acts as an optimistic lock. Snowflake's row-level locking ensures only one UPDATE can succeed per row.
3. **Check rowcount** — if `cursor.rowcount == 0`, the row was grabbed by someone else; try the next candidate
4. **Retry through batch** — iterates through up to 10 candidates before giving up

This means even under heavy concurrency, each user works through their candidate list until one succeeds. The only failure case is if all 10 candidates are claimed between the SELECT and the UPDATE loop, which would require extreme load.
