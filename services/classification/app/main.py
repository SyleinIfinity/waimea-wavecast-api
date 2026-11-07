from fastapi import FastAPI
app = FastAPI(title="Waimea WaveCast - classification")

@app.get("/health")
def health():
    return {"service": "classification", "status": "up", "pipeline_implemented": False}
