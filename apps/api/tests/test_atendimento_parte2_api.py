"""A API da caixa de atendimento na parte 2 (28/09/2026).

- P2: o nome da LOJA (não o da integração) no /resumo, na lista, no
  cabeçalho da conversa, nos canais e nas métricas — pelos dois vínculos do
  cadastro (`stores.integration_id` e `integrations.store_id`), com o
  `lojas.py` de verdade; sem ele, ou com ele falhando, fica o nome da
  integração e a resposta não quebra;
- P5: o detalhe traz o cartão "Cliente" (`cliente.cartao_cliente`); sem o
  módulo, ou com ele falhando (inclusive no SQL), `cliente = {}` e a
  conversa abre do mesmo jeito;
- P4: `flags.leitura_ativa` no /resumo e `pedido_atualizavel` no detalhe (a
  tela esconde o "atualizar" em vez de mostrar o 409);
- P7: regra com tipo/categoria/prioridade, 409 `regra_conflitante` no
  POST/PATCH, a trava que segura dois "Salvar" ao mesmo tempo, os conflitos
  que já existem no GET /regras, o GET /categorias e o só-humano do manual
  base no automático;
- Amazon: o cabeçalho da conversa (GET e PATCH) traz os links do rodapé do
  e-mail (`amazon_link_sem_resposta`, `amazon_link_caso`, `amazon_caso_id`),
  só se forem o https do próprio Seller Central.

`cliente.py` e `manual.py` são de outros lotes: entram falsos, pelo contrato,
no lugar do módulo inteiro (`sys.modules`). O último teste usa o `manual.py`
de verdade quando ele existir.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoRegra,
    Company,
    Integration,
    IntegrationPlatform,
    Store,
    User,
    UserRole,
)
from app.models.enums import Marketplace
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import clientes, contexto, gravar, validador

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}

PLATAFORMA_ENUM = {
    "shopee": IntegrationPlatform.SHOPEE,
    "ml": IntegrationPlatform.ML,
    "tiktok": IntegrationPlatform.TIKTOK,
    "amazon": IntegrationPlatform.AMAZON,
}
CANAL_PADRAO = {"shopee": "chat", "ml": "pergunta", "tiktok": "chat", "amazon": "email"}


# ─────────────── falsos e chaves ───────────────


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    """A caixa está SÓ ADMIN por enquanto (rota.SO_ADMIN, 30/09/2026). Os
    testes daqui usam não-admin com o recurso `atendimento` porque cobrem a
    permissão fina (view/edit/delete, equipe, `auto` só admin), que volta a
    valer quando a caixa abrir para a equipe. A trava de admin tem os testes
    dela em test_atendimento_so_admin.py."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture(autouse=True)
def _cerebro_falso(monkeypatch):
    def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
        return " ".join((texto or "").split())

    def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
        return [] if texto.strip() else ["Resposta vazia."]

    async def contexto_da_conversa(session, conversa):
        return contexto.vazio()

    monkeypatch.setattr(validador, "normalizar", normalizar)
    monkeypatch.setattr(validador, "validar", validar)
    monkeypatch.setattr(contexto, "contexto_da_conversa", contexto_da_conversa)


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_envio_ativo",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_alerta_telegram",
        "atendimento_simulador",
    ):
        monkeypatch.setattr(s, nome, False)
    return s


@pytest.fixture(autouse=True)
def _nunca_a_loja_de_verdade(monkeypatch):
    """Nenhum caminho daqui fala com a API de marketplace (o cartão do ML iria).

    O cartão engole o erro (nunca levanta), então a tentativa fica anotada e
    o teste falha no fim.
    """
    tentativas: list = []

    async def cliente_da_integracao(integration):
        tentativas.append(integration.id)
        raise RuntimeError("teste tentou falar com a loja de verdade")

    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_da_integracao)
    yield
    assert tentativas == []


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


def _trocar_modulo(monkeypatch, nome: str, **funcoes) -> types.ModuleType:
    """Põe um módulo falso no lugar do de verdade (o router importa pelo nome)."""
    modulo = types.ModuleType(nome)
    for chave, valor in funcoes.items():
        setattr(modulo, chave, valor)
    monkeypatch.setitem(sys.modules, nome, modulo)
    return modulo


def _sem_modulo(monkeypatch, nome: str) -> None:
    """O módulo "não existe": `import_module` levanta ModuleNotFoundError."""
    monkeypatch.setitem(sys.modules, nome, None)


