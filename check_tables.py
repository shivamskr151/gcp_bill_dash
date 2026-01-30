
import os
from google.cloud import bigquery
from google.oauth2 import service_account
from config import PROJECT_ID, BIGQUERY_DATASET, SERVICE_ACCOUNT_FILE

def check_tables():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=['https://www.googleapis.com/auth/bigquery.readonly']
    )
    client = bigquery.Client(project=PROJECT_ID, credentials=credentials)
    dataset_ref = client.dataset(BIGQUERY_DATASET, project=PROJECT_ID)
    tables = list(client.list_tables(dataset_ref))
    
    print(f"Tables in {PROJECT_ID}.{BIGQUERY_DATASET}:")
    for table in tables:
        print(f" - {table.table_id}")

if __name__ == "__main__":
    check_tables()
