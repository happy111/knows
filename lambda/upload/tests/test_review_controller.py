"""Unit tests for controllers/review.py — get_review, update_review, send_for_review."""
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure upload lambda source is on path
UPLOAD_DIR = Path(__file__).resolve().parents[1]
if str(UPLOAD_DIR) not in sys.path:
    sys.path.insert(0, str(UPLOAD_DIR))

# Set env vars before importing modules
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("UPLOAD_S3_BUCKET", "know-dev-uploads")
os.environ.setdefault("KNOW_UPLOADS_TABLE", "know-uploads-dev")
os.environ.setdefault("KNOW_METADATA_TABLE", "know-metadata-dev")
os.environ.setdefault("KNOW_AUDIT_TRAIL_TABLE", "know-audit-trail-dev")
os.environ.setdefault("ENRICHMENT_FUNCTION", "novartis-know-dev-document-enrichment")
os.environ.setdefault("MAX_FILE_SIZE_MB", "100")
os.environ.setdefault("PRESIGNED_URL_EXPIRY", "3600")
os.environ.setdefault("ALLOWED_THERAPEUTIC_AREAS", "CRM,IMM,ONC,HEM,NS,RLT")


# Helpers
L3_USER = {"user_id": "l3.user@novartis.com", "email": "l3.user@novartis.com", "role": "L3"}
L4_USER = {"user_id": "l4.user@novartis.com", "email": "l4.user@novartis.com", "role": "L4"}
L2_USER = {"user_id": "l2.user@novartis.com", "email": "l2.user@novartis.com", "role": "L2"}
OTHER_L3 = {"user_id": "other.l3@novartis.com", "email": "other.l3@novartis.com", "role": "L3"}

UPLOAD_ID = "test-upload-001"


def _event(method, path, body=None):
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body) if body else None,
        "queryStringParameters": {},
        "headers": {"Content-Type": "application/json"},
        "requestContext": {"authorizer": {"claims": {}}},
    }


def _parse(resp):
    body = resp.get("body", "{}")
    return json.loads(body) if isinstance(body, str) else body


def _enriched_record(uploaded_by="l3.user@novartis.com", status="enriched"):
    return {
        "PK": f"UPLOAD#{UPLOAD_ID}",
        "SK": "META",
        "upload_id": UPLOAD_ID,
        "file_name": "report.pdf",
        "category": "MR",
        "status": status,
        "uploaded_by": uploaded_by,
        "extracted_metadata": {"title": "Existing Title"},
        "file_hash": "abc123hash",
        "duplicate_info": None,
    }