class _Manual:
    """O `manual.py` falso, pelo contrato, com a regra de conflito da spec (P7).

    Conflito = outra regra ATIVA do tipo `categoria`, COM assunto, com a mesma
    (categoria, plataforma, canal) — regra geral não conflita, como no
    `manual.py` de verdade. Devolve id em UUID e data em datetime de
    propósito: o 409 tem de sair em JSON mesmo assim.
    """

    def __init__(self) -> None:
        self.chamadas: list[dict] = []
        self.espera = 0.0
        self.erro_existentes: Exception | None = None
        self.categorias: list[dict] = [
            {
                "id": "rastreio",
                "nome": "Rastreio",
                "descricao": "Onde está o pedido",
                "exemplos": ["cadê meu pedido?"],
                "so_humano": False,
                "lacunas": ["{rastreio}"],
                "ativa": True,
                "ordem": 1,
            },
            {
                "id": "troca_devolucao",
                "nome": "Troca ou devolução",
                "descricao": None,
                "exemplos": None,
                "so_humano": True,
                "lacunas": None,
                "ativa": True,
                "ordem": 2,
            },
            # Só existe no manual base (não nas constantes).
            {"id": "prazo_entrega", "nome": "Prazo de entrega", "so_humano": False, "ordem": 3},
        ]

    def _mesma_chave(self, dados: dict):
        return (
            AtendimentoRegra.ativa.is_(True),
            AtendimentoRegra.tipo == "categoria",
            AtendimentoRegra.categoria.is_not_distinct_from(dados.get("categoria")),
            AtendimentoRegra.plataforma.is_not_distinct_from(dados.get("plataforma")),
            AtendimentoRegra.canal.is_not_distinct_from(dados.get("canal")),
        )

    async def conflitos_da_regra(self, session, regra_dados, ignorar_id=None):
        self.chamadas.append({"dados": dict(regra_dados), "ignorar_id": ignorar_id})
        if (
            regra_dados.get("tipo") != "categoria"
            or not regra_dados.get("ativa", True)
            or not regra_dados.get("categoria")
        ):
            return []
        consulta = select(AtendimentoRegra).where(*self._mesma_chave(regra_dados))
        if ignorar_id is not None:
            consulta = consulta.where(AtendimentoRegra.id != ignorar_id)
        achadas = (
            (await session.execute(consulta.order_by(AtendimentoRegra.created_at)))
            .scalars()
            .all()
        )
        if self.espera:
            # Abre a janela entre conferir e gravar: sem a trava do router,
            # o segundo "Salvar" passaria por aqui antes do primeiro gravar.
            await asyncio.sleep(self.espera)
        return [
            {
                "id": r.id,
                "quando": r.quando,
                "categoria": r.categoria,
                "plataforma": r.plataforma,
                "canal": r.canal,
                "created_at": r.created_at,
            }
            for r in achadas
        ]

    async def conflitos_existentes(self, session):
        if self.erro_existentes is not None:
            raise self.erro_existentes
        ativas = (
            (
                await session.execute(
                    select(AtendimentoRegra).where(
                        AtendimentoRegra.ativa.is_(True),
                        AtendimentoRegra.tipo == "categoria",
                        AtendimentoRegra.categoria.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        grupos: dict[tuple, list] = {}
        for r in ativas:
            grupos.setdefault((r.categoria, r.plataforma, r.canal), []).append(r)
        return [
            {
                "categoria": chave[0],
                "plataforma": chave[1],
                "canal": chave[2],
                "regras": [{"id": r.id, "quando": r.quando} for r in regras],
            }
            for chave, regras in grupos.items()
            if len(regras) > 1
        ]

    async def categorias_ativas(self, session):
        return [dict(c) for c in self.categorias]


@pytest.fixture
def manual(monkeypatch) -> _Manual:
    falso = _Manual()
    _trocar_modulo(
        monkeypatch,
        rota._MANUAL,
        conflitos_da_regra=falso.conflitos_da_regra,
        conflitos_existentes=falso.conflitos_existentes,
        categorias_ativas=falso.categorias_ativas,
    )
    return falso


# ─────────────── fábrica ───────────────


async def _loja(
    db: AsyncSession,
    dono: User,
    nome: str,
    plataforma: str = "shopee",
    *,
    canais: dict[str, str] | None = None,
    modo: str = "observar",
    **kw,
) -> tuple[Integration, dict[str, AtendimentoCanal]]:
    """Integração + os canais dela ({canal: status})."""
    integ = Integration(
        user_id=dono.id,
        platform=PLATAFORMA_ENUM[plataforma],
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
        **kw,
    )
    db.add(integ)
    await db.flush()
    feitos: dict[str, AtendimentoCanal] = {}
    for canal, status in (canais or {CANAL_PADRAO[plataforma]: "ok"}).items():
        feitos[canal] = AtendimentoCanal(
            integration_id=integ.id, plataforma=plataforma, canal=canal, modo=modo, status=status
        )
        db.add(feitos[canal])
    await db.commit()
    return integ, feitos


async def _conversa(
    db: AsyncSession,
    integ: Integration | None,
    canal: AtendimentoCanal | None,
    externo_id: str,
    *,
    plataforma: str = "shopee",
    conta: str | None = None,
    **campos,
) -> AtendimentoConversa:
    """Conversa: o cliente perguntou 1 h atrás e a loja respondeu 30 min atrás."""
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal.canal if canal is not None else CANAL_PADRAO[plataforma],
        externo_id=externo_id,
        comprador_nome="Comprador",
        **campos,
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{externo_id}-c",
        autor="cliente",
        texto=f"pergunta {externo_id}",
        enviada_em=AGORA - timedelta(hours=1),
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{externo_id}-l",
        autor="loja",
        texto=f"resposta {externo_id}",
        enviada_em=AGORA - timedelta(minutes=30),
    )
    if conta is not None:
        # O retrato gravado ANTES da parte 2 (o nome da integração).
        conversa.conta = conta
    await db.commit()
    return conversa


async def _regra_direto(db: AsyncSession, **campos) -> AtendimentoRegra:
    """Regra gravada sem passar pela API (como as de antes da trava de conflito)."""
    r = AtendimentoRegra(
        quando=campos.pop("quando", "perguntar do rastreio"),
        faca=campos.pop("faca", "mande o {rastreio}"),
        **campos,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


# ─────────────── P2: o nome da loja ───────────────


async def test_nome_da_loja_pelos_dois_vinculos_em_toda_a_caixa(client, db, make_user, pessoa):
    """`stores.integration_id` e `integrations.store_id`, com o `lojas.py` de verdade."""
    dono = await make_user()
    mega, mega_c = await _loja(db, dono, "mega")
    amz, amz_c = await _loja(db, dono, "amz-kfa", "amazon")
    inova, _ = await _loja(db, dono, "Inova")  # sem loja ligada: o nome da integração
    marquezini = Company(razao_social="Marquezini LTDA", apelido="Shopee Marquezini")
    kfa = Company(razao_social="KFA LTDA", apelido="KFA Comércio")
    db.add_all([marquezini, kfa])
    await db.flush()
    # Vínculo 1: a loja aponta para a integração.
    db.add(Store(company_id=marquezini.id, marketplace=Marketplace.SHOPEE, integration_id=mega.id))
    # Vínculo 2: a integração aponta para a loja (e o apelido da loja manda).
    loja_amz = Store(
        company_id=kfa.id, marketplace=Marketplace.AMAZON, apelido_override="Amazon KFA"
    )
    db.add(loja_amz)
    await db.flush()
    amz.store_id = loja_amz.id
    await db.commit()

    c1 = await _conversa(db, mega, mega_c["chat"], "m1", conta="mega")
    await _conversa(db, amz, amz_c["email"], "<t1@amazon>", plataforma="amazon", conta="amz-kfa")

    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200
    lojas = {
        (lj["plataforma"], lj["conta"], lj["integracao"]) for lj in r.json()["lojas"]
    }
    assert lojas == {
        ("amazon", "KFA", "amz-kfa"),
        ("shopee", "Inova", "Inova"),
        ("shopee", "Marquezini", "mega"),
    }
    canais = {(c["conta"], c["integracao"]) for c in r.json()["canais"]}
    assert canais == {("KFA", "amz-kfa"), ("Inova", "Inova"), ("Marquezini", "mega")}
    assert [c["conta"] for c in (await client.get(f"{URL}/canais")).json()] == [
        "KFA",
        "Inova",
        "Marquezini",
    ]

    # Lista e cabeçalho: a conversa antiga (conta = "mega") sai com o nome de hoje.
    itens = (await client.get(f"{URL}/conversas")).json()["itens"]
    assert sorted(i["conta"] for i in itens) == ["KFA", "Marquezini"]
    detalhe = (await client.get(f"{URL}/conversas/{c1.id}")).json()
    assert detalhe["conversa"]["conta"] == "Marquezini"

    metricas = (await client.get(f"{URL}/metricas")).json()["lojas"]
    assert sorted(m["conta"] for m in metricas) == ["KFA", "Marquezini"]


async def test_sem_lojas_ou_com_ele_falhando_fica_o_nome_da_integracao(
    client, db, make_user, pessoa, monkeypatch
):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "Shopee Marquezini")
    c = await _conversa(db, integ, canais["chat"], "m1", conta="Shopee Marquezini")
    # Antes de trocar o módulo: a gravação (sync) também usa o `lojas.py`.
    await _conversa(db, integ, canais["chat"], "m2", conta="Shopee Marquezini")

    # Falha NO BANCO lá dentro: o SAVEPOINT segura, e o resto do detalhe
    # (mensagens, sugestões, envio) ainda consulta a mesma sessão.
    async def nome_que_quebra(session, integration):
        await session.execute(text("SELECT 1/0"))
        return "nunca"

    _trocar_modulo(monkeypatch, rota._LOJAS, nome_da_loja=nome_que_quebra)
    d = await client.get(f"{URL}/conversas/{c.id}")
    assert d.status_code == 200
    assert d.json()["conversa"]["conta"] == "Shopee Marquezini"
    assert len(d.json()["mensagens"]) == 2
    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200
    assert [lj["conta"] for lj in r.json()["lojas"]] == ["Shopee Marquezini"]
    itens = (await client.get(f"{URL}/conversas")).json()["itens"]
    assert [i["conta"] for i in itens] == ["Shopee Marquezini"] * 2

    # Sem o módulo: o nome da integração, sem erro.
    _sem_modulo(monkeypatch, rota._LOJAS)
    itens = (await client.get(f"{URL}/conversas")).json()["itens"]
    assert [i["conta"] for i in itens] == ["Shopee Marquezini"] * 2

    # Com o módulo (falso): a lista troca pelo nome da loja, uma chamada por loja.
    chamadas: list = []

    async def nome_da_loja(session, integration):
        chamadas.append(integration.id)
        return integration.name.removeprefix("Shopee ")

    _trocar_modulo(monkeypatch, rota._LOJAS, nome_da_loja=nome_da_loja)
    itens = (await client.get(f"{URL}/conversas")).json()["itens"]
    assert [i["conta"] for i in itens] == ["Marquezini", "Marquezini"]
    assert chamadas == [integ.id]


# ─────────────── P5: o cartão "Cliente" ───────────────

CARTAO = {
    "desde": "2026-03-02T10:00:00+00:00",
    "compras": 3,
    "total_gasto": 1840.0,
    "ultima_compra": "2026-09-24T12:00:00+00:00",
    "devolucoes": 0,
    "cancelamentos": 1,
    "avaliacoes": [
        {
            "estrelas": 2,
            "texto": "demorou",
            "pedido": "250928ABC",
            "criado_em": "2026-09-26T08:00:00+00:00",
            "respondida": False,
        }
    ],
    "perguntas_pre_venda": 1,
    "sinais": ["recorrente", "avaliou_mal"],
    "linha_do_tempo": [
        {"tipo": "compra", "em": "2026-09-24T12:00:00+00:00", "texto": "R$ 766,19", "ref": "x"}
    ],
}


async def test_detalhe_traz_o_cartao_cliente(client, db, make_user, pessoa, monkeypatch):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "c1")
    vistas: list = []

    async def cartao_cliente(session, conversa):
        vistas.append(conversa.id)
        return dict(CARTAO)

    _trocar_modulo(monkeypatch, rota._CLIENTE, cartao_cliente=cartao_cliente)
    d = await client.get(f"{URL}/conversas/{c.id}")
    assert d.status_code == 200
    assert d.json()["cliente"] == CARTAO
    assert vistas == [c.id]


async def test_cartao_cliente_de_verdade_pelo_indice_da_shopee(client, db, make_user, pessoa):
    """Com o `cliente.py` do outro lote: as compras do comprador vêm do índice próprio."""
    from app.services.atendimento import indice

    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "c1", comprador_id="buyer-123")
    for pedido, total, dias in (("250901AAA", 100.0, 20), ("250920BBB", 50.5, 5)):
        await indice.registrar_pedido(
            db,
            integration_id=integ.id,
            plataforma="shopee",
            comprador_id="buyer-123",
            pedido=pedido,
            criado_em=AGORA - timedelta(days=dias),
            total=total,
            status="COMPLETED",
            itens_resumo="Mala de bordo",
        )
    await db.commit()

    cartao = (await client.get(f"{URL}/conversas/{c.id}")).json()["cliente"]
    assert (cartao["compras"], cartao["total_gasto"]) == (2, 150.5)
    assert isinstance(cartao["sinais"], list)
    assert isinstance(cartao["linha_do_tempo"], list)


async def test_cartao_cliente_ausente_ou_falhando_nao_esconde_a_conversa(
    client, db, make_user, pessoa, monkeypatch
):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "c1")
    url = f"{URL}/conversas/{c.id}"

    _sem_modulo(monkeypatch, rota._CLIENTE)
    d = await client.get(url)
    assert (d.status_code, d.json()["cliente"]) == (200, {})

    async def quebra(session, conversa):
        raise RuntimeError("API do ML fora do ar")

    _trocar_modulo(monkeypatch, rota._CLIENTE, cartao_cliente=quebra)
    d = await client.get(url)
    assert (d.status_code, d.json()["cliente"]) == (200, {})
    assert len(d.json()["mensagens"]) == 2

    async def quebra_no_banco(session, conversa):
        await session.execute(text("SELECT 1/0"))

    _trocar_modulo(monkeypatch, rota._CLIENTE, cartao_cliente=quebra_no_banco)
    d = await client.get(url)
    assert (d.status_code, d.json()["cliente"]) == (200, {})

    async def formato_estranho(session, conversa):
        return ["não", "é", "dict"]

    _trocar_modulo(monkeypatch, rota._CLIENTE, cartao_cliente=formato_estranho)
    assert (await client.get(url)).json()["cliente"] == {}


