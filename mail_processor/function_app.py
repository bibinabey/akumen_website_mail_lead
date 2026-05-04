import os
import re
import json
import logging
import requests
import azure.functions as func
from azure.storage.queue import QueueClient
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TENANT_ID      = os.environ["TENANT_ID"]
CLIENT_ID      = os.environ["CLIENT_ID"]
CLIENT_SECRET  = os.environ["CLIENT_SECRET"]
MAILBOX        = os.environ["MAILBOX_EMAIL"]
CLIENT_STATE   = os.environ["CLIENT_STATE"]
ALLOWED_SENDER = os.environ["ALLOWED_SENDER_EMAIL"]
STORAGE_CONN   = os.environ["AzureWebJobsStorage"]
QUEUE_NAME     = os.environ["OUTLOOK_QUEUE_NAME"]

# CRM / alert config
APP_API_CLIENT_ID     = os.environ["APP_API_CLIENT_ID"]
APP_API_CLIENT_SECRET = os.environ["APP_API_CLIENT_SECRET"]
TOKEN_URL             = os.environ["TOKEN_URL"]
CRM_URL               = os.environ["CRM_URL"]
SENDER_EMAIL          = os.environ["SENDER_EMAIL"]
ALERT_EMAIL           = os.environ["ALERT_EMAIL"]

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ── Field map ─────────────────────────────────────────────────────────────────

FIELD_MAP = {
    "full name":                    "name",
    "name":                         "name",
    "email":                        "email",
    "e-mail":                       "email",
    "phone":                        "phone",
    "phone number":                 "phone",
    "course":                       "course",
    "interested in":                "course",
    "program":                      "course",
    "downloaded file":              "course",
    "current experience level":     "experience_level",
    "current background":           "background",
    "target transition timeline":   "transition_timeline",
    "enquiry regarding":            "enquiry_regarding",
    "how can we help?":             "message",
    "how can we help":              "message",
    "time":                         "download_time",
}


TOP_LEVEL_FIELDS = {"name", "phone", "email", "course"}

EMAIL_TYPE_RULES = [
    ("new consultation request",     "Consultation Request"),
    ("new application",              "Application"),
    ("new form submission",          "Form Submission"),
    ("new resource download",        "Resource Download"),
]

COURSE_DB = {
    "data engineering": "Data Engineering",
    "cloud devops": "Cloud & DevOps",
    "cloud & devops": "Cloud & DevOps",
    "ai engineering": "AI Engineering",
    "dynamic 365 crm": "Dynamic 365 CRM",
    "cloud devops weekend": "Cloud & DevOps - Weekend",
    "power platform & dynamic 365 crm": "Power Platform & Dynamic 365 CRM",
}

# ── email type ──────────────────────────────────────────────────────────────
def detect_email_type(subject: str) -> str:
    """
    Derive a human-readable email type from the subject line.
    Checks fixed prefixes first, then looks for 'enquiry' anywhere
    in the subject. Falls back to 'Unknown' for future/unrecognised types.
    """
    subject_lower = subject.strip().lower()

    for prefix, email_type in EMAIL_TYPE_RULES:
        if subject_lower.startswith(prefix):
            return email_type

    if "enquiry" in subject_lower:
        return "Enquiry"

    # Future/unrecognised email types — nothing breaks, we just flag it
    return "Unknown"

# ── Auth helpers ──────────────────────────────────────────────────────────────

def get_graph_token():
    """Graph API token using client credentials (for mailbox access)."""
    res = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope":         "https://graph.microsoft.com/.default",
        },
    )
    res.raise_for_status()
    return res.json()["access_token"]

def graph_headers():
    return {"Authorization": f"Bearer {get_graph_token()}"}

def get_crm_token():
    """External CRM API token."""
    res = requests.post(
        TOKEN_URL,
        json={"clientId": APP_API_CLIENT_ID, "clientSecret": APP_API_CLIENT_SECRET},
        timeout=30,
    )
    res.raise_for_status()
    token = res.json().get("accessToken")
    if not token:
        raise ValueError("accessToken missing from CRM token response")
    return token
