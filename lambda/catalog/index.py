"""
Insurance Catalog Lambda

Read-only interface to the product catalog in DynamoDB. Used by the React
frontend's Comparator page to populate the product-type dropdown and the
per-type product picker.

Routes (all exposed through API Gateway, Cognito-authorized):
  GET /catalog/product-types
    -> list of distinct product types present in the catalog, sorted.

  GET /catalog/products?type=<product_type>
    -> list of catalog entries filtered to a given type. No pagination
       (catalog is small; capped at a few dozen items).

  GET /catalog/products/{product_id}
    -> single catalog entry.

Design notes:
- DynamoDB table: primary key `product_id`, GSI `product-type-index` on
  `product_type`.
- Item shape: { product_id, carrier_id, carrier_name, product_name,
  product_type, pricing_tier, s3_bucket, s3_key }.
- No scan is used for the type filter path; we always query the GSI.
"""
import json
import os
import traceback
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["CATALOG_TABLE"])


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
    }


def _response(status: int, body):
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(body, default=_default_json),
    }


def _default_json(value):
    """Serialize DynamoDB Decimal values as plain int/float."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _list_product_types() -> list[str]:
    """Scan catalog, project only product_type, dedupe, sort.

    The catalog is small (~30 items) so a scan with ProjectionExpression
    is cheap. Not paginated for that reason.
    """
    response = table.scan(
        ProjectionExpression="#pt",
        ExpressionAttributeNames={"#pt": "product_type"},
    )
    seen = set()
    for item in response.get("Items", []):
        value = item.get("product_type")
        if value:
            seen.add(value)
    return sorted(seen)


def _list_products_by_type(product_type: str) -> list[dict]:
    """Query GSI for all products of a given type."""
    response = table.query(
        IndexName="product-type-index",
        KeyConditionExpression=Key("product_type").eq(product_type),
    )
    # Sort for stable client-side rendering: carrier, then product name.
    items = response.get("Items", [])
    items.sort(key=lambda i: (i.get("carrier_name", ""), i.get("product_name", "")))
    return items


def _get_product(product_id: str) -> dict | None:
    response = table.get_item(Key={"product_id": product_id})
    return response.get("Item")


def handler(event, context):
    try:
        method = event.get("httpMethod", "GET")
        if method != "GET":
            return _response(405, {"message": "Only GET supported"})

        resource = event.get("resource", "")
        path_params = event.get("pathParameters") or {}
        query_params = event.get("queryStringParameters") or {}

        if resource.endswith("/product-types"):
            return _response(200, {"product_types": _list_product_types()})

        if resource.endswith("/products/{product_id}"):
            pid = path_params.get("product_id")
            if not pid:
                return _response(400, {"message": "product_id is required"})
            item = _get_product(pid)
            if not item:
                return _response(404, {"message": "Product not found"})
            return _response(200, item)

        if resource.endswith("/products"):
            product_type = query_params.get("type")
            if not product_type:
                return _response(400, {"message": "query param 'type' is required"})
            return _response(200, {"products": _list_products_by_type(product_type)})

        return _response(404, {"message": f"Unknown route: {resource}"})

    except Exception as e:  # pragma: no cover
        print(f"Error in catalog handler: {e}")
        traceback.print_exc()
        return _response(500, {"message": "An internal error occurred"})
