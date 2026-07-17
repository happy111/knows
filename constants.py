"""Role Definitions and Constants — Enterprise RBAC Pattern.

Single source of truth for role capabilities across all Lambda modules.

Roles:
- L1 (Basic Viewer) — default for all authenticated users
- L3 (Content Uploader) — can upload, publish, send for review
- L4 (Access Manager) — can approve, reject, delete, admin

L2 is NOT a role — it's ta_access being non-empty (composable entitlement).
Any role with ta_access populated → view_restricted: true.

Document categories (5): MR, CI, IPST, PV, LT
- MR/CI = general (view_general)
- IPST/PV/LT = restricted (view_restricted + ta_access check)

Usage:
    from shared.constants import ROLE_CAPABILITIES, get_capabilities
"""
from typing import Dict, List, Set

ROLE_HIERARCHY: Dict[str, int] = {"L1": 1, "L3": 3, "L4": 4}

VALID_ROLES: Set[str] = {"L1", "L3", "L4"}

ROLE_DESCRIPTIONS: Dict[str, str] = {
    "L1": "Basic Viewer — View MR/CI, search, bookmark, chat. Can request TA access.",
    "L3": "Content Uploader — Upload, publish (own), send for review, edit tags (own).",
    "L4": "Access Manager — Approve/reject, delete, manage users, edit tags (any), admin console.",
}

ROLE_CAPABILITIES: Dict[str, Dict[str, bool]] = {
    "L1": {
        "view_general": True,
        "view_restricted": False,
        "upload": False,
        "publish": False,
        "send_for_review": False,
        "approve": False,
        "reject": False,
        "delete": False,
        "discard": False,
        "qc_tags": False,
        "admin": False,
    },
    "L3": {
        "view_general": True,
        "view_restricted": False,
        "upload": True,
        "publish": True,
        "send_for_review": True,
        "approve": False,
        "reject": False,
        "delete": False,
        "discard": True,
        "qc_tags": True,
        "admin": False,
    },
    "L4": {
        "view_general": True,
        "view_restricted": False,
        "upload": True,
        "publish": True,
        "send_for_review": False,
        "approve": True,
        "reject": True,
        "delete": True,
        "discard": True,
        "qc_tags": True,
        "admin": True,
    },
}

DOCUMENT_CATEGORIES: List[str] = ["MR", "CI", "IPST", "PV", "LT"]
GENERAL_CATEGORIES: Set[str] = {"MR", "CI"}
RESTRICTED_CATEGORIES: Set[str] = {"IPST", "PV", "LT"}

DEFAULT_ROLE: str = "L1"
DEFAULT_TA_ACCESS: List[str] = []


def get_capabilities(role: str, ta_access: List[str] = None) -> Dict[str, bool]:
    """Compute capabilities from role + ta_access. Single source of truth."""
    caps = ROLE_CAPABILITIES.get(role, ROLE_CAPABILITIES["L1"]).copy()
    if ta_access:
        caps["view_restricted"] = True
    return caps
