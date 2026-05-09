"""
User Distribution - Attendee App
Allows hands-on lab participants to claim a username from an available account.
"""

import streamlit as st
from config import get_connection, DATABASE, DEFAULT_PASSWORD

# =============================================================================
# Page configuration
# =============================================================================

st.set_page_config(
    page_title="Claim Your Account",
    page_icon=":material/person_add:",
    layout="centered",
)

# =============================================================================
# Session state
# =============================================================================

st.session_state.setdefault("conn", None)
st.session_state.setdefault("claimed", None)


def get_conn():
    if st.session_state["conn"] is None or st.session_state["conn"].is_closed():
        st.session_state["conn"] = get_connection()
    return st.session_state["conn"]


def get_event_schemas():
    """List available event schemas."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(f"SHOW SCHEMAS IN DATABASE {DATABASE}")
    except Exception:
        return []
    rows = cur.fetchall()
    exclude = {"INFORMATION_SCHEMA", "PUBLIC"}
    return [r[1] for r in rows if r[1] not in exclude]


def check_existing_claim(schema: str, email: str):
    """Check if this email already has a claimed username."""
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
        return {
            "username": row[0],
            "account_id": row[1],
            "account_url": row[2],
            "email": row[3],
            "claimed_at": row[4],
        }
    return None


def get_claimed_usernames(schema: str):
    """Get all claimed usernames for the event."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""SELECT USERNAME, ACCOUNT_ID, ACCOUNT_URL, CLAIMER_EMAIL, CLAIMED_AT
            FROM {DATABASE}.{schema}.USERNAMES
            WHERE CLAIMER_EMAIL IS NOT NULL
            ORDER BY CLAIMED_AT DESC"""
    )
    return [
        {
            "username": row[0],
            "account_id": row[1],
            "account_url": row[2],
            "email": row[3],
            "claimed_at": row[4],
        }
        for row in cur.fetchall()
    ]


def claim_username(schema: str, email: str):
    """
    Attempt to claim the next available username (sequential fill by account).
    Returns the claimed row dict, or None if no usernames available.
    """
    conn = get_conn()
    cur = conn.cursor()

    # Find next available username (sequential fill: first by account, then by username)
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

    # Try to claim each candidate (handles race conditions)
    for candidate in candidates:
        username, account_id, account_url = candidate
        cur.execute(
            f"""UPDATE {DATABASE}.{schema}.USERNAMES
                SET CLAIMER_EMAIL = %s, CLAIMED_AT = CURRENT_TIMESTAMP()
                WHERE USERNAME = %s AND ACCOUNT_ID = %s AND CLAIMER_EMAIL IS NULL""",
            (email, username, account_id),
        )
        if cur.rowcount > 0:
            return {
                "username": username,
                "account_id": account_id,
                "account_url": account_url,
                "email": email,
            }

    # All candidates were grabbed by someone else
    return None


def render_confirmation(claim):
    """Render the confirmation/credentials view for a claim."""
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
# UI
# =============================================================================

st.title(":material/person_add: Claim Your Account")
st.caption("Enter your email to receive your lab credentials.")

# Determine event from URL query param (e.g., ?event=TEST1)
path = st.query_params.get("event", "").strip().upper()
schemas = get_event_schemas()

if not path:
    st.info("Please ask your instructor for the event URL.")
    st.stop()

selected_event = path
if selected_event not in schemas:
    st.error(f"Event **{selected_event}** not found. Please check the URL provided by your organizer.")
    st.stop()

# If viewing a specific claim, show confirmation
if st.session_state["claimed"]:
    render_confirmation(st.session_state["claimed"])

    if st.button("Back", use_container_width=True):
        st.session_state["claimed"] = None
        st.rerun()

else:
    # Email input and claim form
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
                # Check for existing claim — bring them to confirmation
                existing = check_existing_claim(selected_event, email_clean)
                if existing:
                    st.session_state["claimed"] = existing
                    st.rerun()
                else:
                    # Claim a new username
                    result = claim_username(selected_event, email_clean)
                    if result:
                        st.session_state["claimed"] = result
                        st.rerun()
                    else:
                        st.error(
                            "All accounts are currently full. "
                            "Please contact the event organizer for assistance."
                        )

# Always show the claimed usernames list
st.divider()
st.subheader("Claimed Accounts")
st.caption("Click an email to view credentials.")

claimed_list = get_claimed_usernames(selected_event)
if not claimed_list:
    st.info("No accounts have been claimed yet.")
else:
    for claim in claimed_list:
        label = f"{claim['email']}  —  {claim['username']} @ {claim['account_id']}"
        if st.button(
            label,
            key=f"lookup_{claim['email']}_{claim['account_id']}_{claim['username']}",
            use_container_width=True,
        ):
            st.session_state["claimed"] = claim
            st.rerun()
