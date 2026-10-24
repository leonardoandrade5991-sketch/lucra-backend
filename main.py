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
