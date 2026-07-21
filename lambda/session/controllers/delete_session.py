"""Controller: Delete Session — DELETE /sessions"""

import time
import boto3
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger, Tracer

from core.clients import get_session_table, get_activity_table
from core.response import build_response
from shared.audit import log_audit_event

logger = Logger()
tracer = Tracer()


def _query_with_backoff(table, query_args, max_retries=5) -> dict:
    """Run a DynamoDB query with exponential backoff."""
    retries = 0
    delay = 1
    while True:
        try:
            return table.query(**query_args)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("ProvisionedThroughputExceededException", "ThrottlingException"):
                if retries < max_retries:
                    time.sleep(delay)
                    delay *= 2
                    retries += 1
                else:
                    raise
            else:
                raise


@tracer.capture_method
def _delete_activity_records(user_id: str, session_id: str):
    """Delete all user activity records associated with a session."""
    activity_table = get_activity_table()
    last_evaluated_key = None
    total_deleted = 0

    while True:
        query_args = {
            "KeyConditionExpression": boto3.dynamodb.conditions.Key("user_id").eq(user_id),
            "FilterExpression": boto3.dynamodb.conditions.Attr("session").eq(session_id),
        }
        if last_evaluated_key:
            query_args["ExclusiveStartKey"] = last_evaluated_key

        response = _query_with_backoff(activity_table, query_args)
        items = response.get("Items", [])
        last_evaluated_key = response.get("LastEvaluatedKey")

        if items:
            with activity_table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(Key={"user_id": item["user_id"], "datetime": item["datetime"]})
                    total_deleted += 1

        if not last_evaluated_key:
            break

    logger.info(f"Deleted {total_deleted} activity records for session {session_id}")


@tracer.capture_method
def delete_session(event: dict, user_id: str) -> dict:
    """Delete a session and its associated activity records."""
    query_params = event.get("queryStringParameters") or {}
    session_id = query_params.get("session_id")

    if not session_id:
        return build_response(400, {"error": {"code": "VALIDATION_ERROR", "message": "session_id must be provided in query parameters"}})

    session_table = get_session_table()

    try:
        session_table.delete_item(Key={"user_id": user_id, "session_id": session_id})
        _delete_activity_records(user_id, session_id)

        log_audit_event(
            "session_delete",
            user_id=user_id,
            resource_type="session",
            resource_id=session_id,
            result="success",
            details={
                "session_id": session_id,
                "activities_deleted": True,
            },
            event=event,
        )

        return build_response(200, {"status": "success", "message": f"Deleted session '{session_id}' and associated activity records"})

    except Exception as e:
        logger.error(str(e))
        return build_response(500, {"error": {"code": "INTERNAL_ERROR", "message": str(e)}})
