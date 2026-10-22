
import os
import secrets
import httpx

from fastapi import FastAPI, Request, HTTPException

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


@app.get("/nuvemshop/callback")
async def nuvemshop_callback(code: str, state: str | None = None):
    return {
        "sistema": "LUCRA",
        "integracao": "Nuvemshop",
        "status": "codigo_recebido"
    }



@app.get("/nuvemshop/status")
def nuvemshop_status():
    return {
        "sistema": "LUCRA",
        "integracao": "Nuvemshop",
        "callback": "configurado",
        "autenticacao": "pendente"
    }
