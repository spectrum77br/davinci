"""Leitura do caso na TELA pelo robô (Vinicius, 22/09/2026 — chamado do 292592).

O buraco que estes testes fecham: a abertura sem caminho pela API vai pro robô,
que abre no Seller Center e devolve o protocolo — e a partir dali a plataforma
respondia na tela e NADA trazia essa fala pro painel. O Agente Shopee respondeu
em 19/09 às 21:42 e três dias depois a aba não sabia.

O que é garantido aqui:
- a fila só entrega caso ABERTO NA TELA (marca gravada por quem escreveu o nº);
- a entrega não se repete (claim) e respeita a cadência;
- `falas[]` vira resposta da plataforma COM A HORA DA TELA (o teste do 292592);
- `historico` é só contexto: não mexe na coluna "Últ. resposta";
- o eco da nossa própria fala não entra, e a repetida não duplica;
- o `/agent/lease` de hoje NÃO muda (o robô do Eduardo não precisa tocar nada);
- caso de tela sai da varredura por API, que batia nele de hora em hora — mas
  chamado com nº de API VÁLIDO continua sendo varrido (achado da revisão);
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem
from app.services import chamados as svc
from app.services import chamados_leitura

pytestmark = pytest.mark.asyncio

_TOKEN = "tok-leitura-teste"  # noqa: S105
_HDR = {"X-Agent-Token": _TOKEN}

# O caso real que originou tudo.
PROTOCOLO = "2101308949814067207"
URL = f"https://seller-service.cs.shopee.com.br/detail/{PROTOCOLO}"
RESPOSTA_DO_AGENTE = (
    "Olá,\n\nEstamos analisando sua solicitação. Retornaremos em breve!\n\n"
    "Atenciosamente,\nEquipe Shopee"
)
QUANDO_ELE_FALOU = datetime(2026, 9, 19, 21, 42, tzinfo=svc.SAO_PAULO)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("NF_AGENT_TOKEN", _TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _caso_de_tela(
    db,
    *,
    plataforma: str = "shopee",
    chamado: str = PROTOCOLO,
    url: str | None = URL,
    canal: str = "api",
    abertura_canal: str = "robo",
    abertura_status: str = "enviada",
    conta: str = "Shopee Marquezini",
    pedido: str = "292592",
    de_tela: bool = True,
) -> Chamado:
    """Um chamado como o 441aa837: aberto pela TELA pelo robô (que devolveu o
    protocolo pelo `/agent/resultado`, marcando `chamado_de_tela`), e o CHAMADO
    ainda em canal `api` — é assim que a varredura de aberturas presas o deixa."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling=pedido,
        pedido_marketplace="260826D2E44FBF",
        plataforma=plataforma,
        conta=conta,
        origem="devolucao",
        chamado=chamado,
        chamado_url=url,
        canal=canal,
        chamado_de_tela=de_tela and bool((chamado or "").strip()),
    )
    db.add(ch)
    await db.flush()
    m = svc.nova_mensagem(
        ch,
        texto="Recebemos o pacote da devolução sem o produto dentro.",
        tipo="abertura",
        direcao="enviada",
        autor_nome="robô",
        status=abertura_status,
    )
    m.canal = abertura_canal
    # 19/09 11:02, como no caso real — o Agente Shopee respondeu às 21:42 do
    # mesmo dia. A data importa: fala da plataforma ANTERIOR à nossa última
    # mensagem não vira "plataforma respondeu" (é a regra que protege o 296936).
    m.created_at = m.enviada_at = datetime(2026, 9, 19, 11, 2, tzinfo=svc.SAO_PAULO)
    db.add(m)
    await db.commit()
    await db.refresh(ch)
    return ch


# ------------------------------------------------------------------ a fila


async def test_fila_entrega_o_caso_de_tela_da_plataforma_pedida(client, db):
    ch = await _caso_de_tela(db)
    r = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert r.status_code == 200, r.text
    casos = r.json()["casos"]
    assert [c["chamado_id"] for c in casos] == [str(ch.id)]
    assert casos[0]["chamado"] == PROTOCOLO
    assert casos[0]["chamado_url"] == URL
    assert casos[0]["leitura_robo_at"] is None  # nunca foi lido
    # Nada de texto pra postar: leitura não escreve na conversa do cliente.
    assert "texto" not in casos[0]


async def test_fila_nao_entrega_pra_quem_pediu_outra_plataforma(client, db):
    await _caso_de_tela(db)
    r = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["ml"]}
    )
    assert r.status_code == 200
    assert r.json()["casos"] == []


