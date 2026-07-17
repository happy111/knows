"""KB Search Strategy — dual-KB business logic shared by Search Lambda and Agent.

Decides WHICH KBs to search based on user's access level (ta_access).
Uses kb_client.py (transport layer) for the actual HTTP calls.

Architecture:
    Consumer (Search Lambda / Agent)
      → search_with_access(query, user_context, filters, max_results)
        → Strategy decides: market only OR market + market_restricted (parallel)
          → kb_client.search(appname="market", ...)
          → kb_client.search(appname="market_restricted", user_group=[...])
        → Merge, deduplicate, return

Usage (Search Lambda):
    from shared.kb_search_strategy import search_with_access
    results = search_with_access(query, user_context, filters, max_results)
    # results.chunks → for enrichment + pagination
    # results.text → not used by Search (used by Agent)

Usage (Agent):
    from shared.kb_search_strategy import search_with_access
    results = search_with_access(query, user_context, max_results=10)
    # results.text → feed into LLM context window
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aws_lambda_powertools import Logger

from shared.kb_client import KBSearchResult, KBSearchClient, create_kb_client

logger = Logger(child=True)

_KB_MARKET = "market"
_KB_MARKET_RESTRICTED = "market_restricted"


@dataclass
class KBStrategyResult:
    """Combined result from dual-KB search."""

    chunks: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""
    market_latency_ms: int = 0
    restricted_latency_ms: int = 0
    searched_kbs: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error


def search_with_access(
    query: str,
    user_context: Dict[str, Any],
    filters: Optional[Dict[str, Any]] = None,
    max_results: int = 25,
    kb_client: Optional[KBSearchClient] = None,
) -> KBStrategyResult:
    """Execute KB search with access-based strategy.

    Args:
        query: Search query string.
        user_context: Must contain 'ta_access' (list) and optionally 'user_groups' (list).
        filters: Optional ui_filters (therapeutic_area, brand, indication, etc.).
        max_results: Maximum results per KB call.
        kb_client: Optional override (for testing). Default: create_kb_client().

    Returns:
        KBStrategyResult with merged chunks from all searched KBs.

    Strategy:
        - ALL users: search market KB (MR + CI documents)
        - L2 users (ta_access non-empty): ALSO search market_restricted KB in parallel
        - user_group passed to restricted KB for permission-based filtering
    """
    client = kb_client or create_kb_client()
    ta_access = user_context.get("ta_access", [])
    user_groups = user_context.get("user_groups", [])
    has_restricted_access = len(ta_access) > 0

    if has_restricted_access:
        return _search_parallel(client, query, filters, user_groups, max_results)
    return _search_market_only(client, query, filters, max_results)


def _search_market_only(
    client: KBSearchClient,
    query: str,
    filters: Optional[Dict[str, Any]],
    max_results: int,
) -> KBStrategyResult:
    """Search market KB only (L1 users without ta_access)."""
    result = client.search(
        query=query,
        appname=_KB_MARKET,
        ui_filters=filters,
        max_results=max_results,
    )

    if not result.success:
        logger.error("Market KB search failed: %s", result.error)
        return KBStrategyResult(error=result.error)

    for chunk in result.chunks:
        chunk.setdefault("source_kb", _KB_MARKET)

    return KBStrategyResult(
        chunks=result.chunks,
        text=result.text,
        market_latency_ms=result.latency_ms,
        searched_kbs=[_KB_MARKET],
    )


def _search_parallel(
    client: KBSearchClient,
    query: str,
    filters: Optional[Dict[str, Any]],
    user_groups: List[str],
    max_results: int,
) -> KBStrategyResult:
    """Search both market and market_restricted KBs in parallel."""
    market_result, restricted_result = _execute_parallel(
        client, query, filters, user_groups, max_results
    )
    return _merge_results(market_result, restricted_result)


def _execute_parallel(
    client: KBSearchClient,
    query: str,
    filters: Optional[Dict[str, Any]],
    user_groups: List[str],
    max_results: int,
) -> tuple:
    """Fire both KB calls concurrently. Returns (market_result, restricted_result)."""
    market_result = KBSearchResult()
    restricted_result = KBSearchResult()

    with ThreadPoolExecutor(max_workers=2) as executor:
        market_future = executor.submit(
            client.search, query=query, appname=_KB_MARKET,
            ui_filters=filters, max_results=max_results,
        )
        restricted_future = executor.submit(
            client.search, query=query, appname=_KB_MARKET_RESTRICTED,
            ui_filters=filters, user_group=user_groups or None, max_results=max_results,
        )

        for future in as_completed([market_future, restricted_future]):
            try:
                if future == market_future:
                    market_result = future.result()
                else:
                    restricted_result = future.result()
            except Exception as e:
                logger.error("KB parallel search error: %s", e)

    return market_result, restricted_result


def _collect_kb_result(
    result: KBSearchResult, kb_name: str,
    chunks: List[Dict[str, Any]], text_parts: List[str], searched: List[str],
) -> None:
    """Collect a single KB result into the accumulator lists."""
    if result.success:
        for chunk in result.chunks:
            chunk.setdefault("source_kb", kb_name)
        chunks.extend(result.chunks)
        text_parts.append(result.text)
        searched.append(kb_name)
    else:
        logger.warning("%s KB failed: %s", kb_name, result.error)


def _merge_results(market_result: KBSearchResult, restricted_result: KBSearchResult) -> KBStrategyResult:
    """Merge results from both KBs into a single KBStrategyResult."""
    all_chunks = []
    all_text_parts = []
    searched = []

    _collect_kb_result(market_result, _KB_MARKET, all_chunks, all_text_parts, searched)
    _collect_kb_result(restricted_result, _KB_MARKET_RESTRICTED, all_chunks, all_text_parts, searched)

    if not all_chunks and not all_text_parts:
        return KBStrategyResult(error="Both KBs failed")

    return KBStrategyResult(
        chunks=all_chunks,
        text="\n\n".join(all_text_parts),
        market_latency_ms=market_result.latency_ms,
        restricted_latency_ms=restricted_result.latency_ms,
        searched_kbs=searched,
    )
