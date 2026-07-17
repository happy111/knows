"""Taxonomy Service — Sidebar browse tree and document browsing.

Builds the 4-level hierarchy for the left sidebar navigation:
    L1 (Therapeutic Area) → L2 (Brand) → L3 (Indication) → L4 (Document Type)

Data source:
    The entire hierarchy is stored in kNOW-Taxonomy DynamoDB table (NOT hardcoded).
    Adding/removing nodes = inserting/deleting rows in DynamoDB. No code changes.

Variable depth:
    Not all branches have all 4 levels today. The tree builder handles this —
    it links children to parents using parent_id, regardless of depth.
    e.g., If Oncology > Kisqali has no L3/L4 yet, the tree simply shows 2 levels.

Sonar complexity:
    Tree building uses a 2-pass approach (index then link) to keep
    cyclomatic complexity low. No recursion, no nested loops > 2 deep.
"""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from core.config import CACHE_TTL
from core.clients import get_cache_client
from core.logger import get_logger
from dal.metadata_adapter import MetadataAdapter
from dal.taxonomy_adapter import TaxonomyAdapter
from models.dashboard_models import BrowseDocsResponse, BrowseTreeNode
from shared.constants import get_capabilities

logger = get_logger(__name__)

# Level mapping from DynamoDB PK to display label
LEVEL_LABELS = {"L1": "TA", "L2": "Brand", "L3": "Indication", "L4": "Category"}

# Category shortcode → full display name (used for L4 children_counts)
_CATEGORY_DISPLAY = {
    "MR": "Market Research",
    "CI": "Competitive Intelligence",
    "IPST": "IPST Documents",
    "PV": "Performance Vigilance",
    "LT": "LT Updates",
}

# Reverse mapping: display name → shortcode (used for tree node value field)
_CATEGORY_VALUE = {v: k for k, v in _CATEGORY_DISPLAY.items()}


