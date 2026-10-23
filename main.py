
import os
import secrets
import httpx
from fastapi import FastAPI, HTTPException, Header
from supabase import create_client

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SECRET_KEY"]
)

NUVEMSHOP_CLIENT_ID = os.environ["NUVEMSHOP_CLIENT_ID"]
NUVEMSHOP_CLIENT_SECRET = os.environ["NUVEMSHOP_CLIENT_SECRET"]

app = FastAPI(
    title="LUCRA API",
    version="0.2.0"
)


@app.get("/")
def home():
    return {
        "sistema": "LUCRA",
        "status": "online",
        "versao": "0.2.0"
    }


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


@app.get("/supabase/status")
def supabase_status():
    try:
        supabase.table("lojas").select("id").limit(1).execute()

        return {
            "sistema": "LUCRA",
            "banco": "Supabase",
            "conexao": "funcionando"
        }
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Falha na conexao com o banco de dados"
        )


@app.get("/nuvemshop/status")
def nuvemshop_status():
    return {
        "sistema": "LUCRA",
        "integracao": "Nuvemshop",
        "callback": "configurado",
        "autenticacao": "habilitada_para_teste"
    }


@app.get("/nuvemshop/callback")
async def nuvemshop_callback(code: str):
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resposta = await client.post(
                "https://www.nuvemshop.com.br/apps/authorize/token",
                json={
                    "client_id": NUVEMSHOP_CLIENT_ID,
                    "client_secret": NUVEMSHOP_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code
                }
            )

        if resposta.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail="Nao foi possivel autorizar a loja na Nuvemshop"
            )

        dados = resposta.json()
        token = dados.get("access_token")
        store_id = dados.get("user_id")

        if not token or not store_id:
            raise HTTPException(
                status_code=502,
                detail="Resposta de autorizacao incompleta"
            )

        supabase.table("nuvemshop_integracoes").upsert(
            {
                "store_id": int(store_id),
                "access_token": token,
                "scope": str(dados.get("scope") or ""),
            },
            on_conflict="store_id"
        ).execute()

        return {
            "sistema": "LUCRA",
            "integracao": "Nuvemshop",
            "status": "loja_conectada",
            "store_id": store_id
        }

    except HTTPException:
        raise
    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Falha de comunicacao com a Nuvemshop"
        )
    except Exception as e:
        print(
            f"ERRO INTEGRACAO LUCRA: {type(e).__name__}",
            flush=True
        )
        raise HTTPException(
            status_code=503,
            detail="Falha ao concluir a integracao"
        )
        
@app.get("/nuvemshop/teste-pedidos")
async def testar_pedidos(
    chave: str = Header(..., alias="X-Lucra-Install-Key")
):
    if not secrets.compare_digest(
        chave, os.environ["LUCRA_INSTALL_SECRET"]
    ):
        raise HTTPException(
            status_code=403,
            detail="Acesso nao autorizado"
        )

    try:
        integracao = (
            supabase.table("nuvemshop_integracoes")
            .select("store_id, access_token")
            .eq("store_id", 8414927)
            .single()
            .execute()
        )

        dados = integracao.data

        if not dados:
            raise HTTPException(
                status_code=404,
                detail="Loja nao encontrada"
            )

        async with httpx.AsyncClient(timeout=20.0) as client:
            resposta = await client.get(
                f"https://api.nuvemshop.com.br/v1/{dados['store_id']}/orders",
                headers={
                    "Authentication": f"bearer {dados['access_token']}",
                    "User-Agent": "LUCRA (integracao de testes)"
                },
                params={"per_page": 5}
            )

        if resposta.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Erro na API Nuvemshop: {resposta.status_code}"
            )

        pedidos = resposta.json()

        return {
            "sistema": "LUCRA",
            "loja": dados["store_id"],
            "quantidade_retornada": len(pedidos),
            "pedidos": [
                {
                    "id": pedido.get("id"),
                    "numero": pedido.get("number"),
                    "total": pedido.get("total"),
                    "status_pagamento": pedido.get("payment_status")
                }
                for pedido in pedidos
            ]
        }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Falha ao consultar pedidos"
        )
