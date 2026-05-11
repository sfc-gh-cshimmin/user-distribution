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

    # Get distribution mode from event config
    try:
        cur.execute(f"SELECT DISTRIBUTION_MODE FROM {DATABASE}.{schema}.EVENT_CONFIG LIMIT 1")
        row = cur.fetchone()
        mode = row[0] if row and row[0] else "sequential"
    except Exception:
        mode = "sequential"

    # Build ORDER BY based on distribution mode
    if mode == "round_robin":
        # Pick from the account with the most available usernames
        order_clause = """
            ORDER BY (SELECT COUNT(*) FROM {db}.{schema}.USERNAMES u2 
                      WHERE u2.ACCOUNT_ID = {db}.{schema}.USERNAMES.ACCOUNT_ID 
                      AND u2.CLAIMER_EMAIL IS NULL) DESC, USERNAME ASC
        """.format(db=DATABASE, schema=schema)
    elif mode == "random":
        order_clause = "ORDER BY RANDOM()"
    else:
        # sequential (default): fill one account before moving to the next
        order_clause = "ORDER BY ACCOUNT_ID ASC, USERNAME ASC"

    cur.execute(
        f"""SELECT USERNAME, ACCOUNT_ID, ACCOUNT_URL
            FROM {DATABASE}.{schema}.USERNAMES
            WHERE CLAIMER_EMAIL IS NULL
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

        usernames_input = st.text_area(
            "Usernames (comma or newline separated)",
            height=200,
            placeholder="USER1, USER2, USER3, ..., USER50\nor one per line:\nUSER1\nUSER2\nUSER3",
            help="These usernames will be created for EACH account",
        )
        if usernames_input.strip():
            parsed_usernames_preview = [u.strip().upper() for u in re.split(r'[,\n]+', usernames_input) if u.strip()]
            st.caption(f"{len(parsed_usernames_preview)} username(s) detected")

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

        # Overview panel
        st.divider()
        st.subheader("Event Summary")
        preview_accounts = parse_account_csv(accounts_csv) if accounts_csv.strip() else []
        preview_usernames = [u.strip().upper() for u in re.split(r'[,\n]+', usernames_input) if u.strip()] if usernames_input.strip() else []
        preview_schema = sanitize_schema_name(event_name) if event_name.strip() else "—"
        total_rows = len(preview_accounts) * len(preview_usernames)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Accounts", len(preview_accounts))
        col2.metric("Usernames", len(preview_usernames))
        col3.metric("Total Rows", total_rows)
        col4.metric("Distribution", distribution_mode)
        st.markdown(f"**Event slug:** `{preview_schema}` &nbsp;&nbsp; **URL:** `?event={preview_schema}`")

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
                usernames = [u.strip().upper() for u in re.split(r'[,\n]+', usernames_input) if u.strip()]

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
                                EVENT_NAME          VARCHAR,
                                PASSWORD            VARCHAR,
                                DISTRIBUTION_MODE   VARCHAR DEFAULT 'sequential',
                                CREATED_AT          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
                            )
                        """)
                        cur.execute(
                            f"INSERT INTO {DATABASE}.{schema}.EVENT_CONFIG (EVENT_NAME, PASSWORD, DISTRIBUTION_MODE) VALUES (%s, %s, %s)",
                            (schema, DEFAULT_PASSWORD, distribution_mode),
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
                        disabled=["USERNAME", "ACCOUNT_ID", "ACCOUNT_URL", "CLAIMER_EMAIL", "CLAIMED_AT"],
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
                                    cur = get_conn().cursor()
                                    for _, row in claimed_in_selection.iterrows():
                                        cur.execute(
                                            f"""UPDATE {DATABASE}.{selected_event}.USERNAMES
                                                SET CLAIMER_EMAIL = NULL, CLAIMED_AT = NULL
                                                WHERE USERNAME = %s AND ACCOUNT_ID = %s""",
                                            (row["USERNAME"], row["ACCOUNT_ID"]),
                                        )
                                        unassigned += cur.rowcount
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
        render_confirmation(st.session_state["claimed"])
        if st.button("Back", use_container_width=True):
            st.session_state["claimed"] = None
            st.rerun()
    else:
        # Check if any usernames are available
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM {DATABASE}.{selected_event}.USERNAMES WHERE CLAIMER_EMAIL IS NULL"
        )
        available_count = cur.fetchone()[0]

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
