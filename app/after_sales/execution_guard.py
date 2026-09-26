"""Bind a pending action to the facts and principal seen during assessment."""

import hashlib
import json
from dataclasses import asdict


def action_fingerprint(draft: dict, state: dict) -> str:
    payload = {
        "action_type": draft.get("action_type"),
        "order_id": draft.get("order_id"),
        "message_text": draft.get("message_text"),
        "decision": draft.get("decision"),
        "tenant_id": state.get("tenant_id"),
        "customer_id": state.get("customer_id"),
        "user_id": state.get("user_id"),
        "session_id": state.get("session_id"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def order_fingerprint(order) -> str:
    payload = asdict(order)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
