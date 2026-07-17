"""Stats Service — Aggregated dashboard statistics with Redis caching.

Reads all published documents from kNOW-Metadata via MetadataAdapter,
then counts occurrences by brand and therapeutic_area.
Results cached in Redis for 5 minutes to reduce DDB scan load.
"""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import boto3
from botocore.config import Config

from core.config import UPLOAD_S3_BUCKET, ASSETS_BASE_URL, CACHE_TTL
from core.clients import get_cache_client
from core.logger import get_logger
from dal.metadata_adapter import MetadataAdapter
from models.dashboard_models import BrandItem, DashboardStats, FunctionItem, TAItem

logger = get_logger(__name__)

CACHE_KEY = "dashboard:stats"
NEW_UPLOAD_DAYS = 30

BUCKET_NAME = UPLOAD_S3_BUCKET
BRAND_FOLDER = "assets/brand"
REGION = "us-east-1"

# S3 client for presigned URLs — uses the public endpoint so URLs are
# reachable from browsers outside the VPC (Lambda runs behind an S3 VPCE).
_s3_client = boto3.client(
    "s3",
    region_name=REGION,
    config=Config(signature_version="s3v4"),
    endpoint_url=f"https://s3.{REGION}.amazonaws.com",
)

_CATEGORY_LABELS = {
    "MR": "Market Research",
    "CI": "Competitive Intelligence",
    "IPST": "IPST",
    "PV": "Performance Vigilance",
    "LT": "LT Updates",
}


def _normalize_field(value) -> List[str]:
    """Normalise a DynamoDB field that may be a str or list into a list of non-empty strings."""
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if value:
        return [str(value)]
    return []


class StatsService:
    """Provides aggregated dashboard statistics (brand counts, TA counts, category highlights).

    Data flow:
        1. Check Redis cache (key: "dashboard:stats")
        2. If miss: scan kNOW-Metadata (status=published) via MetadataAdapter
        3. Aggregate by brand, therapeutic_area, category
        4. Cache result for CACHE_TTL seconds (default 300s = 5 min)
        5. Return DashboardStats model as dict
    """

    def __init__(self, metadata_adapter: MetadataAdapter = None, cache_client=None):
        self._adapter = metadata_adapter or MetadataAdapter()
        self._cache = cache_client if cache_client is not None else get_cache_client()

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregated dashboard statistics."""
        if self._cache:
            try:
                cached = self._cache.get(CACHE_KEY)
                if cached:
                    logger.info("Stats cache HIT")
                    return json.loads(cached)
            except Exception as e:
                logger.warning("Cache read failed: %s", e)

        logger.info("Stats cache MISS — scanning metadata table")
        documents = self._adapter.get_published_documents()
        stats = self._aggregate(documents)
        result = stats.to_dict()

        if self._cache:
            try:
                self._cache.setex(CACHE_KEY, CACHE_TTL, json.dumps(result))
                logger.info("Stats cached (TTL=%ds)", CACHE_TTL)
            except Exception as e:
                logger.warning("Cache write failed: %s", e)

        return result

    def _aggregate(self, documents: List[Dict[str, Any]]) -> DashboardStats:
        """Aggregate document metadata into dashboard statistics model."""
        brand_counter: Counter = Counter()
        ta_counter: Counter = Counter()
        function_counter: Counter = Counter()
        category_counter: Counter = Counter()

        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=NEW_UPLOAD_DAYS)).isoformat()
        total_uploads_this_month = 0

        for doc in documents:
            brands = _normalize_field(doc.get("brand", ""))
            tas = _normalize_field(doc.get("therapeutic_area", ""))
            functions = _normalize_field(doc.get("function", ""))
            categories = _normalize_field(doc.get("category", ""))
            published_at = doc.get("published_at", "")

            for brand in brands:
                brand_counter[brand] += 1
            for ta in tas:
                ta_counter[ta] += 1
            for func in functions:
                function_counter[func] += 1
            for category in categories:
                category_counter[category] += 1
            if published_at and published_at >= cutoff_date:
                total_uploads_this_month += 1

        category_highlights = [
            {"category": k, "label": _CATEGORY_LABELS.get(k, k), "count": v}
            for k, v in category_counter.most_common(2)
        ]

        return DashboardStats(
            brands=self._build_brands(brand_counter),
            therapeutic_areas=self._build_tas(ta_counter),
            functions=self._build_functions(function_counter),
            category_highlights=category_highlights,
            total_documents=len(documents),
            total_uploads_this_month=total_uploads_this_month,
        )

    def _build_brands(self, counter: Counter) -> List[BrandItem]:
        """Build brand list with logo URLs."""
        return [
            BrandItem(
                name=name,
                count=count,
                logo_url=self._brand_logo_url(name),
            )
            for name, count in counter.most_common(20)
        ]

    def _build_tas(self, counter: Counter) -> List[TAItem]:
        """Build therapeutic area list with icon URLs."""
        return [
            TAItem(
                name=name,
                count=count,
                icon_url=self._ta_icon_url(name),
            )
            for name, count in counter.most_common(20)
        ]

    def _build_functions(self, counter: Counter) -> List[FunctionItem]:
        """Build function list sorted by document count."""
        return [
            FunctionItem(name=name, count=count)
            for name, count in counter.most_common(20)
        ]

    @staticmethod
    def _brand_logo_url(name: str) -> str:
        """Generate presigned S3 URL for brand logo SVG."""
        key = f"{BRAND_FOLDER}/{name.capitalize().replace(' ', '-').replace('(', '').replace(')', '')}.svg"
        try:
            url = _s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": BUCKET_NAME, "Key": key},
                ExpiresIn=3600,
            )
            return url
        except Exception:
            logger.warning("Failed to generate presigned URL for brand: %s", name)
            return ""

    @staticmethod
    def _ta_icon_url(name: str) -> str:
        """Compute S3/CDN URL for therapeutic area icon."""
        if not ASSETS_BASE_URL:
            return ""
        key = name.lower().replace(" ", "-").replace(",", "").replace("&", "and")
        return f"{ASSETS_BASE_URL}/therapeutic-areas/{key}.svg"
