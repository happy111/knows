"""Taxonomy Adapter — DynamoDB access for kNOW-Taxonomy table.

Handles read operations for the sidebar browse tree hierarchy.
The hierarchy is stored in DynamoDB, NOT hardcoded.
Business users can manage taxonomy without code changes.

4-Level Hierarchy Design (per CDK cdk_dynamodb.py):
    PK (type_level)  |  SK (name)           |  parent_id       |  order
    ─────────────────┼──────────────────────┼──────────────────┼───────
    nav#L1           |  Cardiovascular...   |  root            |  1
    nav#L2           |  Leqvio              |  Cardiovascular...|  2
    nav#L3           |  HeFH               |  Leqvio          |  3
    nav#L4           |  Market Research     |  HeFH            |  1

    L1 = Therapeutic Area (top-level, parent_id="root")
    L2 = Brand/Indication (parent_id = L1 name)
    L3 = Indication (parent_id = L2 brand name)
    L4 = Document Type (parent_id = L3 name, leaf nodes)

Variable depth:
    Not all branches are 4 levels deep. Some brands may only have L1→L2
    today. The tree builder handles this — it attaches children wherever
    a matching parent_id exists, regardless of how many levels are filled.
"""
from typing import Any, Dict, List

from core.clients import get_dynamodb_resource
from core.config import KNOW_TAXONOMY_TABLE
from core.logger import get_logger

logger = get_logger(__name__)


class TaxonomyAdapter:
    """Reads the full taxonomy hierarchy from kNOW-Taxonomy DynamoDB table.

    This table is the single source of truth for the sidebar navigation tree.
    Adding a new TA, brand, or indication = inserting one DynamoDB row.

    Table schema (per CDK cdk_dynamodb.py):
        PK: type_level (String) — "nav#L1", "nav#L2", "nav#L3", "nav#L4"
        SK: name (String) — Node display name (must be unique within its level)
        GSI: parent-index (parent_id + order)
        Attributes: parent_id (String), description (String), order (Number)

    Key constraint: Since PK=level and SK=name, node names must be unique
    within a level. If the same brand appears under multiple TAs, disambiguate
    in the name (e.g., "Rhapsido (Immunology)" vs "Rhapsido (Neuro)") or
    migrate to a UUID-based PK in a future iteration.
    """

    def __init__(self, dynamodb_resource=None):
        self._dynamodb = dynamodb_resource or get_dynamodb_resource()
        self._table = self._dynamodb.Table(KNOW_TAXONOMY_TABLE)

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Scan all taxonomy nodes across all levels (L1–L4).

        Returns raw items — tree-building logic lives in TaxonomyService.
        Adapter is responsible only for data access.

        Table size: ~100-200 rows max (7 TAs x 5 brands x 3 sub x 5 types).
        Full scan is cheaper and simpler than 4 separate queries by level.
        """
        items: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {}

        while True:
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        logger.info("Scanned %d taxonomy nodes from %s", len(items), KNOW_TAXONOMY_TABLE)
        return items

