"""
shared/helpers.py — Common utilities for Lambda functions.

Provides:
  - DynamoDB table resolution from environment variables
  - Request body / authorizer context extraction
  - Decimal → JSON-safe conversion
  - Standardized API Gateway response builder (consistent with bookmarks module)
"""

import os
import json
import boto3
from decimal import Decimal
from datetime import datetime, timezone
from aws_lambda_powertools import Logger, Tracer

logger = Logger()
tracer = Tracer()

dynamodb = boto3.resource("dynamodb")

HEADERS = {
    "Content-Type": "application/json",
    "X-Content-Type-Options": "nosniff",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,PATCH,OPTIONS",
}


def get_table(table_env: str):
    """Retrieve DynamoDB table using table name from environment variable."""
    table_name = os.environ[table_env]
    tracer.put_annotation(key=table_env, value=table_name)
    return dynamodb.Table(table_name)


def get_request_body(event: dict) -> dict:
    """Parse the request body from the Lambda event."""
    body_string = event.get("body")
    if not body_string:
        return {}
    if isinstance(body_string, dict):
        return body_string
    return json.loads(body_string)


def get_request_context(event: dict) -> dict:
    """Extract authorizer context from the event."""
    request_context = event.get("requestContext", {})
    return request_context.get("authorizer") or request_context.get("Authorizer") or {}


def convert_decimals(obj):
    """Convert Decimal types to int/float for JSON serialization."""
    if isinstance(obj, list):
        return [convert_decimals(item) for item in obj]
    if isinstance(obj, dict):
        return {key: convert_decimals(value) for key, value in obj.items()}
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    return obj


def build_response(status_code: int, body) -> dict:
    """Build a standard API Gateway HTTP proxy response.

    Args:
        status_code (int): HTTP status code (e.g. 200, 201, 400, 404).
        body: Response payload — serialised to JSON.

    Returns:
        dict: API Gateway proxy response dict with statusCode, headers, body.
    """
    return {
        "statusCode": status_code,
        "headers": HEADERS,
        "body": json.dumps(body, default=str),
    }


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
