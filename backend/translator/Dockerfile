FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 2 workers = safe for e2-small (2GB RAM). Each worker handles requests concurrently
# via its own thread pool. Bubble translations within each request run in parallel.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5003", "--workers", "2"]
