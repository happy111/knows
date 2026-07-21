"""kNOW Upload Lambda — Delete and Discard controllers.

Business decision (28-May-2026):
- L3 sees DISCARD button in the same position where L4 sees REJECT PROJECT.
- DISCARD cancels the upload and removes from S3.
- L3 can discard own uploads (any non-published status).
- L4 can discard any upload.
"""
import json
from typing import Dict

from utils import (
    AILENS_TRIGGER_LAMBDA_ARN,
    METADATA_TABLE,
    UPLOAD_S3_BUCKET,
    UPLOADS_TABLE,
    dynamodb,
    lambda_client,
    s3,
    extract_upload_id,
    now_iso,
    response,
)


def discard(event: Dict, user: Dict) -> Dict:
    """Discard upload — L3 (own) or L4 (any). Removes file from S3, marks discarded.

    UI: L3 sees DISCARD button where L4 sees REJECT PROJECT on review page.
    Allowed from any non-published status (enriched, pending_review, duplicate_detected, etc.)

    Steps:
    1. Get upload record
    2. Verify status != "published" (can't discard published — use DELETE for that)
    3. Verify caller is owner (L3) or L4
    4. Delete file from S3: uploads/{upload_id}/{file_name}
    5. Update status = "discarded"
    6. Log audit trail
    """
    upload_id = extract_upload_id(event)
    if not upload_id:
        return response(400, {"error": "upload_id required"})

    table = dynamodb.Table(UPLOADS_TABLE)
    resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
    item = resp.get("Item")

    if not item:
        return response(404, {"error": "Upload not found"})

    if item.get("status") == "published":
        return response(409, {"error": "Cannot discard published document. Use DELETE instead."})

    if user["role"] not in ("L3", "L4"):
        return response(403, {"error": "Requires L3 or L4 role"})

    if user["role"] == "L3" and item.get("uploaded_by") != user["user_id"]:
        return response(403, {"error": "L3 can only discard own uploads"})

    # Delete file from S3
    s3_key = item.get("s3_key", f"uploads/{upload_id}/{item.get('file_name', '')}")
    try:
        s3.delete_object(Bucket=UPLOAD_S3_BUCKET, Key=s3_key)
    except Exception:
        pass  # Best effort — file may already be gone

    # Update status
    table.update_item(
        Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        UpdateExpression="SET #s = :s, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "discarded", ":t": now_iso()},
    )

    return response(200, {"upload_id": upload_id, "status": "discarded"})


def _get_upload_item(table, upload_id: str) -> Dict:
    resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
    return resp.get("Item")


def _validate_delete_permission(item: Dict, user: Dict, is_published: bool):
    if is_published and user.get("role") != "L4":
        return response(403, {"error": "Only L4 can delete published documents"})

    if is_published:
        return None

    if user.get("role") == "L3" and item.get("uploaded_by") != user.get("user_id"):
        return response(403, {"error": "L3 can only delete own uploads"})

    if user.get("role") not in ("L3", "L4"):
        return response(403, {"error": "Requires L3 or L4 role"})

    return None


def _delete_s3_object(s3_key: str) -> None:
    try:
        s3.delete_object(Bucket=UPLOAD_S3_BUCKET, Key=s3_key)
    except Exception:
        pass


def _update_metadata_deleted(document_id: str, user: Dict) -> None:
    meta_table = dynamodb.Table(METADATA_TABLE)

    try:
        meta_table.update_item(
            Key={"document_id": document_id},
            UpdateExpression="SET #s = :s, kb_status = :kb, deleted_at = :t, deleted_by = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "deleted",
                ":kb": "pending_removal",
                ":t": now_iso(),
                ":u": user.get("user_id", "unknown"),
            },
        )
    except Exception:
        pass


def _invoke_ailens_delete(document_id: str) -> None:
    if not AILENS_TRIGGER_LAMBDA_ARN:
        return

    try:
        lambda_client.invoke(
            FunctionName=AILENS_TRIGGER_LAMBDA_ARN,
            InvocationType="Event",
            Payload=json.dumps({
                "action": "delete",
                "document_id": document_id,
                "site_id": "know-upload",
            }),
        )
    except Exception:
        pass


def _delete_published_document(item: Dict, document_id: str, user: Dict) -> None:
    published_key = f"published/{document_id}/{item.get('file_name', '')}"
    _delete_s3_object(published_key)
    _update_metadata_deleted(document_id, user)
    _invoke_ailens_delete(document_id)


def _delete_staging_upload(item: Dict, upload_id: str) -> None:
    s3_key = item.get("s3_key", f"uploads/{upload_id}/{item.get('file_name', '')}")
    _delete_s3_object(s3_key)


def _mark_upload_deleted(table, upload_id: str, user: Dict) -> None:
    table.update_item(
        Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        UpdateExpression="SET #s = :s, updated_at = :t, deleted_by = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "deleted",
            ":t": now_iso(),
            ":u": user.get("user_id", "unknown"),
        },
    )
    
def delete_upload(event: Dict, user: Dict) -> Dict:
    """Delete upload/document."""
    upload_id = extract_upload_id(event)

    if not upload_id:
        return response(400, {"error": "upload_id required"})

    table = dynamodb.Table(UPLOADS_TABLE)
    item = _get_upload_item(table, upload_id)

    if not item:
        return response(404, {"error": "Upload not found"})

    is_published = item.get("status") == "published"

    permission_error = _validate_delete_permission(item, user, is_published)
    if permission_error:
        return permission_error

    document_id = item.get("document_id", "")

    if is_published and document_id:
        _delete_published_document(item, document_id, user)
    else:
        _delete_staging_upload(item, upload_id)

    _mark_upload_deleted(table, upload_id, user)

    return response(200, {
        "upload_id": upload_id,
        "status": "deleted",
        "document_id": document_id,
    })
