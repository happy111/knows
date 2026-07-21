"""Controller: Get Sessions Last N Days — GET /sessions/last_n_days"""

from datetime import datetime, timedelta, timezone
from boto3.dynamodb.conditions import Key, Attr

from aws_lambda_powertools import Logger, Tracer

from core.clients import get_session_table
from core.response import build_response

logger = Logger()
tracer = Tracer()

DAYS_LOOKBACK = 30


def _group_sessions_by_period(sessions: list) -> dict:
    """Group sessions into today, yesterday, previous 7 days, previous 30 days."""
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
    yesterday_start = today_start - timedelta(days=1)
    seven_days_ago = today_start - timedelta(days=7)

    grouped = {"today": [], "yesterday": [], "previous_7_days": [], "previous_30_days": []}

    for session in sessions:
        last_accessed = session.get("last_accessed_at") or session.get("created_at", "")
        if not last_accessed:
            grouped["previous_30_days"].append(session)
            continue

        try:
            ts = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            grouped["previous_30_days"].append(session)
            continue

        if ts >= today_start:
            grouped["today"].append(session)
        elif ts >= yesterday_start:
            grouped["yesterday"].append(session)
        elif ts >= seven_days_ago:
            grouped["previous_7_days"].append(session)
        else:
            grouped["previous_30_days"].append(session)

    return grouped


@tracer.capture_method
def get_sessions_last_n(event: dict, user_id: str) -> dict:
    """Query sessions for a user from the last N days, grouped by period."""
    from lambda_function import parse_authorizer

    authorizer = parse_authorizer(event)
    group = authorizer.get("group")

    session_table = get_session_table()

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS_LOOKBACK)).isoformat()
        sessions = []
        last_evaluated_key = None

        while True:
            qp = {
                "IndexName": "user_recent_sessions_index",
                "KeyConditionExpression": Key("user_id").eq(user_id) & Key("last_accessed_at").gte(cutoff),
            }
            if group:
                qp["FilterExpression"] = Attr("group").eq(group)
            if last_evaluated_key:
                qp["ExclusiveStartKey"] = last_evaluated_key

            response = session_table.query(**qp)
            sessions.extend(response.get("Items", []))
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

        grouped = _group_sessions_by_period(sessions)

        return build_response(200, {
            "status": "success",
            "message": f"Retrieved {len(sessions)} sessions for {user_id}",
            "today": grouped["today"],
            "yesterday": grouped["yesterday"],
            "previous_7_days": grouped["previous_7_days"],
            "previous_30_days": grouped["previous_30_days"],
        })

    except Exception as e:
        logger.error(str(e))
        return build_response(500, {"error": {"code": "INTERNAL_ERROR", "message": str(e)}})
