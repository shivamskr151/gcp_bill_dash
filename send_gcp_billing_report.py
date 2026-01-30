#!/usr/bin/env python3
"""
Daily GCP billing report script.

This script:
1. Fetches GCP billing data (via Prometheus metrics) for the day that is
   two days before "today" (e.g. on 29/01/2026 it fetches 27/01/2026).
2. Generates a simple PDF report.
3. Emails the PDF to a fixed list of recipients over SMTP (GoDaddy).

Schedule this script via cron (or another scheduler) to run daily at 9:30 AM.
"""

import os
import ssl
import smtplib
from datetime import datetime, date, time, timedelta, timezone
from typing import Dict, List, Tuple
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import requests
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Prometheus instance that already has GCP billing metrics
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "glassy-song-449317-a9")

# Email / SMTP configuration (GoDaddy)
SMTP_CONFIG = {
    "host": os.environ.get("SMTP_HOST", "smtpout.secureserver.net"),
    "port": int(os.environ.get("SMTP_PORT", "465")),
    "username": os.environ.get("SMTP_USERNAME", "information@variphi.com"),
    # Store real password in environment variable for safety
    "password": os.environ.get("SMTP_PASSWORD", "CHANGE_ME"),
    "sender": os.environ.get("SMTP_SENDER", "information@variphi.com"),
    "recipients": [
        "shivamskr151@gmail.com",
        "muskan.betla@gmail.com",
        "wr.akashkumar@gmail.com",
        "deepsprojects10@gmail.com",
        "nitishmishra006@gmail.com",
        "surajsinghdeo15@gmail.com",
        "msdeo@variphi.com",
        "atulverma2861@gmail.com",
        "yrshivani2001@gmail.com"
    ],
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_target_date(days_before_today: int = 2) -> date:
    """Return the date N days before today (local date)."""
    return datetime.now().date() - timedelta(days=days_before_today)


def _prometheus_instant_query(query: str, eval_time: datetime) -> Dict:
    """Run an instant Prometheus query at a specific evaluation time."""
    resp = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": query, "time": eval_time.timestamp()},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        error_msg = data.get("error", "Unknown error")
        error_type = data.get("errorType", "Unknown")
        raise RuntimeError(f"Prometheus query failed ({error_type}): {error_msg}\nQuery: {query}")
    return data


def fetch_billing_data_for_date(target_date: date) -> Tuple[float, Dict[str, float], List[Dict], str]:
    """
    Fetch billing data for a single calendar day from Prometheus.

    Returns:
        total_cost: float
        service_costs: dict[service_name] = cost
        instance_sku_costs: list of dicts [{'vm_name': str, 'sku': str, 'cost': float}]
        currency: str (e.g., "USD", "INR")
    """
    # Format date as YYYY-MM-DD to match the date label in Prometheus metrics
    date_str = target_date.strftime("%Y-%m-%d")

    # Query daily cost metrics using the date label
    # These metrics are already daily aggregates, so we just filter by date
    total_query = (
        f'gcp_billing_cost_daily{{project="{GCP_PROJECT_ID}",date="{date_str}"}}'
    )
    per_service_query = (
        f'gcp_billing_cost_daily_by_service{{project="{GCP_PROJECT_ID}",date="{date_str}"}}'
    )
    # Using SKU level query for detailed instance report
    per_instance_sku_query = (
        f'gcp_billing_cost_instance_sku_daily{{project="{GCP_PROJECT_ID}",date="{date_str}"}}'
    )

    # Use current time for query (these are gauge metrics, not time-series)
    query_time = datetime.now(timezone.utc)
    
    total_data = _prometheus_instant_query(total_query, query_time)
    per_service_data = _prometheus_instant_query(per_service_query, query_time)
    per_instance_sku_data = _prometheus_instant_query(per_instance_sku_query, query_time)

    # Parse total cost
    total_cost = 0.0
    currency = "USD"  # Default, will be updated from metric if available
    result_total = total_data.get("data", {}).get("result", [])
    if result_total:
        # Get the first result (should only be one for a specific date)
        value = result_total[0].get("value", [None, "0"])[1]
        total_cost = float(value)
        # Extract currency from metric labels if available
        metric = result_total[0].get("metric", {})
        currency = metric.get("currency", "USD")
    else:
        print(f"⚠️  Warning: No billing data found for date {date_str}")
        print(f"   Available dates might be different. Check Prometheus metrics.")

    # Parse costs per service
    service_costs: Dict[str, float] = {}
    for series in per_service_data.get("data", {}).get("result", []):
        metric = series.get("metric", {})
        service_name = metric.get("service", "unknown")
        value = series.get("value", [None, "0"])[1]
        service_costs[service_name] = float(value)

    # Parse costs per instance SKU
    instance_sku_costs: List[Dict] = []
    
    for series in per_instance_sku_data.get("data", {}).get("result", []):
        metric = series.get("metric", {})
        # Sometimes vm_name might be missing or under 'exported_instance'
        vm_name = metric.get("vm_name") or metric.get("exported_instance") or "unknown"
        sku = metric.get("sku", "unknown")
        value = series.get("value", [None, "0"])[1]
        
        instance_sku_costs.append({
            "vm_name": vm_name,
            "sku": sku,
            "cost": float(value)
        })

    return total_cost, service_costs, instance_sku_costs, currency


def generate_billing_pdf(
    output_path: str,
    target_date: date,
    total_cost: float,
    service_costs: Dict[str, float],
    instance_sku_costs: List[Dict],
    currency: str = "USD",
) -> None:
    """Generate a simple PDF summary of billing data."""
    c = canvas.Canvas(output_path, pagesize=LETTER)
    width, height = LETTER

    # Margins
    margin_x = 50
    y = height - 60

    # Title
    c.setFont("Helvetica-Bold", 20)
    c.drawString(
        margin_x,
        y,
        f"GCP Billing Report - {target_date.strftime('%d/%m/%Y')}",
    )
    y -= 40

    # Total cost
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin_x, y, f"Total Cost ({currency}): {total_cost:,.2f}")
    y -= 30

    # --- Service Cost Table ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin_x, y, "Service Breakdown")
    y -= 20

    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_x, y, "Service")
    c.drawString(width - margin_x - 100, y, f"Cost ({currency})")
    y -= 10
    c.line(margin_x, y, width - margin_x, y)
    y -= 15

    # Table rows
    c.setFont("Helvetica", 10)
    if service_costs:
        sorted_services = sorted(service_costs.items(), key=lambda x: x[1], reverse=True)
        for service, cost in sorted_services:
            if y < 80:
                c.showPage()
                y = height - 60
            c.drawString(margin_x, y, service)
            c.drawRightString(width - margin_x, y, f"{cost:,.2f}")
            y -= 15
    else:
        c.drawString(margin_x, y, "No service data.")
        y -= 15
    
    y -= 25

    # --- Instance Cost Details Table (with SKUs) ---
    if y < 150: # Check if enough space for header + a few rows
        c.showPage()
        y = height - 60
    
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin_x, y, "Instance Cost Details")
    y -= 20

    # Table Header
    c.setFont("Helvetica-Bold", 9)
    # Define columns: Instance (left), SKU (left, offset), Cost (right)
    col_instance = margin_x
    col_sku = margin_x + 150
    col_cost = width - margin_x
    
    c.drawString(col_instance, y, "Instance Name")
    c.drawString(col_sku, y, "SKU Description")
    c.drawRightString(col_cost, y, f"Cost ({currency})")
    y -= 10
    c.line(margin_x, y, width - margin_x, y)
    y -= 15

    c.setFont("Helvetica", 8)
    if instance_sku_costs:
        # Sort by cost descending
        sorted_items = sorted(instance_sku_costs, key=lambda x: x['cost'], reverse=True)
        total_instance_cost = sum(item['cost'] for item in sorted_items)
        
        for item in sorted_items:
            if y < 50:
                c.showPage()
                y = height - 60
                # Re-draw header on new page
                c.setFont("Helvetica-Bold", 9)
                c.drawString(col_instance, y, "Instance Name")
                c.drawString(col_sku, y, "SKU Description")
                c.drawRightString(col_cost, y, f"Cost ({currency})")
                y -= 10
                c.line(margin_x, y, width - margin_x, y)
                y -= 15
                c.setFont("Helvetica", 8)
            
            # Truncate names if too long
            vm_text = item['vm_name'][:25] + "..." if len(item['vm_name']) > 28 else item['vm_name']
            sku_text = item['sku'][:50] + "..." if len(item['sku']) > 53 else item['sku']
            
            c.drawString(col_instance, y, vm_text)
            c.drawString(col_sku, y, sku_text)
            c.drawRightString(col_cost, y, f"{item['cost']:,.2f}")
            y -= 12
        
        # Add total line
        y -= 5
        c.line(margin_x, y, width - margin_x, y)
        y -= 15
        c.setFont("Helvetica-Bold", 9)
        c.drawString(col_instance, y, "Total Instance Cost")
        c.drawRightString(col_cost, y, f"{total_instance_cost:,.2f}")
    else:
        c.drawString(margin_x, y, "No instance cost data available.")
        y -= 15

    c.showPage()
    c.save()