#normailize course
def normalize_course(text: str) -> str:
    if not text:
        return ""

    text = text.lower()
    text = text.replace(".pdf", "")
    text = text.replace("_", " ")
    text = text.replace("&", "and")

    text = re.sub(r"\s+", " ", text).strip()

    return text

def map_course_to_db(course_value: str) -> str:
    normalized = normalize_course(course_value)

    for key, db_value in COURSE_DB.items():
        key_normalized = normalize_course(key)

        if key_normalized in normalized:
            return db_value

    return course_value  # fallback
# ── Webhook trigger ───────────────────────────────────────────────────────────

@app.route(route="webhook", methods=["GET", "POST"])
def webhook(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("Webhook triggered")

    # Graph subscription validation handshake
    validation_token = req.params.get("validationToken")
    if validation_token:
        return func.HttpResponse(validation_token, status_code=200, mimetype="text/plain")

    if req.method != "POST":
        return func.HttpResponse(status_code=200)

    try:
        body = req.get_json()
    except ValueError:
        logger.warning("Could not parse webhook body as JSON")
        return func.HttpResponse(status_code=200)

    queue_client = QueueClient.from_connection_string(
        conn_str=STORAGE_CONN,
        queue_name=QUEUE_NAME,
    )
    # Create queue if it doesn't exist yet (safe to call repeatedly)
    try:
        queue_client.create_queue()
    except Exception:
        pass

    queued = 0
    for notification in body.get("value", []):
        # Validate clientState to reject spoofed notifications
        if notification.get("clientState") != CLIENT_STATE:
            logger.warning("clientState mismatch — skipping notification")
            continue

        message_id = notification.get("resourceData", {}).get("id")
        if message_id:
            queue_client.send_message(json.dumps({"message_id": message_id}))
            queued += 1
            logger.info(f"Queued message_id: {message_id}")

    logger.info(f"{queued} message(s) pushed to queue")
    # Return 202 immediately — Graph expects a fast response
    return func.HttpResponse(status_code=202)

# ── Queue trigger ─────────────────────────────────────────────────────────────

@app.queue_trigger(
    arg_name="msg",
    queue_name="queue-trigger-test",   # must be the hard-coded queue name string here
    connection="AzureWebJobsStorage",
)
def process_queue(msg: func.QueueMessage):
    logger.info("Queue trigger fired")
    try:
        data       = json.loads(msg.get_body().decode())
        message_id = data.get("message_id")
        dequeue_count = msg.dequeue_count
        if not message_id:
            logger.error("No message_id in queue payload — dropping")
            return

        logger.info(f"Processing message_id: {message_id}")
        process_message(message_id, dequeue_count)

    except Exception:
        logger.exception("Queue processing failed")
        raise  # Let Azure retry (up to maxDequeueCount times, then dead-letters it)

# ── Core processing ───────────────────────────────────────────────────────────

def process_message(message_id: str, dequeue_count:int):
    """Fetch the email from Graph, validate, parse, and send to CRM."""
    res = requests.get(
        f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages/{message_id}",
        headers={
            **graph_headers(),
            "Prefer": 'outlook.body-content-type="text"',
        },
    )
    res.raise_for_status()
    message = res.json()

    # 1. Sender check
    sender_address = (
        message.get("from", {})
               .get("emailAddress", {})
               .get("address", "")
               .lower()
    )
    if sender_address != ALLOWED_SENDER.lower():
        logger.info(f"Ignored — sender not allowed: {sender_address}")
        return

    # 2. Subject must start with "New" (case-insensitive)
    subject = message.get("subject", "")
    if not subject.lower().startswith("new"):
        logger.info(f"Ignored — subject doesn't start with 'New': {subject}")
        return

    # 3. Body must exist
    body_content = (message.get("body", {}).get("content") or "").strip()
    if not body_content:
        logger.warning("Empty body — skipping")
        return

    # 4. Detect email type from subject
    email_type = detect_email_type(subject)
    logger.info(f"Email type detected: {email_type}")

    # 5. Parse
    parsed = parse_body(body_content)
    logger.info(f"Parsed lead: {json.dumps(parsed, indent=2)}")

    crm_payload = {
        "name":          parsed.get("name") or "unknown",
        "mobile":        parsed.get("phone") or None,
        "email":         parsed.get("email") or None,
        "course_name":   parsed.get("course") or None,
        "lead_source":   "Webpage",
        "other_details": build_other_details(parsed, email_type),
    }
    logger.info(f"payload_crm: {crm_payload}")
    send_to_crm(crm_payload, dequeue_count)

def build_other_details(parsed, email_type):
    other = parsed.get("other")

    if other:
        return f"Email Type: {email_type}; {other}"
    return f"Email Type: {email_type}"

def parse_body(body_content: str) -> dict:
    result = {
        "name":        None,
        "email":       None,
        "phone":       None,
        "course":      None,
    }
    other_parts = []

    for raw_line in body_content.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue

        raw_key, _, raw_value = line.partition(":")
        key_clean   = raw_key.strip().lower().rstrip("*")
        value_clean = raw_value.strip()

        if not key_clean:
            continue

        standard_key = FIELD_MAP.get(key_clean)

        clean_label = raw_key.rstrip("*").strip()
        if standard_key:
            if standard_key in TOP_LEVEL_FIELDS:
                # Goes into its own slot — first occurrence wins
                if result.get(standard_key) is None:
                    if standard_key == "course" and value_clean:
                        value_clean = map_course_to_db(value_clean)
                    result[standard_key] = value_clean or None
            else:
                # Known but not top-level — goes into other_parts
                if value_clean:
                    other_parts.append(f"{clean_label}: {value_clean}")
        else:
            # Completely unknown field — still captured
            if value_clean:
                other_parts.append(f"{clean_label}: {value_clean}")

    result["other"] = "; ".join(other_parts) if other_parts else None
    return result

# ── CRM integration ───────────────────────────────────────────────────────────

def send_to_crm(payload: dict, dequeue_count: int):
    try:
        token = get_crm_token()
        res = requests.post(
            CRM_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        logger.info(f"CRM response: {res.status_code} — {res.text}")
        res.raise_for_status()
        logger.info("Lead sent to CRM successfully")

    # except requests.exceptions.RequestException as e:
    except Exception as e:
        body_text = e.response.text if hasattr(e, "response") and e.response else ""
        full_error = f"{e} | CRM Response: {body_text}"
        logger.error(f"CRM send failed: {full_error}")
        if dequeue_count >= 2: 
            send_failure_email(payload, full_error)
        raise  # Re-raise so the queue message gets retried by Azure

# ── Failure alert email ───────────────────────────────────────────────────────

def send_failure_email(payload: dict, error_message: str):
    try:
        token = get_graph_token()
        ist   = timezone(timedelta(hours=5, minutes=30))
        now   = datetime.now(ist).strftime("%d %B %Y | %I:%M:%S %p IST")

        email_body = (
            f"CRM Lead Sending Failed\n\n"
            f"Time: {now}\n\n"
            f"Error:\n{error_message}\n\n"
            f"Payload:\n{json.dumps(payload, indent=2)}"
        )
        message = {
            "message": {
                "subject": "🚨 akumen website mail CRM Lead Failed",
                "body": {"contentType": "Text", "content": email_body},
                "toRecipients": [{"emailAddress": {"address": ALERT_EMAIL}}],
            }
        }
        res = requests.post(
            f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=message,
            timeout=30,
        )
        res.raise_for_status()
        logger.info("Failure alert email sent")

    except Exception:
        logger.exception("Could not send failure alert email")