#!/usr/bin/env python3
"""Test script to check BigQuery access and find billing tables"""

from google.oauth2 import service_account
from google.cloud import bigquery
import sys

from config import BIGQUERY_DATASET, PROJECT_ID, SERVICE_ACCOUNT_FILE

try:
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=[
            'https://www.googleapis.com/auth/bigquery.readonly',
            'https://www.googleapis.com/auth/bigquery',
            'https://www.googleapis.com/auth/cloud-platform.read-only'
        ]
    )
    client = bigquery.Client(project=PROJECT_ID, credentials=credentials)
    
    print("Successfully authenticated")
    print(f"Connected to project: {PROJECT_ID}")
    
    # List datasets
    print(f"\nListing datasets in project {PROJECT_ID}...")
    datasets = list(client.list_datasets())
    print(f"Found {len(datasets)} dataset(s):")
    for dataset in datasets:
        print(f"  - {dataset.dataset_id}")
    
    # List tables in the billing dataset
    print(f"\nListing tables in dataset '{BIGQUERY_DATASET}'...")
    try:
        dataset_ref = client.dataset(BIGQUERY_DATASET, project=PROJECT_ID)
        tables = list(client.list_tables(dataset_ref))
        print(f"Found {len(tables)} table(s):")
        for table in tables:
            print(f"  - {table.table_id}")
            
            # Try to get table schema
            try:
                table_ref = dataset_ref.table(table.table_id)
                table_obj = client.get_table(table_ref)
                print(f"    Rows: {table_obj.num_rows:,}")
                print(f"    Size: {table_obj.num_bytes / (1024*1024):.2f} MB")
                print(f"    Columns: {', '.join([col.name for col in table_obj.schema[:5]])}...")
            except Exception as e:
                print(f"    Error getting table info: {e}")
    except Exception as e:
        print(f"Error listing tables: {e}")
        print(f"  Make sure the service account has 'BigQuery Data Viewer' role on dataset '{BIGQUERY_DATASET}'")
        sys.exit(1)
    
    # Try a simple query on the first billing table found
    billing_tables = [t for t in tables if 'billing' in t.table_id.lower()]
    if billing_tables:
        test_table = billing_tables[0]
        print(f"\nTesting query on table: {test_table.table_id}")
        try:
            query = f"SELECT COUNT(*) as count FROM `{PROJECT_ID}.{BIGQUERY_DATASET}.{test_table.table_id}` LIMIT 1"
            query_job = client.query(query)
            results = query_job.result()
            for row in results:
                print(f"Query successful! Table has {row.count:,} rows")
        except Exception as e:
            print(f"Query failed: {e}")
            print(f"  Make sure the service account has 'BigQuery Job User' role")
    else:
        print("\n⚠ No billing tables found in the dataset")
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
