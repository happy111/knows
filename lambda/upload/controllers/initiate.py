"""kNOW Upload Lambda — Initiate Upload controller."""
import json
import uuid
from typing import Dict, Optional, Tuple

from utils import (
    KMS_KEY_ARN,
    MAX_FILE_SIZE_MB,
    METADATA_TABLE,
    PRESIGNED_URL_EXPIRY,
    UPLOAD_S3_BUCKET,
    UPLOADS_TABLE,
    VALID_CATEGORIES,
    dynamodb,
    now_iso,
    response,
    s3,
)

ACTIVE_DUPLICATE_STATUSES = {
    "uploading",
    "processing",
    "enriching",
    "enriched",
    "pending_review",
    "duplicate_detected",
}


def _scan_first_matching_upload(file_name: str) -> Optional[Dict]:
    """Check kNOW-Uploads for same filename in an active pipeline state."""
    table = dynamodb.Table(UPLOADS_TABLE)
    last_key = None

    while True:
        scan_kwargs = {
            "FilterExpression": (
                "file_name = :fn AND #s IN "
                "(:uploading, :processing, :enriching, :enriched, :pending_review, :duplicate_detected)"
            ),
            "ExpressionAttributeNames": {
                "#s": "status",
            },
            "ExpressionAttributeValues": {
                ":fn": file_name,
                ":uploading": "uploading",
                ":processing": "processing",
                ":enriching": "enriching",
                ":enriched": "enriched",
                ":pending_review": "pending_review",
                ":duplicate_detected": "duplicate_detected",
            },
        }

        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key

        resp = table.scan(**scan_kwargs)
        items = resp.get("Items", [])

        if items:
            items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            return items[0]

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return None


def _scan_first_published_duplicate(file_name: str) -> Optional[Dict]:
    """Check kNOW-Metadata for same filename that is not deleted."""
    table = dynamodb.Table(METADATA_TABLE)
    last_key = None

    while True:
        scan_kwargs = {
            "FilterExpression": "file_name = :fn AND #s <> :deleted",
            "ExpressionAttributeNames": {
                "#s": "status",
            },
            "ExpressionAttributeValues": {
                ":fn": file_name,
                ":deleted": "deleted",
            },
        }

        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key

        resp = table.scan(**scan_kwargs)
        items = resp.get("Items", [])

        if items:
            items.sort(key=lambda item: item.get("published_at", item.get("created_at", "")), reverse=True)
            return items[0]

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return None


def _get_metadata_document(document_id: str) -> Optional[Dict]:
    """Get one metadata document by id."""
    if not document_id:
        return None

    table = dynamodb.Table(METADATA_TABLE)
    resp = table.get_item(Key={"document_id": document_id})
    return resp.get("Item")


def _as_bool(value) -> bool:
    """Normalize bool-like request values."""
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}

    return bool(value)

def _duplicate_warning_response(file_name: str, published: Dict) -> Dict:
    """Return pre-upload duplicate warning for published metadata duplicate."""
    return response(200, {
        "duplicate_detected": True,
        "duplicate_source": "published",
        "message": f"A published document named '{file_name}' already exists.",
        "existing_document_id": published.get("document_id"),
        "existing_file_name": published.get("file_name"),
        "existing_title": published.get("title", ""),
        "existing_published_at": published.get("published_at", ""),
        "existing_uploaded_by": published.get("uploaded_by", ""),
        "existing_category": published.get("category", published.get("document_type", "")),
        "existing_therapeutic_area": published.get("therapeutic_area", ""),
        "allowed_actions": ["keep_both", "replace", "cancel"],
    })

def _validate_role(user: Dict) -> Optional[Dict]:
    """Validate caller role."""
    if user.get("role") not in ("L3", "L4"):
        return response(403, {"error": "Requires L3 or L4 role"})

    return None


