"""Item 4, fase 4c — a TROCA DE PRODUTO no pedido em "Aguardando Cancelamento" (07/10/2026).

`services/atendimento/troca.py` e as rotas de `routers/atendimento_troca.py`.
O Bling e a Shopee são FALSOS (nada sai daqui): o Bling falso registra cada
GET, PUT e PATCH e aplica o PUT no pedido dele, como o de verdade. O que
estes testes seguram (desenho §3.7 com as decisões do dono de 07/10):

- as chaves (desligada, piloto) e cada trava — do banco e ao vivo — recusam
  SEM escrever no Bling;
- a prévia não escreve nada;
- o caminho feliz: UM PUT (item novo sem `id`, mesmo valor, a linha "TROCA"
  nas Observações), o pino Aprovado ANTES do PATCH 6, 83955 → 9 → 6, o
  espelho, as trilhas, a NF de volta à fila (compare-and-set), a nota e o
  `concluida`; o GET do pedido é a última chamada antes do PUT;
- cada falha no meio deixa o estado certo, e o `retomar` segue — GET
  primeiro, só para frente, NUNCA PUT;
- a idempotência (`idem_key`, uma troca aberta por pedido) e a vez do retomar;
- os kits (sempre substituir; a peça dividida confere o saldo dela);
- o aceite: caixinha obrigatória nos níveis 1 e 2, a prova opcional
  (mensagem da conversa, Duoke ou declarado) e o nível 0 sem aceite;
- o modo AUTOMÁTICO do robô de lote (nível 0, sem pessoa, sem aprovar a
  Margem) atrás de `atendimento_troca_lote_auto` e do piloto;
- os robôs depois: a Margem não segura de novo e o sweep de NF pega;
- o Histórico atribui as mudanças a quem clicou e fica fora da tabela nova;
- o painel e a lista trazem a troca aberta; quem não vê a Margem não vê custo.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select, text, update

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    AtendimentoTroca,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    Logistica,
    MargemAudit,
    NfCommand,
    NfEtiquetaArquivo,
    NfFaturador,
    NfFaturamento,
    NfNota,
    Product,
    StoreInfo,
    UserRole,
)
from app.routers import atendimento as rota
from app.schemas.atendimento_painel import PainelOut
from app.security.cipher import decrypt_json, encrypt_json
from app.services import margem_auto_hold, nf_auto_enfileirar
from app.services.atendimento import gravar, painel, troca, troca_sugestoes
from app.services.marketplaces.bling import BlingCloudflareError

NUMERO = "300101"
BLING_ID = 77001
NUMEROLOJA = "SHPTROCA1"
LOJA = "7001"
AGORA = datetime.now(UTC)
URL = "/api/atendimento"
ERRO_MARCA = "Aguardando Cancelamento — saldo negativo: {}"

A17_BRANCO = "Uranyx A17 Pro Max 12.64 - Branco"
A17_LARANJA = "Uranyx A17 Pro Max 12.64 - Laranja"
# O `_cliente_shopee` de verdade (os cenários trocam pelo falso).
_CLIENTE_SHOPEE = troca._cliente_shopee


# ─────────────── os falsos ───────────────


def _http(status: int, corpo: str = "{}") -> httpx.HTTPStatusError:
    req = httpx.Request("PUT", f"https://api.bling.com.br/Api/v3/pedidos/vendas/{BLING_ID}")
    return httpx.HTTPStatusError(
        str(status), request=req, response=httpx.Response(status, request=req, text=corpo)
    )


class BlingFalso:
    """O Bling: registra cada chamada e APLICA o PUT e o PATCH no pedido dele.

    `falha_put`: None | "4xx" | "timeout" (não aplicou) | "timeout_aplica"
    (aplicou e não respondeu) | "cf" (429/503 depois das tentativas, não
    aplicou). `falha_get` = quantos GETs do pedido falham daqui em diante.
    `falha_patch` = situações cujo PATCH falha; `ja_estava` = situações que
    respondem "a venda possui a mesma situação" (False). `ao_patch` = um
    gancho chamado ANTES de aplicar o PATCH (confere o banco naquele instante).
    """

    def __init__(self, itens, *, situacao=83955, observacoes="obs antiga do pedido"):
        self.order = {
            "id": BLING_ID,
            "numero": NUMERO,
            "numeroLoja": NUMEROLOJA,
            "situacao": {"id": situacao, "valor": 0},
            "observacoes": observacoes,
            "contato": {"id": 1, "nome": "Cliente"},
            "loja": {"id": int(LOJA)},
            "notaFiscal": {"id": 0},
            "transporte": {"volumes": []},
            "total": 899,
            "itens": [
                {
                    "id": 7000 + i,
                    "codigo": sku,
                    "quantidade": qtd,
                    "valor": 899.0,
                    "descricao": f"Produto {sku}",
                    "produto": {"id": pid},
                }
                for i, (sku, qtd, pid) in enumerate(itens)
            ],
        }
        self.produtos: dict[str, dict] = {}
        self.saldos_por_id: dict[int, float] = {}
        self.pedidos_loja: list[dict] = []
        self.chamadas: list[tuple] = []
        self.falha_get = 0
        self.falha_put: str | None = None
        self.falha_patch: set[int] = set()
        self.ja_estava: set[int] = set()
        self.falha_produto = False
        self.ao_patch = None
        self.apos_put = None

    def produto(self, sku, pid, estoque, nome=None):
        self.produtos[sku.lower()] = {"id": pid, "sku": sku, "stock": estoque, "name": nome}

    def escritas(self):
        return [c for c in self.chamadas if c[0] in ("PUT", "PATCH")]

    def puts(self):
        return [c for c in self.chamadas if c[0] == "PUT"]

    def patches(self):
        return [c[1] for c in self.chamadas if c[0] == "PATCH"]

    async def get_order(self, bling_id):
        self.chamadas.append(("GET", bling_id))
        if self.falha_get:
            self.falha_get -= 1
            raise httpx.ReadTimeout("o Bling não respondeu")
        return copy.deepcopy(self.order)

    async def update_order(self, bling_id, body):
        self.chamadas.append(("PUT", copy.deepcopy(body)))
        if self.falha_put == "4xx":
            raise _http(
                400,
                '{"error":{"type":"VALIDATION_ERROR","fields":[{"code":67,'
                '"msg":"Estoque insuficiente do produto"}]}}',
            )
        aplica = self.falha_put in (None, "timeout_aplica")
        if aplica:
            self.order["itens"] = [
                {**it, "id": it.get("id") or 8000 + n} for n, it in enumerate(body["itens"])
            ]
            self.order["observacoes"] = body.get("observacoes")
            if self.apos_put is not None:
                self.apos_put(self.order)
        if self.falha_put in ("timeout", "timeout_aplica"):
            raise httpx.ReadTimeout("sem resposta")
        if self.falha_put == "cf":
            raise BlingCloudflareError("status=503 cf_html=False")
        return {}

    async def update_order_situacao(self, bling_id, situacao_id):
        self.chamadas.append(("PATCH", situacao_id))
        if self.ao_patch is not None:
            await self.ao_patch(situacao_id)
        if situacao_id in self.falha_patch:
            raise RuntimeError("bling fora do ar")
        if situacao_id in self.ja_estava or int(self.order["situacao"]["id"]) == situacao_id:
            self.order["situacao"]["id"] = situacao_id
            return False
        self.order["situacao"]["id"] = situacao_id
        return True

    async def find_active_product_by_sku(self, sku, *, estrito=False):
        self.chamadas.append(("PRODUTO", sku))
        if self.falha_produto:
            raise BlingCloudflareError("status=429 cf_html=False")
        p = self.produtos.get(sku.strip().lower())
        return dict(p) if p else None

    async def get_product(self, pid):
        self.chamadas.append(("GET_PRODUTO", pid))
        return {"estoque": {"saldoVirtualTotal": self.saldos_por_id.get(pid, 5)}}

    async def list_pedidos_vendas(self, **kw):
        self.chamadas.append(("LISTA", tuple(kw.get("numeros_lojas") or ())))
        return list(self.pedidos_loja)


class ShopeeFalsa:
    def __init__(self, status="READY_TO_SHIP", *, nf=None, falha=False, vazio=False):
        self.status, self.nf, self.falha, self.vazio = status, nf, falha, vazio
        self.chamadas: list[str] = []

    async def get_order_detail_completo(self, order_sn):
        self.chamadas.append(order_sn)
        if self.falha:
            raise RuntimeError("shopee_order_detail error_server: fora")
        if self.vazio:
            return {}
        return {
            "order_sn": order_sn,
            "order_status": self.status,
            "invoice_data": {"number": self.nf} if self.nf else {},
        }


# ─────────────── as chaves ───────────────


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_envio_ativo",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_simulador",
        "atendimento_troca_sugestoes_ativa",
        "atendimento_troca_lote_auto",
        "nf_auto_enfileirar",
        "nf_auto_ml_amazon",
        "estoque_familia_ativo",
        "estoque_familia_redireciona",
        "prioridade_substitui_item",
    ):
        monkeypatch.setattr(s, nome, False)
    monkeypatch.setattr(s, "atendimento_troca_ativa", True)
    monkeypatch.setattr(s, "atendimento_troca_pedidos", "")
    monkeypatch.setattr(s, "atendimento_troca_folga_prazo_horas", 2.0)
    monkeypatch.setattr(s, "atendimento_troca_aceite_max_dias", 7)
    monkeypatch.setattr(s, "atendimento_troca_teto_custo_pct", 5.0)
    monkeypatch.setattr(s, "atendimento_troca_piso_nivel2_pct", -10.0)
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "estoque_familia_prefixos", "")
    monkeypatch.setattr(troca, "ESPERAS_CONFERENCIA_S", (0.0, 0.0, 0.0))
    troca_sugestoes.limpar_memoria()
    yield s
    troca_sugestoes.limpar_memoria()


@pytest.fixture(autouse=True)
def _observacoes(monkeypatch):
    """As Observações do painel esquecidas (o passo 7) — sem Redis."""
    esquecidas: list = []

    async def _esquecer(bling_id):
        esquecidas.append(bling_id)

    monkeypatch.setattr(painel, "invalidar_observacoes", _esquecer)

    async def _sem_bling(session):
        return None

    # O painel (Observações) nunca fala com Bling nenhum aqui.
    monkeypatch.setattr(painel, "_cliente_bling", _sem_bling)
    return esquecidas


def _ligar(monkeypatch, bling: BlingFalso | None, shopee: ShopeeFalsa | None = None):
    shopee = shopee if shopee is not None else ShopeeFalsa()

    async def _b(session):
        return bling

    async def _s(session, p):
        return shopee

    monkeypatch.setattr(troca, "_cliente_bling", _b)
    monkeypatch.setattr(troca, "_cliente_shopee", _s)
    return shopee


# ─────────────── fábrica ───────────────


async def _loja(db, dono, *, plataforma="shopee", faturador_id=None):
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name="kfa",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo="observar", status="ok"
    )
    db.add(canal)
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform=plataforma,
            account_name="kfa",
            bling_store_id=LOJA,
            integration_id=integ.id,
            nf_faturador_id=faturador_id,
        )
    )
    await db.commit()
    return integ, canal


async def _pedido(
    db, itens=(("dg053.sp", 1, 501),), *, situacao="83955", prazo_horas=20, status=None
):
    for i, (sku, qtd, pid) in enumerate(itens):
        db.add(
            BlingOrder(
                bling_id=BLING_ID,
                item_index=i,
                numero=NUMERO,
                numeroloja=NUMEROLOJA,
                situacao=situacao,
                loja=LOJA,
                item_codigo=sku,
                item_produto_id=pid,
                item_descricao=f"Produto {sku}",
                item_quantidade=qtd,
                itemvalor=899,
                status=status,
                data=AGORA - timedelta(days=1),
                marketplace_ship_deadline=(
                    AGORA + timedelta(hours=prazo_horas) if prazo_horas is not None else None
                ),
            )
        )
    await db.commit()


async def _marca(db, skus=("dg053.sp",), *, status="sem_estoque", horas=3):
    quando = AGORA - timedelta(hours=horas)
    db.add(
        NfFaturamento(
            pedido_bling=NUMERO,
            status_faturamento=status,
            erro_faturamento=ERRO_MARCA.format(", ".join(skus)),
            created_at=quando,
            updated_at=quando,
        )
    )
    await db.commit()


async def _produtos(db, dono, linhas):
    """(sku, nome, estoque no DaVinci, custo, id no Bling, formato)."""
    for sku, nome, estoque, custo, pid, formato in linhas:
        db.add(
            Product(
                user_id=dono.id,
                sku=sku,
                name=nome,
                stock=estoque,
                formato=formato,
                situacao="A",
                bling_cost_price=Decimal(str(custo)),
                bling_product_id=pid,
            )
        )
    await db.commit()


async def _mensagem(db, conversa, autor, texto, minutos_atras, *, origem=None):
    m = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=f"m-{uuid4().hex[:10]}",
        autor=autor,
        origem=origem or ("cliente" if autor == "cliente" else "externo"),
        tipo="texto",
        texto=texto,
        enviada_em=AGORA - timedelta(minutes=minutos_atras),
        status="recebida",
        anexos=[],
        payload={},
    )
    db.add(m)
    await db.commit()
    return m


async def _snapshot(db, *, status=None, situacao="83955", margem=0.05):
    await db.execute(
        text(
            "INSERT INTO verificar_margem (bling_order_item_id, pedido_bling, bling_id, sku, "
            "situacao, situacao_nome, plataforma_bling, loja_nome, item_proportion, "
            "bling_status_margem, marketplace_margem, margem_minima, "
            "marketplace_liquido_base_margem_item) VALUES (:id, :p, :b, 'dg053.sp', :sit, "
            "'x', 'shopee', 'kfa', 1, :st, :m, 0.10, 100)"
        ),
        {
            "id": str(uuid.uuid4()),
            "p": NUMERO,
            "b": BLING_ID,
            "sit": situacao,
            "st": status,
            "m": margem,
        },
    )
    await db.commit()


async def _cenario(db, make_user, monkeypatch, *, nivel0=False, kit=False, faturador=False):
    """O pedido 300101 em 83955 por falta de estoque, com conversa e o Bling/Shopee falsos.

    Padrão: dg053.sp (Branco, sem estoque) → dg054.sp (Laranja, nível 1).
    `nivel0`: dg053.ci → dg053.sp (o mesmo produto, com a soma por família ligada).
    `kit`: dg057.ci+a001.ci → dg057.sp+a001.sp (kit, nível 0).
    """
    dono = await make_user()
    adm = await make_user(role=UserRole.ADMIN, email=f"adm-{uuid4().hex[:6]}@davinci-test.com")
    adm.name = "Fulana Atendimento"
    await db.commit()
    fat_id = None
    if faturador:
        fat = NfFaturador(nome="upseller", modo="upseller")
        db.add(fat)
        await db.commit()
        fat_id = fat.id
    integ, canal = await _loja(db, dono, faturador_id=fat_id)
    if nivel0 or kit:
        s = get_settings()
        monkeypatch.setattr(s, "estoque_familia_ativo", True)
        monkeypatch.setattr(s, "estoque_familia_redireciona", True)
    if kit:
        antigo, novo = "dg057.ci+a001.ci", "dg057.sp+a001.sp"
        await _produtos(
            db,
            dono,
            [
                (antigo, "Kit Uranyx C57 + Fone", 0, 600, 601, "E"),
                (novo, "Kit Uranyx C57 + Fone", 4, 600, 602, "E"),
            ],
        )
        itens = ((antigo, 1, 601),)
    elif nivel0:
        antigo, novo = "dg053.ci", "dg053.sp"
        await _produtos(
            db,
            dono,
            [
                (antigo, A17_BRANCO, 0, 495, 511, "S"),
                (novo, A17_BRANCO, 7, 495, 512, "S"),
            ],
        )
        itens = ((antigo, 1, 511),)
    else:
        antigo, novo = "dg053.sp", "dg054.sp"
        await _produtos(
            db,
            dono,
            [
                (antigo, A17_BRANCO, 0, 495, 501, "S"),
                (novo, A17_LARANJA, 9, 495, 502, "S"),
            ],
        )
        itens = ((antigo, 1, 501),)
    await _pedido(db, itens)
    await _marca(db, (antigo,))
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="conv-troca",
        pedido_marketplace=NUMEROLOJA,
        comprador_nome="Comprador Fictício",
    )
    await db.commit()
    bling = BlingFalso(itens)
    bling.produto(novo, 502 if not (nivel0 or kit) else (512 if nivel0 else 602), 9, f"Novo {novo}")
    shopee = _ligar(monkeypatch, bling, ShopeeFalsa())
    return SimpleNamespace(
        dono=dono,
        adm=adm,
        integ=integ,
        canal=canal,
        conversa=conversa,
        bling=bling,
        shopee=shopee,
        antigo=antigo,
        novo=novo,
    )


async def _aceite_na_conversa(db, c):
    """A oferta da loja (pelo Duoke) e o "pode mandar" do cliente, depois da marca."""
    await _mensagem(db, c.conversa, "loja", "Podemos trocar pela Laranja?", 50)
    return await _mensagem(db, c.conversa, "cliente", "pode mandar a laranja", 40)


async def _previa(db, c, user=None):
    return await troca.previa(
        db, user or c.adm, numero_bling=NUMERO, sku_antigo=c.antigo, sku_novo=c.novo
    )


async def _trocar(db, c, *, user=None, previa_hash=None, confirmar=True, aceite=None, idem=None):
    if previa_hash is None:
        previa_hash = (await _previa(db, c, user))["previa_hash"]
    c.bling.chamadas.clear()
    return await troca.executar(
        db,
        user or c.adm,
        numero_bling=NUMERO,
        sku_antigo=c.antigo,
        sku_novo=c.novo,
        idem_key=idem or uuid4(),
        aceite=aceite,
        confirmar=confirmar,
        previa_hash=previa_hash,
    )


async def _linhas_bo(db):
    # `populate_existing`: relê o que outro passo (ou outra sessão) gravou, sem
    # expirar o resto da sessão (o usuário de `c.adm` continua carregado).
    return (
        (
            await db.execute(
                select(BlingOrder)
                .where(BlingOrder.numero == NUMERO)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _trilhas(db):
    return [
        (a.acao, a.valor_antigo, a.valor_novo, a.origem, a.mudado_por)
        for a in (
            await db.execute(
                select(MargemAudit)
                .where(MargemAudit.pedido_bling == NUMERO)
                # O mesmo passo grava as trilhas na mesma transação (o mesmo now()).
                .order_by(MargemAudit.created_at, MargemAudit.acao)
            )
        )
        .scalars()
        .all()
    ]


async def _trocas(db):
    return (
        (await db.execute(select(AtendimentoTroca).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )


# ─────────────── as chaves ───────────────


async def test_desligada_recusa(db, make_user, monkeypatch, _chaves):
    c = await _cenario(db, make_user, monkeypatch)
    monkeypatch.setattr(_chaves, "atendimento_troca_ativa", False)
    for chamar in (
        lambda: _previa(db, c),
        lambda: _trocar(db, c, previa_hash="x" * 32),
    ):
        with pytest.raises(troca.TrocaRecusada) as e:
            await chamar()
        assert e.value.code == "troca_desligada"
    assert c.bling.chamadas == [] and c.shopee.chamadas == []
    assert await _trocas(db) == []


async def test_desligar_freia_o_retomar(db, make_user, monkeypatch, _chaves):
    """A chave geral é o freio de tudo: a troca parada no meio também não anda."""
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {9}
    t = await _trocar(db, c)
    assert t.estado == "item_trocado"
    monkeypatch.setattr(_chaves, "atendimento_troca_ativa", False)
    c.bling.chamadas.clear()
    with pytest.raises(troca.TrocaRecusada) as e:
        await troca.retomar(db, c.adm, t.id)
    assert e.value.code == "troca_desligada"
    assert c.bling.chamadas == []
    # Religada, segue de onde parou.
    monkeypatch.setattr(_chaves, "atendimento_troca_ativa", True)
    c.bling.falha_patch = set()
    t = await troca.retomar(db, c.adm, t.id)
    assert t.estado == "concluida" and c.bling.patches() == [9, 6]


async def test_fora_do_piloto_recusa(db, make_user, monkeypatch, _chaves):
    c = await _cenario(db, make_user, monkeypatch)
    monkeypatch.setattr(_chaves, "atendimento_troca_pedidos", "299999, 300000")
    with pytest.raises(troca.TrocaRecusada) as e:
        await _previa(db, c)
    assert e.value.code == "pedido_fora_do_piloto"
    # Na lista piloto, passa.
    monkeypatch.setattr(_chaves, "atendimento_troca_pedidos", f"299999,{NUMERO}")
    assert (await _previa(db, c))["pode"] is True


# ─────────────── a prévia ───────────────


async def test_previa_nao_escreve(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    msg = await _aceite_na_conversa(db, c)
    pv = await _previa(db, c)
    assert pv["pode"] is True, pv["travas"]
    assert pv["ao_vivo"] is True
    assert (pv["nivel"], pv["exige_aceite"], pv["mesmo_produto"]) == (1, True, False)
    assert pv["previa_hash"] and len(pv["previa_hash"]) == 32
    assert pv["antes"]["sku"] == "dg053.sp" and pv["depois"]["sku"] == "dg054.sp"
    assert pv["depois"]["saldo_ao_vivo"] == 9.0 and pv["depois"]["produto_id"] == 502
    assert pv["valor_unitario"] == 899.0
    # Admin vê a Margem: o custo e o %.
    assert (pv["antes"]["custo"], pv["depois"]["custo"], pv["dif_custo_pct"]) == (495.0, 495.0, 0.0)
    assert pv["observacao"].split(" - ", 1)[1].startswith("TROCA dg053.sp -> dg054.sp")
    assert pv["passos_previstos"] == [
        "PUT itens + observação no Bling",
        "83955 → 9 → 6",
        "Margem Aprovado por Fulana Atendimento",
        "NF volta à fila",
    ]
    assert [(a["id"], a["depois_da_oferta"]) for a in pv["aceites_possiveis"]] == [
        (str(msg.id), True)
    ]
    assert pv["aviso_nf"] and "aba NF" in pv["aviso_nf"]
    assert all(t["ok"] for t in pv["travas"])
    # Só GETs: nada escrito no Bling nem no banco.
    assert c.bling.escritas() == []
    assert await _trocas(db) == []
    assert await _trilhas(db) == []
    assert {r.status for r in await _linhas_bo(db)} == {None}


# ─────────────── as travas do banco ───────────────


async def _trava_margem(db, c):
    db.add(
        MargemAudit(
            pedido_bling=NUMERO,
            acao="situacao",
            valor_antigo="6",
            valor_novo="83955",
            origem="margens_auto",
            mudado_por=None,
            created_at=AGORA - timedelta(hours=4),
        )
    )
    await db.execute(
        update(BlingOrder).where(BlingOrder.numero == NUMERO).values(status="Pendente")
    )
    await db.commit()


async def _nf_nota(db, c):
    db.add(NfNota(chave="3".zfill(44), pedido_bling=NUMERO, numero="4270", xml=b"<nfe/>"))
    await db.commit()


async def _fila(db, c, *, status="pending", attempts=0, completed=None, action="import_avulsa"):
    db.add(
        NfCommand(
            action=action,
            numeros=[NUMERO],
            planilha=b"x",
            nome_arquivo="a.csv",
            status=status,
            attempts=attempts,
            completed_at=completed,
            source="auto",
        )
    )
    await db.commit()


async def _comando_falho(db, c):
    await _fila(db, c, status="failed", attempts=1, completed=AGORA - timedelta(hours=1))


async def _emissao_na_fila(db, c):
    # A emissão que vem depois da planilha (a etapa inteira do faturamento conta).
    await _fila(db, c, action="emitir_nf_upseller")


async def _etiqueta(db, c):
    db.add(
        NfEtiquetaArquivo(
            pedido_bling=NUMERO, filename="e.pdf", content_type="application/pdf", blob=b"%PDF"
        )
    )
    await db.commit()


async def _rastreio(db, c):
    db.add(Logistica(pedido_bling=NUMERO, rastreio="BR123456789"))
    await db.commit()


async def _saiu(db, c):
    db.add(
        MargemAudit(
            pedido_bling=NUMERO,
            acao="situacao",
            valor_antigo="21",
            valor_novo="15",
            origem="shipment_check",
            created_at=AGORA - timedelta(days=3),
        )
    )
    await db.commit()


async def _dobro(db, c):
    db.add(
        BlingOrder(
            bling_id=BLING_ID + 1,
            item_index=0,
            numero="300999",
            numeroloja=NUMEROLOJA,
            situacao="6",
            loja=LOJA,
            item_codigo=c.antigo,
        )
    )
    await db.commit()


async def _prazo(db, c):
    await db.execute(
        update(BlingOrder)
        .where(BlingOrder.numero == NUMERO)
        .values(marketplace_ship_deadline=AGORA + timedelta(minutes=30))
    )
    await db.commit()


async def _plataforma_ml(db, c):
    await db.execute(
        update(StoreInfo).where(StoreInfo.bling_store_id == LOJA).values(platform="ml")
    )
    await db.commit()


async def _restricao(db, c):
    await db.execute(
        update(NfFaturamento)
        .where(NfFaturamento.pedido_bling == NUMERO)
        .values(status_faturamento="restricao", erro_faturamento="não envia pro RJ")
    )
    await db.commit()


async def _dois_em_falta(db, c):
    db.add(
        BlingOrder(
            bling_id=BLING_ID,
            item_index=1,
            numero=NUMERO,
            numeroloja=NUMEROLOJA,
            situacao="83955",
            loja=LOJA,
            item_codigo="a001.sp",
            item_quantidade=1,
        )
    )
    await db.execute(
        update(NfFaturamento)
        .where(NfFaturamento.pedido_bling == NUMERO)
        .values(erro_faturamento=ERRO_MARCA.format("dg053.sp, a001.sp"))
    )
    await db.commit()


@pytest.mark.parametrize(
    ("preparo", "code", "sku_novo"),
    [
        (_trava_margem, "motivo_nao_permite", None),
        (_restricao, "motivo_nao_permite", None),
        (_plataforma_ml, "plataforma_sem_conferencia", None),
        (_nf_nota, "nf_emitida", None),
        (_fila, "em_fila_nf", None),
        (_comando_falho, "em_fila_nf", None),
        (_emissao_na_fila, "em_fila_nf", None),
        (_etiqueta, "etiqueta_gerada", None),
        (_rastreio, "rastreio", None),
        (_saiu, "pedido_saiu", None),
        (_dobro, "pedido_em_dobro", None),
        (_prazo, "prazo_vencido", None),
        (_dois_em_falta, "outro_item_sem_estoque", None),
        (None, "sugestao_invalida", "i223.sa"),
        (None, "kit_lotes_misturados", "dg054.sp+a001.ci"),
    ],
)
async def test_trava_do_banco_recusa_sem_escrever(
    db, make_user, monkeypatch, preparo, code, sku_novo
):
    c = await _cenario(db, make_user, monkeypatch)
    if preparo is not None:
        await preparo(db, c)
    if sku_novo is not None:
        c.novo = sku_novo
    pv = await _previa(db, c)
    assert pv["pode"] is False and pv["ao_vivo"] is False
    falhas = [t["code"] for t in pv["travas"] if not t["ok"]]
    assert code in falhas, pv["travas"]
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c, previa_hash="x" * 32)
    # O clique recusa pela 1ª trava (com NF ou etiqueta, o motivo já vira
    # "movido à mão" e a trava do motivo vem antes).
    assert e.value.code == falhas[0]
    # Nada no Bling (nem GET: a conferência ao vivo nem começa) e nenhuma linha.
    assert c.bling.chamadas == []
    assert await _trocas(db) == []


async def test_trava_custo_acima(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    await db.execute(
        update(Product).where(Product.sku == "dg054.sp").values(bling_cost_price=Decimal("560"))
    )
    await db.commit()
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c, previa_hash="x" * 32)
    assert e.value.code == "custo_acima"
    assert c.bling.chamadas == []


async def test_trava_custo_abaixo_do_piso(db, make_user, monkeypatch):
    """O nível 2 bem mais barato (o piso de −10% da 4b) recusa sem escrever."""
    c = await _cenario(db, make_user, monkeypatch)
    abaixo = troca_sugestoes.Sugestao(
        sku=c.novo,
        nome=A17_LARANJA,
        nivel=troca_sugestoes.NIVEL_ESPECIFICACAO,
        estoque=9,
        estoque_em=None,
        produto_id=502,
        dif_custo_pct=-12.0,
        motivo_fora=troca_sugestoes.FORA_CUSTO_ABAIXO_PISO,
    )
    monkeypatch.setattr(troca_sugestoes, "candidatos", lambda *a, **kw: [abaixo])
    pv = await _previa(db, c)
    assert pv["pode"] is False and pv["ao_vivo"] is False
    assert "custo_abaixo_piso" in [t["code"] for t in pv["travas"] if not t["ok"]]
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c, previa_hash="x" * 32)
    assert e.value.code == "custo_abaixo_piso"
    assert c.bling.chamadas == [] and await _trocas(db) == []


# ─────────────── as travas ao vivo ───────────────


def _situacao_6(c):
    c.bling.order["situacao"]["id"] = 6


def _nf_no_bling(c):
    c.bling.order["notaFiscal"] = {"id": 123}


def _rastreio_no_bling(c):
    c.bling.order["transporte"] = {"volumes": [{"id": 1, "codigoRastreamento": "BR1"}]}


def _itens_mudaram(c):
    c.bling.order["itens"][0]["quantidade"] = 2


def _sem_produto(c):
    c.bling.produtos.clear()


def _sem_saldo(c):
    c.bling.produtos[c.novo.lower()]["stock"] = 0


def _cancelado(c):
    c.shopee.status = "IN_CANCEL"


def _etiqueta_shopee(c):
    c.shopee.status = "PROCESSED"


def _shopee_vazia(c):
    c.shopee.vazio = True


def _nf_shopee(c):
    c.shopee.nf = "4270"


def _dobro_no_bling(c):
    c.bling.pedidos_loja = [
        {"id": 1, "numero": NUMERO, "loja": {"id": int(LOJA)}, "situacao": {"id": 83955}},
        {"id": 2, "numero": "300999", "loja": {"id": int(LOJA)}, "situacao": {"id": 6}},
        # De outra loja ou cancelado: não conta.
        {"id": 3, "numero": "300998", "loja": {"id": 99}, "situacao": {"id": 6}},
        {"id": 4, "numero": "300997", "loja": {"id": int(LOJA)}, "situacao": {"id": 12}},
    ]


def _bling_429(c):
    c.bling.falha_produto = True


@pytest.mark.parametrize(
    ("estragar", "code"),
    [
        (_situacao_6, "situacao_mudou"),
        (_nf_no_bling, "nf_emitida"),
        (_rastreio_no_bling, "rastreio"),
        (_itens_mudaram, "item_divergente"),
        (_sem_produto, "produto_novo_inativo"),
        (_sem_saldo, "saldo_insuficiente"),
        (_cancelado, "cancelado_na_plataforma"),
        (_etiqueta_shopee, "plataforma_status"),
        (_shopee_vazia, "plataforma_indisponivel"),
        (_nf_shopee, "nf_emitida"),
        (_dobro_no_bling, "pedido_em_dobro"),
        (_bling_429, "bling_indisponivel"),
    ],
)
async def test_trava_ao_vivo_recusa_sem_escrever(db, make_user, monkeypatch, estragar, code):
    c = await _cenario(db, make_user, monkeypatch)
    pv = await _previa(db, c)
    assert pv["pode"] is True
    estragar(c)
    pv2 = await _previa(db, c)
    assert pv2["pode"] is False and pv2["previa_hash"] is None
    assert code in [t["code"] for t in pv2["travas"] if not t["ok"]], pv2["travas"]
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c, previa_hash=pv["previa_hash"])
    assert e.value.code == code
    assert e.value.troca_id is not None
    assert c.bling.escritas() == []
    [t] = await _trocas(db)
    assert (t.estado, t.codigo_erro) == ("abortada", code)
    assert t.passos[-1]["passo"] == "abortada"


async def test_outro_item_negativo_no_bling_recusa(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    db.add(
        BlingOrder(
            bling_id=BLING_ID,
            item_index=1,
            numero=NUMERO,
            numeroloja=NUMEROLOJA,
            situacao="83955",
            loja=LOJA,
            item_codigo="a001.sp",
            item_produto_id=900,
            item_quantidade=1,
        )
    )
    await db.commit()
    c.bling.order["itens"].append(
        {"id": 7009, "codigo": "a001.sp", "quantidade": 1, "valor": 0, "produto": {"id": 900}}
    )
    c.bling.saldos_por_id[900] = -1
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c, previa_hash="x" * 32)
    assert e.value.code == "outro_item_sem_estoque"
    assert c.bling.escritas() == []


# ─────────────── o caminho feliz ───────────────


async def test_caminho_feliz(db, client, auth_as, make_user, monkeypatch, _observacoes):
    c = await _cenario(db, make_user, monkeypatch)
    await _snapshot(db)
    msg = await _aceite_na_conversa(db, c)
    auth_as(c.adm)
    r = await client.post(
        f"{URL}/pedidos/{NUMERO}/troca/previa",
        json={"sku_antigo": c.antigo, "sku_novo": c.novo},
    )
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["pode"] is True
    c.bling.chamadas.clear()

    r = await client.post(
        f"{URL}/pedidos/{NUMERO}/troca",
        json={
            "sku_antigo": c.antigo,
            "sku_novo": c.novo,
            "previa_hash": pv["previa_hash"],
            "idem_key": str(uuid4()),
            "confirmar": True,
            "aceite": {"mensagem_aceite_id": str(msg.id)},
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["estado"] == "concluida" and out["aberta"] is False
    assert (out["aceite_fonte"], out["mensagem_aceite_id"]) == ("davinci", str(msg.id))
    assert out["aceite_texto"] == "pode mandar a laranja"
    assert (out["custo_antigo"], out["custo_novo"]) == (495.0, 495.0)
    assert [p["passo"] for p in out["passos"]] == [
        "iniciada",
        "conferencia",
        "put",
        "item_trocado",
        "situacao_9",
        "situacao_6",
        "nf_liberada",
        "concluida",
    ]

    # UM PUT, e o GET do pedido é a última chamada antes dele (crítica M3).
    [put] = c.bling.puts()
    i_put = c.bling.chamadas.index(put)
    assert c.bling.chamadas[i_put - 1][0] == "GET"
    body = put[1]
    [item] = body["itens"]
    assert "id" not in item  # SUBSTITUI: o Bling refaz a composição
    assert (item["codigo"], item["produto"], item["valor"], item["quantidade"]) == (
        "dg054.sp",
        {"id": 502},
        899.0,
        1,
    )
    hoje = datetime.now().strftime("%d/%m")
    linha, resto = body["observacoes"].split("\n", 1)
    assert linha.startswith(f"{hoje} - TROCA dg053.sp -> dg054.sp (aceite em ")
    assert linha.endswith(", davinci) - Atendimento/Fulana Atendimento")
    assert resto == "obs antiga do pedido"
    assert body["situacao"] == {"id": 83955}
    # 83955 → 9 → 6, nessa ordem.
    assert c.bling.patches() == [9, 6]

    # O espelho, o pino e as trilhas.
    [bo] = await _linhas_bo(db)
    assert (bo.item_codigo, bo.item_produto_id, bo.situacao) == ("dg054.sp", 502, "6")
    assert (bo.status, bo.aprovado_por, bo.verificado) == ("Aprovado", c.adm.id, True)
    assert bo.preco_custo == 495.0
    assert await _trilhas(db) == [
        ("sku", "dg053.sp", "dg054.sp", "atendimento_troca", c.adm.id),
        ("status", None, "Aprovado", "atendimento_troca", c.adm.id),
        ("situacao", "83955", "6", "atendimento_troca", c.adm.id),
    ]
    snap = (
        await db.execute(
            text(
                "SELECT situacao, bling_status_margem FROM verificar_margem WHERE pedido_bling=:p"
            ),
            {"p": NUMERO},
        )
    ).one()
    assert tuple(snap) == ("6", "Aprovado")
    # A NF de volta à fila; a marca antiga fica guardada na troca.
    nf = (
        await db.execute(select(NfFaturamento).where(NfFaturamento.pedido_bling == NUMERO))
    ).scalar_one()
    await db.refresh(nf)
    assert (nf.status_faturamento, nf.erro_faturamento) == (None, None)
    [t] = await _trocas(db)
    assert (t.nf_status_anterior, t.nf_erro_anterior) == (
        "sem_estoque",
        ERRO_MARCA.format("dg053.sp"),
    )
    assert t.concluida_em is not None and t.em_execucao_ate is None
    # A nota na conversa e as Observações do painel esquecidas.
    notas = (
        (
            await db.execute(
                select(AtendimentoMensagem).where(
                    AtendimentoMensagem.conversa_id == c.conversa.id,
                    AtendimentoMensagem.tipo == "nota",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notas) == 1 and "dg053.sp → dg054.sp" in notas[0].texto
    assert "Fulana Atendimento" in notas[0].texto and notas[0].autor_user_id == c.adm.id
    assert _observacoes == [BLING_ID]


async def test_pino_aprovado_antes_do_patch_6(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    vistos: dict[int, tuple] = {}

    async def _confere(sit):
        async with _db.SessionLocal() as s2:
            vistos[sit] = tuple(
                (
                    await s2.execute(
                        select(BlingOrder.status, BlingOrder.item_codigo).where(
                            BlingOrder.numero == NUMERO
                        )
                    )
                ).one()
            )

    c.bling.ao_patch = _confere
    t = await _trocar(db, c)
    assert t.estado == "concluida"
    # No instante do PATCH 6 o pino Aprovado (e o item novo) já estavam no banco.
    assert vistos[6] == ("Aprovado", "dg054.sp")
    assert vistos[9] == ("Aprovado", "dg054.sp")


# ─────────────── falhas no PUT ───────────────


async def test_put_4xx_aborta_sem_efeito(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_put = "4xx"
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c)
    assert e.value.code == "bling_recusou"
    assert "Estoque insuficiente do produto" in e.value.detail
    assert c.bling.patches() == [] and len(c.bling.puts()) == 1
    [t] = await _trocas(db)
    assert t.estado == "abortada"
    [bo] = await _linhas_bo(db)
    assert (bo.item_codigo, bo.status) == ("dg053.sp", None)
    assert await _trilhas(db) == []


async def test_put_timeout_fica_incerta(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_put = "timeout"
    original = c.bling.update_order

    async def _put(bling_id, body):
        c.bling.falha_get = 3  # os GETs de conferência também falham
        return await original(bling_id, body)

    c.bling.update_order = _put
    t = await _trocar(db, c)
    assert (t.estado, t.codigo_erro) == ("incerta", "bling_indisponivel")
    assert t.em_execucao_ate is None
    assert len(c.bling.puts()) == 1 and c.bling.patches() == []
    [bo] = await _linhas_bo(db)
    assert bo.item_codigo == "dg053.sp"  # nada registrado sem saber


async def test_put_cloudflare_conferido_antigo_aborta(db, make_user, monkeypatch):
    """503/429 depois das tentativas não é "nada mudou" sem conferir (crítica M4)."""
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_put = "cf"
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c)
    assert e.value.code == "bling_indisponivel"
    # Os 3 GETs espaçados viram o item antigo.
    gets_depois = [
        x for x in c.bling.chamadas[c.bling.chamadas.index(c.bling.puts()[0]) :] if x[0] == "GET"
    ]
    assert len(gets_depois) == 3
    [t] = await _trocas(db)
    assert t.estado == "abortada"


async def test_put_timeout_conferido_novo_segue(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_put = "timeout_aplica"
    t = await _trocar(db, c)
    assert t.estado == "concluida"
    assert len(c.bling.puts()) == 1 and c.bling.patches() == [9, 6]


async def _incerta(db, c, *, aplica: bool):
    c.bling.falha_put = "timeout_aplica" if aplica else "timeout"
    original = c.bling.update_order

    async def _put(bling_id, body):
        c.bling.falha_get = 3
        return await original(bling_id, body)

    c.bling.update_order = _put
    t = await _trocar(db, c)
    assert t.estado == "incerta"
    c.bling.falha_put = None
    c.bling.update_order = original
    return t


async def test_retomar_incerta_item_novo_continua(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    t = await _incerta(db, c, aplica=True)
    t = await troca.retomar(db, c.adm, t.id)
    assert t.estado == "concluida"
    assert len(c.bling.puts()) == 1  # o retomar NUNCA faz PUT
    assert c.bling.patches() == [9, 6]
    [bo] = await _linhas_bo(db)
    assert (bo.item_codigo, bo.status, bo.situacao) == ("dg054.sp", "Aprovado", "6")


async def test_retomar_incerta_item_antigo_aborta(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    t = await _incerta(db, c, aplica=False)
    t = await troca.retomar(db, c.adm, t.id)
    assert (t.estado, t.codigo_erro) == ("abortada", "put_nao_aplicado")
    assert len(c.bling.puts()) == 1 and c.bling.patches() == []
    # Abortada, a troca não prende mais o pedido.
    assert await troca.troca_aberta_do_pedido(db, NUMERO) is None


# ─────────────── falhas na situação ───────────────


async def test_patch9_falha_e_retomar_continua(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {9}
    t = await _trocar(db, c)
    assert (t.estado, t.codigo_erro) == ("item_trocado", "bling_indisponivel")
    assert t.em_execucao_ate is None
    resumo = await troca.troca_aberta_do_pedido(db, NUMERO)
    assert resumo["estado"] == "item_trocado" and resumo["pode_retomar"] is True
    c.bling.falha_patch = set()
    t = await troca.retomar(db, c.adm, t.id)
    assert t.estado == "concluida"
    assert c.bling.patches() == [9, 9, 6]
    assert len(c.bling.puts()) == 1


async def test_patch6_falha_e_retomar_pula_o_9(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {6}
    t = await _trocar(db, c)
    assert t.estado == "em_atendido"
    c.bling.falha_patch = set()
    c.bling.chamadas.clear()
    t = await troca.retomar(db, c.adm, t.id)
    assert t.estado == "concluida"
    # Em 9 (Atendido), o retomar só faz o PATCH 6 — e nunca PUT.
    assert c.bling.patches() == [6]
    assert c.bling.puts() == []


async def test_ja_estava_conta_como_ok(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.ja_estava = {9}
    t = await _trocar(db, c)
    assert t.estado == "concluida"
    assert c.bling.patches() == [9, 6]


async def test_situacao_mudou_no_meio_aborta(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)

    def _cancelaram(order):
        order["situacao"]["id"] = 12

    c.bling.apos_put = _cancelaram
    t = await _trocar(db, c)
    assert (t.estado, t.codigo_erro) == ("abortada", "situacao_mudou_no_meio")
    assert c.bling.patches() == []
    # O item já é o novo no espelho; a nota avisa a pessoa.
    [bo] = await _linhas_bo(db)
    assert bo.item_codigo == "dg054.sp"
    notas = (
        (
            await db.execute(
                select(AtendimentoMensagem.texto).where(AtendimentoMensagem.tipo == "nota")
            )
        )
        .scalars()
        .all()
    )
    assert any("situação 12" in n for n in notas)


async def test_retomar_nunca_faz_put_depois_de_patch(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {6}
    t = await _trocar(db, c)
    assert t.estado == "em_atendido"
    puts_antes = len(c.bling.puts())
    c.bling.falha_patch = {6}
    t = await troca.retomar(db, c.adm, t.id)  # falha de novo
    assert t.estado == "em_atendido"
    c.bling.falha_patch = set()
    t = await troca.retomar(db, c.adm, t.id)
    assert t.estado == "concluida"
    assert len(c.bling.puts()) == puts_antes == 1


async def test_retomar_respeita_a_vez(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {9}
    t = await _trocar(db, c)
    await db.execute(
        update(AtendimentoTroca)
        .where(AtendimentoTroca.id == t.id)
        .values(em_execucao_ate=datetime.now(UTC) + timedelta(minutes=3))
    )
    await db.commit()
    with pytest.raises(troca.TrocaRecusada) as e:
        await troca.retomar(db, c.adm, t.id)
    assert e.value.code == "troca_em_andamento"


async def test_retomar_confere_a_shopee_antes_de_mover(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {9}
    t = await _trocar(db, c)
    c.bling.falha_patch = set()
    c.shopee.status = "IN_CANCEL"
    t = await troca.retomar(db, c.adm, t.id)
    assert (t.estado, t.codigo_erro) == ("abortada", "cancelado_na_plataforma")
    assert c.bling.patches() == [9]  # só a tentativa que falhou antes


# ─────────────── a NF (compare-and-set) ───────────────


async def test_nf_que_seguiu_por_outro_caminho_nao_e_zerada(db, make_user, monkeypatch):
    """Alguém enfileirou a NF entre o PATCH 6 e o passo 6 (crítica A3)."""
    c = await _cenario(db, make_user, monkeypatch)

    async def _enfileiraram(sit):
        if sit == 6:
            async with _db.SessionLocal() as s2:
                await s2.execute(
                    update(NfFaturamento)
                    .where(NfFaturamento.pedido_bling == NUMERO)
                    .values(status_faturamento="processando", erro_faturamento=None)
                )
                await s2.commit()

    c.bling.ao_patch = _enfileiraram
    t = await _trocar(db, c)
    assert t.estado == "concluida"
    nf = (
        await db.execute(select(NfFaturamento).where(NfFaturamento.pedido_bling == NUMERO))
    ).scalar_one()
    await db.refresh(nf)
    assert nf.status_faturamento == "processando"
    passo = next(p for p in t.passos if p["passo"] == "nf_liberada")
    assert "outro caminho" in passo["detalhe"]


# ─────────────── idempotência ───────────────


async def test_idem_key_nao_repete(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    chave = uuid4()
    pv = await _previa(db, c)
    t1 = await _trocar(db, c, previa_hash=pv["previa_hash"], idem=chave)
    assert t1.estado == "concluida"
    c.bling.chamadas.clear()
    t2 = await _trocar(db, c, previa_hash=pv["previa_hash"], idem=chave)
    assert t2.id == t1.id
    assert c.bling.chamadas == []
    assert len(await _trocas(db)) == 1


async def test_duas_trocas_abertas_409(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {9}
    t = await _trocar(db, c)
    assert t.estado == "item_trocado"
    pv = await _previa(db, c)
    assert pv["pode"] is False and pv["troca_aberta"]["id"] == str(t.id)
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c, previa_hash="x" * 32)
    # A troca aberta prende o pedido (antes até do motivo, que já envelheceu).
    assert (e.value.code, e.value.troca_id) == ("troca_em_andamento", t.id)
    assert len(await _trocas(db)) == 1


async def test_indice_unico_segura_a_corrida(db, make_user, monkeypatch):
    await _cenario(db, make_user, monkeypatch)
    for estado in ("concluida", "abortada", "abortada"):
        db.add(_troca_linha(estado))
    await db.commit()
    db.add(_troca_linha("em_aberto"))
    await db.commit()
    db.add(_troca_linha("incerta"))
    with pytest.raises(Exception, match="uq_atendimento_trocas_aberta"):
        await db.commit()
    await db.rollback()


async def test_peca_em_disputa_recusa(db, make_user, monkeypatch):
    """Outra troca conferindo o MESMO produto novo (crítica M14): esta recusa sem escrever."""
    c = await _cenario(db, make_user, monkeypatch)
    pv = await _previa(db, c)
    async with _db.SessionLocal() as s2:
        # A "outra troca": segura o lock do SKU novo até o fim do bloco.
        await s2.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"atendimento_troca_sku:{c.novo}"},
        )
        with pytest.raises(troca.TrocaRecusada) as e:
            await _trocar(db, c, previa_hash=pv["previa_hash"])
        await s2.rollback()
    assert (e.value.code, e.value.troca_id is not None) == ("peca_em_disputa", True)
    assert c.bling.escritas() == []
    [t] = await _trocas(db)
    assert (t.estado, t.codigo_erro) == ("abortada", "peca_em_disputa")


def _troca_linha(estado):
    return AtendimentoTroca(
        pedido_bling=NUMERO,
        bling_id=BLING_ID,
        motivo_codigo="sem_estoque",
        sku_antigo="dg053.sp",
        sku_novo="dg054.sp",
        produto_novo_id=502,
        quantidade=1,
        nivel=1,
        estado=estado,
        passos=[],
        idem_key=uuid4(),
        criado_por_nome="x",
    )


# ─────────────── kits ───────────────


async def test_kit_sempre_substitui(db, make_user, monkeypatch, _chaves):
    assert _chaves.prioridade_substitui_item is False
    c = await _cenario(db, make_user, monkeypatch, kit=True)
    pv = await _previa(db, c)
    assert pv["pode"] is True, pv["travas"]
    assert (pv["nivel"], pv["exige_aceite"], pv["aceites_possiveis"]) == (0, False, [])
    # Nível 0: sem a caixinha.
    t = await _trocar(db, c, previa_hash=pv["previa_hash"], confirmar=False)
    assert t.estado == "concluida" and t.aceite_fonte is None
    [item] = c.bling.puts()[0][1]["itens"]
    assert "id" not in item
    assert (item["codigo"], item["produto"]) == ("dg057.sp+a001.sp", {"id": 602})
    assert "(mesmo produto, outro lote) - Atendimento/" in c.bling.puts()[0][1]["observacoes"]


async def test_kit_peca_compartilhada_soma(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch, kit=True)
    # Outro kit do pedido usa o mesmo fone que ENTRA (a001.sp).
    db.add(
        BlingOrder(
            bling_id=BLING_ID,
            item_index=1,
            numero=NUMERO,
            numeroloja=NUMEROLOJA,
            situacao="83955",
            loja=LOJA,
            item_codigo="dg060.sp+a001.sp",
            item_produto_id=610,
            item_quantidade=1,
        )
    )
    await db.commit()
    c.bling.order["itens"].append(
        {
            "id": 7009,
            "codigo": "dg060.sp+a001.sp",
            "quantidade": 1,
            "valor": 900,
            "produto": {"id": 610},
        }
    )
    # O kit novo "tem" 9, mas o fone (dividido) não tem nenhum.
    c.bling.produto("a001.sp", 700, 0, "Fone")
    pv = await _previa(db, c)
    assert pv["pode"] is False
    falha = next(t for t in pv["travas"] if not t["ok"])
    assert falha["code"] == "saldo_insuficiente" and "a001.sp" in falha["texto"]
    # Com o fone: passa.
    c.bling.produto("a001.sp", 700, 2, "Fone")
    assert (await _previa(db, c))["pode"] is True


# ─────────────── o aceite ───────────────


async def test_aceite_obrigatorio_nos_niveis_1_e_2(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c, confirmar=False)
    assert e.value.code == "aceite_obrigatorio"
    assert c.bling.escritas() == [] and await _trocas(db) == []


async def test_aceite_declarado_sem_prova(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    t = await _trocar(db, c)
    assert (t.aceite_fonte, t.aceite_texto, t.mensagem_aceite_id) == ("declarado", None, None)
    assert t.aceite_em is not None
    obs = c.bling.puts()[0][1]["observacoes"].splitlines()[0]
    assert obs.endswith(
        "TROCA dg053.sp -> dg054.sp (aceite declarado) - Atendimento/Fulana Atendimento"
    )


async def test_aceite_colado_do_duoke(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    quando = (AGORA - timedelta(minutes=30)).replace(microsecond=0)
    for ruim in (
        {"fonte": "duoke", "texto": "", "em": quando},
        {"fonte": "duoke", "texto": "pode", "em": None},
        # Velho (fora dos 7 dias) e no futuro. Antes da falta de estoque VALE
        # (decisão (b): a janela é só a dos dias; o cliente pode ter aceitado antes).
        {"fonte": "duoke", "texto": "pode", "em": AGORA - timedelta(days=9)},
        {"fonte": "duoke", "texto": "pode", "em": AGORA + timedelta(hours=2)},
        # A mensagem E o Duoke juntos.
        {"fonte": "duoke", "texto": "pode", "em": quando, "mensagem_aceite_id": uuid4()},
    ):
        with pytest.raises(troca.TrocaRecusada) as e:
            await _trocar(db, c, previa_hash="x" * 32, aceite=ruim)
        assert e.value.code == "aceite_invalido", ruim
    assert c.bling.escritas() == [] and await _trocas(db) == []
    # Sem fuso = horário de Brasília (o que a pessoa vê no Duoke).
    t = await _trocar(
        db, c, aceite={"fonte": "duoke", "texto": "  pode sim, manda a laranja ", "em": quando}
    )
    assert (t.aceite_fonte, t.aceite_texto, t.estado) == (
        "duoke",
        "pode sim, manda a laranja",
        "concluida",
    )
    assert t.aceite_em == quando
    assert ", duoke) - Atendimento/" in c.bling.puts()[0][1]["observacoes"]


async def test_aceite_de_outra_conversa_ou_da_loja_recusa(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    msg_ok = await _aceite_na_conversa(db, c)
    da_loja = await _mensagem(db, c.conversa, "loja", "obrigado!", 30)
    antes_da_oferta = await _mensagem(db, c.conversa, "cliente", "se faltar, manda outra cor", 55)
    velha = await _mensagem(db, c.conversa, "cliente", "pode ser", 8 * 24 * 60)
    # A prévia lista as do cliente da janela (a mais nova primeiro); "depois da
    # oferta" é só a dica da tela.
    pv = await _previa(db, c)
    assert [(a["id"], a["depois_da_oferta"]) for a in pv["aceites_possiveis"]] == [
        (str(msg_ok.id), True),
        (str(antes_da_oferta.id), False),
    ]
    outra, _ = await gravar.upsert_conversa(
        db,
        canal=c.canal,
        integration=c.integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id="conv-outra",
        pedido_marketplace="SHPOUTRO",
    )
    await db.commit()
    await _mensagem(db, outra, "loja", "oferta", 50)
    de_outra = await _mensagem(db, outra, "cliente", "pode", 40)
    # A fala da loja, a de outra conversa e a de fora dos 7 dias não valem.
    for ruim in (da_loja, de_outra, velha):
        with pytest.raises(troca.TrocaRecusada) as e:
            await _trocar(db, c, aceite={"mensagem_aceite_id": ruim.id})
        assert e.value.code == "aceite_invalido", ruim.texto
    assert c.bling.escritas() == [] and await _trocas(db) == []
    # A do cliente, mesmo antes da oferta da loja: vale (decisão (b) do dono).
    t = await _trocar(db, c, aceite={"mensagem_aceite_id": antes_da_oferta.id})
    assert (t.aceite_fonte, t.mensagem_aceite_id, t.estado) == (
        "davinci",
        antes_da_oferta.id,
        "concluida",
    )
    assert t.aceite_texto == "se faltar, manda outra cor"


async def test_previa_mudou(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    pv = await _previa(db, c)
    c.bling.order["itens"][0]["valor"] = 799.0  # o preço mudou desde a prévia
    with pytest.raises(troca.TrocaRecusada) as e:
        await _trocar(db, c, previa_hash=pv["previa_hash"])
    assert e.value.code == "previa_mudou"
    assert c.bling.escritas() == []
    [t] = await _trocas(db)
    assert t.estado == "abortada"


# ─────────────── o robô de lote (nível 0, sem pessoa) ───────────────


def _robo(c):
    return troca.executar(
        c.db,
        None,
        numero_bling=NUMERO,
        sku_antigo=c.antigo,
        sku_novo=c.novo,
        idem_key=uuid4(),
        automatica=True,
    )


async def test_modo_automatico_troca_o_lote_sozinho(db, make_user, monkeypatch, _chaves):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    c.db = db
    # Desligado (o padrão), fora do piloto ou com a lista piloto VAZIA
    # (decisão (e): o robô não solta em todos por esquecimento): nada.
    for chave, valor, code in (
        ("atendimento_troca_lote_auto", False, "troca_lote_auto_desligada"),
        ("atendimento_troca_pedidos", "", "piloto_vazio_no_automatico"),
        ("atendimento_troca_pedidos", "299999", "pedido_fora_do_piloto"),
    ):
        monkeypatch.setattr(_chaves, "atendimento_troca_lote_auto", True)
        monkeypatch.setattr(_chaves, chave, valor)
        with pytest.raises(troca.TrocaRecusada) as e:
            await _robo(c)
        assert e.value.code == code
    assert c.bling.chamadas == [] and await _trocas(db) == []
    # A pessoa segue a regra de sempre: a lista vazia = todos.
    monkeypatch.setattr(_chaves, "atendimento_troca_pedidos", "")
    assert troca.no_piloto(NUMERO) is True

    # "*" = todos, dito com todas as letras.
    monkeypatch.setattr(_chaves, "atendimento_troca_pedidos", "*")
    t = await _robo(c)
    assert t is not None and t.estado == "concluida", (t.estado, t.passos)
    assert (t.automatica, t.criado_por, t.criado_por_nome) == (True, None, troca.NOME_ROBO)
    assert (t.nivel, t.aceite_fonte, t.sku_antigo, t.sku_novo) == (0, None, "dg053.ci", "dg053.sp")
    assert c.bling.patches() == [9, 6]
    obs = c.bling.puts()[0][1]["observacoes"].splitlines()[0]
    assert obs.endswith(
        "TROCA dg053.ci -> dg053.sp (mesmo produto, outro lote) - Robô de lote (troca automática)"
    )
    # Sem pessoa, a Margem NÃO é aprovada: o pino fica como estava.
    [bo] = await _linhas_bo(db)
    assert (bo.item_codigo, bo.situacao, bo.status, bo.aprovado_por) == (
        "dg053.sp",
        "6",
        None,
        None,
    )
    assert [x[0] for x in await _trilhas(db)] == ["sku", "situacao"]
    assert all(x[3] == "atendimento_troca" and x[4] is None for x in await _trilhas(db))


async def test_modo_automatico_so_nivel_0(db, make_user, monkeypatch, _chaves):
    c = await _cenario(db, make_user, monkeypatch)  # dg053.sp → dg054.sp é nível 1
    monkeypatch.setattr(_chaves, "atendimento_troca_lote_auto", True)
    monkeypatch.setattr(_chaves, "atendimento_troca_pedidos", NUMERO)
    with pytest.raises(troca.TrocaRecusada) as e:
        await troca.executar(
            db,
            None,
            numero_bling=NUMERO,
            sku_antigo=c.antigo,
            sku_novo=c.novo,
            idem_key=uuid4(),
            automatica=True,
        )
    assert e.value.code == "nivel_exige_pessoa"
    assert c.bling.chamadas == []
    # Com a chave do robô desligada, a chamada direta também recusa.
    monkeypatch.setattr(_chaves, "atendimento_troca_lote_auto", False)
    with pytest.raises(troca.TrocaRecusada) as e:
        await troca.executar(
            db,
            None,
            numero_bling=NUMERO,
            sku_antigo=c.antigo,
            sku_novo=c.novo,
            idem_key=uuid4(),
            automatica=True,
        )
    assert e.value.code == "troca_lote_auto_desligada"


async def test_pessoa_troca_o_lote_sem_caixinha(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch, nivel0=True)
    t = await _trocar(db, c, confirmar=False)
    assert (t.estado, t.nivel, t.aceite_fonte, t.automatica) == ("concluida", 0, None, False)
    [bo] = await _linhas_bo(db)
    assert bo.status == "Aprovado"  # a pessoa aprova a Margem


# ─────────────── os robôs depois ───────────────


async def test_robo_margem_nao_segura_depois(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    await _snapshot(db, status=None, margem=0.05)  # margem baixa: o robô seguraria
    await _trocar(db, c)
    candidatos = (await db.execute(text(margem_auto_hold._candidatos_sql()))).all()
    assert NUMERO not in {r.pedido_bling for r in candidatos}
    # Sem o pino (o mesmo pedido em 6 com margem baixa), o robô pegaria.
    await db.execute(text("UPDATE verificar_margem SET bling_status_margem = NULL"))
    await db.commit()
    candidatos = (await db.execute(text(margem_auto_hold._candidatos_sql()))).all()
    assert NUMERO in {r.pedido_bling for r in candidatos}


async def test_sweep_nf_pega_depois(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch, faturador=True)
    assert NUMERO not in await nf_auto_enfileirar._candidatos(db)
    await _trocar(db, c)
    assert NUMERO in await nf_auto_enfileirar._candidatos(db)


# ─────────────── o Histórico ───────────────


async def test_historico_atribui_a_quem_clicou(db, make_user, monkeypatch):
    from app.historico import contexto as hctx

    c = await _cenario(db, make_user, monkeypatch)
    pv = await _previa(db, c)
    ator = hctx.Ator(
        metodo="POST",
        caminho="/api/atendimento/pedidos/{numero_bling}/troca",
        grava=True,
        escrita=True,
        user_id=c.adm.id,
        nome="Fulana",
    )
    tok = hctx.abrir(ator)
    try:
        await db.commit()  # a próxima transação nasce marcada
        t = await _trocar(db, c, previa_hash=pv["previa_hash"])
    finally:
        hctx.fechar(tok)
    assert t.estado == "concluida"
    linhas = (await db.execute(text("SELECT tabela, ator_id FROM historico_alteracao"))).all()
    tabelas = {tb for tb, _ in linhas}
    # Os commits de cada passo continuam em nome de quem clicou.
    assert {"bling_orders", "nf_faturamento"} <= tabelas
    assert {a for tb, a in linhas if tb in ("bling_orders", "nf_faturamento")} == {c.adm.id}
    # A troca (com o texto do comprador) fica fora.
    assert "atendimento_trocas" not in tabelas


# ─────────────── as correções da revisão (08/10) ───────────────


async def _marca_da_nf(db):
    nf = (
        await db.execute(select(NfFaturamento).where(NfFaturamento.pedido_bling == NUMERO))
    ).scalar_one()
    await db.refresh(nf)
    return nf.status_faturamento


async def _notas(db, c):
    return (
        (
            await db.execute(
                select(AtendimentoMensagem.texto).where(
                    AtendimentoMensagem.conversa_id == c.conversa.id,
                    AtendimentoMensagem.tipo == "nota",
                )
            )
        )
        .scalars()
        .all()
    )


async def test_nf_no_bling_com_o_pedido_ja_em_6_nao_libera_a_nf(db, make_user, monkeypatch):
    """O PATCH 6 levou 429; alguém pôs o pedido em 6 e emitiu a NF direto no Bling, e o
    coletor de XML ainda não gravou a `nf_nota`: o Retomar (sem PATCH a fazer) confere a
    NF no GET e NÃO limpa a marca — o sweep emitiria a segunda."""
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {6}
    t = await _trocar(db, c)
    assert t.estado == "em_atendido"
    c.bling.falha_patch = set()
    c.bling.order["situacao"]["id"] = 6
    c.bling.order["notaFiscal"] = {"id": 4270}
    c.bling.chamadas.clear()
    t = await troca.retomar(db, c.adm, t.id)
    assert (t.estado, t.codigo_erro) == ("abortada", "nf_emitida")
    assert c.bling.escritas() == []
    assert await _marca_da_nf(db) == "sem_estoque"
    assert any("já tem NF no Bling" in n for n in await _notas(db, c))


async def test_retomar_de_em_aberto_confere_a_nf_no_bling(db, make_user, monkeypatch):
    """A troca parou em `em_aberto` (o passo 6 quebrou): o Retomar parte do GET e,
    com NF no Bling, não limpa a marca."""
    c = await _cenario(db, make_user, monkeypatch)
    original = troca._liberar_nf

    async def _quebra(session, t, *, order=None):
        raise RuntimeError("o DaVinci caiu")

    monkeypatch.setattr(troca, "_liberar_nf", _quebra)
    t = await _trocar(db, c)
    assert (t.estado, t.codigo_erro) == ("em_aberto", "erro_interno")
    monkeypatch.setattr(troca, "_liberar_nf", original)
    c.bling.order["notaFiscal"] = {"id": 4270}
    t = await troca.retomar(db, c.adm, t.id)
    assert (t.estado, t.codigo_erro) == ("abortada", "nf_emitida")
    assert await _marca_da_nf(db) == "sem_estoque"


async def test_retomar_de_em_aberto_sem_nf_conclui(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    original = troca._liberar_nf

    async def _quebra(session, t, *, order=None):
        raise RuntimeError("o DaVinci caiu")

    monkeypatch.setattr(troca, "_liberar_nf", _quebra)
    t = await _trocar(db, c)
    monkeypatch.setattr(troca, "_liberar_nf", original)
    c.bling.chamadas.clear()
    t = await troca.retomar(db, c.adm, t.id)
    assert t.estado == "concluida"
    assert c.bling.escritas() == []  # já estava em 6: nem PATCH, nem PUT
    assert await _marca_da_nf(db) is None


async def test_token_da_shopee_renovado_na_troca_sobrevive_ao_rollback(db, make_user, monkeypatch):
    """O refresh token da Shopee é de uso único: o token renovado no meio da troca vai
    para uma sessão própria (`clientes.cliente_da_integracao`), sob a trava da loja."""
    c = await _cenario(db, make_user, monkeypatch)
    integ_id = c.integ.id  # antes do rollback (que expira a sessão)
    p = await troca._carregar(db, NUMERO)
    cliente = await _CLIENTE_SHOPEE(db, p)
    assert cliente is not None
    # A renovação passa pela `token_refresh_lock` (a do sync de 2 em 2 min).
    assert cliente.refresh.__name__ == "refresh_com_trava"
    novas = {"access_token": "novo", "refresh_token": "rt-novo", "expires_at": 2_000_000_000}
    await cliente._on_refresh(novas)
    # Um erro qualquer depois (o `_erro_interno` da troca, a exceção da prévia).
    await db.rollback()
    async with _db.SessionLocal() as s2:
        blob = await s2.scalar(select(Integration.credentials).where(Integration.id == integ_id))
    assert decrypt_json(blob)["access_token"] == "novo"  # noqa: S105 — token falso


async def test_pedido_excluido_e_reimportado_nao_e_dobro(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    db.add(
        BlingOrder(
            bling_id=BLING_ID + 1,
            item_index=0,
            numero="300999",
            numeroloja=NUMEROLOJA,
            situacao="excluido",
            loja=LOJA,
            item_codigo=c.antigo,
        )
    )
    await db.commit()
    pv = await _previa(db, c)
    assert pv["pode"] is True, pv["travas"]
    assert next(t for t in pv["travas"] if t["code"] == "pedido_em_dobro")["ok"] is True


def test_chaves_das_pecas():
    assert troca.chaves_das_pecas("dg053.ci", "DG053.SP") == ["dg053.sp"]
    assert troca.chaves_das_pecas("dg053.ci+a001.ci", "dg053.sp+a001.sp") == [
        "a001.sp",
        "dg053.sp",
        "dg053.sp+a001.sp",
    ]
    # O pedaço igual nos dois (sem lote) não é gasto pela troca.
    assert troca.chaves_das_pecas("dg057.ci+brinde", "dg057.sp+brinde") == [
        "dg057.sp",
        "dg057.sp+brinde",
    ]


async def test_peca_do_kit_em_disputa_com_o_produto_simples(db, make_user, monkeypatch):
    """Crítica M14 pela PEÇA: outra troca gastando o dg057.sp (simples ou noutro kit)
    segura o kit que também o leva."""
    c = await _cenario(db, make_user, monkeypatch, kit=True)
    pv = await _previa(db, c)
    async with _db.SessionLocal() as s2:
        await s2.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": "atendimento_troca_sku:dg057.sp"},
        )
        with pytest.raises(troca.TrocaRecusada) as e:
            await _trocar(db, c, previa_hash=pv["previa_hash"], confirmar=False)
        await s2.rollback()
    assert e.value.code == "peca_em_disputa"
    assert c.bling.escritas() == []


async def test_banco_falha_depois_do_put_e_retomar_refaz_o_passo_4(db, make_user, monkeypatch):
    """§3.4, linha 4: o PUT valeu e o passo do banco depois dele quebrou — a troca fica
    `iniciada` com `erro_interno`; o Retomar vê o item novo no GET e refaz o passo 4."""
    c = await _cenario(db, make_user, monkeypatch)
    original = troca.record_margem_audit
    vezes = {"n": 0}

    async def _falha_uma(*a, **kw):
        vezes["n"] += 1
        if vezes["n"] == 1:
            raise RuntimeError("o banco caiu")
        return await original(*a, **kw)

    monkeypatch.setattr(troca, "record_margem_audit", _falha_uma)
    t = await _trocar(db, c)
    assert (t.estado, t.codigo_erro) == ("iniciada", "erro_interno")
    assert len(c.bling.puts()) == 1 and c.bling.patches() == []
    [bo] = await _linhas_bo(db)
    assert bo.item_codigo == "dg053.sp"  # o passo 4 voltou atrás inteiro
    await db.refresh(c.adm)  # o rollback do `_erro_interno` expirou a sessão
    t = await troca.retomar(db, c.adm, t.id)
    assert t.estado == "concluida", (t.codigo_erro, t.passos)
    assert len(c.bling.puts()) == 1  # o Retomar NUNCA faz PUT
    assert c.bling.patches() == [9, 6]
    [bo] = await _linhas_bo(db)
    assert (bo.item_codigo, bo.status) == ("dg054.sp", "Aprovado")
    assert [x[0] for x in await _trilhas(db)] == ["sku", "status", "situacao"]


async def test_previa_e_clique_leem_o_mesmo_catalogo(db, make_user, monkeypatch):
    """O `previa_hash` leva os custos: a prévia relê o catálogo como o clique (o de 10 min
    de outro processo daria `previa_mudou` à toa)."""
    c = await _cenario(db, make_user, monkeypatch)
    pv1 = await _previa(db, c)  # o catálogo também fica na memória do processo
    await db.execute(
        update(Product).where(Product.sku == "dg054.sp").values(bling_cost_price=Decimal("500"))
    )
    await db.commit()
    pv2 = await _previa(db, c)
    assert pv2["depois"]["custo"] == 500.0
    assert pv2["previa_hash"] != pv1["previa_hash"]
    t = await _trocar(db, c, previa_hash=pv2["previa_hash"])
    assert t.estado == "concluida"


async def test_troca_parada_fora_de_83955_continua_visivel_e_retomavel(
    db, client, auth_as, make_user, monkeypatch
):
    """O PATCH 6 levou 429 e o webhook gravou 9 no espelho: a troca sai da lista
    Ag. cancelamento e do cartão, mas fica no painel e no GET /trocas, com o Retomar."""
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {6}
    t = await _trocar(db, c)
    assert t.estado == "em_atendido"
    await db.execute(update(BlingOrder).where(BlingOrder.numero == NUMERO).values(situacao="9"))
    await db.commit()
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"] is None
    aberta = out["troca_aberta"]
    assert (aberta["id"], aberta["estado"], aberta["pode_retomar"]) == (
        str(t.id),
        "em_atendido",
        True,
    )
    assert PainelOut(**out).troca_aberta.estado == "em_atendido"
    auth_as(c.adm)
    assert (await client.get(f"{URL}/ag-cancelamento")).json()["pedidos"] == []
    r = await client.get(f"{URL}/trocas", params={"abertas": "true"})
    assert [(x["pedido_bling"], x["estado"]) for x in r.json()["itens"]] == [
        (NUMERO, "em_atendido")
    ]
    c.bling.falha_patch = set()
    r = await client.post(f"{URL}/trocas/{t.id}/retomar")
    assert r.status_code == 200 and r.json()["estado"] == "concluida"
    assert c.bling.patches() == [9, 6, 6] and len(c.bling.puts()) == 1


async def test_observacoes_sem_o_recado_da_margem_para_quem_nao_ve(db, make_user, monkeypatch):
    """Decisão (g): o motivo da Margem vira "em análise" — e as Observações do Bling não
    o contam pelo lado (o recado do robô da Margem sai para quem não vê a Margem)."""
    c = await _cenario(db, make_user, monkeypatch)
    await _trava_margem(db, c)
    recado = (
        "02/10 - Margem DaVinci: pedido reprovado automaticamente (margem abaixo do mínimo) "
        "— situação movida para Aguardando Cancelamento. Aprovar na aba Margem devolve o "
        "pedido ao fluxo."
    )
    obs = {
        "observacoes": f"{recado}\n01/10 - cliente pediu nota com CNPJ",
        "observacoes_internas": "Margem DaVinci: segurado para análise",
        "lido_em": AGORA.isoformat(),
        "do_cache": False,
        "erro": None,
        "codigo": None,
    }

    async def _obs(bling_id, *, session=None, forcar=False):
        return dict(obs)

    monkeypatch.setattr(painel, "observacoes_bling", _obs)
    leitor = await make_user(permissions={"atendimento": {"view": True}})
    out = await painel.painel_da_conversa(db, c.conversa, user=leitor)
    assert out["ag_cancelamento"]["codigo"] == "em_analise"
    vistas = out["observacoes_bling"]
    assert vistas["observacoes"] == "01/10 - cliente pediu nota com CNPJ"
    assert vistas["observacoes_internas"] is None
    assert "margem" not in repr(vistas).lower()
    # Quem vê a Margem lê tudo.
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["observacoes_bling"]["observacoes"].startswith(recado)
    assert out["observacoes_bling"]["observacoes_internas"].startswith("Margem DaVinci:")


async def test_o_trocar_diz_se_pode(db, client, auth_as, make_user, monkeypatch, _chaves):
    """`troca_envio` (painel e lista): a chave, o piloto e as travas do pedido, só banco —
    tudo nasce desligado, e o botão nasce cinza com o porquê."""
    c = await _cenario(db, make_user, monkeypatch)

    async def _envio(user=None):
        out = await painel.painel_da_conversa(db, c.conversa, user=user or c.adm)
        return out["ag_cancelamento"]["troca_envio"]

    assert await _envio() == {"disponivel": True, "motivo": None, "texto_motivo": None}
    auth_as(c.adm)
    [pl] = (await client.get(f"{URL}/ag-cancelamento")).json()["pedidos"]
    assert pl["motivo"]["troca_envio"]["disponivel"] is True
    monkeypatch.setattr(_chaves, "atendimento_troca_pedidos", "299999")
    assert (await _envio())["motivo"] == "pedido_fora_do_piloto"
    monkeypatch.setattr(_chaves, "atendimento_troca_pedidos", "")
    await _fila(db, c)
    assert (await _envio())["motivo"] == "em_fila_nf"
    await db.execute(delete(NfCommand))
    await db.commit()
    await _plataforma_ml(db, c)
    envio = await _envio()
    assert envio["motivo"] == "plataforma_sem_conferencia" and "Shopee" in envio["texto_motivo"]
    monkeypatch.setattr(_chaves, "atendimento_troca_ativa", False)
    assert (await _envio())["motivo"] == "troca_desligada"
    # Quem só lê (fase de observação): cinza também (o botão nem aparece na tela).
    monkeypatch.setattr(_chaves, "atendimento_troca_ativa", True)
    leitor = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    assert (await _envio(leitor))["motivo"] == "atendimento_so_leitura"
    # Só banco: nenhum GET no Bling nem na Shopee.
    assert c.bling.chamadas == [] and c.shopee.chamadas == []


# ─────────────── as rotas ───────────────


async def test_rotas_recusam_quem_so_le(db, client, make_user, auth_as, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    leitor = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    auth_as(leitor)
    corpo = {"sku_antigo": c.antigo, "sku_novo": c.novo}
    r = await client.post(f"{URL}/pedidos/{NUMERO}/troca/previa", json=corpo)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "atendimento_so_leitura"
    r = await client.post(
        f"{URL}/pedidos/{NUMERO}/troca",
        json={**corpo, "previa_hash": "x" * 32, "idem_key": str(uuid4()), "confirmar": True},
    )
    assert r.status_code == 403
    r = await client.post(f"{URL}/trocas/{uuid4()}/retomar")
    assert r.status_code == 403
    # Ler, lê.
    r = await client.get(f"{URL}/trocas")
    assert r.status_code == 200 and r.json() == {"itens": []}
    assert c.bling.chamadas == []


async def test_rotas_pedem_a_margem(db, client, make_user, auth_as, monkeypatch):
    """Fora da observação: atendimento.edit sem margem.edit = 403 (a troca aprova a Margem)."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    c = await _cenario(db, make_user, monkeypatch)
    sem = await make_user(permissions={"atendimento": {"view": True, "edit": True}})
    auth_as(sem)
    r = await client.post(
        f"{URL}/pedidos/{NUMERO}/troca/previa", json={"sku_antigo": c.antigo, "sku_novo": c.novo}
    )
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "forbidden", "resource": "margem", "action": "edit"}
    com = await make_user(
        permissions={
            "atendimento": {"view": True, "edit": True},
            "margem": {"view": True, "edit": True},
        }
    )
    auth_as(com)
    r = await client.post(
        f"{URL}/pedidos/{NUMERO}/troca/previa", json={"sku_antigo": c.antigo, "sku_novo": c.novo}
    )
    assert r.status_code == 200, r.text


