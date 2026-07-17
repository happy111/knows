"""Shared Auth Utilities -- JWT extraction and user identity resolution.

Single source of truth for authentication across all Lambda modules:
- API Gateway authorizer (decoded claims in requestContext)
- OAuth callback (raw JWT token in Authorization header)
- Local development (mock user via environment variables)
- Role-based access control (DynamoDB lookup + decorator)

Usage:
    from shared.auth import extract_user_id, extract_user_from_event
    from shared.auth import required_role, is_permission_granted
"""
import base64
import json
import os
from typing import Any, Dict, Optional

import jwt

from shared.logger import get_logger

logger = get_logger(__name__)

# Content type header constant
JSON_CONTENT_TYPE = "application/json"


def extract_token_from_event(event: Dict[str, Any]) -> Optional[str]:
    """Extract bearer token from Authorization header.

    Args:
        event: API Gateway proxy event

    Returns:
        Token string (without 'Bearer ') or None
    """
    headers = event.get("headers", {}) or {}
    auth_header = headers.get("Authorization", "")
    if not auth_header:
        return None

    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]

    return None


def decode_jwt_token(
    token: str, verify_signature: bool = False
) -> Optional[Dict[str, Any]]:
    """Decode JWT token (with or without signature verification).

    Args:
        token: JWT token string
        verify_signature: Whether to verify JWT signature (requires public key)

    Returns:
        Decoded JWT claims dict, or None if decode fails
    """
    try:
        decoded = jwt.decode(
            token,
            key="" if not verify_signature else None,
            options={"verify_signature": verify_signature},
            algorithms=["RS256", "HS256"],
        )
        return decoded
    except (jwt.DecodeError, jwt.InvalidSignatureError, jwt.InvalidKeyError):
        # If PyJWT fails, try manual decoding (bypasses signature validation)
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None

            # Manually decode payload
            payload_padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload_decoded = base64.urlsafe_b64decode(payload_padded)
            decoded = json.loads(payload_decoded)

            # Only return if not verifying signature
            if not verify_signature:
                return decoded
            return None
        except Exception:
            return None
    except Exception as e:
        logger.warning(f"Unexpected JWT error: {e}")
        return None


def extract_oauth_callback_user(userinfo: Dict[str, Any]) -> Dict[str, str]:
    """Extract user details from OAuth callback userinfo.

    Cognito OAuth callback includes userinfo object with:
    - email: Email address
    - custom:idp-name: Email address (alternate field)
    - sub: Subject (unique user ID)
    - cognito:username: Cognito username
    - cognito:groups: User groups

    Args:
        userinfo: Userinfo dict from OAuth callback

    Returns:
        Dict with user_id, email, name, sub, groups
    """
    # Try email field first (standard JWT claim)
    email = userinfo.get("email", "")

    # Fallback to custom:idp-name
    if not email:
        email = userinfo.get("custom:idp-name", "")

    # Last resort: extract from cognito:username (format: idp_<suffix>)
    if not email:
        username = userinfo.get("cognito:username", "")
        email = username.split("_")[0] if "_" in username else username

    sub = userinfo.get("sub", "")
    name = userinfo.get("given_name", "")
    groups_list = userinfo.get("cognito:groups", [])

    # Ensure groups is a list
    if isinstance(groups_list, str):
        groups_list = [g.strip() for g in groups_list.split(",") if g.strip()]

    return {
        "user_id": email or sub,
        "email": email,
        "name": name,
        "sub": sub,
        "groups": groups_list,
    }


def extract_user_from_oauth_token(token: str) -> Optional[Dict[str, str]]:
    """Extract user info from OAuth ID token.

    OAuth ID token is JWT with userinfo embedded in claims.
    Assumes token is already verified by OAuth provider (Cognito).

    Args:
        token: JWT ID token from OAuth callback

    Returns:
        Dict with user_id, email, name, sub, groups, or None if decode fails
    """
    # Decode without verification (Cognito already verified it)
    decoded = decode_jwt_token(token, verify_signature=False)
    if not decoded:
        return None

    return extract_oauth_callback_user(decoded)


def extract_user_from_event(event: Dict[str, Any]) -> Dict[str, str]:
    """Extract user identity from API Gateway event.

    Checks in order of precedence:
    1. OAuth token in Authorization header (raw JWT)
    2. API Gateway Cognito authorizer decoded claims
    3. Environment variables (local dev mock)

    Args:
        event: API Gateway proxy event

    Returns:
        Dict with user_id, email, name, sub, groups
    """
    claims = {}

    # Try 1: Extract from Authorization header (OAuth callback)
    token = extract_token_from_event(event)
    if token:
        user_info = extract_user_from_oauth_token(token)
        if user_info:
            logger.info(f"User extracted from OAuth token: {user_info.get('user_id')}")
            return user_info

    # Try 2: Extract from API Gateway authorizer (Cognito authorizer)
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    claims = authorizer.get("claims", authorizer.get("jwt", {}).get("claims", {}))

    if claims:
        logger.info("User extracted from authorizer claims")
    else:
        # Try 3: Environment variables (local dev)
        if os.getenv("MOCK_USER_EMAIL"):
            claims = {
                "email": os.getenv("MOCK_USER_EMAIL", ""),
                "sub": os.getenv("MOCK_USER_SUB", "local-dev-user"),
                "name": os.getenv("MOCK_USER_NAME", "Local Dev User"),
                "custom:groups": os.getenv("MOCK_USER_GROUPS", ""),
            }
            logger.info("User extracted from environment variables")

    email = claims.get("email", "")
    sub = claims.get("sub", "")
    name = claims.get("name", claims.get("cognito:username", ""))
    groups_raw = claims.get("custom:groups", claims.get("cognito:groups", ""))

    groups = (
        [g.strip() for g in groups_raw.split(",") if g.strip()]
        if groups_raw
        else []
    )

    return {
        "user_id": email or sub,
        "email": email,
        "name": name,
        "sub": sub,
        "groups": groups,
    }


