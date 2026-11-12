FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir '.[ml]'
COPY packages /app/packages
COPY services/prediction /app/services/prediction
EXPOSE 8000
CMD ["uvicorn", "services.prediction.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
