FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose metrics port
EXPOSE 9091

# Run exporter
CMD ["python", "gcp_billing_exporter.py"]
