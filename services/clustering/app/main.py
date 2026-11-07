from fastapi import FastAPI
app = FastAPI(title="Waimea WaveCast - clustering")

@app.get("/health")
def health():
    return {"service": "clustering", "status": "up", "pipeline_implemented": False}