async def test_rota_409_e_404(db, client, make_user, auth_as, monkeypatch, _chaves):
    c = await _cenario(db, make_user, monkeypatch)
    auth_as(c.adm)
    corpo = {"sku_antigo": c.antigo, "sku_novo": c.novo}
    r = await client.post(f"{URL}/pedidos/999999/troca/previa", json=corpo)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "pedido_nao_encontrado"
    r = await client.post(
        f"{URL}/pedidos/{NUMERO}/troca",
        json={**corpo, "previa_hash": "x" * 32, "idem_key": str(uuid4())},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "aceite_obrigatorio"
    r = await client.post(f"{URL}/pedidos/{NUMERO}/troca", json={**corpo, "idem_key": "x"})
    assert r.status_code == 422
    monkeypatch.setattr(_chaves, "atendimento_troca_ativa", False)
    r = await client.post(f"{URL}/pedidos/{NUMERO}/troca/previa", json=corpo)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "troca_desligada"


async def test_rota_retomar_e_lista(db, client, make_user, auth_as, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    c.bling.falha_patch = {9}
    t = await _trocar(db, c)
    auth_as(c.adm)
    r = await client.get(f"{URL}/trocas")
    assert r.status_code == 200
    [item] = r.json()["itens"]
    assert (item["id"], item["estado"], item["pode_retomar"]) == (str(t.id), "item_trocado", True)
    c.bling.falha_patch = set()
    r = await client.post(f"{URL}/trocas/{t.id}/retomar")
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "concluida"
    assert (await client.get(f"{URL}/trocas")).json() == {"itens": []}
    todas = (
        await client.get(f"{URL}/trocas", params={"abertas": "false", "pedido": NUMERO})
    ).json()
    assert [x["estado"] for x in todas["itens"]] == ["concluida"]
    r = await client.post(f"{URL}/trocas/{uuid4()}/retomar")
    assert r.status_code == 404


async def test_lista_de_trocas_sem_custo_para_quem_nao_ve_a_margem(
    db, client, make_user, auth_as, monkeypatch
):
    c = await _cenario(db, make_user, monkeypatch)
    await _trocar(db, c)
    leitor = await make_user(permissions={"atendimento": {"view": True}})
    auth_as(leitor)
    r = await client.get(f"{URL}/trocas", params={"abertas": "false"})
    assert r.status_code == 200
    [item] = r.json()["itens"]
    assert (item["custo_antigo"], item["custo_novo"]) == (None, None)
    auth_as(c.adm)
    [item] = (await client.get(f"{URL}/trocas", params={"abertas": "false"})).json()["itens"]
    assert (item["custo_antigo"], item["custo_novo"]) == (495.0, 495.0)


# ─────────────── o painel ───────────────


async def test_painel_traz_a_troca_aberta(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["codigo"] == "sem_estoque"
    assert out["ag_cancelamento"]["troca_aberta"] is None
    c.bling.falha_patch = {9}
    t = await _trocar(db, c)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    aberta = out["ag_cancelamento"]["troca_aberta"]
    assert (aberta["id"], aberta["estado"], aberta["sku_novo"]) == (
        str(t.id),
        "item_trocado",
        "dg054.sp",
    )
    assert "custo_novo" not in aberta


async def test_painel_mascara_o_motivo_da_margem(db, make_user, monkeypatch):
    c = await _cenario(db, make_user, monkeypatch)
    await _trava_margem(db, c)
    out = await painel.painel_da_conversa(db, c.conversa, user=c.adm)
    assert out["ag_cancelamento"]["codigo"] == "margem_trava"
    leitor = await make_user(permissions={"atendimento": {"view": True}})
    out = await painel.painel_da_conversa(db, c.conversa, user=leitor)
    ag = out["ag_cancelamento"]
    assert (ag["codigo"], ag["titulo"], ag["texto"]) == (
        "em_analise",
        "Em análise",
        "em análise pela equipe",
    )
    assert ag["conflito"] is None and ag["skus"] == []
    assert "margem" not in ag["texto"].lower()
    assert out["margem"] is None and out["ve_margem"] is False
