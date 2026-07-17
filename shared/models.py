"""DEPRECATED — This file is an OUTDATED reference. Do NOT use as source of truth.

Actual DDB schemas are defined by:
  - CDK: cdk_pipeline/cdk_pipeline/cdk_dynamodb.py (source of truth for table keys + GSIs)
  - Each Lambda's dal/ folder (source of truth for access patterns)

Known inaccuracies in this file (as of 8 Jun 2026):
  - METADATA_SCHEMA shows PK/SK composite, actual table uses simple key `document_id`
  - METADATA_SCHEMA lists SML category — removed (actual: MR, CI, IPST, PV, LT)
  - METADATA_SCHEMA uses field name "therapeutic_area" — CDK GSI uses "ta"
  - CHUNKS_SCHEMA — table removed in 2-KB approach (AILENS handles chunking)
  - UserRoles uses PK/SK in this file — actual CDK uses simple key `user_id`

Tables (actual):
  1. kNOW-UserRoles      — User identity, role level, TA entitlements
  2. kNOW-Metadata       — Document metadata (superset of AILENS)
  3. kNOW-Uploads        — Upload lifecycle tracking
  4. kNOW-Bookmarks      — Per-user bookmarks
  5. kNOW-Taxonomy       — TA/Brand/Indication/DocType hierarchy
  6. kNOW-Chunks         — KB pipeline chunk storage (pre-embedding)
  7. kNOW-AccessRequests — RBAC access request workflow
  8. kNOW-Notifications  — User notification records
  9. kNOW-Shares         — Document share history
"""

# ─── Common schema value type constants ───────────────────────────────────────
_PK_USER = "USER#{user_id}"
_PK_DOC = "DOC#{document_id}"
_TYPE_EMAIL = "str (email)"
_TYPE_UUID = "str (uuid)"
_TYPE_ISO_DATETIME = "str (ISO datetime)"
_TYPE_LIST_STR = "list[str]"

# ─── kNOW-UserRoles ───────────────────────────────────────────────────────────
# PK: USER#{email}  |  SK: ROLE
# GSI1: role_level (for admin listing)
# GSI2: display_name (for user search type-ahead)
USER_ROLES_SCHEMA = {
    "PK": _PK_USER,
    "SK": "ROLE",
    "user_id": _TYPE_EMAIL,
    "display_name": "str",
    "email": "str",
    "role_level": "str (L1|L2|L3|L4)",
    "ta_entitlements": f"{_TYPE_LIST_STR} (TAs user can access for IPST)",
    "managed_tas": f"{_TYPE_LIST_STR} (TAs this L4 admin manages)",
    "sharepoint_group": "str (for AILENS KB pre-filter)",
    "azure_ad_groups": f"{_TYPE_LIST_STR} (synced from AD)",
    "last_login": _TYPE_ISO_DATETIME,
    "created_at": _TYPE_ISO_DATETIME,
}

# ─── kNOW-Metadata ────────────────────────────────────────────────────────────
# PK: DOC#{document_id}  |  SK: META
# GSI1: category + upload_date (for filtered listing)
# GSI2: therapeutic_area + brand (for browse/filter)
METADATA_SCHEMA = {
    "PK": _PK_DOC,
    "SK": "META",
    "document_id": _TYPE_UUID,
    "title": "str",
    "file_name": "str",
    "s3_key": "str",
    "category": "str (MR|CI|SML|IPST)",
    "therapeutic_area": "str",
    "brand": "str",
    "indication": "str",
    "document_type": "str (extracted by enrichment)",
    "date_range": "str (year or date range)",
    "permissions_group": "str (SharePoint group for access control)",
    "uploaded_by": _TYPE_EMAIL,
    "upload_date": _TYPE_ISO_DATETIME,
    "file_size": "int (bytes)",
    "content_type": "str (MIME type)",
    "sha256_hash": "str (for duplicate detection)",
    "status": "str (published|deleted)",
    "kb_status": "str (pending|indexed|index_failed)",
    "chunks_indexed": "int",
    "is_restricted": "bool (IPST = true)",
    "is_deleted": "bool (soft delete flag)",
    "deleted_at": f"{_TYPE_ISO_DATETIME}, if deleted)",
    "ailens_site_id": "str (correlation with AILENS)",
    "ailens_file_id": "str (correlation with AILENS)",
}

