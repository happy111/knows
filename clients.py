"""Clients — Lazy-initialized AWS service clients.

Singleton pattern for Lambda warm-start reuse.
DynamoDB resource and Redis client are created once per container lifetime.

Why singleton?
    Lambda containers stay warm for ~5-15 minutes. Re-creating boto3 sessions
    on every invocation wastes time (~200ms). By caching the resource at module
    level, subsequent invocations in the same container skip initialization.
"""
import os

import boto3
from botocore.config import Config

from core.config import AWS_REGION, CACHE_ENDPOINT

# Module-level singletons — persisted across warm Lambda invocations
_dynamodb_resource = None
_cache_client = None
_cache_initialized = False


def get_dynamodb_resource():
    """Return shared DynamoDB resource (created once per Lambda container).

    Supports local testing via DYNAMODB_ENDPOINT_URL env var.
    In production, this connects to the regional DynamoDB endpoint.
    """
    global _dynamodb_resource
    if _dynamodb_resource is None:
        config = Config(
            proxies={},
            # Adaptive retry: backs off automatically on throttling
            retries={"max_attempts": 2, "mode": "adaptive"},
        )
        endpoint_url = os.environ.get("DYNAMODB_ENDPOINT_URL", "")
        kwargs = {"region_name": AWS_REGION, "config": config}
        if endpoint_url:
            # Local DynamoDB (docker) or LocalStack for dev/testing
            kwargs["endpoint_url"] = endpoint_url
        _dynamodb_resource = boto3.resource("dynamodb", **kwargs)
    return _dynamodb_resource


def get_cache_client():
    """Return shared Redis client or None if unavailable.

    Gracefully degrades — Lambda works without cache, just slower.
    If CACHE_ENDPOINT is empty (default in dev/test), caching is disabled.
    If Redis connection fails, returns None and services skip caching.
    """
    global _cache_client, _cache_initialized
    if _cache_initialized:
        return _cache_client
    _cache_initialized = True

    if not CACHE_ENDPOINT:
        # No endpoint configured — caching disabled (normal for dev/test)
        return None

    try:
        import redis

        _cache_client = redis.Redis(
            host=CACHE_ENDPOINT,
            port=6379,
            decode_responses=True,
            socket_connect_timeout=2,  # Fail fast if Redis unreachable
        )
        # Verify connectivity — fail early on cold start rather than on first request
        _cache_client.ping()
    except Exception:
        # Redis unavailable — Lambda continues without caching
        _cache_client = None

    return _cache_client
