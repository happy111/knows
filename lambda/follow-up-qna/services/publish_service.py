"""kNOW Upload Lambda — Publish service.

Orchestrates the publish flow:
  S3 move + metadata record creation + AILENS pipeline trigger.

Used by both publish and approve flows.
"""
import json
import logging
import uuid
from decimal import Decimal
from typing import Dict, Tuple

from utils import KMS_KEY_ARN
from utils import (
    AILENS_TRIGGER_LAMBDA_ARN,
    AUDIT_TABLE,
    METADATA_TABLE,
    UPLOAD_S3_BUCKET,
    UPLOADS_TABLE,
    dynamodb,
    lambda_client,
    now_iso,
    s3,
)
from services.lens_routing import determine_lens_and_permissions

logger = logging.getLogger(__name__)

PUBLISHED_STATUS = "published"
PENDING_KB_STATUS = "pending"
PENDING_REMOVAL_KB_STATUS = "pending_removal"
REPLACED_STATUS = "replaced"
USER_UPLOAD_SOURCE = "user_upload"
AI_METADATA_SOURCE = "ai_enrichment"
AILENS_SITE_ID = "know-upload"
RESTRICTED_CATEGORIES = {"IPST", "PV", "LT"}


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types from DynamoDB."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj == int(obj) else float(obj)

        return super().default(obj)


def _first_value(value, default: str = "") -> str:
    """Return string value. If list, return first non-empty item."""
    if value is None or value == "":
        return default

    if isinstance(value, list):
        return _first_non_empty_list_value(value, default)

    return str(value)


def _first_non_empty_list_value(values, default: str = "") -> str:
    """Return first non-empty list item."""
    for item in values:
        if item not in (None, ""):
            return str(item)

    return default


def _file_extension(file_name: str) -> str:
    """Return file extension without dot."""
    return file_name.rsplit(".", 1)[-1] if "." in file_name else ""


def _new_document_context(record: Dict) -> Dict:
    """Build generated document context."""
    document_id = str(uuid.uuid4())
    file_name = record["file_name"]

    return {
        "document_id": document_id,
        "file_name": file_name,
        "file_extension": _file_extension(file_name),
        "new_s3_key": f"published/{document_id}/{file_name}",
    }


def _move_s3_object(source_key: str, destination_key: str) -> None:
    """Move file from upload staging prefix to published prefix."""
    s3.copy_object(
        Bucket=UPLOAD_S3_BUCKET,
        CopySource={"Bucket": UPLOAD_S3_BUCKET, "Key": source_key},
        Key=destination_key,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=KMS_KEY_ARN,
    )

    s3.delete_object(Bucket=UPLOAD_S3_BUCKET, Key=source_key)


def _metadata_field(metadata: Dict, field_name: str, default: str = "") -> str:
    """Read normalized metadata field safely."""
    return _first_value(metadata.get(field_name), default)


def _determine_routing(category: str, extracted_metadata: Dict) -> Tuple[str, str, list]:
    """Determine therapeutic area, AILENS lens, and permission groups."""
    therapeutic_area = _metadata_field(
        extracted_metadata,
        "therapeutic_area",
        "UNKNOWN",
    )

    lens, permissions_groups = determine_lens_and_permissions(
        category,
        therapeutic_area,
    )

    return therapeutic_area, lens, permissions_groups


