"""kNOW Upload Lambda — Query controllers: list, KPIs, progress."""
from decimal import Decimal
from typing import Any, Dict, List

from utils import (
    METADATA_TABLE,
    UPLOADS_TABLE,
    dynamodb,
    extract_upload_id,
    response,
)

KNOWN_UPLOAD_STATUSES = [
    "uploading",
    "processing",
    "enriching",
    "enriched",
    "pending_review",
    "duplicate_detected",
    "extraction_failed",
    "rejected",
    "published",
    "discarded",
    "deleted",
]

VALID_ACTIVE_CARDS = {"approved", "processing", "pendingReview", "errors"}

PROCESSING_STATUSES = {"uploading", "processing", "enriching"}
ERROR_STATUSES = {"duplicate_detected", "extraction_failed", "rejected"}
APPROVED_STATUSES = {"published"}
L4_OWN_PENDING_REVIEW_STATUSES = {"enriched", "enriching"}


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


def _query_params(event: Dict) -> Dict:
    return event.get("queryStringParameters") or {}


def _is_l4(user: Dict) -> bool:
    return user.get("role") == "L4"


def _is_own_upload(item: Dict, user: Dict) -> bool:
    return item.get("uploaded_by") == user.get("user_id")


def _is_processing_item(item: Dict, user: Dict) -> bool:
    return (
        item.get("status") in PROCESSING_STATUSES
        and _is_own_upload(item, user)
    )


def _is_error_item(item: Dict, user: Dict) -> bool:
    return (
        item.get("status") in ERROR_STATUSES
        and _is_own_upload(item, user)
    )


def _is_approved_item(item: Dict, user: Dict) -> bool:
    if item.get("status") not in APPROVED_STATUSES:
        return False

    if _is_l4(user):
        return True

    return _is_own_upload(item, user)


def _is_l3_pending_review_item(item: Dict, user: Dict) -> bool:
    return (
        item.get("status") == "enriched"
        and _is_own_upload(item, user)
    )


def _is_l4_pending_review_item(item: Dict, user: Dict) -> bool:
    status = item.get("status")

    return (
        status == "pending_review"
        or (
            status in L4_OWN_PENDING_REVIEW_STATUSES
            and _is_own_upload(item, user)
        )
    )


def _is_pending_review_item(item: Dict, user: Dict) -> bool:
    if _is_l4(user):
        return _is_l4_pending_review_item(item, user)

    return _is_l3_pending_review_item(item, user)


def _filter_by_active_card(items: List[Dict], active_card: str, user: Dict) -> List[Dict]:
    if not active_card:
        return items

    if active_card == "processing":
        return [item for item in items if _is_processing_item(item, user)]

    if active_card == "pendingReview":
        return [item for item in items if _is_pending_review_item(item, user)]

    if active_card == "errors":
        return [item for item in items if _is_error_item(item, user)]

    if active_card == "approved":
        return [item for item in items if _is_approved_item(item, user)]

    return items


def _scan_all_uploads(table) -> List[Dict]:
    """Temporary scan-based query to avoid GSI dependency."""
    items: List[Dict] = []
    last_key = None

    while True:
        scan_kwargs = {}

        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key

        resp = table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return [
        item for item in items
        if item.get("status") in KNOWN_UPLOAD_STATUSES
    ]


def _query_l3_uploads(table, user_id: str) -> List[Dict]:
    """L3 sees own uploads only."""
    items = _scan_all_uploads(table)

    return [
        item for item in items
        if item.get("uploaded_by") == user_id
    ]


def _query_l4_all_uploads(table) -> List[Dict]:
    """L4 sees all uploads."""
    return _scan_all_uploads(table)


