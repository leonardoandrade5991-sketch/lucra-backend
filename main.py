
import os
import re
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from supabase import create_client

supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SECRET_KEY'])
NUVEMSHOP_CLIENT_ID = os.environ['NUVEMSHOP_CLIENT_ID']
NUVEMSHOP_CLIENT_SECRET = os.environ['NUVEMSHOP_CLIENT_SECRET']
app = FastAPI(title='LUCRA API', version='0.3.0')


def verificar_chave(chave):
    segredo = os.environ.get('LUCRA_INSTALL_SECRET', '')
    if not segredo or not secrets.compare_digest(chave, segredo):
        raise HTTPException(status_code=403, detail='Acesso nao autorizado')


def obter_integracao():
    try:
        loja_teste = int(os.environ.get('LUCRA_TEST_STORE_ID', '8414927'))
        resposta = (supabase.table('nuvemshop_integracoes')
                    .select('store_id, access_token')
                    .eq('store_id', loja_teste).single().execute())
        if not resposta.data:
            raise HTTPException(status_code=404, detail='Loja nao encontrada')
        return resposta.data
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail='Falha ao acessar a integracao')


def numero(valor):
    try:
        return Decimal(str(valor if valor is not None else '0'))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def dinheiro(valor):
    return float(round(numero(valor), 2))


async def consultar_pedidos(integracao):
    pedidos = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for pagina in range(1, 52):
            try:
                resposta = await client.get(
                    f"https://api.nuvemshop.com.br/v1/{integracao['store_id']}/orders",
                    headers={
                        'Authentication': f"bearer {integracao['access_token']}",
                        'User-Agent': 'LUCRA (integracao de testes)'
                    },
                    params={'per_page': 200, 'page': pagina}
                )
            except httpx.RequestError:
                raise HTTPException(status_code=502, detail='Falha de comunicacao com a Nuvemshop')
            if resposta.status_code != 200:
                raise HTTPException(status_code=502, detail=f'Erro ao consultar pedidos: {resposta.status_code}')
            try:
                lote = resposta.json()
            except ValueError:
                raise HTTPException(status_code=502, detail='Resposta invalida da Nuvemshop')
            if not isinstance(lote, list):
                raise HTTPException(status_code=502, detail='Formato inesperado de pedidos')
            pedidos.extend(lote)
            if len(lote) < 200:
                return pedidos
    raise HTTPException(status_code=503, detail='Limite de consulta atingido; paginacao incompleta')


def pedidos_validos(pedidos):
    return [p for p in pedidos if p.get('payment_status') == 'paid' and p.get('status') != 'cancelled']


def nome_cliente(p):
    cliente = p.get('customer') or {}
    contato = p.get('contact_name') or cliente.get('name')
    if contato:
        return str(contato)
    nome = ' '.join(str(cliente.get(c) or '').strip() for c in ('first_name', 'last_name')).strip()
    return nome or 'Cliente nao informado'


def data_pedido(p):
    return p.get('created_at') or p.get('date_created') or ''


def pertence_ao_mes(p, mes):
    data = data_pedido(p)
    # Datas ISO 8601 da Nuvemshop; referencia pelo mes registrado no pedido.
    return isinstance(data, str) and data[:7] == mes


def produto_formatado(p):
    nome = p.get('name')
    if isinstance(nome, dict):
        nome = nome.get('pt') or next(iter(nome.values()), '')
    return {
        'produto_id': p.get('product_id'),
        'variante_id': p.get('variant_id'),
        'nome': str(nome or 'Produto'),
        'quantidade': int(p.get('quantity') or 0),
        'preco_unitario': dinheiro(p.get('price')),
        'sku': p.get('sku') or None,
    }


def pedido_formatado(p):
    produtos = [produto_formatado(x) for x in (p.get('products') or [])]
    return {
        'id': p.get('id'),
        'numero': p.get('number'),
        'cliente': nome_cliente(p),
        'data': data_pedido(p),
        'status_pagamento': p.get('payment_status'),
        'valor_pago': dinheiro(p.get('total')),
        'frete_checkout': dinheiro(p.get('shipping_cost_customer')),
        'desconto': dinheiro(p.get('discount')),
        'subtotal': dinheiro(p.get('subtotal')),
        'quantidade_itens': sum(x['quantidade'] for x in produtos),
        'produtos': produtos,
        # Custos unitarios sao cadastrados no LUCRA e ainda nao estao no banco.
        'custo_produtos': None,
        'taxas': None,
        'lucro_pedido': None,
        'lucro_status': 'pendente_custos_e_taxas'
    }


@app.get('/')
def home():
    return {'sistema': 'LUCRA', 'status': 'online', 'versao': '0.3.0'}


@app.get('/healthz')
def health_check():
    return {'status': 'ok'}


