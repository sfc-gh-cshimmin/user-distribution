"""
User Distribution - Admin App
Manage events, accounts, and username assignments.
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
    page_title="User Distribution - Admin",
    page_icon=":material/admin_panel_settings:",
    layout="wide",
)

# =============================================================================
# Session state
# =============================================================================

st.session_state.setdefault("conn", None)
st.session_state.setdefault("authenticated", False)

# =============================================================================
# Password gate
# =============================================================================


def check_admin_password():
    """Require admin password before showing app content."""
    if st.session_state["authenticated"]:
        return True

    try:
        admin_password = st.secrets["admin"]["password"]
    except Exception:
        admin_password = "admin"  # fallback for local dev

    entered = st.text_input("Admin Password", type="password", placeholder="Enter admin password")
    if st.button("Login", type="primary"):
        if entered == admin_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


if not check_admin_password():
    st.stop()


def get_conn():
    if st.session_state["conn"] is None or st.session_state["conn"].is_closed():
        st.session_state["conn"] = get_connection()
    return st.session_state["conn"]


# =============================================================================
# Helper functions
# =============================================================================


def sanitize_schema_name(name: str) -> str:
    """Sanitize event name into a valid Snowflake schema identifier."""
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", name.strip()).upper()
    if not sanitized or sanitized[0].isdigit():
        sanitized = "EVT_" + sanitized
    return sanitized


def parse_account_csv(csv_text: str) -> List[Dict]:
    """
    Parse CSV text containing Snowflake account IDs.
    Expected format: Account ID, Status, Assigned To, URL
    """
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


def ensure_database_exists():
    """Create the database if it doesn't exist."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")


def get_event_schemas() -> List[str]:
    """List all event schemas in the database (excludes INFORMATION_SCHEMA, PUBLIC)."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SHOW SCHEMAS IN DATABASE {DATABASE}")
    except Exception:
        # Database might not exist yet — create it and retry
        ensure_database_exists()
        cur.execute(f"SHOW SCHEMAS IN DATABASE {DATABASE}")
    rows = cur.fetchall()
    exclude = {"INFORMATION_SCHEMA", "PUBLIC"}
    return [r[1] for r in rows if r[1] not in exclude]


def get_accounts_for_event(schema: str) -> List[str]:
    """Get distinct account IDs for an event."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT DISTINCT ACCOUNT_ID FROM {DATABASE}.{schema}.USERNAMES ORDER BY ACCOUNT_ID")
    return [r[0] for r in cur.fetchall()]


def get_usernames_df(schema: str, accounts: List[str], availability: str) -> pd.DataFrame:
    """Query usernames with filters applied."""
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
# UI: Event Creation
# =============================================================================

st.title(":material/admin_panel_settings: User Distribution Admin")

tab_create, tab_manage = st.tabs(["Create Event", "Manage Usernames"])

with tab_create:
    st.header("Create New Event")

    event_name = st.text_input(
        "Event Name",
        placeholder="e.g., SI_SUMMIT_2025",
        help="Will be used as the schema name (sanitized to uppercase alphanumeric + underscores)",
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

                    # Create schema
                    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {DATABASE}.{schema}")

                    # Create usernames table
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

                    # Create event config table
                    cur.execute(f"""
                        CREATE TABLE IF NOT EXISTS {DATABASE}.{schema}.EVENT_CONFIG (
                            EVENT_NAME  VARCHAR,
                            PASSWORD    VARCHAR,
                            CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
                        )
                    """)

                    # Insert event config
                    cur.execute(
                        f"INSERT INTO {DATABASE}.{schema}.EVENT_CONFIG (EVENT_NAME, PASSWORD) VALUES (%s, %s)",
                        (schema, DEFAULT_PASSWORD),
                    )

                    # Insert all username × account combinations
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

# =============================================================================
# UI: Manage Usernames
# =============================================================================

with tab_manage:
    st.header("Manage Usernames")

    schemas = get_event_schemas()
    if not schemas:
        st.info("No events found. Create one in the 'Create Event' tab.")
    else:
        selected_event = st.selectbox("Select Event", schemas)

        if selected_event:
            # Stats bar
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CLAIMER_EMAIL) AS claimed
                FROM {DATABASE}.{selected_event}.USERNAMES
            """)
            total, claimed = cur.fetchone()
            available = total - claimed

            col1, col2, col3 = st.columns(3)
            col1.metric("Total", total)
            col2.metric("Claimed", claimed)
            col3.metric("Available", available)

            st.progress(claimed / total if total > 0 else 0)

            # Filters
            st.subheader("Filters")
            filter_col1, filter_col2 = st.columns(2)

            all_accounts = get_accounts_for_event(selected_event)
            with filter_col1:
                selected_accounts = st.multiselect(
                    "Accounts", all_accounts, default=all_accounts
                )
            with filter_col2:
                availability = st.radio(
                    "Availability", ["All", "Available", "Claimed"], horizontal=True
                )

            # Data display
            df = get_usernames_df(selected_event, selected_accounts, availability)

            if df.empty:
                st.info("No usernames match the current filters.")
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)

                # Actions
                st.subheader("Actions")

                action_col1, action_col2 = st.columns(2)

                with action_col1:
                    st.markdown("**Assign Username**")
                    available_df = df[df["CLAIMER_EMAIL"].isna()]
                    if available_df.empty:
                        st.caption("No available usernames to assign.")
                    else:
                        assign_options = [
                            f"{row['USERNAME']} @ {row['ACCOUNT_ID']}"
                            for _, row in available_df.iterrows()
                        ]
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
                                        st.warning("Username was already claimed. Refresh and try again.")
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
