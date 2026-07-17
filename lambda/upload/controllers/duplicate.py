"""kNOW Upload Lambda — Resolve Duplicate controller."""
import json
from typing import Dict, Optional, Tuple

from utils import (
    ENRICHMENT_FUNCTION,
    METADATA_TABLE,
    UPLOADS_TABLE,
    dynamodb,
    extract_upload_id,
    lambda_client,
    now_iso,
    response,
)


VALID_DUPLICATE_ACTIONS = {"keep_both", "replace"}
PROCESSING_MESSAGE = "Processing document..."


def _can_resolve_duplicate(record: Dict, user: Dict) -> bool:
    """L4 can resolve any duplicate. L3 can resolve own upload only."""
    is_l4 = user.get("role") == "L4"
    is_own_l3_upload = (
        user.get("role") == "L3"
        and record.get("uploaded_by") == user.get("user_id")
    )

    return is_l4 or is_own_l3_upload


def _published_document_exists(document_id: str) -> bool:
    """Return True if metadata document exists and is not deleted."""
    metadata_table = dynamodb.Table(METADATA_TABLE)
    resp = metadata_table.get_item(Key={"document_id": document_id})
    item = resp.get("Item")

    return bool(item and item.get("status") != "deleted")


def _parse_json_body(event: Dict) -> Tuple[Dict, Optional[Dict]]:
    """Parse request body."""
    try:
        return json.loads(event.get("body") or "{}"), None
    except json.JSONDecodeError:
        return {}, response(400, {"error": "Invalid JSON body"})


def _validate_user_and_upload_id(upload_id: str, user: Dict) -> Optional[Dict]:
    """Validate role and upload id."""
    if user.get("role") not in ("L3", "L4"):
        return response(403, {"error": "Requires L3 or L4 role"})

    if not upload_id:
        return response(400, {"error": "upload_id required"})

    return None


def _validate_action(action: str, existing_id: Optional[str]) -> Optional[Dict]:
    """Validate duplicate resolution action."""
    if action not in VALID_DUPLICATE_ACTIONS:
        return response(400, {"error": "action must be one of: keep_both, replace"})

    if action == "replace" and not existing_id:
        return response(400, {"error": "existing_document_id is required for replace action"})

    return None


def _get_upload_record(table, upload_id: str) -> Optional[Dict]:
    """Fetch upload record."""
    resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
    return resp.get("Item")


def _validate_record(record: Optional[Dict], user: Dict) -> Optional[Dict]:
    """Validate upload record status and access."""
    if not record:
        return response(404, {"error": "Upload not found"})

    if record.get("status") != "duplicate_detected":
        return response(409, {
            "error": f"Cannot resolve duplicate in status: {record.get('status')}"
        })

    if not _can_resolve_duplicate(record, user):
        return response(403, {"error": "Access denied"})

    return None


def _validate_duplicate_source(record: Dict) -> Optional[Dict]:
    """Allow keep_both or replace only for published duplicates."""
    duplicate_info = record.get("duplicate_info") or {}
    duplicate_source = duplicate_info.get("duplicate_source")

    if duplicate_source == "in_progress":
        return response(409, {
            "error": "duplicate_in_progress",
            "message": (
                "This duplicate is from another active upload. It cannot be resolved "
                "with keep_both or replace. Please discard this upload or wait."
            ),
            "allowed_actions": ["discard"],
        })

    if duplicate_source and duplicate_source != "published":
        return response(409, {
            "error": "unsupported_duplicate_source",
            "message": f"Cannot resolve duplicate source: {duplicate_source}",
        })

    return None


def _validate_replace_target(action: str, existing_id: Optional[str]) -> Optional[Dict]:
    """Validate replacement target when action is replace."""
    if action != "replace":
        return None

    if not _published_document_exists(existing_id):
        return response(404, {
            "error": "existing_document_id does not reference an existing non-deleted document"
        })

    return None


