"""Chamado do ML com a consulta do Fale conosco ligada (Vinicius, 11/11/2026).

297650 (ML Marquezini): o chamado segue a mediação 5584853622 pela API, mas a
conversa que importava estava na consulta 489678080, aberta pelo Fale conosco
pra pedir revisão depois do "perdemos". A IA dizia "cobrei o Flavio" e a coluna
da conversa parava em 16/10: nada ligava a consulta ao chamado. E a leitura da
mediação pela API parou 21 dias depois do "perdemos", antes de o mediador
voltar a falar (28/10).

O que é garantido aqui:
- a consulta é ligada pelo campo (link ou número), pela Observação/instrução e
  pela análise da IA, nunca com o número do próprio chamado;
- o executor de leitura recebe a consulta ligada como `ml_consulta`, mesmo com a
  mediação decidida, e o que ele lê entra no chamado;
- a Shopee continua exigindo o ID longo do Portal;
- a mediação do ML segue sendo lida pela API enquanto a IA espera a plataforma.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoLeitor, ChamadoMensagem
from app.services import chamados as svc
from app.services import chamados_devolucao_sync as sync
from app.services import chamados_leitura

pytestmark = pytest.mark.asyncio

_LEITOR = "tok-leitor-ml-ligada"  # noqa: S105
_HDR = {"X-Agent-Token": _LEITOR}
PERM = {"chamados": {"view": True, "edit": True, "delete": True}}

MEDIACAO = "5584853622"
CONSULTA = "489678080"


@pytest.fixture(autouse=True)
async def _leitor(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", "tok-nf-teste")
    db.add(
        ChamadoLeitor(
            nome="Executor de leitura (teste ML ligada)",
            token_hash=hashlib.sha256(_LEITOR.encode()).hexdigest(),
        )
    )
    await db.commit()


async def _mediacao(
    db,
    *,
    plataforma: str = "ml",
    conta: str = "ML Marquezini",
    chamado: str = MEDIACAO,
    consulta: str | None = None,
    status_plataforma: str | None = svc.STATUS_PERDEMOS,
    decidido_ha: timedelta = timedelta(days=36),
    resolvido: bool = False,
) -> Chamado:
    """Como o 297650: devolução contestada pela API, decidida a favor do comprador."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling="297650",
        pedido_marketplace="2000018490695252",
        plataforma=plataforma,
        conta=conta,
        origem="logistica",
        chamado=chamado,
        canal="api",
        consulta_portal=consulta,
        status_plataforma=status_plataforma,
        status_plataforma_at=datetime.now(UTC) - decidido_ha if status_plataforma else None,
        resolvido=resolvido,
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ch


async def _analise(db, ch: Chamado, texto: str, *, ha: timedelta = timedelta(days=2)) -> None:
    m = svc.nova_mensagem(
        ch,
        texto=f"Análise da IA de Chamado [x]: {texto}",
        tipo="analise",
        direcao="sistema",
        autor_nome="IA de Chamado",
        status="registrada",
    )
    m.canal = "robo"
    m.created_at = datetime.now(UTC) - ha
    db.add(m)
    await db.commit()


async def _fila(client, **body) -> list[dict]:
    r = await client.post("/api/chamados/agent/leitor/fila", headers=_HDR, json=body)
    assert r.status_code == 200, r.text
    return r.json()["casos"]


# ------------------------------------------------------------ ligar a consulta


@pytest.mark.parametrize(
    ("texto", "achada"),
    [
        # instrução do Cairo de 09/11, com a mediação junto
        (f"ver se o ML respondeu na consulta {CONSULTA} ou na mediação {MEDIACAO}", CONSULTA),
        (f"Consulta número: {CONSULTA}", CONSULTA),
        (f"consulta nº {CONSULTA}", CONSULTA),
        (f"https://www.mercadolivre.com.br/cases/detail/{CONSULTA}?x=1", CONSULTA),
        (f"na mediação {MEDIACAO} o Carlos pediu o valor", None),
        ("sem número nenhum", None),
    ],
)
async def test_extrai_a_consulta_do_ml_do_texto(texto, achada):
    assert chamados_leitura.extrair_consulta_ml(texto) == achada


async def test_liga_pelo_texto_so_no_ml_e_nunca_o_proprio_chamado(db):
    ch = await _mediacao(db)
    assert chamados_leitura.ligar_consulta_do_texto(ch, f"na consulta {CONSULTA}") is True
    assert ch.consulta_portal == CONSULTA
    # Já ligada: o texto seguinte não troca.
    assert chamados_leitura.ligar_consulta_do_texto(ch, "na consulta 492602634") is False
    assert ch.consulta_portal == CONSULTA

    outro = await _mediacao(db, chamado="491000001")
    assert chamados_leitura.ligar_consulta_do_texto(outro, "consulta 491000001") is False
    assert outro.consulta_portal is None

    # A consulta aberta na tela já é o próprio chamado.
    tela = await _mediacao(db, chamado="491000002")
    tela.chamado_de_tela = True
    assert chamados_leitura.ligar_consulta_do_texto(tela, f"consulta {CONSULTA}") is False

    # Shopee segue pedindo o ID do Portal (19 dígitos).
    shopee = await _mediacao(db, plataforma="shopee", conta="Shopee Minas", chamado="2601")
    assert chamados_leitura.ligar_consulta_do_texto(shopee, f"consulta {CONSULTA}") is False