def _build_metadata_record(
    record: Dict,
    user: Dict,
    document_context: Dict,
    therapeutic_area: str,
    now: str,
) -> Dict:
    """Build kNOW-Metadata item."""
    extracted_metadata = record.get("extracted_metadata", {})
    category = record["category"]
    replace_document_id = record.get("replace_document_id")

    return {
        "document_id": document_context["document_id"],
        "title": _metadata_field(extracted_metadata, "title", document_context["file_name"]),
        "file_name": document_context["file_name"],
        "file_type": document_context["file_extension"],
        "file_size_bytes": record.get("file_size", 0),
        "s3_key": document_context["new_s3_key"],
        "file_hash": record.get("file_hash") or "",
        "therapeutic_area": therapeutic_area,
        "brand": _metadata_field(extracted_metadata, "brand"),
        "indication": _metadata_field(extracted_metadata, "indication"),
        "document_type": category,
        "function": _metadata_field(extracted_metadata, "function"),
        "year": _metadata_field(extracted_metadata, "year"),
        "summary": _metadata_field(extracted_metadata, "summary"),
        "key_findings": _metadata_field(extracted_metadata, "key_findings"),
        "methodology": _metadata_field(extracted_metadata, "methodology"),
        "data_sources": _metadata_field(extracted_metadata, "data_sources"),
        "geographic_scope": _metadata_field(extracted_metadata, "geographic_scope"),
        "recommendations": _metadata_field(extracted_metadata, "recommendations"),
        "reviewed_metadata": extracted_metadata,
        "category": category,
        "is_restricted": category in RESTRICTED_CATEGORIES,
        "status": PUBLISHED_STATUS,
        "kb_status": PENDING_KB_STATUS,
        "source": USER_UPLOAD_SOURCE,
        "metadata_source": AI_METADATA_SOURCE,
        "uploaded_by": record.get("uploaded_by") or "",
        "published_by": user.get("user_id", "unknown"),
        "published_at": now,
        "created_at": record.get("created_at") or now,
        "ailens_site_id": AILENS_SITE_ID,
        "ailens_file_id": document_context["document_id"],
        "replaces_document_id": replace_document_id or "",
    }


def _save_metadata_record(metadata_record: Dict) -> None:
    """Persist kNOW-Metadata item."""
    metadata_table = dynamodb.Table(METADATA_TABLE)
    metadata_table.put_item(Item=metadata_record)


def _update_upload_published(
    upload_id: str,
    document_id: str,
    user: Dict,
    now: str,
) -> None:
    """Mark upload record as published."""
    uploads_table = dynamodb.Table(UPLOADS_TABLE)

    uploads_table.update_item(
        Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"},
        UpdateExpression=(
            "SET #s = :s, "
            "document_id = :did, "
            "published_at = :pa, "
            "published_by = :pb, "
            "updated_at = :ts"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": PUBLISHED_STATUS,
            ":did": document_id,
            ":pa": now,
            ":pb": user["user_id"],
            ":ts": now,
        },
    )


def _build_ailens_trigger_payload(
    record: Dict,
    document_context: Dict,
    therapeutic_area: str,
    lens: str,
    permissions_groups: list,
) -> Dict:
    """Build AILENS trigger payload for newly published document."""
    extracted_metadata = record.get("extracted_metadata", {})
    category = record["category"]

    return {
        "document_id": document_context["document_id"],
        "s3_uri": f"s3://{UPLOAD_S3_BUCKET}/{document_context['new_s3_key']}",
        "file_name": document_context["file_name"],
        "file_type": document_context["file_extension"],
        "file_size_bytes": record.get("file_size", 0),
        "lens": lens,
        "permissions_groups": permissions_groups,
        "tags": {
            "therapeutic_area": therapeutic_area,
            "brand": _metadata_field(extracted_metadata, "brand"),
            "indication": _metadata_field(extracted_metadata, "indication"),
            "document_type": category,
            "year": _metadata_field(extracted_metadata, "year"),
        },
        "site_id": AILENS_SITE_ID,
    }


def _invoke_ailens_publish(trigger_payload: Dict) -> None:
    """Invoke AILENS trigger for publish if configured."""
    if not AILENS_TRIGGER_LAMBDA_ARN:
        return

    lambda_client.invoke(
        FunctionName=AILENS_TRIGGER_LAMBDA_ARN,
        InvocationType="Event",
        Payload=json.dumps(trigger_payload, cls=DecimalEncoder),
    )


def _get_metadata_record(document_id: str) -> Dict:
    """Fetch metadata record by document id."""
    metadata_table = dynamodb.Table(METADATA_TABLE)

    try:
        resp = metadata_table.get_item(Key={"document_id": document_id})
        return resp.get("Item", {})
    except Exception:
        logger.warning("Could not retrieve old metadata for %s", document_id)
        return {}