# ─────────────── P4: o botão "atualizar" do painel Pedido ───────────────


async def test_flag_de_leitura_e_pedido_atualizavel(client, db, make_user, pessoa, _chaves):
    dono = await make_user()
    kfa, kfa_c = await _loja(db, dono, "kfa")
    ml, ml_c = await _loja(db, dono, "DREAM2", "ml")
    tiktok, tiktok_c = await _loja(db, dono, "ATV", "tiktok")
    parada, parada_c = await _loja(db, dono, "Inova", canais={"chat": "desligado"})
    velha, velha_c = await _loja(db, dono, "Velha", archived_at=AGORA)
    conversas = {
        "shopee": await _conversa(db, kfa, kfa_c["chat"], "s1"),
        "ml": await _conversa(db, ml, ml_c["pergunta"], "p1", plataforma="ml"),
        "tiktok": await _conversa(db, tiktok, tiktok_c["chat"], "t1", plataforma="tiktok"),
        "desligado": await _conversa(db, parada, parada_c["chat"], "d1"),
        "arquivada": await _conversa(db, velha, velha_c["chat"], "a1"),
    }

    async def atualizavel() -> dict[str, bool]:
        saida = {}
        for nome, c in conversas.items():
            d = await client.get(f"{URL}/conversas/{c.id}")
            assert d.status_code == 200
            saida[nome] = d.json()["pedido_atualizavel"]
        return saida

    # Leitura desligada: o botão some em todas (o POST daria 409).
    assert (await client.get(f"{URL}/resumo")).json()["flags"]["leitura_ativa"] is False
    assert not any((await atualizavel()).values())

    _chaves.atendimento_leitura_ativa = True
    assert (await client.get(f"{URL}/resumo")).json()["flags"]["leitura_ativa"] is True
    assert await atualizavel() == {
        "shopee": True,
        "ml": True,
        "tiktok": False,  # sem retrato de pedido pela API
        "desligado": False,  # a leitura desta loja está desligada
        "arquivada": False,  # a loja não está mais conectada
    }
    # O mesmo portão do POST: onde o detalhe diz False, o POST recusa.
    r = await client.post(f"{URL}/conversas/{conversas['desligado'].id}/pedido/atualizar")
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "canal_desligado")


