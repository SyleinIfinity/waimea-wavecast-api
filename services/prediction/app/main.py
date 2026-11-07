from fastapi import FastAPI
app = FastAPI(title="Waimea WaveCast - prediction")

@app.get("/health")
def health():
    return {"service": "prediction", "status": "up", "pipeline_implemented": False}
