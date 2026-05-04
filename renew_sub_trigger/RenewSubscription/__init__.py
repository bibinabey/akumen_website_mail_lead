import os
import logging
import requests
from datetime import datetime, timedelta, timezone
import azure.functions as func

TENANT_ID     = os.environ["TENANT_ID"]
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
SUBSCRIPTION_ID = os.environ["SUBSCRIPTION_ID"]

def get_token():
    res = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope":         "https://graph.microsoft.com/.default"
        }
    )
    res.raise_for_status()
    return res.json()["access_token"]

def main(mytimer: func.TimerRequest) -> None:
    logging.info("Starting subscription renewal job")

    try:
        token = get_token()
        headers = {"Authorization": f"Bearer {token}"}

        # Step 1 — Get current subscription
        res = requests.get(
            f"https://graph.microsoft.com/v1.0/subscriptions/{SUBSCRIPTION_ID}",
            headers=headers
        )
        res.raise_for_status()

        data = res.json()
        expiry_str = data["expirationDateTime"]

        expiry_time = datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        time_left = expiry_time - now

        logging.info(f"Subscription expires in: {time_left}")

        # Step 2 — Renew only if needed (e.g., < 1 day left)
        if time_left > timedelta(days=1):
            logging.info("No renewal needed yet")
            return

        # Step 3 — Renew
        new_expiry = (now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

        patch_res = requests.patch(
            f"https://graph.microsoft.com/v1.0/subscriptions/{SUBSCRIPTION_ID}",
            headers=headers,
            json={"expirationDateTime": new_expiry}
        )

        patch_res.raise_for_status()
        logging.info(f"Renewed successfully until {new_expiry}")

    except Exception as e:
        logging.error(f"Error during renewal: {str(e)}")