"""Shared configuration — loads from SSM Parameter Store."""
import os
from functools import lru_cache

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SSM_PATH = os.environ.get("SSM_PARAMETER_PATH", "/know/dev")


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Load all SSM parameters under the configured path."""
    client = boto3.client("ssm", region_name=AWS_REGION)
    params = {}
    paginator = client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=SSM_PATH, Recursive=True, WithDecryption=True):
        for p in page["Parameters"]:
            key = p["Name"].replace(f"{SSM_PATH}/", "")
            params[key] = p["Value"]
    return params
