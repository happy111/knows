"""Tests for Search Lambda Handler."""
import json

import pytest
from unittest.mock import MagicMock, patch

from models import FilterOptions, SearchResponse, SearchResultItem


class TestSearchHandler:
    """Test handler routing, validation, and error handling."""

    @patch("lambda_function._get_service")
    def test_search_success(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_service.search.return_value = SearchResponse(
            results=[
                SearchResultItem(document_id="doc-001", title="Leqvio Report", snippet="Market share...", score=0.92)
            ],
            total_count=1,
            page=1,
            page_size=25,
            search_source="ailens",
        )
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(body={"query": "Leqvio market share"})
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["pagination"]["total"] == 1
        assert body["documents"][0]["document_id"] == "doc-001"
        assert body["search_source"] == "ailens"

    @patch("lambda_function._get_service")
    def test_search_empty_query_returns_400(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(body={"query": ""})
        response = lambda_handler(event, None)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"]["code"] == "VALIDATION_ERROR"

    @patch("lambda_function._get_service")
    def test_search_missing_query_returns_400(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(body={})
        response = lambda_handler(event, None)

        assert response["statusCode"] == 400

    @patch("lambda_function._get_service")
    def test_search_invalid_json_body(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event()
        event["body"] = "not-valid-json"
        response = lambda_handler(event, None)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"]["code"] == "INVALID_JSON"

    @patch("lambda_function._get_service")
    def test_search_with_filters(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_service.search.return_value = SearchResponse(
            results=[], total_count=0, page=1, page_size=25, search_source="ailens"
        )
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(
            body={
                "query": "market analysis",
                "filters": {"therapeutic_area": ["CRM"], "brand": ["Leqvio"]},
                "page": 2,
                "page_size": 10,
                "sort": "date",
            }
        )
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        # Verify service was called with correct SearchRequest
        call_args = mock_service.search.call_args[0][0]
        assert call_args.query == "market analysis"
        assert call_args.filters == {"therapeutic_area": ["CRM"], "brand": ["Leqvio"]}
        assert call_args.page == 2
        assert call_args.page_size == 10
        assert call_args.sort == "date"

    @patch("lambda_function._get_service")
    def test_search_page_size_capped_at_50(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_service.search.return_value = SearchResponse(
            results=[], total_count=0, page=1, page_size=50, search_source="ailens"
        )
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(body={"query": "test", "page_size": 200})
        lambda_handler(event, None)

        call_args = mock_service.search.call_args[0][0]
        assert call_args.page_size == 50

    @patch("lambda_function._get_service")
    def test_filter_options_success(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_service.get_filter_options.return_value = FilterOptions(
            therapeutic_area=[], brand=[], indication=[], category=[], project_type=[]
        )
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(path="/api/filters/options", method="GET")
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "therapeutic_area" in body
        assert "brand" in body
        assert "date_range" in body

    @patch("lambda_function._get_service")
    def test_filter_options_with_ta_param(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_service.get_filter_options.return_value = FilterOptions()
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(path="/api/filters/options", method="GET", params={"ta": "CRM"})
        lambda_handler(event, None)

        mock_service.get_filter_options.assert_called_once_with(ta_filter="CRM")

    @patch("lambda_function._get_service")
    def test_unknown_route_returns_404(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(path="/api/unknown", method="GET")
        response = lambda_handler(event, None)

        assert response["statusCode"] == 404

    @patch("lambda_function._get_service")
    def test_options_preflight_returns_200(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(path="/api/search", method="OPTIONS")
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200

    @patch("lambda_function._get_service")
    def test_internal_error_returns_500(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_service.search.side_effect = Exception("DDB timeout")
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(body={"query": "test"})
        response = lambda_handler(event, None)

        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["error"]["code"] == "INTERNAL_ERROR"

    @patch("lambda_function._get_service")
    def test_cors_headers_present(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_service.search.return_value = SearchResponse(
            results=[], total_count=0, page=1, page_size=25, search_source="ailens"
        )
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(body={"query": "test"})
        response = lambda_handler(event, None)

        assert response["headers"]["Access-Control-Allow-Origin"] == "*"
        assert "Content-Type" in response["headers"]

    @patch("lambda_function._get_service")
    def test_user_context_extracted_from_jwt(self, mock_get_service, api_gw_event):
        mock_service = MagicMock()
        mock_service.search.return_value = SearchResponse(
            results=[], total_count=0, page=1, page_size=25, search_source="ailens"
        )
        mock_get_service.return_value = mock_service

        from lambda_function import lambda_handler

        event = api_gw_event(
            body={"query": "test"},
            user_email="john@novartis.com",
            user_groups="know-viewer,crm-access,ipst-crm",
        )
        lambda_handler(event, None)

        call_args = mock_service.search.call_args[0][0]
        assert call_args.user_id == "john@novartis.com"
        assert "know-viewer" in call_args.user_groups
        assert "ipst-crm" in call_args.user_groups
        # ta_access should be extracted from ipst- prefixed groups
        assert "crm" in call_args.ta_access


class TestInitCacheClient:
    """Tests for _init_cache_client — module-level Redis initializer."""

    @patch("lambda_function._CACHE_ENDPOINT", None)
    def test_returns_none_when_no_endpoint(self):
        """When CACHE_ENDPOINT is not set, should return None immediately."""
        from lambda_function import _init_cache_client

        assert _init_cache_client() is None

    @patch("lambda_function._CACHE_ENDPOINT", "redis.example.com")
    def test_returns_client_when_redis_available(self):
        """When Redis connects and pings successfully, return the client."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True

        with patch("redis.Redis", return_value=mock_client) as mock_redis_cls:
            from lambda_function import _init_cache_client

            result = _init_cache_client()

            assert result is mock_client
            mock_redis_cls.assert_called_once_with(
                host="redis.example.com",
                port=6379,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            mock_client.ping.assert_called_once()

    @patch("lambda_function._CACHE_ENDPOINT", "redis.example.com")
    def test_returns_none_when_redis_fails(self):
        """When Redis ping fails, should return None and log warning."""
        with patch("redis.Redis", side_effect=ConnectionError("Connection refused")):
            from lambda_function import _init_cache_client

            result = _init_cache_client()

            assert result is None


class TestGetService:
    """Tests for _get_service — lazy singleton factory."""

    def test_creates_service_with_cache(self):
        """First call should create SearchService with cache client."""
        import lambda_function

        # Reset singleton
        lambda_function._service = None

        mock_cache = MagicMock()
        mock_svc = MagicMock()

        with patch.object(lambda_function, "_init_cache_client", return_value=mock_cache), \
             patch("services.search_service.SearchService", return_value=mock_svc) as mock_cls:
            result = lambda_function._get_service()

            assert result is mock_svc
            mock_cls.assert_called_once_with(cache_client=mock_cache)

        # Cleanup
        lambda_function._service = None

    def test_returns_cached_singleton(self):
        """Subsequent calls should return the same instance without re-creating."""
        import lambda_function

        sentinel = MagicMock(name="cached-service")
        lambda_function._service = sentinel

        result = lambda_function._get_service()

        assert result is sentinel

        # Cleanup
        lambda_function._service = None
