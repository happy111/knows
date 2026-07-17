"""Shared Audit Trail -- decorator + direct log function for all Lambda handlers.

Single source of truth for audit logging across all Lambda modules.
Logs actions to DynamoDB audit table. Non-blocking — DDB failures don't crash handlers.

Usage (decorator):
    from shared.audit import audit_action

    @audit_action("publish", resource_type="upload")
    def publish(event, user):
        ...

Usage (direct):
    from shared.audit import log_audit_event

    log_audit_event(
        action="delete",
        user_id=user_id,
        resource_type="document",
        resource_id=doc_id,
        event=event,
    )
"""
import json
import logging
import os
from datetime import datetime, timezone
from functools import wraps
from typing import Dict, Optional

import boto3

logger = logging.getLogger(__name__)

AUDIT_TABLE = os.environ.get("KNOW_AUDIT_TRAIL_TABLE", "know-audit-trail-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

# Standard action vocabulary — superset of all modules
ACTIONS = {
    # Auth
    "login", "logout",
    # Document lifecycle
    "search", "view", "download",
    "upload", "publish", "approve", "reject", "discard", "delete",
    "send_for_review", "duplicate_resolve", "review",
    # Bookmarks
    "bookmark", "unbookmark", "bookmark_list", "bookmark_check",
    # Feedback
    "feedback_create", "feedback_list", "feedback_stats",
    # RBAC
    "role_change", "access_request",
    # Social
    "share",
}


def audit_action(action: str, resource_type: str = "document"):
    """Decorator that auto-logs an audit event on successful handler execution.

    Only logs on 2xx responses. Non-blocking — DDB failures don't crash the handler.

    Args:
        action: One of ACTIONS (e.g., "publish", "delete", "bookmark", "feedback_create")
        resource_type: "document", "upload", "user", "session", "bookmark", "feedback"

    Example:
        @audit_action("publish", resource_type="upload")
        def publish(event, user):
            result = do_publish(record, user)
            return {"statusCode": 200, "body": json.dumps(result)}

        @audit_action("bookmark", resource_type="bookmark")
        def handle(event, user_id):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(event, user_id, *args, **kwargs):
            result = func(event, user_id, *args, **kwargs)

            status_code = result.get("statusCode", 500) if isinstance(result, dict) else 500
            if 200 <= status_code < 300:
                try:
                    _uid = user_id if isinstance(user_id, str) else user_id.get("user_id", "unknown")
                    _write_audit(
                        action=action,
                        user_id=_uid,
                        resource_type=resource_type,
                        resource_id="",
                        ip_address=_extract_ip(event),
                        user_agent=_extract_user_agent(event),
                        result="success",
                        details={"path": event.get("path", "")},
                        api_details={"path": event.get("path", ""), "method": event.get("httpMethod", "")},
                    )
                except Exception as e:
                    logger.warning("Audit log failed (non-blocking): %s", str(e))

            return result
        return wrapper
    return decorator


def log_audit_event(
    action: str,
    user_id: str,
    resource_type: str,
    resource_id: str = "",
    ip_address: str = "",
    user_agent: str = "",
    result: str = "success",
    details: Optional[Dict] = None,
    before: Optional[Dict] = None,
    after: Optional[Dict] = None,
    event: Optional[Dict] = None,
):
    """Direct audit log function — for cases where decorator doesn't fit.

    Auto-extracts IP, User-Agent, and resource_id from event if provided and not already set.

    Args:
        action: One of ACTIONS vocabulary
        user_id: Email or sub from JWT
        resource_type: "document", "upload", "user", "session", "bookmark", "feedback"
        resource_id: document_id, upload_id, or user_id (auto-extracted if not provided)
        ip_address: Client IP from API Gateway (optional, auto-extracted if not provided)
        user_agent: Browser/client UA string (optional, auto-extracted if not provided)
        result: "success" or "denied" or "failure"
        details: Extra context as dict (optional)
        before: State before the change (optional)
        after: State after the change (optional)
        event: Lambda event dict (optional, used for auto-extraction)

    Examples:
        # Document action
        log_audit_event("delete", "user@novartis.com", "document", "doc-123",
                       ip_address="10.0.0.1", event=event)

        # Bookmark action
        log_audit_event("bookmark", "user@novartis.com", "bookmark",
                       resource_id="doc-123", event=event)

        # Feedback action
        log_audit_event("feedback_create", "user@novartis.com", "feedback",
                       resource_id="feedback-123", event=event)

        # Role change with before/after snapshots
        log_audit_event("role_change", "admin@novartis.com", "user",
                       resource_id="target@novartis.com", event=event,
                       before={"role": "L3"}, after={"role": "L1"})
    """
    api_details = {}
    if event:
        if not ip_address:
            ip_address = _extract_ip(event)
        if not user_agent:
            user_agent = _extract_user_agent(event)
        api_details = {"path": event.get("path", ""), "method": event.get("httpMethod", "")}

    _write_audit(
        action, user_id, resource_type, resource_id,
        ip_address, user_agent, result, details, before, after, api_details
    )


def _write_audit(
    action: str, user_id: str, resource_type: str, resource_id: str,
    ip_address: str = "", user_agent: str = "", result: str = "success",
    details: Optional[Dict] = None, before: Optional[Dict] = None,
    after: Optional[Dict] = None, api_details: Optional[Dict] = None,
):
    """Write audit record to DynamoDB. Non-blocking (catches all exceptions)."""
    try:
        now = datetime.now(timezone.utc)
        table = dynamodb.Table(AUDIT_TABLE)
        table.put_item(Item={
            "PK": f"AUDIT#{now.strftime('%Y-%m-%d')}",
            "SK": f"{user_id}#{now.isoformat()}",
            "action": action,
            "user_id": user_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "result": result,
            "details": details or {},
            "before": before or {},
            "after": after or {},
            "timestamp": now.isoformat(),
            "ttl": int(now.timestamp() + (365 * 24 * 3600)),  # 365-day retention
            "api_details": api_details or {},
        })
        logger.debug(f"Audit logged: {action} by {user_id} on {resource_type}/{resource_id}")
    except Exception as e:
        logger.warning("Audit log failed (non-blocking): %s", str(e))


def _extract_ip(event: Dict) -> str:
    """Extract client IP from API Gateway requestContext."""
    identity = event.get("requestContext", {}).get("identity", {})
    return identity.get("sourceIp", "")


def _extract_user_agent(event: Dict) -> str:
    """Extract User-Agent from request headers."""
    identity = event.get("requestContext", {}).get("identity", {})
    return identity.get("userAgent", "")