def extract_user_id(event: Dict[str, Any]) -> str:
    """Quick extraction of just user_id (email or sub).

    Args:
        event: API Gateway proxy event

    Returns:
        User ID string
    """
    user = extract_user_from_event(event)
    user_id = user.get("user_id", "")
    if user_id:
        logger.info(f"Extracted user ID: {user_id}")
    return user_id


def _get_user_role(user_id: str) -> tuple[str, Optional[Dict[str, Any]]]:
    """Fetch user role from the Users DynamoDB table.

    Generic DynamoDB lookup — does not depend on any Lambda-specific DAL.
    Reads from KNOW_USER_ROLES_TABLE (default: know-user-roles-dev).

    Args:
        user_id: User ID (email or sub) to fetch role for.

    Returns:
        Tuple of (role, user_record) where role defaults to "L1" if not found.
    """
    if not user_id or not user_id.strip():
        logger.warning("Empty user_id passed to _get_user_role, defaulting to L1")
        return "L1", None

    import boto3

    users_table = os.environ.get("KNOW_USER_ROLES_TABLE",
                                  os.environ.get("KNOW_USERS_TABLE", "know-user-roles-dev"))
    region = os.environ.get("AWS_REGION", "us-east-1")

    try:
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(users_table)
        response = table.get_item(Key={"user_id": user_id})
        user_record = response.get("Item")

        if not user_record:
            logger.info("User %s not found in users table, defaulting to L1", user_id)
            return "L1", None

        user_role = user_record.get("role", "L1")
        logger.info("Fetched user role for %s: %s", user_id, user_role)
        return user_role, user_record

    except Exception as e:
        logger.error("Failed to fetch user role for %s: %s", user_id, e)
        raise


def is_permission_granted(user_id: str, required_role: str) -> bool:
    """Check if user has the required role.

    Args:
        user_id: User ID (email or sub)
        required_role: Required role (e.g., 'L1', 'L3', 'L4')

    Returns:
        True if user has the required role, False otherwise
    """
    try:
        user_role, _ = _get_user_role(user_id)
    except Exception as e:
        logger.error(f"Failed to fetch user role for {user_id}: {e}")
        return False

    return user_role == required_role


def required_role(*allowed_roles: str):
    """Decorator to enforce role-based access control on Lambda handlers.

    Can be used with single or multiple roles. Extracts user from event,
    fetches role from DynamoDB, and enforces role check before calling handler.

    Usage:
        from shared.auth import required_role

        @required_role("L3")
        def upload_handler(event, context):
            return {"statusCode": 200, "body": "Upload successful"}

        @required_role("L3", "L4")
        def publish_handler(event, context):
            return {"statusCode": 200, "body": "Publish successful"}

    Args:
        *allowed_roles: One or more allowed roles (e.g., "L3", "L4")

    Returns:
        Decorator function
    """
    from functools import wraps

    def decorator(func):
        @wraps(func)
        def wrapper(event: Dict[str, Any], context: Any, *args, **kwargs):
            # Extract user ID
            try:
                user_id = extract_user_id(event)
            except Exception as e:
                logger.error(f"Failed to extract user ID: {e}")
                return {
                    "statusCode": 401,
                    "headers": {"Content-Type": JSON_CONTENT_TYPE},
                    "body": json.dumps({
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": "Failed to extract user identity"
                        }
                    }),
                }

            # Fetch user role
            try:
                user_role, _ = _get_user_role(user_id)
            except Exception as e:
                logger.error(f"Failed to fetch user role for {user_id}: {e}")
                return {
                    "statusCode": 500,
                    "headers": {"Content-Type": JSON_CONTENT_TYPE},
                    "body": json.dumps({
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "Failed to verify user role"
                        }
                    }),
                }

            # Check if user has required role
            if user_role not in allowed_roles:
                logger.warning(
                    f"Access denied for {user_id}: has {user_role}, "
                    f"requires {allowed_roles}"
                )
                return {
                    "statusCode": 403,
                    "headers": {"Content-Type": JSON_CONTENT_TYPE},
                    "body": json.dumps({
                        "error": {
                            "code": "INSUFFICIENT_ROLE",
                            "message": f"Access denied: requires one of {allowed_roles}"
                        }
                    }),
                }

            logger.info(f"Access granted for {user_id} with role {user_role}")

            # Call the original handler
            return func(event, context, *args, **kwargs)

        return wrapper
    return decorator