async def test_fila_exige_plataforma(client, db):
    """Sem default de propósito: no lease, plataforma vazia significa "tudo menos
    Shopee/TikTok" — herdar isso aqui esconderia justamente o caso que motivou a
    fila."""
    await _caso_de_tela(db)
    r = await client.post("/api/chamados/agent/leitura", headers=_HDR, json={})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "kw",
    [
        {"de_tela": False},  # nº veio da API: quem lê é o sync das :25
        {"chamado": ""},  # sem protocolo não há o que abrir
    ],
)
async def test_fila_exclui(client, db, kw):
    await _caso_de_tela(db, **kw)
    r = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert r.json()["casos"] == []


async def test_fila_pega_caso_de_tela_sem_url(client, db):
    """`chamado_url` é opcional no /agent/resultado. Exigir a URL aqui deixava o
    caso sem ninguém lendo: ele já tinha saído da varredura por API. O protocolo
    basta — o robô sabe em que plataforma está."""
    await _caso_de_tela(db, url=None)
    r = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    casos = r.json()["casos"]
    assert len(casos) == 1
    assert casos[0]["chamado_url"] is None
    assert casos[0]["chamado"] == PROTOCOLO


async def test_fila_exclui_resolvido_e_encerrado(client, db):
    concluido = await _caso_de_tela(db, pedido="292001")
    concluido.resolvido = True
    encerrado = await _caso_de_tela(db, pedido="292002")
    encerrado.status_plataforma = svc.STATUS_GANHAMOS
    await db.commit()
    r = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert r.json()["casos"] == []


async def test_fila_filtra_por_conta(client, db):
    await _caso_de_tela(db, conta="Shopee Marquezini", pedido="292003")
    outro = await _caso_de_tela(db, conta="Shopee Mega", pedido="292004")
    r = await client.post(
        "/api/chamados/agent/leitura",
        headers=_HDR,
        json={"plataformas": ["shopee"], "conta": "shopee mega"},
    )
    assert [c["chamado_id"] for c in r.json()["casos"]] == [str(outro.id)]


async def test_claim_nao_reentrega_o_mesmo_caso(client, db):
    """Dois polls seguidos não dão o mesmo caso duas vezes; robô que morre no
    meio não trava nada — o claim vence sozinho em 30 min."""
    ch = await _caso_de_tela(db)
    cid = ch.id
    primeiro = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert len(primeiro.json()["casos"]) == 1
    segundo = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert segundo.json()["casos"] == []

    db.expire_all()
    linha = await db.get(Chamado, cid)
    linha.leitura_robo_claim_at = datetime.now(UTC) - chamados_leitura.CLAIM_STALE - timedelta(
        minutes=1
    )
    await db.commit()
    terceiro = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert len(terceiro.json()["casos"]) == 1


async def test_cadencia_espera_o_intervalo_depois_de_uma_leitura_boa(client, db):
    ch = await _caso_de_tela(db)
    cid = ch.id
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={"chamado_id": str(cid), "ok": True},
    )
    assert r.status_code == 200, r.text
    # Leu agora: não sai de novo.
    fila = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert fila.json()["casos"] == []

    db.expire_all()
    linha = await db.get(Chamado, cid)
    linha.leitura_robo_at = datetime.now(UTC) - chamados_leitura.INTERVALO - timedelta(minutes=1)
    await db.commit()
    fila = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert len(fila.json()["casos"]) == 1


async def test_caso_frio_le_uma_vez_por_dia(client, db):
    """Cada leitura é uma sessão de navegador logada no Seller Center. Caso em que
    ninguém fala há mais de duas semanas não merece browser de 3 em 3 h."""
    ch = await _caso_de_tela(db)
    cid = ch.id
    velho = datetime.now(UTC) - chamados_leitura.FRIO - timedelta(days=5)
    for m in (
        await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id))
    ).scalars():
        m.created_at = velho
        m.enviada_at = velho
    ch.leitura_robo_at = datetime.now(UTC) - chamados_leitura.INTERVALO - timedelta(minutes=5)
    await db.commit()
    fila = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert fila.json()["casos"] == [], "caso frio não sai só porque passaram 3 h"

    db.expire_all()
    linha = await db.get(Chamado, cid)
    linha.leitura_robo_at = datetime.now(UTC) - chamados_leitura.INTERVALO_FRIO - timedelta(
        minutes=5
    )
    await db.commit()
    fila = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert len(fila.json()["casos"]) == 1


