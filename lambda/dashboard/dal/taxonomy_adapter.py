"""Metadata Adapter — DynamoDB access for kNOW-Metadata table.

Handles all read operations against the document metadata table.
Services NEVER call boto3 directly — they go through this adapter.
This ensures a single point of change if table schema or access patterns evolve.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Attr

from core.clients import get_dynamodb_resource
from core.config import KNOW_METADATA_TABLE
from core.logger import get_logger

logger = get_logger(__name__)

_BROWSE_FIELDS = (
    "document_id, title, file_name, file_type, summary, is_restricted, "
    "project_type, therapeutic_area, brand, indication, category, #func, published_at, #src"
)
_BROWSE_EXPR_NAMES = {"#src": "source", "#func": "function"}

_STATS_FIELDS = "document_id, title, brand, therapeutic_area, #func, category, published_at, #src"


class MetadataAdapter:
    """Reads published documents from the kNOW-Metadata DynamoDB table.

    Table schema (per CDK cdk_dynamodb.py):
        PK: document_id (String, simple key)
        GSIs: ta-date-index, category-index, hash-index, kb-status-index, ta-brand-index
        Attributes: title, brand, ta, category, status, published_at, file_type, etc.
    """

    def __init__(self, dynamodb_resource=None):
        self._dynamodb = dynamodb_resource or get_dynamodb_resource()
        self._table = self._dynamodb.Table(KNOW_METADATA_TABLE)

    def get_published_documents(self) -> List[Dict[str, Any]]:
        """Scan all documents with status='published'.

        Used by StatsService to compute aggregations.
        Returns fields needed for stats + new monthly/upload counts.
        """
        items: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {
            "FilterExpression": Attr("status").eq("published"),
            "ProjectionExpression": _STATS_FIELDS,
            "ExpressionAttributeNames": _BROWSE_EXPR_NAMES,
        }

        while True:
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        logger.info("Scanned %d published documents from %s", len(items), KNOW_METADATA_TABLE)
        return items

    @staticmethod
    def _apply_multi_value_filter(filter_expr, attr_name: str, csv_value: str, use_contains: bool = False):
        """Apply a multi-value (comma-separated) filter using eq/is_in/contains."""
        values = [v.strip() for v in csv_value.split(",")]
        if len(values) == 1:
            condition = Attr(attr_name).contains(values[0]) if use_contains else Attr(attr_name).eq(values[0])
        else:
            condition = Attr(attr_name).is_in(values)
        return filter_expr & condition

    def get_browse_documents(
        self,
        ta: Optional[str] = None,
        brand: Optional[str] = None,
        indication: Optional[str] = None,
        project_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get published documents with optional filters for browse.

        All params optional — no filter means all published docs (root level).
        Multi-value params (comma-separated) use OR logic via is_in().
        Returns fields needed for document cards + children_counts computation.
        """
        filter_expr = Attr("status").eq("published")

        # TA, Brand, Indication filters use OR logic (union of results)
        or_conditions = []
        if ta:
            values = [v.strip() for v in ta.split(",")]
            if len(values) == 1:
                or_conditions.append(Attr("therapeutic_area").eq(values[0]))
            else:
                or_conditions.append(Attr("therapeutic_area").is_in(values))
        if brand:
            values = [v.strip() for v in brand.split(",")]
            if len(values) == 1:
                or_conditions.append(Attr("brand").eq(values[0]))
            else:
                or_conditions.append(Attr("brand").is_in(values))
        if indication:
            values = [v.strip() for v in indication.split(",")]
            if len(values) == 1:
                or_conditions.append(Attr("indication").contains(values[0]))
            else:
                # OR across multiple indication values
                ind_cond = Attr("indication").contains(values[0])
                for v in values[1:]:
                    ind_cond = ind_cond | Attr("indication").contains(v)
                or_conditions.append(ind_cond)

        if or_conditions:
            combined_or = or_conditions[0]
            for cond in or_conditions[1:]:
                combined_or = combined_or | cond
            filter_expr = filter_expr & (combined_or)

        # project_type, date filters remain AND (narrowing filters)
        if project_type:
            filter_expr = self._apply_multi_value_filter(filter_expr, "project_type", project_type)
        if date_from:
            filter_expr = filter_expr & Attr("published_at").gte(date_from)
        if date_to:
            filter_expr = filter_expr & Attr("published_at").lte(date_to)

        items: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {
            "FilterExpression": filter_expr,
            "ProjectionExpression": _BROWSE_FIELDS,
            "ExpressionAttributeNames": _BROWSE_EXPR_NAMES,
        }

        while True:
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        return items

    def get_documents_by_ta(
        self, ta: str, brand: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Legacy method — kept for backward compatibility. Use get_browse_documents instead."""
        return self.get_browse_documents(ta=ta, brand=brand)
