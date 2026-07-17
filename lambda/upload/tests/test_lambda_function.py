"""Unit tests for upload lambda_function.py — routing logic."""
import json
import os
import sys
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
os.environ.setdefault("AILENS_TRIGGER_LAMBDA_ARN", "arn:aws:lambda:us-east-1:123:function:ailens")
os.environ.setdefault("MAX_FILE_SIZE_MB", "100")
os.environ.setdefault("PRESIGNED_URL_EXPIRY", "3600")


def _event(method, path, body=None, user_claims=None):
    """Build a minimal API Gateway proxy event."""
    claims = user_claims or {
        "email": "test@novartis.com",
        "custom:role": "L3",
    }
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body) if body else None,
        "queryStringParameters": {},
        "headers": {"Content-Type": "application/json"},
        "requestContext": {
            "authorizer": {"claims": claims},
        },
    }


def _parse_response(resp):
    """Parse the response body from JSON string."""
    body = resp.get("body", "{}")
    return json.loads(body) if isinstance(body, str) else body


# ===========================================================================
# Route: POST /upload — initiate_upload
# ===========================================================================
@patch("lambda_function.initiate_upload")
@patch("lambda_function.get_user")
def test_route_post_upload(mock_get_user, mock_initiate):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_initiate.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload")
    result = lambda_handler(event, None)
    mock_initiate.assert_called_once()
    assert result["statusCode"] == 200


# ===========================================================================
# Route: POST /upload/complete — complete_upload
# ===========================================================================
@patch("lambda_function.complete_upload")
@patch("lambda_function.get_user")
def test_route_post_upload_complete(mock_get_user, mock_complete):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_complete.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload/complete")
    result = lambda_handler(event, None)
    mock_complete.assert_called_once()
    assert result["statusCode"] == 200


# ===========================================================================
# Route: GET /upload/list — list_uploads
# ===========================================================================
@patch("lambda_function.list_uploads")
@patch("lambda_function.get_user")
def test_route_get_upload_list(mock_get_user, mock_list):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_list.return_value = {"statusCode": 200, "body": "[]"}

    from lambda_function import lambda_handler

    event = _event("GET", "/api/upload/list")
    result = lambda_handler(event, None)
    mock_list.assert_called_once()
    assert result["statusCode"] == 200


# ===========================================================================
# Route: GET /upload/kpis — get_kpis
# ===========================================================================
@patch("lambda_function.get_kpis")
@patch("lambda_function.get_user")
def test_route_get_upload_kpis(mock_get_user, mock_kpis):
    mock_get_user.return_value = {"user_id": "u1", "role": "L4"}
    mock_kpis.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("GET", "/api/upload/kpis")
    result = lambda_handler(event, None)
    mock_kpis.assert_called_once()


# ===========================================================================
# Route: GET /progress — get_progress
# ===========================================================================
@patch("lambda_function.get_progress")
@patch("lambda_function.get_user")
def test_route_get_progress(mock_get_user, mock_progress):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_progress.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("GET", "/api/upload/abc123/progress")
    result = lambda_handler(event, None)
    mock_progress.assert_called_once()


# ===========================================================================
# Route: GET /review — get_review
# ===========================================================================
@patch("lambda_function.get_review")
@patch("lambda_function.get_user")
def test_route_get_review(mock_get_user, mock_review):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_review.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("GET", "/api/upload/abc123/review")
    result = lambda_handler(event, None)
    mock_review.assert_called_once()


# ===========================================================================
# Route: PUT /review — update_review
# ===========================================================================
@patch("lambda_function.update_review")
@patch("lambda_function.get_user")
def test_route_put_review(mock_get_user, mock_update):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_update.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("PUT", "/api/upload/abc123/review", body={"title": "Test"})
    result = lambda_handler(event, None)
    mock_update.assert_called_once()


# ===========================================================================
# Route: POST /publish — publish
# ===========================================================================
@patch("lambda_function.publish")
@patch("lambda_function.get_user")
def test_route_post_publish(mock_get_user, mock_publish):
    mock_get_user.return_value = {"user_id": "u1", "role": "L4"}
    mock_publish.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload/abc123/publish")
    result = lambda_handler(event, None)
    mock_publish.assert_called_once()


# ===========================================================================
# Route: POST /send-review — send_for_review
# ===========================================================================
@patch("lambda_function.send_for_review")
@patch("lambda_function.get_user")
def test_route_post_send_review(mock_get_user, mock_send):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_send.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload/abc123/send-review")
    result = lambda_handler(event, None)
    mock_send.assert_called_once()


# ===========================================================================
# Route: POST /approve — approve
# ===========================================================================
@patch("lambda_function.approve")
@patch("lambda_function.get_user")
def test_route_post_approve(mock_get_user, mock_approve):
    mock_get_user.return_value = {"user_id": "u1", "role": "L4"}
    mock_approve.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload/abc123/approve")
    result = lambda_handler(event, None)
    mock_approve.assert_called_once()


# ===========================================================================
# Route: POST /reject — reject
# ===========================================================================
@patch("lambda_function.reject")
@patch("lambda_function.get_user")
def test_route_post_reject(mock_get_user, mock_reject):
    mock_get_user.return_value = {"user_id": "u1", "role": "L4"}
    mock_reject.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload/abc123/reject")
    result = lambda_handler(event, None)
    mock_reject.assert_called_once()


