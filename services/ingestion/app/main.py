from fastapi import FastAPI
app = FastAPI(title="Waimea WaveCast - ingestion")

@app.get("/health")
def health():
    return {"service": "ingestion", "status": "up", "pipeline_implemented": False}
