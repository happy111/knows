"""Controller: Get Session Documents — GET /sessions/documents"""

import os
import boto3
from aws_lambda_powertools import Logger, Tracer

from core.response import build_response

logger = Logger()
tracer = Tracer()

METADATA_TABLE = os.environ.get("KNOW_METADATA_TABLE", "know-metadata-dev")

_dynamodb = boto3.resource("dynamodb")
_metadata_table = None


def _get_metadata_table():
    global _metadata_table
    if _metadata_table is None:
        _metadata_table = _dynamodb.Table(METADATA_TABLE)
    return _metadata_table


@tracer.capture_method
def get_session_documents(event: dict) -> dict:
    """Retrieve document metadata for a list of document IDs.

    Query: GET /sessions/documents?doc_id=id1,id2,id3
    Returns: list of { document_id, title, therapeutic_area, category }
    """
    query_params = event.get("queryStringParameters") or {}
    doc_id_param = query_params.get("doc_id", "")

    if not doc_id_param:
        return build_response(400, {"error": {"code": "VALIDATION_ERROR", "message": "doc_id query parameter is required"}})

    doc_ids = [d.strip() for d in doc_id_param.split(",") if d.strip()]

    if not doc_ids:
        return build_response(400, {"error": {"code": "VALIDATION_ERROR", "message": "doc_id must contain at least one valid ID"}})

    if len(doc_ids) > 100:
        return build_response(400, {"error": {"code": "VALIDATION_ERROR", "message": "Maximum 100 document IDs allowed per request"}})

    documents = []

    # BatchGetItem supports up to 100 keys per call
    keys = [{"document_id": doc_id} for doc_id in doc_ids]

    try:
        response = _dynamodb.batch_get_item(
            RequestItems={
                METADATA_TABLE: {
                    "Keys": keys,
                    "ProjectionExpression": "document_id, title, therapeutic_area, category",
                }
            }
        )

        items = response.get("Responses", {}).get(METADATA_TABLE, [])
        documents = [
            {
                "document_id": item.get("document_id"),
                "title": item.get("title", ""),
                "therapeutic_area": item.get("therapeutic_area", ""),
                "category": item.get("category", ""),
            }
            for item in items
        ]

        return build_response(200, {
            "status": "success",
            "message": f"Retrieved {len(documents)} of {len(doc_ids)} documents",
            "documents": documents,
        })

    except Exception as e:
        logger.error(f"Error fetching documents: {e}")
        return build_response(500, {"error": {"code": "INTERNAL_ERROR", "message": str(e)}})
