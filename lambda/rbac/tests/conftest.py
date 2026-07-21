import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest
from moto import mock_aws


UPLOAD_DIR = Path(__file__).resolve().parents[1]
if str(UPLOAD_DIR) not in sys.path:
    sys.path.insert(0, str(UPLOAD_DIR))


REGION = "us-east-1"

UPLOAD_BUCKET = "know-dev-uploads"
UPLOADS_TABLE = "know-uploads-dev"
METADATA_TABLE = "know-metadata-dev"
AUDIT_TABLE = "know-audit-trail-dev"

ENRICHMENT_FUNCTION = "novartis-know-dev-document-enrichment"
AILENS_TRIGGER_LAMBDA_ARN = (
    "arn:aws:lambda:us-east-1:970547336770:function:"
    "novartis-know-dev-ailens-pipeline-trigger"
)


L3_USER = {
    "user_id": "l3.user@novartis.com",
    "email": "l3.user@novartis.com",
    "role": "L3",
}

OTHER_L3_USER = {
    "user_id": "other.l3@novartis.com",
    "email": "other.l3@novartis.com",
    "role": "L3",
}

L4_USER = {
    "user_id": "l4.user@novartis.com",
    "email": "l4.user@novartis.com",
    "role": "L4",
}


def parse_body(resp):
    body = resp.get("body", {})
    if isinstance(body, str):
        return json.loads(body)
    return body


def api_event(method, path, body=None, query=None):
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": query or {},
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body or {}),
    }


def upload_path(upload_id, suffix):
    return f"/api/upload/{upload_id}/{suffix}"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_REGION", REGION)

    monkeypatch.setenv("UPLOAD_S3_BUCKET", UPLOAD_BUCKET)
    monkeypatch.setenv("KNOW_UPLOADS_TABLE", UPLOADS_TABLE)
    monkeypatch.setenv("KNOW_METADATA_TABLE", METADATA_TABLE)
    monkeypatch.setenv("KNOW_AUDIT_TRAIL_TABLE", AUDIT_TABLE)
    monkeypatch.setenv("ENRICHMENT_FUNCTION", ENRICHMENT_FUNCTION)
    monkeypatch.setenv("AILENS_TRIGGER_LAMBDA_ARN", AILENS_TRIGGER_LAMBDA_ARN)
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "100")
    monkeypatch.setenv("PRESIGNED_URL_EXPIRY", "3600")
    monkeypatch.setenv("ALLOWED_THERAPEUTIC_AREAS", "CRM,IMM,ONC,HEM,NS,RLT")


@pytest.fixture
def aws_stack(monkeypatch):
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        s3 = boto3.client("s3", region_name=REGION)

        uploads_table = dynamodb.create_table(
            TableName=UPLOADS_TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "uploaded_by", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "uploaded_by-index",
                    "KeySchema": [
                        {"AttributeName": "uploaded_by", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "status-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        metadata_table = dynamodb.create_table(
            TableName=METADATA_TABLE,
            KeySchema=[
                {"AttributeName": "document_id", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "document_id", "AttributeType": "S"},
                {"AttributeName": "kb_status", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "kb-status-index",
                    "KeySchema": [
                        {"AttributeName": "kb_status", "KeyType": "HASH"},
                        {"AttributeName": "document_id", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        audit_table = dynamodb.create_table(
            TableName=AUDIT_TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        s3.create_bucket(Bucket=UPLOAD_BUCKET)

        import controllers.initiate as initiate
        import controllers.complete as complete
        import controllers.publish as publish
        import controllers.review as review
        import controllers.reject as reject
        import controllers.duplicate as duplicate
        import controllers.delete as delete
        import controllers.queries as queries
        import services.publish_service as publish_service

        lambda_client_mock = Mock()

        modules = [
            initiate,
            complete,
            publish,
            review,
            reject,
            duplicate,
            delete,
            queries,
            publish_service,
        ]

        for module in modules:
            if hasattr(module, "dynamodb"):
                monkeypatch.setattr(module, "dynamodb", dynamodb)

            if hasattr(module, "s3"):
                monkeypatch.setattr(module, "s3", s3)

            if hasattr(module, "lambda_client"):
                monkeypatch.setattr(module, "lambda_client", lambda_client_mock)

            if hasattr(module, "UPLOADS_TABLE"):
                monkeypatch.setattr(module, "UPLOADS_TABLE", UPLOADS_TABLE)

            if hasattr(module, "METADATA_TABLE"):
                monkeypatch.setattr(module, "METADATA_TABLE", METADATA_TABLE)

            if hasattr(module, "AUDIT_TABLE"):
                monkeypatch.setattr(module, "AUDIT_TABLE", AUDIT_TABLE)

            if hasattr(module, "UPLOAD_S3_BUCKET"):
                monkeypatch.setattr(module, "UPLOAD_S3_BUCKET", UPLOAD_BUCKET)

            if hasattr(module, "ENRICHMENT_FUNCTION"):
                monkeypatch.setattr(module, "ENRICHMENT_FUNCTION", ENRICHMENT_FUNCTION)

            if hasattr(module, "AILENS_TRIGGER_LAMBDA_ARN"):
                monkeypatch.setattr(module, "AILENS_TRIGGER_LAMBDA_ARN", AILENS_TRIGGER_LAMBDA_ARN)

        yield {
            "dynamodb": dynamodb,
            "s3": s3,
            "uploads_table": uploads_table,
            "metadata_table": metadata_table,
            "audit_table": audit_table,
            "lambda_client": lambda_client_mock,
            "modules": {
                "initiate": initiate,
                "complete": complete,
                "publish": publish,
                "review": review,
                "reject": reject,
                "duplicate": duplicate,
                "delete": delete,
                "queries": queries,
                "publish_service": publish_service,
            },
        }


def seed_upload(
    table,
    upload_id="upload-1",
    status="enriched",
    uploaded_by=None,
    category="MR",
    file_name="report.pdf",
    document_id=None,
    replace_document_id=None,
    created_at="2026-05-28T10:00:00Z",
    updated_at=None,
    s3_key=None,
    extracted_metadata=None,
    duplicate_info=None,
):
    uploaded_by = uploaded_by or L3_USER["user_id"]
    updated_at = updated_at or created_at
    s3_key = s3_key or f"uploads/{upload_id}/{file_name}"

    if duplicate_info is None and status == "duplicate_detected":
        duplicate_info = {
            "existing_document_id": "existing-doc-1",
            "file_name": file_name,
        }

    item = {
        "PK": f"UPLOAD#{upload_id}",
        "SK": "META",
        "upload_id": upload_id,
        "file_name": file_name,
        "file_size": 1234,
        "content_type": "application/pdf",
        "category": category,
        "s3_key": s3_key,
        "status": status,
        "uploaded_by": uploaded_by,
        "progress": {
            "percentage": 75,
            "phase": "enrichment",
            "current_step": "extracting",
            "message": "Extracting metadata",
        },
        "duplicate_info": duplicate_info,
        "replace_document_id": replace_document_id,
        "document_id": document_id,
        "file_hash": "sha256:test",
        "extracted_metadata": extracted_metadata or {
            "title": "Test Report",
            "therapeutic_area": "CRM",
            "brand": "Entresto",
            "indication": "Heart Failure",
            "year": "2026",
            "summary": "Summary",
            "key_findings": "Findings",
            "methodology": "Methodology",
            "data_sources": "Data sources",
            "geographic_scope": "Global",
            "recommendations": "Recommendations",
        },
        "created_at": created_at,
        "updated_at": updated_at,
    }

    table.put_item(Item=item)
    return item
