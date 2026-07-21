"""Controller: Create Session — POST /sessions"""

import uuid
from datetime import datetime, timezone

from aws_lambda_powertools import Logger, Tracer

from core.clients import get_session_table
from core.response import build_response
from shared.audit import log_audit_event

logger = Logger()
tracer = Tracer()


@tracer.capture_method
def create_session(event: dict, user_id: str) -> dict:
    """Create a new chat session."""
    from lambda_function import parse_body, parse_authorizer

    authorizer = parse_authorizer(event)

    group = authorizer.get("group", "")

    try:
        session_table = get_session_table()
        session_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        session_data = {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": created_at,
            "state": "active",
            "is_bookmarked": 0,
            "group": group,
        }

        session_table.put_item(Item=session_data)

        log_audit_event(
            "session_create",
            user_id=user_id,
            resource_type="session",
            resource_id=session_id,
            result="success",
            details={
                "session_id": session_id,
                "group": group,
            },
            event=event,
        )

        return build_response(201, {
            "status": "success",
            "message": "Session created successfully",
            "session_id": session_id,
            "session_data": session_data,
        })

    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return build_response(500, {"error": {"code": "INTERNAL_ERROR", "message": str(e)}})