# ===========================================================================
# get_review tests
# ===========================================================================
class TestGetReview:
    @patch("controllers.review.dynamodb")
    def test_get_review_success(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import get_review

        event = _event("GET", f"/api/upload/{UPLOAD_ID}/review")
        result = get_review(event, L3_USER)
        assert result["statusCode"] == 200
        body = _parse(result)
        assert body["upload_id"] == UPLOAD_ID
        assert body["status"] == "enriched"

    @patch("controllers.review.dynamodb")
    def test_get_review_l4_access_any(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        record = _enriched_record(uploaded_by="someone-else@novartis.com")
        mock_table.get_item.return_value = {"Item": record}

        from controllers.review import get_review

        event = _event("GET", f"/api/upload/{UPLOAD_ID}/review")
        result = get_review(event, L4_USER)
        assert result["statusCode"] == 200

    @patch("controllers.review.dynamodb")
    def test_get_review_l2_forbidden(self, mock_ddb):
        from controllers.review import get_review

        event = _event("GET", f"/api/upload/{UPLOAD_ID}/review")
        result = get_review(event, L2_USER)
        assert result["statusCode"] == 403

    @patch("controllers.review.dynamodb")
    def test_get_review_missing_upload_id(self, mock_ddb):
        from controllers.review import get_review

        event = _event("GET", "/api/upload/")
        result = get_review(event, L3_USER)
        assert result["statusCode"] == 400

    @patch("controllers.review.dynamodb")
    def test_get_review_not_found(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {}

        from controllers.review import get_review

        event = _event("GET", f"/api/upload/{UPLOAD_ID}/review")
        result = get_review(event, L3_USER)
        assert result["statusCode"] == 404

    @patch("controllers.review.dynamodb")
    def test_get_review_access_denied_other_l3(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import get_review

        event = _event("GET", f"/api/upload/{UPLOAD_ID}/review")
        result = get_review(event, OTHER_L3)
        assert result["statusCode"] == 403

    @patch("controllers.review.dynamodb")
    def test_get_review_wrong_status(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        record = _enriched_record(status="uploading")
        mock_table.get_item.return_value = {"Item": record}

        from controllers.review import get_review

        event = _event("GET", f"/api/upload/{UPLOAD_ID}/review")
        result = get_review(event, L3_USER)
        assert result["statusCode"] == 409

    @patch("controllers.review.dynamodb")
    def test_get_review_pending_review_status_allowed(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        record = _enriched_record(status="pending_review")
        mock_table.get_item.return_value = {"Item": record}

        from controllers.review import get_review

        event = _event("GET", f"/api/upload/{UPLOAD_ID}/review")
        result = get_review(event, L3_USER)
        assert result["statusCode"] == 200


# ===========================================================================
# update_review tests
# ===========================================================================
class TestUpdateReview:
    @patch("controllers.review._log_audit")
    @patch("controllers.review.dynamodb")
    def test_update_review_success(self, mock_ddb, mock_audit):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review", body={"title": "New Title"})
        result = update_review(event, L3_USER)
        assert result["statusCode"] == 200
        body = _parse(result)
        assert body["updated"] is True
        mock_table.update_item.assert_called_once()

    @patch("controllers.review._log_audit")
    @patch("controllers.review.dynamodb")
    def test_update_review_category_change(self, mock_ddb, mock_audit):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review", body={"category": "CI"})
        result = update_review(event, L3_USER)
        assert result["statusCode"] == 200

    @patch("controllers.review.dynamodb")
    def test_update_review_invalid_category(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review", body={"category": "INVALID"})
        result = update_review(event, L3_USER)
        assert result["statusCode"] == 400

    @patch("controllers.review.dynamodb")
    def test_update_review_invalid_year(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review", body={"year": "20XX"})
        result = update_review(event, L3_USER)
        assert result["statusCode"] == 400
        body = _parse(result)
        assert "YYYY" in body["error"]

    @patch("controllers.review.dynamodb")
    def test_update_review_invalid_therapeutic_area(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review", body={"therapeutic_area": "BADTA"})
        result = update_review(event, L3_USER)
        assert result["statusCode"] == 400

    @patch("controllers.review.dynamodb")
    def test_update_review_no_valid_fields(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review", body={"unknown_field": "value"})
        result = update_review(event, L3_USER)
        assert result["statusCode"] == 400
        body = _parse(result)
        assert "No valid fields" in body["error"]

    @patch("controllers.review.dynamodb")
    def test_update_review_invalid_json_body(self, mock_ddb):
        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review")
        event["body"] = "{{invalid json"
        result = update_review(event, L3_USER)
        assert result["statusCode"] == 400

    @patch("controllers.review.dynamodb")
    def test_update_review_l2_forbidden(self, mock_ddb):
        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review", body={"title": "T"})
        result = update_review(event, L2_USER)
        assert result["statusCode"] == 403

    @patch("controllers.review.dynamodb")
    def test_update_review_wrong_status(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        record = _enriched_record(status="published")
        mock_table.get_item.return_value = {"Item": record}

        from controllers.review import update_review

        event = _event("PUT", f"/api/upload/{UPLOAD_ID}/review", body={"title": "T"})
        result = update_review(event, L3_USER)
        assert result["statusCode"] == 409


# ===========================================================================
# send_for_review tests
# ===========================================================================
class TestSendForReview:
    @patch("controllers.review._log_audit")
    @patch("controllers.review.dynamodb")
    def test_send_for_review_success(self, mock_ddb, mock_audit):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import send_for_review

        event = _event("POST", f"/api/upload/{UPLOAD_ID}/send-review")
        result = send_for_review(event, L3_USER)
        assert result["statusCode"] == 200
        body = _parse(result)
        assert body["status"] == "pending_review"
        mock_table.update_item.assert_called_once()

    @patch("controllers.review.dynamodb")
    def test_send_for_review_l4_forbidden(self, mock_ddb):
        from controllers.review import send_for_review

        event = _event("POST", f"/api/upload/{UPLOAD_ID}/send-review")
        result = send_for_review(event, L4_USER)
        assert result["statusCode"] == 403

    @patch("controllers.review.dynamodb")
    def test_send_for_review_not_owner(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": _enriched_record()}

        from controllers.review import send_for_review

        event = _event("POST", f"/api/upload/{UPLOAD_ID}/send-review")
        result = send_for_review(event, OTHER_L3)
        assert result["statusCode"] == 403

    @patch("controllers.review.dynamodb")
    def test_send_for_review_wrong_status(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        record = _enriched_record(status="pending_review")
        mock_table.get_item.return_value = {"Item": record}

        from controllers.review import send_for_review

        event = _event("POST", f"/api/upload/{UPLOAD_ID}/send-review")
        result = send_for_review(event, L3_USER)
        assert result["statusCode"] == 409

    @patch("controllers.review.dynamodb")
    def test_send_for_review_not_found(self, mock_ddb):
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {}

        from controllers.review import send_for_review

        event = _event("POST", f"/api/upload/{UPLOAD_ID}/send-review")
        result = send_for_review(event, L3_USER)
        assert result["statusCode"] == 404

    @patch("controllers.review.dynamodb")
    def test_send_for_review_missing_upload_id(self, mock_ddb):
        from controllers.review import send_for_review

        event = _event("POST", "/api/upload/")
        result = send_for_review(event, L3_USER)
        assert result["statusCode"] == 400


# ===========================================================================
# _json_safe helper tests
# ===========================================================================
class TestJsonSafe:
    def test_json_safe_decimal_integer(self):
        from controllers.review import _json_safe

        assert _json_safe(Decimal("42")) == 42
        assert isinstance(_json_safe(Decimal("42")), int)

    def test_json_safe_decimal_float(self):
        from controllers.review import _json_safe

        assert _json_safe(Decimal("3.14")) == 3.14
        assert isinstance(_json_safe(Decimal("3.14")), float)

    def test_json_safe_nested_dict(self):
        from controllers.review import _json_safe

        data = {"count": Decimal("5"), "values": [Decimal("1.5"), Decimal("2")]}
        result = _json_safe(data)
        assert result == {"count": 5, "values": [1.5, 2]}

    def test_json_safe_plain_values(self):
        from controllers.review import _json_safe

        assert _json_safe("hello") == "hello"
        assert _json_safe(42) == 42
        assert _json_safe(None) is None
