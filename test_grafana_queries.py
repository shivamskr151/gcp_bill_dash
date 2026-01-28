#!/usr/bin/env python3
"""Test Prometheus queries that Grafana will use"""

import requests
import json
from urllib.parse import quote

from config import DASHBOARD_PROMETHEUS_URL, PROJECT_ID

if not DASHBOARD_PROMETHEUS_URL:
    raise RuntimeError("Missing DASHBOARD_PROMETHEUS_URL in environment (.env)")

PROMETHEUS_URL = DASHBOARD_PROMETHEUS_URL

def test_query(query, description):
    """Test a Prometheus query"""
    print(f"\n{'='*60}")
    print(f"Testing: {description}")
    print(f"Query: {query}")
    print(f"{'='*60}")
    
    encoded_query = quote(query)
    url = f"{PROMETHEUS_URL}/api/v1/query?query={encoded_query}"
    
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        
        if data.get('status') == 'success':
            results = data.get('data', {}).get('result', [])
            if results:
                print(f"SUCCESS: Found {len(results)} result(s)")
                for i, result in enumerate(results[:3], 1):  # Show first 3
                    metric = result.get('metric', {})
                    value = result.get('value', [None, None])[1]
                    print(f"  Result {i}:")
                    print(f"    Labels: {metric}")
                    print(f"    Value: {value}")
                if len(results) > 3:
                    print(f"  ... and {len(results) - 3} more results")
            else:
                print("WARNING: Query succeeded but returned no results")
                print(f"Response: {json.dumps(data, indent=2)}")
        else:
            print(f"ERROR: {data.get('errorType', 'Unknown')}")
            print(f"Message: {data.get('error', 'No error message')}")
            
    except Exception as e:
        print(f"EXCEPTION: {e}")

# Test queries that Grafana dashboard uses
queries = [
    (f'gcp_billing_cost_total{{project="{PROJECT_ID}"}}',
     'Total Billing Cost'),
    
    (f'gcp_billing_cost{{project="{PROJECT_ID}"}}',
     'Billing Cost by Service'),
    
    (f'gcp_billing_exporter_up{{project="{PROJECT_ID}"}}',
     'Exporter Status'),
    
    (f'gcp_billing_exporter_error{{project="{PROJECT_ID}"}}',
     'Exporter Error Status'),
]

print("Testing Prometheus Queries for Grafana Dashboard")
print("=" * 60)

for query, desc in queries:
    test_query(query, desc)

print("\n" + "=" * 60)
print("Test Complete!")
print("\nIf all queries return results, the issue is in Grafana configuration.")
print("If queries fail, check Prometheus is scraping the exporter correctly.")