# ─────────────── P7: regras sem regra batendo com regra ───────────────


async def test_regra_com_tipo_categoria_e_prioridade(client, pessoa, manual):
    r = await client.post(
        f"{URL}/regras",
        json={
            "quando": "perguntar do rastreio",
            "faca": "mande o {rastreio}",
            "tipo": "categoria",
            "categoria": " Rastreio ",
            "plataforma": "shopee",
            "canal": "chat",
            "prioridade": 10,
        },
    )
    assert r.status_code == 201
    regra = r.json()
    assert (regra["tipo"], regra["categoria"], regra["prioridade"]) == ("categoria", "rastreio", 10)
    assert (regra["em_conflito"], regra["conflita_com"]) == (False, [])
    # A conferência recebeu a regra inteira.
    assert manual.chamadas[-1]["dados"] == {
        "quando": "perguntar do rastreio",
        "faca": "mande o {rastreio}",
        "tipo": "categoria",
        "categoria": "rastreio",
        "plataforma": "shopee",
        "canal": "chat",
        "prioridade": 10,
        "ativa": True,
    }

    # Assunto que só existe no manual base vale; o que não existe nele, não.
    r = await client.post(
        f"{URL}/regras", json={"quando": "a", "faca": "b", "categoria": "prazo_entrega"}
    )
    assert r.status_code == 201
    r = await client.post(
        f"{URL}/regras", json={"quando": "a", "faca": "b", "categoria": "garantia"}
    )
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "categoria_invalida")
    # Formato e tipo são do schema.
    for corpo in (
        {"quando": "a", "faca": "b", "categoria": "Rastreio; DROP"},
        {"quando": "a", "faca": "b", "tipo": "urgente"},
        {"quando": "a", "faca": "b", "prioridade": -1},
    ):
        assert (await client.post(f"{URL}/regras", json=corpo)).status_code == 422

    # Segurança e estilo valem para toda mensagem: o assunto não é gravado.
    r = await client.post(
        f"{URL}/regras",
        json={"quando": "sempre", "faca": "nunca passe WhatsApp", "tipo": "seguranca",
              "categoria": "rastreio"},
    )
    assert (r.status_code, r.json()["tipo"], r.json()["categoria"]) == (201, "seguranca", None)
    seguranca = r.json()
    r = await client.patch(f"{URL}/regras/{seguranca['id']}", json={"categoria": "rastreio"})
    assert (r.status_code, r.json()["categoria"]) == (200, None)
    # Virar `categoria` com assunto: agora vale.
    r = await client.patch(
        f"{URL}/regras/{seguranca['id']}",
        json={"tipo": "categoria", "categoria": "troca_devolucao", "prioridade": 5},
    )
    assert (r.json()["tipo"], r.json()["categoria"], r.json()["prioridade"]) == (
        "categoria",
        "troca_devolucao",
        5,
    )


