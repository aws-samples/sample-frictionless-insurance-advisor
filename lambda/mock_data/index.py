"""
Mock Data Populator Lambda Function

Populates DynamoDB profile and policy tables with sample data and reconciles
deletions. Invoked by a CloudFormation custom resource in `cdk/tools_stack.py`
on stack create and on any change to the seed JSON files (the custom
resource's physical_resource_id is hashed from the file contents, so
CloudFormation fires `on_update` whenever a seed changes).

Reconciliation behavior:
- Rows present in the seed files are upserted via `put_item` (idempotent).
- Rows present in the DynamoDB tables but absent from the seed files are
  deleted. This mirrors a "source of truth" workflow where the JSON files
  are the authoritative set of demo data.

Note: this table model is suitable for demo workloads only. In a real
system you would not reconcile user-owned rows from a static JSON seed.
"""
import json
import os
from decimal import Decimal

import boto3


PROFILES_PRIMARY_KEY = "customer_id"
POLICIES_PRIMARY_KEY = "id"
CATALOG_PRIMARY_KEY = "product_id"


def load_json_data(filename):
    """Load data from JSON file and convert numeric values to Decimal for DynamoDB"""
    with open(filename, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Convert numeric values to Decimal for DynamoDB compatibility
    def convert_to_decimal(obj):
        if isinstance(obj, list):
            return [convert_to_decimal(item) for item in obj]
        elif isinstance(obj, dict):
            return {key: convert_to_decimal(value) for key, value in obj.items()}
        elif isinstance(obj, float) or (isinstance(obj, int) and not isinstance(obj, bool)):
            return Decimal(str(obj))
        return obj

    return convert_to_decimal(data)


def create_sample_data():
    """Load sample customer profiles, policies, and catalog from JSON files"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    profiles = load_json_data(os.path.join(script_dir, 'profiles.json'))
    policies = load_json_data(os.path.join(script_dir, 'policies.json'))
    catalog = load_json_data(os.path.join(script_dir, 'catalog.json'))
    return profiles, policies, catalog


def _scan_primary_keys(table, pk_attr):
    """Return the set of primary-key values currently in the table.

    Uses a paginated Scan with ProjectionExpression so only the PK is
    transferred — adequate for demo-sized tables (hundreds of items). Not
    intended for tables with large item counts.
    """
    existing = set()
    kwargs = {"ProjectionExpression": "#pk", "ExpressionAttributeNames": {"#pk": pk_attr}}
    while True:
        response = table.scan(**kwargs)
        for item in response.get("Items", []):
            existing.add(item[pk_attr])
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return existing


def _sync_table(table, items, pk_attr, label):
    """Upsert every item in `items` and delete any existing rows whose PK
    is not in the seed set. Returns a (upserts, deletes) count tuple.
    """
    expected = {item[pk_attr] for item in items}
    existing = _scan_primary_keys(table, pk_attr)
    stale = existing - expected

    # Upsert seed rows
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)

    # Delete rows that no longer appear in the seed file
    if stale:
        print(f"[{label}] reconciling {len(stale)} stale row(s) for deletion")
        with table.batch_writer() as batch:
            for pk_value in stale:
                batch.delete_item(Key={pk_attr: pk_value})

    print(f"[{label}] upserts={len(items)} deletes={len(stale)}")
    return len(items), len(stale)


def lambda_handler(event, context):
    """Populate and reconcile DynamoDB tables against the seed JSON files."""
    try:
        profiles_table = boto3.resource('dynamodb').Table(os.environ['PROFILES_TABLE'])
        policies_table = boto3.resource('dynamodb').Table(os.environ['POLICIES_TABLE'])
        catalog_table = boto3.resource('dynamodb').Table(os.environ['CATALOG_TABLE'])

        profiles, policies, catalog = create_sample_data()

        profiles_up, profiles_del = _sync_table(
            profiles_table, profiles, PROFILES_PRIMARY_KEY, "profiles"
        )
        policies_up, policies_del = _sync_table(
            policies_table, policies, POLICIES_PRIMARY_KEY, "policies"
        )
        catalog_up, catalog_del = _sync_table(
            catalog_table, catalog, CATALOG_PRIMARY_KEY, "catalog"
        )

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Sample data synchronized successfully',
                'profiles': {'upserts': profiles_up, 'deletes': profiles_del},
                'policies': {'upserts': policies_up, 'deletes': policies_del},
                'catalog': {'upserts': catalog_up, 'deletes': catalog_del},
            })
        }

    except Exception as e:
        print(f"Error populating mock data: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Error encountered during data loading'})
        }
