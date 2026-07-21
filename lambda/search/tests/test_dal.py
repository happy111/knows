"""Tests for Metadata Enrichment DAL (DynamoDB access layer)."""
import pytest
from unittest.mock import MagicMock, patch

from dal.metadata_adapter import MetadataEnrichmentDAL


class TestMetadataEnrichmentDAL:
    """Test DynamoDB queries for metadata enrichment and filter options."""

    def setup_method(self):
        self.mock_dynamodb = MagicMock()
        self.mock_metadata_table = MagicMock()
        self.mock_taxonomy_table = MagicMock()

        def table_factory(name):
            if "metadata" in name:
                return self.mock_metadata_table
            elif "taxonomy" in name:
                return self.mock_taxonomy_table
            return MagicMock()

        self.mock_dynamodb.Table.side_effect = table_factory
        self.dal = MetadataEnrichmentDAL(dynamodb_resource=self.mock_dynamodb)

    def test_batch_get_metadata_returns_map(self, sample_metadata):
        items = list(sample_metadata.values())
        self.mock_dynamodb.batch_get_item.return_value = {
            "Responses": {"know-metadata-test": items},
            "UnprocessedKeys": {},
        }

        result = self.dal.batch_get_metadata(["doc-001", "doc-002", "doc-003", "doc-004"])

        assert len(result) == 4
        assert result["doc-001"]["title"] == "Leqvio Market Report Q4 2024"
        assert result["doc-002"]["brand"] == "Cosentyx"

    def test_batch_get_metadata_empty_ids(self):
        result = self.dal.batch_get_metadata([])
        assert result == {}

    def test_batch_get_metadata_handles_pagination(self, sample_metadata):
        """DDB BatchGetItem handles max 100 items per call."""
        items = list(sample_metadata.values())
        self.mock_dynamodb.batch_get_item.return_value = {
            "Responses": {"know-metadata-test": items},
            "UnprocessedKeys": {},
        }

        # Generate 150 IDs to test batching
        doc_ids = [f"doc-{i:03d}" for i in range(150)]
        self.dal.batch_get_metadata(doc_ids)

        # Should be called twice (100 + 50)
        assert self.mock_dynamodb.batch_get_item.call_count == 2

    def test_batch_get_metadata_handles_unprocessed_keys(self, sample_metadata):
        items = list(sample_metadata.values())[:2]
        remaining = list(sample_metadata.values())[2:]

        self.mock_dynamodb.batch_get_item.side_effect = [
            {
                "Responses": {"know-metadata-test": items},
                "UnprocessedKeys": {"know-metadata-test": {"Keys": [{"document_id": "doc-003"}]}},
            },
            {
                "Responses": {"know-metadata-test": remaining},
                "UnprocessedKeys": {},
            },
        ]

        result = self.dal.batch_get_metadata(["doc-001", "doc-002", "doc-003", "doc-004"])

        assert len(result) == 4

    def test_get_filter_options_aggregates_correctly(self):
        self.mock_metadata_table.scan.return_value = {
            "Items": [
                {"therapeutic_area": "CRM", "brand": "Leqvio", "indication": "ASCVD", "category": "MR", "date_range": "2024", "status": "published"},
                {"therapeutic_area": "CRM", "brand": "Leqvio", "indication": "HeFH", "category": "MR", "date_range": "2024", "status": "published"},
                {"therapeutic_area": "Immunology", "brand": "Cosentyx", "indication": "Psoriasis", "category": "CI", "date_range": "2023", "status": "published"},
            ]
        }

        result = self.dal.get_filter_options()

        ta_values = {f["value"]: f["count"] for f in result["therapeutic_area"]}
        assert ta_values["CRM"] == 2
        assert ta_values["Immunology"] == 1

        brand_values = {f["value"]: f["count"] for f in result["brand"]}
        assert brand_values["Leqvio"] == 2
        assert brand_values["Cosentyx"] == 1

    def test_get_filter_options_with_ta_filter(self):
        self.mock_metadata_table.scan.return_value = {
            "Items": [
                {"therapeutic_area": "CRM", "brand": "Leqvio", "indication": "ASCVD", "category": "MR", "date_range": "2024", "status": "published"},
                {"therapeutic_area": "CRM", "brand": "Entresto", "indication": "HF", "category": "MR", "date_range": "2024", "status": "published"},
                {"therapeutic_area": "Immunology", "brand": "Cosentyx", "indication": "Psoriasis", "category": "CI", "date_range": "2023", "status": "published"},
            ]
        }

        result = self.dal.get_filter_options(ta_filter="CRM")

        # Brands should only include CRM brands
        brand_values = {f["value"]: f["count"] for f in result["brand"]}
        assert "Leqvio" in brand_values
        assert "Entresto" in brand_values
        assert "Cosentyx" not in brand_values

        # TAs should still show all (for display in dropdown)
        ta_values = {f["value"]: f["count"] for f in result["therapeutic_area"]}
        assert "CRM" in ta_values
        assert "Immunology" in ta_values

    def test_get_filter_options_handles_pagination(self):
        self.mock_metadata_table.scan.side_effect = [
            {
                "Items": [{"therapeutic_area": "CRM", "brand": "Leqvio", "category": "MR", "date_range": "2024", "status": "published"}],
                "LastEvaluatedKey": {"PK": "doc-1"},
            },
            {
                "Items": [{"therapeutic_area": "Oncology", "brand": "Kisqali", "category": "MR", "date_range": "2023", "status": "published"}],
            },
        ]

        result = self.dal.get_filter_options()

        ta_values = {f["value"] for f in result["therapeutic_area"]}
        assert "CRM" in ta_values
        assert "Oncology" in ta_values

    def test_get_filter_options_date_range(self):
        self.mock_metadata_table.scan.return_value = {
            "Items": [
                {"therapeutic_area": "CRM", "date_range": "2022", "status": "published"},
                {"therapeutic_area": "CRM", "date_range": "2024", "status": "published"},
                {"therapeutic_area": "CRM", "date_range": "2023", "status": "published"},
            ]
        }

        result = self.dal.get_filter_options()

        assert result["date_range"]["min"] == "2022-01-01"
        assert result["date_range"]["max"] == "2024-12-31"

    def test_is_within_days_true(self):
        from datetime import datetime, timezone

        recent_date = datetime.now(timezone.utc).isoformat()
        assert MetadataEnrichmentDAL.is_within_days(recent_date, days=7) is True

    def test_is_within_days_false(self):
        assert MetadataEnrichmentDAL.is_within_days("2020-01-01T00:00:00Z", days=7) is False

    def test_is_within_days_empty(self):
        assert MetadataEnrichmentDAL.is_within_days("", days=7) is False
        assert MetadataEnrichmentDAL.is_within_days(None, days=7) is False

    def test_is_within_days_invalid_format(self):
        assert MetadataEnrichmentDAL.is_within_days("not-a-date", days=7) is False

    # -----------------------------------------------------------------------
    # List-typed field handling (brand, indication, ta stored as DynamoDB lists)
    # -----------------------------------------------------------------------
    def test_get_filter_options_handles_list_brand(self):
        """brand stored as a list in DynamoDB should be counted per element, not crash."""
        self.mock_metadata_table.scan.return_value = {
            "Items": [
                {"therapeutic_area": "CRM", "brand": ["Leqvio", "Entresto"], "indication": "ASCVD", "category": "MR", "date_range": "2024", "status": "published"},
                {"therapeutic_area": "CRM", "brand": "Leqvio", "indication": "HF", "category": "MR", "date_range": "2024", "status": "published"},
            ]
        }

        result = self.dal.get_filter_options()

        brand_values = {f["value"]: f["count"] for f in result["brand"]}
        assert brand_values["Leqvio"] == 2
        assert brand_values["Entresto"] == 1

    def test_get_filter_options_handles_list_ta_and_indication(self):
        """therapeutic_area and indication stored as lists should be handled."""
        self.mock_metadata_table.scan.return_value = {
            "Items": [
                {"therapeutic_area": ["CRM", "Oncology"], "brand": "BrandA", "indication": ["Ind1", "Ind2"], "category": "MR", "date_range": "2024", "status": "published"},
            ]
        }

        result = self.dal.get_filter_options()

        ta_values = {f["value"]: f["count"] for f in result["therapeutic_area"]}
        assert "CRM" in ta_values
        assert "Oncology" in ta_values

        ind_values = {f["value"]: f["count"] for f in result["indication"]}
        assert "Ind1" in ind_values
        assert "Ind2" in ind_values

    def test_get_filter_options_ta_filter_with_list_ta(self):
        """ta_filter should correctly intersect when therapeutic_area is a list."""
        self.mock_metadata_table.scan.return_value = {
            "Items": [
                {"therapeutic_area": ["CRM", "Oncology"], "brand": "BrandA", "indication": "Ind1", "category": "MR", "date_range": "2024", "status": "published"},
                {"therapeutic_area": "Immunology", "brand": "BrandB", "indication": "Ind2", "category": "CI", "date_range": "2024", "status": "published"},
            ]
        }

        result = self.dal.get_filter_options(ta_filter="CRM")

        brand_values = {f["value"] for f in result["brand"]}
        assert "BrandA" in brand_values
        assert "BrandB" not in brand_values
