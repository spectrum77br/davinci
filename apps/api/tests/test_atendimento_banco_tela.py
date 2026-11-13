"""A TELA DO BANCO DE RESPOSTAS (13/11/2026, routers/atendimento_banco.py): só admin.

- a trava: quem não é admin leva 403 (na leitura e na escrita, com a trava da caixa ou a fina);
- a revisão do dia: o que a IA mandou SOZINHA (a mensagem `davinci_ia` enviada ou a conferir,
  ligada ao rascunho) aparece com o assunto, a loja, o selo "sensível", a pergunta (o turno do
  cliente) e a resposta, no dia de São Paulo; fica fora o que falhou, o de outro dia, o que a
  pessoa mandou e o que a pessoa já avaliou na conversa; a amostra (todos os sensíveis + até 20,
  o sorteio do dia, os revisados ficam) é determinística;
- o voto: 👍 e 👎 gravam a avaliação `automatico` (uma por rascunho; refazer troca a nota); o 👎
  sem correção, com correção vazia, longa demais ou igual ao que saiu é 422; o que não saiu
  sozinho é 409, o que a pessoa avaliou é 409, o que não existe é 404; a nota provisória da
  observação vira a da revisão;
- a correção entra no banco (ajustada por pessoa, generalizada, com o tipo do rascunho), a busca
  do agente a acha e o refazer da conversa não a aposenta; o 👍 depois a tira; a que não serve
  não entra, diz por quê e tira a anterior; nada de texto no log;
- a busca no banco: por texto (o `tsv`), por assunto e por loja; só as ativas; os filtros.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.config import get_settings
from app.main import app
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    IntegrationPlatform,
    User,
    UserRole,
)
from app.models.atendimento import AtendimentoResposta
from app.routers import atendimento as rota_atendimento
from app.routers import atendimento_banco as rota_banco
from app.security.cipher import encrypt_json
from app.services.atendimento import banco_respostas, constantes, duvida, gravar

URL = "/api/atendimento/banco"
FUSO = ZoneInfo("America/Sao_Paulo")
DIA = date(2026, 11, 10)
# 15:00 em São Paulo; o dia vai de 00:00 a 24:00 de lá (03:00 a 03:00 em UTC).
BASE = datetime.combine(DIA, time(15, 0), tzinfo=FUSO).astimezone(UTC)
FIM_DO_DIA = datetime.combine(DIA + timedelta(days=1), time.min, tzinfo=FUSO).astimezone(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True, "delete": True}}
TITULO = "Celular Uranyx Fossibot F105 64 Gb Memoria 6 Gb Ram"
RESPOSTA_IA = "Tem sim, NFC para pagamento por aproximação."


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    s = get_settings()
    # Todo admin mexe (a lista do .env não vale aqui); a trava da observação (SO_ADMIN) fica.
    monkeypatch.setattr(s, "atendimento_usuarios", "")
    monkeypatch.setattr(s, "atendimento_envio_ativo", False)


@pytest.fixture
async def admin(make_user, auth_as) -> User:
    u = await make_user(role=UserRole.ADMIN, email="revisora@davinci-test.com")
    auth_as(u)
    return u


async def _loja(
    db: AsyncSession, dono: User, nome: str = "kfa"
) -> tuple[Integration, AtendimentoCanal]:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo="auto", status="ok"
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _conversa(db: AsyncSession, integ, canal, externo_id: str) -> AtendimentoConversa:
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=externo_id,
        comprador_nome="Maria Souza",
        comprador_id="mariasouza22",
        anuncio_titulo=TITULO,
    )
    await db.commit()
    return conversa


async def _cliente(db: AsyncSession, conversa, texto: str, quando: datetime):
    m, _ = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"c-{uuid4().hex[:10]}",
        autor="cliente",
        texto=texto,
        enviada_em=quando,
    )
    await db.commit()
    return m


async def _envio(
    db: AsyncSession,
    conversa,
    gatilho,
    texto: str,
    quando: datetime,
    *,
    categoria: str | None,
    status: str = "enviada",
    origem: str = constantes.ORIGEM_IA,
) -> tuple[AtendimentoRascunho, AtendimentoMensagem]:
    """O rascunho da IA e a mensagem que saiu dele (sozinha, com a origem `davinci_ia`)."""
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=gatilho.id,
        texto=texto,
        categoria=categoria,
        confianca=0.9,
        precisa_humano=False,
        status="enviado",
    )
    db.add(r)
    await db.flush()
    m = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=f"ia-{uuid4().hex[:10]}",
        autor="loja",
        origem=origem,
        tipo="texto",
        texto=texto,
        enviada_em=quando,
        status=status,
        rascunho_id=r.id,
    )
    db.add(m)
    await db.commit()
    return r, m


async def _avaliacao(db: AsyncSession, rascunho_id: UUID) -> AtendimentoAvaliacao | None:
    return await db.scalar(
        select(AtendimentoAvaliacao)
        .where(AtendimentoAvaliacao.rascunho_id == rascunho_id)
        .execution_options(populate_existing=True)
    )


async def _banco(db: AsyncSession) -> list[AtendimentoResposta]:
    return list(
        (
            await db.scalars(
                select(AtendimentoResposta)
                .order_by(AtendimentoResposta.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def _um_envio(db, make_user, externo_id="V1"):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    c = await _conversa(db, integ, canal, externo_id)
    g = await _cliente(
        db, c, "esse celular tem NFC para pagar por aproximação?", BASE - timedelta(minutes=10)
    )
    r, m = await _envio(db, c, g, RESPOSTA_IA, BASE, categoria="espec_celular")
    return integ, c, r, m


# ─────────────── a trava ───────────────


async def test_quem_nao_e_admin_leva_403(client, make_user, auth_as, monkeypatch):
    pessoa = await make_user(permissions=PODE_TUDO)
    auth_as(pessoa)
    for url in (f"{URL}/revisao", f"{URL}/respostas"):
        r = await client.get(url)
        assert r.status_code == 403, url
        assert r.json()["detail"]["code"] == "so_admin"
    # A escrita: a trava da caixa (só quem mexe) já barra...
    r = await client.post(f"{URL}/revisao/{uuid4()}", json={"nota": "ok"})
    assert r.status_code == 403
    # ...e, sem a trava da observação (a permissão fina), a da tela.
    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    r = await client.post(f"{URL}/revisao/{uuid4()}", json={"nota": "ok"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "so_admin"


def test_as_rotas_tem_a_trava_da_caixa_e_a_de_admin():
    rotas = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.endpoint.__module__ == rota_banco.__name__
    ]
    assert {(m, r.path) for r in rotas for m in r.methods} == {
        ("GET", f"{URL}/revisao"),
        ("POST", f"{URL}/revisao/{{rascunho_id}}"),
        ("GET", f"{URL}/respostas"),
    }
    for r in rotas:
        dependencias = [d.call for d in r.dependant.dependencies]
        assert rota_atendimento._so_admin in dependencias, r.path
        assert rota_banco._admin in dependencias, r.path


# ─────────────── a revisão do dia ───────────────


async def test_revisao_do_dia_lista_o_que_a_ia_mandou_sozinha(client, db, make_user, admin):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    # 1) sensível (reembolso), com o turno do cliente de duas mensagens.
    c1 = await _conversa(db, integ, canal, "C1")
    await _cliente(db, c1, "oi, boa tarde", BASE - timedelta(minutes=12))
    g1 = await _cliente(
        db,
        c1,
        "quero o reembolso do pedido, como faço para pedir o estorno?",
        BASE - timedelta(minutes=10),
    )
    r1, _ = await _envio(
        db, c1, g1, "O reembolso sai pela Shopee depois da devolução.", BASE, categoria="reembolso"
    )
    # 2) comum, a conferir (`revisar` conta), às 23:30 em São Paulo (02:30 UTC do dia seguinte).
    c2 = await _conversa(db, integ, canal, "C2")
    g2 = await _cliente(db, c2, "esse celular tem NFC?", FIM_DO_DIA - timedelta(minutes=35))
    r2, _ = await _envio(
        db,
        c2,
        g2,
        RESPOSTA_IA,
        FIM_DO_DIA - timedelta(minutes=30),
        categoria="espec_celular",
        status="revisar",
    )
    # Fora: 3) o de ontem às 23:30 em São Paulo (que em UTC já é o dia 10)...
    c3 = await _conversa(db, integ, canal, "C3")
    ontem = datetime.combine(DIA, time.min, tzinfo=FUSO).astimezone(UTC) - timedelta(minutes=30)
    g3 = await _cliente(db, c3, "vem carregador junto?", ontem - timedelta(minutes=5))
    r3, _ = await _envio(db, c3, g3, "Vem sim.", ontem, categoria="conteudo_da_caixa")
    # ...4) o que falhou, 5) o que a pessoa mandou, 6) o que a pessoa avaliou na conversa.
    c4 = await _conversa(db, integ, canal, "C4")
    g4 = await _cliente(db, c4, "tem na cor azul?", BASE - timedelta(minutes=40))
    await _envio(db, c4, g4, "Tem sim.", BASE, categoria="estoque_variacao", status="falhou")
    g5 = await _cliente(db, c4, "e na preta?", BASE + timedelta(minutes=20))
    await _envio(
        db,
        c4,
        g5,
        "Tem também.",
        BASE + timedelta(minutes=25),
        categoria="estoque_variacao",
        origem=constantes.ORIGEM_HUMANO,
    )
    g6 = await _cliente(db, c4, "e chega rápido?", BASE + timedelta(minutes=40))
    r6, _ = await _envio(
        db, c4, g6, "Chega em poucos dias.", BASE + timedelta(minutes=45), categoria="outro"
    )
    db.add(AtendimentoAvaliacao(rascunho_id=r6.id, acao="enviou_igual", nota="ok"))
    await db.commit()

    r = await client.get(f"{URL}/revisao", params={"dia": DIA.isoformat()})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["dia"] == DIA.isoformat() and corpo["amostra"] == 20
    # O sensível primeiro.
    assert [i["rascunho_id"] for i in corpo["itens"]] == [str(r1.id), str(r2.id)]
    a, b = corpo["itens"]
    assert a["sensivel"] is True and a["assunto"] == "reembolso"
    assert a["assunto_nome"] == duvida.nome_do_assunto("reembolso") == "Reembolso"
    assert a["pergunta"] == (
        "oi, boa tarde\nquero o reembolso do pedido, como faço para pedir o estorno?"
    )
    assert a["resposta"] == "O reembolso sai pela Shopee depois da devolução."
    assert (a["plataforma"], a["canal"], a["conversa_id"]) == ("shopee", "chat", str(c1.id))
    assert a["loja"].lower() == "kfa" and a["integration_id"] == str(integ.id)
    assert datetime.fromisoformat(a["enviada_em"]) == BASE
    assert a["avaliacao"] is None and a["com_anexo"] is False
    assert b["sensivel"] is False and b["assunto"] == "espec_celular"
    assert b["assunto_nome"] == duvida.nome_do_assunto("espec_celular")
    assert (b["pergunta"], b["resposta"]) == ("esse celular tem NFC?", RESPOSTA_IA)
    assert corpo["contagens"] == {
        "total": 2,
        "sensiveis": 1,
        "revisados": 0,
        "erros": 0,
        "mostrados": 2,
    }
    # O de ontem está no dia de ontem (o dia é o de São Paulo).
    ontem_corpo = (
        await client.get(f"{URL}/revisao", params={"dia": (DIA - timedelta(days=1)).isoformat()})
    ).json()
    assert [i["rascunho_id"] for i in ontem_corpo["itens"]] == [str(r3.id)]
    # Sem o dia: hoje em São Paulo.
    hoje = (await client.get(f"{URL}/revisao")).json()
    assert hoje["dia"] == hoje["hoje"] == rota_banco.hoje().isoformat()
    assert (await client.get(f"{URL}/revisao", params={"dia": "10/11/2026"})).status_code == 422


def test_amostra_todos_os_sensiveis_e_ate_20_dos_outros_pelo_sorteio_do_dia():
    def envio(categoria: str, minuto: int, *, revisado: bool = False) -> rota_banco.Envio:
        return rota_banco.Envio(
            rascunho_id=uuid4(),
            conversa_id=uuid4(),
            categoria=categoria,
            gatilho_id=None,
            enviada_em=BASE + timedelta(minutes=minuto),
            acao=constantes.AVALIACAO_AUTOMATICO if revisado else None,
            nota=constantes.NOTA_OK if revisado else None,
        )

    sensiveis = [
        envio(c, i) for i, c in enumerate(("reembolso", "cancelamento", "troca_devolucao"))
    ]
    outros = [envio("rastreio", 10 + i, revisado=i >= 28) for i in range(30)]
    todos = [*outros, *sensiveis]
    escolhidos = rota_banco.escolher(todos, DIA)
    assert escolhidos[:3] == sensiveis, "os sensíveis, todos, primeiro"
    resto = escolhidos[3:]
    assert len(resto) == 20 and outros[28] in resto and outros[29] in resto, "o revisado fica"
    assert [e.enviada_em for e in resto] == sorted(e.enviada_em for e in resto)
    # O mesmo dia, a mesma amostra (em qualquer ordem); outro dia, outra.
    assert rota_banco.escolher(list(reversed(todos)), DIA) == escolhidos
    assert rota_banco.escolher(todos, DIA + timedelta(days=1)) != escolhidos
    assert rota_banco.escolher(outros[:5], DIA) == outros[:5]


# ─────────────── o voto ───────────────


async def test_o_voto_grava_a_avaliacao_automatico_e_a_correcao_entra_no_banco(
    client, db, make_user, admin
):
    integ, c, r, m = await _um_envio(db, make_user)
    url = f"{URL}/revisao/{r.id}"

    # 👍: a avaliação `automatico`, sem correção (e nada no banco).
    resp = await client.post(url, json={"nota": "ok", "correcao": "ignorada no 👍"})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["item"]["rascunho_id"] == str(r.id)
    assert corpo["item"]["avaliacao"]["nota"] == "ok"
    assert corpo["item"]["avaliacao"]["correcao"] is None
    assert corpo["item"]["avaliacao"]["por"] == "revisora@davinci-test.com"
    assert corpo["banco"] == {"entrou": False, "motivo": None, "frase": None, "retirada": False}
    av = await _avaliacao(db, r.id)
    assert (av.acao, av.nota, av.correcao, av.user_id) == ("automatico", "ok", None, admin.id)
    assert av.texto_final == RESPOSTA_IA
    assert await _banco(db) == []

    # 👎 com a correção: a MESMA avaliação; a correção entra no banco.
    correcao = "Esse modelo F105 não tem NFC, mas tem leitor de digital na lateral."
    with capture_logs() as logs:
        resp = await client.post(url, json={"nota": "erro", "correcao": f"  {correcao}\n"})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["banco"]["entrou"] is True and corpo["banco"]["motivo"] is None
    nota = corpo["item"]["avaliacao"]
    assert (nota["nota"], nota["correcao"], nota["no_banco"]) == ("erro", correcao, True)
    total = await db.scalar(
        select(func.count())
        .select_from(AtendimentoAvaliacao)
        .where(AtendimentoAvaliacao.rascunho_id == r.id)
    )
    assert total == 1
    av = await _avaliacao(db, r.id)
    assert (av.acao, av.nota, av.correcao) == ("automatico", "erro", correcao)
    [linha] = await _banco(db)
    assert linha.fonte == banco_respostas.FONTE_AJUSTOU and linha.status == "ativa"
    assert linha.tipo == "espec_celular" and linha.modelos == ["F105"]
    assert linha.integration_id == integ.id and linha.plataforma == "shopee"
    assert linha.conversa_id is None and linha.resposta_msg_id == m.id
    assert linha.chave == banco_respostas.chave_de(c.id, m.id)
    assert linha.pergunta == "esse celular tem NFC para pagar por aproximação?"
    assert linha.resposta == correcao
    # Nada de texto no log (nem do cliente, nem da correção).
    textos = json.dumps(logs, default=str)
    assert "NFC" not in textos and "aproxima" not in textos and "digital" not in textos
    assert any(e.get("event") == "atendimento_banco_revisado" for e in logs)

    # O refazer da conversa (o cron, o envio por pessoa) não aposenta a correção...
    await db.refresh(c)
    await banco_respostas.indexar_conversa(db, c)
    await db.commit()
    [linha] = await _banco(db)
    assert linha.status == "ativa"
    # ...a busca do agente a acha...
    achados = await banco_respostas.buscar(
        db,
        pergunta="tem NFC?",
        plataforma="shopee",
        integration_id=integ.id,
        item_ids=[],
        modelos=["F105"],
        tipo="espec_celular",
        conversa_id=None,
        as_of=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert [a["resposta"] for a in achados] == [correcao]
    assert achados[0]["fonte"] == banco_respostas.FONTE_AJUSTOU
    # ...e a busca da tela também, marcada como da revisão, com a conversa.
    busca = (await client.get(f"{URL}/respostas", params={"q": "nfc"})).json()
    assert [(i["resposta"], i["da_revisao"], i["conversa_id"]) for i in busca["itens"]] == [
        (correcao, True, str(c.id))
    ]

    # 👍 de novo: a correção sai do banco (aposentada, nunca apagada).
    resp = await client.post(url, json={"nota": "ok"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["banco"]["retirada"] is True
    [linha] = await _banco(db)
    assert linha.status == banco_respostas.STATUS_APOSENTADA
    lista = (await client.get(f"{URL}/revisao", params={"dia": DIA.isoformat()})).json()
    assert lista["contagens"]["revisados"] == 1 and lista["contagens"]["erros"] == 0
    assert lista["itens"][0]["avaliacao"]["nota"] == "ok"
    assert (await client.get(f"{URL}/respostas", params={"q": "nfc"})).json()["total"] == 0


async def test_erro_sem_correcao_ou_igual_ao_que_saiu_da_422(client, db, make_user, admin):
    _integ, _c, r, _m = await _um_envio(db, make_user)
    url = f"{URL}/revisao/{r.id}"
    for corpo in (
        {"nota": "erro"},
        {"nota": "erro", "correcao": "   "},
        {"nota": "erro", "correcao": "x" * 4001},
        {"nota": "talvez"},
        {},
    ):
        assert (await client.post(url, json=corpo)).status_code == 422, corpo
    # A "correção" igual ao que saiu (só espaço a mais) não corrige nada.
    resp = await client.post(url, json={"nota": "erro", "correcao": f"  {RESPOSTA_IA}\n"})
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "correcao_igual_ao_enviado"
    assert await _avaliacao(db, r.id) is None
    # 4.000 caracteres ainda vale.
    resp = await client.post(url, json={"nota": "erro", "correcao": "Não tem NFC. " + "a" * 3987})
    assert resp.status_code == 200, resp.text


async def test_correcao_que_nao_serve_fica_fora_do_banco_e_diz_por_que(
    client, db, make_user, admin
):
    _integ, _c, r, _m = await _um_envio(db, make_user)
    url = f"{URL}/revisao/{r.id}"
    boa = "Esse modelo F105 não tem NFC, mas tem leitor de digital na lateral."
    assert (await client.post(url, json={"nota": "erro", "correcao": boa})).json()["banco"][
        "entrou"
    ]
    # A correção nova com contato: não entra, diz por quê e a anterior sai (a mais nova vence).
    resp = await client.post(
        url,
        json={"nota": "erro", "correcao": "manda no email fulano.silva@gmail.com que te explico"},
    )
    assert resp.status_code == 200, resp.text
    banco = resp.json()["banco"]
    assert banco["entrou"] is False and banco["motivo"] in {"dado_pessoal", "proibido"}
    assert banco["frase"] == rota_banco.FRASE_FORA_DO_BANCO[banco["motivo"]]
    assert resp.json()["item"]["avaliacao"]["no_banco"] is False
    [linha] = await _banco(db)
    assert linha.status == banco_respostas.STATUS_APOSENTADA
    av = await _avaliacao(db, r.id)
    assert av.nota == "erro" and "fulano" in av.correcao


async def test_so_o_que_saiu_sozinho_e_ninguem_avaliou_na_conversa(client, db, make_user, admin):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    c = await _conversa(db, integ, canal, "P1")
    # A pessoa já avaliou na conversa (o 👍/👎 de antes): fora da lista e 409.
    g1 = await _cliente(db, c, "tem na cor azul?", BASE - timedelta(minutes=30))
    avaliado, _ = await _envio(db, c, g1, "Tem sim.", BASE - timedelta(minutes=25), categoria="x")
    db.add(AtendimentoAvaliacao(rascunho_id=avaliado.id, acao="enviou_igual", nota="ok"))
    # A nota provisória da observação (`observou`): na lista, e vira a da revisão.
    g2 = await _cliente(db, c, "e na preta?", BASE - timedelta(minutes=20))
    observado, _ = await _envio(db, c, g2, "Na preta também.", BASE, categoria="estoque_variacao")
    db.add(AtendimentoAvaliacao(rascunho_id=observado.id, acao="observou", nota="erro"))
    # O rascunho que não saiu (pendente).
    g3 = await _cliente(db, c, "e na branca?", BASE + timedelta(minutes=5))
    pendente = AtendimentoRascunho(
        conversa_id=c.id, mensagem_gatilho_id=g3.id, texto="Na branca não.", status="pendente"
    )
    db.add(pendente)
    await db.commit()

    lista = (await client.get(f"{URL}/revisao", params={"dia": DIA.isoformat()})).json()
    assert [i["rascunho_id"] for i in lista["itens"]] == [str(observado.id)]
    assert lista["itens"][0]["avaliacao"] is None, "a nota da observação não é a da revisão"
    assert lista["contagens"]["total"] == 1

    resp = await client.post(f"{URL}/revisao/{avaliado.id}", json={"nota": "ok"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "avaliado_por_pessoa"
    resp = await client.post(f"{URL}/revisao/{pendente.id}", json={"nota": "ok"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "nao_saiu_sozinho"
    resp = await client.post(f"{URL}/revisao/{uuid4()}", json={"nota": "ok"})
    assert resp.status_code == 404
    resp = await client.post(f"{URL}/revisao/{observado.id}", json={"nota": "ok"})
    assert resp.status_code == 200, resp.text
    av = await _avaliacao(db, observado.id)
    assert (av.acao, av.nota) == ("automatico", "ok")
    av = await _avaliacao(db, avaliado.id)
    assert (av.acao, av.nota) == ("enviou_igual", "ok"), "a nota da pessoa fica"


# ─────────────── a busca no banco ───────────────


def _resposta(
    chave: str, integ: Integration, tipo: str, pergunta: str, resposta: str, *, dias: int
) -> banco_respostas.RespostaNova:
    quando = datetime.now(UTC) - timedelta(days=dias)
    return banco_respostas.RespostaNova(
        chave=chave,
        conversa_id=None,
        resposta_msg_id=uuid4(),
        plataforma="shopee",
        canal="chat",
        integration_id=integ.id,
        loja=integ.name,
        item_id=None,
        anuncio_titulo=None,
        modelos=("F105",),
        tipo=tipo,
        pergunta=pergunta,
        resposta=resposta,
        fonte=banco_respostas.FONTE_FORA,
        perguntado_em=quando,
        respondido_em=quando,
        cliente_depois=banco_respostas.DEPOIS_RESOLVEU,
    )


async def test_busca_no_banco_acha_por_texto_assunto_e_loja(client, db, make_user, admin):
    dono = await make_user()
    kfa, _ = await _loja(db, dono, "kfa")
    atv, _ = await _loja(db, dono, "atv")
    await banco_respostas._gravar(
        db,
        [
            _resposta(
                "r1", kfa, "espec_celular", "Esse celular tem NFC?", "O F105 não tem NFC.", dias=1
            ),
            _resposta(
                "r2",
                atv,
                "conteudo_da_caixa",
                "Vem carregador junto?",
                "Vem sim, carregador e cabo na caixa.",
                dias=2,
            ),
            _resposta("r3", kfa, "espec_celular", "Tem NFC?", "Tem NFC sim, pode pagar.", dias=3),
        ],
    )
    await db.execute(
        update(AtendimentoResposta)
        .where(AtendimentoResposta.chave == "r3")
        .values(status=banco_respostas.STATUS_APOSENTADA)
    )
    await db.commit()

    async def busca(**params) -> dict:
        r = await client.get(f"{URL}/respostas", params=params)
        assert r.status_code == 200, r.text
        return r.json()

    tudo = await busca()
    assert [i["pergunta"] for i in tudo["itens"]] == [
        "Esse celular tem NFC?",
        "Vem carregador junto?",
    ]
    assert tudo["total"] == 2, "só as ativas, a mais nova primeiro"
    primeiro = tudo["itens"][0]
    assert primeiro["tipo"] == "espec_celular" and primeiro["loja"].lower() == "kfa"
    assert primeiro["tipo_nome"] == duvida.nome_do_assunto("espec_celular")
    assert primeiro["modelos"] == ["F105"] and primeiro["fonte"] == banco_respostas.FONTE_FORA
    assert primeiro["da_revisao"] is False and primeiro["conversa_id"] is None
    assert {(t["tipo"], t["n"]) for t in tudo["tipos"]} == {
        ("espec_celular", 1),
        ("conteudo_da_caixa", 1),
    }
    assert {(x["integration_id"], x["loja"].lower(), x["n"]) for x in tudo["lojas"]} == {
        (str(kfa.id), "kfa", 1),
        (str(atv.id), "atv", 1),
    }
    # Por texto (todas as palavras, sem acento nem caixa).
    assert [i["pergunta"] for i in (await busca(q="NFC"))["itens"]] == ["Esse celular tem NFC?"]
    assert [i["pergunta"] for i in (await busca(q="Carregador CABO"))["itens"]] == [
        "Vem carregador junto?"
    ]
    assert (await busca(q="carregador nfc"))["total"] == 0
    # Por assunto e por loja.
    assert [i["pergunta"] for i in (await busca(tipo="conteudo_da_caixa"))["itens"]] == [
        "Vem carregador junto?"
    ]
    assert [i["pergunta"] for i in (await busca(integration_id=str(kfa.id)))["itens"]] == [
        "Esse celular tem NFC?"
    ]
    assert (await busca(limite=1))["total"] == 2 and len((await busca(limite=1))["itens"]) == 1
    assert (await client.get(f"{URL}/respostas", params={"limite": 0})).status_code == 422
