"""KB Cross-Account Access — DynamoDB and S3 operations on AILENS account.

Used by:
  - StatusPoller Lambda: reads AILENS DDB to check kb_status
  - KB Ingestion Trigger Lambda: writes to AILENS DDB
  - Documents Lambda (future): presigned URLs from AILENS S3

These are NOT search operations — they are direct AWS resource access
using STS AssumeRole for cross-account permissions.
"""
import os
from typing import Any, Dict, Optional

import boto3
from aws_lambda_powertools import Logger
from botocore.config import Config as BotoConfig

logger = Logger(child=True)

_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_CROSS_ACCOUNT_ROLE_ARN = os.environ.get("AILENS_CROSS_ACCOUNT_ROLE_ARN", "")
_KB_DDB_TABLE = os.environ.get("AILENS_DDB_TABLE", "")
_KB_S3_BUCKET = os.environ.get("AILENS_S3_BUCKET", "")


class KBDynamoDB:
    """Cross-account reader for KB pipeline DynamoDB metadata table.

    Assumes an IAM role in the KB account to read document pipeline status.
    Used by StatusPoller to confirm documents are indexed.
    """

    def __init__(
        self,
        table_name: Optional[str] = None,
        role_arn: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self._table_name = table_name or _KB_DDB_TABLE
        self._region = region or _AWS_REGION
        self._role_arn = role_arn or _CROSS_ACCOUNT_ROLE_ARN
        self._resource = None

    def _get_table(self):
        if self._resource is None:
            if self._role_arn:
                sts = boto3.client("sts", region_name=self._region)
                creds = sts.assume_role(
                    RoleArn=self._role_arn, RoleSessionName="know-kb-read"
                )["Credentials"]
                session = boto3.Session(
                    aws_access_key_id=creds["AccessKeyId"],
                    aws_secret_access_key=creds["SecretAccessKey"],
                    aws_session_token=creds["SessionToken"],
                    region_name=self._region,
                )
            else:
                session = boto3.Session(region_name=self._region)
            self._resource = session.resource("dynamodb").Table(self._table_name)
        return self._resource

    def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Read single document metadata by document_id."""
        try:
            table = self._get_table()
            response = table.scan(
                FilterExpression="document_id = :did",
                ExpressionAttributeValues={":did": document_id},
                Limit=1,
            )
            items = response.get("Items", [])
            return items[0] if items else None
        except Exception as e:
            logger.error("KB DDB get_document failed", error=str(e))
            return None

    def get_document_by_site_file(self, site_id: str, file_id: str) -> Optional[Dict[str, Any]]:
        """Find document by site_id + file_id combination."""
        try:
            table = self._get_table()
            response = table.scan(
                FilterExpression="site_id = :sid AND file_id = :fid",
                ExpressionAttributeValues={":sid": site_id, ":fid": file_id},
                Limit=1,
            )
            items = response.get("Items", [])
            return items[0] if items else None
        except Exception as e:
            logger.error("KB DDB get_by_site_file failed", error=str(e))
            return None

    def scan_documents(
        self, lens: str = "market", limit: int = 100, last_key: Optional[Dict] = None
    ) -> tuple:
        """Scan KB DDB for documents in a given lens. Returns (items, last_evaluated_key)."""
        try:
            table = self._get_table()
            kwargs: Dict[str, Any] = {
                "FilterExpression": "lens = :lens AND SK = :sk",
                "ExpressionAttributeValues": {":lens": lens, ":sk": "CURRENT"},
                "Limit": limit,
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            response = table.scan(**kwargs)
            return response.get("Items", []), response.get("LastEvaluatedKey")
        except Exception as e:
            logger.error("KB DDB scan failed", error=str(e))
            return [], None


class KBS3:
    """Presigned URL generator for documents in KB S3 bucket (cross-account)."""

    def __init__(
        self,
        bucket: Optional[str] = None,
        role_arn: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self._bucket = bucket or _KB_S3_BUCKET
        self._region = region or _AWS_REGION
        self._role_arn = role_arn or _CROSS_ACCOUNT_ROLE_ARN
        self._client = None

    def _get_client(self):
        if self._client is None:
            if self._role_arn:
                sts = boto3.client("sts", region_name=self._region)
                creds = sts.assume_role(
                    RoleArn=self._role_arn, RoleSessionName="know-kb-s3-read"
                )["Credentials"]
                self._client = boto3.client(
                    "s3",
                    region_name=self._region,
                    aws_access_key_id=creds["AccessKeyId"],
                    aws_secret_access_key=creds["SecretAccessKey"],
                    aws_session_token=creds["SessionToken"],
                    config=BotoConfig(signature_version="s3v4"),
                )
            else:
                self._client = boto3.client(
                    "s3", region_name=self._region,
                    config=BotoConfig(signature_version="s3v4"),
                )
        return self._client

    def get_presigned_url(self, s3_uri: str, expiry_seconds: int = 900) -> Optional[str]:
        """Generate a presigned URL for a document in KB S3.

        Args:
            s3_uri: Full S3 URI (s3://bucket/key) or just the key.
            expiry_seconds: URL expiry time (default 15 minutes).
        """
        try:
            if s3_uri.startswith("s3://"):
                parts = s3_uri.replace("s3://", "").split("/", 1)
                bucket = parts[0]
                key = parts[1] if len(parts) > 1 else ""
            else:
                bucket = self._bucket
                key = s3_uri

            client = self._get_client()
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expiry_seconds,
            )
        except Exception as e:
            logger.error("Failed to generate presigned URL", s3_uri=s3_uri, error=str(e))
            return None


# Backward-compatible aliases (existing code may import old names)
AILENSDynamoDB = KBDynamoDB
AILENSS3 = KBS3
