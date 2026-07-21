"""kNOW Upload Lambda — Review controllers: get, update, send-for-review."""
import json
import logging
import os
import re
from decimal import Decimal
from typing import Any, Dict
from utils import UPLOAD_ID_REQUIRED_ERROR
from utils import UPLOAD_NOT_FOUND_ERROR

from utils import (
    AUDIT_TABLE,
    UPLOADS_TABLE,
    VALID_CATEGORIES,
    dynamodb,
    extract_upload_id,
    now_iso,
    response,
)

logger = logging.getLogger(__name__)

REVIEWABLE_STATUSES = {"enriched", "pending_review"}

EDITABLE_METADATA_FIELDS = {
    "title",
    "therapeutic_area",
    "brand",
    "indication",
    "year",
    "summary",
    "key_findings",
    "methodology",
    "data_sources",
    "geographic_scope",
    "recommendations",
}

DEFAULT_ALLOWED_THERAPEUTIC_AREAS = {
    "CRM",
    "IMM",
    "ONC",
    "HEM",
    "NS",
    "RLT",
    "RENAL",
    "RESP",
    "DERMA",
    "OPHTHA",
}


def _json_safe(value: Any) -> Any:
    """Convert DynamoDB Decimal values into JSON-safe values."""
    if isinstance(value, list):
        return [_json_safe(item) for item in value]

    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}

    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)

    return value


def _allowed_therapeutic_areas() -> set:
    """Read allowed TA list from env, fallback to common kNOW TA codes."""
    raw = os.environ.get("ALLOWED_THERAPEUTIC_AREAS", "").strip()

    if raw:
        return {item.strip() for item in raw.split(",") if item.strip()}

    return DEFAULT_ALLOWED_THERAPEUTIC_AREAS


def _is_l4(user: Dict) -> bool:
    return user.get("role") == "L4"
  
def _is_l3_or_l4(user: Dict) -> bool:
    return user.get("role") in ("L3", "L4")

def _can_access(record: Dict, user: Dict) -> bool:
    if user.get("role") == "L4":
        return True

    if user.get("role") == "L3" and record.get("uploaded_by") == user.get("user_id"):
        return True

    return False


def _get_upload(upload_id: str) -> Dict:
    table = dynamodb.Table(UPLOADS_TABLE)
    resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
    return resp.get("Item")


def _validate_metadata_updates(body: Dict) -> Dict:
    """Validate and return only editable metadata fields from request body."""
    updates = {}

    for field in EDITABLE_METADATA_FIELDS:
        if field in body:
            updates[field] = body[field]

    if "year" in updates:
        year = str(updates["year"]).strip()

        if not re.fullmatch(r"\d{4}", year):
            raise ValueError("year must be in YYYY format")

        updates["year"] = year

    if "therapeutic_area" in updates:
        ta = str(updates["therapeutic_area"]).strip()

        if ta not in _allowed_therapeutic_areas():
            raise ValueError("Invalid therapeutic_area")

        updates["therapeutic_area"] = ta

    return updates


def _log_audit(upload_id: str, user: Dict, action: str, details: Dict = None) -> None:
    """Best-effort audit logging."""
    try:
        audit_table = dynamodb.Table(AUDIT_TABLE)
        ts = now_iso()

        audit_table.put_item(Item={
            "PK": f"AUDIT#{upload_id}",
            "SK": f"{ts}#{action}",
            "upload_id": upload_id,
            "action": action,
            "user_id": user.get("user_id"),
            "user_email": user.get("email", ""),
            "timestamp": ts,
            "details": details or {},
        })
    except Exception:
        logger.warning("Failed to write review audit log", exc_info=True)


def get_review(event: Dict, user: Dict) -> Dict:
    """Get upload details for review form.

    Returns:
    {
      upload_id,
      file_name,
      category,
      status,
      extracted_metadata,
      file_hash,
      duplicate_info
    }
    """
    
    upload_id = extract_upload_id(event)
    
    if not _is_l3_or_l4(user):
        return response(403, {"error": "Requires L3 or L4 role"})

    if not upload_id:
        return response(400, {"error":  UPLOAD_ID_REQUIRED_ERROR})

    record = _get_upload(upload_id)

    if not record:
        return response(404, {"error": UPLOAD_NOT_FOUND_ERROR})

    if not _can_access(record, user):
        return response(403, {"error": "Access denied"})

    if record.get("status") not in REVIEWABLE_STATUSES:
        return response(409, {"error": f"Cannot review upload in status: {record.get('status')}"})

    return response(200, _json_safe({
        "upload_id": upload_id,
        "user_id": record.get("uploaded_by", ""),
        "file_name": record.get("file_name"),
        "category": record.get("category"),
        "status": record.get("status"),
        "extracted_metadata": record.get("extracted_metadata") or {},
        "file_hash": record.get("file_hash"),
        "duplicate_info": record.get("duplicate_info"),
    }))


