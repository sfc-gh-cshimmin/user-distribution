"""
User Distribution App
- Attendee claim flow: ?event=EVENT_NAME
- Admin panel: ?event=admin (password protected)
"""

import re
import pandas as pd
import streamlit as st
from typing import List, Dict
from config import get_connection, get_account_connection, DATABASE, DEFAULT_PASSWORD


# =============================================================================
# Email helper (AWS SES)
# =============================================================================


def send_claim_email(claim: dict, instructions_url: str = None):
    """Send credentials email via AWS SES. Shows toast on success/failure."""
    try:
        if "ses" not in st.secrets:
            return

        ses_config = st.secrets["ses"]

        import boto3

        client = boto3.client(
            "ses",
            region_name=ses_config.get("region", "us-west-2"),
            aws_access_key_id=ses_config["aws_access_key_id"],
            aws_secret_access_key=ses_config["aws_secret_access_key"],
        )

        sender = ses_config["sender"]
        account_link = claim.get("account_url", claim["account_id"])

        instructions_html = ""
        instructions_text = ""
        if instructions_url:
            instructions_html = (
                f'<p style="margin-top:24px;">'
                f'<a href="{instructions_url}" style="display:inline-block; padding:12px 24px; '
                f'background-color:#29B5E8; color:#ffffff; text-decoration:none; font-weight:bold; '
                f'font-size:16px; border-radius:6px;">View Lab Instructions &rarr;</a></p>'
            )
            instructions_text = f"\nInstructions: {instructions_url}"

        body_html = f"""
        <h2>Your Lab Credentials</h2>
        <table style="border-collapse:collapse; font-size:16px;">
            <tr><td style="padding:8px; font-weight:bold;">Account</td><td style="padding:8px;"><a href="{account_link}">{account_link}</a></td></tr>
            <tr><td style="padding:8px; font-weight:bold;">Username</td><td style="padding:8px;"><code>{claim['username']}</code></td></tr>
            <tr><td style="padding:8px; font-weight:bold;">Password</td><td style="padding:8px;"><code>{DEFAULT_PASSWORD}</code></td></tr>
        </table>
        {instructions_html}
        <p style="margin-top:16px; color:#666;">Save these credentials. You'll need them to log in to the lab environment.</p>
        """

        body_text = f"""Your Lab Credentials
Account: {account_link}
Username: {claim['username']}
Password: {DEFAULT_PASSWORD}
{instructions_text}
Save these credentials. You'll need them to log in to the lab environment."""

        client.send_email(
            Source=sender,
            Destination={"ToAddresses": [claim["email"]]},
            Message={
                "Subject": {"Data": "Your Lab Account Credentials"},
                "Body": {
                    "Html": {"Data": body_html},
                    "Text": {"Data": body_text},
                },
            },
        )
        st.toast(f"Email sent to {claim['email']}")
    except Exception as e:
        st.toast(f"Email failed: {e}", icon="⚠️")

# =============================================================================
# Page configuration
# =============================================================================

st.set_page_config(
    page_title="User Distribution",
    page_icon=":material/person_add:",
    layout="centered",
)

# =============================================================================
# Session state
# =============================================================================

st.session_state.setdefault("conn", None)
st.session_state.setdefault("claimed", None)
st.session_state.setdefault("admin_authenticated", False)


def get_conn():
    if st.session_state["conn"] is None or st.session_state["conn"].is_closed():
        st.session_state["conn"] = get_connection()
    return st.session_state["conn"]


# =============================================================================
# Shared helpers
# =============================================================================


def get_event_schemas() -> List[str]:
    """List all event schemas in the database (excludes INFORMATION_SCHEMA, PUBLIC)."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SHOW SCHEMAS IN DATABASE {DATABASE}")
    except Exception:
        return []
    rows = cur.fetchall()
    exclude = {"INFORMATION_SCHEMA", "PUBLIC", "SPCS"}
    return [r[1] for r in rows if r[1] not in exclude]


def ensure_database_exists():
    """Create the database if it doesn't exist."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")


# =============================================================================
# Admin helpers
# =============================================================================


def sanitize_schema_name(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name.strip()).upper()
    if not sanitized or sanitized[0].isdigit():
        sanitized = "EVT_" + sanitized
    return sanitized