# ===========================================================================
# Route: POST /resolve-duplicate — resolve_duplicate
# ===========================================================================
@patch("lambda_function.resolve_duplicate")
@patch("lambda_function.get_user")
def test_route_post_resolve_duplicate(mock_get_user, mock_resolve):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_resolve.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload/abc123/resolve-duplicate")
    result = lambda_handler(event, None)
    mock_resolve.assert_called_once()


# ===========================================================================
# Route: POST /discard — discard
# ===========================================================================
@patch("lambda_function.discard")
@patch("lambda_function.get_user")
def test_route_post_discard(mock_get_user, mock_discard):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_discard.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload/abc123/discard")
    result = lambda_handler(event, None)
    mock_discard.assert_called_once()


# ===========================================================================
# Route: DELETE /upload/{id} — delete_upload
# ===========================================================================
@patch("lambda_function.delete_upload")
@patch("lambda_function.get_user")
def test_route_delete_upload(mock_get_user, mock_delete):
    mock_get_user.return_value = {"user_id": "u1", "role": "L4"}
    mock_delete.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("DELETE", "/api/upload/abc123")
    result = lambda_handler(event, None)
    mock_delete.assert_called_once()


# ===========================================================================
# Route: 404 for unknown routes
# ===========================================================================
@patch("lambda_function.get_user")
def test_route_not_found_unknown_method(mock_get_user):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}

    from lambda_function import lambda_handler

    event = _event("PATCH", "/api/upload/abc123/review")
    result = lambda_handler(event, None)
    assert result["statusCode"] == 404
    body = _parse_response(result)
    assert "Not found" in body.get("error", "")


@patch("lambda_function.get_user")
def test_route_not_found_unknown_path(mock_get_user):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}

    from lambda_function import lambda_handler

    event = _event("GET", "/api/unknown/route")
    result = lambda_handler(event, None)
    assert result["statusCode"] == 404


# ===========================================================================
# Verify user is extracted and passed to controllers
# ===========================================================================
@patch("lambda_function.initiate_upload")
@patch("lambda_function.get_user")
def test_user_passed_to_controller(mock_get_user, mock_initiate):
    user = {"user_id": "somebody@novartis.com", "role": "L3", "email": "somebody@novartis.com"}
    mock_get_user.return_value = user
    mock_initiate.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("POST", "/api/upload")
    lambda_handler(event, None)
    mock_initiate.assert_called_once_with(event, user)


# ===========================================================================
# HTTP method from requestContext fallback
# ===========================================================================
@patch("lambda_function.initiate_upload")
@patch("lambda_function.get_user")
def test_http_method_from_request_context(mock_get_user, mock_initiate):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_initiate.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = {
        "requestContext": {
            "http": {"method": "POST"},
            "authorizer": {"claims": {"email": "u1", "custom:role": "L3"}},
        },
        "rawPath": "/api/upload",
        "body": None,
        "queryStringParameters": {},
        "headers": {},
    }
    lambda_handler(event, None)
    mock_initiate.assert_called_once()


# ===========================================================================
# Path from rawPath fallback
# ===========================================================================
@patch("lambda_function.list_uploads")
@patch("lambda_function.get_user")
def test_path_from_raw_path(mock_get_user, mock_list):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_list.return_value = {"statusCode": 200, "body": "[]"}

    from lambda_function import lambda_handler

    event = {
        "requestContext": {
            "http": {"method": "GET"},
            "authorizer": {"claims": {"email": "u1", "custom:role": "L3"}},
        },
        "rawPath": "/api/upload/list",
        "body": None,
        "queryStringParameters": {},
        "headers": {},
    }
    lambda_handler(event, None)
    mock_list.assert_called_once()


# ===========================================================================
# Empty event fields — graceful handling
# ===========================================================================
@patch("lambda_function.get_user")
def test_empty_event_returns_404(mock_get_user):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}

    from lambda_function import lambda_handler

    event = {"headers": {}, "requestContext": {"authorizer": {"claims": {}}}}
    result = lambda_handler(event, None)
    assert result["statusCode"] == 404


# ===========================================================================
# GET /review does not match POST /review
# ===========================================================================
@patch("lambda_function.get_review")
@patch("lambda_function.update_review")
@patch("lambda_function.get_user")
def test_get_review_not_matched_by_put(mock_get_user, mock_update, mock_get):
    mock_get_user.return_value = {"user_id": "u1", "role": "L3"}
    mock_get.return_value = {"statusCode": 200, "body": "{}"}
    mock_update.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    event = _event("GET", "/api/upload/abc123/review")
    lambda_handler(event, None)
    mock_get.assert_called_once()
    mock_update.assert_not_called()


# ===========================================================================
# DELETE route only triggers on DELETE method
# ===========================================================================
@patch("lambda_function.delete_upload")
@patch("lambda_function.get_user")
def test_delete_route_only_on_delete_method(mock_get_user, mock_delete):
    mock_get_user.return_value = {"user_id": "u1", "role": "L4"}
    mock_delete.return_value = {"statusCode": 200, "body": "{}"}

    from lambda_function import lambda_handler

    # GET should NOT route to delete
    event = _event("GET", "/api/upload/abc123")
    result = lambda_handler(event, None)
    mock_delete.assert_not_called()
    assert result["statusCode"] == 404