def _first_value(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return ""


def _project_upload(record: Dict) -> Dict:
    metadata = record.get("extracted_metadata") or {}

    return _json_safe({
        "upload_id": record.get("upload_id", ""),
        "document_id": record.get("document_id", ""),
        "user_id": record.get("uploaded_by", ""),
        "status": record.get("status", ""),
        "project_number": _first_value(
            metadata.get("project_number"),
            metadata.get("projectNumber"),
            record.get("project_number"),
        ),
        "file_name": record.get("file_name", ""),
        "project_summary": _first_value(
            metadata.get("project_summary"),
            metadata.get("summary"),
            record.get("project_summary"),
        ),
        "therapeutic_area": _first_value(
            metadata.get("therapeutic_area"),
            record.get("therapeutic_area"),
        ),
        "brand": _first_value(
            metadata.get("brand"),
            record.get("brand"),
        ),
        "indication": _first_value(
            metadata.get("indication"),
            record.get("indication"),
        ),
        "healthcare_provider": _first_value(
            metadata.get("healthcare_provider"),
            metadata.get("healthcareProvider"),
            metadata.get("hcp"),
            record.get("healthcare_provider"),
        ),
    })


def list_uploads(event: Dict, user: Dict) -> Dict:
    """List uploads by activeCard.

    L3:
      - own uploads only
      - pendingReview card shows enriched

    L4:
      - all uploads
      - pendingReview card shows pending_review
    """
    params = _query_params(event)
    
    active_card = params.get("activeCard", "")

    if active_card and active_card not in VALID_ACTIVE_CARDS:
        return response(400, {
            "error": "activeCard must be one of: approved, processing, pendingReview, errors"
        })

    try:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 20))
    except ValueError:
        return response(400, {"error": "page and page_size must be integers"})

    page = max(page, 1)
    page_size = max(min(page_size, 100), 1)

    table = dynamodb.Table(UPLOADS_TABLE)

    if _is_l4(user):
        items = _query_l4_all_uploads(table)
    else:
        items = _query_l3_uploads(table, user.get("user_id"))

    items = _filter_by_active_card(items, active_card, user)

    items.sort(key=lambda item: item.get("created_at", ""), reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size

    return response(200, {
        "uploads": [_project_upload(item) for item in items[start:end]],
        "total": total,
        "page": page,
        "page_size": page_size,
        "activeCard": active_card,
    })


def _is_indexed_published(record: Dict) -> bool:
    """Approved means published and indexed in metadata table."""
    if record.get("status") != "published":
        return False

    document_id = record.get("document_id")
    if not document_id:
        return False

    metadata_table = dynamodb.Table(METADATA_TABLE)
    resp = metadata_table.get_item(Key={"document_id": document_id})
    metadata = resp.get("Item") or {}

    return metadata.get("kb_status") == "indexed"


def get_kpis(event: Dict, user: Dict) -> Dict:
    """KPI cards.

    L3:
      pending_review count = enriched

    L4:
      pending_review count = pending_review
    """
    table = dynamodb.Table(UPLOADS_TABLE)

    if _is_l4(user):
        items = _query_l4_all_uploads(table)
    else:
        items = _query_l3_uploads(table, user.get("user_id"))

    kpis = {
        "processing": 0,
        "pending_review": 0,
        "errors": 0,
        "approved": 0,
    }

    for item in items:
        if _is_processing_item(item, user):
            kpis["processing"] += 1
        elif _is_pending_review_item(item, user):
            kpis["pending_review"] += 1
        elif _is_error_item(item, user):
            kpis["errors"] += 1
        elif _is_approved_item(item, user):
            kpis["approved"] += 1

    return response(200, kpis)


def get_progress(event: Dict, user: Dict = None) -> Dict:
    """Get progress data for frontend progress bar polling.

    The Lambda router calls handlers as handler(event, user), so this function
    must accept user even if it does not currently need it.
    """
    upload_id = extract_upload_id(event)

    if not upload_id:
        return response(400, {"error": "upload_id required"})

    table = dynamodb.Table(UPLOADS_TABLE)

    try:
        resp = table.get_item(Key={"PK": f"UPLOAD#{upload_id}", "SK": "META"})
        record = resp.get("Item")
    except Exception as e:
        return response(500, {
            "error": "Failed to read upload progress",
            "details": str(e),
        })

    if not record:
        return response(404, {"error": "Upload not found"})

    status = record.get("status", "")
    progress = record.get("progress") or {}

    payload = {
        "upload_id": upload_id,
        "status": status,
        "progress": {
            "percentage": progress.get("percentage", 0),
            "phase": progress.get("phase", ""),
            "current_step": progress.get("current_step", ""),
            "message": progress.get("message", ""),
        },
        "is_ready_for_review": status == "enriched",
    }

    if status == "duplicate_detected":
        duplicate_info = record.get("duplicate_info") or {}
        payload["duplicate_info"] = duplicate_info
        payload["duplicate_source"] = duplicate_info.get("duplicate_source", "")
        payload["allowed_actions"] = duplicate_info.get("allowed_actions", [])

    return response(200, _json_safe(payload))