class TaxonomyService:
    """Provides sidebar browse tree and paginated document browsing.

    Browse tree flow:
        1. Check Redis cache (key: "dashboard:browse_tree")
        2. If miss: scan kNOW-Taxonomy via TaxonomyAdapter
        3. Build tree using parent_id linkage (supports L1–L4)
        4. Cache and return

    Browse docs flow:
        1. Check cache (key pattern: "dashboard:browse_docs:{ta}:{brand}:{page}:{size}")
        2. If miss: scan kNOW-Metadata via MetadataAdapter (published only)
        3. Sort, paginate in-memory, return

    Dependencies (injected for testability):
        - TaxonomyAdapter: reads hierarchy from DynamoDB
        - MetadataAdapter: reads documents from DynamoDB
        - Redis cache: optional (graceful degradation)
    """

    def __init__(
        self,
        taxonomy_adapter: TaxonomyAdapter = None,
        metadata_adapter: MetadataAdapter = None,
        cache_client=None,
    ):
        self._taxonomy = taxonomy_adapter or TaxonomyAdapter()
        self._metadata = metadata_adapter or MetadataAdapter()
        self._cache = cache_client if cache_client is not None else get_cache_client()

    # -----------------------------------------------------------------------
    # Browse Tree
    # -----------------------------------------------------------------------

    def get_browse_tree(self) -> List[Dict[str, Any]]:
        """Get sidebar browse tree (L1 → L2 → L3 → L4).

        Returns list of L1 (TA) nodes, each with nested children.
        Tree is built dynamically from kNOW-Taxonomy DynamoDB table.
        """
        cache_key = "dashboard:browse_tree"
        cached_result = self._read_cache(cache_key)
        if cached_result is not None:
            return cached_result

        nodes = self._taxonomy.get_all_nodes()
        tree = self._build_tree(nodes)

        self._write_cache(cache_key, tree, CACHE_TTL)
        return tree

    # -----------------------------------------------------------------------
    # Browse Documents
    # -----------------------------------------------------------------------

    def get_browse_docs(
        self,
        ta: str = "",
        brand: str = "",
        indication: str = "",
        project_type: str = "",
        date_from: str = "",
        date_to: str = "",
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "published_at",
        sort_order: str = "desc",
        role: str = "L1",
        capabilities: dict = None,
    ) -> Dict[str, Any]:
        """Get paginated documents with optional filters + children counts.

        Args:
            ta: Therapeutic area filter (optional — empty means all)
            brand: Brand filter (optional)
            indication: Indication filter (optional)
            project_type: Document type filter (optional)
            date_from: Start date filter ISO (optional)
            date_to: End date filter ISO (optional)
            page: Page number (1-based)
            page_size: Items per page (default 10, configurable)
            sort_by: Sort field — "published_at" or "title"
            sort_order: "desc" (newest first) or "asc"

        Returns:
            {children_counts: [...], children_level: str, documents: [...], pagination: {...}}
        """
        cache_key = (
            f"dashboard:browse_docs:{ta}:{brand}:{indication}:"
            f"{project_type}:{date_from}:{date_to}:{page}:{page_size}:{sort_by}:{sort_order}"
        )
        cached_result = self._read_cache(cache_key)
        if cached_result is not None:
            return cached_result

        all_docs = self._metadata.get_browse_documents(
            ta=ta or None,
            brand=brand or None,
            indication=indication or None,
            project_type=project_type or None,
            date_from=date_from or None,
            date_to=date_to or None,
        )

        reverse = sort_order == "desc"
        all_docs.sort(key=lambda d: d.get(sort_by, ""), reverse=reverse)

        children_level = self._determine_children_level(ta, brand, indication, project_type)
        children_counts = self._compute_children_counts(all_docs, ta, brand, indication, project_type)

        result = self._paginate_docs(all_docs, page, page_size, role, capabilities)
        result["children_counts"] = children_counts
        result["children_level"] = children_level

        self._write_cache(cache_key, result, CACHE_TTL // 2)
        return result

    @staticmethod
    def _determine_children_level(ta: str, brand: str, indication: str, project_type: str) -> str:
        """Determine what level the children cards represent."""
        if project_type:
            return ""
        if indication:
            return "Document Type"
        if brand:
            return "Indication"
        if ta:
            return "Brand"
        return "Therapeutic Area"

    def _compute_children_counts(
        self, docs: List[Dict[str, Any]], ta: str, brand: str, indication: str, project_type: str
    ) -> List[Dict[str, Any]]:
        """Compute counts for the next-level children cards.

        Includes taxonomy nodes with 0 documents so the UI always shows
        all brands/indications even if no docs exist yet.

        At L4 (indication→category), maps shortcodes (MR/CI) to full names
        (Market Research/Competitive Intelligence) to match taxonomy node names.
        """
        if project_type:
            return []

        field, parent_name = self._get_count_field_and_parent(ta, brand, indication)
        is_category_level = field == "category"

        counter: Counter = Counter()
        for doc in docs:
            self._count_doc_field(doc, field, is_category_level, counter)

        all_children_names = self._get_taxonomy_children_names(parent_name)

        for name in all_children_names:
            if name not in counter:
                counter[name] = 0

        return sorted(
            [{"name": k, "count": v} for k, v in counter.items()],
            key=lambda x: (-x["count"], x["name"]),
        )

    @staticmethod
    def _count_doc_field(doc: Dict[str, Any], field: str, is_category_level: bool, counter: Counter) -> None:
        """Extract and count values from a single document's field."""
        value = doc.get(field, "")
        if not value:
            return
        values = value if isinstance(value, list) else [value]
        for v in values:
            if v:
                display_name = _CATEGORY_DISPLAY.get(v, v) if is_category_level else v
                counter[display_name] += 1

    @staticmethod
    def _get_count_field_and_parent(ta: str, brand: str, indication: str) -> Tuple[str, str]:
        """Determine which DDB field to count and which taxonomy parent to query."""
        if indication:
            return "category", indication
        if brand:
            return "indication", brand
        if ta:
            return "brand", ta
        return "therapeutic_area", "root"

    def _get_taxonomy_children_names(self, parent_name: str) -> List[str]:
        """Get all child node names from taxonomy for a given parent.

        Uses cached taxonomy nodes to avoid redundant DDB scans.
        """
        if not hasattr(self, "_taxonomy_nodes_cache") or self._taxonomy_nodes_cache is None:
            self._taxonomy_nodes_cache = self._taxonomy.get_all_nodes()
        return [
            node.get("name", "") for node in self._taxonomy_nodes_cache
            if node.get("parent_id", "") == parent_name and node.get("name")
        ]

    # -----------------------------------------------------------------------
    # Tree Building (4-level, Sonar-friendly: 2-pass index + link)
    # -----------------------------------------------------------------------

    def _build_tree(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build hierarchical tree from flat taxonomy nodes.

        Algorithm (2-pass, O(n) — no recursion, no deep nesting):
            Pass 1: Create BrowseTreeNode for every item, keyed by unique_id.
            Pass 2: Link each non-root node to its parent via parent_id.

        Unique ID: "{parent_id}#{name}" — handles duplicate names across branches
        (e.g., "Market Research" under multiple indications).

        This approach works for any number of levels without code changes.
        Adding L5, L6, etc. in DynamoDB would automatically build deeper trees.
        """
        node_map, name_to_ids = self._index_nodes(nodes)
        root_nodes = self._link_children(nodes, node_map, name_to_ids)
        return [node.to_dict() for node in root_nodes]

    def _index_nodes(
        self, items: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, BrowseTreeNode], Dict[str, List[str]]]:
        """Pass 1: Create BrowseTreeNode for each item.

        Returns:
            node_map: {unique_id: BrowseTreeNode} — unique_id = "parent#name"
            name_to_ids: {name: [unique_id, ...]} — for parent lookups
        """
        node_map: Dict[str, BrowseTreeNode] = {}
        name_to_ids: Dict[str, List[str]] = {}

        for item in items:
            name = self._get_node_name(item)
            parent_id = item.get("parent_id", "root")
            level = self._get_level_label(item)
            sort_order = int(item.get("order", 0))

            unique_id = f"{parent_id}#{name}"
            icon_key = item.get("icon_key", name.lower().replace(" ", "-").replace(",", ""))
            value = _CATEGORY_VALUE.get(name, "") if level == "Category" else ""
            node_map[unique_id] = BrowseTreeNode(
                id=name, name=name, level=level, sort_order=sort_order, icon_key=icon_key, value=value
            )

            if name not in name_to_ids:
                name_to_ids[name] = []
            name_to_ids[name].append(unique_id)

        return node_map, name_to_ids

    def _link_children(
        self,
        items: List[Dict[str, Any]],
        node_map: Dict[str, BrowseTreeNode],
        name_to_ids: Dict[str, List[str]],
    ) -> List[BrowseTreeNode]:
        """Pass 2: Link children to parents. Return root-level nodes.

        Uses name_to_ids to find the parent's unique_id(s) when linking.
        """
        roots: List[BrowseTreeNode] = []

        for item in items:
            name = self._get_node_name(item)
            parent_id = item.get("parent_id", "")
            unique_id = f"{parent_id}#{name}"
            node = node_map.get(unique_id)
            if not node:
                continue

            if parent_id == "root" or not parent_id:
                roots.append(node)
            else:
                self._attach_to_parent(node, parent_id, node_map, name_to_ids)

        self._sort_all_children(node_map, roots)
        return roots

    @staticmethod
    def _attach_to_parent(
        node: BrowseTreeNode,
        parent_name: str,
        node_map: Dict[str, BrowseTreeNode],
        name_to_ids: Dict[str, List[str]],
    ) -> None:
        """Attach a child node to its parent by finding parent's unique_id."""
        parent_ids = name_to_ids.get(parent_name, [])
        for pid in parent_ids:
            parent_node = node_map.get(pid)
            if parent_node:
                parent_node.children.append(node)
                break

    @staticmethod
    def _sort_all_children(
        node_map: Dict[str, BrowseTreeNode], roots: List[BrowseTreeNode]
    ) -> None:
        """Sort children at every level by sort_order for consistent display."""
        for node in node_map.values():
            node.children.sort(key=lambda n: n.sort_order)
        roots.sort(key=lambda n: n.sort_order)

    # -----------------------------------------------------------------------
    # Helpers (keep main methods short, Sonar complexity < 10)
    # -----------------------------------------------------------------------

    @staticmethod
    def _get_node_name(item: Dict[str, Any]) -> str:
        """Extract display name from DynamoDB item."""
        return item.get("name", "")

    @staticmethod
    def _get_level_label(item: Dict[str, Any]) -> str:
        """Map DynamoDB PK (nav#L1, nav#L2...) to human-readable level label."""
        pk = item.get("PK", item.get("type_level", ""))
        for key, label in LEVEL_LABELS.items():
            if key in pk:
                return label
        return "Unknown"

    @staticmethod
    def _paginate_docs(
        docs: List[Dict[str, Any]], page: int, page_size: int,
        role: str = "L1", capabilities: dict = None
    ) -> Dict[str, Any]:
        """Paginate a sorted list of documents and return response dict."""
        total = len(docs)
        start = (page - 1) * page_size
        page_docs = docs[start: start + page_size]

        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        # Compute system capabilities for document restriction check
        ta_access = (capabilities or {}).get("ta_access", [])
        system_capabilities = get_capabilities(role, ta_access)

        response = BrowseDocsResponse(
            documents=[
                {
                    "document_id": doc.get("document_id", ""),
                    "title": doc.get("title", ""),
                    "file_name": doc.get("file_name", ""),
                    "file_type": doc.get("file_type", ""),
                    "summary": (doc.get("summary", "") or "")[:200],
                    "project_type": doc.get("project_type", ""),
                    "therapeutic_area": doc.get("therapeutic_area", ""),
                    "brand": doc.get("brand", ""),
                    "indication": doc.get("indication", ""),
                    "category": doc.get("category", ""),
                    "published_at": doc.get("published_at", ""),
                    "is_new": doc.get("published_at", "") >= cutoff,
                    "is_restricted": not (
                        (system_capabilities.get("view_general") and doc.get("category") in ["MR", "CI", "Competitive Intelligence", "Market Research"])
                        or (system_capabilities.get("view_restricted") and doc.get("therapeutic_area") in ta_access)
                    ),
                }
                for doc in page_docs
            ],
            page=page,
            page_size=page_size,
            total=total,
        )
        return response.to_dict()

    def _read_cache(self, key: str) -> Any:
        """Read from Redis cache. Returns parsed JSON or None."""
        if not self._cache:
            return None
        try:
            cached = self._cache.get(key)
            if cached:
                logger.info("Cache HIT: %s", key)
                return json.loads(cached)
        except Exception:
            pass
        return None

    def _write_cache(self, key: str, data: Any, ttl: int) -> None:
        """Write to Redis cache (best-effort, failures are non-fatal)."""
        if not self._cache:
            return
        try:
            self._cache.setex(key, ttl, json.dumps(data, default=str))
            logger.info("Cache SET: %s (TTL=%ds)", key, ttl)
        except Exception:
            pass