async def test_regra_com_assunto_que_saiu_do_manual_continua_editavel(client, pessoa, manual):
    """TELA-4: o assunto só é conferido quando MUDA.

    A regra de "prazo_entrega" continua editável depois que o assunto sai do
    manual (mesmo com uma tela que mande o corpo inteiro, com o assunto de
    antes); trocar para um assunto que não existe continua 422.
    """
    r = await client.post(
        f"{URL}/regras", json={"quando": "a", "faca": "b", "categoria": "prazo_entrega"}
    )
    assert r.status_code == 201
    regra_id = r.json()["id"]
    manual.categorias = [c for c in manual.categorias if c["id"] != "prazo_entrega"]

    r = await client.patch(f"{URL}/regras/{regra_id}", json={"faca": "novo texto"})
    assert (r.status_code, r.json()["faca"]) == (200, "novo texto")
    r = await client.patch(
        f"{URL}/regras/{regra_id}", json={"faca": "outro", "categoria": "prazo_entrega"}
    )
    assert (r.status_code, r.json()["categoria"]) == (200, "prazo_entrega")
    r = await client.patch(f"{URL}/regras/{regra_id}", json={"categoria": "garantia"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "categoria_invalida")
    r = await client.patch(f"{URL}/regras/{regra_id}", json={"categoria": "rastreio"})
    assert (r.status_code, r.json()["categoria"]) == (200, "rastreio")


async def test_regra_conflitante_e_409_no_post_e_no_patch(client, db, pessoa, manual):
    corpo = {
        "quando": "perguntar do rastreio",
        "faca": "mande o {rastreio}",
        "categoria": "rastreio",
        "plataforma": "shopee",
        "canal": "chat",
    }
    primeira = (await client.post(f"{URL}/regras", json=corpo)).json()

    r = await client.post(f"{URL}/regras", json={**corpo, "faca": "diga que já saiu"})
    assert r.status_code == 409
    detalhe = r.json()["detail"]
    assert detalhe["code"] == "regra_conflitante"
    assert detalhe["regra"]["id"] == primeira["id"]
    assert [c["id"] for c in detalhe["conflitos"]] == [primeira["id"]]
    assert (await db.scalar(select(func.count()).select_from(AtendimentoRegra))) == 1

    # Outra plataforma, outro canal, outro assunto ou GERAL: não bate.
    for outro in (
        {"plataforma": "ml", "canal": "pergunta"},
        {"canal": None},
        {"categoria": "prazo_entrega"},
        {"categoria": None},
    ):
        r = await client.post(f"{URL}/regras", json={**corpo, **outro})
        assert r.status_code == 201, outro
    # Inativa não entra no prompt: pode nascer — ATIVAR é que é recusado.
    r = await client.post(f"{URL}/regras", json={**corpo, "ativa": False})
    assert r.status_code == 201
    inativa = r.json()
    r = await client.patch(f"{URL}/regras/{inativa['id']}", json={"ativa": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "regra_conflitante")
    # Levar uma regra que não batia para cima da primeira também não.
    geral = next(
        x for x in (await client.get(f"{URL}/regras")).json()["regras"]
        if x["categoria"] is None and x["canal"] == "chat"
    )
    r = await client.patch(f"{URL}/regras/{geral['id']}", json={"categoria": "rastreio"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "regra_conflitante")
    # Mexer no texto da própria regra não é conflito com ela mesma.
    r = await client.patch(f"{URL}/regras/{primeira['id']}", json={"faca": "mande o rastreio"})
    assert r.status_code == 200


async def test_conflito_que_ja_existe_aparece_e_se_resolve_editando(client, db, pessoa, manual):
    """Regra de antes da trava (ou importada): a API não apaga — mostra e deixa resolver."""
    chave = {"tipo": "categoria", "categoria": "rastreio", "plataforma": "shopee", "canal": "chat"}
    a = await _regra_direto(db, faca="mande o {rastreio}", **chave)
    b = await _regra_direto(db, faca="diga que já saiu", **chave)
    sozinha = await _regra_direto(db, tipo="estilo", faca="assine Equipe KFA")

    lista = (await client.get(f"{URL}/regras")).json()
    por_id = {x["id"]: x for x in lista["regras"]}
    assert (por_id[str(a.id)]["em_conflito"], por_id[str(a.id)]["conflita_com"]) == (
        True,
        [str(b.id)],
    )
    assert por_id[str(b.id)]["conflita_com"] == [str(a.id)]
    assert (por_id[str(sozinha.id)]["em_conflito"], por_id[str(sozinha.id)]["tipo"]) == (
        False,
        "estilo",
    )
    # O formato do serviço passa como veio (ids em texto).
    assert len(lista["conflitos"]) == 1
    assert {x["id"] for x in lista["conflitos"][0]["regras"]} == {str(a.id), str(b.id)}

    # Editar o TEXTO de uma delas passa (o conflito não é novo), e a resposta
    # continua marcando o vermelho.
    r = await client.patch(
        f"{URL}/regras/{a.id}", json={"faca": "mande o rastreio", "prioridade": 1}
    )
    assert r.status_code == 200
    assert (r.json()["em_conflito"], r.json()["conflita_com"]) == (True, [str(b.id)])
    # Desativar resolve.
    r = await client.patch(f"{URL}/regras/{b.id}", json={"ativa": False})
    assert (r.status_code, r.json()["em_conflito"]) == (200, False)
    lista = (await client.get(f"{URL}/regras")).json()
    assert lista["conflitos"] == []
    assert not any(x["em_conflito"] for x in lista["regras"])

    # A conferência que falha não esconde o manual.
    manual.erro_existentes = RuntimeError("quebrou")
    r = await client.get(f"{URL}/regras")
    assert (r.status_code, r.json()["conflitos"], len(r.json()["regras"])) == (200, [], 3)


async def test_dois_salvar_ao_mesmo_tempo_nao_criam_duas_regras_que_batem(
    client, db, pessoa, manual
):
    """A trava da transação junta a conferência e o INSERT: um passa, o outro é 409."""
    manual.espera = 0.3
    corpo = {"quando": "rastreio", "faca": "mande o {rastreio}", "categoria": "rastreio"}
    r1, r2 = await asyncio.gather(
        client.post(f"{URL}/regras", json=corpo),
        client.post(f"{URL}/regras", json={**corpo, "faca": "outra"}),
    )
    assert sorted((r1.status_code, r2.status_code)) == [201, 409]
    total = await db.scalar(select(func.count()).select_from(AtendimentoRegra))
    assert total == 1


async def test_sem_o_manual_a_api_nao_grava_regra_ativa_sem_conferir(
    client, pessoa, monkeypatch
):
    _sem_modulo(monkeypatch, rota._MANUAL)
    # Assunto conferido pelas constantes; inativa não precisa de conferência.
    r = await client.post(
        f"{URL}/regras", json={"quando": "a", "faca": "b", "categoria": "garantia", "ativa": False}
    )
    assert r.status_code == 201
    r = await client.post(f"{URL}/regras", json={"quando": "a", "faca": "b"})
    assert (r.status_code, r.json()["detail"]["code"]) == (503, "manual_indisponivel")
    # A leitura continua.
    lista = (await client.get(f"{URL}/regras")).json()
    assert (len(lista["regras"]), lista["conflitos"]) == (1, [])
    categorias = (await client.get(f"{URL}/categorias")).json()
    ids = [c["id"] for c in categorias]
    assert ids[:2] == ["rastreio", "prazo_envio"]
    assert {c["id"]: c["so_humano"] for c in categorias}["troca_devolucao"] is True


async def test_categorias_do_manual_base(client, pessoa, manual):
    r = await client.get(f"{URL}/categorias")
    assert r.status_code == 200
    cats = r.json()
    assert [c["id"] for c in cats] == ["rastreio", "troca_devolucao", "prazo_entrega"]
    assert cats[0] == {
        "id": "rastreio",
        "nome": "Rastreio",
        "descricao": "Onde está o pedido",
        "exemplos": ["cadê meu pedido?"],
        "so_humano": False,
        "lacunas": ["{rastreio}"],
        "ordem": 1,
    }
    # JSONB vazio (None) vira lista vazia, não 500.
    assert (cats[1]["exemplos"], cats[1]["lacunas"]) == ([], [])

    # O manual base vazio (serviço sem nada): as constantes.
    manual.categorias = []
    assert (await client.get(f"{URL}/categorias")).json()[0]["id"] == "rastreio"


async def test_so_humano_do_manual_base_trava_o_automatico(
    client, db, make_user, auth_as, manual
):
    auth_as(await make_user(role=UserRole.ADMIN))
    dono = await make_user()
    _integ, canais = await _loja(db, dono, "kfa")
    url = f"{URL}/canais/{canais['chat'].id}"
    assert (await client.patch(url, json={"auto_categorias": ["rastreio"]})).status_code == 200
    # O manual base passou a dizer que rastreio é só com pessoa.
    manual.categorias[0]["so_humano"] = True
    r = await client.patch(url, json={"auto_categorias": ["rastreio", "agradecimento"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "categoria_so_humano")
    assert r.json()["detail"]["categorias"] == ["rastreio"]


async def test_automatico_segue_os_assuntos_do_manual_base(client, db, make_user, auth_as, manual):
    """As categorias do automático são as do manual base (a lista com que a IA classifica).

    Assunto que só existe no manual pode ser liberado; um que o manual não tem
    (nem as constantes, com o manual vazio) é 422 `categoria_invalida` — e a
    lista gravada segue a ordem do manual, sem repetição.
    """
    auth_as(await make_user(role=UserRole.ADMIN))
    _integ, canais = await _loja(db, await make_user(), "kfa")
    url = f"{URL}/canais/{canais['chat'].id}"
    manual.categorias.append({"id": "brinde", "nome": "Brinde", "so_humano": False, "ordem": 0})

    r = await client.patch(url, json={"auto_categorias": ["rastreio", "brinde", "rastreio"]})
    assert r.status_code == 200
    assert r.json()["auto_categorias"] == ["rastreio", "brinde"]

    # "agradecimento" está nas constantes, mas não neste manual base.
    r = await client.patch(url, json={"auto_categorias": ["agradecimento"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "categoria_invalida")
    assert r.json()["detail"]["categorias"] == ["agradecimento"]
    # Formato inválido nem chega ao manual.
    r = await client.patch(url, json={"auto_categorias": ["Rastreio!"]})
    assert r.status_code == 422


async def test_conflito_com_o_manual_de_verdade(client, db, pessoa):
    """Com o `manual.py` do outro lote (quando existir): mesmo assunto/loja/canal bate."""
    pytest.importorskip(rota._MANUAL)
    corpo = {
        "quando": "perguntar do rastreio",
        "faca": "mande o {rastreio}",
        "tipo": "categoria",
        "categoria": "rastreio",
        "plataforma": "shopee",
        "canal": "chat",
    }
    r = await client.post(f"{URL}/regras", json=corpo)
    assert r.status_code == 201, r.json()
    primeira = r.json()
    r = await client.post(f"{URL}/regras", json={**corpo, "faca": "diga que já saiu"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "regra_conflitante")
    assert UUID(str(r.json()["detail"]["regra"]["id"])) == UUID(primeira["id"])

    # Um par que já existia (gravado por fora) aparece marcado no GET.
    chave = {k: corpo[k] for k in ("tipo", "categoria", "plataforma", "canal")}
    outra = await _regra_direto(db, faca="responda em 1 h", **chave)
    lista = (await client.get(f"{URL}/regras")).json()
    por_id = {x["id"]: x for x in lista["regras"]}
    assert por_id[primeira["id"]]["em_conflito"] is True
    assert str(outra.id) in por_id[primeira["id"]]["conflita_com"]
    assert lista["conflitos"]
    assert "rastreio" in [c["id"] for c in (await client.get(f"{URL}/categorias")).json()]


# ─────────────── Amazon: links do rodapé no cabeçalho ───────────────

# Formato real, valores inventados (hash, token e caso são falsos).
CASO_FALSO = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
LINK_SEM_RESPOSTA = (
    "https://sellercentral.amazon.com.br/messaging/no-response-needed"
    "?t=tokenfalso123&h=hashfalso456&m=msgfalsa789"
)
LINK_CASO = (
    "https://sellercentral.amazon.com.br/messaging/inbox"
    f"?fi=caseId&ss={CASO_FALSO}&cc={CASO_FALSO}"
)


async def test_detalhe_e_patch_expoem_os_links_da_amazon(client, db, make_user, pessoa):
    """GET e PATCH levam os links de `conversa.dados` como campos soltos (não o `dados`)."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa", "amazon")
    c = await _conversa(
        db,
        integ,
        canais["email"],
        "<t1@marketplace.amazon.com.br>",
        plataforma="amazon",
        dados={
            "amazon_link_sem_resposta": LINK_SEM_RESPOSTA,
            "amazon_caso_id": CASO_FALSO,
            "amazon_link_caso": LINK_CASO,
        },
    )

    conversa = (await client.get(f"{URL}/conversas/{c.id}")).json()["conversa"]
    assert conversa["amazon_link_sem_resposta"] == LINK_SEM_RESPOSTA  # assinado: como veio
    assert conversa["amazon_link_caso"] == LINK_CASO
    assert conversa["amazon_caso_id"] == CASO_FALSO
    assert "dados" not in conversa

    # O "Não precisa de resposta" devolve a conversa inteira: os links continuam.
    r = await client.patch(f"{URL}/conversas/{c.id}", json={"sem_resposta_necessaria": True})
    assert r.status_code == 200, r.json()
    assert r.json()["conversa"]["amazon_link_sem_resposta"] == LINK_SEM_RESPOSTA
    assert r.json()["conversa"]["amazon_link_caso"] == LINK_CASO

    # A lista não carrega os links (só o detalhe precisa).
    item = (await client.get(f"{URL}/conversas")).json()["itens"][0]
    assert "amazon_link_caso" not in item


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "http://sellercentral.amazon.com.br/messaging/no-response-needed?t=x",
        "https://sellercentral.amazon.com.br.golpe.com/messaging/no-response-needed?t=x",
        "https://usuario:senha@sellercentral.amazon.com.br/messaging/inbox?fi=caseId",
        "https://golpe.com/?u=https://sellercentral.amazon.com.br/",
        "  ",
        123,
    ],
)
async def test_link_estranho_da_amazon_nao_sai(client, db, make_user, pessoa, link):
    """`dados` é JSON livre: só o https do próprio Seller Central vira link na tela."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa", "amazon")
    c = await _conversa(
        db,
        integ,
        canais["email"],
        "<t2@marketplace.amazon.com.br>",
        plataforma="amazon",
        dados={
            "amazon_link_sem_resposta": link,
            "amazon_link_caso": link,
            "amazon_caso_id": "abc&cc=outro-caso",
        },
    )
    conversa = (await client.get(f"{URL}/conversas/{c.id}")).json()["conversa"]
    assert conversa["amazon_link_sem_resposta"] is None
    assert conversa["amazon_link_caso"] is None
    assert conversa["amazon_caso_id"] is None


async def test_links_da_amazon_so_na_amazon(client, db, make_user, pessoa):
    """Outra plataforma com as mesmas chaves em `dados` (não deveria, mas é JSON livre) → None."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "mega")
    c = await _conversa(
        db,
        integ,
        canais["chat"],
        "s1",
        dados={"amazon_link_sem_resposta": LINK_SEM_RESPOSTA, "amazon_caso_id": CASO_FALSO},
    )
    conversa = (await client.get(f"{URL}/conversas/{c.id}")).json()["conversa"]
    assert conversa["amazon_link_sem_resposta"] is None
    assert conversa["amazon_link_caso"] is None
    assert conversa["amazon_caso_id"] is None

    # Amazon sem e-mail com rodapé (dados vazio): tudo None, a tela esconde os botões.
    amz, amz_c = await _loja(db, dono, "kfa", "amazon")
    c2 = await _conversa(db, amz, amz_c["email"], "<t3@amazon>", plataforma="amazon")
    conversa = (await client.get(f"{URL}/conversas/{c2.id}")).json()["conversa"]
    assert (
        conversa["amazon_link_sem_resposta"],
        conversa["amazon_link_caso"],
        conversa["amazon_caso_id"],
    ) == (None, None, None)


# ─────────────── revisão 4: simulador com exceção e cópia ambígua ───────────────


async def test_resumo_diz_quais_plataformas_escapam_do_simulador(
    client, db, make_user, pessoa, _chaves, monkeypatch
):
    """SEG-03: com o simulador ligado e a Amazon na exceção, a resposta na
    Amazon CHEGA ao comprador — a tela precisa saber para não dizer o
    contrário. Normalizado como no envio (`enviar.vai_para_o_simulador`)."""
    _chaves.atendimento_simulador = True
    monkeypatch.setattr(_chaves, "atendimento_simulador_exceto", " Amazon , ,shopee")
    flags = (await client.get(f"{URL}/resumo")).json()["flags"]
    assert flags["simulador"] is True
    assert flags["simulador_exceto"] == ["amazon", "shopee"]

    _chaves.atendimento_simulador = False  # sem o simulador a exceção não quer dizer nada
    assert (await client.get(f"{URL}/resumo")).json()["flags"]["simulador_exceto"] == []


async def test_mensagem_diz_se_saiu_pelo_simulador(client, db, make_user, pessoa):
    """SEG-03: o balão/toast se baseia no que o ENVIO gravou, não na chave de hoje."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa", "amazon")
    c = await _conversa(db, integ, canais["email"], "<sim@amazon>", plataforma="amazon")
    for i, envio in enumerate(({"simulador": True}, {"message_id": "<x@loja>", "smtp": None})):
        await gravar.gravar_mensagem(
            db,
            c,
            externo_id=f"sim-{i}",
            autor="loja",
            origem="davinci_humano",
            texto=f"resposta {i}",
            enviada_em=AGORA - timedelta(minutes=10 - i),
            payload={"envio": envio},
        )
    await db.commit()
    mensagens = (await client.get(f"{URL}/conversas/{c.id}")).json()["mensagens"]
    simulado = {m["texto"]: m["simulado"] for m in mensagens}
    assert simulado["resposta 0"] is True
    assert simulado["resposta 1"] is False
    assert simulado["pergunta <sim@amazon>"] is False


async def test_aviso_da_copia_ambigua_so_enquanto_a_pergunta_espera(
    client, db, make_user, pessoa
):
    """A cópia do Seller Central empatou entre duas conversas do mesmo nome
    (nenhuma saiu da fila): a tela avisa "conferir" — só enquanto a conversa
    aguarda e nenhuma pergunta mais nova que a cópia chegou."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa", "amazon")
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canais["email"],
        integration=integ,
        plataforma="amazon",
        canal_nome="email",
        externo_id="<amb@amazon>",
        comprador_nome="Maria Silva",
    )
    await gravar.gravar_mensagem(
        db, conversa, externo_id="amb-c1", autor="cliente", texto="tem azul?",
        enviada_em=AGORA - timedelta(hours=2),
    )
    copia_em = AGORA - timedelta(hours=1)
    conversa.dados = {
        **(conversa.dados or {}),
        "amazon_copia_a_conferir": {"message_id": "<c@amazon.com>", "em": copia_em.isoformat()},
    }
    await db.commit()

    async def _aviso():
        return (await client.get(f"{URL}/conversas/{conversa.id}")).json()["conversa"][
            "amazon_copia_a_conferir_em"
        ]

    assert datetime.fromisoformat(await _aviso()) == copia_em
    # Pergunta MAIS NOVA que a cópia: o aviso é sobre a anterior, some.
    await gravar.gravar_mensagem(
        db, conversa, externo_id="amb-c2", autor="cliente", texto="e verde?",
        enviada_em=AGORA - timedelta(minutes=30),
    )
    await db.commit()
    assert await _aviso() is None