def _parse_request_body(event: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Parse JSON request body."""
    try:
        return json.loads(event.get("body") or "{}"), None
    except json.JSONDecodeError:
        return None, response(400, {"error": "Invalid JSON body"})


def _build_upload_request(body: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Extract and normalize upload request fields."""
    try:
        file_size = int(body.get("file_size", 0))
    except (TypeError, ValueError):
        return None, response(400, {"error": "file_size must be an integer"})

    upload_request = {
        "file_name": body.get("file_name"),
        "file_size": file_size,
        "content_type": body.get("content_type", "application/octet-stream"),
        "category": str(body.get("category", "")).upper(),
        "skip_duplicate_check": _as_bool(body.get("skip_duplicate_check", False)),
        "replace_document_id": body.get("replace_document_id"),
    }

    return upload_request, None


def _validate_upload_request(upload_request: Dict) -> Optional[Dict]:
    """Validate upload request fields."""
    file_name = upload_request["file_name"]
    file_size = upload_request["file_size"]
    category = upload_request["category"]

    if not file_name:
        return response(400, {"error": "file_name is required"})

    if file_size <= 0:
        return response(400, {"error": "file_size must be greater than 0"})

    if category not in VALID_CATEGORIES:
        return response(400, {
            "error": f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
        })

    if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        return response(400, {"error": f"File exceeds {MAX_FILE_SIZE_MB}MB limit"})

    return None


def _active_duplicate_error(file_name: str) -> Optional[Dict]:
    """Return 409 response if same filename is already active in upload pipeline."""
    active = _scan_first_matching_upload(file_name)

    if not active:
        return None

    return response(409, {
        "error": "duplicate_in_progress",
        "message": f"'{file_name}' is already active in the upload pipeline.",
        "duplicate_source": "in_progress",
        "existing_upload_id": active.get("upload_id"),
        "existing_status": active.get("status"),
        "existing_file_name": active.get("file_name"),
        "uploaded_by": active.get("uploaded_by"),
        "allowed_actions": [],
    })


def _resolve_duplicate_choice(skip_duplicate_check: bool,replace_document_id: Optional[str],) -> Tuple[Optional[str], bool, Optional[Dict]]:
    """Resolve duplicate user choice from initiate request.

    Returns:
        duplicate_resolution, skip_duplicate_check, error_response
    """
    if replace_document_id:
        replacement_doc = _get_metadata_document(replace_document_id)

        if not replacement_doc or replacement_doc.get("status") == "deleted":
            return None, skip_duplicate_check, response(404, {
                "error": "replace_document_id does not reference an existing non-deleted document"
            })

        return "replace", True, None

    if skip_duplicate_check:
        return "keep_both", True, None

    return None, False, None


def _published_duplicate_warning(file_name: str,skip_duplicate_check: bool,replace_document_id: Optional[str],) -> Optional[Dict]:
    """Return published duplicate warning if user has not already chosen an action."""
    if skip_duplicate_check or replace_document_id:
        return None

    published = _scan_first_published_duplicate(file_name)

    if not published:
        return None

    return _duplicate_warning_response(file_name, published)


def _generate_presigned_upload(s3_key: str,content_type: str,) -> Tuple[str, Dict]:
    """Generate S3 presigned URL and required upload headers."""
    s3_params = {
        "Bucket": UPLOAD_S3_BUCKET,
        "Key": s3_key,
        "ContentType": content_type,
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": KMS_KEY_ARN,
    }

    presigned_url = s3.generate_presigned_url(
        "put_object",
        Params=s3_params,
        ExpiresIn=PRESIGNED_URL_EXPIRY,
    )

    upload_headers = {
        "Content-Type": content_type,
        "x-amz-server-side-encryption": "aws:kms",
        "x-amz-server-side-encryption-aws-kms-key-id": KMS_KEY_ARN,
    }

    return presigned_url, upload_headers


def _build_upload_item(upload_id: str,s3_key: str,upload_request: Dict,user: Dict,duplicate_resolution: Optional[str],skip_duplicate_check: bool,) -> Dict:
    """Build kNOW-Uploads DynamoDB item."""
    now = now_iso()

    return {
        "PK": f"UPLOAD#{upload_id}",
        "SK": "META",
        "upload_id": upload_id,
        "file_name": upload_request["file_name"],
        "file_size": upload_request["file_size"],
        "content_type": upload_request["content_type"],
        "category": upload_request["category"],
        "s3_key": s3_key,
        "status": "uploading",
        "uploaded_by": user["user_id"],
        "uploader_name": user.get("name") or user.get("email") or user.get("user_id"),
        "progress": {
            "percentage": 0,
            "phase": "upload",
            "current_step": "uploading",
            "message": "Uploading file...",
        },
        "duplicate_info": None,
        "duplicate_resolution": duplicate_resolution,
        "skip_duplicate_check": skip_duplicate_check,
        "replace_document_id": upload_request["replace_document_id"],
        "document_id": None,
        "created_at": now,
        "updated_at": now,
    }


def _save_upload_item(item: Dict) -> None:
    """Persist upload item."""
    table = dynamodb.Table(UPLOADS_TABLE)
    table.put_item(Item=item)


def _upload_success_response(upload_id: str,s3_key: str,presigned_url: str,upload_headers: Dict,duplicate_resolution: Optional[str],replace_document_id: Optional[str],) -> Dict:
    """Build successful initiate-upload response."""
    return response(200, {
        "upload_id": upload_id,
        "presigned_url": presigned_url,
        "s3_key": s3_key,
        "expires_in": PRESIGNED_URL_EXPIRY,
        "upload_headers": upload_headers,
        "duplicate_resolution": duplicate_resolution,
        "replace_document_id": replace_document_id,
    })

def initiate_upload(event: Dict, user: Dict) -> Dict:
    """Initiate upload — returns presigned URL for client-side S3 upload.

    Duplicate behavior:
    1. Always block if same file_name exists in kNOW-Uploads active states.
    2. If same file_name exists in kNOW-Metadata and is not deleted:
       - return duplicate warning before upload starts
       - frontend can re-call with skip_duplicate_check=true for keep both
       - frontend can re-call with replace_document_id for replace
    """
    role_error = _validate_role(user)
    if role_error:
        return role_error

    body, parse_error = _parse_request_body(event)
    if parse_error:
        return parse_error

    upload_request, build_error = _build_upload_request(body)
    if build_error:
        return build_error

    validation_error = _validate_upload_request(upload_request)
    if validation_error:
        return validation_error

    active_duplicate_error = _active_duplicate_error(upload_request["file_name"])
    if active_duplicate_error:
        return active_duplicate_error

    duplicate_resolution, skip_duplicate_check, duplicate_choice_error = _resolve_duplicate_choice(
        upload_request["skip_duplicate_check"],
        upload_request["replace_document_id"],
    )
    if duplicate_choice_error:
        return duplicate_choice_error

    published_warning = _published_duplicate_warning(
        upload_request["file_name"],
        skip_duplicate_check,
        upload_request["replace_document_id"],
    )
    if published_warning:
        return published_warning

    upload_id = str(uuid.uuid4())
    s3_key = f"uploads/{upload_id}/{upload_request['file_name']}"

    presigned_url, upload_headers = _generate_presigned_upload(
        s3_key,
        upload_request["content_type"],
    )

    upload_item = _build_upload_item(
        upload_id=upload_id,
        s3_key=s3_key,
        upload_request=upload_request,
        user=user,
        duplicate_resolution=duplicate_resolution,
        skip_duplicate_check=skip_duplicate_check,
    )

    _save_upload_item(upload_item)

    return _upload_success_response(
        upload_id=upload_id,
        s3_key=s3_key,
        presigned_url=presigned_url,
        upload_headers=upload_headers,
        duplicate_resolution=duplicate_resolution,
        replace_document_id=upload_request["replace_document_id"],
    )
