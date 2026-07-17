"""Shared middleware utilities for Lambda handlers.

Common patterns: request parsing, response formatting, error handling, audit logging.
"""
import json
import logging
import os
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional

import boto3

logger = logging.getLogger(__name__)

AUDIT_TABLE = os.environ.get("KNOW_AUDIT_TRAIL_TABLE", "know-audit-trail-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def parse_request(event: Dict) -> Dict:
    """Parse API Gateway event into a normalized request object."""
    return {
        "method": event.get("httpMethod", event.get("requestContext", {}).get("http", {}).get("method", "")),
        "path": event.get("path", event.get("rawPath", "")),
        "query_params": event.get("queryStringParameters") or {},
        "body": _parse_body(event),
        "user_id": _extract_user_id(event),
        "headers": event.get("headers", {}),
    }


def json_response(status: int, body: Dict, headers: Dict = None) -> Dict:
    """Create a standard JSON response."""
    default_headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    }
    if headers:
        default_headers.update(headers)

    return {
        "statusCode": status,
        "headers": default_headers,
        "body": json.dumps(body, default=str),
    }


def error_response(status: int, message: str, details: str = None) -> Dict:
    """Create a standard error response."""
    body = {"error": message}
    if details:
        body["details"] = details
    return json_response(status, body)


def log_audit(action: str, user_id: str, resource_type: str, resource_id: str, details: Dict = None):
    """Log an audit event to DynamoDB.

    Actions: upload, download, view, delete, approve, reject, share, login, role_change
    """
    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table(AUDIT_TABLE)
        table.put_item(Item={
            "PK": f"AUDIT#{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "SK": f"{datetime.now(timezone.utc).isoformat()}#{user_id}",
            "action": action,
            "user_id": user_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning("Audit log failed: %s", str(e))


def cors_preflight() -> Dict:
    """Handle CORS preflight OPTIONS request."""
    return json_response(200, {})


def validate_required_fields(body: Dict, required: list) -> Optional[str]:
    """Validate that required fields are present in request body.

    Returns error message if validation fails, None if OK.
    """
    missing = [f for f in required if not body.get(f)]
    if missing:
        return f"Missing required fields: {', '.join(missing)}"
    return None


def paginate_params(query_params: Dict, default_size: int = 25, max_size: int = 50) -> Dict:
    """Extract and validate pagination parameters."""
    page = max(1, int(query_params.get("page", "1")))
    page_size = min(max_size, max(1, int(query_params.get("page_size", str(default_size)))))
    return {"page": page, "page_size": page_size, "offset": (page - 1) * page_size}


def _parse_body(event: Dict) -> Dict:
    """Parse request body from event."""
    body = event.get("body")
    if not body:
        return {}
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return body


def _extract_user_id(event: Dict) -> str:
    """Extract user_id from JWT claims."""
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    claims = authorizer.get("claims", authorizer.get("jwt", {}).get("claims", {}))
    return claims.get("email") or claims.get("sub") or ""
