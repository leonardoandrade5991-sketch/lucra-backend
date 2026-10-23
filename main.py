
import os
import re
import secrets
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.environ["SUPABASE_PUBLISHABLE_KEY"]
supabase = create_client(
    SUPABASE_URL,
    os.environ["SUPABASE_SECRET_KEY"]
)

NUVEMSHOP_CLIENT_ID = os.environ["NUVEMSHOP_CLIENT_ID"]
NUVEMSHOP_CLIENT_SECRET = os.environ["NUVEMSHOP_CLIENT_SECRET"]

app = FastAPI(title="LUCRA API", version="0.9.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://lucra-frontend.onrender.com"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Authorization", "Content-Type"],
)


def numero(valor):
    try:
        return Decimal(str(valor if valor is not None else "0"))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal("0")


def dinheiro(valor):
    return float(round(numero(valor), 2))


def verificar_chave(chave):
    segredo = os.environ.get("LUCRA_INSTALL_SECRET", "")
    if not segredo or not secrets.compare_digest(chave, segredo):
        raise HTTPException(403, "Acesso nao autorizado")


def obter_integracao(store_id):
    try:
        resposta = (
            supabase.table("nuvemshop_integracoes")
            .select("store_id, access_token")
            .eq("store_id", int(store_id))
            .limit(1)
            .execute()
        )
    except Exception:
        raise HTTPException(503, "Falha ao consultar integracao")

    if not resposta.data:
        raise HTTPException(404, "Integracao nao encontrada")

    return resposta.data[0]


def loja_teste():
    return int(os.environ.get("LUCRA_TEST_STORE_ID", "8414927"))


async def usuario_autenticado(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login necessario")

    token = authorization[7:].strip()

    if not token:
        raise HTTPException(401, "Token ausente")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resposta = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_PUBLISHABLE_KEY,
                    "Authorization": f"Bearer {token}"
                }
            )
    except httpx.RequestError:
        raise HTTPException(503, "Servico de autenticacao indisponivel")

    if resposta.status_code != 200:
        raise HTTPException(401, "Sessao invalida ou expirada")

    try:
        usuario = resposta.json()
    except ValueError:
        raise HTTPException(502, "Resposta de autenticacao invalida")

    if not usuario.get("id"):
        raise HTTPException(401, "Usuario nao identificado")

    return usuario["id"]


def lojas_do_usuario(usuario_id):
    try:
        vinculos = (
            supabase.table("usuarios_lojas")
            .select("loja_id, papel")
            .eq("usuario_id", usuario_id)
            .execute()
        ).data or []

        if not vinculos:
            return []

        ids = [v["loja_id"] for v in vinculos]

        lojas = (
            supabase.table("lojas")
            .select("id, nome, nuvemshop_store_id")
            .in_("id", ids)
            .execute()
        ).data or []

        papeis = {v["loja_id"]: v["papel"] for v in vinculos}

        return [
            {
                "id": loja["id"],
                "nome": loja["nome"],
                "nuvemshop_store_id": loja["nuvemshop_store_id"],
                "papel": papeis.get(loja["id"])
            }
            for loja in lojas
        ]
    except Exception:
        raise HTTPException(503, "Falha ao consultar permissoes")


async def consultar_pedidos(integracao):
    pedidos = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for pagina in range(1, 52):
            try:
                resposta = await client.get(
                    f"https://api.nuvemshop.com.br/v1/{integracao['store_id']}/orders",
                    headers={
                        "Authentication": f"bearer {integracao['access_token']}",
                        "User-Agent": "LUCRA (integracao de testes)"
                    },
                    params={"per_page": 200, "page": pagina}
                )
            except httpx.RequestError:
                raise HTTPException(502, "Falha de comunicacao com a Nuvemshop")

            if resposta.status_code != 200:
                raise HTTPException(
                    502,
                    f"Erro ao consultar Nuvemshop: {resposta.status_code}"
                )

            try:
                lote = resposta.json()
            except ValueError:
                raise HTTPException(502, "Resposta invalida da Nuvemshop")

            if not isinstance(lote, list):
                raise HTTPException(502, "Formato inesperado de pedidos")

            pedidos.extend(lote)

            if len(lote) < 200:
                return pedidos

    raise HTTPException(503, "Limite de paginacao atingido")


