"""KB Search Client — unified transport layer for Knowledge Base retrieval.

Strategy pattern: multiple transport implementations behind a single interface.
Factory selects which to use based on KB_ACCESS_MODE env var.

Transport Layer (this file):
    HOW to reach the KB retrieval service (REST vs MCP vs Compare).

Business Logic Layer (SearchService):
    WHICH KBs to call (market, market_restricted) based on user role.
    Lives in search/services/search_service.py — NOT here.

Usage:
    from shared.kb_client import create_kb_client

    kb = create_kb_client()
    result = kb.search(query="Leqvio market share", appname="market", max_results=10)

    # Agent uses:
    result.text        # raw formatted string for LLM context

    # Search Lambda uses:
    result.chunks      # parsed [{document_id, filename, text, score}] for enrichment

    # Latency comparison:
    result.latency_ms  # round-trip time
    result.source      # "rest" or "mcp"
"""
import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import httpx
from aws_lambda_powertools import Logger

logger = Logger(child=True)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
KB_ACCESS_MODE = os.environ.get("KB_ACCESS_MODE", "rest")
KB_REST_URL = os.environ.get("KB_REST_URL", "")
KB_MCP_URL = os.environ.get("KB_MCP_URL", "")
KB_TIMEOUT = int(os.environ.get("KB_TIMEOUT", "30"))
KB_RETRY_ATTEMPTS = int(os.environ.get("KB_RETRY_ATTEMPTS", "1"))
KB_PRIMARY = os.environ.get("KB_PRIMARY", "rest")

_VALID_APPNAMES = ("market", "market_restricted", "medical", "launch")
_MAX_QUERY_LENGTH = 2000
_MAX_RESULTS_LIMIT = 100
_RETRY_BACKOFF_BASE = 0.5

_CHUNK_PATTERN = re.compile(
    r"---\s{0,10}Chunk\s{1,5}(\d+)\s{0,10}\(Score:\s{0,5}([\d.]+)\)\s{0,10}---\s{0,5}"
    r"Document:\s{0,5}(.+?)\n"
    r"Content:\s{0,5}(.+?)(?=---\s{0,10}Chunk|\Z)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class KBSearchResult:
    """Unified result from any transport."""

    text: str = ""
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    source: str = ""
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
class KBSearchClient(Protocol):
    """Interface all transport implementations satisfy."""

    def search(
        self,
        query: str,
        appname: str = "market",
        ui_filters: Optional[Dict[str, Any]] = None,
        user_group: Optional[List[str]] = None,
        max_results: int = 10,
    ) -> KBSearchResult: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------------------------
def _validate_query(query: str) -> str:
    if not query or not query.strip():
        return ""
    return query.strip()[:_MAX_QUERY_LENGTH]


def _validate_appname(appname: str) -> str:
    if appname not in _VALID_APPNAMES:
        logger.warning("Invalid appname '%s', defaulting to 'market'", appname)
        return "market"
    return appname


def _validate_max_results(max_results: int) -> int:
    return max(1, min(max_results, _MAX_RESULTS_LIMIT))


# ---------------------------------------------------------------------------
# Shared Parser
# ---------------------------------------------------------------------------
def _parse_chunks(text: str) -> List[Dict[str, Any]]:
    """Parse KB retrieval response text into structured chunks.

    Both REST and MCP return the same format after envelope unwrapping:
        --- Chunk 1 (Score: 0.85) ---
        Document: filename.pdf
        Content: ...text...
    """
    if not text:
        return []

    text = text[:100_000]
    chunks: List[Dict[str, Any]] = []
    matches = _CHUNK_PATTERN.findall(text)

    if matches:
        for match in matches:
            _chunk_num, score, filename, content = match
            doc_id = _extract_document_id(filename.strip())
            chunks.append({
                "document_id": doc_id,
                "filename": filename.strip(),
                "text": content.strip()[:500],
                "score": float(score),
            })
        return chunks

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "results" in parsed:
            return parsed["results"]
    except (json.JSONDecodeError, TypeError):
        pass

    if text.strip():
        chunks.append({
            "document_id": "unknown",
            "filename": "",
            "text": text.strip()[:500],
            "score": 0.5,
        })

    return chunks


def _extract_document_id(filename: str) -> str:
    name = re.sub(r"\.[^.]+$", "", filename)
    doc_id = re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-")
    return doc_id or "unknown"


# ---------------------------------------------------------------------------
# HTTP Call Helper (shared by REST and MCP)
# ---------------------------------------------------------------------------
def _http_call(
    client: httpx.Client, url: str, payload: Dict, retry_attempts: int
) -> tuple:
    """POST with retry. Returns (response_json, latency_ms, error_str)."""
    last_error = ""
    request_id = str(uuid.uuid4())[:8]

    for attempt in range(retry_attempts + 1):
        start = time.time()
        try:
            response = client.post(
                url, json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Request-Id": request_id,
                },
            )
            latency_ms = int((time.time() - start) * 1000)

            if response.status_code != 200:
                logger.warning(
                    "KB call non-200",
                    status=response.status_code,
                    attempt=attempt,
                    body_preview=response.text[:200],
                )
                last_error = f"HTTP {response.status_code}"
                if attempt < retry_attempts:
                    time.sleep(_RETRY_BACKOFF_BASE * (2 ** attempt))
                    continue
                return None, latency_ms, last_error

            return response.json(), latency_ms, ""

        except httpx.TimeoutException:
            latency_ms = int((time.time() - start) * 1000)
            logger.warning("KB call timeout", latency_ms=latency_ms, attempt=attempt)
            last_error = "Request timed out"
            if attempt < retry_attempts:
                time.sleep(_RETRY_BACKOFF_BASE * (2 ** attempt))
                continue

        except httpx.ConnectError as e:
            logger.error("KB call connection failed", error=str(e))
            return None, 0, "Connection failed"

        except Exception as e:
            logger.error("KB call unexpected error", error=str(e))
            return None, 0, f"Unexpected error: {str(e)}"

    return None, 0, last_error


# ---------------------------------------------------------------------------
# Implementation: REST (API Gateway)
# ---------------------------------------------------------------------------
class RestKBClient:
    """Calls KB Retrieval API via private API Gateway.

    Endpoint: POST {KB_REST_URL}
    Body: {"query", "appname", "ui_filters", "user_group", "max_results"}
    Response: {"result": "Found N chunks...\\n--- Chunk 1 (Score: 0.87) ---\\n..."}
    """

    def __init__(self, url: Optional[str] = None, timeout: int = KB_TIMEOUT):
        self._url = url or KB_REST_URL
        self._client = httpx.Client(timeout=timeout)

    def search(
        self,
        query: str,
        appname: str = "market",
        ui_filters: Optional[Dict[str, Any]] = None,
        user_group: Optional[List[str]] = None,
        max_results: int = 10,
    ) -> KBSearchResult:
        if not self._url:
            return KBSearchResult(error="KB_REST_URL not configured", source="rest")

        payload: Dict[str, Any] = {
            "query": _validate_query(query),
            "appname": _validate_appname(appname),
            "max_results": _validate_max_results(max_results),
        }
        if ui_filters:
            payload["ui_filters"] = ui_filters
            payload["filter_logic"] = "OR"
        if user_group:
            payload["user_group"] = user_group

        result_json, latency_ms, error = _http_call(
            self._client, self._url, payload, KB_RETRY_ATTEMPTS
        )

        if error:
            return KBSearchResult(error=error, latency_ms=latency_ms, source="rest")

        if "error" in result_json:
            return KBSearchResult(
                error=result_json["error"], latency_ms=latency_ms, source="rest"
            )

        text = result_json.get("result", "")

        # Prefer structured chunks (has real document_id from AILENS DDB)
        if "chunks" in result_json and result_json["chunks"]:
            chunks = result_json["chunks"]
        else:
            # Fallback: parse text format (legacy — loses document_id)
            chunks = _parse_chunks(text)

        return KBSearchResult(
            text=text,
            chunks=chunks,
            latency_ms=latency_ms,
            source="rest",
        )

    def close(self):
        self._client.close()


# ---------------------------------------------------------------------------
# Implementation: MCP (JSON-RPC)
# ---------------------------------------------------------------------------
class MCPKBClient:
    """Calls KB search via MCP server using JSON-RPC protocol.

    Endpoint: POST {KB_MCP_URL}
    Body: {"jsonrpc": "2.0", "method": "tools/call",
           "params": {"name": "search_knowledge_base", "arguments": {...}}}
    Response: {"result": {"content": [{"type": "text", "text": "Found N chunks..."}]}}
    """

    def __init__(self, url: Optional[str] = None, timeout: int = KB_TIMEOUT):
        self._url = url or KB_MCP_URL
        self._client = httpx.Client(timeout=timeout)

    def search(
        self,
        query: str,
        appname: str = "market",
        ui_filters: Optional[Dict[str, Any]] = None,
        user_group: Optional[List[str]] = None,
        max_results: int = 10,
    ) -> KBSearchResult:
        if not self._url:
            return KBSearchResult(error="KB_MCP_URL not configured", source="mcp")

        arguments: Dict[str, Any] = {
            "query": _validate_query(query),
            "appname": _validate_appname(appname),
            "max_results": _validate_max_results(max_results),
        }
        if ui_filters:
            arguments["ui_filters"] = ui_filters
            arguments["filter_logic"] = "OR"
        if user_group:
            arguments["user_group"] = user_group

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_knowledge_base", "arguments": arguments},
        }

        result_json, latency_ms, error = _http_call(
            self._client, self._url, payload, KB_RETRY_ATTEMPTS
        )

        if error:
            return KBSearchResult(error=error, latency_ms=latency_ms, source="mcp")

        if "error" in result_json:
            return KBSearchResult(
                error=str(result_json["error"]), latency_ms=latency_ms, source="mcp"
            )

        text = self._extract_text(result_json)
        return KBSearchResult(
            text=text,
            chunks=_parse_chunks(text),
            latency_ms=latency_ms,
            source="mcp",
        )

    def _extract_text(self, result: Dict) -> str:
        """Unwrap MCP JSON-RPC response envelope to get raw text."""
        tool_result = result.get("result", {})
        content = tool_result.get("content", [])

        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block["text"])
            elif isinstance(block, str):
                text_parts.append(block)

        return "\n".join(text_parts) if text_parts else str(tool_result)

    def close(self):
        self._client.close()


# ---------------------------------------------------------------------------
# Implementation: Compare (calls both, logs latency, returns primary)
# ---------------------------------------------------------------------------
class CompareKBClient:
    """Calls both REST and MCP in parallel for latency comparison.

    Returns the result from KB_PRIMARY (default: rest).
    If primary fails, falls back to secondary.
    Logs both latencies for CloudWatch analysis.
    """

    def __init__(self):
        self._rest = RestKBClient()
        self._mcp = MCPKBClient()
        self._primary = KB_PRIMARY

    def search(
        self,
        query: str,
        appname: str = "market",
        ui_filters: Optional[Dict[str, Any]] = None,
        user_group: Optional[List[str]] = None,
        max_results: int = 10,
    ) -> KBSearchResult:
        kwargs = {
            "query": query,
            "appname": appname,
            "ui_filters": ui_filters,
            "user_group": user_group,
            "max_results": max_results,
        }

        results: Dict[str, KBSearchResult] = {}

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(self._rest.search, **kwargs): "rest",
                executor.submit(self._mcp.search, **kwargs): "mcp",
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    results[source] = future.result()
                except Exception as e:
                    results[source] = KBSearchResult(
                        error=str(e), source=source
                    )

        rest_result = results.get("rest", KBSearchResult(error="not executed", source="rest"))
        mcp_result = results.get("mcp", KBSearchResult(error="not executed", source="mcp"))

        logger.info(
            "KB access mode comparison",
            rest_ms=rest_result.latency_ms,
            rest_success=rest_result.success,
            mcp_ms=mcp_result.latency_ms,
            mcp_success=mcp_result.success,
            delta_ms=abs(rest_result.latency_ms - mcp_result.latency_ms),
            primary=self._primary,
            query_preview=query[:50],
            appname=appname,
        )

        primary_result = rest_result if self._primary == "rest" else mcp_result
        secondary_result = mcp_result if self._primary == "rest" else rest_result

        if primary_result.success:
            return primary_result

        logger.warning(
            "KB primary failed, falling back to secondary",
            primary=self._primary,
            primary_error=primary_result.error,
        )
        return secondary_result

    def close(self):
        self._rest.close()
        self._mcp.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_kb_client(mode: Optional[str] = None) -> KBSearchClient:
    """Create KB client based on access mode configuration.

    Args:
        mode: Override KB_ACCESS_MODE env var. Options: "rest", "mcp", "compare".

    Returns:
        Appropriate KBSearchClient implementation.
    """
    selected = (mode or KB_ACCESS_MODE).lower()

    if selected == "compare":
        logger.info("KB access mode: compare (both REST and MCP)")
        return CompareKBClient()

    if selected == "mcp":
        logger.info("KB access mode: mcp", url=KB_MCP_URL[:50] if KB_MCP_URL else "not set")
        return MCPKBClient()

    logger.info("KB access mode: rest", url=KB_REST_URL[:50] if KB_REST_URL else "not set")
    return RestKBClient()
