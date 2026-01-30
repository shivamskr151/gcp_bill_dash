#!/usr/bin/env python3
"""
GCP Billing Exporter for Prometheus
Fetches billing data from Google Cloud Platform and exposes it as Prometheus metrics
"""

import os
import json
import time
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.cloud import bigquery
import logging

from config import (
    BILLING_ACCOUNT_ID,
    BIGQUERY_DATASET,
    EXPORTER_HOST,
    EXPORTER_PORT,
    METRICS_ENDPOINT,
    PROJECT_ID,
    SERVICE_ACCOUNT_FILE,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler for Prometheus metrics endpoint"""
    
    def do_GET(self):
        if self.path == METRICS_ENDPOINT or self.path == "/":
            self.send_response(200)
            self.send_header('Content-type', 'text/plain; version=0.0.4')
            self.end_headers()
            
            try:
                metrics = get_billing_metrics()
                self.wfile.write(metrics.encode())
            except Exception as e:
                logger.error(f"Error generating metrics: {e}")
                error_metrics = f"# Error generating metrics: {str(e)}\n"
                self.wfile.write(error_metrics.encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        logger.info(f"{self.address_string()} - {format % args}")

def get_authenticated_service():
    """Authenticate and return Cloud Billing API service"""
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=['https://www.googleapis.com/auth/cloud-billing.readonly',
                   'https://www.googleapis.com/auth/cloud-platform.read-only']
        )
        service = build('cloudbilling', 'v1', credentials=credentials)
        return service
    except Exception as e:
        logger.error(f"Error authenticating: {e}")
        raise

def get_billing_account_id(service):
    """Get the billing account ID for the project"""
    try:
        project_name = f"projects/{PROJECT_ID}"
        project = service.projects().getBillingInfo(name=project_name).execute()
        billing_account = project.get('billingAccountName', '')
        if billing_account:
            # Extract billing account ID from full name
            # Format: billingAccounts/01XXXX-XXXXXX-XXXXXX
            return billing_account.split('/')[-1]
        return None
    except HttpError as e:
        logger.error(f"Error getting billing account: {e}")
        return None

def get_bigquery_billing_metrics():
    """Get billing metrics from BigQuery (billing export)"""
    metrics = []
    
    try:
        # Authenticate with BigQuery
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=[
                'https://www.googleapis.com/auth/bigquery.readonly',
                'https://www.googleapis.com/auth/bigquery',
                'https://www.googleapis.com/auth/cloud-platform.read-only'
            ]
        )
        client = bigquery.Client(project=PROJECT_ID, credentials=credentials)
        
        # Query billing data from BigQuery
        # Standard usage cost table: gcp_billing_export_v1_<BILLING_ACCOUNT_ID>
        # Detailed usage cost table: gcp_billing_export_resource_v1_<BILLING_ACCOUNT_ID>
        
        # Get current month's total cost
        # Use IST timezone (UTC+5:30) for date calculations to match dashboard
        from datetime import timezone, timedelta
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        today_utc = datetime.utcnow()
        today = today_utc.astimezone(ist_tz)  # Convert to IST
        first_day = today.replace(day=1)
        
        # Calculate previous month dates
        if today.month == 1:
            previous_month_start = datetime(today.year - 1, 12, 1)
        else:
            previous_month_start = datetime(today.year, today.month - 1, 1)
        previous_month_end = first_day - timedelta(days=1)
        
        # First, try to discover the correct table name
        dataset_ref = client.dataset(BIGQUERY_DATASET, project=PROJECT_ID)
        tables = list(client.list_tables(dataset_ref))
        
        # Find billing export table (prefer standard export over resource export)
        billing_table = None
        standard_table = None
        resource_table = None
        
        for table in tables:
            if 'gcp_billing_export_v1_' in table.table_id and 'resource' not in table.table_id:
                standard_table = table.table_id
            elif 'gcp_billing_export_resource_v1_' in table.table_id:
                resource_table = table.table_id
        
        # Prefer standard export table, fallback to resource table
        billing_table = standard_table or resource_table
        
        if not billing_table:
            # Try standard naming convention
            billing_table = f"gcp_billing_export_v1_{BILLING_ACCOUNT_ID.replace('-', '_')}"
            logger.info(f"Using standard table name: {billing_table}")
        else:
            logger.info(f"Found billing table: {billing_table}")
            if resource_table:
                logger.info(f"Found resource table: {resource_table}")

        
        table_id = f"{PROJECT_ID}.{BIGQUERY_DATASET}.{billing_table}"
        
        # Query billing cost - try partition time first, then usage_start_time
        query = f"""
        SELECT 
            SUM(cost) as total_cost,
            service.description as service_name,
            service.id as service_id,
            currency
        FROM `{table_id}`
        WHERE (
            _PARTITIONTIME >= TIMESTAMP('{first_day.strftime('%Y-%m-%d')}')
            AND _PARTITIONTIME < TIMESTAMP('{today.strftime('%Y-%m-%d')}')
        ) OR (
            usage_start_time >= TIMESTAMP('{first_day.strftime('%Y-%m-%d')}')
            AND usage_start_time < TIMESTAMP('{today.strftime('%Y-%m-%d')}')
        )
        GROUP BY service.description, service.id, currency
        ORDER BY total_cost DESC
        """
        
        try:
            query_job = client.query(query)
            results = query_job.result()
            
            total_cost = 0.0
            currency = "USD"
            
            for row in results:
                cost = float(row.total_cost) if row.total_cost else 0.0
                total_cost += cost
                currency = row.currency or "USD"
                service_name = row.service_name or "Unknown"
                service_id = row.service_id or "unknown"
                
                # Add per-service cost metric
                metrics.append(
                    f'gcp_billing_cost{{project="{PROJECT_ID}",service="{service_name}",service_id="{service_id}",currency="{currency}"}} {cost}'
                )
            
            # Add total cost metric (current month)
            metrics.append(
                f'gcp_billing_cost_total{{project="{PROJECT_ID}",billing_account_id="{BILLING_ACCOUNT_ID}",currency="{currency}"}} {total_cost}'
            )
            
            # Query daily costs for the last 7 days (excluding today since it's incomplete)
            # Use yesterday as the end date to ensure we only get complete days
            try:
                yesterday = today - timedelta(days=1)
                seven_days_ago = today - timedelta(days=8)  # Go back 8 days to get 7 complete days
                
                # Convert IST dates to UTC for WHERE clause (BigQuery stores timestamps in UTC)
                # IST is UTC+5:30
                # If today is Jan 28 00:00 IST, that's Jan 27 18:30 UTC
                # To include all of 27 Jan IST, we need data from 27 Jan 00:00 IST to 28 Jan 00:00 IST
                # Which is: 26 Jan 18:30 UTC to 27 Jan 18:30 UTC
                # But we want to be safe and include a bit more, so we use 27 Jan 00:00 UTC to 28 Jan 00:00 UTC
                
                seven_days_ago_ist_start = seven_days_ago.replace(hour=0, minute=0, second=0, microsecond=0)
                today_ist_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
                
                # Convert to UTC: subtract 5:30 hours
                seven_days_ago_utc = seven_days_ago_ist_start - timedelta(hours=5, minutes=30)
                # For today IST 00:00, we want to include up to today IST 23:59:59
                # Today IST 00:00 = (today - 1 day) UTC 18:30
                # Today IST 23:59:59 = today UTC 18:29:59
                # So we use today's date in UTC as the upper bound
                today_utc_end = today_ist_start.replace(tzinfo=None)
                
                seven_days_ago_utc_str = seven_days_ago_utc.strftime('%Y-%m-%d')
                today_utc_end_str = today_utc_end.strftime('%Y-%m-%d')
                
                logger.info(f"Daily cost query date range: {seven_days_ago_utc_str} to {today_utc_end_str} (UTC)")
                logger.info(f"IST dates: {seven_days_ago.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}")
                
                daily_cost_query = f"""
                SELECT 
                    EXTRACT(DATE FROM TIMESTAMP(usage_start_time) AT TIME ZONE 'Asia/Kolkata') as usage_date,
                    SUM(cost) as daily_cost,
                    currency
                FROM `{table_id}`
                WHERE (
                    _PARTITIONTIME >= TIMESTAMP('{seven_days_ago_utc_str}')
                    AND _PARTITIONTIME < TIMESTAMP('{today_utc_end_str}')
                ) OR (
                    usage_start_time >= TIMESTAMP('{seven_days_ago_utc_str}')
                    AND usage_start_time < TIMESTAMP('{today_utc_end_str}')
                )
                GROUP BY EXTRACT(DATE FROM TIMESTAMP(usage_start_time) AT TIME ZONE 'Asia/Kolkata'), currency
                HAVING usage_date < EXTRACT(DATE FROM CURRENT_TIMESTAMP() AT TIME ZONE 'Asia/Kolkata')
                ORDER BY usage_date DESC
                """
                
                daily_query_job = client.query(daily_cost_query)
                daily_results = daily_query_job.result()
                
                row_count = 0
                for row in daily_results:
                    usage_date = row.usage_date
                    daily_cost = float(row.daily_cost) if row.daily_cost else 0.0
                    daily_currency = row.currency or currency
                    
                    # BigQuery EXTRACT(DATE ...) returns a date object
                    # Format as YYYY-MM-DD string
                    if hasattr(usage_date, 'strftime'):
                        date_str = usage_date.strftime('%Y-%m-%d')
                    else:
                        # If it's already a string or different format, convert it
                        date_str = str(usage_date)
                        if 'T' in date_str:
                            date_str = date_str.split('T')[0]
                    
                    metrics.append(
                        f'gcp_billing_cost_daily{{project="{PROJECT_ID}",date="{date_str}",currency="{daily_currency}"}} {daily_cost}'
                    )
                    logger.info(f"Daily cost metric: {date_str} = {daily_cost} {daily_currency}")
                    row_count += 1
                
                logger.info(f"Added {row_count} daily cost metrics for last 7 days")
                logger.info(f"Today (IST): {today.strftime('%Y-%m-%d')}, Yesterday (IST): {yesterday.strftime('%Y-%m-%d')}")
                logger.info(f"Query date range: {seven_days_ago_utc.strftime('%Y-%m-%d')} to {today_utc.strftime('%Y-%m-%d')} (UTC)")
                
                # Daily cost by service (day-wise breakdown per service)
                daily_by_service_query = f"""
                SELECT 
                    EXTRACT(DATE FROM TIMESTAMP(usage_start_time) AT TIME ZONE 'Asia/Kolkata') as usage_date,
                    service.description as service_name,
                    service.id as service_id,
                    SUM(cost) as daily_cost,
                    currency
                FROM `{table_id}`
                WHERE (
                    _PARTITIONTIME >= TIMESTAMP('{seven_days_ago_utc_str}')
                    AND _PARTITIONTIME < TIMESTAMP('{today_utc_end_str}')
                ) OR (
                    usage_start_time >= TIMESTAMP('{seven_days_ago_utc_str}')
                    AND usage_start_time < TIMESTAMP('{today_utc_end_str}')
                )
                GROUP BY EXTRACT(DATE FROM TIMESTAMP(usage_start_time) AT TIME ZONE 'Asia/Kolkata'), service.description, service.id, currency
                HAVING usage_date < EXTRACT(DATE FROM CURRENT_TIMESTAMP() AT TIME ZONE 'Asia/Kolkata')
                ORDER BY usage_date DESC, daily_cost DESC
                """
                daily_by_svc_job = client.query(daily_by_service_query)
                daily_by_svc_results = daily_by_svc_job.result()
                for row in daily_by_svc_results:
                    usage_date = row.usage_date
                    daily_cost = float(row.daily_cost) if row.daily_cost else 0.0
                    daily_currency = row.currency or currency
                    service_name = (row.service_name or "Unknown").replace('"', '\\"')
                    service_id = row.service_id or "unknown"
                    if hasattr(usage_date, 'strftime'):
                        date_str = usage_date.strftime('%Y-%m-%d')
                    else:
                        date_str = str(usage_date).split('T')[0] if 'T' in str(usage_date) else str(usage_date)
                    metrics.append(
                        f'gcp_billing_cost_daily_by_service{{project="{PROJECT_ID}",date="{date_str}",service="{service_name}",service_id="{service_id}",currency="{daily_currency}"}} {daily_cost}'
                    )
                logger.info(f"Added daily-by-service metrics for last 7 days")
                
            except Exception as e:
                logger.warning(f"Could not fetch daily costs: {e}")
            
            # Query instance-level costs if resource table is available
            if resource_table:
                try:
                    resource_table_id = f"{PROJECT_ID}.{BIGQUERY_DATASET}.{resource_table}"
                    logger.info(f"Querying instance costs from {resource_table_id}")
                    
                    instance_cost_query = f"""
                    SELECT 
                        EXTRACT(DATE FROM TIMESTAMP(usage_start_time) AT TIME ZONE 'Asia/Kolkata') as usage_date,
                        (SELECT value FROM UNNEST(labels) WHERE key = 'goog-compute-vm-name' LIMIT 1) as vm_name,
                        resource.name as resource_name,
                        SUM(cost) as daily_cost,
                        currency
                    FROM `{resource_table_id}`
                    WHERE (
                        _PARTITIONTIME >= TIMESTAMP('{seven_days_ago_utc_str}')
                        AND _PARTITIONTIME < TIMESTAMP('{today_utc_end_str}')
                    )
                    AND service.description = 'Compute Engine'
                    GROUP BY usage_date, vm_name, resource_name, currency
                    HAVING usage_date < EXTRACT(DATE FROM CURRENT_TIMESTAMP() AT TIME ZONE 'Asia/Kolkata')
                    AND daily_cost > 0.01
                    ORDER BY daily_cost DESC
                    LIMIT 100
                    """
                    
                    instance_query_job = client.query(instance_cost_query)
                    instance_results = instance_query_job.result()
                    
                    instance_count = 0
                    for row in instance_results:
                        usage_date = row.usage_date
                        daily_cost = float(row.daily_cost) if row.daily_cost else 0.0
                        daily_currency = row.currency or currency
                        
                        # Determine instance name (prefer label, fallback to resource name)
                        instance_name = "unknown"
                        if row.vm_name:
                            instance_name = row.vm_name
                        elif row.resource_name:
                            instance_name = row.resource_name
                        
                        # Clean up instance name (sometimes it's a full path)
                        # e.g. .../instances/my-instance
                        if '/' in instance_name:
                            instance_name = instance_name.split('/')[-1]
                            
                        # Format date
                        if hasattr(usage_date, 'strftime'):
                            date_str = usage_date.strftime('%Y-%m-%d')
                        else:
                            date_str = str(usage_date).split('T')[0]
                            
                        metrics.append(
                            f'gcp_billing_cost_instance_daily{{project="{PROJECT_ID}",date="{date_str}",vm_name="{instance_name}",currency="{daily_currency}"}} {daily_cost}'
                        )
                        instance_count += 1
                        
                    logger.info(f"Added {instance_count} instance cost metrics")
                    
                except Exception as e:
                    logger.warning(f"Could not fetch instance costs: {e}")
            else:
                logger.info("Skipping instance costs query (no resource table found)")

            
            # Query previous month's cost
            try:
                logger.info(f"Querying previous month: {previous_month_start.strftime('%Y-%m-%d')} to {previous_month_end.strftime('%Y-%m-%d')}")
                prev_month_query = f"""
                SELECT 
                    SUM(cost) as total_cost,
                    currency
                FROM `{table_id}`
                WHERE (
                    _PARTITIONTIME >= TIMESTAMP('{previous_month_start.strftime('%Y-%m-%d')}')
                    AND _PARTITIONTIME < TIMESTAMP('{first_day.strftime('%Y-%m-%d')}')
                ) OR (
                    usage_start_time >= TIMESTAMP('{previous_month_start.strftime('%Y-%m-%d')}')
                    AND usage_start_time < TIMESTAMP('{first_day.strftime('%Y-%m-%d')}')
                )
                GROUP BY currency
                """
                
                prev_query_job = client.query(prev_month_query)
                prev_results = prev_query_job.result()
                
                previous_month_total = 0.0
                prev_currency = currency
                row_count = 0
                for row in prev_results:
                    row_count += 1
                    cost = float(row.total_cost) if row.total_cost else 0.0
                    previous_month_total += cost
                    prev_currency = row.currency or currency
                
                logger.info(f"Previous month query returned {row_count} row(s), total cost: {previous_month_total} {prev_currency}")
                
                # Add previous month cost metric
                metrics.append(
                    f'gcp_billing_cost_previous_month{{project="{PROJECT_ID}",billing_account_id="{BILLING_ACCOUNT_ID}",currency="{prev_currency}"}} {previous_month_total}'
                )
                logger.info(f"Previous month cost metric added: {previous_month_total} {prev_currency}")
                
            except Exception as e:
                logger.warning(f"Could not fetch previous month cost: {e}")
                # Add zero value so metric exists
                metrics.append(
                    f'gcp_billing_cost_previous_month{{project="{PROJECT_ID}",billing_account_id="{BILLING_ACCOUNT_ID}",currency="{currency}"}} 0'
                )
            
            # Add exporter status
            metrics.append("# HELP gcp_billing_exporter_up Whether the exporter is working")
            metrics.append("# TYPE gcp_billing_exporter_up gauge")
            metrics.append(f'gcp_billing_exporter_up{{project="{PROJECT_ID}"}} 1')
            
            logger.info(f"Successfully fetched billing data from BigQuery. Total cost: {total_cost} {currency}")
            
        except Exception as e:
            logger.warning(f"Error querying BigQuery table {table_id}: {e}")
            logger.info("Trying alternative query without partition filter...")
            
            # Try query without partition filter (some tables might not have _PARTITIONTIME)
            try:
                alt_query = f"""
                SELECT 
                    SUM(cost) as total_cost,
                    service.description as service_name,
                    service.id as service_id,
                    currency
                FROM `{table_id}`
                WHERE billing_account_id = '{BILLING_ACCOUNT_ID}'
                    AND usage_start_time >= TIMESTAMP('{first_day.strftime('%Y-%m-%d')}')
                    AND usage_start_time < TIMESTAMP('{today.strftime('%Y-%m-%d')}')
                GROUP BY service.description, service.id, currency
                ORDER BY total_cost DESC
                """
                query_job = client.query(alt_query)
                results = query_job.result()
                
                total_cost = 0.0
                currency = "USD"
                for row in results:
                    cost = float(row.total_cost) if row.total_cost else 0.0
                    total_cost += cost
                    currency = row.currency or "USD"
                    service_name = row.service_name or "Unknown"
                    service_id = row.service_id or "unknown"
                    metrics.append(
                        f'gcp_billing_cost{{project="{PROJECT_ID}",service="{service_name}",service_id="{service_id}",currency="{currency}"}} {cost}'
                    )
                metrics.append(
                    f'gcp_billing_cost_total{{project="{PROJECT_ID}",billing_account_id="{BILLING_ACCOUNT_ID}",currency="{currency}"}} {total_cost}'
                )
            except Exception as e2:
                logger.error(f"Alternative query also failed: {e2}")
                # Return zero cost but mark as error
                metrics.append(
                    f'gcp_billing_cost_total{{project="{PROJECT_ID}",billing_account_id="{BILLING_ACCOUNT_ID}",currency="USD"}} 0'
                )
                metrics.append("# HELP gcp_billing_exporter_error Error status")
                metrics.append("# TYPE gcp_billing_exporter_error gauge")
                metrics.append(f'gcp_billing_exporter_error{{project="{PROJECT_ID}"}} 1')
                metrics.append(f"# Error: {str(e2)}")
                return format_prometheus_metrics(metrics)
        
        return format_prometheus_metrics(metrics)
        
    except Exception as e:
        error_msg = str(e)
        if "Access Denied" in error_msg or "Permission" in error_msg:
            logger.error(f"Permission denied accessing BigQuery: {e}")
            logger.error("The service account needs the following roles:")
            logger.error(f"  1. BigQuery Data Viewer (on dataset '{BIGQUERY_DATASET}')")
            logger.error("  2. BigQuery Job User (on project level)")
            logger.error("Grant these in GCP Console: IAM & Admin -> Service Accounts")
        else:
            logger.error(f"Error fetching BigQuery billing metrics: {e}")
        raise

def get_billing_metrics():
    """Fetch billing data and format as Prometheus metrics"""
    try:
        # Try BigQuery first (more reliable if billing export is enabled)
        return get_bigquery_billing_metrics()
    except Exception as e:
        logger.warning(f"BigQuery query failed, trying Cloud Monitoring: {e}")
        try:
            # Fallback to Cloud Monitoring API
            return get_cloud_monitoring_metrics()
        except Exception as e2:
            logger.error(f"Error fetching billing data: {e2}")
            # Return error metric
            metrics = []
            metrics.append("# HELP gcp_billing_exporter_error Error status")
            metrics.append("# TYPE gcp_billing_exporter_error gauge")
            metrics.append(f'gcp_billing_exporter_error{{project="{PROJECT_ID}"}} 1')
            metrics.append(f"# Error: {str(e2)}")
            return format_prometheus_metrics(metrics)

def get_cloud_monitoring_metrics():
    """Get billing metrics from Cloud Monitoring API"""
    metrics = []
    
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=['https://www.googleapis.com/auth/monitoring.read']
        )
        service = build('monitoring', 'v3', credentials=credentials)
        
        project_name = f"projects/{PROJECT_ID}"
        
        # Get billing account ID first (use configured ID or fetch from API)
        billing_service = get_authenticated_service()
        billing_account_id = BILLING_ACCOUNT_ID or get_billing_account_id(billing_service)
        
        if billing_account_id:
            # Query billing cost metric from Cloud Monitoring
            # Metric name: billing/billing_account_id/cost
            metric_name = f"billing/billing_account_id/cost"
            
            # Get current time range (last 30 days)
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=30)
            
            # Convert to RFC3339 format
            end_time_str = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
            start_time_str = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Build the time series query
            request = {
                'name': project_name,
                'filter': f'metric.type="{metric_name}" AND resource.labels.billing_account_id="{billing_account_id}"',
                'interval': {
                    'endTime': end_time_str,
                    'startTime': start_time_str
                },
                'view': 'FULL'
            }
            
            try:
                response = service.projects().timeSeries().list(**request).execute()
                
                total_cost = 0.0
                if 'timeSeries' in response:
                    for series in response['timeSeries']:
                        if 'points' in series and len(series['points']) > 0:
                            # Get the latest point
                            latest_point = series['points'][-1]
                            if 'value' in latest_point and 'doubleValue' in latest_point['value']:
                                cost = latest_point['value']['doubleValue']
                                total_cost += cost
                                
                                # Extract labels
                                labels = {}
                                if 'resource' in series and 'labels' in series['resource']:
                                    labels.update(series['resource']['labels'])
                                if 'metric' in series and 'labels' in series['metric']:
                                    labels.update(series['metric']['labels'])
                                
                                # Format labels for Prometheus
                                label_str = ','.join([f'{k}="{v}"' for k, v in labels.items()])
                                label_str = f'project="{PROJECT_ID}",{label_str}' if label_str else f'project="{PROJECT_ID}"'
                                
                                metrics.append(f'gcp_billing_cost{{billing_account_id="{billing_account_id}",{label_str}}} {cost}')
                
                # Add total cost metric
                metrics.append(f'gcp_billing_cost_total{{project="{PROJECT_ID}",billing_account_id="{billing_account_id}",currency="USD"}} {total_cost}')
                
            except HttpError as e:
                logger.warning(f"Could not fetch billing metrics from Monitoring API: {e}")
                logger.info("This might be because billing export to Cloud Monitoring is not enabled.")
                logger.info("You may need to enable billing export in GCP Console.")
                # Return zero cost as fallback
                metrics.append(f'gcp_billing_cost_total{{project="{PROJECT_ID}",billing_account_id="{billing_account_id}",currency="USD"}} 0')
        else:
            logger.warning("No billing account found for project")
            metrics.append(f'gcp_billing_cost_total{{project="{PROJECT_ID}",currency="USD"}} 0')
        
        # Add exporter status
        metrics.append("# HELP gcp_billing_exporter_up Whether the exporter is working")
        metrics.append("# TYPE gcp_billing_exporter_up gauge")
        metrics.append(f'gcp_billing_exporter_up{{project="{PROJECT_ID}"}} 1')
        
        return format_prometheus_metrics(metrics)
        
    except Exception as e:
        logger.error(f"Error fetching Cloud Monitoring metrics: {e}")
        metrics.append("# HELP gcp_billing_exporter_error Error status")
        metrics.append("# TYPE gcp_billing_exporter_error gauge")
        metrics.append(f'gcp_billing_exporter_error{{project="{PROJECT_ID}"}} 1')
        metrics.append(f"# Error: {str(e)}")
        return format_prometheus_metrics(metrics)

def format_prometheus_metrics(metrics_list):
    """Format metrics list with proper Prometheus format"""
    # Add help and type declarations if not present
    formatted = []
    
    # Add standard headers
    if not any("# HELP gcp_billing_cost" in m for m in metrics_list):
        formatted.append("# HELP gcp_billing_cost Billing cost in USD")
        formatted.append("# TYPE gcp_billing_cost gauge")
    
    if not any("# HELP gcp_billing_cost_total" in m for m in metrics_list):
        formatted.append("# HELP gcp_billing_cost_total Total billing cost in USD")
        formatted.append("# TYPE gcp_billing_cost_total gauge")
    
    if not any("# HELP gcp_billing_cost_daily_by_service" in m for m in metrics_list) and any("gcp_billing_cost_daily_by_service" in m for m in metrics_list):
        formatted.append("# HELP gcp_billing_cost_daily_by_service Daily billing cost per service (by date)")
        formatted.append("# TYPE gcp_billing_cost_daily_by_service gauge")

    if not any("# HELP gcp_billing_cost_instance_daily" in m for m in metrics_list) and any("gcp_billing_cost_instance_daily" in m for m in metrics_list):
        formatted.append("# HELP gcp_billing_cost_instance_daily Daily billing cost per VM instance")
        formatted.append("# TYPE gcp_billing_cost_instance_daily gauge")

    
    formatted.extend(metrics_list)
    return "\n".join(formatted) + "\n"

def main():
    """Main function to start the HTTP server"""
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        logger.error(f"Service account file not found: {SERVICE_ACCOUNT_FILE}")
        return
    
    server = HTTPServer((EXPORTER_HOST, EXPORTER_PORT), MetricsHandler)
    logger.info(f"GCP Billing Exporter started on {EXPORTER_HOST}:{EXPORTER_PORT}")
    logger.info(f"Metrics available at http://localhost:{EXPORTER_PORT}{METRICS_ENDPOINT}")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.shutdown()

if __name__ == "__main__":
    main()
