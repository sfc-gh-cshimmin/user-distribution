"""
User Distribution App
- Attendee claim flow: ?event=EVENT_NAME
- Admin panel: ?event=admin (password protected)
"""

import re
import pandas as pd
import streamlit as st
from typing import List, Dict
from config import get_connection, DATABASE, DEFAULT_PASSWORD

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
    conn = get_conn()
    cur = conn.cursor()
    query = f"SELECT USERNAME, ACCOUNT_ID, ACCOUNT_URL, CLAIMER_EMAIL, CLAIMED_AT FROM {DATABASE}.{schema}.USERNAMES WHERE 1=1"
    params = []
    if accounts:
        placeholders = ", ".join(["%s"] * len(accounts))
        query += f" AND ACCOUNT_ID IN ({placeholders})"
        params.extend(accounts)
    if availability == "Available":
        query += " AND CLAIMER_EMAIL IS NULL"
    elif availability == "Claimed":
        query += " AND CLAIMER_EMAIL IS NOT NULL"
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
    cur.execute(
        f"""SELECT USERNAME, ACCOUNT_ID, ACCOUNT_URL
            FROM {DATABASE}.{schema}.USERNAMES
            WHERE CLAIMER_EMAIL IS NULL
            ORDER BY ACCOUNT_ID ASC, USERNAME ASC
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
                WHERE USERNAME = %s AND ACCOUNT_ID = %s AND CLAIMER_EMAIL IS NULL""",
            (email, username, account_id),
        )
        if cur.rowcount > 0:
            return {"username": username, "account_id": account_id, "account_url": account_url, "email": email}
    return None


