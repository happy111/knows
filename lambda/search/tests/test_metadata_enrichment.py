"""Unit tests for search/services/metadata_enrichment.py — MetadataEnrichmentDAL."""
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure search lambda source is on path
SEARCH_DIR = Path(__file__).resolve().parents[1]
if str(SEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(SEARCH_DIR))

# Set env vars before importing modules
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("KNOW_METADATA_TABLE", "know-metadata-dev")
os.environ.setdefault("KNOW_TAXONOMY_TABLE", "know-taxonomy-dev")
os.environ.setdefault("POWERTOOLS_TRACE_DISABLED", "true")
os.environ.setdefault("POWERTOOLS_METRICS_DISABLED", "true")
os.environ.setdefault("POWERTOOLS_LOG_DEDUPLICATION_DISABLED", "true")


def _make_item(doc_id, ta="CRM", brand="BrandA", indication="Ind1",
               category="MR", project_type="Report", year="2024", status="published"):
    return {
        "document_id": doc_id,
        "therapeutic_area": ta,
        "brand": brand,
        "indication": indication,
        "category": category,
        "project_type": project_type,
        "year": year,
        "status": status,
    }


# ===========================================================================
# batch_get_metadata tests
# ===========================================================================
class TestBatchGetMetadata:
    def test_empty_document_ids(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        mock_ddb = MagicMock()
        dal = MetadataEnrichmentDAL(dynamodb_resource=mock_ddb)
        result = dal.batch_get_metadata([])
        assert result == {}
        mock_ddb.batch_get_item.assert_not_called()

    def test_single_document(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        mock_ddb = MagicMock()
        mock_ddb.batch_get_item.return_value = {
            "Responses": {
                "know-metadata-dev": [
                    {"document_id": "doc-1", "title": "Test Doc"}
                ]
            },
            "UnprocessedKeys": {},
        }
        dal = MetadataEnrichmentDAL(dynamodb_resource=mock_ddb)
        result = dal.batch_get_metadata(["doc-1"])
        assert "doc-1" in result
        assert result["doc-1"]["title"] == "Test Doc"

    def test_multiple_documents(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        mock_ddb = MagicMock()
        mock_ddb.batch_get_item.return_value = {
            "Responses": {
                "know-metadata-dev": [
                    {"document_id": "doc-1", "title": "A"},
                    {"document_id": "doc-2", "title": "B"},
                ]
            },
            "UnprocessedKeys": {},
        }
        dal = MetadataEnrichmentDAL(dynamodb_resource=mock_ddb)
        result = dal.batch_get_metadata(["doc-1", "doc-2"])
        assert len(result) == 2

    def test_batch_size_splitting(self):
        """Over 100 docs should be split into batches."""
        from services.metadata_enrichment import MetadataEnrichmentDAL

        mock_ddb = MagicMock()
        mock_ddb.batch_get_item.return_value = {
            "Responses": {"know-metadata-dev": []},
            "UnprocessedKeys": {},
        }
        dal = MetadataEnrichmentDAL(dynamodb_resource=mock_ddb)
        doc_ids = [f"doc-{i}" for i in range(150)]
        dal.batch_get_metadata(doc_ids)
        # Should be called twice: 100 + 50
        assert mock_ddb.batch_get_item.call_count == 2

    def test_unprocessed_keys_retry(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        mock_ddb = MagicMock()
        # First call has unprocessed keys
        mock_ddb.batch_get_item.side_effect = [
            {
                "Responses": {"know-metadata-dev": [{"document_id": "doc-1", "title": "A"}]},
                "UnprocessedKeys": {
                    "know-metadata-dev": {"Keys": [{"document_id": "doc-2"}]}
                },
            },
            {
                "Responses": {"know-metadata-dev": [{"document_id": "doc-2", "title": "B"}]},
                "UnprocessedKeys": {},
            },
        ]
        dal = MetadataEnrichmentDAL(dynamodb_resource=mock_ddb)
        result = dal.batch_get_metadata(["doc-1", "doc-2"])
        assert "doc-1" in result
        assert "doc-2" in result


# ===========================================================================
# get_filter_options tests
# ===========================================================================
class TestGetFilterOptions:
    def _make_dal_with_items(self, items):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        mock_ddb = MagicMock()
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.scan.return_value = {"Items": items}
        return MetadataEnrichmentDAL(dynamodb_resource=mock_ddb)

    def test_empty_table(self):
        dal = self._make_dal_with_items([])
        result = dal.get_filter_options()
        assert result["therapeutic_area"] == []
        assert result["brand"] == []
        assert result["indication"] == []
        assert "date_range" in result

    def test_basic_aggregation(self):
        items = [
            _make_item("d1", ta="CRM", brand="BrandX", indication="Ind1", year="2023"),
            _make_item("d2", ta="CRM", brand="BrandX", indication="Ind2", year="2024"),
            _make_item("d3", ta="ONC", brand="BrandY", indication="Ind1", year="2024"),
        ]
        dal = self._make_dal_with_items(items)
        result = dal.get_filter_options()

        ta_values = [x["value"] for x in result["therapeutic_area"]]
        assert "CRM" in ta_values
        assert "ONC" in ta_values

        # CRM has count 2
        crm_entry = next(x for x in result["therapeutic_area"] if x["value"] == "CRM")
        assert crm_entry["count"] == 2

    def test_date_range_min_max(self):
        items = [
            _make_item("d1", year="2022"),
            _make_item("d2", year="2025"),
        ]
        dal = self._make_dal_with_items(items)
        result = dal.get_filter_options()
        assert result["date_range"]["min"] == "2022-01-01"
        assert result["date_range"]["max"] == "2025-12-31"

    def test_ta_filter_applies_to_brand_indication(self):
        items = [
            _make_item("d1", ta="CRM", brand="BrandA", indication="IndA"),
            _make_item("d2", ta="ONC", brand="BrandB", indication="IndB"),
        ]
        dal = self._make_dal_with_items(items)
        result = dal.get_filter_options(ta_filter="CRM")

        brand_values = [x["value"] for x in result["brand"]]
        assert "BrandA" in brand_values
        assert "BrandB" not in brand_values

    def test_document_type_grouped_by_category(self):
        items = [
            _make_item("d1", category="MR", project_type="Analysis"),
            _make_item("d2", category="MR", project_type="Report"),
            _make_item("d3", category="CI", project_type="Survey"),
        ]
        dal = self._make_dal_with_items(items)
        result = dal.get_filter_options()
        assert "MR" in result["document_type"]
        assert "CI" in result["document_type"]

    def test_pagination_handling(self):
        """Ensure scan pagination is followed."""
        from services.metadata_enrichment import MetadataEnrichmentDAL

        mock_ddb = MagicMock()
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table

        # Simulate pagination: first call returns LastEvaluatedKey, second doesn't
        mock_table.scan.side_effect = [
            {"Items": [_make_item("d1")], "LastEvaluatedKey": {"PK": "x"}},
            {"Items": [_make_item("d2")]},
        ]
        dal = MetadataEnrichmentDAL(dynamodb_resource=mock_ddb)
        result = dal.get_filter_options()
        assert mock_table.scan.call_count == 2
        # Both items should be counted
        ta_values = [x["value"] for x in result["therapeutic_area"]]
        assert "CRM" in ta_values

    def test_missing_fields_handled_gracefully(self):
        """Items with missing brand/indication/year should not crash."""
        items = [
            {"document_id": "d1", "status": "published", "therapeutic_area": "CRM"},
            {"document_id": "d2", "status": "published"},
        ]
        dal = self._make_dal_with_items(items)
        result = dal.get_filter_options()
        # Should not raise, brand/indication lists may be empty
        assert isinstance(result["brand"], list)


# ===========================================================================
# is_within_days static method tests
# ===========================================================================
class TestIsWithinDays:
    def test_recent_date_within_7_days(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        assert MetadataEnrichmentDAL.is_within_days(recent, 7) is True

    def test_old_date_outside_7_days(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        assert MetadataEnrichmentDAL.is_within_days(old, 7) is False

    def test_empty_string(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        assert MetadataEnrichmentDAL.is_within_days("", 7) is False

    def test_none_value(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        assert MetadataEnrichmentDAL.is_within_days(None, 7) is False

    def test_invalid_date_string(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        assert MetadataEnrichmentDAL.is_within_days("not-a-date", 7) is False

    def test_custom_days_param(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        assert MetadataEnrichmentDAL.is_within_days(recent, 1) is False
        assert MetadataEnrichmentDAL.is_within_days(recent, 3) is True

    def test_utc_z_suffix(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        recent = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert MetadataEnrichmentDAL.is_within_days(recent, 1) is True


# ===========================================================================
# SearchDAL alias test
# ===========================================================================
class TestSearchDALAlias:
    def test_searchdal_is_same_class(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL, SearchDAL

        assert SearchDAL is MetadataEnrichmentDAL


# ===========================================================================
# Constructor / initialization tests
# ===========================================================================
class TestDALInit:
    def test_default_dynamodb_resource_used(self):
        """When no resource is passed, boto3.resource is used."""
        with patch("services.metadata_enrichment.boto3.resource") as mock_boto:
            mock_resource = MagicMock()
            mock_boto.return_value = mock_resource

            from services.metadata_enrichment import MetadataEnrichmentDAL

            dal = MetadataEnrichmentDAL()
            mock_boto.assert_called_once_with("dynamodb", region_name="us-east-1")

    def test_custom_resource_injected(self):
        from services.metadata_enrichment import MetadataEnrichmentDAL

        custom_resource = MagicMock()
        dal = MetadataEnrichmentDAL(dynamodb_resource=custom_resource)
        assert dal._dynamodb is custom_resource