def _parse_json_body(event: Dict):
    try:
        return json.loads(event.get("body") or "{}"), None
    except json.JSONDecodeError:
        return {}, response(400, {"error": "Invalid JSON body"})


def _validate_update_review_request(upload_id: str, user: Dict):
    if not _is_l3_or_l4(user):
        return response(403, {"error": "Requires L3 or L4 role"})

    if not upload_id:
        return response(400, {"error": UPLOAD_ID_REQUIRED_ERROR})

    return None


def _validate_update_review_record(record: Dict, user: Dict):
    if not record:
        return response(404, {"error": UPLOAD_NOT_FOUND_ERROR})

    if record.get("status") not in REVIEWABLE_STATUSES:
        return response(409, {"error": f"Cannot update review in status: {record.get('status')}"})

    if not _can_access(record, user):
        return response(403, {"error": "Access denied"})

    return None


def _normalize_category_update(body: Dict):
    if "category" not in body:
        return None

    category = str(body.get("category", "")).upper()

    if category not in VALID_CATEGORIES:
        return response(400, {
            "error": f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
        })

    body["category"] = category
    return None


def _build_review_update_kwargs(
    upload_id: str,
    current_metadata: Dict,
    body: Dict,
    has_category_update: bool,
) -> Dict:
    update_expression = "SET extracted_metadata = :metadata, updated_at = :updated_at"

    expression_values = {
        ":metadata": current_metadata,
        ":updated_at": now_iso(),
    }

    update_kwargs = {
        "Key": {"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        "UpdateExpression": update_expression,
        "ExpressionAttributeValues": expression_values,
    }

    if has_category_update:
        update_kwargs["UpdateExpression"] += ", #category = :category"
        update_kwargs["ExpressionAttributeNames"] = {"#category": "category"}
        expression_values[":category"] = body["category"]

    return update_kwargs

def update_review(event: Dict, user: Dict) -> Dict:
    """Update metadata during review."""
    upload_id = extract_upload_id(event)

    validation_error = _validate_update_review_request(upload_id, user)
    if validation_error:
        return validation_error

    body, parse_error = _parse_json_body(event)
    if parse_error:
        return parse_error

    record = _get_upload(upload_id)

    record_error = _validate_update_review_record(record, user)
    if record_error:
        return record_error

    category_error = _normalize_category_update(body)
    if category_error:
        return category_error

    try:
        metadata_updates = _validate_metadata_updates(body)
    except ValueError as exc:
        return response(400, {"error": str(exc)})

    has_category_update = "category" in body

    if not metadata_updates and not has_category_update:
        return response(400, {"error": "No valid fields provided"})

    current_metadata = record.get("extracted_metadata") or {}
    current_metadata.update(metadata_updates)

    update_kwargs = _build_review_update_kwargs(
        upload_id=upload_id,
        current_metadata=current_metadata,
        body=body,
        has_category_update=has_category_update,
    )

    table = dynamodb.Table(UPLOADS_TABLE)
    table.update_item(**update_kwargs)

    updated_fields = list(metadata_updates.keys()) + (["category"] if has_category_update else [])

    _log_audit(
        upload_id=upload_id,
        user=user,
        action="update_review",
        details={"updated_fields": updated_fields},
    )

    return response(200, {
        "upload_id": upload_id,
        "updated": True,
    })


def send_for_review(event: Dict, user: Dict) -> Dict:
    """L3 sends enriched upload for L4 review.

    Requirements:
    1. Verify status is enriched
    2. Verify caller is owner
    3. Update status to pending_review
    4. Email can be added in Sprint 4
    5. Log audit trail
    """
    upload_id = extract_upload_id(event)
    
    if user.get("role") != "L3":
        return response(403, {"error": "Only L3 can send own uploads for review"})

    if not upload_id:
        return response(400, {"error": UPLOAD_ID_REQUIRED_ERROR})

    record = _get_upload(upload_id)

    if not record:
        return response(404, {"error": "Upload not found"})

    if record.get("status") != "enriched":
        return response(409, {"error": f"Cannot send for review in status: {record.get('status')}"})

    if record.get("uploaded_by") != user.get("user_id"):
        return response(403, {"error": "Only the uploader can send this upload for review"})

    table = dynamodb.Table(UPLOADS_TABLE)
    table.update_item(
        Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        UpdateExpression="SET #s = :status, updated_at = :updated_at",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":status": "pending_review",
            ":updated_at": now_iso(),
        },
    )

    _log_audit(
        upload_id=upload_id,
        user=user,
        action="send_for_review",
        details={"status": "pending_review"},
    )

    return response(200, {
        "upload_id": upload_id,
        "status": "pending_review",
    })
