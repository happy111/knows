"""Search controller — handles POST /api/search."""
import json
from typing import Any, Dict

from models import SearchRequest
from shared.audit import log_audit_event
from shared.auth import _get_user_role, extract_user_id
from utils import build_response, get_user_context


def handle_search(event: Dict[str, Any], service) -> Dict[str, Any]:
    """Handle POST /api/search.

    Validates request, builds SearchRequest from body + JWT context,
    delegates to SearchService, returns formatted response.
    """
    # Parse request body
    try:
        body = json.loads(event.get("body", "{}"))
    except (json.JSONDecodeError, TypeError):
        return build_response(
            400, {"error": {"code": "INVALID_JSON", "message": "Request body must be valid JSON"}}
        )

    # Validate required fields
    query = body.get("query", "").strip()
    if not query:
        return build_response(
            400, {"error": {"code": "VALIDATION_ERROR", "message": "query is required and cannot be empty"}}
        )

    # Build search request with user context (includes ta_access for restricted KB)
    user_context = get_user_context(event)

    # Reject if no user identity could be extracted
    user_id = extract_user_id(event)
    if not user_id or not user_id.strip():
        log_audit_event(
            "search",
            user_id="unknown",
            resource_type="search",
            result="failure",
            details={
                "error_code": "UNAUTHORIZED",
                "error_message": "No user identity found in request",
            },
            event=event,
        )
        return build_response(
            401, {"error": {"code": "UNAUTHORIZED", "message": "No user identity found in request"}}
        )

    # Fetch role and capabilities for document restriction checks
    role, capabilities = _get_user_role(user_id)
    user_context["role"] = role
    user_context["user_id"] = user_id
    # Use ta_access from capabilities if available, otherwise keep JWT-derived value
    caps_ta_access = (capabilities or {}).get("ta_access", [])
    if caps_ta_access:
        user_context["ta_access"] = caps_ta_access

    request = SearchRequest.from_event_body(body, user_context)

    # Execute search
    response = service.search(request)

    # Audit: log successful search
    log_audit_event(
        "search",
        user_id=user_id,
        resource_type="search",
        result="success",
        details={
            "query": query,
            "filters": {
                "therapeutic_area": body.get("therapeutic_area", body.get("ta")),
                "brand": body.get("brand"),
                "indication": body.get("indication"),
                "doc_type": body.get("doc_type", body.get("document_type")),
                "date_range": body.get("date_range"),
            },
            "result_count": response.total_count if hasattr(response, "total_count") else len(response.to_dict().get("results", [])),
            "role": role,
            "ta_access": caps_ta_access,
        },
        event=event,
    )

    return build_response(200, response.to_dict())