@app.get('/supabase/status')
def supabase_status():
    try:
        supabase.table('lojas').select('id').limit(1).execute()
        return {'sistema': 'LUCRA', 'banco': 'Supabase', 'conexao': 'funcionando'}
    except Exception:
        raise HTTPException(status_code=503, detail='Falha na conexao com o banco de dados')


@app.get('/nuvemshop/status')
def nuvemshop_status():
    return {'sistema': 'LUCRA', 'integracao': 'Nuvemshop', 'callback': 'configurado', 'autenticacao': 'habilitada_para_teste'}


@app.get('/nuvemshop/callback')
async def nuvemshop_callback(code: str):
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resposta = await client.post(
                'https://www.nuvemshop.com.br/apps/authorize/token',
                json={'client_id': NUVEMSHOP_CLIENT_ID,
                      'client_secret': NUVEMSHOP_CLIENT_SECRET,
                      'grant_type': 'authorization_code', 'code': code}
            )
        if resposta.status_code != 200:
            raise HTTPException(status_code=502, detail='Nao foi possivel autorizar a loja na Nuvemshop')
        dados = resposta.json()
        token, store_id = dados.get('access_token'), dados.get('user_id')
        if not token or not store_id:
            raise HTTPException(status_code=502, detail='Resposta de autorizacao incompleta')
        supabase.table('nuvemshop_integracoes').upsert(
            {'store_id': int(store_id), 'access_token': token, 'scope': str(dados.get('scope') or '')},
            on_conflict='store_id'
        ).execute()
        return {'sistema': 'LUCRA', 'integracao': 'Nuvemshop', 'status': 'loja_conectada', 'store_id': store_id}
    except HTTPException:
        raise
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail='Falha de comunicacao com a Nuvemshop')
    except Exception as e:
        print(f'ERRO INTEGRACAO LUCRA: {type(e).__name__}', flush=True)
        raise HTTPException(status_code=503, detail='Falha ao concluir a integracao')


@app.get('/nuvemshop/teste-pedidos')
async def testar_pedidos(chave: str = Header(..., alias='X-Lucra-Install-Key')):
    verificar_chave(chave)
    integracao = obter_integracao()
    pedidos = await consultar_pedidos(integracao)
    return {
        'sistema': 'LUCRA', 'loja': integracao['store_id'],
        'quantidade_retornada': min(len(pedidos), 5),
        'pedidos': [{'id': p.get('id'), 'numero': p.get('number'),
                     'total': p.get('total'), 'status_pagamento': p.get('payment_status')}
                    for p in pedidos[:5]]
    }


@app.get('/dashboard/resumo')
async def dashboard_resumo(chave: str = Header(..., alias='X-Lucra-Install-Key')):
    verificar_chave(chave)
    integracao = obter_integracao()
    pedidos = pedidos_validos(await consultar_pedidos(integracao))
    faturamento = sum((numero(p.get('total')) for p in pedidos), Decimal('0'))
    quantidade = len(pedidos)
    return {
        'sistema': 'LUCRA', 'loja': integracao['store_id'],
        'faturamento': dinheiro(faturamento), 'pedidos_pagos': quantidade,
        'ticket_medio': dinheiro(faturamento / quantidade) if quantidade else 0,
        'moeda': 'BRL'
    }


@app.get('/pedidos')
async def listar_pedidos(
    mes: str = Query(..., description='Mes de referencia, no formato AAAA-MM'),
    chave: str = Header(..., alias='X-Lucra-Install-Key')
):
    verificar_chave(chave)
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', mes):
        raise HTTPException(status_code=422, detail='Mes invalido; use AAAA-MM')
    integracao = obter_integracao()
    pedidos = [pedido_formatado(p) for p in pedidos_validos(await consultar_pedidos(integracao))
               if pertence_ao_mes(p, mes)]
    pedidos.sort(key=lambda p: p['data'], reverse=True)
    total = sum((numero(p['valor_pago']) for p in pedidos), Decimal('0'))
    fretes = sum((numero(p['frete_checkout']) for p in pedidos), Decimal('0'))
    itens = sum(p['quantidade_itens'] for p in pedidos)
    qtd = len(pedidos)
    return {
        'sistema': 'LUCRA', 'loja': integracao['store_id'], 'mes': mes,
        'quantidade_pedidos': qtd,
        'faturamento': dinheiro(total),
        'ticket_medio': dinheiro(total / qtd) if qtd else 0,
        'itens_por_pedido': round(itens / qtd, 2) if qtd else 0,
        'frete_medio': dinheiro(fretes / qtd) if qtd else 0,
        'frete_total': dinheiro(fretes),
        'lucro_medio': None,
        'pedidos': pedidos,
        'aviso': 'Custos dos produtos e taxas ainda nao integrados. Lucro nao calculado.'
    }
