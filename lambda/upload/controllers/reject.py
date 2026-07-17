"""kNOW Upload Lambda — Reject controller."""
import json
import logging
from typing import Dict

from utils import (
    AUDIT_TABLE,
    UPLOADS_TABLE,
    dynamodb,
    extract_upload_id,
    now_iso,
    response,
)

logger = logging.getLogger(__name__)


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
        logger.warning("Failed to write reject audit log", exc_info=True)


def reject(event: Dict, user: Dict) -> Dict:
    """L4: Reject with reason — uploader can see reason and re-upload.

    Input:
    {
      "reason": "Incomplete data, please re-upload with correct classification"
    }

    Requirements:
    1. Verify caller is L4
    2. Verify status is "pending_review"
    3. Update kNOW-Uploads:
       - status = rejected
       - reject_reason = reason
       - reviewer_id = user_id
       - updated_at = now
    4. Email can be added in Sprint 4
    5. Log to audit trail
    """
    upload_id = extract_upload_id(event)

    if not upload_id:
        return response(400, {"error": "upload_id required"})

    if user.get("role") != "L4":
        return response(403, {"error": "Only L4 can reject uploads"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "Invalid JSON body"})

    reason = str(body.get("reason", "")).strip()

    if not reason:
        return response(400, {"error": "reason is required"})

    table = dynamodb.Table(UPLOADS_TABLE)
    resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
    record = resp.get("Item")

    if not record:
        return response(404, {"error": "Upload not found"})

    if record.get("status") != "pending_review":
        return response(409, {"error": f"Cannot reject upload in status: {record.get('status')}"})

    table.update_item(
        Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        UpdateExpression=(
            "SET #s = :status, "
            "reject_reason = :reason, "
            "reviewer_id = :reviewer_id, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":status": "rejected",
            ":reason": reason,
            ":reviewer_id": user.get("user_id"),
            ":updated_at": now_iso(),
        },
    )

    _log_audit(
        upload_id=upload_id,
        user=user,
        action="reject",
        details={"reason": reason},
    )

    return response(200, {
        "upload_id": upload_id,
        "status": "rejected",
        "reason": reason,
    })
