"""Filters controller — handles GET /api/filters/options."""
from typing import Any, Dict

from utils import build_response


def handle_filter_options(query_params: Dict[str, str], service) -> Dict[str, Any]:
    """Handle GET /api/filters/options.

    Returns filter dropdown values (TA, Brand, Indication, DocType, DateRange).
    Optionally scoped to a specific TA via ?ta= query param.
    """
    ta_filter = query_params.get("ta")

    options = service.get_filter_options(ta_filter=ta_filter)

    return build_response(200, options.to_dict())
