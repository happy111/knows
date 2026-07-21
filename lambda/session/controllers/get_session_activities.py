"""Controller: Get Session Activities — POST /sessions/activities"""

from boto3.dynamodb.conditions import Key, Attr
from aws_lambda_powertools import Logger, Tracer

from core.clients import get_activity_table
from core.response import build_response

logger = Logger()
tracer = Tracer()


@tracer.capture_method
def get_session_activities(event: dict, user_id: str) -> dict:
    """Retrieve all activity records (messages) for a given session."""
    from lambda_function import parse_body

    body = parse_body(event)
    session_id = body.get("session_id")

    if not session_id:
        return build_response(400, {"error": {"code": "VALIDATION_ERROR", "message": "session_id must be provided"}})

    last_eval_key = body.get("LastEvaluatedKey")
    activity_table = get_activity_table()

    try:
        query_params = {
            "KeyConditionExpression": Key("user_id").eq(user_id),
            "FilterExpression": Attr("session").eq(session_id),
            "ScanIndexForward": False,
        }
        if last_eval_key:
            query_params["ExclusiveStartKey"] = last_eval_key

        response = activity_table.query(**query_params)

        return build_response(200, {
            "status": "success",
            "Items": response.get("Items", []),
            "LastEvaluatedKey": response.get("LastEvaluatedKey"),
        })

    except Exception as e:
        logger.error(str(e))
        return build_response(500, {"error": {"code": "INTERNAL_ERROR", "message": str(e)}})
