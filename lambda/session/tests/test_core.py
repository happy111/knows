"""Tests for core/ — config, clients, and response modules."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock


class TestCoreConfig:
    """Tests for core/__init__.py environment-variable loading."""

    def test_defaults_applied(self):
        import core
        assert core.SESSION_TABLE == "know-session-test"
        assert core.SESSION_ACTIVITY_TABLE == "know-session-activity-test"
        assert core.METADATA_TABLE == "know-metadata-test"
        assert core.AWS_REGION == "us-east-1"

    def test_env_override(self):
        with patch.dict(os.environ, {"SESSION_TABLE": "custom-table"}):
            import importlib
            import core
            importlib.reload(core)
            assert core.SESSION_TABLE == "custom-table"
        # restore
        with patch.dict(os.environ, {"SESSION_TABLE": "know-session-test"}):
            import importlib
            import core
            importlib.reload(core)


class TestBuildResponse:
    """Tests for core/response.py."""

    def test_status_code_preserved(self):
        from core.response import build_response
        resp = build_response(201, {"ok": True})
        assert resp["statusCode"] == 201

    def test_cors_headers_present(self):
        from core.response import build_response
        resp = build_response(200, {})
        assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
        assert "Content-Type" in resp["headers"]

    def test_body_is_json_string(self):
        from core.response import build_response
        resp = build_response(200, {"key": "val"})
        assert isinstance(resp["body"], str)
        assert json.loads(resp["body"]) == {"key": "val"}

    def test_default_serializer_handles_datetime(self):
        from datetime import datetime
        from core.response import build_response
        resp = build_response(200, {"ts": datetime(2026, 1, 1)})
        body = json.loads(resp["body"])
        assert "2026" in body["ts"]


class TestCoreClients:
    """Tests for core/clients.py."""

    @patch("core.clients.dynamodb")
    def test_get_session_table_creates_once(self, mock_ddb):
        import core.clients as c
        c._session_table = None
        tbl = MagicMock()
        mock_ddb.Table.return_value = tbl
        assert c.get_session_table() is tbl
        assert c.get_session_table() is tbl
        mock_ddb.Table.assert_called_once()

    @patch("core.clients.dynamodb")
    def test_get_activity_table_creates_once(self, mock_ddb):
        import core.clients as c
        c._activity_table = None
        tbl = MagicMock()
        mock_ddb.Table.return_value = tbl
        assert c.get_activity_table() is tbl
        assert c.get_activity_table() is tbl
        mock_ddb.Table.assert_called_once()
