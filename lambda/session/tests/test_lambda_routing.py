"""Tests for session/lambda_function.py routing."""
import json
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _mock_auth():
    """Mock auth for all routing tests."""
    with patch("lambda_function.extract_user_id", return_value="user@example.com"), \
         patch("lambda_function._get_user_role", return_value=("L1", {"ta_access": []})):
        yield


class TestLambdaHandlerRouting:
    """Tests for lambda_function routing logic."""

    def test_options_returns_200(self, make_event, mock_lambda_context):
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions", "OPTIONS"), mock_lambda_context)
        assert response["statusCode"] == 200

    @patch("lambda_function.create_session")
    def test_routes_to_create_session(self, mock_ctrl, make_event, mock_lambda_context):
        mock_ctrl.return_value = {"statusCode": 201, "body": "{}"}
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions", "POST", body={"user_email": "u@e.com"}), mock_lambda_context)
        mock_ctrl.assert_called_once()
        assert response["statusCode"] == 201

    @patch("lambda_function.get_sessions_last_n")
    def test_routes_to_get_sessions_last_n(self, mock_ctrl, make_event, mock_lambda_context):
        mock_ctrl.return_value = {"statusCode": 200, "body": "{}"}
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions/last_n_days", "GET", query_params={"user_email": "u@e.com"}), mock_lambda_context)
        mock_ctrl.assert_called_once()

    @patch("lambda_function.get_session_by_id")
    def test_routes_to_get_session_by_id(self, mock_ctrl, make_event, mock_lambda_context):
        mock_ctrl.return_value = {"statusCode": 200, "body": "{}"}
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions/by_id", "GET", query_params={"user_email": "u@e.com", "session_id": "abc"}), mock_lambda_context)
        mock_ctrl.assert_called_once()

    @patch("lambda_function.update_session")
    def test_routes_to_update_session(self, mock_ctrl, make_event, mock_lambda_context):
        mock_ctrl.return_value = {"statusCode": 200, "body": "{}"}
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions/abc/users/u@e.com", "PUT", body={"attributes": {"name": "x"}}), mock_lambda_context)
        mock_ctrl.assert_called_once()

    @patch("lambda_function.delete_session")
    def test_routes_to_delete_session(self, mock_ctrl, make_event, mock_lambda_context):
        mock_ctrl.return_value = {"statusCode": 200, "body": "{}"}
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions", "DELETE", query_params={"user_email": "u@e.com", "session_id": "abc"}), mock_lambda_context)
        mock_ctrl.assert_called_once()

    @patch("lambda_function.get_session_activities")
    def test_routes_to_get_activities(self, mock_ctrl, make_event, mock_lambda_context):
        mock_ctrl.return_value = {"statusCode": 200, "body": "{}"}
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions/activities", "POST", body={"user_email": "u@e.com", "session_id": "abc"}), mock_lambda_context)
        mock_ctrl.assert_called_once()

    @patch("lambda_function.log_activity")
    def test_routes_to_log_activity(self, mock_ctrl, make_event, mock_lambda_context):
        mock_ctrl.return_value = {"statusCode": 200, "body": "{}"}
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions/log-user-activity", "POST", body={"session_id": "abc"}), mock_lambda_context)
        mock_ctrl.assert_called_once()

    @patch("lambda_function.get_session_documents")
    def test_routes_to_get_session_documents(self, mock_ctrl, make_event, mock_lambda_context):
        mock_ctrl.return_value = {"statusCode": 200, "body": "{}"}
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/sessions/documents", "GET", query_params={"doc_id": "d1,d2"}), mock_lambda_context)
        mock_ctrl.assert_called_once()

    def test_unknown_route_returns_404(self, make_event, mock_lambda_context):
        import lambda_function
        response = lambda_function.lambda_handler(make_event("/unknown", "PATCH"), mock_lambda_context)
        assert response["statusCode"] == 404
        body = json.loads(response["body"])
        assert body["error"]["code"] == "NOT_FOUND"


class TestLambdaHandlerAuth:
    """Tests for authentication branches in lambda_function."""

    def test_no_user_id_returns_401(self, make_event, mock_lambda_context):
        """Cover the 'if not user_id' branch returning 401."""
        with patch("lambda_function.extract_user_id", return_value=None), \
             patch("lambda_function._get_user_role", return_value=("L1", [])):
            import lambda_function
            response = lambda_function.lambda_handler(make_event("/sessions", "POST"), mock_lambda_context)
            assert response["statusCode"] == 401
            body = json.loads(response["body"])
            assert body["error"]["code"] == "NO_IDENTITY"
