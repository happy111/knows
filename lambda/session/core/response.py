"""Standardized API Gateway response builder."""
import json
from typing import Any, Dict


def build_response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build API Gateway compatible response with CORS headers."""
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "X-Content-Type-Options": "nosniff",
        },
        "body": json.dumps(body, default=str),
    }
