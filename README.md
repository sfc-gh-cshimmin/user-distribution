# User Distribution

Streamlit app for distributing Snowflake lab accounts to hands-on event attendees.

## How it works

- **Admin** creates an event with a list of Snowflake accounts and usernames
- **Attendees** visit a URL, enter their email, and receive credentials (account URL, username, password)
- Usernames are assigned sequentially (one account fills before the next)
- Same email re-submitted returns the existing assignment (idempotent)

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
```

## Project Structure

```
├── attendee_app.py      # Main app (attendee + admin, routed by ?event= param)
├── config.py            # Snowflake connection helper with multi-auth support
├── admin_app.py         # Standalone admin app (unused in production, kept for reference)
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