# ─── kNOW-Uploads ─────────────────────────────────────────────────────────────
# PK: UPLOAD#{upload_id}  |  SK: META
# GSI1: uploaded_by + created_at (user's uploads)
# GSI2: status (for KPI aggregation)
UPLOADS_SCHEMA = {
    "PK": "UPLOAD#{upload_id}",
    "SK": "META",
    "upload_id": _TYPE_UUID,
    "file_name": "str",
    "file_size": "int",
    "content_type": "str",
    "category": "str (MR|CI|SML|IPST)",
    "s3_key": "str",
    "status": "str (uploading|processing|pending_review|published|rejected|extraction_failed|deleted)",
    "uploaded_by": _TYPE_EMAIL,
    "created_at": _TYPE_ISO_DATETIME,
    "updated_at": _TYPE_ISO_DATETIME,
    "extracted_metadata": "map (AI-extracted fields)",
    "reviewed_by": "str (L4 email, if reviewed)",
    "reviewed_at": _TYPE_ISO_DATETIME,
    "rejection_reason": "str",
    "error_message": "str",
    "sharepoint_path": "str (if written to SP)",
}

# ─── kNOW-Bookmarks ──────────────────────────────────────────────────────────
# PK: USER#{user_id}  |  SK: DOC#{document_id}
# GSI1: document_id (for batch bookmark check)
BOOKMARKS_SCHEMA = {
    "PK": _PK_USER,
    "SK": _PK_DOC,
    "user_id": "str",
    "document_id": "str",
    "bookmarked_at": _TYPE_ISO_DATETIME,
}

# ─── kNOW-Taxonomy ────────────────────────────────────────────────────────────
# PK: LEVEL#{level}  |  SK: {parent_id}#{name}
# GSI1: parent_id (for tree queries)
TAXONOMY_SCHEMA = {
    "PK": "LEVEL#{level}",
    "SK": "{parent_id}#{name}",
    "item_id": _TYPE_UUID,
    "name": "str",
    "parent_id": "str (parent item_id)",
    "level": "int (1-5)",
    "order": "int (display order)",
    "metadata": "map (optional extra fields)",
}

# ─── kNOW-Chunks ──────────────────────────────────────────────────────────────
# PK: DOC#{document_id}  |  SK: {chunk_id}
CHUNKS_SCHEMA = {
    "PK": _PK_DOC,
    "SK": "{document_id}#chunk_{index:04d}",
    "text": "str (chunk content)",
    "page_start": "int (nullable)",
    "page_end": "int (nullable)",
    "token_estimate": "int",
    "therapeutic_area": "str",
    "brand": "str",
    "indication": "str",
    "document_type": "str",
    "date_range": "str",
    "permissions_group": "str",
}

# ─── kNOW-AccessRequests ──────────────────────────────────────────────────────
# PK: REQUEST#{request_id}  |  SK: META
# GSI1: requester_id + created_at (user's requests)
# GSI2: status + requested_ta (admin view)
ACCESS_REQUESTS_SCHEMA = {
    "PK": "REQUEST#{request_id}",
    "SK": "META",
    "request_id": _TYPE_UUID,
    "requester_id": _TYPE_EMAIL,
    "requested_ta": "str",
    "justification": "str",
    "status": "str (pending|approved|rejected)",
    "reviewed_by": "str (L4 email)",
    "reviewed_at": _TYPE_ISO_DATETIME,
    "rejection_reason": "str",
    "created_at": _TYPE_ISO_DATETIME,
}

# ─── kNOW-Notifications ───────────────────────────────────────────────────────
# PK: USER#{user_id}  |  SK: NOTIF#{timestamp}#{notification_id}
# GSI1: user_id + is_read (unread count)
NOTIFICATIONS_SCHEMA = {
    "PK": _PK_USER,
    "SK": "NOTIF#{timestamp}#{notification_id}",
    "notification_id": _TYPE_UUID,
    "user_id": "str",
    "type": "str (new_document|review_ready|approved|rejected|access_granted)",
    "title": "str",
    "body": "str",
    "document_id": "str (nullable)",
    "is_read": "bool",
    "created_at": _TYPE_ISO_DATETIME,
}

# ─── kNOW-Subscriptions (part of Notifications table) ─────────────────────────
# PK: USER#{user_id}  |  SK: SUB#META
SUBSCRIPTIONS_SCHEMA = {
    "PK": _PK_USER,
    "SK": "SUB#META",
    "follow_ta": _TYPE_LIST_STR,
    "follow_brand": _TYPE_LIST_STR,
    "follow_indication": _TYPE_LIST_STR,
    "follow_function": _TYPE_LIST_STR,
}

# ─── kNOW-Shares ──────────────────────────────────────────────────────────────
# PK: DOC#{document_id}  |  SK: SHARE#{timestamp}#{share_id}
SHARES_SCHEMA = {
    "PK": _PK_DOC,
    "SK": "SHARE#{timestamp}#{share_id}",
    "share_id": _TYPE_UUID,
    "document_id": "str",
    "shared_by": _TYPE_EMAIL,
    "recipients": f"{_TYPE_LIST_STR} (emails)",
    "personal_note": "str",
    "include_chat_history": "bool",
    "mark_confidential": "bool",
    "shared_at": _TYPE_ISO_DATETIME,
}
