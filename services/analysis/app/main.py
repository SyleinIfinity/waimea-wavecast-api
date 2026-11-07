from fastapi import FastAPI
app = FastAPI(title="Waimea WaveCast - analysis")

@app.get("/health")
def health():
    return {"service": "analysis", "status": "up", "pipeline_implemented": False}
