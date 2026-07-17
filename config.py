"""Configuration — Environment variables and constants.

All environment-driven settings are centralized here.
No business logic — only reads from os.environ with sensible defaults.

Why centralize?
    Single point of change for table names, region, TTL.
    Tests override these via os.environ BEFORE importing modules (see conftest.py).
"""
import os

# ---------------------------------------------------------------------------
# DynamoDB Tables
# ---------------------------------------------------------------------------
KNOW_METADATA_TABLE = os.environ.get("KNOW_METADATA_TABLE", "know-metadata-dev")
KNOW_TAXONOMY_TABLE = os.environ.get("KNOW_TAXONOMY_TABLE", "know-taxonomy-dev")

# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# Cache (ElastiCache Redis)
# Empty string = caching disabled (default for dev/test environments)
# ---------------------------------------------------------------------------
CACHE_ENDPOINT = os.environ.get("CACHE_ENDPOINT", "")
CACHE_TTL = int(os.environ.get("DASHBOARD_CACHE_TTL", "300"))  # 5 minutes

# ---------------------------------------------------------------------------
# Document Categories
# MR=Market Research, CI=Competitive Intelligence, PV=Performance Vigilance,
# LT=LT Updates, IPST=Internal Patient Safety Team
# ---------------------------------------------------------------------------
VALID_CATEGORIES = ("MR", "CI", "IPST", "PV", "LT")

# ---------------------------------------------------------------------------
# Assets (S3 folder in uploads bucket — brand logos, TA icons)
# Pattern: {ASSETS_BASE_URL}/brands/{key}.svg, {ASSETS_BASE_URL}/therapeutic-areas/{key}.svg
# ---------------------------------------------------------------------------
ASSETS_BASE_URL = os.environ.get("ASSETS_BASE_URL", "")
UPLOAD_S3_BUCKET = os.environ.get("UPLOAD_S3_BUCKET", "novartis-970547336770-know-dev-uploads")
