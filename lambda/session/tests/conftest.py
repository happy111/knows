"""Shared test fixtures for Session Lambda."""
import os
import sys
from unittest.mock import MagicMock

import pytest

# Add lambda root to path so controllers/, core/ resolve correctly
_LAMBDA_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(_LAMBDA_ROOT))

# Add shared module to path (backend/ is the parent of shared/)
_BACKEND_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
sys.path.insert(0, os.path.abspath(_BACKEND_ROOT))

# ---------------------------------------------------------------------------
# Mock aws_xray_sdk before any module imports it (may not be installed in CI)
# ---------------------------------------------------------------------------
if "aws_xray_sdk" not in sys.modules:
    sys.modules["aws_xray_sdk"] = MagicMock()
    sys.modules["aws_xray_sdk.core"] = MagicMock()

# ---------------------------------------------------------------------------
# Environment setup — must happen before any Lambda module is imported
# ---------------------------------------------------------------------------
os.environ.setdefault("SESSION_TABLE", "know-session-test")
os.environ.setdefault("SESSION_ACTIVITY_TABLE", "know-session-activity-test")
os.environ.setdefault("KNOW_METADATA_TABLE", "know-metadata-test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ["POWERTOOLS_TRACE_DISABLED"] = "true"
os.environ["POWERTOOLS_METRICS_DISABLED"] = "true"


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    """Bypass auth gate for all tests."""
    monkeypatch.setattr("lambda_function.extract_user_id", lambda event: "user@example.com")
    monkeypatch.setattr("lambda_function._get_user_role", lambda uid: ("L1", []))


@pytest.fixture
def mock_lambda_context():
    """Minimal mock Lambda context required by @logger.inject_lambda_context."""
    ctx = MagicMock()
    ctx.function_name = "session-test"
    ctx.function_version = "$LATEST"
    ctx.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:session-test"
    ctx.memory_limit_in_mb = "128"
    ctx.aws_request_id = "test-request-id"
    ctx.log_group_name = "/aws/lambda/session-test"
    ctx.log_stream_name = "test-stream"
    ctx.get_remaining_time_in_millis = lambda: 30000
    return ctx


@pytest.fixture
def make_event():
    """Factory for API Gateway events."""
    def _make(path="/sessions", method="POST", body=None, query_params=None, authorizer=None):
        default_authorizer = {"username": "user@example.com", "group": "test-group"}
        return {
            "httpMethod": method,
            "path": path,
            "requestContext": {
                "requestId": "test-req-123",
                "authorizer": authorizer if authorizer is not None else default_authorizer,
            },
            "body": __import__("json").dumps(body) if body else None,
            "queryStringParameters": query_params or {},
            "pathParameters": {},
        }
    return _make
