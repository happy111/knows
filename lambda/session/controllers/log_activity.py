"""Controller: Log User Activity — POST /sessions/log-user-activity"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from aws_lambda_powertools import Logger, Tracer

from core.clients import get_session_table, get_activity_table
from core.response import build_response

logger = Logger()
tracer = Tracer()


@tracer.capture_method
def _log_activity(user_id: str, session_id: str, activity: dict, feedback: int = 0, agent_latency=None, chattime: str = None) -> None:
    """Write a chat interaction record to the User Activity table."""
    activity_table = get_activity_table()

    datetime_value = chattime or datetime.now(timezone.utc).isoformat()

    item = {
        "user_id": user_id,
        "session": session_id,
        "datetime": datetime_value,
        "chat_activity": activity,
        "feedback": feedback,
    }

    if agent_latency is not None:
        try:
            item["agent_latency"] = Decimal(str(agent_latency))
        except (InvalidOperation, ValueError, TypeError):
            pass

    activity_table.put_item(Item=item)


@tracer.capture_method
def _update_session_details(user_id: str, session_id: str, last_filter_values: dict) -> None:
    """Update session last_accessed_at and last_filter_values."""
    session_table = get_session_table()
    current_time = datetime.now(timezone.utc).isoformat()

    session_table.update_item(
        Key={"user_id": user_id, "session_id": session_id},
        UpdateExpression="SET last_accessed_at = :ts, last_filter_values = :fv",
        ExpressionAttributeValues={":ts": current_time, ":fv": last_filter_values},
    )


@tracer.capture_method
def log_activity(event: dict, user_id: str) -> dict:
    """Log a chat interaction and update session metadata."""
    from lambda_function import parse_body

    try:
        body = parse_body(event)

        session_id = body.get("session_id")
        last_filter_values = body.get("filter_dict", {})
        agent_response = body.get("agent_response", "")
        raw_latency = body.get("latency", 0)
        try:
            agent_latency = Decimal(str(raw_latency))
        except (InvalidOperation, ValueError, TypeError):
            agent_latency = Decimal(0)
        chattime = body.get("chattime", datetime.now(timezone.utc).isoformat())

        activity = {
            "query": body.get("query", ""),
            "agent_response": agent_response,
            "session_id": session_id,
            "filter_dict": last_filter_values,
        }

        _log_activity(
            user_id=user_id,
            session_id=session_id,
            activity=activity,
            agent_latency=agent_latency,
            chattime=chattime,
        )

        _update_session_details(user_id=user_id, session_id=session_id, last_filter_values=last_filter_values)

        return build_response(200, {"status": "success", "message": "Activity logged"})

    except Exception as e:
        logger.error(f"Error logging activity: {e}")
        return build_response(500, {"error": {"code": "INTERNAL_ERROR", "message": str(e)}})