def render_confirmation(claim):
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
    st.set_page_config  # already set, just change layout dynamically isn't possible
    tab_create, tab_manage = st.tabs(["Create Event", "Manage Usernames"])

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
        usernames_input = st.text_area(
            "Usernames (comma-separated)",
            placeholder="USER1, USER2, USER3, ..., USER50",
            help="These usernames will be created for EACH account",
        )

        if st.button("Create Event", type="primary"):
            if not event_name.strip():
                st.error("Event name is required.")
            elif not accounts_csv.strip():
                st.error("Account list is required.")
            elif not usernames_input.strip():
                st.error("Username list is required.")
            else:
                schema = sanitize_schema_name(event_name)
                accounts = parse_account_csv(accounts_csv)
                usernames = [u.strip().upper() for u in usernames_input.split(",") if u.strip()]

                if not accounts:
                    st.error("No valid accounts parsed from CSV.")
                elif not usernames:
                    st.error("No valid usernames parsed.")
                else:
                    try:
                        conn = get_conn()
                        cur = conn.cursor()
                        ensure_database_exists()
                        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {DATABASE}.{schema}")
                        cur.execute(f"""
                            CREATE TABLE IF NOT EXISTS {DATABASE}.{schema}.USERNAMES (
                                USERNAME        VARCHAR NOT NULL,
                                ACCOUNT_ID      VARCHAR NOT NULL,
                                ACCOUNT_URL     VARCHAR,
                                CLAIMER_EMAIL   VARCHAR,
                                CLAIMED_AT      TIMESTAMP_NTZ,
                                EVENT_NAME      VARCHAR
                            )
                        """)
                        cur.execute(f"""
                            CREATE TABLE IF NOT EXISTS {DATABASE}.{schema}.EVENT_CONFIG (
                                EVENT_NAME  VARCHAR,
                                PASSWORD    VARCHAR,
                                CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
                            )
                        """)
                        cur.execute(
                            f"INSERT INTO {DATABASE}.{schema}.EVENT_CONFIG (EVENT_NAME, PASSWORD) VALUES (%s, %s)",
                            (schema, DEFAULT_PASSWORD),
                        )
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
                cur.execute(f"""
                    SELECT COUNT(*) AS total, COUNT(CLAIMER_EMAIL) AS claimed
                    FROM {DATABASE}.{selected_event}.USERNAMES
                """)
                total, claimed = cur.fetchone()
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
                    availability = st.radio("Availability", ["All", "Available", "Claimed"], horizontal=True)

                df = get_usernames_df(selected_event, selected_accounts, availability)

                if df.empty:
                    st.info("No usernames match the current filters.")
                else:
                    st.dataframe(df, use_container_width=True, hide_index=True)

                    st.subheader("Actions")
                    action_col1, action_col2 = st.columns(2)

                    with action_col1:
                        st.markdown("**Assign Username**")
                        available_df = df[df["CLAIMER_EMAIL"].isna()]
                        if available_df.empty:
                            st.caption("No available usernames to assign.")
                        else:
                            assign_options = [f"{row['USERNAME']} @ {row['ACCOUNT_ID']}" for _, row in available_df.iterrows()]
                            assign_selection = st.selectbox("Select username to assign", assign_options, key="assign_select")
                            assign_email = st.text_input("Email to assign", key="assign_email")
                            if st.button("Assign", type="primary", key="assign_btn"):
                                if not assign_email.strip():
                                    st.error("Email is required.")
                                else:
                                    idx = assign_options.index(assign_selection)
                                    row = available_df.iloc[idx]
                                    try:
                                        cur = get_conn().cursor()
                                        cur.execute(
                                            f"""UPDATE {DATABASE}.{selected_event}.USERNAMES
                                                SET CLAIMER_EMAIL = %s, CLAIMED_AT = CURRENT_TIMESTAMP()
                                                WHERE USERNAME = %s AND ACCOUNT_ID = %s AND CLAIMER_EMAIL IS NULL""",
                                            (assign_email.strip(), row["USERNAME"], row["ACCOUNT_ID"]),
                                        )
                                        if cur.rowcount > 0:
                                            st.success(f"Assigned {row['USERNAME']} to {assign_email.strip()}")
                                            st.rerun()
                                        else:
                                            st.warning("Username was already claimed.")
                                    except Exception as e:
                                        st.error(f"Error: {e}")

                    with action_col2:
                        st.markdown("**Unassign Username**")
                        claimed_df = df[df["CLAIMER_EMAIL"].notna()]
                        if claimed_df.empty:
                            st.caption("No claimed usernames to unassign.")
                        else:
                            unassign_options = [
                                f"{row['USERNAME']} @ {row['ACCOUNT_ID']} ({row['CLAIMER_EMAIL']})"
                                for _, row in claimed_df.iterrows()
                            ]
                            unassign_selection = st.selectbox("Select username to unassign", unassign_options, key="unassign_select")
                            if st.button("Unassign", type="secondary", key="unassign_btn"):
                                idx = unassign_options.index(unassign_selection)
                                row = claimed_df.iloc[idx]
                                try:
                                    cur = get_conn().cursor()
                                    cur.execute(
                                        f"""UPDATE {DATABASE}.{selected_event}.USERNAMES
                                            SET CLAIMER_EMAIL = NULL, CLAIMED_AT = NULL
                                            WHERE USERNAME = %s AND ACCOUNT_ID = %s""",
                                        (row["USERNAME"], row["ACCOUNT_ID"]),
                                    )
                                    st.success(f"Unassigned {row['USERNAME']}")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")

                    # Bulk unassign
                    st.divider()
                    if st.button("Unassign All (filtered)", type="secondary"):
                        st.session_state["confirm_bulk_unassign"] = True

                    if st.session_state.get("confirm_bulk_unassign"):
                        claimed_df = df[df["CLAIMER_EMAIL"].notna()]
                        st.warning(f"This will unassign **{len(claimed_df)}** usernames. Are you sure?")
                        confirm_col1, confirm_col2 = st.columns(2)
                        with confirm_col1:
                            if st.button("Yes, unassign all", type="primary", key="confirm_yes"):
                                try:
                                    cur = get_conn().cursor()
                                    for _, row in claimed_df.iterrows():
                                        cur.execute(
                                            f"""UPDATE {DATABASE}.{selected_event}.USERNAMES
                                                SET CLAIMER_EMAIL = NULL, CLAIMED_AT = NULL
                                                WHERE USERNAME = %s AND ACCOUNT_ID = %s""",
                                            (row["USERNAME"], row["ACCOUNT_ID"]),
                                        )
                                    st.success(f"Unassigned {len(claimed_df)} usernames.")
                                    st.session_state["confirm_bulk_unassign"] = False
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")
                        with confirm_col2:
                            if st.button("Cancel", key="confirm_no"):
                                st.session_state["confirm_bulk_unassign"] = False
                                st.rerun()


# =============================================================================
# Attendee UI
# =============================================================================


def render_attendee(selected_event: str):
    """Render the attendee claim flow."""
    st.title(":material/person_add: Claim Your Account")
    st.caption("Enter your email to receive your lab credentials.")

    # If viewing a specific claim, show confirmation
    if st.session_state["claimed"]:
        render_confirmation(st.session_state["claimed"])
        if st.button("Back", use_container_width=True):
            st.session_state["claimed"] = None
            st.rerun()
    else:
        email = st.text_input(
            "Email Address",
            placeholder="you@company.com",
            help="Enter the email you registered with",
        )
        if st.button("Claim Account", type="primary", use_container_width=True):
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
                            st.rerun()
                        else:
                            st.error("All accounts are currently full. Please contact the event organizer.")

    # Claimed accounts list
    st.divider()
    st.subheader("Claimed Accounts")
    st.caption("Click an email to view credentials.")

    claimed_list = get_claimed_usernames(selected_event)
    if not claimed_list:
        st.info("No accounts have been claimed yet.")
    else:
        for claim in claimed_list:
            label = f"{claim['email']}  —  {claim['username']} @ {claim['account_id']}"
            if st.button(label, key=f"lookup_{claim['email']}_{claim['account_id']}_{claim['username']}", use_container_width=True):
                st.session_state["claimed"] = claim
                st.rerun()


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
