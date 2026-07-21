"""Shared DynamoDB clients and helpers."""
import boto3
from core import SESSION_TABLE, SESSION_ACTIVITY_TABLE

dynamodb = boto3.resource("dynamodb")

_session_table = None
_activity_table = None


def get_session_table():
    """Lazy-init session DynamoDB table resource."""
    global _session_table
    if _session_table is None:
        _session_table = dynamodb.Table(SESSION_TABLE)
    return _session_table


def get_activity_table():
    """Lazy-init activity DynamoDB table resource."""
    global _activity_table
    if _activity_table is None:
        _activity_table = dynamodb.Table(SESSION_ACTIVITY_TABLE)
    return _activity_table
