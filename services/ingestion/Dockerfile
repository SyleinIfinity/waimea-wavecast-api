# Ingestion service Dockerfile (TL04)
# Build: docker build -f services/ingestion/Dockerfile -t waimea-ingestion .
FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy shared packages and service code
COPY packages/ ./packages/
COPY services/ingestion/ ./services/ingestion/

ENV PYTHONPATH=/app

EXPOSE 8001

CMD ["uvicorn", "services.ingestion.app.main:app", "--host", "0.0.0.0", "--port", "8001"]
