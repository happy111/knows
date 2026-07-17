"""RBAC middleware — resolves user role from JWT + DynamoDB.

Phased approach:
  Sprint 1-5: Static RBAC (DynamoDB lookup, manually seeded)
  Sprint 6:   Dynamic RBAC (Azure AD group sync)

Role Levels:
  L1 — Basic Viewer (all users, MR/CI only)
  L2 — Restricted Viewer (TA-approved, can see IPST for that TA)
  L3 — Content Uploader (can upload, publish, send for review)
  L4 — Access Manager (approve/reject, manage roles, QC tags, grant access)
"""
import logging
import os
from functools import wraps
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)

USER_ROLES_TABLE = os.environ.get("KNOW_USER_ROLES_TABLE", "know-user-roles-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

# Role hierarchy (higher number = more permissions)
ROLE_HIERARCHY = {"L1": 1, "L3": 3, "L4": 4}

# Capability matrix — MUST match rbac/core/constants.py ROLE_CAPABILITIES
CAPABILITIES = {
    "L1": {
        "view_general": True,
        "view_restricted": False,
        "upload": False,
        "publish": False,
        "send_for_review": False,
        "approve": False,
        "reject": False,
        "delete": False,
        "qc_tags": False,
        "admin": False,
    },
    "L3": {
        "view_general": True,
        "view_restricted": False,
        "upload": True,
        "publish": True,
        "send_for_review": True,
        "approve": False,
        "reject": False,
        "delete": False,
        "qc_tags": True,
        "admin": False,
    },
    "L4": {
        "view_general": True,
        "view_restricted": False,
        "upload": True,
        "publish": True,
        "send_for_review": False,
        "approve": True,
        "reject": True,
        "delete": True,
        "qc_tags": True,
        "admin": True,
    },
}

GENERAL_CATEGORIES = {"MR", "CI"}
RESTRICTED_CATEGORIES = {"IPST", "PV", "LT"}


def get_capabilities(role, ta_access=None):
    """Compute capabilities. Mirrors rbac/core/constants.get_capabilities()."""
    caps = CAPABILITIES.get(role, CAPABILITIES["L1"]).copy()
    if ta_access:
        caps["view_restricted"] = True
    return caps


def resolve_user_context(user_id: str) -> Dict[str, Any]:
    """Load user role, TA entitlements, and capabilities from DynamoDB.

    Returns user context with computed capabilities for request processing.
    """
    table = dynamodb.Table(USER_ROLES_TABLE)

    try:
        response = table.get_item(Key={"user_id": user_id})
        item = response.get("Item")
    except Exception as e:
        logger.error("Failed to resolve user %s: %s", user_id, str(e))
        item = None

    if not item:
        return {
            "user_id": user_id,
            "role": "L1",
            "ta_access": [],
            "capabilities": get_capabilities("L1"),
        }

    role = item.get("role", "L1")
    ta_access = item.get("ta_access", [])
    return {
        "user_id": user_id,
        "role": role,
        "ta_access": ta_access,
        "capabilities": get_capabilities(role, ta_access),
    }


def require_role(minimum_level: str):
    """Decorator that enforces minimum role level on a handler function.

    Usage:
        @require_role("L3")
        def upload_handler(event, context, user_context):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(event, context, *args, **kwargs):
            user_id = extract_user_id(event)
            if not user_id:
                return _forbidden("No user identity found")

            user_context = resolve_user_context(user_id)
            user_level = ROLE_HIERARCHY.get(user_context["role"], 0)
            required_level = ROLE_HIERARCHY.get(minimum_level, 99)

            if user_level < required_level:
                return _forbidden(f"Requires {minimum_level} or higher")

            return func(event, context, user_context=user_context, **kwargs)
        return wrapper
    return decorator


def can_access_document(user_context: Dict, document_metadata: Dict) -> bool:
    """Check if user can access a specific document based on RBAC rules.

    Rules:
    - MR/CI (general): All users can access
    - IPST/PV/LT (restricted): Only if view_restricted=true AND doc TA in ta_access
    """
    category = document_metadata.get("category", "")

    if category in GENERAL_CATEGORIES:
        return True

    if category in RESTRICTED_CATEGORIES:
        caps = user_context.get("capabilities", {})
        if not caps.get("view_restricted", False):
            return False
        doc_ta = document_metadata.get("therapeutic_area", "")
        return doc_ta in user_context.get("ta_access", user_context.get("ta_entitlements", []))

    return True


def can_delete_upload(user_context: Dict, upload_record: Dict) -> bool:
    """Check if user can delete an upload.

    L3: Own uploads only (pre-publish)
    L4: Any upload
    """
    if user_context["role_level"] == "L4":
        return True

    if user_context["role_level"] == "L3":
        return (
            upload_record.get("uploaded_by") == user_context["user_id"]
            and upload_record.get("status") != "published"
        )

    return False


def extract_user_id(event: Dict) -> Optional[str]:
    """Extract user_id (email) from API Gateway JWT authorizer claims."""
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    claims = authorizer.get("claims", authorizer.get("jwt", {}).get("claims", {}))
    return claims.get("email") or claims.get("sub")


def _forbidden(message: str) -> Dict:
    """Return 403 Forbidden response."""
    import json
    return {
        "statusCode": 403,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "Forbidden", "message": message}),
    }