def _mark_old_document_replaced(
    old_document_id: str,
    new_document_id: str,
) -> None:
    """Mark old metadata record as replaced."""
    metadata_table = dynamodb.Table(METADATA_TABLE)

    metadata_table.update_item(
        Key={"document_id": old_document_id},
        UpdateExpression=(
            "SET #s = :s, "
            "kb_status = :kb, "
            "replaced_by = :rb, "
            "updated_at = :ts"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": REPLACED_STATUS,
            ":kb": PENDING_REMOVAL_KB_STATUS,
            ":rb": new_document_id,
            ":ts": now_iso(),
        },
    )


def _delete_s3_object_if_present(s3_key: str) -> None:
    """Delete S3 object best-effort."""
    if not s3_key:
        return

    try:
        s3.delete_object(Bucket=UPLOAD_S3_BUCKET, Key=s3_key)
    except Exception:
        logger.warning("Could not delete old S3 object: %s", s3_key)


def _invoke_ailens_delete(document_id: str) -> None:
    """Invoke AILENS delete action for replaced document."""
    if not AILENS_TRIGGER_LAMBDA_ARN:
        return

    lambda_client.invoke(
        FunctionName=AILENS_TRIGGER_LAMBDA_ARN,
        InvocationType="Event",
        Payload=json.dumps({
            "action": "delete",
            "document_id": document_id,
            "site_id": AILENS_SITE_ID,
        }),
    )


def _handle_replacement(old_document_id: str, new_document_id: str) -> None:
    """Handle replacement of an existing document."""
    old_record = _get_metadata_record(old_document_id)

    if old_record:
        _mark_old_document_replaced(old_document_id, new_document_id)
        _delete_s3_object_if_present(old_record.get("s3_key", ""))

    _invoke_ailens_delete(old_document_id)


def _handle_replacement_if_needed(record: Dict, new_document_id: str) -> None:
    """Handle replacement only when replace_document_id exists."""
    replace_document_id = record.get("replace_document_id")

    if replace_document_id:
        _handle_replacement(replace_document_id, new_document_id)


def _log_audit(upload_id: str, document_id: str, user: Dict, action: str) -> None:
    """Log action to kNOW-AuditTrail table."""
    try:
        timestamp = now_iso()
        audit_table = dynamodb.Table(AUDIT_TABLE)

        audit_table.put_item(Item={
            "PK": f"AUDIT#{upload_id}",
            "SK": f"{timestamp}#{action}",
            "upload_id": upload_id,
            "document_id": document_id,
            "action": action,
            "user_id": user["user_id"],
            "user_email": user.get("email", ""),
            "timestamp": timestamp,
        })
    except Exception:
        logger.warning("Failed to log audit for %s/%s", upload_id, action)


def do_publish(record: Dict, user: Dict) -> Dict:
    """Execute the full publish workflow for an upload record."""
    upload_id = record["upload_id"]
    category = record["category"]
    source_s3_key = record["s3_key"]
    extracted_metadata = record.get("extracted_metadata", {})

    document_context = _new_document_context(record)
    now = now_iso()

    _move_s3_object(
        source_key=source_s3_key,
        destination_key=document_context["new_s3_key"],
    )

    therapeutic_area, lens, permissions_groups = _determine_routing(
        category,
        extracted_metadata,
    )

    metadata_record = _build_metadata_record(
        record=record,
        user=user,
        document_context=document_context,
        therapeutic_area=therapeutic_area,
        now=now,
    )

    _save_metadata_record(metadata_record)

    _update_upload_published(
        upload_id=upload_id,
        document_id=document_context["document_id"],
        user=user,
        now=now,
    )

    _handle_replacement_if_needed(
        record=record,
        new_document_id=document_context["document_id"],
    )

    trigger_payload = _build_ailens_trigger_payload(
        record=record,
        document_context=document_context,
        therapeutic_area=therapeutic_area,
        lens=lens,
        permissions_groups=permissions_groups,
    )

    _invoke_ailens_publish(trigger_payload)

    _log_audit(
        upload_id=upload_id,
        document_id=document_context["document_id"],
        user=user,
        action="publish",
    )

    return {
        "document_id": document_context["document_id"],
        "status": PUBLISHED_STATUS,
    }
