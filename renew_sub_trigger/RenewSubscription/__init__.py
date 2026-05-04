import os
import logging
import requests
from datetime import datetime, timedelta, timezone
import azure.functions as func

TENANT_ID       = os.environ["TENANT_ID"]
CLIENT_ID       = os.environ["CLIENT_ID"]
CLIENT_SECRET   = os.environ["CLIENT_SECRET"]
SUBSCRIPTION_ID = os.environ["SUBSCRIPTION_ID"]

# How many days to extend the subscription on each renewal
RENEWAL_DAYS = 3

# Renew when this many days or fewer remain
RENEWAL_THRESHOLD_DAYS = 1


def get_token() -> str:
    """Obtain a Bearer token from Azure AD using client credentials."""
    logging.info("Requesting access token from Azure AD")
    try:
        res = requests.post(
            f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope":         "https://graph.microsoft.com/.default",
            },
            timeout=10,
        )
        res.raise_for_status()
        logging.info("Access token obtained successfully")
        return res.json()["access_token"]
    except requests.RequestException as e:
        logging.error(f"Failed to obtain access token: {e}")
        raise


def parse_expiry(expiry_str: str) -> datetime:
    """
    Robustly parse the expirationDateTime from Microsoft Graph.
    Handles fractional seconds of any length (e.g., '2026-05-05T18:23:45.9356913Z').
    Always returns a UTC-aware datetime.
    """
    # Normalise: strip trailing 'Z', then split off fractional seconds
    clean = expiry_str.rstrip("Z")
    if "." in clean:
        base, frac = clean.split(".", 1)
        # Truncate or pad fractional seconds to exactly 6 digits (microseconds)
        frac = (frac + "000000")[:6]
        clean = f"{base}.{frac}"

    dt = datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_subscription(headers: dict) -> dict:
    """Fetch the current subscription details from Microsoft Graph."""
    logging.info(f"Fetching subscription details for ID: {SUBSCRIPTION_ID}")
    res = requests.get(
        f"https://graph.microsoft.com/v1.0/subscriptions/{SUBSCRIPTION_ID}",
        headers=headers,
        timeout=10,
    )
    if not res.ok:
        logging.error(f"GET subscription failed: {res.status_code} — {res.text}")
    res.raise_for_status()
    return res.json()


def renew_subscription(headers: dict, new_expiry: str) -> None:
    """PATCH the subscription with a new expirationDateTime."""
    logging.info(f"Sending renewal request. New expiry: {new_expiry}")
    patch_res = requests.patch(
        f"https://graph.microsoft.com/v1.0/subscriptions/{SUBSCRIPTION_ID}",
        headers=headers,
        json={"expirationDateTime": new_expiry},
        timeout=10,
    )
    if not patch_res.ok:
        logging.error(
            f"PATCH subscription failed: {patch_res.status_code} — {patch_res.text}"
        )
    patch_res.raise_for_status()


def main(mytimer: func.TimerRequest) -> None:
    logging.info("=" * 60)
    logging.info("Subscription renewal job started")

    if mytimer.past_due:
        logging.warning("Timer trigger is running PAST DUE — check your schedule")

    logging.info(f"Target subscription ID : {SUBSCRIPTION_ID}")
    logging.info(f"Renewal threshold       : {RENEWAL_THRESHOLD_DAYS} day(s)")
    logging.info(f"Renewal duration        : {RENEWAL_DAYS} day(s)")

    try:
        # ── Step 1: Authenticate ──────────────────────────────────────────────
        token = get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }

        # ── Step 2: Fetch subscription ────────────────────────────────────────
        data = get_subscription(headers)

        expiry_str = data.get("expirationDateTime")
        if not expiry_str:
            raise ValueError("Response missing 'expirationDateTime'")

        # ── Step 3: Parse expiry (handles fractional seconds safely) ──────────
        expiry_time = parse_expiry(expiry_str)
        now         = datetime.now(timezone.utc)
        time_left   = expiry_time - now

        logging.info(f"Current time (UTC)  : {now.isoformat()}")
        logging.info(f"Expiry time (UTC)   : {expiry_time.isoformat()}")
        logging.info(
            f"Time remaining      : {int(time_left.total_seconds() // 3600)}h "
            f"{int((time_left.total_seconds() % 3600) // 60)}m"
        )

        # ── Step 4: Decide whether to renew ──────────────────────────────────
        threshold = timedelta(days=RENEWAL_THRESHOLD_DAYS)
        if time_left > threshold:
            logging.info(
                f"Renewal not needed yet "
                f"(threshold: {RENEWAL_THRESHOLD_DAYS} day(s)). Exiting."
            )
            return

        logging.warning(
            f"Subscription expires soon ({time_left}). Proceeding with renewal."
        )

        # ── Step 5: Renew ─────────────────────────────────────────────────────
        new_expiry = (now + timedelta(days=RENEWAL_DAYS)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        renew_subscription(headers, new_expiry)

        logging.info(f"Subscription renewed successfully until: {new_expiry}")

    except requests.HTTPError as e:
        logging.error(f"HTTP error during renewal: {e}")
        raise  # re-raise so Azure marks the invocation as Failed
    except Exception as e:
        logging.error(f"Unexpected error during renewal: {e}", exc_info=True)
        raise

    logging.info("Subscription renewal job completed")
    logging.info("=" * 60)