# ------------------------------------------------- o que o robô leu de volta


async def test_fala_da_tela_vira_resposta_com_a_hora_da_plataforma(client, db):
    """O TESTE DO 292592: o Agente Shopee falou em 19/09 21:42 e é essa hora que
    tem que aparecer — não a hora em que o robô leu."""
    ch = await _caso_de_tela(db)
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [
                {
                    "texto": RESPOSTA_DO_AGENTE,
                    "quando": QUANDO_ELE_FALOU.isoformat(),
                    "autor": "Agente Shopee",
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["falas_novas"] == 1

    m = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"
            )
        )
    ).scalar_one()
    assert m.tipo == "resposta"
    assert m.autor_nome == "Agente Shopee"
    assert "Estamos analisando sua solicitação" in m.texto
    assert m.created_at.astimezone(UTC) == QUANDO_ELE_FALOU.astimezone(UTC)
    assert m.enviada_at.astimezone(UTC) == QUANDO_ELE_FALOU.astimezone(UTC)


async def test_fala_nova_aparece_na_coluna_ultima_resposta(client, db, make_user, auth_as):
    ch = await _caso_de_tela(db)
    await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [
                {"texto": RESPOSTA_DO_AGENTE, "quando": QUANDO_ELE_FALOU.isoformat()}
            ],
        },
    )
    user = await make_user(
        permissions={"chamados": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(user)
    lista = await client.get("/api/chamados", params={"q": "292592"})
    assert lista.status_code == 200, lista.text
    linha = next(x for x in lista.json()["items"] if x["id"] == str(ch.id))
    assert linha["ultima_resposta_direcao"] == "recebida"
    assert linha["ultima_resposta_at"].startswith("2026-09-20T00:42")  # 21:42 BRT em UTC


async def test_historico_e_so_contexto(client, db, make_user, auth_as):
    """A página inteira entra como contexto e NÃO conta como resposta — senão
    toda releitura de 3 em 3 h faria a linha gritar "plataforma respondeu"."""
    ch = await _caso_de_tela(db)
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={"chamado_id": str(ch.id), "historico": "Vendedor: ... \nAgente: ..."},
    )
    assert r.json()["historico_alterado"] is True
    assert r.json()["falas_novas"] == 0

    user = await make_user(
        permissions={"chamados": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(user)
    lista = await client.get("/api/chamados", params={"q": "292592"})
    linha = next(x for x in lista.json()["items"] if x["id"] == str(ch.id))
    assert linha["ultima_resposta_direcao"] != "recebida"

    # Mesma página de novo: não muda nada.
    de_novo = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={"chamado_id": str(ch.id), "historico": "Vendedor: ... \nAgente: ..."},
    )
    assert de_novo.json()["historico_alterado"] is False


async def test_eco_da_nossa_propria_fala_nao_entra(client, db):
    """A página mostra a NOSSA abertura junto. Deixá-la entrar como `recebida`
    jogaria a coluna "Últ. resposta" pro lado errado e a linha pediria gente à
    toa — por isso a regra pro robô pode ser "na dúvida, mande"."""
    ch = await _caso_de_tela(db)
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [
                {
                    "texto": "Recebemos o pacote da devolução sem o produto dentro.",
                    "quando": QUANDO_ELE_FALOU.isoformat(),
                },
                {"texto": RESPOSTA_DO_AGENTE, "quando": QUANDO_ELE_FALOU.isoformat()},
            ],
        },
    )
    assert r.json() == pytest.approx(r.json())  # (só pra ler o corpo uma vez)
    assert r.json()["ecos"] == 1
    assert r.json()["falas_novas"] == 1


async def test_reler_a_mesma_pagina_nao_duplica(client, db):
    ch = await _caso_de_tela(db)
    corpo = {
        "chamado_id": str(ch.id),
        "falas": [{"texto": RESPOSTA_DO_AGENTE, "quando": QUANDO_ELE_FALOU.isoformat()}],
    }
    primeira = await client.post(
        "/api/chamados/agent/leitura/resultado", headers=_HDR, json=corpo
    )
    segunda = await client.post(
        "/api/chamados/agent/leitura/resultado", headers=_HDR, json=corpo
    )
    assert primeira.json()["falas_novas"] == 1
    assert segunda.json()["falas_novas"] == 0
    assert segunda.json()["duplicadas"] == 1
    n = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"
            )
        )
    ).scalars().all()
    assert len(n) == 1


