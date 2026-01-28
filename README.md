# GCP Billing Monitor

Exports GCP billing data from BigQuery as Prometheus metrics and provides dashboards (Grafana and standalone HTML).

All configuration is read from `.env` (no hardcoded IDs/ports in code).

---

## Prerequisites

- **Python 3.10+**
- **Docker Desktop** (for Prometheus + Grafana)
- **Service account key file** (path configured in `.env` via `GOOGLE_SERVICE_ACCOUNT_FILE`)
- **GCP:** Billing export to BigQuery enabled (project/dataset configured in `.env`)

---

## Quick start

### 0. Configure environment

Copy `.env.example` to `.env` and fill values:

```bash
cp .env.example .env
```

### 1. Virtual environment and dependencies

```bash
cd /Users/shivam/Desktop/monitor
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Verify GCP / BigQuery access (optional)

```bash
python test_bigquery_access.py
```

### 3. Run the exporter

**Terminal 1:**

```bash
source venv/bin/activate
python gcp_billing_exporter.py
```

Exporter: `http://localhost:$EXPORTER_PORT` — metrics at `http://localhost:$EXPORTER_PORT$EXPORTER_METRICS_ENDPOINT`

### 4. Run Prometheus and Grafana

**Terminal 2:**

```bash
docker-compose up -d
```

- **Prometheus:** `http://localhost:$PROMETHEUS_PORT`  
- **Grafana:** `http://localhost:$GRAFANA_PORT` (login from `.env`)

### 5. View dashboards

- **HTML dashboard:** generate `dashboard_config.js` from `.env`, then open `gcp_billing_dashboard.html`:

```bash
python generate_dashboard_config.py
```

- **Grafana:** http://localhost:3000 → Dashboards → Import → upload `gcp_billing_dashboard.json` → choose Prometheus data source.

---

## Project layout

| File / folder            | Purpose |
|--------------------------|--------|
| `gcp_billing_exporter.py`| Exporter: BigQuery → Prometheus metrics on :9091 |
| `gcp_billing_dashboard.html` | Standalone HTML dashboard (talks to Prometheus) |
| `gcp_billing_dashboard.json` | Grafana dashboard definition |
| `config.py` | Loads `.env` + exposes configuration |
| `generate_dashboard_config.py` | Generates `dashboard_config.js` for the HTML dashboard |
| `test_bigquery_access.py`    | Check BigQuery access and list billing tables |
| `test_grafana_queries.py`    | Test Prometheus queries used by dashboards |
| `docker-compose.yml`        | Prometheus + Grafana containers |
| `requirements.txt`          | Python dependencies |
| `.env` | All runtime configuration (not committed) |

---

## GCP setup

### Billing export to BigQuery

