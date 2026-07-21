"""Tests for session controllers — business logic."""
import json
import time
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from botocore.exceptions import ClientError


class TestCreateSession:
    @patch("controllers.create_session.get_session_table")
    def test_success(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_get_table.return_value = mock_table

        from controllers.create_session import create_session
        event = make_event("/sessions", "POST", body={"user_id": "u@e.com"})
        response = create_session(event, "u@e.com")

        assert response["statusCode"] == 201
        body = json.loads(response["body"])
        assert body["status"] == "success"
        assert "session_id" in body
        mock_table.put_item.assert_called_once()

    @patch("controllers.create_session.get_session_table")
    def test_missing_group_still_creates(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_get_table.return_value = mock_table

        from controllers.create_session import create_session
        event = make_event("/sessions", "POST", body={}, authorizer={"username": "u@e.com"})
        response = create_session(event, "u@e.com")
        assert response["statusCode"] == 201


class TestGetSessionById:
    @patch("controllers.get_session_by_id.get_session_table")
    def test_found(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": {"session_id": "abc", "user_id": "u@e.com"}}
        mock_get_table.return_value = mock_table

        from controllers.get_session_by_id import get_session_by_id
        event = make_event("/sessions/by_id", "GET", query_params={"session_id": "abc"})
        response = get_session_by_id(event, "u@e.com")

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["session"]["session_id"] == "abc"

    @patch("controllers.get_session_by_id.get_session_table")
    def test_not_found(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_get_table.return_value = mock_table

        from controllers.get_session_by_id import get_session_by_id
        event = make_event("/sessions/by_id", "GET", query_params={"session_id": "nope"})
        response = get_session_by_id(event, "u@e.com")

        assert response["statusCode"] == 404
        body = json.loads(response["body"])
        assert body["error"]["code"] == "NOT_FOUND"

    def test_missing_params_returns_400(self, make_event):
        from controllers.get_session_by_id import get_session_by_id
        event = make_event("/sessions/by_id", "GET", query_params={})
        response = get_session_by_id(event, "u@e.com")
        assert response["statusCode"] == 400


class TestDeleteSession:
    @patch("controllers.delete_session._delete_activity_records")
    @patch("controllers.delete_session.get_session_table")
    def test_success(self, mock_get_table, mock_del_activities, make_event):
        mock_table = MagicMock()
        mock_get_table.return_value = mock_table

        from controllers.delete_session import delete_session
        event = make_event("/sessions", "DELETE", query_params={"session_id": "abc"})
        response = delete_session(event, "u@e.com")

        assert response["statusCode"] == 200
        mock_table.delete_item.assert_called_once()
        mock_del_activities.assert_called_once_with("u@e.com", "abc")

    def test_missing_params_returns_400(self, make_event):
        from controllers.delete_session import delete_session
        event = make_event("/sessions", "DELETE", query_params={})
        response = delete_session(event, "u@e.com")
        assert response["statusCode"] == 400


class TestGetSessionActivities:
    @patch("controllers.get_session_activities.get_activity_table")
    def test_success(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": [{"user_id": "u@e.com", "datetime": "2026-01-01"}]}
        mock_get_table.return_value = mock_table

        from controllers.get_session_activities import get_session_activities
        event = make_event("/sessions/activities", "POST", body={"session_id": "abc"})
        response = get_session_activities(event, "u@e.com")

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert len(body["Items"]) == 1

    def test_missing_params_returns_400(self, make_event):
        from controllers.get_session_activities import get_session_activities
        event = make_event("/sessions/activities", "POST", body={})
        response = get_session_activities(event, "u@e.com")
        assert response["statusCode"] == 400


class TestUpdateSession:
    @patch("controllers.update_session.get_session_table")
    def test_success(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": {"session_id": "abc"}}
        mock_table.update_item.return_value = {"Attributes": {"session_name": "New"}}
        mock_get_table.return_value = mock_table

        from controllers.update_session import update_session
        event = make_event("/sessions/abc/users/u@e.com", "PUT", body={"attributes": {"session_name": "New"}})
        event["pathParameters"] = {"sessionId": "abc", "userEmail": "u@e.com"}
        response = update_session(event)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["updated_attributes"]["session_name"] == "New"

    @patch("controllers.update_session.get_session_table")
    def test_not_found(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_get_table.return_value = mock_table

        from controllers.update_session import update_session
        event = make_event("/sessions/abc/users/u@e.com", "PUT", body={"attributes": {"session_name": "X"}})
        event["pathParameters"] = {"sessionId": "abc", "userEmail": "u@e.com"}
        response = update_session(event)

        assert response["statusCode"] == 404

    def test_missing_attributes_returns_400(self, make_event):
        from controllers.update_session import update_session
        event = make_event("/sessions/abc/users/u@e.com", "PUT", body={})
        event["pathParameters"] = {"sessionId": "abc", "userEmail": "u@e.com"}
        response = update_session(event)
        assert response["statusCode"] == 400


class TestGetSessionDocuments:
    @patch("controllers.get_session_documents._dynamodb")
    def test_success(self, mock_dynamodb, make_event):
        mock_dynamodb.batch_get_item.return_value = {
            "Responses": {
                "know-metadata-test": [
                    {"document_id": "d1", "title": "Doc 1", "therapeutic_area": "Oncology", "category": "MR"},
                    {"document_id": "d2", "title": "Doc 2", "therapeutic_area": "CRM", "category": "CI"},
                ]
            }
        }

        from controllers.get_session_documents import get_session_documents
        event = make_event("/sessions/documents", "GET", query_params={"doc_id": "d1,d2"})
        response = get_session_documents(event)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert len(body["documents"]) == 2
        assert body["documents"][0]["document_id"] in ["d1", "d2"]

    def test_missing_doc_id_returns_400(self, make_event):
        from controllers.get_session_documents import get_session_documents
        event = make_event("/sessions/documents", "GET", query_params={})
        response = get_session_documents(event)
        assert response["statusCode"] == 400

    def test_empty_doc_id_returns_400(self, make_event):
        from controllers.get_session_documents import get_session_documents
        event = make_event("/sessions/documents", "GET", query_params={"doc_id": "  ,  "})
        response = get_session_documents(event)
        assert response["statusCode"] == 400

    def test_too_many_ids_returns_400(self, make_event):
        from controllers.get_session_documents import get_session_documents
        ids = ",".join([f"doc-{i}" for i in range(101)])
        event = make_event("/sessions/documents", "GET", query_params={"doc_id": ids})
        response = get_session_documents(event)
        assert response["statusCode"] == 400

    @patch("controllers.get_session_documents._dynamodb")
    def test_dynamo_error_returns_500(self, mock_dynamodb, make_event):
        mock_dynamodb.batch_get_item.side_effect = Exception("DynamoDB error")
        from controllers.get_session_documents import get_session_documents
        event = make_event("/sessions/documents", "GET", query_params={"doc_id": "d1"})
        response = get_session_documents(event)
        assert response["statusCode"] == 500


class TestLogActivity:
    @patch("controllers.log_activity._update_session_details")
    @patch("controllers.log_activity._log_activity")
    def test_success(self, mock_log, mock_update, make_event):
        from controllers.log_activity import log_activity
        event = make_event("/sessions/log-user-activity", "POST", body={
            "session_id": "abc",
            "query": "What is oncology?",
            "filter_dict": {"ta": "Oncology"},
            "agent_response": "Hello",
            "latency": 1.5,
            "chattime": "2026-07-14T10:00:00",
        })
        response = log_activity(event, "u@e.com")
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "success"
        mock_log.assert_called_once()
        mock_update.assert_called_once()

    @patch("controllers.log_activity._update_session_details")
    @patch("controllers.log_activity._log_activity")
    def test_error_returns_500(self, mock_log, mock_update, make_event):
        mock_log.side_effect = Exception("DynamoDB error")
        from controllers.log_activity import log_activity
        event = make_event("/sessions/log-user-activity", "POST", body={"session_id": "abc"})
        response = log_activity(event, "u@e.com")
        assert response["statusCode"] == 500

    @patch("controllers.log_activity._update_session_details")
    @patch("controllers.log_activity._log_activity")
    def test_invalid_latency_defaults_to_zero(self, mock_log, mock_update, make_event):
        """Cover the except branch when latency is not a valid number."""
        from controllers.log_activity import log_activity
        event = make_event("/sessions/log-user-activity", "POST", body={
            "session_id": "abc",
            "latency": "not-a-number",
        })
        response = log_activity(event, "u@e.com")
        assert response["statusCode"] == 200
        mock_log.assert_called_once()
        # agent_latency should be Decimal(0)
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["agent_latency"] == Decimal(0)

    @patch("controllers.log_activity.get_activity_table")
    def test_log_activity_internal(self, mock_get_table):
        mock_table = MagicMock()
        mock_get_table.return_value = mock_table
        from controllers.log_activity import _log_activity
        activity = {
            "query": "What is AWS?",
            "agent_response": "hi",
            "session_id": "sess-1",
            "filter_dict": {},
        }
        _log_activity("u@e.com", "sess-1", activity, agent_latency=2.5, chattime="2026-07-14T10:00:00")
        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["user_id"] == "u@e.com"
        assert item["session"] == "sess-1"
        assert item["datetime"] == "2026-07-14T10:00:00"
        assert item["agent_latency"] == Decimal("2.5")

    @patch("controllers.log_activity.get_activity_table")
    def test_log_activity_invalid_latency(self, mock_get_table):
        mock_table = MagicMock()
        mock_get_table.return_value = mock_table
        from controllers.log_activity import _log_activity
        _log_activity("u@e.com", "sess-1", {}, agent_latency="not-a-number")
        item = mock_table.put_item.call_args[1]["Item"]
        assert "agent_latency" not in item

    @patch("controllers.log_activity.get_session_table")
    def test_update_session_details(self, mock_get_table):
        mock_table = MagicMock()
        mock_get_table.return_value = mock_table
        from controllers.log_activity import _update_session_details
        _update_session_details("u@e.com", "sess-1", {"ta": "Oncology"})
        mock_table.update_item.assert_called_once()


class TestGetSessionsLastN:
    @patch("controllers.get_sessions_last_n.get_session_table")
    def test_success(self, mock_get_table, make_event):
        now = datetime.now(timezone.utc)
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                {"session_id": "s1", "user_id": "u@e.com", "last_accessed_at": now.isoformat()},
                {"session_id": "s2", "user_id": "u@e.com", "last_accessed_at": (now - timedelta(days=1)).isoformat()},
                {"session_id": "s3", "user_id": "u@e.com", "last_accessed_at": (now - timedelta(days=5)).isoformat()},
                {"session_id": "s4", "user_id": "u@e.com", "last_accessed_at": (now - timedelta(days=20)).isoformat()},
            ],
            "LastEvaluatedKey": None,
        }
        mock_get_table.return_value = mock_table

        from controllers.get_sessions_last_n import get_sessions_last_n
        event = make_event("/sessions/last_n_days", "GET")
        response = get_sessions_last_n(event, "u@e.com")

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "success"
        assert len(body["today"]) == 1
        assert len(body["yesterday"]) == 1
        assert len(body["previous_7_days"]) == 1
        assert len(body["previous_30_days"]) == 1

    @patch("controllers.get_sessions_last_n.get_session_table")
    def test_error_returns_500(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.query.side_effect = Exception("DynamoDB error")
        mock_get_table.return_value = mock_table

        from controllers.get_sessions_last_n import get_sessions_last_n
        event = make_event("/sessions/last_n_days", "GET")
        response = get_sessions_last_n(event, "u@e.com")
        assert response["statusCode"] == 500

    @patch("controllers.get_sessions_last_n.get_session_table")
    def test_pagination(self, mock_get_table, make_event):
        """Cover the LastEvaluatedKey pagination branch in get_sessions_last_n."""
        now = datetime.now(timezone.utc)
        mock_table = MagicMock()
        mock_table.query.side_effect = [
            {
                "Items": [{"session_id": "s1", "user_id": "u@e.com", "last_accessed_at": now.isoformat()}],
                "LastEvaluatedKey": {"user_id": "u@e.com", "last_accessed_at": now.isoformat()},
            },
            {
                "Items": [{"session_id": "s2", "user_id": "u@e.com", "last_accessed_at": now.isoformat()}],
                "LastEvaluatedKey": None,
            },
        ]
        mock_get_table.return_value = mock_table

        from controllers.get_sessions_last_n import get_sessions_last_n
        event = make_event("/sessions/last_n_days", "GET")
        response = get_sessions_last_n(event, "u@e.com")
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert len(body["today"]) == 2

    def test_group_sessions_missing_timestamp(self):
        from controllers.get_sessions_last_n import _group_sessions_by_period
        sessions = [{"session_id": "s1"}]
        grouped = _group_sessions_by_period(sessions)
        assert len(grouped["previous_30_days"]) == 1

    def test_group_sessions_invalid_timestamp(self):
        from controllers.get_sessions_last_n import _group_sessions_by_period
        sessions = [{"session_id": "s1", "last_accessed_at": "not-a-date"}]
        grouped = _group_sessions_by_period(sessions)
        assert len(grouped["previous_30_days"]) == 1

    def test_group_sessions_naive_timestamp(self):
        from controllers.get_sessions_last_n import _group_sessions_by_period
        now = datetime.now(timezone.utc)
        sessions = [{"session_id": "s1", "last_accessed_at": now.strftime("%Y-%m-%dT%H:%M:%S")}]
        grouped = _group_sessions_by_period(sessions)
        assert len(grouped["today"]) == 1


class TestDeleteActivityRecords:
    @patch("controllers.delete_session.get_activity_table")
    def test_deletes_records(self, mock_get_table):
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [{"user_id": "u@e.com", "datetime": "2026-01-01T00:00:00"}],
            "LastEvaluatedKey": None,
        }
        mock_batch = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(return_value=mock_batch)
        mock_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_table.return_value = mock_table

        from controllers.delete_session import _delete_activity_records
        _delete_activity_records("u@e.com", "sess-1")
        mock_batch.delete_item.assert_called_once()

    @patch("controllers.delete_session.get_activity_table")
    def test_pagination(self, mock_get_table):
        """Cover the LastEvaluatedKey pagination branch."""
        mock_table = MagicMock()
        mock_table.query.side_effect = [
            {
                "Items": [{"user_id": "u@e.com", "datetime": "2026-01-01T00:00:00"}],
                "LastEvaluatedKey": {"user_id": "u@e.com", "datetime": "2026-01-01T00:00:00"},
            },
            {
                "Items": [{"user_id": "u@e.com", "datetime": "2026-01-02T00:00:00"}],
                "LastEvaluatedKey": None,
            },
        ]
        mock_batch = MagicMock()
        mock_table.batch_writer.return_value.__enter__ = MagicMock(return_value=mock_batch)
        mock_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_table.return_value = mock_table

        from controllers.delete_session import _delete_activity_records
        _delete_activity_records("u@e.com", "sess-1")
        assert mock_batch.delete_item.call_count == 2

    @patch("controllers.delete_session.time.sleep")
    def test_query_with_backoff_retries(self, mock_sleep):
        from controllers.delete_session import _query_with_backoff
        mock_table = MagicMock()
        error = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Query")
        mock_table.query.side_effect = [error, {"Items": []}]
        result = _query_with_backoff(mock_table, {}, max_retries=2)
        assert result == {"Items": []}
        mock_sleep.assert_called_once_with(1)

    @patch("controllers.delete_session.time.sleep")
    def test_query_with_backoff_raises_after_max_retries(self, mock_sleep):
        from controllers.delete_session import _query_with_backoff
        mock_table = MagicMock()
        error = ClientError({"Error": {"Code": "ThrottlingException"}}, "Query")
        mock_table.query.side_effect = error
        with pytest.raises(ClientError):
            _query_with_backoff(mock_table, {}, max_retries=1)

    def test_query_with_backoff_raises_non_throttle_error(self):
        from controllers.delete_session import _query_with_backoff
        mock_table = MagicMock()
        error = ClientError({"Error": {"Code": "ValidationException"}}, "Query")
        mock_table.query.side_effect = error
        with pytest.raises(ClientError):
            _query_with_backoff(mock_table, {})


class TestCreateSessionError:
    @patch("controllers.create_session.get_session_table")
    def test_dynamo_error_returns_500(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.put_item.side_effect = Exception("DynamoDB error")
        mock_get_table.return_value = mock_table

        from controllers.create_session import create_session
        event = make_event("/sessions", "POST", body={})
        response = create_session(event, "u@e.com")
        assert response["statusCode"] == 500


class TestGetSessionByIdError:
    @patch("controllers.get_session_by_id.get_session_table")
    def test_dynamo_error_returns_500(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.get_item.side_effect = Exception("DynamoDB error")
        mock_get_table.return_value = mock_table

        from controllers.get_session_by_id import get_session_by_id
        event = make_event("/sessions/by_id", "GET", query_params={"session_id": "abc"})
        response = get_session_by_id(event, "u@e.com")
        assert response["statusCode"] == 500


class TestGetSessionActivitiesError:
    @patch("controllers.get_session_activities.get_activity_table")
    def test_dynamo_error_returns_500(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.query.side_effect = Exception("DynamoDB error")
        mock_get_table.return_value = mock_table

        from controllers.get_session_activities import get_session_activities
        event = make_event("/sessions/activities", "POST", body={"session_id": "abc"})
        response = get_session_activities(event, "u@e.com")
        assert response["statusCode"] == 500

    @patch("controllers.get_session_activities.get_activity_table")
    def test_with_last_evaluated_key(self, mock_get_table, make_event):
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        mock_get_table.return_value = mock_table

        from controllers.get_session_activities import get_session_activities
        event = make_event("/sessions/activities", "POST", body={
            "session_id": "abc",
            "LastEvaluatedKey": {"user_id": "u@e.com", "datetime": "2026-01-01"},
        })
        response = get_session_activities(event, "u@e.com")
        assert response["statusCode"] == 200


class TestDeleteSessionError:
    @patch("controllers.delete_session._delete_activity_records")
    @patch("controllers.delete_session.get_session_table")
    def test_dynamo_error_returns_500(self, mock_get_table, mock_del, make_event):
        mock_table = MagicMock()
        mock_table.delete_item.side_effect = Exception("DynamoDB error")
        mock_get_table.return_value = mock_table

        from controllers.delete_session import delete_session
        event = make_event("/sessions", "DELETE", query_params={"session_id": "abc"})
        response = delete_session(event, "u@e.com")
        assert response["statusCode"] == 500


class TestUpdateSessionPathParsing:
    def test_path_parsing_fallback(self, make_event):
        from controllers.update_session import _get_path_parameter
        result = _get_path_parameter("/sessions/abc/users/u%40e.com")
        assert result["sessionId"] == "abc"
        assert result["userEmail"] == "u@e.com"

    def test_path_parsing_invalid(self, make_event):
        from controllers.update_session import _get_path_parameter
        assert _get_path_parameter("/invalid/path") is None
        assert _get_path_parameter("") is None
        assert _get_path_parameter(None) is None


class TestCoreClients:
    """Tests for core/clients.py lazy-init singletons."""

    @patch("core.clients.dynamodb")
    def test_get_session_table_lazy_init(self, mock_dynamodb):
        import core.clients as c
        c._session_table = None
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        assert c.get_session_table() is mock_table
        mock_dynamodb.Table.assert_called_once()

    @patch("core.clients.dynamodb")
    def test_get_session_table_cached(self, mock_dynamodb):
        import core.clients as c
        sentinel = MagicMock()
        c._session_table = sentinel
        assert c.get_session_table() is sentinel
        mock_dynamodb.Table.assert_not_called()

    @patch("core.clients.dynamodb")
    def test_get_activity_table_lazy_init(self, mock_dynamodb):
        import core.clients as c
        c._activity_table = None
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        assert c.get_activity_table() is mock_table
        mock_dynamodb.Table.assert_called_once()

    @patch("core.clients.dynamodb")
    def test_get_activity_table_cached(self, mock_dynamodb):
        import core.clients as c
        sentinel = MagicMock()
        c._activity_table = sentinel
        assert c.get_activity_table() is sentinel
        mock_dynamodb.Table.assert_not_called()


class TestGetSessionDocumentsMetadataTable:
    """Cover _get_metadata_table lazy-init in get_session_documents.py."""

    @patch("controllers.get_session_documents._dynamodb")
    def test_lazy_init(self, mock_dynamodb):
        import controllers.get_session_documents as mod
        mod._metadata_table = None
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        assert mod._get_metadata_table() is mock_table

    @patch("controllers.get_session_documents._dynamodb")
    def test_cached(self, mock_dynamodb):
        import controllers.get_session_documents as mod
        sentinel = MagicMock()
        mod._metadata_table = sentinel
        assert mod._get_metadata_table() is sentinel
        mock_dynamodb.Table.assert_not_called()


class TestLambdaFunctionEdgeCases:
    """Cover remaining lambda_function.py branches."""

    def test_parse_body_empty(self, make_event):
        import lambda_function
        event = make_event()
        event["body"] = None
        assert lambda_function.parse_body(event) == {}

    def test_parse_body_dict_passthrough(self, make_event):
        import lambda_function
        event = make_event()
        event["body"] = {"key": "val"}
        assert lambda_function.parse_body(event) == {"key": "val"}

    def test_resolve_route_returns_none(self):
        import lambda_function
        assert lambda_function._resolve_route("PATCH", "/unknown") is None

    @patch("lambda_function.create_session", side_effect=RuntimeError("boom"))
    def test_unhandled_exception_returns_500(self, _mock, make_event, mock_lambda_context):
        import lambda_function
        event = make_event("/sessions", "POST")
        response = lambda_function.lambda_handler(event, mock_lambda_context)
        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["error"]["code"] == "INTERNAL_ERROR"
