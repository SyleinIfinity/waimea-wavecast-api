FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir '.[ml]'
COPY packages /app/packages
COPY services/classification /app/services/classification
EXPOSE 8000
CMD ["uvicorn", "services.classification.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
