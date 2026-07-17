"""kNOW Upload Lambda — Complete Upload controller."""
import json
from typing import Dict

from utils import (
    UPLOAD_S3_BUCKET,
    UPLOADS_TABLE,
    dynamodb,
    now_iso,
    response,
    s3,
)

IDEMPOTENT_COMPLETE_STATUSES = {
    "processing",
    "enriching",
    "enriched",
    "pending_review",
    "duplicate_detected",
    "extraction_failed",
    "published",
}

CONFLICT_COMPLETE_STATUSES = {
    "discarded",
    "deleted",
}


def complete_upload(event: Dict, user: Dict) -> Dict:
    """Client confirms upload complete — S3 event will trigger enrichment automatically.

    Input: {"upload_id": "uuid"}
    Note: Enrichment is triggered by S3 Event Notification on uploads/ prefix.
          This endpoint just confirms the file is in S3 and updates status.
    """
    if user.get("role") not in ("L3", "L4"):
        return response(403, {"error": "Requires L3 or L4 role"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "Invalid JSON body"})

    upload_id = body.get("upload_id")

    if not upload_id:
        return response(400, {"error": "upload_id required"})

    table = dynamodb.Table(UPLOADS_TABLE)
    resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
    record = resp.get("Item")

    if not record:
        return response(404, {"error": "Upload not found"})

    if user.get("role") == "L3" and record.get("uploaded_by") != user.get("user_id"):
        return response(403, {"error": "L3 can only complete own uploads"})

    current_status = record.get("status")

    if current_status in IDEMPOTENT_COMPLETE_STATUSES:
        return response(200, {
            "upload_id": upload_id,
            "status": current_status,
        })

    if current_status in CONFLICT_COMPLETE_STATUSES:
        return response(409, {
            "error": f"Cannot complete upload in status: {current_status}",
        })

    if current_status != "uploading":
        return response(409, {
            "error": f"Cannot complete upload in status: {current_status}",
        })

    # Verify file exists in S3
    try:
        s3.head_object(Bucket=UPLOAD_S3_BUCKET, Key=record["s3_key"])
    except s3.exceptions.ClientError:
        return response(400, {"error": "File not found in S3. Upload may have failed."})

    # Update status — S3 event notification triggers enrichment automatically
    table.update_item(
        Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        UpdateExpression=(
            "SET #s = :s, "
            "progress.percentage = :p, "
            "progress.phase = :ph, "
            "progress.current_step = :step, "
            "progress.message = :msg, "
            "updated_at = :ts"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "processing",
            ":p": 50,
            ":ph": "enrichment",
            ":step": "processing",
            ":msg": "Processing document...",
            ":ts": now_iso(),
        },
    )

    return response(200, {"upload_id": upload_id, "status": "processing"})