def pedidos_validos(pedidos):
    return [
        p for p in pedidos
        if p.get("payment_status") == "paid"
        and p.get("status") != "cancelled"
    ]


def nome_cliente(pedido):
    cliente = pedido.get("customer") or {}

    nome = pedido.get("contact_name") or cliente.get("name")

    if nome:
        return str(nome)

    nome = " ".join(
        str(cliente.get(c) or "").strip()
        for c in ("first_name", "last_name")
    ).strip()

    return nome or "Cliente nao informado"


def data_pedido(pedido):
    return pedido.get("created_at") or pedido.get("date_created") or ""


def pertence_ao_mes(pedido, mes):
    data = data_pedido(pedido)
    return isinstance(data, str) and data[:7] == mes


def produto_formatado(produto):
    nome = produto.get("name")

    if isinstance(nome, dict):
        nome = nome.get("pt") or next(iter(nome.values()), "")

    return {
        "produto_id": produto.get("product_id"),
        "variante_id": produto.get("variant_id"),
        "nome": str(nome or "Produto"),
        "quantidade": int(produto.get("quantity") or 0),
        "preco_unitario": dinheiro(produto.get("price")),
        "sku": produto.get("sku")
    }


def pedido_formatado(pedido):
    produtos = [
        produto_formatado(p)
        for p in (pedido.get("products") or [])
    ]

    return {
        "id": pedido.get("id"),
        "numero": pedido.get("number"),
        "cliente": nome_cliente(pedido),
        "data": data_pedido(pedido),
        "status_pagamento": pedido.get("payment_status"),
        "valor_pago": dinheiro(pedido.get("total")),
        "frete_checkout": dinheiro(
            pedido.get("shipping_cost_customer")
        ),
        "desconto": dinheiro(pedido.get("discount")),
        "subtotal": dinheiro(pedido.get("subtotal")),
        "quantidade_itens": sum(
            p["quantidade"] for p in produtos
        ),
        "produtos": produtos,
        "custo_produtos": None,
        "taxas": None,
        "lucro_pedido": None,
        "lucro_status": "pendente_custos_e_taxas"
    }


def resumo_pedidos(pedidos, store_id, mes):
    pedidos = [
        pedido_formatado(p)
        for p in pedidos_validos(pedidos)
        if pertence_ao_mes(p, mes)
    ]

    pedidos.sort(
        key=lambda p: p["data"],
        reverse=True
    )

    quantidade = len(pedidos)

    total = sum(
        (numero(p["valor_pago"]) for p in pedidos),
        Decimal("0")
    )

    fretes = sum(
        (numero(p["frete_checkout"]) for p in pedidos),
        Decimal("0")
    )

    itens = sum(
        p["quantidade_itens"] for p in pedidos
    )

    return {
        "sistema": "LUCRA",
        "loja": store_id,
        "mes": mes,
        "quantidade_pedidos": quantidade,
        "faturamento": dinheiro(total),
        "ticket_medio": dinheiro(total / quantidade)
        if quantidade else 0,
        "itens_por_pedido": round(itens / quantidade, 2)
        if quantidade else 0,
        "frete_medio": dinheiro(fretes / quantidade)
        if quantidade else 0,
        "frete_total": dinheiro(fretes),
        "lucro_medio": None,
        "pedidos": pedidos,
        "aviso": "Custos e taxas ainda nao integrados."
    }


def validar_mes(mes):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mes):
        raise HTTPException(422, "Mes invalido. Use AAAA-MM")


