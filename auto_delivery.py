import json
import re
from urllib.parse import parse_qs, unquote, urlparse


PAID_RED_REMINDERS = {"等待卖家发货"}

AUTO_DELIVERY_TRIGGER_TEXTS = (
    "[我已付款，等待你发货]",
    "[已付款，待发货]",
    "我已付款，等待你发货",
    "[记得及时发货]",
)


def strip_goofish_suffix(value):
    if not value:
        return None
    return str(value).split("@", 1)[0]


def extract_item_id_from_url(url):
    if not url:
        return None

    parsed = urlparse(str(url))
    query = parse_qs(parsed.query)
    for key in ("itemId", "item_id", "itemid", "id"):
        values = query.get(key)
        if values and values[0]:
            return values[0]

    match = re.search(r"(?:itemId|item_id|itemid|id)=([^&\s\"']+)", str(url))
    if match:
        return unquote(match.group(1))
    return None


def extract_order_id_from_text(text):
    if not text:
        return None

    patterns = (
        r"(?:bizOrderId|biz_order_id|orderId|order_id)[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_-]+)",
        r"(?:bizOrderId|biz_order_id|orderId|order_id)=([^&\s\"']+)",
    )
    for pattern in patterns:
        match = re.search(pattern, str(text))
        if match:
            return unquote(match.group(1))
    return None


def build_delivery_key(order_id, chat_id, item_id, buyer_id):
    if order_id:
        return f"order:{order_id}"
    return f"chat:{chat_id or 'unknown'}:item:{item_id or 'unknown'}:buyer:{buyer_id or 'unknown'}"


def _stringify_message(message):
    try:
        return json.dumps(message, ensure_ascii=False, default=str)
    except Exception:
        return str(message)


def extract_pending_delivery_event(message):
    event = {
        "triggered": False,
        "chat_id": None,
        "buyer_id": None,
        "item_id": None,
        "order_id": None,
        "red_reminder": None,
        "content": "",
        "reason": "",
    }
    if not isinstance(message, dict):
        return event

    raw_text = _stringify_message(message)
    payload_3 = message.get("3") if isinstance(message.get("3"), dict) else {}
    red_reminder = payload_3.get("redReminder")
    event["red_reminder"] = red_reminder

    payload_1 = message.get("1")
    if isinstance(payload_1, str):
        event["chat_id"] = strip_goofish_suffix(payload_1)
    elif isinstance(payload_1, dict):
        event["chat_id"] = strip_goofish_suffix(payload_1.get("2"))
        payload_10 = payload_1.get("10") if isinstance(payload_1.get("10"), dict) else {}
        event["buyer_id"] = payload_10.get("senderUserId")
        event["content"] = payload_10.get("reminderContent") or ""
        event["item_id"] = extract_item_id_from_url(payload_10.get("reminderUrl"))
        red_reminder = red_reminder or payload_10.get("redReminder")
        event["red_reminder"] = red_reminder

    event["order_id"] = extract_order_id_from_text(raw_text)

    if red_reminder in PAID_RED_REMINDERS:
        event["triggered"] = True
        event["reason"] = "red_reminder_pending_ship"
        return event

    content = event["content"] or ""
    if any(keyword in content for keyword in AUTO_DELIVERY_TRIGGER_TEXTS):
        event["triggered"] = True
        event["reason"] = "paid_system_text"
        return event

    return event