async def test_texto_refluido_pela_pagina_nao_duplica(client, db):
    """O Seller Center reflui o texto entre uma leitura e outra. Comparar cru
    faria a MESMA resposta entrar a cada 3 h e o caso ficaria preso em
    "plataforma respondeu"."""
    ch = await _caso_de_tela(db)
    await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [{"texto": RESPOSTA_DO_AGENTE, "quando": QUANDO_ELE_FALOU.isoformat()}],
        },
    )
    refluido = "  ".join(RESPOSTA_DO_AGENTE.split())
    de_novo = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [{"texto": refluido, "quando": QUANDO_ELE_FALOU.isoformat()}],
        },
    )
    assert de_novo.json()["falas_novas"] == 0


async def test_hora_no_futuro_e_clampada_com_aviso(client, db):
    ch = await _caso_de_tela(db)
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [
                {
                    "texto": RESPOSTA_DO_AGENTE,
                    "quando": (datetime.now(UTC) + timedelta(days=9)).isoformat(),
                }
            ],
        },
    )
    assert r.json()["falas_novas"] == 1
    msgs = (
        (
            await db.execute(
                select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)
            )
        )
        .scalars()
        .all()
    )
    fala = next(m for m in msgs if m.direcao == "recebida")
    assert fala.created_at.astimezone(UTC) <= datetime.now(UTC) + timedelta(minutes=1)
    assert any("ilegível" in (m.texto or "") for m in msgs if m.tipo == "sistema")


async def test_encerrado_poe_no_estado_encerrado_e_sai_da_fila(client, db):
    """A tela mostra o caso fechado. O chamado vai pro Encerrado (falta uma
    PESSOA concluir com lucro/prejuízo) e o robô para de gastar browser nele."""
    ch = await _caso_de_tela(db)
    cid = ch.id
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={"chamado_id": str(cid), "encerrado": True},
    )
    assert r.json()["encerrado"] is True
    db.expire_all()
    linha = await db.get(Chamado, cid)
    assert linha.status_plataforma == svc.STATUS_ENCERRADO
    assert linha.resolvido is False  # nada fecha sozinho
    assert linha.auto_ligada is False

    linha.leitura_robo_at = None
    linha.leitura_robo_claim_at = None
    await db.commit()
    fila = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert fila.json()["casos"] == []


async def test_falha_de_leitura_nao_avanca_a_ancora(client, db):
    """Leitura que não funcionou não pode parecer leitura feita — é o silêncio
    que criou este problema."""
    ch = await _caso_de_tela(db)
    cid = ch.id
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={"chamado_id": str(cid), "ok": False, "erro": "login do Seller Center caiu"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["falas_novas"] == 0
    db.expire_all()
    linha = await db.get(Chamado, cid)
    assert linha.leitura_robo_at is None, "continua como 'nunca lido'"
    assert linha.leitura_robo_claim_at is not None, "o claim vira o backoff"


async def test_resultado_de_chamado_inexistente_da_404(client, db):
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={"chamado_id": str(uuid4())},
    )
    assert r.status_code == 404


async def test_sem_token_nao_entra(client, db):
    await _caso_de_tela(db)
    r = await client.post("/api/chamados/agent/leitura", json={"plataformas": ["shopee"]})
    assert r.status_code == 401


# ------------------------------------------- o que NÃO pode ter mudado


async def test_o_lease_de_hoje_nao_muda(client, db):
    """A garantia mais importante do lote: o robô do Eduardo não precisa tocar
    uma linha. O lease continua entregando só `abrir`/`responder`, e nenhum caso
    de leitura escapa por ele."""
    await _caso_de_tela(db)
    r = await client.post(
        "/api/chamados/agent/lease", headers=_HDR, json={"limite": 10, "plataforma": "shopee"}
    )
    assert r.status_code == 200, r.text
    tarefas = r.json()["tarefas"]
    assert tarefas == [], "a abertura já está enviada; leitura não vaza pro lease"

    padrao = await client.post("/api/chamados/agent/lease", headers=_HDR, json={"limite": 10})
    assert padrao.json()["tarefas"] == []


async def test_caso_de_tela_sai_da_varredura_por_api(db):
    """Era isto que falhava de hora em hora contra a Shopee ("The return you
    queried doesn't exist") e enchia a Ouvidoria de falha falsa."""
    from app.services import chamados_devolucao_sync

    await _caso_de_tela(db)
    r = await chamados_devolucao_sync.sync_respostas(db)
    assert r["verificados"] == 0
    assert r["falhas"] == 0


