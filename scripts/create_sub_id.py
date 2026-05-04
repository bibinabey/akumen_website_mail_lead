import requests
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()

def get_token():
    r = requests.post(
        f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}/oauth2/v2.0/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope":         "https://graph.microsoft.com/.default"
        }
    )
    return r.json()["access_token"]

def create_subscription(notification_url):
    token = get_token()

    # Always 3 days from now — never hardcode a date
    expiry = (
        datetime.now(timezone.utc) + timedelta(days=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    r = requests.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "changeType":         "created",
            "notificationUrl":    notification_url,
            "resource":           f"/users/{os.environ['MAILBOX_EMAIL']}/mailFolders/inbox/messages",
            "expirationDateTime": expiry,
            "clientState":        os.environ["CLIENT_STATE"]
        }
    )

    result = r.json()

    if "id" in result:
        print("Subscription created successfully!")
        print(f"Subscription ID: {result['id']}")
        print(f"Expires: {result['expirationDateTime']}")
        print()
        print("ACTION NEEDED: Copy the subscription ID above")
        print("and add it to your .env file as SUBSCRIPTION_ID=")
    else:
        print("Something went wrong:")
        print(result)

if __name__ == "__main__":
    # Change this URL depending on local vs production
    url = input("Enter your notificationUrl (ngrok or GCP URL): ").strip()
    create_subscription(url)