def send_email_with_attachments(
    subject: str,
    body_text: str,
    body_html: str,
    attachment_paths: List[str],
) -> None:
    """Send an email with attachments via SMTP (SSL)."""
    if not SMTP_CONFIG["password"] or SMTP_CONFIG["password"] == "CHANGE_ME":
        raise RuntimeError(
            "SMTP_PASSWORD environment variable not set. "
            "Set it to the real password for information@variphi.com."
        )

    msg = MIMEMultipart("mixed")
    msg["From"] = SMTP_CONFIG["sender"]
    msg["To"] = ", ".join(SMTP_CONFIG["recipients"])
    msg["Subject"] = subject

    # Alternative (text + HTML)
    msg_body = MIMEMultipart("alternative")
    msg_body.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg_body.attach(MIMEText(body_html, "html"))
    msg.attach(msg_body)

    # Attach files
    for path in attachment_paths:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            part = MIMEApplication(f.read())
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=os.path.basename(path),
            )
            msg.attach(part)

    # Log recipients for debugging
    print(f"📧 Sending email to {len(SMTP_CONFIG['recipients'])} recipients:")
    for recipient in SMTP_CONFIG["recipients"]:
        print(f"   - {recipient}")
    
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        SMTP_CONFIG["host"],
        SMTP_CONFIG["port"],
        context=context,
    ) as server:
        server.login(SMTP_CONFIG["username"], SMTP_CONFIG["password"])
        
        # Send to all recipients
        # sendmail returns a dictionary of failed recipients (empty dict = success)
        failed_recipients = server.sendmail(
            SMTP_CONFIG["sender"],
            SMTP_CONFIG["recipients"],
            msg.as_string(),
        )
        
        if failed_recipients:
            print(f"⚠️  Warning: Failed to send to some recipients: {failed_recipients}")
        else:
            print(f"✅ Email sent successfully to all {len(SMTP_CONFIG['recipients'])} recipients")


