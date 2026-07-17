import json

import pytest

from conftest import (
    L3_USER,
    L4_USER,
    OTHER_L3_USER,
    UPLOAD_BUCKET,
    api_event,
    parse_body,
    seed_upload,
    upload_path,
)


def test_initiate_valid(aws_stack):
    initiate = aws_stack["modules"]["initiate"]
    table = aws_stack["uploads_table"]

    event = api_event(
        "POST",
        "/api/upload",
        {
            "file_name": "report.pdf",
            "file_size": 1024,
            "content_type": "application/pdf",
            "category": "MR",
        },
    )

    resp = initiate.initiate_upload(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["upload_id"]
    assert body["presigned_url"]
    assert body["s3_key"].startswith("uploads/")
    assert body["expires_in"] == 3600

    item = table.get_item(
        Key={
            "PK": f"UPLOAD#{body['upload_id']}",
            "SK": "META",
        }
    ).get("Item")

    assert item is not None
    assert item["file_name"] == "report.pdf"
    assert item["category"] == "MR"
    assert item["status"] == "uploading"
    assert item["uploaded_by"] == L3_USER["user_id"]


def test_initiate_invalid_category(aws_stack):
    initiate = aws_stack["modules"]["initiate"]

    event = api_event(
        "POST",
        "/api/upload",
        {
            "file_name": "report.pdf",
            "file_size": 1024,
            "content_type": "application/pdf",
            "category": "SML",
        },
    )

    resp = initiate.initiate_upload(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 400
    assert "category" in body["error"]


def test_initiate_exceeds_size(aws_stack):
    initiate = aws_stack["modules"]["initiate"]

    event = api_event(
        "POST",
        "/api/upload",
        {
            "file_name": "large-report.pdf",
            "file_size": 101 * 1024 * 1024,
            "content_type": "application/pdf",
            "category": "MR",
        },
    )

    resp = initiate.initiate_upload(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 400
    assert "exceeds" in body["error"]


def test_complete_file_not_in_s3(aws_stack):
    complete = aws_stack["modules"]["complete"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="u-complete-missing",
        status="uploading",
        uploaded_by=L3_USER["user_id"],
    )

    event = api_event(
        "POST",
        "/api/upload/complete",
        {
            "upload_id": "u-complete-missing",
        },
    )

    resp = complete.complete_upload(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 400
    assert "File not found" in body["error"]


def test_list_l3_own_only(aws_stack):
    queries = aws_stack["modules"]["queries"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="own-1",
        uploaded_by=L3_USER["user_id"],
        created_at="2026-05-28T10:00:00Z",
    )
    seed_upload(
        table,
        upload_id="other-1",
        uploaded_by=OTHER_L3_USER["user_id"],
        created_at="2026-05-28T11:00:00Z",
    )

    event = api_event(
        "GET",
        "/api/upload/list",
        query={
            "page": "1",
            "page_size": "20",
        },
    )

    resp = queries.list_uploads(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["total"] == 1
    assert body["uploads"][0]["upload_id"] == "own-1"


def test_list_l4_sees_all(aws_stack):
    queries = aws_stack["modules"]["queries"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="own-1",
        uploaded_by=L3_USER["user_id"],
        created_at="2026-05-28T10:00:00Z",
    )
    seed_upload(
        table,
        upload_id="other-1",
        uploaded_by=OTHER_L3_USER["user_id"],
        created_at="2026-05-28T11:00:00Z",
    )

    event = api_event(
        "GET",
        "/api/upload/list",
        query={
            "page": "1",
            "page_size": "20",
        },
    )

    resp = queries.list_uploads(event, L4_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["total"] == 2

    upload_ids = {item["upload_id"] for item in body["uploads"]}
    assert upload_ids == {"own-1", "other-1"}


def test_kpis_correct_grouping(aws_stack):
    queries = aws_stack["modules"]["queries"]
    uploads_table = aws_stack["uploads_table"]
    metadata_table = aws_stack["metadata_table"]

    seed_upload(uploads_table, upload_id="u-uploading", status="uploading")
    seed_upload(uploads_table, upload_id="u-processing", status="processing")
    seed_upload(uploads_table, upload_id="u-enriching", status="enriching")
    seed_upload(uploads_table, upload_id="u-enriched", status="enriched")
    seed_upload(uploads_table, upload_id="u-pending", status="pending_review")
    seed_upload(uploads_table, upload_id="u-duplicate", status="duplicate_detected")
    seed_upload(uploads_table, upload_id="u-failed", status="extraction_failed")
    seed_upload(uploads_table, upload_id="u-rejected", status="rejected")
    seed_upload(
        uploads_table,
        upload_id="u-published-indexed",
        status="published",
        document_id="doc-indexed",
    )
    seed_upload(
        uploads_table,
        upload_id="u-published-pending",
        status="published",
        document_id="doc-pending",
    )

    metadata_table.put_item(
        Item={
            "document_id": "doc-indexed",
            "status": "published",
            "kb_status": "indexed",
        }
    )
    metadata_table.put_item(
        Item={
            "document_id": "doc-pending",
            "status": "published",
            "kb_status": "pending",
        }
    )

    event = api_event("GET", "/api/upload/kpis")
    resp = queries.get_kpis(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["processing"] == 3
    assert body["pending_review"] == 2
    assert body["errors"] == 3
    assert body["approved"] == 1


def test_progress_returns_percentage(aws_stack):
    queries = aws_stack["modules"]["queries"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="u-progress",
        status="processing",
        uploaded_by=L3_USER["user_id"],
    )

    event = api_event("GET", upload_path("u-progress", "progress"))
    resp = queries.get_progress(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["upload_id"] == "u-progress"
    assert body["status"] == "processing"
    assert body["progress"]["percentage"] == 75
    assert body["progress"]["phase"] == "enrichment"
    assert body["progress"]["current_step"] == "extracting"
    assert body["progress"]["message"] == "Extracting metadata"
    assert body["is_ready_for_review"] is False


def test_publish_moves_file(aws_stack):
    publish = aws_stack["modules"]["publish"]
    s3 = aws_stack["s3"]
    table = aws_stack["uploads_table"]

    record = seed_upload(
        table,
        upload_id="u-publish-move",
        status="enriched",
        uploaded_by=L3_USER["user_id"],
    )

    s3.put_object(
        Bucket=UPLOAD_BUCKET,
        Key=record["s3_key"],
        Body=b"pdf-data",
    )

    event = api_event("POST", upload_path("u-publish-move", "publish"))
    resp = publish.publish(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["status"] == "published"
    assert body["document_id"]

    new_key = f"published/{body['document_id']}/report.pdf"
    new_obj = s3.get_object(Bucket=UPLOAD_BUCKET, Key=new_key)

    assert new_obj["Body"].read() == b"pdf-data"

    with pytest.raises(Exception):
        s3.get_object(Bucket=UPLOAD_BUCKET, Key=record["s3_key"])


def test_publish_creates_metadata(aws_stack):
    publish = aws_stack["modules"]["publish"]
    s3 = aws_stack["s3"]
    uploads_table = aws_stack["uploads_table"]
    metadata_table = aws_stack["metadata_table"]

    record = seed_upload(
        uploads_table,
        upload_id="u-publish-meta",
        status="enriched",
        uploaded_by=L3_USER["user_id"],
        category="MR",
    )

    s3.put_object(
        Bucket=UPLOAD_BUCKET,
        Key=record["s3_key"],
        Body=b"pdf-data",
    )

    event = api_event("POST", upload_path("u-publish-meta", "publish"))
    resp = publish.publish(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200

    document_id = body["document_id"]
    metadata = metadata_table.get_item(
        Key={
            "document_id": document_id,
        }
    ).get("Item")

    assert metadata is not None
    assert metadata["document_id"] == document_id
    assert metadata["title"] == "Test Report"
    assert metadata["file_name"] == "report.pdf"
    assert metadata["file_type"] == "pdf"
    assert metadata["file_size_bytes"] == 1234
    assert metadata["s3_key"] == f"published/{document_id}/report.pdf"
    assert metadata["file_hash"] == "sha256:test"
    assert metadata["therapeutic_area"] == "CRM"
    assert metadata["brand"] == "Entresto"
    assert metadata["indication"] == "Heart Failure"
    assert metadata["document_type"] == "MR"
    assert metadata["year"] == "2026"
    assert metadata["category"] == "MR"
    assert metadata["is_restricted"] is False
    assert metadata["status"] == "published"
    assert metadata["kb_status"] == "pending"
    assert metadata["source"] == "user_upload"
    assert metadata["metadata_source"] == "ai_enrichment"
    assert metadata["uploaded_by"] == L3_USER["user_id"]
    assert metadata["published_by"] == L3_USER["user_id"]
    assert metadata["ailens_site_id"] == "know-upload"
    assert metadata["ailens_file_id"] == document_id


def test_publish_invokes_trigger(aws_stack):
    publish = aws_stack["modules"]["publish"]
    s3 = aws_stack["s3"]
    uploads_table = aws_stack["uploads_table"]
    lambda_client = aws_stack["lambda_client"]

    record = seed_upload(
        uploads_table,
        upload_id="u-publish-trigger",
        status="enriched",
        uploaded_by=L3_USER["user_id"],
        category="IPST",
    )

    s3.put_object(
        Bucket=UPLOAD_BUCKET,
        Key=record["s3_key"],
        Body=b"pdf-data",
    )

    event = api_event("POST", upload_path("u-publish-trigger", "publish"))
    resp = publish.publish(event, L3_USER)

    assert resp["statusCode"] == 200
    assert lambda_client.invoke.called

    call = lambda_client.invoke.call_args_list[-1]
    payload = json.loads(call.kwargs["Payload"])

    assert call.kwargs["InvocationType"] == "Event"
    assert payload["file_name"] == "report.pdf"
    assert payload["file_type"] == "pdf"
    assert payload["lens"] == "market_restricted"
    assert payload["permissions_groups"] == ["kNOW-IPST-CRM"]
    assert payload["site_id"] == "know-upload"
    assert payload["tags"]["therapeutic_area"] == "CRM"
    assert payload["tags"]["document_type"] == "IPST"


def test_publish_replace_mode(aws_stack):
    publish = aws_stack["modules"]["publish"]
    s3 = aws_stack["s3"]
    uploads_table = aws_stack["uploads_table"]
    metadata_table = aws_stack["metadata_table"]
    lambda_client = aws_stack["lambda_client"]

    old_document_id = "old-doc-123"
    old_key = f"published/{old_document_id}/old-report.pdf"

    s3.put_object(
        Bucket=UPLOAD_BUCKET,
        Key=old_key,
        Body=b"old-pdf-data",
    )

    metadata_table.put_item(
        Item={
            "document_id": old_document_id,
            "title": "Old Report",
            "file_name": "old-report.pdf",
            "s3_key": old_key,
            "status": "published",
            "kb_status": "indexed",
        }
    )

    record = seed_upload(
        uploads_table,
        upload_id="u-replace",
        status="enriched",
        uploaded_by=L3_USER["user_id"],
        replace_document_id=old_document_id,
    )

    s3.put_object(
        Bucket=UPLOAD_BUCKET,
        Key=record["s3_key"],
        Body=b"new-pdf-data",
    )

    event = api_event("POST", upload_path("u-replace", "publish"))
    resp = publish.publish(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["document_id"]

    old_metadata = metadata_table.get_item(
        Key={
            "document_id": old_document_id,
        }
    ).get("Item")

    assert old_metadata["status"] == "replaced"
    assert old_metadata["replaced_by"] == body["document_id"]

    with pytest.raises(Exception):
        s3.get_object(Bucket=UPLOAD_BUCKET, Key=old_key)

    delete_payloads = []

    for call in lambda_client.invoke.call_args_list:
        payload = json.loads(call.kwargs["Payload"])
        if payload.get("action") == "delete":
            delete_payloads.append(payload)

    assert delete_payloads
    assert delete_payloads[0]["document_id"] == old_document_id
    assert delete_payloads[0]["site_id"] == "know-upload"


def test_approve_l4_only(aws_stack):
    publish = aws_stack["modules"]["publish"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="u-approve-l4-only",
        status="pending_review",
        uploaded_by=L3_USER["user_id"],
    )

    event = api_event("POST", upload_path("u-approve-l4-only", "approve"))
    resp = publish.approve(event, L3_USER)

    assert resp["statusCode"] == 403


def test_reject_stores_reason(aws_stack):
    reject = aws_stack["modules"]["reject"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="u-reject",
        status="pending_review",
        uploaded_by=L3_USER["user_id"],
    )

    event = api_event(
        "POST",
        upload_path("u-reject", "reject"),
        {
            "reason": "Incomplete data",
        },
    )

    resp = reject.reject(event, L4_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["upload_id"] == "u-reject"
    assert body["status"] == "rejected"
    assert body["reason"] == "Incomplete data"

    item = table.get_item(
        Key={
            "PK": "UPLOAD#u-reject",
            "SK": "META",
        }
    ).get("Item")

    assert item["status"] == "rejected"
    assert item["reject_reason"] == "Incomplete data"
    assert item["reviewer_id"] == L4_USER["user_id"]


def test_resolve_dup_reinvokes_enrichment(aws_stack):
    duplicate = aws_stack["modules"]["duplicate"]
    table = aws_stack["uploads_table"]
    lambda_client = aws_stack["lambda_client"]

    seed_upload(
        table,
        upload_id="u-dup",
        status="duplicate_detected",
        uploaded_by=L3_USER["user_id"],
    )

    event = api_event(
        "POST",
        upload_path("u-dup", "resolve-duplicate"),
        {
            "action": "keep_both",
        },
    )

    resp = duplicate.resolve_duplicate(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["upload_id"] == "u-dup"
    assert body["action"] == "keep_both"
    assert body["status"] == "processing"

    item = table.get_item(
        Key={
            "PK": "UPLOAD#u-dup",
            "SK": "META",
        }
    ).get("Item")

    assert item["status"] == "processing"

    lambda_client.invoke.assert_called()

    call = lambda_client.invoke.call_args
    payload = json.loads(call.kwargs["Payload"])

    assert call.kwargs["InvocationType"] == "Event"
    assert payload["upload_id"] == "u-dup"
    assert payload["file_name"] == "report.pdf"
    assert payload["s3_key"] == "uploads/u-dup/report.pdf"
    assert payload["skip_duplicate_check"] is True


def test_resolve_dup_replace_stores_replace_document_id(aws_stack):
    duplicate = aws_stack["modules"]["duplicate"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="u-dup-replace",
        status="duplicate_detected",
        uploaded_by=L3_USER["user_id"],
    )

    event = api_event(
        "POST",
        upload_path("u-dup-replace", "resolve-duplicate"),
        {
            "action": "replace",
            "existing_document_id": "existing-doc-999",
        },
    )

    resp = duplicate.resolve_duplicate(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["action"] == "replace"
    assert body["status"] == "processing"

    item = table.get_item(
        Key={
            "PK": "UPLOAD#u-dup-replace",
            "SK": "META",
        }
    ).get("Item")

    assert item["status"] == "processing"
    assert item["replace_document_id"] == "existing-doc-999"


def test_discard_deletes_s3(aws_stack):
    delete = aws_stack["modules"]["delete"]
    s3 = aws_stack["s3"]
    table = aws_stack["uploads_table"]

    record = seed_upload(
        table,
        upload_id="u-discard",
        status="enriched",
        uploaded_by=L3_USER["user_id"],
    )

    s3.put_object(
        Bucket=UPLOAD_BUCKET,
        Key=record["s3_key"],
        Body=b"pdf-data",
    )

    event = api_event("POST", upload_path("u-discard", "discard"))
    resp = delete.discard(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["upload_id"] == "u-discard"
    assert body["status"] == "discarded"

    item = table.get_item(
        Key={
            "PK": "UPLOAD#u-discard",
            "SK": "META",
        }
    ).get("Item")

    assert item["status"] == "discarded"

    with pytest.raises(Exception):
        s3.get_object(Bucket=UPLOAD_BUCKET, Key=record["s3_key"])


def test_discard_published_fails(aws_stack):
    delete = aws_stack["modules"]["delete"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="u-discard-published",
        status="published",
        uploaded_by=L3_USER["user_id"],
        document_id="doc-published",
    )

    event = api_event("POST", upload_path("u-discard-published", "discard"))
    resp = delete.discard(event, L3_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 409
    assert "Cannot discard published" in body["error"]


def test_discard_l3_own_only(aws_stack):
    delete = aws_stack["modules"]["delete"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="u-other-discard",
        status="enriched",
        uploaded_by=OTHER_L3_USER["user_id"],
    )

    event = api_event("POST", upload_path("u-other-discard", "discard"))
    resp = delete.discard(event, L3_USER)

    assert resp["statusCode"] == 403


def test_delete_published_l4_cascades(aws_stack):
    delete = aws_stack["modules"]["delete"]
    s3 = aws_stack["s3"]
    uploads_table = aws_stack["uploads_table"]
    metadata_table = aws_stack["metadata_table"]
    lambda_client = aws_stack["lambda_client"]

    document_id = "doc-delete-123"
    published_key = f"published/{document_id}/report.pdf"

    seed_upload(
        uploads_table,
        upload_id="u-delete-published",
        status="published",
        uploaded_by=L3_USER["user_id"],
        document_id=document_id,
    )

    metadata_table.put_item(
        Item={
            "document_id": document_id,
            "title": "Delete Test",
            "file_name": "report.pdf",
            "s3_key": published_key,
            "status": "published",
            "kb_status": "indexed",
        }
    )

    s3.put_object(
        Bucket=UPLOAD_BUCKET,
        Key=published_key,
        Body=b"published-data",
    )

    event = api_event("DELETE", "/api/upload/u-delete-published")
    resp = delete.delete_upload(event, L4_USER)
    body = parse_body(resp)

    assert resp["statusCode"] == 200
    assert body["upload_id"] == "u-delete-published"
    assert body["status"] == "deleted"
    assert body["document_id"] == document_id

    upload_item = uploads_table.get_item(
        Key={
            "PK": "UPLOAD#u-delete-published",
            "SK": "META",
        }
    ).get("Item")

    assert upload_item["status"] == "deleted"
    assert upload_item["deleted_by"] == L4_USER["user_id"]

    metadata = metadata_table.get_item(
        Key={
            "document_id": document_id,
        }
    ).get("Item")

    assert metadata["status"] == "deleted"
    assert metadata["kb_status"] == "pending_removal"
    assert metadata["deleted_by"] == L4_USER["user_id"]

    with pytest.raises(Exception):
        s3.get_object(Bucket=UPLOAD_BUCKET, Key=published_key)

    lambda_client.invoke.assert_called()

    payload = json.loads(lambda_client.invoke.call_args.kwargs["Payload"])

    assert payload["action"] == "delete"
    assert payload["document_id"] == document_id
    assert payload["site_id"] == "know-upload"


def test_delete_published_l3_denied(aws_stack):
    delete = aws_stack["modules"]["delete"]
    table = aws_stack["uploads_table"]

    seed_upload(
        table,
        upload_id="u-delete-denied",
        status="published",
        uploaded_by=L3_USER["user_id"],
        document_id="doc-denied",
    )

    event = api_event("DELETE", "/api/upload/u-delete-denied")
    resp = delete.delete_upload(event, L3_USER)

    assert resp["statusCode"] == 403
