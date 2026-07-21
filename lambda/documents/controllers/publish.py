"""kNOW Upload Lambda — Publish and Approve controllers.

Shared do_publish helper via services/publish_service.py.
"""
from typing import Dict

from utils import (
    UPLOADS_TABLE,
    dynamodb,
    extract_upload_id,
    now_iso,
    response,
)
from services.publish_service import do_publish

  
def _can_publish(record: Dict, user: Dict) -> bool:
    if user.get("role") == "L4":
        return True

    if user.get("role") == "L3" and record.get("uploaded_by") == user.get("user_id"):
        return True

    return False
    


def publish(event: Dict, user: Dict) -> Dict:
    """L3/L4: Publish directly — document goes live, triggers AILENS pipeline.

    Requirements:
    1. Get upload record, verify status is "enriched" or "pending_review"
    2. Verify caller is owner or L4
    3. Delegate full publish orchestration to publish_service.do_publish()
    """
    upload_id = extract_upload_id(event)
    
    if user.get("role") not in ("L3", "L4"):
        return response(403, {"error": "Requires L3 or L4 role"})

    if not upload_id:
        return response(400, {"error": "upload_id required"})

    table = dynamodb.Table(UPLOADS_TABLE)
    resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
    record = resp.get("Item")

    if not record:
        return response(404, {"error": "Upload not found"})

    if record.get("status") not in ("enriched", "pending_review"):
        return response(409, {"error": f"Cannot publish upload in status: {record.get('status')}"})

    if not _can_publish(record, user):
        return response(403, {"error": "Access denied"})

    try:
        result = do_publish(record, user)
    except Exception as e:
        return response(500, {"error": f"Publish failed: {str(e)}"})

    return response(200, {
        "upload_id": upload_id,
        **result,
    })


def approve(event: Dict, user: Dict) -> Dict:
    """L4: Approve and publish.

    Requirements:
    1. Verify caller is L4
    2. Verify status is "pending_review"
    3. Store reviewer_id in upload record
    4. Delegate full publish orchestration to publish_service.do_publish()
    5. Approval email can be added in Sprint 4
    """
    upload_id = extract_upload_id(event)

    if not upload_id:
        return response(400, {"error": "upload_id required"})

    if user.get("role") != "L4":
        return response(403, {"error": "Only L4 can approve uploads"})

    table = dynamodb.Table(UPLOADS_TABLE)
    resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
    record = resp.get("Item")

    if not record:
        return response(404, {"error": "Upload not found"})

    if record.get("status") != "pending_review":
        return response(409, {"error": f"Cannot approve upload in status: {record.get('status')}"})

    now = now_iso()

    table.update_item(
        Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        UpdateExpression=(
            "SET reviewer_id = :reviewer_id, "
            "approved_at = :approved_at, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeValues={
            ":reviewer_id": user.get("user_id"),
            ":approved_at": now,
            ":updated_at": now,
        },
    )

    record["reviewer_id"] = user.get("user_id")
    record["approved_at"] = now
    record["updated_at"] = now

    result = do_publish(record, user)

    return response(200, {
        "upload_id": upload_id,
        "approved_by": user.get("user_id"),
        **result,
    })
