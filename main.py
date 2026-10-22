
from fastapi import FastAPI

app = FastAPI(
    title="LUCRA API",
    version="0.1.0"
)

@app.get("/")
def home():
    return {
        "sistema": "LUCRA",
        "status": "online",
        "versao": "0.1.0"
    }

@app.get("/healthz")
def health_check():
    return {"status": "ok"}
