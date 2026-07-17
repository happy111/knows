"""Response Builder — Standardized API Gateway proxy responses.

All Lambda responses flow through build_response() to ensure
consistent CORS headers, content-type, and error formatting.
"""
import json
from typing import Any, Dict


def build_response(
    status_code: int, body: Any, request_id: str = ""
) -> Dict[str, Any]:
    """Build API Gateway proxy response with CORS headers.

    Args:
        status_code: HTTP status code (200, 400, 404, 500)
        body: Response payload (dict). Errors should use {"error": {...}} format.
        request_id: Optional AWS request ID for error tracing.

    Returns:
        API Gateway proxy-compatible response dict.
    """
    if isinstance(body, dict) and "error" in body and request_id:
        body["error"]["request_id"] = request_id

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "X-Content-Type-Options": "nosniff",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }
