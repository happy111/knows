"""Dashboard Lambda — Entry Point.

Thin router that delegates to service layer. No business logic here.
All aggregation, caching, and data access happens in services/ and dal/.

Endpoints:
    GET /api/dashboard/stats       — Aggregated platform statistics
    GET /api/browse?type=tree      — Sidebar browse tree (TA > Brands)
    GET /api/browse?type=docs&ta=X — Paginated documents by TA/Brand
    OPTIONS *                      — CORS preflight

Note: Single-document operations (view, download, delete) are handled by
      the Documents Lambda (/api/documents/{id}/*), NOT this Lambda.

Authorization: JWT-based (same pattern as Bookmarks/RBAC modules)
Audit logging: Every API access logged to know-audit-trail DynamoDB table

Environment Variables:
    KNOW_METADATA_TABLE   — DynamoDB table for document metadata
    KNOW_TAXONOMY_TABLE   — DynamoDB table for TA/Brand hierarchy
    CACHE_ENDPOINT        — Redis endpoint (optional, for caching)
    DASHBOARD_CACHE_TTL   — Cache TTL in seconds (default: 300)
"""
from typing import Any, Dict

from aws_lambda_powertools import Logger, Tracer, Metrics

from core.response import build_response
from services.stats_service import StatsService
from services.taxonomy_service import TaxonomyService
from shared.auth import extract_user_id, _get_user_role
from shared.audit import log_audit_event
from shared.constants import get_capabilities

logger = Logger(service="dashboard")
tracer = Tracer(service="dashboard")
metrics = Metrics(namespace="kNOW", service="dashboard")

_ERR_NO_IDENTITY = {"error": {"code": "UNAUTHORIZED", "message": "No user identity"}}

# ---------------------------------------------------------------------------
# Lazy-init services (reused across warm Lambda invocations)
# ---------------------------------------------------------------------------
_stats_service = None
_taxonomy_service = None


def _get_stats_service() -> StatsService:
    global _stats_service
    if _stats_service is None:
        _stats_service = StatsService()
    return _stats_service


def _get_taxonomy_service() -> TaxonomyService:
    global _taxonomy_service
    if _taxonomy_service is None:
        _taxonomy_service = TaxonomyService()
    return _taxonomy_service


# ---------------------------------------------------------------------------
# Route Handlers
# ---------------------------------------------------------------------------
def _handle_stats(event: Dict[str, Any], user_id: str, request_id: str) -> Dict[str, Any]:
    """Handle GET /api/dashboard/stats — aggregated platform statistics."""
    stats = _get_stats_service().get_stats()

    log_audit_event(
        "view",
        user_id=user_id,
        resource_type="dashboard",
        resource_id="stats",
        result="success",
        details={"total_documents": stats.get("total_documents", 0)},
        event=event,
    )

    return build_response(200, stats)


def _handle_browse(
    event: Dict[str, Any], query_params: dict, user_id: str, request_id: str,
    role: str = "L1", capabilities: dict = None
) -> Dict[str, Any]:
    """Handle GET /api/browse — tree or docs."""
    browse_type = query_params.get("type", "")

    if browse_type == "tree":
        tree = _get_taxonomy_service().get_browse_tree()

        log_audit_event(
            "view",
            user_id=user_id,
            resource_type="dashboard",
            resource_id="browse_tree",
            result="success",
            details={"node_count": len(tree)},
            event=event,
        )

        return build_response(200, {"tree": tree})

    if browse_type == "docs":
        return _handle_browse_docs(event, query_params, user_id, request_id, role, capabilities)

    log_audit_event(
        "view",
        user_id=user_id,
        resource_type="dashboard",
        resource_id="browse",
        result="failure",
        details={"error_code": "INVALID_TYPE", "type_param": browse_type},
        event=event,
    )

    return build_response(400, {
        "error": {"code": "INVALID_TYPE", "message": "Supported: type=tree, type=docs"}
    }, request_id)


def _handle_browse_docs(
    event: Dict[str, Any], query_params: dict, user_id: str, request_id: str,
    role: str = "L1", capabilities: dict = None
) -> Dict[str, Any]:
    """Handle GET /api/browse?type=docs — paginated document listing."""
    ta = query_params.get("ta", "")
    brand = query_params.get("brand", "")
    indication = query_params.get("indication", "")
    project_type = query_params.get("project_type", "")
    date_from = query_params.get("date_from", "")
    date_to = query_params.get("date_to", "")
    try:
        page = int(query_params.get("page", "1"))
        page_size = int(query_params.get("page_size", "10"))
    except ValueError:
        return build_response(400, {
            "error": {"code": "INVALID_PARAM", "message": "page and page_size must be integers"}
        }, request_id)
    if page < 1 or page_size < 1 or page_size > 100:
        return build_response(400, {
            "error": {"code": "INVALID_PARAM", "message": "page must be >= 1, page_size must be 1-100"}
        }, request_id)
    sort_by = query_params.get("sort_by", "published_at")
    sort_order = query_params.get("sort_order", "desc")
    if sort_by not in ("published_at", "title"):
        return build_response(400, {
            "error": {"code": "INVALID_PARAM", "message": "sort_by must be 'published_at' or 'title'"}
        }, request_id)
    if sort_order not in ("asc", "desc"):
        return build_response(400, {
            "error": {"code": "INVALID_PARAM", "message": "sort_order must be 'asc' or 'desc'"}
        }, request_id)
    result = _get_taxonomy_service().get_browse_docs(
        ta=ta, brand=brand, indication=indication,
        project_type=project_type, date_from=date_from,
        date_to=date_to, page=page, page_size=page_size,
        sort_by=sort_by, sort_order=sort_order,
        role=role, capabilities=capabilities,
    )

    log_audit_event(
        "view",
        user_id=user_id,
        resource_type="dashboard",
        resource_id="browse_docs",
        result="success",
        details={
            "filters": {
                "ta": ta,
                "brand": brand,
                "indication": indication,
                "project_type": project_type,
                "date_from": date_from,
                "date_to": date_to,
            },
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "total_results": result.get("pagination", {}).get("total", 0),
        },
        event=event,
    )

    return build_response(200, result)


# ---------------------------------------------------------------------------
# Lambda Handler
# ---------------------------------------------------------------------------
@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Route an API Gateway event to the appropriate dashboard service.

    Authorization flow (same as Bookmarks/RBAC modules):
      1. Extract user_id from JWT (OAuth token → authorizer claims → env mock)
      2. Reject with 401 if no identity found
      3. Route to service handler with authenticated user_id
      4. Log audit trail for every request

    Args:
        event: API Gateway proxy event.
        _context: Lambda context object (unused).

    Returns:
        API Gateway proxy response with statusCode, headers, and body.
    """
    path = event.get("path", "") or event.get("rawPath", "")
    method = (
        event.get("httpMethod", "")
        or event.get("requestContext", {}).get("http", {}).get("method", "")
    )
    query_params = event.get("queryStringParameters") or {}
    request_id = event.get("requestContext", {}).get("requestId", "")

    logger.info("Request: %s %s params=%s", method, path, query_params)

    if method == "OPTIONS":
        return build_response(200, {"message": "OK"})

    # ---------------------------------------------------------------------------
    # AUTH — Extract user identity from JWT (same flow as Bookmarks/RBAC modules)
    # ---------------------------------------------------------------------------
    user_id = extract_user_id(event)
    if not user_id:
        logger.warning("Unauthorized request: no user identity found")
        return build_response(401, _ERR_NO_IDENTITY, request_id)

    # Fetch role and capabilities for document restriction checks
    role, capabilities = _get_user_role(user_id)

    try:
        if "stats" in path and method == "GET":
            return _handle_stats(event, user_id, request_id)

        if "browse" in path and method == "GET":
            return _handle_browse(event, query_params, user_id, request_id, role, capabilities)

        log_audit_event(
            "view",
            user_id=user_id,
            resource_type="dashboard",
            resource_id="unknown",
            result="failure",
            details={"error_code": "NOT_FOUND", "path": path, "method": method},
            event=event,
        )

        return build_response(404, {
            "error": {"code": "NOT_FOUND", "message": f"Unknown route: {method} {path}"}
        }, request_id)

    except Exception:
        logger.exception("Unhandled error in dashboard handler")

        log_audit_event(
            "view",
            user_id=user_id,
            resource_type="dashboard",
            resource_id="error",
            result="failure",
            details={"error_code": "INTERNAL_ERROR", "path": path, "method": method},
            event=event,
        )

        return build_response(500, {
            "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}
        }, request_id)
