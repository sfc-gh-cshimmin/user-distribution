"""
Shared configuration for the User Distribution app.
Auth priority:
  1. Streamlit Cloud secrets (st.secrets["snowflake"])
  2. SPCS OAuth token (auto-mounted at /snowflake/session/token in SPCS)
  3. Key-pair auth (SNOWFLAKE_PRIVATE_KEY_PATH env var)
  4. Password auth (SNOWFLAKE_PASSWORD env var)
  5. Named connection from ~/.snowflake/connections.toml (local dev)
"""

import os
import snowflake.connector

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATABASE = "USER_DISTRIBUTION"
DEFAULT_PASSWORD = "sn0wf@ll"
CONNECTION_NAME = os.environ.get("SNOWFLAKE_CONNECTION_NAME", "product_demos")
SPCS_TOKEN_PATH = "/snowflake/session/token"

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------


def _load_private_key(path: str):
    """Load a PEM private key file for key-pair auth."""
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    return private_key


def _get_streamlit_secrets():
    """Try to read Snowflake creds from Streamlit Cloud secrets."""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "snowflake" in st.secrets:
            return dict(st.secrets["snowflake"])
    except Exception:
        pass
    return None


def get_connection() -> snowflake.connector.SnowflakeConnection:
    """
    Return a Snowflake connection. Priority:
    1. Streamlit Cloud secrets (st.secrets["snowflake"])
    2. SPCS OAuth token (running inside SPCS)
    3. Key-pair auth (SNOWFLAKE_PRIVATE_KEY_PATH set)
    4. Password auth (SNOWFLAKE_PASSWORD set)
    5. Named connection from ~/.snowflake/connections.toml (local dev)
    """
    # Streamlit Cloud mode: secrets configured in dashboard
    secrets = _get_streamlit_secrets()
    if secrets:
        return snowflake.connector.connect(
            account=secrets["account"],
            user=secrets["user"],
            password=secrets["password"],
            role=secrets.get("role", "ACCOUNTADMIN"),
            warehouse=secrets.get("warehouse", "COMPUTE_WH"),
            database=DATABASE,
            client_session_keep_alive=True,
        )

    # SPCS mode: token file exists at /snowflake/session/token
    if os.path.isfile(SPCS_TOKEN_PATH):
        with open(SPCS_TOKEN_PATH) as f:
            token = f.read()
        return snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            host=os.environ["SNOWFLAKE_HOST"],
            authenticator="oauth",
            token=token,
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
            database=DATABASE,
            role=os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
            client_session_keep_alive=True,
        )

    # Container/env-var mode (Docker outside SPCS)
    if os.environ.get("SNOWFLAKE_ACCOUNT"):
        connect_kwargs = dict(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ.get("SNOWFLAKE_USER", ""),
            role=os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
            database=DATABASE,
            client_session_keep_alive=True,
        )

        key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
        if key_path:
            connect_kwargs["private_key"] = _load_private_key(key_path)
        else:
            connect_kwargs["password"] = os.environ.get("SNOWFLAKE_PASSWORD", "")

        return snowflake.connector.connect(**connect_kwargs)

    # Local mode: use named connection from ~/.snowflake/connections.toml
    return snowflake.connector.connect(
        connection_name=CONNECTION_NAME,
        database=DATABASE,
        client_session_keep_alive=True,
    )
