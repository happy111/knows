"""Metadata Enrichment DAL — DynamoDB queries for search enrichment and filters.

Provides:
  - Batch metadata fetch (kNOW-Metadata table)
  - Filter option aggregation (kNOW-Metadata + kNOW-Taxonomy)

Table naming convention: know-{suffix}-{env} (e.g., know-metadata-dev)
"""
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Attr

logger = Logger(child=True)

AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
METADATA_TABLE = os.environ.get("KNOW_METADATA_TABLE", "know-metadata-dev")
TAXONOMY_TABLE = os.environ.get("KNOW_TAXONOMY_TABLE", "know-taxonomy-dev")

_BATCH_SIZE = 100


def _normalize_field(value) -> List[str]:
    """Normalise a DynamoDB field that may be a str or list into a list of non-empty strings."""
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if value:
        return [str(value)]
    return []


class MetadataEnrichmentDAL:
    """Data access for search enrichment and filter options."""

    def __init__(self, dynamodb_resource=None):
        self._dynamodb = dynamodb_resource or boto3.resource("dynamodb", region_name=AWS_REGION)
        self._metadata_table = self._dynamodb.Table(METADATA_TABLE)
        self._taxonomy_table = self._dynamodb.Table(TAXONOMY_TABLE)

    def batch_get_metadata(self, document_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch get document metadata from kNOW-Metadata."""
        if not document_ids:
            return {}

        result = {}
        for i in range(0, len(document_ids), _BATCH_SIZE):
            batch = document_ids[i : i + _BATCH_SIZE]
            keys = [{"document_id": doc_id} for doc_id in batch]
            self._fetch_batch(keys, result)

        return result

    def _fetch_batch(self, keys: List[Dict], result: Dict[str, Dict[str, Any]]) -> None:
        response = self._dynamodb.batch_get_item(
            RequestItems={METADATA_TABLE: {"Keys": keys}}
        )
        self._collect_items(response, result)

        unprocessed = response.get("UnprocessedKeys", {}).get(METADATA_TABLE, {})
        if unprocessed:
            retry_response = self._dynamodb.batch_get_item(
                RequestItems={METADATA_TABLE: {"Keys": unprocessed.get("Keys", [])}}
            )
            self._collect_items(retry_response, result)

    def _collect_items(self, response: Dict, result: Dict[str, Dict[str, Any]]) -> None:
        items = response.get("Responses", {}).get(METADATA_TABLE, [])
        for item in items:
            result[item["document_id"]] = item

    def get_filter_options(self, ta_filter: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        """Aggregate filter values from kNOW-Metadata."""
        items = self._scan_published_items()
        counters = self._aggregate_counters(items, ta_filter)
        return self._build_filter_response(counters)

    def _scan_published_items(self) -> List[Dict[str, Any]]:
        items = []
        scan_kwargs = {"FilterExpression": Attr("status").eq("published")}
        while True:
            response = self._metadata_table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        return items

    def _aggregate_counters(self, items: List[Dict], ta_filter: Optional[str]) -> Dict[str, Any]:
        ta_counter: Counter = Counter()
        brand_counter: Counter = Counter()
        indication_counter: Counter = Counter()
        doc_type_by_category: Dict[str, Counter] = {}
        years: Set[str] = set()

        ta_filter_set = set(ta_filter.split(",")) if ta_filter else None

        for item in items:
            ta_values = _normalize_field(item.get("therapeutic_area", ""))
            date_val = item.get("year", item.get("date_range", ""))

            for ta in ta_values:
                ta_counter[ta] += 1
            if date_val:
                years.add(str(date_val))

            if ta_filter_set and not ta_filter_set.intersection(ta_values):
                continue

            self._count_brand_indication(item, brand_counter, indication_counter)
            self._count_project_type(item, doc_type_by_category)

        return {
            "therapeutic_area": ta_counter,
            "brand": brand_counter,
            "indication": indication_counter,
            "doc_type_by_category": doc_type_by_category,
            "years": years,
        }

    def _count_brand_indication(self, item: Dict, brand_counter: Counter, indication_counter: Counter) -> None:
        for brand in _normalize_field(item.get("brand", "")):
            brand_counter[brand] += 1
        for indication in _normalize_field(item.get("indication", "")):
            indication_counter[indication] += 1

    def _count_project_type(self, item: Dict, doc_type_by_category: Dict[str, Counter]) -> None:
        project_types = _normalize_field(item.get("project_type", item.get("document_type", "")))
        categories = _normalize_field(item.get("category", ""))
        for category in categories:
            for project_type in project_types:
                if category not in doc_type_by_category:
                    doc_type_by_category[category] = Counter()
                doc_type_by_category[category][project_type] += 1

    def _build_filter_response(self, counters: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        years = counters["years"]
        sorted_years = sorted(years) if years else []
        date_range_result = {
            "min": f"{sorted_years[0]}-01-01" if sorted_years else "2020-01-01",
            "max": f"{sorted_years[-1]}-12-31" if sorted_years else "2026-12-31",
        }

        document_type_grouped = {
            cat: [{"value": k, "count": v} for k, v in counter.most_common()]
            for cat, counter in counters["doc_type_by_category"].items()
        }

        return {
            "therapeutic_area": [{"value": k, "count": v} for k, v in counters["therapeutic_area"].most_common(20)],
            "brand": [{"value": k, "count": v} for k, v in counters["brand"].most_common(30)],
            "indication": [{"value": k, "count": v} for k, v in counters["indication"].most_common(30)],
            "document_type": document_type_grouped,
            "date_range": date_range_result,
        }

    @staticmethod
    def is_within_days(date_str: str, days: int = 7) -> bool:
        """Check if a date string is within the last N days."""
        if not date_str:
            return False
        try:
            doc_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            return doc_date >= cutoff
        except (ValueError, TypeError):
            return False


SearchDAL = MetadataEnrichmentDAL
