"""Shared test fixtures for search tests."""
import os
import sys
from unittest.mock import patch

import pytest

# Add lambda root FIRST so 'from dal...', 'from models...' resolve to search's own modules
# (not shared/models.py)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

# Set env vars before importing modules
os.environ["AWS_REGION"] = "eu-west-1"
os.environ["KNOW_METADATA_TABLE"] = "know-metadata-test"
os.environ["KNOW_TAXONOMY_TABLE"] = "know-taxonomy-test"
os.environ["KB_ACCESS_MODE"] = "rest"
os.environ["KB_REST_URL"] = "http://localhost:9999/search"
os.environ["SEARCH_CACHE_TTL"] = "60"
os.environ["FILTERS_CACHE_TTL"] = "300"


@pytest.fixture(autouse=True)
def mock_get_user_role():
    """Mock _get_user_role to avoid real DynamoDB calls in tests."""
    with patch("shared.auth._get_user_role", return_value=("L1", {"ta_access": []})):
        yield


@pytest.fixture
def sample_metadata():
    """Sample document metadata from kNOW-Metadata table."""
    return {
        "doc-001": {
            "document_id": "doc-001",
            "title": "Leqvio Market Report Q4 2024",
            "brand": "Leqvio",
            "therapeutic_area": "CRM",
            "indication": "ASCVD",
            "project_type": "Market Research",
            "category": "MR",
            "date_range": "2024",
            "published_at": "2026-05-20T10:00:00Z",
            "file_type": "pdf",
            "file_size": 2500000,
            "status": "published",
            "is_restricted": False,
        },
        "doc-002": {
            "document_id": "doc-002",
            "title": "Cosentyx HCP Feedback Summary",
            "brand": "Cosentyx",
            "therapeutic_area": "Immunology",
            "indication": "Psoriasis",
            "project_type": "Customer Intelligence",
            "category": "CI",
            "date_range": "2024",
            "published_at": "2026-05-15T08:30:00Z",
            "file_type": "pptx",
            "file_size": 4800000,
            "status": "published",
            "is_restricted": False,
        },
        "doc-003": {
            "document_id": "doc-003",
            "title": "IPST Strategy Document - CRM",
            "brand": "Leqvio",
            "therapeutic_area": "CRM",
            "indication": "HeFH",
            "project_type": "IPST",
            "category": "IPST",
            "date_range": "2025",
            "published_at": "2026-04-01T12:00:00Z",
            "file_type": "docx",
            "file_size": 1200000,
            "status": "published",
            "is_restricted": True,
        },
        "doc-004": {
            "document_id": "doc-004",
            "title": "Leqvio Launch Performance Tracker",
            "brand": "Leqvio",
            "therapeutic_area": "CRM",
            "indication": "ASCVD",
            "project_type": "Performance Tracker",
            "category": "LT",
            "date_range": "2024",
            "published_at": "2026-05-22T14:00:00Z",
            "file_type": "xlsx",
            "file_size": 980000,
            "status": "published",
            "is_restricted": True,
        },
    }


@pytest.fixture
def sample_mcp_results():
    """Sample KBSearchResult from market KB search."""
    from shared.kb_client import KBSearchResult
    return KBSearchResult(
        text="--- Chunk 1 (Score: 0.92) ---\nDocument: Leqvio_Market_Report_Q4.pdf\nContent: Leqvio market share increased...",
        chunks=[
            {"document_id": "doc-001", "text": "Leqvio market share increased to 15% in Q4...", "score": 0.92, "filename": "Leqvio_Market_Report_Q4.pdf"},
            {"document_id": "doc-002", "text": "HCP feedback on Cosentyx efficacy shows...", "score": 0.71, "filename": "Cosentyx_HCP_Feedback.pptx"},
        ],
        latency_ms=320,
        source="rest",
    )


@pytest.fixture
def sample_restricted_results():
    """Sample KBSearchResult from market_restricted KB search."""
    from shared.kb_client import KBSearchResult
    return KBSearchResult(
        text="--- Chunk 1 (Score: 0.88) ---\nDocument: IPST_CRM_Strategy.docx\nContent: IPST strategy...",
        chunks=[
            {"document_id": "doc-003", "text": "IPST strategy for CRM therapeutic area focuses...", "score": 0.88, "filename": "IPST_CRM_Strategy.docx"},
            {"document_id": "doc-004", "text": "Launch tracker shows positive uptake...", "score": 0.82, "filename": "Leqvio_Launch_Tracker.xlsx"},
        ],
        latency_ms=450,
        source="rest",
    )


@pytest.fixture
def sample_filter_options():
    """Sample filter options from DAL."""
    return {
        "therapeutic_area": [{"value": "CRM", "count": 45}, {"value": "Immunology", "count": 22}, {"value": "Oncology", "count": 18}],
        "brand": [{"value": "Leqvio", "count": 30}, {"value": "Cosentyx", "count": 15}, {"value": "Kisqali", "count": 12}],
        "indication": [{"value": "ASCVD", "count": 20}, {"value": "Psoriasis", "count": 10}, {"value": "HeFH", "count": 8}],
        "project_type": [{"value": "Market Research", "count": 35}, {"value": "Customer Intelligence", "count": 20}, {"value": "IPST", "count": 10}],
        "date_range": {"min": "2022-01-01", "max": "2026-12-31"},
    }


@pytest.fixture
def api_gw_event():
    """Factory for API Gateway proxy events."""

    def _make(
        path="/api/search",
        method="POST",
        body=None,
        params=None,
        user_email="user@novartis.com",
        user_groups="know-viewer,crm-access",
    ):
        event = {
            "path": path,
            "httpMethod": method,
            "queryStringParameters": params or {},
            "headers": {"Authorization": "Bearer test-token"},
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "email": user_email,
                        "custom:groups": user_groups,
                        "sub": "user-123",
                    }
                }
            },
        }
        if body is not None:
            import json
            event["body"] = json.dumps(body)
        return event

    return _make