def parse_account_csv(csv_text: str) -> List[Dict]:
    if not csv_text or not csv_text.strip():
        return []
    accounts = []
    lines = csv_text.strip().split("\n")
    start_idx = 0
    if lines and "account" in lines[0].lower():
        start_idx = 1
    for line in lines[start_idx:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 1:
            continue
        account_id = parts[0].strip()
        url = parts[3].strip() if len(parts) > 3 else ""
        accounts.append({"account_id": account_id, "url": url})
    return accounts


def get_accounts_for_event(schema: str) -> List[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT DISTINCT ACCOUNT_ID FROM {DATABASE}.{schema}.USERNAMES ORDER BY ACCOUNT_ID")
    return [r[0] for r in cur.fetchall()]


def get_usernames_df(schema: str, accounts: List[str], availability: str) -> pd.DataFrame:
    """Query usernames with filters applied."""
    conn = get_conn()
    cur = conn.cursor()
    query = f"SELECT USERNAME, ACCOUNT_ID, ACCOUNT_URL, CLAIMER_EMAIL, CLAIMED_AT, PREVIOUSLY_ASSIGNED_TO FROM {DATABASE}.{schema}.USERNAMES WHERE USERNAME != '__ACCOUNT_REF__'"
    params = []
    if accounts:
        placeholders = ", ".join(["%s"] * len(accounts))
        query += f" AND ACCOUNT_ID IN ({placeholders})"
        params.extend(accounts)
    if availability == "Available":
        query += " AND CLAIMER_EMAIL IS NULL AND PREVIOUSLY_ASSIGNED_TO IS NULL"
    elif availability == "Claimed":
        query += " AND CLAIMER_EMAIL IS NOT NULL"
    elif availability == "Burned":
        query += " AND PREVIOUSLY_ASSIGNED_TO IS NOT NULL AND CLAIMER_EMAIL IS NULL"
    query += " ORDER BY ACCOUNT_ID, USERNAME"
    cur.execute(query, params)
    cols = [desc[0] for desc in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=cols)


# =============================================================================
# Attendee helpers
# =============================================================================


def check_existing_claim(schema: str, email: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""SELECT USERNAME, ACCOUNT_ID, ACCOUNT_URL, CLAIMER_EMAIL, CLAIMED_AT
            FROM {DATABASE}.{schema}.USERNAMES
            WHERE CLAIMER_EMAIL = %s
            LIMIT 1""",
        (email,),
    )
    row = cur.fetchone()
    if row:
        return {"username": row[0], "account_id": row[1], "account_url": row[2], "email": row[3], "claimed_at": row[4]}
    return None


def get_claimed_usernames(schema: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""SELECT USERNAME, ACCOUNT_ID, ACCOUNT_URL, CLAIMER_EMAIL, CLAIMED_AT
            FROM {DATABASE}.{schema}.USERNAMES
            WHERE CLAIMER_EMAIL IS NOT NULL
            ORDER BY CLAIMED_AT DESC"""
    )
    return [
        {"username": row[0], "account_id": row[1], "account_url": row[2], "email": row[3], "claimed_at": row[4]}
        for row in cur.fetchall()
    ]


def claim_username(schema: str, email: str):
    conn = get_conn()
    cur = conn.cursor()

    # Get event config
    try:
        cur.execute(f"SELECT DISTRIBUTION_MODE, EVENT_MODE, DEFAULT_ROLE FROM {DATABASE}.{schema}.EVENT_CONFIG LIMIT 1")
        row = cur.fetchone()
        mode = row[0] if row and row[0] else "sequential"
        event_mode = row[1] if row and row[1] else "static"
        default_role = row[2] if row and len(row) > 2 and row[2] else "PUBLIC"
    except Exception:
        mode = "sequential"
        event_mode = "static"
        default_role = "PUBLIC"

    if event_mode == "dynamic":
        return _claim_dynamic(schema, email, mode, conn, default_role)
    else:
        return _claim_static(schema, email, mode, conn)


def _claim_static(schema: str, email: str, mode: str, conn):
    """Claim from pre-populated usernames (existing behavior)."""
    cur = conn.cursor()

    # Build ORDER BY based on distribution mode
    if mode == "round_robin":
        order_clause = """
            ORDER BY (SELECT COUNT(*) FROM {db}.{schema}.USERNAMES u2 
                      WHERE u2.ACCOUNT_ID = {db}.{schema}.USERNAMES.ACCOUNT_ID 
                      AND u2.CLAIMER_EMAIL IS NULL AND u2.PREVIOUSLY_ASSIGNED_TO IS NULL) DESC, USERNAME ASC
        """.format(db=DATABASE, schema=schema)
    elif mode == "random":
        order_clause = "ORDER BY RANDOM()"
    else:
        order_clause = "ORDER BY ACCOUNT_ID ASC, USERNAME ASC"

    cur.execute(
        f"""SELECT USERNAME, ACCOUNT_ID, ACCOUNT_URL
            FROM {DATABASE}.{schema}.USERNAMES
            WHERE CLAIMER_EMAIL IS NULL AND PREVIOUSLY_ASSIGNED_TO IS NULL
            {order_clause}
            LIMIT 10""",
    )
    candidates = cur.fetchall()
    if not candidates:
        return None
    for candidate in candidates:
        username, account_id, account_url = candidate
        cur.execute(
            f"""UPDATE {DATABASE}.{schema}.USERNAMES
                SET CLAIMER_EMAIL = %s, CLAIMED_AT = CURRENT_TIMESTAMP()
                WHERE USERNAME = %s AND ACCOUNT_ID = %s AND CLAIMER_EMAIL IS NULL AND PREVIOUSLY_ASSIGNED_TO IS NULL""",
            (email, username, account_id),
        )
        if cur.rowcount > 0:
            return {"username": username, "account_id": account_id, "account_url": account_url, "email": email}
    return None


def _claim_dynamic(schema: str, email: str, mode: str, conn, default_role: str = "PUBLIC"):
    """Generate a new USER on-the-fly and claim it."""
    cur = conn.cursor()

    # Pick which account to create the user on based on distribution mode
    if mode == "round_robin":
        # Account with the fewest claimed users
        cur.execute(f"""
            SELECT ACCOUNT_ID, ACCOUNT_URL FROM {DATABASE}.{schema}.USERNAMES
            WHERE USERNAME = '__ACCOUNT_REF__'
            ORDER BY (
                SELECT COUNT(*) FROM {DATABASE}.{schema}.USERNAMES u2
                WHERE u2.ACCOUNT_ID = {DATABASE}.{schema}.USERNAMES.ACCOUNT_ID
                AND u2.USERNAME != '__ACCOUNT_REF__'
            ) ASC
            LIMIT 1
        """)
    elif mode == "random":
        cur.execute(f"""
            SELECT ACCOUNT_ID, ACCOUNT_URL FROM {DATABASE}.{schema}.USERNAMES
            WHERE USERNAME = '__ACCOUNT_REF__'
            ORDER BY RANDOM()
            LIMIT 1
        """)
    else:
        # Sequential: fill one account at a time
        cur.execute(f"""
            SELECT ACCOUNT_ID, ACCOUNT_URL FROM {DATABASE}.{schema}.USERNAMES
            WHERE USERNAME = '__ACCOUNT_REF__'
            ORDER BY ACCOUNT_ID ASC
            LIMIT 1
        """)

    account_row = cur.fetchone()
    if not account_row:
        return None

    account_id, account_url = account_row

    # Determine next username number for this account
    cur.execute(
        f"""SELECT COUNT(*) FROM {DATABASE}.{schema}.USERNAMES
            WHERE ACCOUNT_ID = %s AND USERNAME != '__ACCOUNT_REF__'""",
        (account_id,),
    )
    user_count = cur.fetchone()[0]
    new_username = f"USER{user_count + 1}"

    # Create the USER on the target account
    try:
        acct_conn = get_account_connection(account_url)
        acct_cur = acct_conn.cursor()
        acct_cur.execute(f"""
            CREATE USER IF NOT EXISTS {new_username}
            PASSWORD = '{DEFAULT_PASSWORD}'
            DEFAULT_ROLE = {default_role}
            MUST_CHANGE_PASSWORD = FALSE
        """)
        acct_cur.execute(f"GRANT ROLE {default_role} TO USER {new_username}")
        acct_cur.execute(f"ALTER USER {new_username} SET MINS_TO_BYPASS_MFA = 30")
        acct_conn.close()
    except Exception as e:
        st.toast(f"Failed to create user on account: {e}", icon="⚠️")
        return None

    # Insert and claim the row in one go
    cur.execute(
        f"""INSERT INTO {DATABASE}.{schema}.USERNAMES
            (USERNAME, ACCOUNT_ID, ACCOUNT_URL, CLAIMER_EMAIL, CLAIMED_AT, EVENT_NAME)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP(), %s)""",
        (new_username, account_id, account_url, email, schema),
    )

    return {"username": new_username, "account_id": account_id, "account_url": account_url, "email": email}


def unassign_username(schema: str, username: str, account_id: str, account_url: str, old_email: str):
    """Unassign a username: mark burned, DROP USER on target account."""
    conn = get_conn()
    cur = conn.cursor()

    # Mark as burned with previous email
    cur.execute(
        f"""UPDATE {DATABASE}.{schema}.USERNAMES
            SET CLAIMER_EMAIL = NULL, CLAIMED_AT = NULL, PREVIOUSLY_ASSIGNED_TO = %s
            WHERE USERNAME = %s AND ACCOUNT_ID = %s""",
        (old_email, username, account_id),
    )

    # Get event mode to decide whether to DROP USER
    try:
        cur.execute(f"SELECT EVENT_MODE FROM {DATABASE}.{schema}.EVENT_CONFIG LIMIT 1")
        row = cur.fetchone()
        event_mode = row[0] if row and row[0] else "static"
    except Exception:
        event_mode = "static"

    # DROP USER on the target account (dynamic mode only)
    if event_mode == "dynamic" and account_url:
        try:
            acct_conn = get_account_connection(account_url)
            acct_cur = acct_conn.cursor()
            acct_cur.execute(f"DROP USER IF EXISTS {username}")
            acct_conn.close()
        except Exception:
            pass  # Best effort — user might already be gone

    return cur.rowcount > 0


def render_confirmation(claim, instructions_url: str = None):
    st.success("Here are your credentials:")
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Account**")
        if claim.get("account_url"):
            st.markdown(f"[Open Account ↗]({claim['account_url']})")
        else:
            st.code(claim["account_id"])
        st.markdown("**Username**")
        st.code(claim["username"])
    with col2:
        st.markdown("**Password**")
        st.code(DEFAULT_PASSWORD)
        st.markdown("**Email**")
        st.code(claim["email"])
    st.markdown("---")
    if instructions_url:
        st.link_button("Lab Instructions", instructions_url, use_container_width=True)
    st.info("Save these credentials. You'll need them to log in to the lab environment.")


# =============================================================================
# Admin Panel UI
# =============================================================================


def render_admin():
    """Render the admin panel (password-protected)."""
    st.title(":material/admin_panel_settings: Admin Panel")

    # Password gate
    if not st.session_state["admin_authenticated"]:
        try:
            admin_password = st.secrets["admin"]["password"]
        except Exception:
            admin_password = "admin"

        entered = st.text_input("Admin Password", type="password", placeholder="Enter admin password")
        if st.button("Login", type="primary"):
            if entered == admin_password:
                st.session_state["admin_authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        return

    # Admin content
    tab_create, tab_manage, tab_events = st.tabs(["Create Event", "Manage Usernames", "Event Management"])

    with tab_create:
        st.header("Create New Event")

        event_name = st.text_input(
            "Event Name",
            placeholder="e.g., SI_SUMMIT_2025",
            help="Will be used as the schema name",
        )

        accounts_csv = st.text_area(
            "Account List (CSV)",
            height=200,
            placeholder="Account ID, Status, Assigned To, URL\nSFSEHOL_ABC123, Active, John, https://app.snowflake.com/...",
            help="Same format as si_admin: Account ID, Status, Assigned To, URL",
        )
        if accounts_csv.strip():
            parsed_accounts_preview = parse_account_csv(accounts_csv)
            st.caption(f"{len(parsed_accounts_preview)} account(s) detected")

        username_source = st.radio(
            "Username Source",
            ["Provide List", "Generate Dynamically"],
            horizontal=True,
            captions=[
                "Provide a list of existing usernames",
                "Create USERs on-the-fly when attendees claim (USER1, USER2, ...)",
            ],
        )

        if username_source == "Provide List":
            usernames_input = st.text_area(
                "Usernames (comma or newline separated)",
                height=200,
                placeholder="USER1, USER2, USER3, ..., USER50\nor one per line:\nUSER1\nUSER2\nUSER3",
                help="These usernames will be created for EACH account",
            )
            if usernames_input.strip():
                parsed_usernames_preview = [u.strip().upper() for u in re.split(r'[,\n]+', usernames_input) if u.strip()]
                st.caption(f"{len(parsed_usernames_preview)} username(s) detected")
        else:
            usernames_input = ""
            st.info("Usernames will be generated automatically (USER1, USER2, ...) when attendees claim.")

        distribution_mode = st.radio(
            "Distribution Mode",
            ["sequential", "round_robin", "random"],
            horizontal=True,
            help="How usernames are assigned when attendees claim",
            captions=[
                "Fill one account completely before moving to the next",
                "Spread claims evenly across accounts (picks from the account with the most available)",
                "Assign a random available username from any account",
            ],
        )

        default_role = st.text_input(
            "Default Role (optional)",
            value="PUBLIC",
            help="Role granted to and set as default for created users (dynamic mode). Also used for static mode if users are pre-created.",
        )

        instructions_url = st.text_input(
            "Instructions URL (optional)",
            placeholder="https://docs.google.com/...",
            help="If provided, this link will be included in the credentials email sent to attendees",
        )

        # Overview panel
        st.divider()
        st.subheader("Event Summary")
        preview_accounts = parse_account_csv(accounts_csv) if accounts_csv.strip() else []
        preview_usernames = [u.strip().upper() for u in re.split(r'[,\n]+', usernames_input) if u.strip()] if usernames_input.strip() else []
        preview_schema = sanitize_schema_name(event_name) if event_name.strip() else "—"
        event_mode = "static" if username_source == "Provide List" else "dynamic"

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Accounts", len(preview_accounts))
        if event_mode == "static":
            col2.metric("Usernames", len(preview_usernames))
            col3.metric("Total Rows", len(preview_accounts) * len(preview_usernames))
        else:
            col2.metric("Usernames", "Dynamic")
            col3.metric("Total Rows", "On demand")
        col4.metric("Distribution", distribution_mode)
        st.markdown(f"**Event slug:** `{preview_schema}` &nbsp;&nbsp; **URL:** `?event={preview_schema}` &nbsp;&nbsp; **Mode:** `{event_mode}`")

        if st.button("Create Event", type="primary"):
            if not event_name.strip():
                st.error("Event name is required.")
            elif not accounts_csv.strip():
                st.error("Account list is required.")
            elif event_mode == "static" and not usernames_input.strip():
                st.error("Username list is required for static mode.")
            else:
                schema = sanitize_schema_name(event_name)
                accounts = parse_account_csv(accounts_csv)
                usernames = [u.strip().upper() for u in re.split(r'[,\n]+', usernames_input) if u.strip()] if usernames_input.strip() else []

                if not accounts:
                    st.error("No valid accounts parsed from CSV.")
                elif event_mode == "static" and not usernames:
                    st.error("No valid usernames parsed.")
                else:
                    try:
                        conn = get_conn()
                        cur = conn.cursor()
                        ensure_database_exists()
                        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {DATABASE}.{schema}")
                        cur.execute(f"""
                            CREATE TABLE IF NOT EXISTS {DATABASE}.{schema}.USERNAMES (
                                USERNAME                VARCHAR NOT NULL,
                                ACCOUNT_ID              VARCHAR NOT NULL,
                                ACCOUNT_URL             VARCHAR,
                                CLAIMER_EMAIL           VARCHAR,
                                CLAIMED_AT              TIMESTAMP_NTZ,
                                PREVIOUSLY_ASSIGNED_TO  VARCHAR,
                                EVENT_NAME              VARCHAR
                            )
                        """)
                        cur.execute(f"""
                            CREATE TABLE IF NOT EXISTS {DATABASE}.{schema}.EVENT_CONFIG (
                                EVENT_NAME          VARCHAR,
                                PASSWORD            VARCHAR,
                                DISTRIBUTION_MODE   VARCHAR DEFAULT 'sequential',
                                EVENT_MODE          VARCHAR DEFAULT 'static',
                                DEFAULT_ROLE        VARCHAR DEFAULT 'PUBLIC',
                                INSTRUCTIONS_URL    VARCHAR,
                                CREATED_AT          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
                            )
                        """)
                        cur.execute(
                            f"INSERT INTO {DATABASE}.{schema}.EVENT_CONFIG (EVENT_NAME, PASSWORD, DISTRIBUTION_MODE, EVENT_MODE, DEFAULT_ROLE, INSTRUCTIONS_URL) VALUES (%s, %s, %s, %s, %s, %s)",
                            (schema, DEFAULT_PASSWORD, distribution_mode, event_mode, default_role.strip() or "PUBLIC", instructions_url.strip() or None),
                        )

                        if event_mode == "static":
                            rows_to_insert = []
                            for acct in accounts:
                                for uname in usernames:
                                    rows_to_insert.append((uname, acct["account_id"], acct["url"], schema))
                            cur.executemany(
                                f"INSERT INTO {DATABASE}.{schema}.USERNAMES (USERNAME, ACCOUNT_ID, ACCOUNT_URL, EVENT_NAME) VALUES (%s, %s, %s, %s)",
                                rows_to_insert,
                            )
                            st.success(
                                f"Event **{schema}** created with {len(accounts)} accounts × {len(usernames)} usernames = **{len(rows_to_insert)}** total rows."
                            )
                        else:
                            # Dynamic mode: store account info but no usernames yet
                            # Insert one placeholder row per account to store account_url mapping
                            for acct in accounts:
                                cur.execute(
                                    f"INSERT INTO {DATABASE}.{schema}.USERNAMES (USERNAME, ACCOUNT_ID, ACCOUNT_URL, EVENT_NAME, PREVIOUSLY_ASSIGNED_TO) VALUES (%s, %s, %s, %s, %s)",
                                    ("__ACCOUNT_REF__", acct["account_id"], acct["url"], schema, "__SYSTEM__"),
                                )
                            st.success(
                                f"Dynamic event **{schema}** created with {len(accounts)} accounts. Users will be created on demand."
                            )
                    except Exception as e:
                        st.error(f"Error creating event: {e}")

    with tab_manage:
        st.header("Manage Usernames")

        schemas = get_event_schemas()
        if not schemas:
            st.info("No events found. Create one in the 'Create Event' tab.")
        else:
            selected_event = st.selectbox("Select Event", schemas)

            if selected_event:
                conn = get_conn()
                cur = conn.cursor()

                # Check event mode
                try:
                    cur.execute(f"SELECT EVENT_MODE FROM {DATABASE}.{selected_event}.EVENT_CONFIG LIMIT 1")
                    mgmt_event_mode = cur.fetchone()
                    mgmt_event_mode = mgmt_event_mode[0] if mgmt_event_mode else "static"
                except Exception:
                    mgmt_event_mode = "static"

                cur.execute(f"""
                    SELECT COUNT(*) AS total, COUNT(CLAIMER_EMAIL) AS claimed
                    FROM {DATABASE}.{selected_event}.USERNAMES
                    WHERE USERNAME != '__ACCOUNT_REF__'
                """)
                total, claimed = cur.fetchone()

                if mgmt_event_mode == "dynamic":
                    col1, col2 = st.columns(2)
                    col1.metric("Users Created", total)
                    col2.metric("Active Claims", claimed)
                    st.caption("Dynamic event — no upper limit on attendees")
                else:
                    available = total - claimed
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Total", total)
                    col2.metric("Claimed", claimed)
                    col3.metric("Available", available)
                    st.progress(claimed / total if total > 0 else 0)

                st.subheader("Filters")
                filter_col1, filter_col2 = st.columns(2)
                all_accounts = get_accounts_for_event(selected_event)
                with filter_col1:
                    selected_accounts = st.multiselect("Accounts", all_accounts, default=all_accounts)
                with filter_col2:
                    availability = st.radio("Availability", ["All", "Available", "Claimed", "Burned"], horizontal=True)

                df = get_usernames_df(selected_event, selected_accounts, availability)

                if df.empty:
                    st.info("No usernames match the current filters.")
                else:
                    # Select all / Select none buttons
                    sel_col1, sel_col2, sel_col3 = st.columns([1, 1, 4])
                    with sel_col1:
                        if st.button("Select All", key="select_all"):
                            st.session_state["select_all_flag"] = True
                            st.rerun()
                    with sel_col2:
                        if st.button("Select None", key="select_none"):
                            st.session_state["select_all_flag"] = False
                            st.rerun()

                    # Add selection column
                    df_display = df.copy()
                    default_select = st.session_state.get("select_all_flag", False)
                    df_display.insert(0, "Select", default_select)

                    edited_df = st.data_editor(
                        df_display,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Select": st.column_config.CheckboxColumn("Select", default=False),
                            "ACCOUNT_URL": None,
                        },
                        disabled=["USERNAME", "ACCOUNT_ID", "ACCOUNT_URL", "CLAIMER_EMAIL", "CLAIMED_AT", "PREVIOUSLY_ASSIGNED_TO"],
                        key="username_editor",
                    )

                    selected_rows = edited_df[edited_df["Select"] == True]
                    selected_count = len(selected_rows)

                    st.caption(f"{selected_count} selected")

                    # Actions
                    st.subheader("Actions")

                    # Assign action
                    assign_email = st.text_input("Email to assign", key="assign_email")
                    btn_col1, btn_col2 = st.columns(2)

                    with btn_col1:
                        if st.button(f"Assign Selected ({selected_count})", type="primary", disabled=selected_count != 1):
                            if not assign_email.strip():
                                st.error("Email is required.")
                            else:
                                # Check if any selected rows are already claimed
                                already_claimed = selected_rows[selected_rows["CLAIMER_EMAIL"].notna()]
                                if len(already_claimed) > 0 and not st.session_state.get("confirm_reassign"):
                                    st.session_state["confirm_reassign"] = True
                                    st.rerun()
                                else:
                                    assigned = 0
                                    try:
                                        cur = get_conn().cursor()
                                        for _, row in selected_rows.iterrows():
                                            cur.execute(
                                                f"""UPDATE {DATABASE}.{selected_event}.USERNAMES
                                                    SET CLAIMER_EMAIL = %s, CLAIMED_AT = CURRENT_TIMESTAMP()
                                                    WHERE USERNAME = %s AND ACCOUNT_ID = %s""",
                                                (assign_email.strip(), row["USERNAME"], row["ACCOUNT_ID"]),
                                            )
                                            assigned += cur.rowcount
                                        st.success(f"Assigned {assigned} username(s) to {assign_email.strip()}")
                                        st.session_state["confirm_reassign"] = False
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Error: {e}")

                    if st.session_state.get("confirm_reassign"):
                        already_claimed = selected_rows[selected_rows["CLAIMER_EMAIL"].notna()]
                        names = ", ".join(f"{r['USERNAME']}@{r['ACCOUNT_ID']} (currently: {r['CLAIMER_EMAIL']})" for _, r in already_claimed.iterrows())
                        st.warning(f"The following are already assigned and will be re-assigned: **{names}**")
                        rc1, rc2 = st.columns(2)
                        with rc1:
                            if st.button("Confirm Re-assign", type="primary", key="confirm_reassign_btn"):
                                assigned = 0
                                try:
                                    cur = get_conn().cursor()
                                    for _, row in selected_rows.iterrows():
                                        cur.execute(
                                            f"""UPDATE {DATABASE}.{selected_event}.USERNAMES
                                                SET CLAIMER_EMAIL = %s, CLAIMED_AT = CURRENT_TIMESTAMP()
                                                WHERE USERNAME = %s AND ACCOUNT_ID = %s""",
                                            (assign_email.strip(), row["USERNAME"], row["ACCOUNT_ID"]),
                                        )
                                        assigned += cur.rowcount
                                    st.success(f"Re-assigned {assigned} username(s) to {assign_email.strip()}")
                                    st.session_state["confirm_reassign"] = False
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")
                        with rc2:
                            if st.button("Cancel", key="cancel_reassign_btn"):
                                st.session_state["confirm_reassign"] = False
                                st.rerun()

                    with btn_col2:
                        if st.button(f"Unassign Selected ({selected_count})", type="secondary", disabled=selected_count == 0):
                            st.session_state["confirm_unassign"] = True
                            st.rerun()

                    if st.session_state.get("confirm_unassign"):
                        claimed_in_selection = selected_rows[selected_rows["CLAIMER_EMAIL"].notna()]
                        st.warning(f"Unassign **{len(claimed_in_selection)}** username(s)?")
                        uc1, uc2 = st.columns(2)
                        with uc1:
                            if st.button("Confirm Unassign", type="primary", key="confirm_unassign_btn"):
                                unassigned = 0
                                try:
                                    for _, row in claimed_in_selection.iterrows():
                                        if unassign_username(
                                            selected_event,
                                            row["USERNAME"],
                                            row["ACCOUNT_ID"],
                                            row.get("ACCOUNT_URL", ""),
                                            row["CLAIMER_EMAIL"],
                                        ):
                                            unassigned += 1
                                    st.success(f"Unassigned {unassigned} username(s).")
                                    st.session_state["confirm_unassign"] = False
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")
                        with uc2:
                            if st.button("Cancel", key="cancel_unassign_btn"):
                                st.session_state["confirm_unassign"] = False
                                st.rerun()

    with tab_events:
        st.header("Event Management")

        schemas = get_event_schemas()
        if not schemas:
            st.info("No events to manage.")
        else:
            evt = st.selectbox("Select Event", schemas, key="evt_mgmt_select")

            if evt:
                st.subheader("Rename Event")
                new_name = st.text_input("New event name", value=evt, key="rename_input")
                update_slug = st.checkbox("Also update the URL slug (schema name)", value=True,
                                         help="Uncheck if QR codes have already been generated for the current URL")

                if st.button("Rename", key="rename_btn"):
                    new_sanitized = sanitize_schema_name(new_name)
                    if update_slug:
                        if new_sanitized == evt:
                            st.info("Name unchanged.")
                        else:
                            try:
                                cur = get_conn().cursor()
                                cur.execute(f"ALTER SCHEMA {DATABASE}.{evt} RENAME TO {DATABASE}.{new_sanitized}")
                                cur.execute(
                                    f"UPDATE {DATABASE}.{new_sanitized}.EVENT_CONFIG SET EVENT_NAME = %s",
                                    (new_sanitized,),
                                )
                                cur.execute(
                                    f"UPDATE {DATABASE}.{new_sanitized}.USERNAMES SET EVENT_NAME = %s",
                                    (new_sanitized,),
                                )
                                st.success(f"Renamed **{evt}** → **{new_sanitized}** (URL slug updated)")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")
                    else:
                        # Only update display name in EVENT_CONFIG, keep schema as-is
                        try:
                            cur = get_conn().cursor()
                            cur.execute(
                                f"UPDATE {DATABASE}.{evt}.EVENT_CONFIG SET EVENT_NAME = %s",
                                (new_sanitized,),
                            )
                            st.success(f"Display name updated to **{new_sanitized}**. URL slug remains `?event={evt}`")
                        except Exception as e:
                            st.error(f"Error: {e}")

                st.divider()
                st.subheader("Default Role")

                # Fetch current default role
                try:
                    cur = get_conn().cursor()
                    cur.execute(f"SELECT DEFAULT_ROLE FROM {DATABASE}.{evt}.EVENT_CONFIG LIMIT 1")
                    role_row = cur.fetchone()
                    current_role = role_row[0] if role_row and role_row[0] else "PUBLIC"
                except Exception:
                    current_role = "PUBLIC"

                new_role = st.text_input(
                    "Default Role",
                    value=current_role,
                    key="default_role_input",
                    help="Role granted to users created in dynamic mode",
                )

                if st.button("Update Default Role", key="update_role_btn"):
                    try:
                        cur = get_conn().cursor()
                        cur.execute(
                            f"UPDATE {DATABASE}.{evt}.EVENT_CONFIG SET DEFAULT_ROLE = %s",
                            (new_role.strip() or "PUBLIC",),
                        )
                        st.success("Default role updated.")
                    except Exception as e:
                        st.error(f"Error: {e}")

                st.divider()
                st.subheader("Instructions URL")

                # Fetch current instructions URL
                try:
                    cur = get_conn().cursor()
                    cur.execute(f"SELECT INSTRUCTIONS_URL FROM {DATABASE}.{evt}.EVENT_CONFIG LIMIT 1")
                    iurl_row = cur.fetchone()
                    current_iurl = iurl_row[0] if iurl_row and iurl_row[0] else ""
                except Exception:
                    current_iurl = ""

                new_iurl = st.text_input(
                    "Instructions URL",
                    value=current_iurl,
                    placeholder="https://docs.google.com/...",
                    key="instructions_url_input",
                    help="Link included in the credentials email sent to attendees",
                )

                if st.button("Update Instructions URL", key="update_iurl_btn"):
                    try:
                        cur = get_conn().cursor()
                        cur.execute(
                            f"UPDATE {DATABASE}.{evt}.EVENT_CONFIG SET INSTRUCTIONS_URL = %s",
                            (new_iurl.strip() or None,),
                        )
                        st.success("Instructions URL updated.")
                    except Exception as e:
                        st.error(f"Error: {e}")

                st.divider()
                st.subheader("Delete Event")
                st.warning(f"This will permanently delete all data for event **{evt}**.")

                confirm_delete = st.text_input(
                    f"Type `{evt}` to confirm deletion",
                    key="delete_confirm_input",
                )
                if st.button("Delete Event", type="secondary", key="delete_btn"):
                    if confirm_delete.strip().upper() == evt:
                        try:
                            cur = get_conn().cursor()
                            cur.execute(f"DROP SCHEMA IF EXISTS {DATABASE}.{evt} CASCADE")
                            st.success(f"Event **{evt}** deleted.")
                            del st.session_state["delete_confirm_input"]
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
                    else:
                        st.error("Confirmation text doesn't match. Type the event name exactly.")


# =============================================================================
# Attendee UI
# =============================================================================


def render_attendee(selected_event: str):
    """Render the attendee claim flow."""
    st.title(":material/person_add: Claim Your Account")
    st.caption("Enter your email to receive your lab credentials.")

    # If viewing a specific claim, show confirmation
    if st.session_state["claimed"]:
        # Fetch instructions URL from event config
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(f"SELECT INSTRUCTIONS_URL FROM {DATABASE}.{selected_event}.EVENT_CONFIG LIMIT 1")
            iurl_row = cur.fetchone()
            event_instructions_url = iurl_row[0] if iurl_row and iurl_row[0] else None
        except Exception:
            event_instructions_url = None

        # Send email on first render after claim (not on page revisits)
        if st.session_state.get("send_email"):
            send_claim_email(st.session_state["claimed"], instructions_url=event_instructions_url)
            st.session_state["send_email"] = False

        render_confirmation(st.session_state["claimed"], instructions_url=event_instructions_url)
    else:
        # Check if any usernames are available
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM {DATABASE}.{selected_event}.USERNAMES WHERE CLAIMER_EMAIL IS NULL AND PREVIOUSLY_ASSIGNED_TO IS NULL AND USERNAME != '__ACCOUNT_REF__'"
        )
        available_count = cur.fetchone()[0]

        # For dynamic events, there are always "available" slots
        try:
            cur.execute(f"SELECT EVENT_MODE FROM {DATABASE}.{selected_event}.EVENT_CONFIG LIMIT 1")
            evt_mode_row = cur.fetchone()
            if evt_mode_row and evt_mode_row[0] == "dynamic":
                available_count = 1  # Always available in dynamic mode
        except Exception:
            pass

        if available_count == 0:
            st.warning("All usernames for this event have been claimed. Please contact your instructor if you need assistance.")
        else:
            email = st.text_input(
                "Email Address",
                placeholder="you@company.com",
                help="Enter the email you registered with",
            )
            if st.button("Claim Username", type="primary", use_container_width=True):
                if not email or not email.strip():
                    st.error("Please enter your email address.")
                elif "@" not in email or "." not in email.split("@")[-1]:
                    st.error("Please enter a valid email address.")
                else:
                    email_clean = email.strip().lower()
                    with st.spinner("Finding your account..."):
                        existing = check_existing_claim(selected_event, email_clean)
                        if existing:
                            st.session_state["claimed"] = existing
                            st.rerun()
                        else:
                            result = claim_username(selected_event, email_clean)
                            if result:
                                st.session_state["claimed"] = result
                                st.session_state["send_email"] = True
                                st.rerun()
                            else:
                                st.error("All accounts are currently full. Please contact the event organizer.")

            # Show lab instructions link if configured for this event
            try:
                cur.execute(f"SELECT INSTRUCTIONS_URL FROM {DATABASE}.{selected_event}.EVENT_CONFIG LIMIT 1")
                instructions_row = cur.fetchone()
                if instructions_row and instructions_row[0]:
                    st.link_button(":material/menu_book: Lab Instructions", instructions_row[0], use_container_width=True)
            except Exception:
                pass


# =============================================================================
# Router
# =============================================================================

path = st.query_params.get("event", "").strip().upper()

if path == "ADMIN":
    render_admin()
elif not path:
    st.title(":material/person_add: Claim Your Account")
    st.info("Please ask your instructor for the event URL.")
else:
    schemas = get_event_schemas()
    if path not in schemas:
        st.title(":material/person_add: Claim Your Account")
        st.error(f"Event **{path}** not found. Please check the URL provided by your organizer.")
    else:
        render_attendee(path)
