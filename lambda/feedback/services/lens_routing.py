"""kNOW Upload Lambda — Lens routing logic.

Determines which AILENS lens and permission groups apply to a document
based on its category and therapeutic area.

Categories: MR, CI, IPST, PV, LT
"""
from typing import Dict, List, Tuple


def determine_lens(category: str) -> str:
    """Determine the AILENS lens for a document based on category.

    MR/CI → lens="market" (open access)
    IPST/PV/LT → lens="market_restricted" (restricted access)
    """
    if category in ("MR", "CI"):
        return "market"
    return "market_restricted"


def determine_permissions(category: str, therapeutic_area: str = "") -> List[str]:
    """Determine the permission groups for a document based on category.

    MR/CI → [] (everyone=true, no group restriction)
    IPST → ["kNOW-IPST-{TA}"] (restricted to TA-specific group)
    PV → ["kNOW-PV"] (restricted to PV group)
    LT → ["kNOW-LT"] (restricted to LT group)
    """
    if category in ("MR", "CI"):
        return []
    elif category == "IPST":
        ta_suffix = therapeutic_area.replace(" ", "-") if therapeutic_area else "UNKNOWN"
        return [f"kNOW-IPST-{ta_suffix}"]
    elif category == "PV":
        return ["kNOW-PV"]
    elif category == "LT":
        return ["kNOW-LT"]
    return []


def determine_lens_and_permissions(category: str, therapeutic_area: str = "") -> Tuple[str, List[str]]:
    """Convenience: return both lens and permissions_groups together."""
    return determine_lens(category), determine_permissions(category, therapeutic_area)
