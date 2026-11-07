from fastapi import FastAPI
app = FastAPI(title="Waimea WaveCast - preprocessing")

@app.get("/health")
def health():
    return {"service": "preprocessing", "status": "up", "pipeline_implemented": False}