1. [GCP Console → Billing → Billing export](https://console.cloud.google.com/billing/export)
2. Enable **BigQuery export**; use the dataset configured in `.env` (`BQ_BILLING_DATASET`) for your project (`GCP_PROJECT_ID`).
3. Ensure tables exist in your dataset, e.g. `gcp_billing_export_v1_*` and/or `gcp_billing_export_resource_v1_*`.

### Service account permissions

- **BigQuery Job User** on your project: run queries.
- **BigQuery Data Viewer** on your dataset: read billing tables.

**Using GCP Console**

1. IAM → find the service account → Edit → add role **BigQuery Job User**.
2. BigQuery → your dataset → **Sharing** → **Permissions** → Add principal (service account email) → role **BigQuery Data Viewer**.

**Using gcloud**

```bash
gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:<service-account-email>" \
  --role="roles/bigquery.jobUser"

bq add-iam-policy-binding --dataset="$GCP_PROJECT_ID:$BQ_BILLING_DATASET" \
  --member="serviceAccount:<service-account-email>" \
  --role="roles/bigquery.dataViewer"
```

---

## Metrics

| Metric | Description |
|--------|-------------|
| `gcp_billing_cost_total` | Total billing cost (currency label) |
| `gcp_billing_cost` | Cost per service |
| `gcp_billing_cost_daily` | Daily cost (last 7+ days) |
| `gcp_billing_cost_previous_month` | Previous month total |
| `gcp_billing_exporter_up` | Exporter up (1) / down (0) |
| `gcp_billing_exporter_error` | Error (1) / OK (0) |

---

## API & endpoints

### Exporter (port 9091)

- **GET** `/metrics` — Prometheus-format metrics  
  Example: `curl http://localhost:9091/metrics`

### Prometheus (port 9090)

- **GET** `/api/v1/query?query=<promql>` — instant query  
- **GET** `/api/v1/query_range?query=...&start=...&end=...&step=...` — range query  
- **GET** `/api/v1/targets` — scrape targets  
- **GET** `/api/v1/label/<name>/values` — label values  

Examples:

```bash
curl "http://localhost:$PROMETHEUS_PORT/api/v1/query?query=gcp_billing_cost_total{project=\"$GCP_PROJECT_ID\"}"
curl "http://localhost:$PROMETHEUS_PORT/api/v1/targets"
```

### Grafana (port 3000)

- **GET** `/api/health` — health check  
- **GET** `/api/datasources` — data sources (needs auth)  
- Login: `admin` / `admin` (change on first use).

### PromQL examples

```promql
gcp_billing_cost_total{project="<your-project-id>"}
gcp_billing_cost{project="<your-project-id>"}
gcp_billing_cost_daily{project="<your-project-id>"}
gcp_billing_exporter_up{project="<your-project-id>"}
gcp_billing_exporter_error{project="<your-project-id>"}
```

---

## Management

**Start**

- Exporter: `source venv/bin/activate && python gcp_billing_exporter.py`
- Stack: `docker-compose up -d`

**Stop**

- Exporter: `Ctrl+C` or `pkill -f gcp_billing_exporter.py`
- Stack: `docker-compose down`

**Background exporter**

```bash
nohup python gcp_billing_exporter.py > exporter.log 2>&1 &
```

**Check status**

```bash
docker-compose ps
curl -s http://localhost:9091/metrics | head -20
curl -s http://localhost:9090/api/v1/targets
```

**Logs**

```bash
docker-compose logs -f prometheus
docker-compose logs -f grafana
```

---

## Troubleshooting

### No billing data

1. Run `python test_bigquery_access.py` and fix any BigQuery/perm errors.
2. Confirm billing export to BigQuery is enabled and dataset `vgi` has billing tables.
3. Hit http://localhost:9091/metrics and look for `gcp_billing_*` metrics.
4. In Prometheus, check http://localhost:9090/targets — job `gcp_billing` should be **UP**.

### Exporter won’t start

- `pip install -r requirements.txt` and ensure your service account key file exists at the path set in `.env` (`GOOGLE_SERVICE_ACCOUNT_FILE`).
- Confirm `.env` values are correct (project/dataset/billing account).

### Prometheus not scraping exporter

- Exporter must be reachable from the Prometheus container. Configure `PROMETHEUS_SCRAPE_TARGET` in `.env` (default is typically `host.docker.internal:9091` on macOS/Windows Docker Desktop).
- From the host: `curl http://localhost:9091/metrics` should return metrics.
- If you run Prometheus on the host, set `PROMETHEUS_SCRAPE_TARGET=localhost:9091`.

### Grafana “No data”

1. In Grafana: **Connections → Data sources** → Prometheus URL should be `http://prometheus:9090` when using Docker (same Compose network).
2. **Explore** → select Prometheus → run `gcp_billing_cost_total{project="$GCP_PROJECT_ID"}` (or set the Grafana dashboard variable `project`). If data appears here, fix the dashboard’s data source/panels.
3. Ensure dashboard time range (e.g. “Last 7 days”) includes recent data.
4. When importing `gcp_billing_dashboard.json`, pick the correct Prometheus data source; if the UID differs, edit panels and set “Data source” to your Prometheus instance.

### BigQuery “Access Denied”

- Ensure the service account has **BigQuery Job User** (project) and **BigQuery Data Viewer** (dataset `vgi`) as in the GCP setup section above.

---

## Ports

| Service   | Port | Purpose        |
|----------|------|----------------|
| Exporter | 9091 | /metrics       |
| Prometheus | 9090 | UI, query API |
| Grafana  | 3000 | Dashboards     |
