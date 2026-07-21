"""Controller: Update Session — PUT /sessions/{sessionId}/users/{userEmail}"""

from urllib.parse import unquote
from aws_lambda_powertools import Logger, Tracer

from core.clients import get_session_table
from core.response import build_response
from shared.audit import log_audit_event

logger = Logger()
tracer = Tracer()


def _get_path_parameter(path: str) -> dict | None:
    """Extract sessionId and userEmail from path: /sessions/{id}/users/{email}"""
    if not path:
        return None
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "sessions" or parts[2] != "users":
        return None
    return {"sessionId": parts[1], "userEmail": unquote(parts[3])}


@tracer.capture_method
def update_session(event: dict) -> dict:
    """Dynamically update session attributes in DynamoDB."""
    from lambda_function import parse_body

    path_parameter = event.get("pathParameters") or _get_path_parameter(event.get("path"))
    body = parse_body(event)
    update_attributes = body.get("attributes")

    if (
        not isinstance(path_parameter, dict)
        or not path_parameter.get("sessionId")
        or not path_parameter.get("userEmail")
        or not isinstance(update_attributes, dict)
        or not update_attributes
    ):
        return build_response(400, {"error": {"code": "VALIDATION_ERROR", "message": "Path must contain sessionId and userEmail, and attributes must be a non-empty dict"}})

    user_id = path_parameter["userEmail"]
    session_id = path_parameter["sessionId"]
    session_table = get_session_table()

    get_response = session_table.get_item(Key={"user_id": user_id, "session_id": session_id})
    if "Item" not in get_response:
        return build_response(404, {"error": {"code": "NOT_FOUND", "message": f"No session found for user_id='{user_id}' and SessionId='{session_id}'."}})

    existing_item = get_response["Item"]
    before_snapshot = {k: existing_item.get(k) for k in update_attributes if k in existing_item}

    update_expression = "SET " + ", ".join(f"#{k} = :{k}" for k in update_attributes.keys())
    expression_attribute_names = {f"#{k}": k for k in update_attributes.keys()}
    expression_attribute_values = {f":{k}": v for k, v in update_attributes.items()}

    response = session_table.update_item(
        Key={"user_id": user_id, "session_id": session_id},
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expression_attribute_names,
        ExpressionAttributeValues=expression_attribute_values,
        ReturnValues="ALL_NEW",
    )

    log_audit_event(
        "session_update",
        user_id=user_id,
        resource_type="session",
        resource_id=session_id,
        result="success",
        details={
            "session_id": session_id,
            "updated_attributes": list(update_attributes.keys()),
        },
        before=before_snapshot,
        after={k: update_attributes[k] for k in update_attributes},
        event=event,
    )

    return build_response(200, {
        "status": "success",
        "message": "Session updated successfully",
        "updated_attributes": response.get("Attributes", {}),
    })
