"""Tests for Search Service."""
import json
import unittest.mock
from unittest.mock import MagicMock

from dal.metadata_adapter import MetadataEnrichmentDAL
from shared.kb_client import KBSearchResult
from shared.kb_search_strategy import KBStrategyResult
from models import SearchRequest, SearchResponse
from services.search_service import SearchService


class TestSearchService:
    """Test search orchestration, enrichment, sorting, caching."""

    def setup_method(self):
        self.mock_dal = MagicMock(spec=MetadataEnrichmentDAL)
        self.mock_kb = MagicMock()
        self.mock_cache = MagicMock()
        self.service = SearchService(
            dal=self.mock_dal,
            
            cache_client=self.mock_cache,
        )

    def _make_request(self, query="test", filters=None, page=1, page_size=25, sort="relevance", ta_access=None):
        return SearchRequest(
            query=query,
            filters=filters or {},
            page=page,
            page_size=page_size,
            sort=sort,
            user_id="user@novartis.com",
            user_groups=["know-viewer"],
            ta_access=ta_access or [],
        )

    def test_search_uses_market_kb(self, sample_mcp_results, sample_metadata):
        """Market KB is always searched."""
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = sample_mcp_results
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        service = SearchService(
            dal=self.mock_dal,
            
            cache_client=self.mock_cache,
        )

        request = self._make_request(query="Leqvio market")
        response = service.search(request)

        assert isinstance(response, SearchResponse)
        assert response.search_source == "kb_retrieval"
        assert response.total_count == 2
        self.mock_kb.search.assert_called_once()
        call_kwargs = self.mock_kb.search.call_args[1]
        assert call_kwargs["appname"] == "market"

    def test_search_dual_kb_when_user_has_ta_access(self, sample_mcp_results, sample_restricted_results, sample_metadata):
        """Both market and market_restricted KBs searched when user has L2 entitlement."""
        self.mock_cache.get.return_value = None

        def kb_search_side_effect(**kwargs):
            if kwargs.get("appname") == "market":
                return sample_mcp_results
            if kwargs.get("appname") == "market_restricted":
                return sample_restricted_results
            return KBSearchResult(error="unknown appname", source="rest")

        self.mock_kb.search.side_effect = kb_search_side_effect
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        service = SearchService(
            dal=self.mock_dal,
            
            cache_client=self.mock_cache,
        )

        request = self._make_request(query="Leqvio strategy", ta_access=["crm"])
        response = service.search(request)

        assert response.search_source == "kb_retrieval"
        assert response.total_count == 4
        assert self.mock_kb.search.call_count == 2

    def test_search_single_kb_when_no_ta_access(self, sample_mcp_results, sample_metadata):
        """Only market KB searched when user has no L2 entitlement."""
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = sample_mcp_results
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        service = SearchService(
            dal=self.mock_dal,
            
            cache_client=self.mock_cache,
        )

        request = self._make_request(query="Leqvio market", ta_access=[])
        response = service.search(request)

        assert response.total_count == 2
        assert self.mock_kb.search.call_count == 1

    def test_search_cache_hit(self):
        cached_response = {
            "results": [{"document_id": "doc-001", "title": "Cached Result"}],
            "total_count": 1,
            "page": 1,
            "page_size": 25,
            "search_source": "kb_retrieval",
        }
        self.mock_cache.get.return_value = json.dumps(cached_response)

        request = self._make_request(query="cached query")
        response = self.service.search(request)

        assert response.total_count == 1
        self.mock_kb.search.assert_not_called()
        self.mock_dal.batch_get_metadata.assert_not_called()

    def test_search_cache_miss_queries_backend(self, sample_mcp_results, sample_metadata):
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = sample_mcp_results
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        request = self._make_request(query="Leqvio")
        response = self.service.search(request)

        assert response.total_count > 0
        self.mock_cache.setex.assert_called_once()

    def test_search_no_cache_client(self, sample_mcp_results, sample_metadata):
        service = SearchService(
            dal=self.mock_dal,
            
            cache_client=None,
        )
        self.mock_kb.search.return_value = sample_mcp_results
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        request = self._make_request(query="test")
        response = service.search(request)

        assert response.total_count == 2

    def test_search_cache_error_falls_through(self, sample_mcp_results, sample_metadata):
        self.mock_cache.get.side_effect = Exception("Redis down")
        self.mock_kb.search.return_value = sample_mcp_results
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        request = self._make_request(query="test")
        response = self.service.search(request)

        assert response.total_count == 2

    def test_search_enriches_with_metadata(self, sample_mcp_results, sample_metadata):
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = sample_mcp_results
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        request = self._make_request(query="Leqvio")
        response = self.service.search(request)

        first_result = response.results[0]
        assert first_result.title == "Leqvio Market Report Q4 2024"
        assert first_result.therapeutic_area == "CRM"
        assert first_result.brand == "Leqvio"
        assert first_result.deep_link == "/documents/doc-001"

    def test_search_deduplicates_chunks(self, sample_metadata):
        """Multiple chunks from same doc should produce one result."""
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = KBSearchResult(
            text="chunks",
            chunks=[
                {"document_id": "doc-001", "text": "chunk 1", "score": 0.95},
                {"document_id": "doc-001", "text": "chunk 2", "score": 0.88},
                {"document_id": "doc-002", "text": "other doc", "score": 0.80},
            ],
            latency_ms=200,
            source="rest",
        )
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        request = self._make_request(query="test")
        response = self.service.search(request)

        assert response.total_count == 2

    def test_search_pagination(self, sample_metadata):
        self.mock_cache.get.return_value = None
        chunks = [
            {"document_id": f"doc-{i:03d}", "text": f"result {i}", "score": 1.0 - i * 0.1}
            for i in range(1, 6)
        ]
        self.mock_kb.search.return_value = KBSearchResult(
            text="results", chunks=chunks, latency_ms=100, source="rest"
        )
        meta = {}
        for i in range(1, 6):
            doc_id = f"doc-{i:03d}"
            meta[doc_id] = {"document_id": doc_id, "title": f"Document {i}", "status": "published"}
        self.mock_dal.batch_get_metadata.return_value = meta

        request = self._make_request(query="test", page=1, page_size=2)
        response = self.service.search(request)

        assert response.total_count == 5
        assert len(response.results) == 2
        assert response.page == 1

        request = self._make_request(query="test", page=2, page_size=2)
        response = self.service.search(request)

        assert response.total_count == 5
        assert len(response.results) == 2
        assert response.page == 2

    def test_search_sort_by_date(self, sample_metadata):
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = KBSearchResult(
            text="results",
            chunks=[
                {"document_id": "doc-001", "text": "old", "score": 0.9},
                {"document_id": "doc-004", "text": "newest", "score": 0.8},
                {"document_id": "doc-002", "text": "middle", "score": 0.7},
            ],
            latency_ms=100,
            source="rest",
        )
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        request = self._make_request(query="test", sort="date")
        response = self.service.search(request)

        assert response.results[0].document_id == "doc-004"

    def test_search_sort_by_title(self, sample_metadata):
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = KBSearchResult(
            text="results",
            chunks=[
                {"document_id": "doc-002", "text": "C", "score": 0.9},
                {"document_id": "doc-001", "text": "L", "score": 0.8},
            ],
            latency_ms=100,
            source="rest",
        )
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        request = self._make_request(query="test", sort="title")
        response = self.service.search(request)

        assert response.results[0].document_id == "doc-002"

    def test_search_empty_results(self):
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = KBSearchResult(
            text="", chunks=[], latency_ms=50, source="rest"
        )

        request = self._make_request(query="nonexistent")
        response = self.service.search(request)

        assert response.total_count == 0
        assert response.results == []
        assert response.search_source == "kb_retrieval"

    def test_search_kb_failure_returns_empty(self):
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = KBSearchResult(error="timeout", source="rest")

        request = self._make_request(query="test")
        response = self.service.search(request)

        assert response.total_count == 0

    def test_get_filter_options_success(self, sample_filter_options):
        self.mock_cache.get.return_value = None
        self.mock_dal.get_filter_options.return_value = sample_filter_options

        options = self.service.get_filter_options()

        assert len(options.therapeutic_area) == 3
        assert options.therapeutic_area[0].value == "CRM"
        assert options.therapeutic_area[0].count == 45

    def test_get_filter_options_with_ta_filter(self, sample_filter_options):
        self.mock_cache.get.return_value = None
        self.mock_dal.get_filter_options.return_value = sample_filter_options

        self.service.get_filter_options(ta_filter="CRM")

        self.mock_dal.get_filter_options.assert_called_once_with("CRM")

    def test_get_filter_options_cached(self, sample_filter_options):
        self.mock_cache.get.return_value = json.dumps(sample_filter_options)

        options = self.service.get_filter_options()

        assert len(options.therapeutic_area) == 3
        self.mock_dal.get_filter_options.assert_not_called()

    def test_search_partial_kb_failure_returns_market_results(self, sample_mcp_results, sample_metadata):
        """If restricted KB fails but market succeeds, return market results."""
        self.mock_cache.get.return_value = None
        partial_result = KBStrategyResult(
            chunks=sample_mcp_results.chunks,
            text=sample_mcp_results.text,
            market_latency_ms=320,
            restricted_latency_ms=0,
            searched_kbs=["market"],
        )
        with unittest.mock.patch("services.search_service.search_with_access", return_value=partial_result):
            self.mock_dal.batch_get_metadata.return_value = sample_metadata
            request = self._make_request(query="test", ta_access=["CRM"])
            response = self.service.search(request)

            assert response.total_count == 2
            assert response.search_source == "kb_retrieval"

    def test_search_ddb_enrichment_failure_graceful(self):
        """If DDB BatchGetItem fails, results still returned (without enrichment)."""
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = KBSearchResult(
            text="chunks", chunks=[
                {"document_id": "doc-001", "text": "result", "score": 0.9},
            ], latency_ms=200, source="rest"
        )
        self.mock_dal.batch_get_metadata.return_value = {}

        request = self._make_request(query="test")
        response = self.service.search(request)

        assert response.total_count == 1
        assert response.results[0].document_id == "doc-001"
        assert response.results[0].title == "doc-001"

    def test_search_page_beyond_results(self):
        """Page 100 when only 5 docs → empty page, total still correct."""
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = KBSearchResult(
            text="results",
            chunks=[{"document_id": f"doc-{i}", "text": f"r{i}", "score": 0.5} for i in range(5)],
            latency_ms=100, source="rest"
        )
        self.mock_dal.batch_get_metadata.return_value = {
            f"doc-{i}": {"document_id": f"doc-{i}", "title": f"Doc {i}"} for i in range(5)
        }

        request = self._make_request(query="test", page=100, page_size=10)
        response = self.service.search(request)

        assert response.total_count == 5
        assert len(response.results) == 0
        assert response.page == 100

    def test_search_sort_by_relevance_default(self, sample_metadata):
        """Default sort (relevance) returns highest score first."""
        self.mock_cache.get.return_value = None
        self.mock_kb.search.return_value = KBSearchResult(
            text="results",
            chunks=[
                {"document_id": "doc-002", "text": "low", "score": 0.3},
                {"document_id": "doc-001", "text": "high", "score": 0.95},
            ],
            latency_ms=100, source="rest"
        )
        self.mock_dal.batch_get_metadata.return_value = sample_metadata

        request = self._make_request(query="test", sort="relevance")
        response = self.service.search(request)

        assert response.results[0].document_id == "doc-001"
        assert response.results[0].score > response.results[1].score