def _base_update_values(action: str) -> Dict:
    """Build common DynamoDB expression values."""
    return {
        ":status": "processing",
        ":updated_at": now_iso(),
        ":duplicate_resolution": action,
        ":skip_duplicate_check": True,
        ":percentage": 50,
        ":phase": "enrichment",
        ":current_step": "processing",
        ":message": PROCESSING_MESSAGE,
    }


def _base_set_expression() -> str:
    """Build common DynamoDB SET expression."""
    return (
        "SET #s = :status, "
        "updated_at = :updated_at, "
        "duplicate_resolution = :duplicate_resolution, "
        "skip_duplicate_check = :skip_duplicate_check, "
        "progress.percentage = :percentage, "
        "progress.phase = :phase, "
        "progress.current_step = :current_step, "
        "progress.message = :message"
    )


def _build_update_request(action: str, existing_id: Optional[str]) -> Tuple[str, Dict]:
    """Build DynamoDB update expression and values."""
    set_expression = _base_set_expression()
    expression_values = _base_update_values(action)

    if action == "replace":
        expression_values[":replace_document_id"] = existing_id
        return f"{set_expression}, replace_document_id = :replace_document_id", expression_values

    return f"{set_expression} REMOVE replace_document_id", expression_values


def _update_duplicate_resolution(
    table,
    upload_id: str,
    action: str,
    existing_id: Optional[str],
) -> None:
    """Persist duplicate resolution state."""
    update_expression, expression_values = _build_update_request(action, existing_id)

    table.update_item(
        Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        UpdateExpression=update_expression,
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=expression_values,
    )


def _validate_enrichment_config() -> Optional[Dict]:
    """Validate enrichment Lambda configuration."""
    if not ENRICHMENT_FUNCTION:
        return response(500, {"error": "ENRICHMENT_FUNCTION is not configured"})

    return None


def _invoke_enrichment(
    upload_id: str,
    record: Dict,
    action: str,
    existing_id: Optional[str],
) -> None:
    """Re-invoke enrichment after duplicate resolution."""
    lambda_client.invoke(
        FunctionName=ENRICHMENT_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps({
            "upload_id": upload_id,
            "s3_key": record.get("s3_key"),
            "file_name": record.get("file_name"),
            "skip_duplicate_check": True,
            "duplicate_resolution": action,
            "replace_document_id": existing_id if action == "replace" else None,
        }),
    )


def resolve_duplicate(event: Dict, user: Dict) -> Dict:
    """Handle duplicate resolution — user chose keep_both or replace.

    Input:
    {
      "action": "keep_both" | "replace",
      "existing_document_id": "..."
    }
    """
    upload_id = extract_upload_id(event)

    request_error = _validate_user_and_upload_id(upload_id, user)
    if request_error:
        return request_error

    body, parse_error = _parse_json_body(event)
    if parse_error:
        return parse_error

    action = body.get("action")
    existing_id = body.get("existing_document_id")

    action_error = _validate_action(action, existing_id)
    if action_error:
        return action_error

    table = dynamodb.Table(UPLOADS_TABLE)
    record = _get_upload_record(table, upload_id)

    record_error = _validate_record(record, user)
    if record_error:
        return record_error

    duplicate_source_error = _validate_duplicate_source(record)
    if duplicate_source_error:
        return duplicate_source_error

    replace_error = _validate_replace_target(action, existing_id)
    if replace_error:
        return replace_error

    _update_duplicate_resolution(
        table=table,
        upload_id=upload_id,
        action=action,
        existing_id=existing_id,
    )

    config_error = _validate_enrichment_config()
    if config_error:
        return config_error

    _invoke_enrichment(
        upload_id=upload_id,
        record=record,
        action=action,
        existing_id=existing_id,
    )

    return response(200, {
        "upload_id": upload_id,
        "action": action,
        "status": "processing",
    })
