# Ingestion service Dockerfile (TL04)
# Build: docker build -f services/ingestion/Dockerfile -t waimea-ingestion .
FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1" \
    "requests>=2.32,<3" "pydantic>=2,<3" "google-cloud-storage>=2.18,<4"

# Copy shared packages and service code
COPY packages/ ./packages/
COPY services/ingestion/ ./services/ingestion/

ENV PYTHONPATH=/app

EXPOSE 8001

CMD ["uvicorn", "services.ingestion.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