@app.get("/")
def home():
    return {
        "sistema": "LUCRA",
        "status": "online",
        "versao": "0.9.0"
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
        raise HTTPException(503, "Falha na conexao com banco")


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
            raise HTTPException(502, "Falha na autorizacao Nuvemshop")

        dados = resposta.json()
        token = dados.get("access_token")
        store_id = dados.get("user_id")

        if not token or not store_id:
            raise HTTPException(502, "Autorizacao incompleta")

        supabase.table("nuvemshop_integracoes").upsert(
            {
                "store_id": int(store_id),
                "access_token": token,
                "scope": str(dados.get("scope") or "")
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
        raise HTTPException(502, "Falha de comunicacao")
    except Exception:
        raise HTTPException(503, "Falha ao concluir integracao")


# ROTAS ADMINISTRATIVAS ANTIGAS
# Exigem a chave privada de instalacao.


@app.get("/nuvemshop/teste-pedidos")
async def testar_pedidos(
    chave: str = Header(..., alias="X-Lucra-Install-Key")
):
    verificar_chave(chave)
    integracao = obter_integracao(loja_teste())
    pedidos = await consultar_pedidos(integracao)

    return {
        "sistema": "LUCRA",
        "loja": integracao["store_id"],
        "quantidade_retornada": min(len(pedidos), 5),
        "pedidos": [
            {
                "id": p.get("id"),
                "numero": p.get("number"),
                "total": p.get("total"),
                "status_pagamento": p.get("payment_status")
            }
            for p in pedidos[:5]
        ]
    }


@app.get("/dashboard/resumo")
async def dashboard_resumo(
    chave: str = Header(..., alias="X-Lucra-Install-Key")
):
    verificar_chave(chave)
    integracao = obter_integracao(loja_teste())
    pedidos = pedidos_validos(
        await consultar_pedidos(integracao)
    )

    faturamento = sum(
        (numero(p.get("total")) for p in pedidos),
        Decimal("0")
    )

    quantidade = len(pedidos)

    return {
        "sistema": "LUCRA",
        "loja": integracao["store_id"],
        "faturamento": dinheiro(faturamento),
        "pedidos_pagos": quantidade,
        "ticket_medio": dinheiro(faturamento / quantidade)
        if quantidade else 0,
        "moeda": "BRL"
    }


@app.get("/pedidos")
async def listar_pedidos_admin(
    mes: str = Query(...),
    chave: str = Header(..., alias="X-Lucra-Install-Key")
):
    verificar_chave(chave)
    validar_mes(mes)
    integracao = obter_integracao(loja_teste())

    return resumo_pedidos(
        await consultar_pedidos(integracao),
        integracao["store_id"],
        mes
    )


# NOVAS ROTAS COM AUTENTICACAO SUPABASE


@app.get("/minhas-lojas")
async def minhas_lojas(
    authorization: str = Header(default="")
):
    usuario_id = await usuario_autenticado(authorization)

    return {
        "sistema": "LUCRA",
        "lojas": lojas_do_usuario(usuario_id)
    }


@app.get("/me/pedidos")
async def meus_pedidos(
    mes: str = Query(...),
    loja_id: str = Query(default=None),
    authorization: str = Header(default="")
):
    validar_mes(mes)

    usuario_id = await usuario_autenticado(authorization)
    lojas = lojas_do_usuario(usuario_id)

    if not lojas:
        raise HTTPException(
            403,
            "Nenhuma loja vinculada a sua conta"
        )

    if loja_id:
        autorizadas = [
            loja for loja in lojas
            if str(loja["id"]) == loja_id
        ]

        if not autorizadas:
            raise HTTPException(
                403,
                "Voce nao tem acesso a esta loja"
            )

        loja = autorizadas[0]

    else:
        if len(lojas) != 1:
            raise HTTPException(
                400,
                "Selecione uma loja usando loja_id"
            )

        loja = lojas[0]

    if not loja.get("nuvemshop_store_id"):
        raise HTTPException(
            404,
            "Loja sem identificador Nuvemshop"
        )

    integracao = obter_integracao(
        loja["nuvemshop_store_id"]
    )

    return resumo_pedidos(
        await consultar_pedidos(integracao),
        integracao["store_id"],
        mes
    )
