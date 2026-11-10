# Preprocessing service Dockerfile (TL04)
# Build: docker build -f services/preprocessing/Dockerfile -t waimea-preprocessing .
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1" \
    "requests>=2.32,<3" "pydantic>=2,<3"

COPY packages/ ./packages/
COPY services/preprocessing/ ./services/preprocessing/

ENV PYTHONPATH=/app

EXPOSE 8002

CMD ["uvicorn", "services.preprocessing.app.main:app", "--host", "0.0.0.0", "--port", "8002"]