def main() -> None:
    # Determine which day's data to send (two days before "today")
    target_date = get_target_date(days_before_today=2)
    print(f"📅 Fetching billing data for: {target_date.strftime('%d/%m/%Y')}")

    # Fetch billing data from Prometheus (which is fed by GCP billing exporter)
    try:
        total_cost, service_costs, instance_sku_costs, currency = fetch_billing_data_for_date(target_date)
        print(f"✅ Fetched data: Total = {total_cost:,.2f}, Services = {len(service_costs)}, SKU entries = {len(instance_sku_costs)}")
    except Exception as e:
        print(f"❌ Error fetching billing data: {e}")
        raise

    # Generate PDF in the same directory as this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pdf_filename = f"gcp_billing_report_{target_date.strftime('%Y%m%d')}.pdf"
    pdf_path = os.path.join(script_dir, pdf_filename)
    generate_billing_pdf(pdf_path, target_date, total_cost, service_costs, instance_sku_costs, currency)

    # Email contents
    date_str = target_date.strftime("%d/%m/%Y")
    subject = f"GCP Billing Report for {date_str}"
    body_text = (
        f"Hello,\n\n"
        f"Please find attached the GCP billing report for {date_str}.\n\n"
        f"Total cost ({currency}): {total_cost:,.2f}\n"
        f"Number of services: {len(service_costs)}\n\n"
        f"Best regards,\n"
        f"Team VariPhi"
    )

    body_html = f"""
<html>
  <body>
    <p>Hello,</p>
    <p>Please find attached the GCP billing report for <strong>{date_str}</strong>.</p>
    <p><strong>Total cost ({currency}): {total_cost:,.2f}</strong></p>
    <p>Number of services: {len(service_costs)}</p>
    <p>Best regards,<br/>Team VariPhi</p>
  </body>
</html>
""".strip()

    # Send email
    send_email_with_attachments(subject, body_text, body_html, [pdf_path])


if __name__ == "__main__":
    main()

