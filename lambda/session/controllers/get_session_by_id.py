"""Controller: Get Session By ID — GET /sessions/by_id"""

from aws_lambda_powertools import Logger, Tracer

from core.clients import get_session_table
from core.response import build_response

logger = Logger()
tracer = Tracer()


@tracer.capture_method
def get_session_by_id(event: dict, user_id: str) -> dict:
    """Retrieve a single session by composite key."""
    query_params = event.get("queryStringParameters") or {}
    session_id = query_params.get("session_id")

    if not session_id:
        return build_response(400, {"error": {"code": "VALIDATION_ERROR", "message": "session_id must be provided in query parameters"}})

    session_table = get_session_table()

    try:
        response = session_table.get_item(Key={"user_id": user_id, "session_id": session_id})
        session = response.get("Item")

        if not session:
            return build_response(404, {"error": {"code": "NOT_FOUND", "message": f"No session found with SessionId '{session_id}' for user '{user_id}'"}})

        return build_response(200, {
            "status": "success",
            "message": f"Retrieved session with SessionId '{session_id}'",
            "session": session,
        })

    except Exception as e:
        logger.error(str(e))
        return build_response(500, {"error": {"code": "INTERNAL_ERROR", "message": str(e)}})
