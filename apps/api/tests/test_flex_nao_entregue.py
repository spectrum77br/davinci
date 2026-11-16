"""Aba Flex (Vinicius, 16/11/2026): coluna Cliente e o botão "Não conseguiu
entregar" (chamado na loja + mensagem ao cliente). Nenhuma chamada externa: o
envio da mensagem é trocado por um falso."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlingOrder,
    Chamado,
    ChamadoMensagem,
    Logistica,
    LogisticaTracken,
    User,
    UserRole,
    UserStatus,
)
from app.services import chamados as chamados_svc
from app.services import flex_config, flex_nao_entregue

_AGORA = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)  # 17h em Brasília


@pytest.fixture
async def admin(db: AsyncSession) -> User:
    email = f"adm-{uuid.uuid4().hex[:6]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        name="Cairo Teste",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _ev(em: str, descricao: str, observacao: str | None = None) -> dict:
    return {"em": em, "descricao": descricao, "observacao": observacao}


async def _pedido(
    db: AsyncSession,
    *,
    bling: str,
    loja: str,
    plataforma: str = "Shopee",
    conta: str = "mega",
    flex: bool | None = True,
    nome: str | None = "MARCIO CRISTIANO ROCHA",
    tentativa: bool = True,
) -> Logistica:
    linha = Logistica(
        plataforma=plataforma,
        conta=conta,
        pedido_bling=bling,
        pedido_marketplace=loja,
        envio_flex=flex,
        data=date.today(),
        meli_status={"order_status": "SHIPPED"},
        status_bling="Em andamento",
    )
    db.add(linha)
    db.add(BlingOrder(numero=bling, numeroloja=loja, nome_destinatario=nome, item_index=0))
    db.add(BlingOrder(numero=bling, numeroloja=loja, nome_destinatario=nome, item_index=1))
    await db.flush()
    if tentativa:
        db.add(
            LogisticaTracken(
                logistica_id=linha.id,
                achado=True,
                status="EM ROTA",
                ultimo_evento="EM ROTA DE ENTREGA",
                tentativas=1,
                historico=[
                    _ev("2026-11-16T13:00:00+00:00", "ARQUIVO IMPORTADO"),
                    _ev("2026-11-16T18:40:00+00:00", "DESTINATÁRIO AUSENTE"),
                    _ev("2026-11-16T19:00:00+00:00", "EM ROTA DE ENTREGA"),
                ],
            )
        )
    await db.commit()
    await db.refresh(linha)
    return linha


async def _mensagens(db: AsyncSession, ch_id) -> list[ChamadoMensagem]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == ch_id)
                .order_by(ChamadoMensagem.created_at)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _chamados(db: AsyncSession, bling: str) -> list[Chamado]:
    return list(
        (
            await db.execute(
                select(Chamado)
                .where(Chamado.pedido_bling == bling)
                .order_by(Chamado.created_at)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _envelhecer(db: AsyncSession) -> None:
    """Passa a janela contra o clique repetido (as notas ficam 10 min mais velhas)."""
    await db.execute(
        update(ChamadoMensagem).values(
            created_at=ChamadoMensagem.created_at - timedelta(minutes=10)
        )
    )
    await db.commit()


def _post(client: AsyncClient, linha: Logistica, texto: str, cliente: str = "", mandar=False):
    return client.post(
        f"/api/logistica/{linha.id}/flex-nao-entregue",
        json={"texto_chamado": texto, "texto_cliente": cliente, "mandar_mensagem": mandar},
    )


# ---------------------------------------------------------------- textos


def test_ultima_tentativa_e_textos():
    tk = LogisticaTracken(
        tentativas=1,
        historico=[
            _ev("2026-11-15T18:40:00+00:00", "DESTINATÁRIO  AUSENTE", "casa fechada"),
            _ev("2026-11-16T12:00:00+00:00", "EM ROTA"),
        ],
    )
    t = flex_nao_entregue.ultima_tentativa(tk)
    assert t.motivo == "destinatário ausente - casa fechada"
    assert t.em == datetime(2026, 11, 15, 18, 40, tzinfo=UTC)

    linha = Logistica(pedido_marketplace="2611ABC", pedido_bling="309000")
    ch = flex_nao_entregue.texto_chamado(linha, "shopee", t)
    assert ch.startswith(
        "Pedido 2611ABC (Entrega Direta): a transportadora tentou entregar em 15/11 às 15h40"
    )
    assert "(destinatário ausente - casa fechada)" in ch
    cli = flex_nao_entregue.texto_cliente("JOÃO DA SILVA", "ml", t, _AGORA)
    assert cli.startswith("Olá, João! Tentamos entregar seu pedido ontem às 15h40")
    # O texto pronto do ML passa na validação do ML (tamanho e caracteres).
    flex_nao_entregue.validar("ml", "c", cli, True)

    # Motivo comprido no ML: sai sem o motivo pra caber nos 350.
    longo = flex_nao_entregue.Tentativa("x" * 300, None)
    assert "x" * 10 not in flex_nao_entregue.texto_cliente(None, "ml", longo, _AGORA)
    assert "x" * 300 in flex_nao_entregue.texto_cliente(None, "shopee", longo, _AGORA)
    # Sem Tracken: textos sem hora nem motivo, a pessoa completa.
    vazio = flex_nao_entregue.ultima_tentativa(None)
    assert flex_nao_entregue.texto_cliente(None, "shopee", vazio, _AGORA).startswith(
        "Olá! Tentamos entregar seu pedido, mas não conseguimos. Vamos"
    )


def test_validar_e_normalizar_ml():
    v = flex_nao_entregue.validar
    v("ml", "chamado", "oi", True)
    v("ml", "chamado", "", False)
    for args, code in [
        (("ml", " ", "oi", True), "sem_texto_chamado"),
        (("shopee", "c", " ", True), "sem_texto_cliente"),
        (("ml", "c", "a" * 351, True), "texto_cliente_ml_grande"),
        (("ml", "c", "chegou 🙂", True), "texto_cliente_ml_caractere"),
    ]:
        with pytest.raises(flex_nao_entregue.FlexNaoEntregueError) as e:
            v(*args)
        assert e.value.code == code
    v("shopee", "c", "chegou 🙂", True)  # a Shopee aceita
    texto = flex_nao_entregue.normalizar_ml("Olá — “amanhã” às 10h…")
    assert texto == 'Olá - "amanhã" às 10h...'
    v("ml", "c", texto, True)


# ---------------------------------------------------------------- coluna Cliente


@pytest.mark.asyncio
async def test_coluna_cliente_vem_do_bling_so_no_flex(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    flex = await _pedido(db, bling="309100", loja="2611FLEX")
    comum = await _pedido(db, bling="309101", loja="2611COMUM", flex=False)
    auth_as(admin)
    lista = (await client.get("/api/logistica?envio=flex")).json()
    linha = next(x for x in lista if x["id"] == str(flex.id))
    assert linha["cliente_nome"] == "MARCIO CRISTIANO ROCHA"
    lista = (await client.get("/api/logistica?plataforma=shopee")).json()
    assert next(x for x in lista if x["id"] == str(comum.id))["cliente_nome"] is None
    # Quem não vê o Flex: nada muda pra ele.
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: False)
    lista = (await client.get("/api/logistica?plataforma=shopee")).json()
    assert next(x for x in lista if x["id"] == str(flex.id))["cliente_nome"] is None


# ---------------------------------------------------------------- botão


@pytest.mark.asyncio
async def test_shopee_abre_chamado_com_instrucao_e_manda_mensagem(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    linha = await _pedido(db, bling="309200", loja="2611SHOPEE")
    enviados: list[str] = []

    async def falso(session, row, texto):
        enviados.append(texto)
        return "enviada", None

    monkeypatch.setattr(flex_nao_entregue, "_mandar_shopee", falso)
    auth_as(admin)

    p = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p["plataforma"] == "shopee" and p["limite_cliente"] is None
    assert p["cliente_nome"] == "MARCIO CRISTIANO ROCHA"
    assert p["motivo"] == "destinatário ausente"
    assert p["chamado_acao"] == "aberto" and p["avisos"] == []
    assert "às 15h40 e não conseguiu (destinatário ausente)" in p["texto_chamado"]
    assert p["texto_cliente"].startswith("Olá, Marcio! Tentamos entregar seu pedido")

    r = await _post(client, linha, p["texto_chamado"], "Oi Marcio", mandar=True)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["chamado_acao"], out["mensagem_status"]) == ("aberto", "enviada")
    assert out["linha"]["cliente_nome"] == "MARCIO CRISTIANO ROCHA"
    assert out["linha"]["tracken"]["tentativas"] == 1
    assert enviados == ["Oi Marcio"]

    [ch] = await _chamados(db, "309200")
    assert (ch.origem, ch.canal, ch.origem_ref) == ("logistica", "robo", str(linha.id))
    assert ch.observacao.startswith("Aberto pela aba Flex")
    msgs = await _mensagens(db, ch.id)
    abertura = next(m for m in msgs if m.tipo == "abertura")
    # Shopee: a abertura fica registrada (fora da fila sem dono); quem age é a IA.
    assert (abertura.status, abertura.canal, abertura.texto) == (
        "registrada",
        "robo",
        p["texto_chamado"],
    )
    instr = [m for m in msgs if m.tipo == "instrucao"]
    assert len(instr) == 1 and "ainda não foi aberto, abra" in instr[0].texto
    assert instr[0].texto.endswith(p["texto_chamado"])
    assert instr[0].autor_nome == "Cairo Teste"
    nota = next(m for m in msgs if m.texto.startswith(flex_nao_entregue.PREFIXO_NOTA))
    assert (
        "chamado aberto; mensagem ao cliente enviada pelo chat da Shopee: «Oi Marcio»" in nota.texto
    )
    # A aba Chamados mostra a instrução pendente pra IA (não "na fila do robô").
    pagina = (await client.get("/api/chamados?search=309200")).json()
    item = next(i for i in pagina["items"] if i["id"] == str(ch.id))
    assert item["status_aba"] == chamados_svc.ABA_ANALISE_ROBO
    assert "instrução pendente" in item["status_aba_motivo"]

    # Clique repetido logo em seguida: 409, nada sai.
    r = await _post(client, linha, "de novo", "Oi de novo", mandar=True)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "acabou_de_avisar")
    assert enviados == ["Oi Marcio"]

    # 2ª tentativa (passados os minutos): o mesmo chamado, instrução COMPLETA de novo.
    await _envelhecer(db)
    p2 = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p2["chamado_acao"] == "instrucao" and len(p2["avisos"]) == 1
    r = await _post(client, linha, "Segunda tentativa falhou")
    assert r.json()["mensagem_status"] == "nao_pedida"
    msgs = await _mensagens(db, ch.id)
    assert len([m for m in msgs if m.tipo == "abertura"]) == 1
    instr = [m for m in msgs if m.tipo == "instrucao"]
    assert len(instr) == 2 and "ainda não foi aberto, abra" in instr[-1].texto
    assert instr[-1].texto.endswith("Segunda tentativa falhou")
    assert enviados == ["Oi Marcio"]  # sem mensagem dessa vez

    # Com o protocolo anotado: "no MESMO atendimento".
    ch.chamado = "2122318524369084505"
    await db.commit()
    await _envelhecer(db)
    await _post(client, linha, "Terceira")
    instr = [m for m in await _mensagens(db, ch.id) if m.tipo == "instrucao"]
    assert "MESMO atendimento" in instr[-1].texto

    # Chamado do botão concluído: o próximo clique abre OUTRO.
    ch.resolvido = True
    await db.commit()
    await _envelhecer(db)
    p3 = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p3["chamado_acao"] == "aberto" and len(p3["avisos"]) == 3
    r = await _post(client, linha, "Quarta")
    assert r.json()["chamado_acao"] == "aberto"
    assert len(await _chamados(db, "309200")) == 2


@pytest.mark.asyncio
async def test_nao_mexe_em_chamado_de_outro_assunto(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
):
    """Chamado à mão (com nº) e chamado da regra da aba Status do mesmo pedido
    ficam como estão: o botão abre o dele."""
    linha = await _pedido(
        db, bling="309250", loja="2000016049118601", plataforma="Mercado Livre", conta="atlas"
    )
    manual = Chamado(
        pedido_bling="309250",
        origem="logistica",
        origem_ref=str(linha.id),
        canal="manual",
        plataforma="Mercado Livre",
        chamado="495540550",
    )
    regra = Chamado(
        pedido_bling="309250",
        origem="logistica",
        origem_ref=str(linha.id),
        canal="robo",
        plataforma="Mercado Livre",
    )
    db.add_all([manual, regra])
    await db.flush()
    ab_regra = chamados_svc.nova_mensagem(
        regra, texto="texto da regra", tipo="abertura", status="pendente"
    )
    ab_regra.canal = "robo"
    db.add(ab_regra)
    await db.commit()
    auth_as(admin)

    p = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p["chamado_acao"] == "aberto" and p["chamado_id"] is None
    r = await _post(client, linha, "Flex não entregue")
    assert r.status_code == 200, r.text
    assert len(await _mensagens(db, manual.id)) == 0
    [ab] = await _mensagens(db, regra.id)
    assert ab.texto == "texto da regra"
    novo = (await _chamados(db, "309250"))[-1]
    assert novo.id == uuid.UUID(r.json()["chamado_id"]) and novo.id not in (manual.id, regra.id)
    ab = next(m for m in await _mensagens(db, novo.id) if m.tipo == "abertura")
    assert (ab.status, ab.canal, ab.texto) == ("pendente", "robo", "Flex não entregue")


@pytest.mark.asyncio
async def test_ml_mensagem_recusada_fica_anotada_e_2a_soma_na_abertura(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    linha = await _pedido(
        db, bling="309300", loja="2000016066120677", plataforma="Mercado Livre", conta="atlas"
    )
    enviados: list[str] = []

    async def recusa(session, row, texto):
        enviados.append(texto)
        return "falhou", "o Mercado Livre recusou (HTTP 403 blocked_by_restriction)"

    monkeypatch.setattr(flex_nao_entregue, "_mandar_ml", recusa)
    auth_as(admin)

    # Texto longo demais pro ML: 422 e nada é criado.
    r = await _post(client, linha, "c", "a" * 351, mandar=True)
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "texto_cliente_ml_grande")
    assert await _chamados(db, "309300") == []

    p = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p["plataforma"] == "ml" and p["limite_cliente"] == 350
    assert "(envio Flex)" in p["texto_chamado"]
    # Travessão digitado pela pessoa: sai como hífen (o ML não aceita o travessão).
    r = await _post(client, linha, "Primeira", "Olá — tentamos hoje", mandar=True)
    out = r.json()
    assert (out["chamado_acao"], out["mensagem_status"]) == ("aberto", "falhou")
    assert "blocked_by_restriction" in out["mensagem_erro"]
    assert enviados == ["Olá - tentamos hoje"]
    [ch] = await _chamados(db, "309300")
    msgs = await _mensagens(db, ch.id)
    assert not [m for m in msgs if m.tipo == "instrucao"]  # ML: a fila abrir-ml cuida
    ab = next(m for m in msgs if m.tipo == "abertura")
    assert (ab.status, ab.canal, ab.direcao) == ("pendente", "robo", "enviada")
    nota = next(m for m in msgs if m.texto.startswith(flex_nao_entregue.PREFIXO_NOTA))
    assert "NÃO saiu" in nota.texto and "mandar à mão" in nota.texto
    assert flex_nao_entregue.NOTA_ENVIANDO not in nota.texto

    # 2ª: a abertura ainda está na fila → a novidade entra no fim dela.
    await _envelhecer(db)
    p2 = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p2["chamado_acao"] == "acrescentado"
    await _post(client, linha, "Segunda")
    aberturas = [m for m in await _mensagens(db, ch.id) if m.tipo == "abertura"]
    assert len(aberturas) == 1
    assert aberturas[0].texto.startswith("Primeira\n\nAtualização ")
    assert aberturas[0].texto.endswith(": Segunda")

    # A IA abriu sem trazer o nº ainda (enviada) → instrução pro mesmo atendimento.
    aberturas[0].status = "enviada"
    await db.commit()
    await _envelhecer(db)
    p3 = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p3["chamado_acao"] == "instrucao"

    # A abertura falhou na IA → abre de novo no mesmo chamado.
    aberturas[0].status = "falhou"
    await db.commit()
    p4 = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p4["chamado_acao"] == "aberto" and p4["chamado_id"] == str(ch.id)
    await _post(client, linha, "Terceira")
    aberturas = [m for m in await _mensagens(db, ch.id) if m.tipo == "abertura"]
    assert [a.status for a in aberturas] == ["falhou", "pendente"]

    # Encerrado (sem decisão de pessoa): a abertura não sairia → instrução.
    ch.status_plataforma = chamados_svc.STATUS_ENCERRADO
    await db.commit()
    await _envelhecer(db)
    p5 = (await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")).json()
    assert p5["chamado_acao"] == "instrucao"


@pytest.mark.asyncio
async def test_so_pedido_flex_e_so_quem_ve_flex(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    comum = await _pedido(db, bling="309400", loja="2611COMUM", flex=False)
    tiktok = await _pedido(db, bling="309401", loja="5781", plataforma="TikTok", flex=True)
    flex = await _pedido(db, bling="309402", loja="2611FLEX")
    auth_as(admin)
    for linha in (comum, tiktok):
        r = await client.get(f"/api/logistica/{linha.id}/flex-nao-entregue")
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "nao_e_flex")
    monkeypatch.setattr(flex_config, "pode_ver", lambda user: False)
    assert (await client.get(f"/api/logistica/{flex.id}/flex-nao-entregue")).status_code == 404
    assert (await _post(client, flex, "x")).status_code == 404


# ---------------------------------------------------------------- envio de verdade (falso cliente)


class _MlFalso:
    creds = {"user_id": 111}

    def __init__(self, resposta: httpx.Response) -> None:
        self.resposta = resposta
        self.posts: list[tuple] = []

    async def enviar_mensagem_pack(self, pack, seller, buyer, texto):
        self.posts.append((pack, seller, buyer, texto))
        return self.resposta


@pytest.mark.asyncio
async def test_mandar_ml_le_o_pedido_e_traduz_a_recusa(db: AsyncSession, monkeypatch):
    from app.services import logistica_meli

    linha = Logistica(
        plataforma="Mercado Livre", conta="atlas", pedido_marketplace="2000016066120677"
    )

    async def integ(session, conta):
        return object()

    async def pedido(client, order_id):
        return {
            "id": 2000016066120677,
            "pack_id": None,
            "buyer": {"id": 999},
            "seller": {"id": 111},
        }

    monkeypatch.setattr(logistica_meli, "_ml_integration_for_conta", integ)
    monkeypatch.setattr(logistica_meli, "_fetch_order", pedido)

    ok = _MlFalso(httpx.Response(201, json={"id": "abc", "status": "available"}))
    monkeypatch.setattr(logistica_meli, "_build_ml_client", lambda s, i: ok)
    assert await flex_nao_entregue.mandar_mensagem(db, linha, "ml", " Olá ") == ("enviada", None)
    assert ok.posts == [("2000016066120677", 111, 999, "Olá")]

    nao = _MlFalso(
        httpx.Response(
            403, json={"code": "forbidden", "message": "blocked_by_conversation_not_initiated"}
        )
    )
    monkeypatch.setattr(logistica_meli, "_build_ml_client", lambda s, i: nao)
    status, erro = await flex_nao_entregue.mandar_mensagem(db, linha, "ml", "Olá")
    assert status == "falhou" and erro.startswith("o Mercado Livre recusou (HTTP 403")

    caiu = _MlFalso(httpx.Response(503, json={}))
    monkeypatch.setattr(logistica_meli, "_build_ml_client", lambda s, i: caiu)
    status, _ = await flex_nao_entregue.mandar_mensagem(db, linha, "ml", "Olá")
    assert status == "incerta"


@pytest.mark.asyncio
async def test_mandar_shopee_pega_o_comprador_do_pedido(db: AsyncSession, monkeypatch):
    from app.services import logistica_shopee

    linha = Logistica(plataforma="Shopee", conta="mega", pedido_marketplace="2611SN")
    falas: list[tuple] = []
    erro: list[Exception] = []

    class Falso:
        async def get_order_buyer(self, sn):
            return {"buyer_user_id": 777} if sn == "2611SN" else {}

        async def chat_send_message(self, to_id, *, text=""):
            if erro:
                raise erro[0]
            falas.append((to_id, text))
            return {"message_id": "m1"}

    async def integ(session, conta):
        return object()

    monkeypatch.setattr(logistica_shopee, "_shopee_integration_for_conta", integ)
    monkeypatch.setattr(logistica_shopee, "_build_shopee_client", lambda s, i: Falso())
    assert await flex_nao_entregue.mandar_mensagem(db, linha, "shopee", "Oi") == ("enviada", None)
    assert falas == [(777, "Oi")]

    # Erro que pode ter saído mesmo assim → "incerta" (não manda a pessoa repetir).
    for e in (
        RuntimeError("shopee_chat HTTP 502: <html>bad gateway"),
        httpx.ReadError("conexão caiu"),
    ):
        erro[:] = [e]
        status, _ = await flex_nao_entregue.mandar_mensagem(db, linha, "shopee", "Oi")
        assert status == "incerta", e
    erro[:] = [RuntimeError("shopee_chat error_param: buyer blocked chat")]
    status, msg = await flex_nao_entregue.mandar_mensagem(db, linha, "shopee", "Oi")
    assert status == "falhou" and "buyer blocked chat" in msg

    erro.clear()
    linha.pedido_marketplace = "OUTRO"
    status, msg = await flex_nao_entregue.mandar_mensagem(db, linha, "shopee", "Oi")
    assert (status, msg) == ("falhou", "a Shopee não informou o comprador do pedido")


# ---------------------------------------------------------------- automático


def _bruto(eventos: list[tuple[datetime, str]], status: str = "EM ROTA", entregue=None) -> list:
    """Resposta crua do Tracken (como a tela manda) com estes eventos."""
    sp = flex_nao_entregue.SAO_PAULO
    hist = [
        {
            "hsp_dtstatus": em.astimezone(sp).strftime("%Y-%m-%d"),
            "hsp_hrstatus": em.astimezone(sp).strftime("%H:%M:%S"),
            "deschistorico": desc,
        }
        for em, desc in eventos
    ]
    item = {"codpedido": "636803400", "sts_nome": status, "statushsp": eventos[-1][1]}
    if entregue is not None:
        item["hsp_dtentrega"] = entregue.astimezone(sp).strftime("%Y-%m-%d")
        item["hsp_hrentrega"] = entregue.astimezone(sp).strftime("%H:%M:%S")
    return [{**item, "historico": hist}]


def _resultado(client: AsyncClient, linha: Logistica, lista: list):
    return client.post(
        "/api/logistica/tracken/resultado",
        json={"itens": [{"logistica_id": str(linha.id), "lista": lista}]},
    )


@pytest.mark.asyncio
async def test_automatico_avisa_uma_vez_por_tentativa(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    linha = await _pedido(db, bling="309500", loja="2611AUTO", tentativa=False)
    enviados: list[str] = []

    async def falso(session, row, texto):
        enviados.append(texto)
        return "enviada", None

    monkeypatch.setattr(flex_nao_entregue, "_mandar_shopee", falso)
    auth_as(admin)
    agora = datetime.now(UTC).replace(microsecond=0)

    # Sem tentativa: nada.
    r = await _resultado(client, linha, _bruto([(agora - timedelta(hours=3), "SAIU PARA ENTREGA")]))
    assert r.json()["avisados"] == 0 and await _chamados(db, "309500") == []

    # A transportadora confirma que não conseguiu → chamado + mensagem na hora.
    eventos = [
        (agora - timedelta(hours=3), "SAIU PARA ENTREGA"),
        (agora - timedelta(minutes=50), "DESTINATÁRIO AUSENTE"),
    ]
    r = await _resultado(client, linha, _bruto(eventos))
    assert r.json() == {"gravados": 1, "achados": 1, "avisados": 1}
    [ch] = await _chamados(db, "309500")
    msgs = await _mensagens(db, ch.id)
    ab = next(m for m in msgs if m.tipo == "abertura")
    assert ab.autor_nome == flex_nao_entregue.AUTOR_AUTOMATICO and ab.autor_id is None
    assert "(destinatário ausente)" in ab.texto
    assert any(m.tipo == "instrucao" for m in msgs)  # Shopee: a IA abre
    nota = next(m for m in msgs if m.texto.startswith(flex_nao_entregue.PREFIXO_NOTA))
    assert "(automático (Tracken)): chamado aberto; mensagem ao cliente enviada" in nota.texto
    assert len(enviados) == 1 and enviados[0].startswith("Olá, Marcio! Tentamos entregar")

    # A mesma leitura de novo (30 min depois, outra aba aberta…): não repete.
    await _envelhecer(db)
    r = await _resultado(client, linha, _bruto(eventos))
    assert r.json()["avisados"] == 0 and len(enviados) == 1

    # Nova tentativa depois do aviso → avisa de novo (no mesmo chamado).
    eventos.append((agora - timedelta(minutes=2), "ENDEREÇO NÃO LOCALIZADO"))
    r = await _resultado(client, linha, _bruto(eventos))
    assert r.json()["avisados"] == 1 and len(enviados) == 2
    assert "(endereço não localizado)" in enviados[1]
    assert len(await _chamados(db, "309500")) == 1
    instr = [m for m in await _mensagens(db, ch.id) if m.tipo == "instrucao"]
    assert len(instr) == 2 and instr[-1].autor_nome == flex_nao_entregue.AUTOR_AUTOMATICO


@pytest.mark.asyncio
async def test_automatico_nao_dispara_em_velha_entregue_avisada_ou_desligado(
    client: AsyncClient,
    db: AsyncSession,
    admin: User,
    auth_as: Callable[[User | None], None],
    monkeypatch,
):
    from types import SimpleNamespace

    enviados: list[str] = []

    async def falso(session, row, texto):
        enviados.append(texto)
        return "enviada", None

    monkeypatch.setattr(flex_nao_entregue, "_mandar_shopee", falso)
    monkeypatch.setattr(flex_nao_entregue, "_mandar_ml", falso)
    auth_as(admin)
    agora = datetime.now(UTC).replace(microsecond=0)

    # Tentativa de anteontem (histórico velho na 1ª leitura): não avisa atrasado.
    velha = await _pedido(db, bling="309600", loja="2611VELHA", tentativa=False)
    r = await _resultado(client, velha, _bruto([(agora - timedelta(hours=30), "CLIENTE AUSENTE")]))
    assert r.json()["avisados"] == 0

    # Teve tentativa, mas já entregou: não avisa.
    entregue = await _pedido(db, bling="309601", loja="2611ENTREGUE", tentativa=False)
    lista = _bruto(
        [(agora - timedelta(hours=2), "CLIENTE AUSENTE"), (agora - timedelta(hours=1), "ENTREGUE")],
        status="ENTREGUE",
        entregue=agora - timedelta(hours=1),
    )
    assert (await _resultado(client, entregue, lista)).json()["avisados"] == 0

    # A pessoa já avisou pelo botão depois da tentativa: o automático não repete.
    feito = await _pedido(
        db, bling="309602", loja="2000016099990001", plataforma="Mercado Livre", conta="atlas"
    )
    await _post(client, feito, "à mão")
    await _envelhecer(db)
    lista = _bruto([(agora - timedelta(minutes=40), "DESTINATÁRIO AUSENTE")])
    assert (await _resultado(client, feito, lista)).json()["avisados"] == 0
    # (a nota da pessoa ficou 10 min mais velha, mas ainda depois da tentativa de 40 min)

    # Chave desligada: só o botão.
    monkeypatch.setattr(
        flex_nao_entregue, "get_settings", lambda: SimpleNamespace(flex_nao_entregue_auto=False)
    )
    nova = await _pedido(db, bling="309603", loja="2611DESLIGADO", tentativa=False)
    lista = _bruto([(agora - timedelta(minutes=10), "DESTINATÁRIO AUSENTE")])
    assert (await _resultado(client, nova, lista)).json()["avisados"] == 0
    assert enviados == []
    assert await _chamados(db, "309603") == []