async def test_caso_aberto_pela_api_continua_na_varredura(db):
    """A trava do teste acima: quem TEM caminho pela API não pode sair junto."""
    from app.services import chamados_devolucao_sync

    await _caso_de_tela(db, de_tela=False, chamado="2608310QMDCH65V")
    r = await chamados_devolucao_sync.sync_respostas(db)
    assert r["verificados"] == 1


async def test_atualizar_num_caso_de_tela_fura_a_fila(client, db, make_user, auth_as):
    """O botão Atualizar deixa de bater numa API que não conhece o protocolo:
    passa a significar "lê este antes dos outros"."""
    ch = await _caso_de_tela(db)
    cid = ch.id
    ch.leitura_robo_at = datetime.now(UTC)
    ch.leitura_robo_claim_at = datetime.now(UTC)
    await db.commit()
    user = await make_user(
        permissions={"chamados": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(user)
    r = await client.post(f"/api/chamados/{cid}/reler")
    assert r.status_code == 200, r.text
    db.expire_all()
    linha = await db.get(Chamado, cid)
    assert linha.leitura_robo_at is None
    assert linha.leitura_robo_claim_at is None
    fila = await client.post(
        "/api/chamados/agent/leitura", headers=_HDR, json={"plataformas": ["shopee"]}
    )
    assert len(fila.json()["casos"]) == 1


async def test_recebida_aceita_a_hora_da_plataforma(client, db):
    """Simetria: o monitor antigo também passa a poder dar a hora real."""
    ch = await _caso_de_tela(db)
    r = await client.post(
        "/api/chamados/agent/recebida",
        headers=_HDR,
        json={
            "chamado": PROTOCOLO,
            "texto": RESPOSTA_DO_AGENTE,
            "quando": QUANDO_ELE_FALOU.isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    m = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"
            )
        )
    ).scalar_one()
    assert m.created_at.astimezone(UTC) == QUANDO_ELE_FALOU.astimezone(UTC)


async def test_recebida_sem_quando_continua_como_antes(client, db):
    ch = await _caso_de_tela(db)
    antes = datetime.now(UTC) - timedelta(seconds=5)
    r = await client.post(
        "/api/chamados/agent/recebida",
        headers=_HDR,
        json={"chamado": PROTOCOLO, "texto": RESPOSTA_DO_AGENTE},
    )
    assert r.status_code == 200, r.text
    m = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"
            )
        )
    ).scalar_one()
    assert m.created_at.astimezone(UTC) >= antes


# ------------------------------- o que a revisão adversarial de 22/09 achou


async def test_caso_de_tela_em_canal_api_vai_pra_analise_humano(
    client, db, make_user, auth_as
):
    """Em canal `api` — o caso do 292592 — a fala resgatada pede GENTE: responder
    no Seller Center. É o que a §6.4 da spec promete ao Eduardo."""
    ch = await _caso_de_tela(db)
    await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [{"texto": RESPOSTA_DO_AGENTE, "quando": QUANDO_ELE_FALOU.isoformat()}],
        },
    )
    user = await make_user(
        permissions={"chamados": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(user)
    lista = await client.get("/api/chamados", params={"q": "292592"})
    linha = next(x for x in lista.json()["items"] if x["id"] == str(ch.id))
    assert linha["status_aba"] == svc.ABA_ANALISE_HUMANO
    assert "Seller Center" in (linha["status_aba_motivo"] or "")


async def test_caso_de_tela_em_canal_robo_vai_pra_analise_robo(client, db, make_user, auth_as):
    """A outra metade, e ela NÃO é o que a §6.4 dizia. Em canal `robo` (como o
    `_encaminhar_robo` deixa o "Não recebido" da Shopee) a regra de 19/09 manda pra
    Análise Robô — e o cérebro precisa cobrir aquela plataforma, senão a linha fica
    parada com cara de atendida. Este teste existe pra travar o comportamento REAL:
    a revisão de 22/09 apontou o risco, a regra é deliberada, e a decisão de mudá-la
    é do Vinicius."""
    ch = await _caso_de_tela(db, canal="robo", pedido="292010")
    await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [{"texto": RESPOSTA_DO_AGENTE, "quando": QUANDO_ELE_FALOU.isoformat()}],
        },
    )
    user = await make_user(
        permissions={"chamados": {"view": True, "edit": True, "delete": True}}
    )
    auth_as(user)
    lista = await client.get("/api/chamados", params={"q": "292010"})
    linha = next(x for x in lista.json()["items"] if x["id"] == str(ch.id))
    assert linha["status_aba"] == svc.ABA_ANALISE_ROBO


