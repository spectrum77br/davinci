"""O que o INTEGRADOR ligou entre carrinho (RF9), redes (RF7) e Direct, 02/10/2026.

- os crons: carrinho a cada 30 min e redes a cada 15 min, em minutos que não
  colidem com o :00/:30 nem com os outros crons do atendimento (e longe dos
  crons da Meta), com o `timeout` = a trava da rodada, abaixo do intervalo;
- a conversa do site e a do comentário: a caixa do chat diz onde se trata
  (nada sai por ela), a IA não é chamada (409 `canal_sem_ia`), o painel não
  procura pedido nem AdsPower;
- o status `sem_endpoint` (rota do carrinho não publicada no site) no
  vocabulário, na gravidade e no motivo da barra;
- o `direct_total` da linha da conta (a barra deixa acesa a conta que tem
  Direct mesmo com a caixa de comentários sem permissão).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    DmConversa,
    DmMensagem,
    User,
)
from app.models.marca import Marca, RedeSocial
from app.routers import atendimento as rota
from app.schemas.atendimento import CanalOut
from app.services.atendimento import canais_externos, carrinhos, gravar, redes
from app.services.atendimento.constantes import (
    CANAL_CARRINHO,
    CANAL_COMENTARIO,
    STATUS_CANAL,
    STATUS_CANAL_SEM_ENDPOINT,
)

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


# ── Crons ─────────────────────────────────────────────────────────────────


def _intervalos(minutos: set[int]) -> set[int]:
    m = sorted(minutos)
    return {(b - a) % 60 for a, b in zip(m, m[1:] + m[:1], strict=True)}


def test_crons_do_carrinho_e_das_redes_nos_minutos_certos():
    from app import worker

    carrinho = worker._ATENDIMENTO_CARRINHOS_MINUTOS
    redes_min = worker._ATENDIMENTO_REDES_MINUTOS
    assert _intervalos(carrinho) == {30}, "carrinho a cada 30 min"
    assert _intervalos(redes_min) == {15}, "redes a cada 15 min"
    ocupados = (
        {0, 30}
        | worker._ATENDIMENTO_RECLAMACOES_MINUTOS
        | worker._ATENDIMENTO_ETIQUETAS_MINUTOS
        | worker._ATENDIMENTO_AVALIACOES_MINUTOS
        | {22}  # índice de pedidos do atendimento
    )
    assert not (carrinho & ocupados) and not (redes_min & ocupados)
    assert not (carrinho & redes_min)
    # Longe dos crons que também falam com a Graph API da Meta (reconciliação
    # das postagens :05/:15…, autopostagem :02, métricas :47).
    meta = {5, 15, 25, 35, 45, 55, 2, 47}
    assert not (redes_min & meta)

    def _do_cron(fn) -> object:
        return next(c for c in worker.WorkerSettings.cron_jobs if c.coroutine is fn)

    cr = _do_cron(worker.atendimento_carrinhos)
    rd = _do_cron(worker.atendimento_redes)
    assert set(cr.minute) == carrinho and set(rd.minute) == redes_min
    # O timeout do job cobre a trava da rodada e fica abaixo do intervalo.
    assert carrinhos.TRAVA_TTL_S <= cr.timeout_s < 30 * 60
    assert redes.TRAVA_TTL_S <= rd.timeout_s < 15 * 60
    funcs = {getattr(f, "coroutine", f): f for f in worker.WorkerSettings.functions}
    assert worker.atendimento_carrinhos in funcs and worker.atendimento_redes in funcs


# ── A conversa do site e a do comentário ──────────────────────────────────


async def _conversa(
    db: AsyncSession, *, plataforma: str, canal_nome: str, ref: str, nome: str
) -> AtendimentoConversa:
    canal = await canais_externos.garantir_canal(
        db, externo_ref=ref, plataforma=plataforma, canal=canal_nome, nome=nome
    )
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=None,
        plataforma=plataforma,
        canal_nome=canal_nome,
        externo_id=f"{ref}:p1",
        conta=nome,
        comprador_id="p1",
        comprador_nome="Pessoa",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{ref}:m1",
        autor="cliente",
        texto="Qual o preço?",
        enviada_em=AGORA - timedelta(hours=2),
        tipo="texto",
        anexos=[],
        payload={},
        origem=None,
    )
    await db.commit()
    return conversa


@pytest.mark.parametrize(
    ("plataforma", "canal_nome", "ref", "trecho"),
    [
        ("site", CANAL_CARRINHO, "site:charlots", "Carrinho do site"),
        ("instagram", CANAL_COMENTARIO, "rede:instagram:1784", "cartão da publicação"),
        ("facebook", CANAL_COMENTARIO, "rede:facebook:139", "cartão da publicação"),
    ],
)
async def test_caixa_ia_e_painel_dos_canais_de_fora(
    client, db, pessoa, monkeypatch, plataforma, canal_nome, ref, trecho
):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_ia_ativa", True)
    # Mesmo com o envio LIGADO: nada sai pela caixa do chat nesses canais.
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    c = await _conversa(db, plataforma=plataforma, canal_nome=canal_nome, ref=ref, nome="X")

    d = (await client.get(f"{URL}/conversas/{c.id}")).json()
    assert d["envio"]["pode_enviar"] is False
    assert d["envio"]["codigo"] == "somente_leitura"
    assert trecho in d["envio"]["motivo"]

    r = await client.post(f"{URL}/conversas/{c.id}/responder", json={"texto": "oi"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "somente_leitura")

    r = await client.post(f"{URL}/conversas/{c.id}/rascunho")
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "canal_sem_ia")

    p = await client.get(f"{URL}/conversas/{c.id}/painel")
    assert p.status_code == 200, p.text
    corpo = p.json()
    assert corpo["adspower"]["codigo"] == "sem_perfil"
    assert corpo["envio_foto"]["pode"] is False
    # Nada saiu: nenhuma mensagem da loja na conversa.
    da_loja = (
        await db.execute(
            select(func.count())
            .select_from(AtendimentoMensagem)
            .where(AtendimentoMensagem.conversa_id == c.id, AtendimentoMensagem.autor == "loja")
        )
    ).scalar_one()
    assert da_loja == 0


# ── sem_endpoint ──────────────────────────────────────────────────────────


def _canal_out(**kw) -> CanalOut:
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "plataforma": "site",
        "canal": "carrinho",
        "modo": "observar",
        "status": "ok",
    }
    campos = {k: v for k, v in {**base, **kw}.items() if k in CanalOut.model_fields}
    return CanalOut.model_construct(**campos)


def test_sem_endpoint_no_vocabulario_e_na_barra():
    assert STATUS_CANAL_SEM_ENDPOINT == "sem_endpoint" and "sem_endpoint" in STATUS_CANAL
    g = rota._GRAVIDADE_STATUS
    assert g.index("sem_escopo") < g.index("sem_endpoint") < g.index("erro") < g.index("ok")
    m = rota._motivo_do_status(
        _canal_out(status="sem_endpoint", ultimo_erro=carrinhos.ERRO_SEM_ENDPOINT)
    )
    assert m.startswith("O site ainda não tem a rota de carrinhos: publicar o pacote")
    m = rota._motivo_do_status(
        _canal_out(status="desligado", ultimo_erro=carrinhos.ERRO_SITE_SEM_ACESSO)
    )
    assert "SITES_ESTOQUE_TOKENS" in m
    m = rota._motivo_do_status(
        _canal_out(status="sem_escopo", ultimo_erro=carrinhos.ERRO_SEM_CABECALHO)
    )
    assert m.startswith("O site recusou o token do DaVinci") and ".htaccess" in m


# ── direct_total ──────────────────────────────────────────────────────────


async def test_direct_total_na_linha_da_conta(client, db, pessoa):
    m = Marca(nome="Charlots", slug="charlots")
    db.add(m)
    await db.flush()
    rede = RedeSocial(marca_id=m.id, plataforma="instagram", conta="charlots_br")
    db.add(rede)
    await db.flush()
    # Dois Directs: um esperando, um já respondido.
    for quem, direcoes in (("p1", ["recebida"]), ("p2", ["recebida", "eco"])):
        dm = DmConversa(
            rede_social_id=rede.id,
            conta="charlots_br",
            participante_id=quem,
            participante_nome=quem,
        )
        db.add(dm)
        await db.flush()
        for n, direcao in enumerate(direcoes):
            db.add(
                DmMensagem(
                    conversa_id=dm.id,
                    mid=f"m-{quem}-{n}",
                    direcao=direcao,
                    texto="oi",
                    ocorrido_em=AGORA - timedelta(hours=2 - n),
                )
            )
    # A caixa de comentários da conta, sem permissão (token sem o escopo novo).
    canal = await canais_externos.garantir_canal(
        db,
        externo_ref="rede:instagram:1784",
        plataforma="instagram",
        canal=CANAL_COMENTARIO,
        nome="@charlots_br",
        rede_social_id=rede.id,
    )
    canal.status, canal.ultimo_erro = "sem_escopo", "falta instagram_manage_comments"
    await db.commit()
    corpo = (await client.get(f"{URL}/resumo")).json()
    [linha] = [lj for lj in corpo["lojas"] if lj.get("rede_social_id") == str(rede.id)]
    assert (linha["direct_total"], linha["direct_aguardando"], linha["status_canal"]) == (
        2,
        1,
        "sem_escopo",
    )