async def test_campo_liga_pelo_link_ou_numero_e_confere_o_tamanho(client, make_user, auth_as, db):
    auth_as(await make_user(role="user", permissions=PERM))
    ml = await _mediacao(db)
    r = await client.patch(
        f"/api/chamados/{ml.id}",
        json={"consulta_portal": f"https://www.mercadolivre.com.br/cases/detail/{CONSULTA}"},
    )
    assert r.status_code == 200 and r.json()["consulta_portal"] == CONSULTA, r.text

    shopee = await _mediacao(db, plataforma="shopee", conta="Shopee Minas", chamado="2601")
    r = await client.patch(f"/api/chamados/{shopee.id}", json={"consulta_portal": CONSULTA})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "chamado_consulta_invalida"
    portal = "https://seller-service.cs.shopee.com.br/detail/2103108212314644514"
    r = await client.patch(f"/api/chamados/{shopee.id}", json={"consulta_portal": portal})
    assert r.status_code == 200 and r.json()["consulta_portal"] == "2103108212314644514"

    r = await client.patch(f"/api/chamados/{ml.id}", json={"consulta_portal": ""})
    assert r.status_code == 200 and r.json()["consulta_portal"] is None


async def test_instrucao_com_a_consulta_liga(client, make_user, auth_as, db):
    auth_as(await make_user(role="user", permissions=PERM))
    ch = await _mediacao(db)
    r = await client.post(
        f"/api/chamados/{ch.id}/instrucao",
        json={"texto": f"Pedido da equipe: ver se respondeu na consulta {CONSULTA}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["consulta_portal"] == CONSULTA


async def test_analise_da_ia_que_cita_a_consulta_liga_e_avisa(client, db):
    ch = await _mediacao(db)
    r = await client.post(
        "/api/chamados/agent/analise",
        headers={"X-Agent-Token": "tok-nf-teste"},
        json={
            "chamado_id": str(ch.id),
            "classe": "ml_revisao",
            "resumo": f"Feito na tela: retomei a consulta {CONSULTA} e cobrei o resultado.",
            "acao": "esperar",
        },
    )
    assert r.status_code == 200, r.text
    await db.refresh(ch)
    assert ch.consulta_portal == CONSULTA
    sistema = (
        await db.execute(
            select(ChamadoMensagem.texto).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "sistema"
            )
        )
    ).scalars().all()
    assert any(f"Consulta {CONSULTA} do ML ligada" in t for t in sistema)


# ------------------------------------------------------------ o robô lê a consulta


async def test_consulta_ligada_vai_pro_executor_mesmo_com_a_mediacao_decidida(client, db):
    ch = await _mediacao(db, consulta=CONSULTA)
    assert await _fila(client, contas=[], ml_lojas=["forpaper"], espiar=True) == []
    (caso,) = await _fila(client, contas=[], ml_lojas=["marquezini"])
    assert caso["chamado_id"] == str(ch.id)
    assert caso["tipo"] == "ml_consulta"
    assert caso["chamado"] == CONSULTA
    assert caso["chamado_url"] == f"https://www.mercadolivre.com.br/cases/detail/{CONSULTA}"

    r = await client.post(
        "/api/chamados/agent/leitor/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [
                {
                    "texto": "Olá! Juntei as provas e enviei para análise prioritária.",
                    "quando": "2026-11-04T00:00:00-03:00",
                    "autor": "Flavio",
                    "so_dia": True,
                }
            ],
            "historico": f"Consulta {CONSULTA}\n\n[4 de novembro] Flavio:\nOlá! Juntei as provas…",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["falas_novas"] == 1
    recebidas = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"
            )
        )
    ).scalars().all()
    assert [m.autor_nome for m in recebidas] == ["Flavio"]


@pytest.mark.parametrize("kw", [{"resolvido": True}, {"plataforma": "shopee"}, {"consulta": None}])
async def test_consulta_ligada_fora_da_fila(client, db, kw):
    base = {"consulta": CONSULTA}
    await _mediacao(db, **{**base, **kw})
    assert await _fila(client, contas=[], ml_lojas=["marquezini"], espiar=True) == []


async def test_executor_nao_escreve_em_mediacao_sem_consulta(client, db):
    ch = await _mediacao(db)
    r = await client.post(
        "/api/chamados/agent/leitor/resultado",
        headers=_HDR,
        json={"chamado_id": str(ch.id), "falas": [], "historico": "x"},
    )
    assert r.status_code == 409


# ------------------------------------------------- mediação lida depois da decisão


async def _na_varredura(db, ch: Chamado) -> bool:
    ids = (
        await db.execute(select(Chamado.id).where(sync._le_na_varredura(datetime.now(UTC))))
    ).scalars().all()
    return ch.id in ids


async def test_mediacao_do_ml_segue_lida_enquanto_a_ia_espera(db):
    """36 dias depois do "perdemos": antes saía da leitura no 21º dia."""
    esperando = await _mediacao(db)
    await _analise(db, esperando, "Cobrei o resultado. → aguardar a plataforma")
    assert await _na_varredura(db, esperando)

    # A IA não está esperando a plataforma: sai como antes.
    parada = await _mediacao(db, chamado="5584853699")
    await _analise(db, parada, "Precisa de decisão de valor. → precisa de humano")
    assert not await _na_varredura(db, parada)

    # A última análise é a que vale.
    await _analise(db, esperando, "Precisa de decisão. → precisa de humano", ha=timedelta(hours=1))
    assert not await _na_varredura(db, esperando)

    # Shopee/TikTok seguem com o limite de 21 dias.
    shopee = await _mediacao(db, plataforma="shopee", conta="Shopee Minas", chamado="2601")
    await _analise(db, shopee, "Cobrei. → aguardar a plataforma")
    assert not await _na_varredura(db, shopee)