async def test_chamado_com_numero_de_api_nao_vira_caso_de_tela(client, db):
    """O outro achado grave. A varredura de aberturas presas REAPROVEITA a abertura
    e só troca o canal dela — sem tocar no número. Um chamado que guarda um claim do
    ML válido não pode sair da varredura por API só porque a abertura foi ao robô."""
    from app.services import chamados_devolucao_sync

    ch = await _caso_de_tela(
        db, plataforma="ml", chamado="5476523049", pedido="292011", de_tela=False
    )
    assert ch.chamado_de_tela is False
    r = await chamados_devolucao_sync.sync_respostas(db)
    assert r["verificados"] == 1, "o acompanhamento por API que funcionava continua"


async def test_agent_resultado_marca_o_protocolo_como_de_tela(client, db):
    """Quem grava o número é quem sabe de onde ele veio."""
    ch = await _caso_de_tela(db, chamado="", url=None, de_tela=False, pedido="292012")
    msg = (
        await db.execute(
            select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == ch.id)
        )
    ).scalar_one()
    msg.status = "enviando"
    await db.commit()
    cid = ch.id
    r = await client.post(
        "/api/chamados/agent/resultado",
        headers=_HDR,
        json={
            "mensagem_id": str(msg.id),
            "ok": True,
            "chamado": "2101308949814067207",
            "chamado_url": URL,
        },
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    linha = await db.get(Chamado, cid)
    assert linha.chamado_de_tela is True
    assert linha.chamado == "2101308949814067207"


async def test_fala_nova_com_texto_repetido_nao_e_engolida(client, db):
    """Dedupe só por texto engolia fala NOVA idêntica a uma antiga — e plataforma
    responde com frase padronizada o tempo todo."""
    ch = await _caso_de_tela(db)
    cid = ch.id
    primeira = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(cid),
            "falas": [{"texto": RESPOSTA_DO_AGENTE, "quando": QUANDO_ELE_FALOU.isoformat()}],
        },
    )
    assert primeira.json()["falas_novas"] == 1
    # Mesmo texto, OUTRO dia: é outra fala.
    depois = QUANDO_ELE_FALOU + timedelta(days=1)
    segunda = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(cid),
            "falas": [{"texto": RESPOSTA_DO_AGENTE, "quando": depois.isoformat()}],
        },
    )
    assert segunda.json()["falas_novas"] == 1
    assert segunda.json()["duplicadas"] == 0


async def test_data_absurda_no_passado_e_clampada(client, db):
    """Só o futuro era protegido. "19/09" lido com o ano errado enterrava a fala no
    fundo do histórico — e o dedupe tornava o erro irreversível na releitura."""
    ch = await _caso_de_tela(db)
    cid = ch.id
    r = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(cid),
            "falas": [
                {"texto": RESPOSTA_DO_AGENTE, "quando": "2019-09-19T21:42:00-03:00"}
            ],
        },
    )
    assert r.json()["falas_novas"] == 1
    msgs = (
        (await db.execute(select(ChamadoMensagem).where(ChamadoMensagem.chamado_id == cid)))
        .scalars()
        .all()
    )
    fala = next(m for m in msgs if m.direcao == "recebida")
    assert fala.created_at.year == 2026, "não pode ir pro fundo do histórico"
    assert any("ilegível" in (m.texto or "") for m in msgs if m.tipo == "sistema")


async def test_proxima_leitura_conta_a_verdade(client, db):
    """Campo informativo que mente é pior que campo ausente: o robô se organiza
    por ele. Falha volta pelo claim (30 min), não em 3 h."""
    ch = await _caso_de_tela(db)
    cid = ch.id
    falhou = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={"chamado_id": str(cid), "ok": False, "erro": "login caiu"},
    )
    prox = datetime.fromisoformat(falhou.json()["proxima_leitura_at"])
    assert prox - datetime.now(UTC) < timedelta(hours=1)

    ok = await client.post(
        "/api/chamados/agent/leitura/resultado",
        headers=_HDR,
        json={"chamado_id": str(cid), "ok": True},
    )
    prox_ok = datetime.fromisoformat(ok.json()["proxima_leitura_at"])
    assert timedelta(hours=2) < prox_ok - datetime.now(UTC) < timedelta(hours=4)
