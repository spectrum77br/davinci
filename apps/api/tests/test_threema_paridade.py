"""Paridade do legado (tarefa 16, C5): `threema_envios.legado(chave)` é igual
ao que cada ponto de envio monta HOJE, para TODOS os envios do servidor
gerenciados — quem recebe e se está ligado.

"Hoje" é a função antiga de verdade sempre que ela existe
(`ouvidoria.destinatarios`, `_recipients_margem_auto`, `recipients_auto` da
Amazon e das Devoluções, `denuncia_robo_aviso.destinatarios` com a reserva
`chamados_ia`, o `send_to_all` que cai no THREEMA_RECIPIENTS...). Onde a lista é montada no
meio de uma função maior, a expressão é copiada aqui E um teste confere que
ela continua no código — se alguém mudar o ponto de envio, este arquivo
reprova. Os botões que montam a lista dentro da rota (os 5 Informar e o
botão da regra do item 1) são conferidos também pela ROTA de verdade, com o
envio capturado.

Envio MIGRADO (o ponto passou a ler o Cadastro): a montagem antiga saiu do
código e fica aqui como CÓPIA CONGELADA do ponto antes do commit da fatia
(`_ANTES_DA_F1`). É ela que o `legado` tem de continuar reproduzindo — é a
reserva de emergência e a fonte da semente.

Três cenários (`threema_cadastro_apoio`): o "cheio", parecido com produção,
o "reservas", com as fontes principais vazias, e o "vazias", com toda linha
existindo vazia (linha vazia = ninguém, sem cair na reserva).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.config import get_settings
from app.models import OuvidoriaRobo, User, UserRole
from app.routers import informar as informar_router
from app.services import (
    chamados_ia_aviso,
    chamados_juridico,
    denuncia_relatorio_threema,
    denuncia_robo_aviso,
    devolucao_acao_avisos,
    logistica_amazon_avisos,
    margem_auto_hold,
    ouvidoria,
    threema,
    threema_envios,
)
from app.services.conferencia_shopee import threema_aviso
from app.services.threema_catalogo import CATALOGO, ENVIOS, K, chave_ouvidoria
from tests.threema_cadastro_apoio import ID, limpar_ouvidoria, montar  # noqa: F401 — fixture

_APP = Path(__file__).resolve().parent.parent / "app"
P = threema.parse_recipients


class _Captura:
    """ThreemaClient falso: guarda para quem cada `send_to_all` mandaria."""

    chamadas: list[list[str]] = []

    def __init__(self, *a, contexto=None, **kw) -> None:
        self.contexto = contexto
        self.disabled = False

    async def send_to_all(self, texto, destinos=None):
        _Captura.chamadas.append(list(destinos or []))
        return {"sent": list(destinos or []), "failed": []}


async def _tuta_antes_da_f1(db) -> list[str]:
    """tuta_devolucoes.entregar_resultado antes da F1/C8a: o usuário
    "thatcher" pelo nome, com Threema."""
    user = (
        (
            await db.execute(
                select(User)
                .where(func.lower(User.name) == "thatcher")
                .where(User.threema.is_not(None))
            )
        )
        .scalars()
        .first()
    )
    return P(user.threema if user else None)


# Cópias congeladas dos pontos que a F1 migrou (C8a, C8b e C8c: o código deles
# já não monta a lista, lê o Cadastro).
_ANTES_DA_F1 = {
    # logistica_track_sync.avisar_graves (o mesmo para o pacote de devolução)
    K.LOGISTICA_CORREIOS_GRAVE: lambda db: P(get_settings().threema_recipients),
    K.DEVOLUCOES_PACOTE_VOLTA_GRAVE: lambda db: P(get_settings().threema_recipients),
    # worker.logistica_vigia
    K.LOGISTICA_VIGIA_AUTOMACAO: lambda db: P(get_settings().nf_sem_estoque_threema_recipients),
    # marketplace_shipment_check._destinos_do_aviso
    K.LOGISTICA_LOJA_MUDA: lambda db: P(
        get_settings().shipment_muda_threema_recipients or get_settings().threema_recipients
    ),
    # devolucao_prazo.avisar_prazos
    K.DEVOLUCOES_PRAZO_CONTESTACAO: lambda db: P(get_settings().threema_recipients),
    K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR: _tuta_antes_da_f1,
    # F1/C8b — nf_auto_enfileirar._notificar_sem_estoque (18) e
    # _destinatarios_sem_estoque (19 a 22), prioridade_estoque_movimentos.
    # avisar_falhas (23): o grupo do sem estoque no .env
    **dict.fromkeys(
        (
            K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO,
            K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE,
            K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO,
            K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING,
            K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO,
            K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA,
        ),
        lambda db: P(get_settings().nf_sem_estoque_threema_recipients),
    ),
    # F1/C8c — chamados_tiktok_reembolso._destinatarios, usado pelo aviso do
    # TikTok só reembolso (13) e pela varredura das aberturas presas (14): o
    # TIKTOK_REEMBOLSO, com o grupo do sem estoque de reserva
    **dict.fromkeys(
        (K.CHAMADOS_TIKTOK_SO_REEMBOLSO, K.CHAMADOS_ABERTURAS_PRESAS),
        lambda db: P(
            get_settings().tiktok_reembolso_threema_recipients
            or get_settings().nf_sem_estoque_threema_recipients
        ),
    ),
    # vigia_estoque_familia.vigia_estoque_familia_sweep (24)
    K.PRODUTOS_VIGIA_SOMA_FAMILIA: lambda db: P(
        getattr(get_settings(), "vigia_estoque_familia_threema_recipients", "")
    ),
}


async def _informar(db, ctx: str) -> list[str]:
    row = await informar_router._config_row(db, ctx)
    return P(row.recipients if row else "")


async def _hoje(chave: str, db, monkeypatch) -> list[str]:
    """Quem recebe HOJE o envio `chave`, pelo código de hoje."""
    s = get_settings()
    robo = chave.removeprefix("ouvidoria.")
    if chave in _ANTES_DA_F1:
        r = _ANTES_DA_F1[chave](db)
        return list(await r if hasattr(r, "__await__") else r)
    if chave.startswith("ouvidoria."):
        # ouvidoria.avisar_pendentes → ouvidoria.destinatarios(robo, chave)
        return ouvidoria.destinatarios(await db.get(OuvidoriaRobo, robo), robo)[0]
    tabela = {
        # sem regra: `parse(rule) or None` → o send_to_all cai no THREEMA_RECIPIENTS
        # (o caminho de verdade, com 👤 e sem, está em test_item_1_por_regra)
        K.LOGISTICA_MENSAGEM_REGRA: lambda: P(s.threema_recipients),
        K.LOGISTICA_INFORMAR: lambda: _informar(db, "logistica"),
        K.LOGISTICA_AMAZON_INFORMAR: lambda: _informar(db, "logistica_amazon"),
        K.LOGISTICA_AMAZON_PRAZOS: lambda: logistica_amazon_avisos.recipients_auto(db),
        K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA: lambda: devolucao_acao_avisos.recipients_auto(db),
        K.DEVOLUCOES_INFORMAR: lambda: _informar(db, "devolucoes"),
        K.CHAMADOS_IA_PARADA: lambda: chamados_ia_aviso.destinatarios(db),
        K.CHAMADOS_ENCAMINHAR_JURIDICO: lambda: chamados_juridico.recipients_juridico(db),
        K.CONTROLE_ESTOQUE_INFORMAR: lambda: _informar(db, "controle_estoque"),
        K.MARGEM_PEDIDO_SEGURADO: lambda: margem_auto_hold._recipients_margem_auto(db),
        K.MARGEM_REAVALIACAO: lambda: margem_auto_hold._recipients_margem_auto(db),
        K.MARGEM_MARGEM_ALTA: lambda: margem_auto_hold._recipients_margem_auto(db),
        K.MARGEM_INFORMAR: lambda: _informar(db, "margem"),
        K.DENUNCIA_RELATORIO_DIARIO: lambda: denuncia_relatorio_threema.destinatarios(db),
        K.DENUNCIA_ROBO_PRECISA_ALGUEM: lambda: denuncia_robo_aviso.destinatarios(db),
        K.MARKETING_CONFERENCIA_SHOPEE: lambda: threema_aviso.destinatarios(db),
        K.MARKETING_CONFERENCIA_ML: lambda: threema_aviso.destinatarios(db),
        K.MARKETING_CONFERENCIA_AMAZON: lambda: threema_aviso.destinatarios(db),
    }
    r = tabela[chave]()
    if hasattr(r, "__await__"):
        r = await r
    return list(r)


async def _ativo_hoje(chave: str, db) -> bool:
    """O liga/desliga de HOJE de cada envio (o resto não tem interruptor
    próprio: depende só do que está em `depende_de`)."""
    if chave == K.DENUNCIA_RELATORIO_DIARIO:
        # denuncia_relatorio_threema.enviar_pendente
        return await ouvidoria.modo_efetivo(db, denuncia_relatorio_threema.ROBO) == "ligado"
    if chave == K.MARGEM_MARGEM_ALTA:
        # margem_auto_hold._alertar_margem_alta
        robo = await db.get(OuvidoriaRobo, "vigia_margem")
        return bool(ouvidoria.config_do_robo(robo, "vigia_margem").get("avisar_margem_alta", False))
    plataforma = {
        K.MARKETING_CONFERENCIA_SHOPEE: "shopee",
        K.MARKETING_CONFERENCIA_ML: "ml",
        K.MARKETING_CONFERENCIA_AMAZON: "amazon",
    }.get(chave)
    if plataforma:
        return threema_aviso.ligado(plataforma)
    return True


GERENCIADOS = sorted(d.chave for d in ENVIOS if d.onde_roda == "servidor" and d.gerenciado)


def test_a_paridade_cobre_todos_os_envios_gerenciados_do_servidor():
    assert len(GERENCIADOS) == 47
    robos = {chave_ouvidoria(r) for r in ouvidoria.ROBOS} - {None}
    assert robos <= set(GERENCIADOS)
    # tudo que não é Ouvidoria tem o "hoje" na tabela de _hoje (KeyError lá)
    fora_da_ouvidoria = [c for c in GERENCIADOS if not c.startswith("ouvidoria.")]
    assert len(fora_da_ouvidoria) == 47 - len(robos)


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_legado_igual_a_hoje_em_todas_as_chaves(db, monkeypatch, variante):
    await montar(db, monkeypatch, variante=variante)
    diferentes = {}
    for chave in GERENCIADOS:
        leg = await threema_envios.legado(chave, session=db)
        hoje = await _hoje(chave, db, monkeypatch)
        if chave in (K.MARGEM_PEDIDO_SEGURADO, K.MARGEM_REAVALIACAO, K.MARGEM_MARGEM_ALTA):
            # `_recipients_margem_auto` já devolve vazio fora do modo ligado: é o
            # "depende de" do envio (o cenário tem o robô ligado).
            deps = await threema_envios.dependencias(chave, db)
            assert all(ok for _, ok in deps)
        if list(leg.ids) != hoje:
            diferentes[chave] = (list(leg.ids), hoje)
        assert leg.ativo == await _ativo_hoje(chave, db), chave
        assert leg.fonte
    assert diferentes == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_o_cenario_exercita_as_reservas(db, monkeypatch):
    """Sem isto a paridade poderia passar com tudo vazio."""
    await montar(db, monkeypatch, variante="reservas")

    async def ids(chave):
        return list((await threema_envios.legado(chave, session=db)).ids)

    # TikTok vazio → NF_SEM_ESTOQUE
    assert await ids(K.CHAMADOS_TIKTOK_SO_REEMBOLSO) == P(
        get_settings().nf_sem_estoque_threema_recipients
    )
    # SHIPMENT_MUDA vazio → THREEMA_RECIPIENTS
    assert await ids(K.LOGISTICA_LOJA_MUDA) == P(get_settings().threema_recipients)
    # denuncia_robo sem linha → chamados_ia
    assert await ids(K.DENUNCIA_ROBO_PRECISA_ALGUEM) == [ID["cairo_sa"]]
    # linha existente e vazia = ninguém
    assert await ids(K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA) == []
    assert await ids(K.LOGISTICA_INFORMAR) == []
    # robô sem Avisar → env do robô; sem env → OUVIDORIA geral (vazio aqui)
    assert await ids(K.OUVIDORIA_VIGIA_IMPORTACAO) == [ID["londres"], ID["harry"]]
    assert await ids(K.OUVIDORIA_VIGIA_FLEX) == []
    # usuário pelo nome (thatcher) e campo que não existe em Settings (24)
    assert await ids(K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR) == [ID["thatcher"]]
    assert await ids(K.PRODUTOS_VIGIA_SOMA_FAMILIA) == []
    # interruptores de hoje
    assert (await threema_envios.legado(K.DENUNCIA_RELATORIO_DIARIO, session=db)).ativo
    assert (await threema_envios.legado(K.MARGEM_MARGEM_ALTA, session=db)).ativo
    assert not (await threema_envios.legado(K.MARKETING_CONFERENCIA_SHOPEE, session=db)).ativo
    assert (await threema_envios.legado(K.MARKETING_CONFERENCIA_AMAZON, session=db)).ativo


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_cheio_tem_cada_fonte_principal(db, monkeypatch):
    await montar(db, monkeypatch)

    async def leg(chave):
        return await threema_envios.legado(chave, session=db)

    assert list((await leg(K.CHAMADOS_TIKTOK_SO_REEMBOLSO)).ids) == [
        ID["heisenberg"],
        ID["cairo_sa"],
    ]
    assert list((await leg(K.OUVIDORIA_VIGIA_CORREIOS)).ids)[-1] == ID["roma"]
    assert list((await leg(K.OUVIDORIA_CHAMADO_FRETE_ML)).ids) == [ID["cairo_sa"]]  # geral
    assert "Avisar do robô" in (await leg(K.OUVIDORIA_VIGIA_IMPORTACAO)).fonte
    assert not (await leg(K.DENUNCIA_RELATORIO_DIARIO)).ativo  # robô desligado
    assert not (await leg(K.MARGEM_MARGEM_ALTA)).ativo  # caixinha desligada


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_margem_fora_do_modo_ligado_e_o_depende_de(db, monkeypatch):
    await montar(db, monkeypatch)
    robo = await db.get(OuvidoriaRobo, "vigia_margem")
    robo.modo = "silencioso"
    await db.commit()
    assert await margem_auto_hold._recipients_margem_auto(db) == []
    for chave in (K.MARGEM_PEDIDO_SEGURADO, K.MARGEM_REAVALIACAO, K.MARGEM_MARGEM_ALTA):
        leg = await threema_envios.legado(chave, session=db)
        deps = await threema_envios.dependencias(chave, db)
        assert leg.ids  # a lista continua a mesma
        assert deps and not all(ok for _, ok in deps)  # e o envio depende do modo
        efetivo = list(leg.ids) if all(ok for _, ok in deps) else []
        assert efetivo == await margem_auto_hold._recipients_margem_auto(db)


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_item_1_por_regra_pelo_send_to_all_de_verdade(db, monkeypatch):
    """Com 👤: a lista da regra. Sem 👤: o `send_to_all(texto, None)` cai no
    THREEMA_RECIPIENTS (services/threema.py) — conferido pelo envio real."""
    c = await montar(db, monkeypatch)
    for regra in c.regras.values():
        enviados: list[str] = []

        def _resp(request: httpx.Request, enviados=enviados) -> httpx.Response:
            to = dict(httpx.QueryParams(request.content.decode()))["to"]
            enviados.append(to)
            return httpx.Response(200, text="abc")

        with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
            router.post("/send_simple").mock(side_effect=_resp)
            segredo = "x"
            cli = threema.ThreemaClient(gateway_id="*TSTGW01", secret=segredo, contexto="logistica")
            # a mesma expressão dos 3 pontos do item 1
            await cli.send_to_all("t", recipients=P(regra.threema_recipients) or None)
        leg = await threema_envios.legado(K.LOGISTICA_MENSAGEM_REGRA, escopo=regra.id, session=db)
        assert list(leg.ids) == enviados, regra.status_plataforma
    sem_regra = await threema_envios.legado(K.LOGISTICA_MENSAGEM_REGRA, session=db)
    assert list(sem_regra.ids) == P(get_settings().threema_recipients)
    # escopo que não é regra: a lista padrão
    lixo = await threema_envios.legado(K.LOGISTICA_MENSAGEM_REGRA, escopo="nao-e-uuid", session=db)
    assert lixo.ids == sem_regra.ids


@pytest.mark.asyncio
async def test_lista_repetida_no_env_sai_igual_a_hoje(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "threema_recipients", "TSTA0001, tsta0001;TSTB0002")
    leg = await threema_envios.legado(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (
        list(leg.ids)
        == P(get_settings().threema_recipients)
        == [
            "TSTA0001",
            "TSTA0001",
            "TSTB0002",
        ]
    )


@pytest.mark.asyncio
async def test_mac_e_informativos_nao_tem_lista_no_servidor(db):
    for d in ENVIOS:
        if d.onde_roda == "servidor" and d.gerenciado:
            continue
        leg = await threema_envios.legado(d.chave, session=db)
        assert leg.ids == (), d.chave
        if d.onde_roda == "mac":
            assert d.legado.env_mac and d.legado.env_mac in leg.fonte
    with pytest.raises(KeyError, match="envio desconhecido"):
        await threema_envios.legado("logistica.nao_existe", session=db)


# Onde a lista é montada no meio de uma função maior, a cópia acima vale só
# enquanto a expressão continuar no código.
_EXPRESSOES: dict[str, tuple[str, ...]] = {
    "services/logistica_bling.py": ("threema.parse_recipients(rule.threema_recipients) or None",),
    "routers/logistica.py": (
        # botão da linha do pedido
        "threema.parse_recipients(rule.threema_recipients) or None",
        # botão da regra (a rota também é conferida de verdade abaixo)
        "threema.parse_recipients(s.threema_recipients) or None",
    ),
    # os 5 Informar (`_informar` acima; a rota também é conferida abaixo)
    "routers/informar.py": ('threema.parse_recipients(row.recipients if row else "")',),
    "services/threema.py": ("recipients or parse_recipients(get_settings().threema_recipients)",),
}


@pytest.mark.parametrize("arquivo", sorted(_EXPRESSOES))
def test_a_expressao_copiada_continua_no_ponto_de_envio(arquivo):
    fonte = (_APP / arquivo).read_text(encoding="utf-8")
    for expressao in _EXPRESSOES[arquivo]:
        assert expressao in fonte, (arquivo, expressao)


# ─────────────────────────────────────────── pela rota de verdade


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_informar_pela_rota_de_verdade(db, monkeypatch, client, make_user, auth_as, variante):
    """`POST /api/informar/{ctx}/enviar` dos 5 contextos manda para
    `legado(chave)` — e, com a lista vazia, não manda (422)."""
    await montar(db, monkeypatch, variante=variante)
    auth_as(await make_user(role=UserRole.ADMIN))
    monkeypatch.setattr(threema, "ThreemaClient", _Captura)
    diferentes = {}
    for ctx in sorted(informar_router._CONTEXTOS_ENVIO):
        chave = informar_router._CHAVE_ENVIO[ctx]
        _Captura.chamadas = []
        r = await client.post(f"/api/informar/{ctx}/enviar")
        leg = list((await threema_envios.legado(chave, session=db)).ids)
        if leg:
            assert r.status_code == 200, (ctx, r.text)
            assert _Captura.chamadas, ctx
        else:
            assert r.status_code == 422, (ctx, r.text)
            assert r.json()["detail"]["code"] == "sem_destinatarios"
        # o Informar manda várias mensagens: todas para a mesma lista
        for enviada in _Captura.chamadas or [[]]:
            if enviada != leg:
                diferentes[ctx] = (leg, enviada)
    assert diferentes == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
@pytest.mark.parametrize("variante", ["cheio", "reservas", "vazias"])
async def test_botao_da_regra_pela_rota_de_verdade(
    db, monkeypatch, client, make_user, auth_as, variante
):
    """`POST /api/logistica/status/{id}/enviar-threema` sem escolha avulsa
    manda para `legado(item 1, escopo=regra)`: o 👤 da regra, ou o
    THREEMA_RECIPIENTS pelo `send_to_all` de verdade quando ela não tem 👤."""
    c = await montar(db, monkeypatch, variante=variante)
    s = get_settings()
    monkeypatch.setattr(s, "threema_gateway_id", "*TSTGW01")
    monkeypatch.setattr(s, "threema_gateway_secret", "s3cr3t-paridade")
    monkeypatch.setattr(s, "threema_separate_chats", False)
    monkeypatch.setattr(s, "threema_context_channels", {})
    auth_as(await make_user(role=UserRole.ADMIN))
    com_mensagem = [r for r in c.regras.values() if (r.mensagem_threema or "").strip()]
    assert len(com_mensagem) == 2
    for regra in com_mensagem:
        enviados: list[str] = []

        def _resp(request: httpx.Request, enviados=enviados) -> httpx.Response:
            enviados.append(dict(httpx.QueryParams(request.content.decode()))["to"])
            return httpx.Response(200, text="abc")

        with respx.mock(base_url=threema.THREEMA_API_BASE, assert_all_called=False) as router:
            router.post("/send_simple").mock(side_effect=_resp)
            r = await client.post(f"/api/logistica/status/{regra.id}/enviar-threema")
        leg = list(
            (
                await threema_envios.legado(K.LOGISTICA_MENSAGEM_REGRA, escopo=regra.id, session=db)
            ).ids
        )
        assert enviados == leg, (regra.status_plataforma, r.status_code, r.text)
        assert r.status_code == (200 if leg else 422), r.text


def test_os_migrados_tem_a_copia_congelada_e_nao_montam_mais_a_lista():
    """Todo envio migrado tem a cópia do ponto de antes (`_ANTES_DA_F1`), e o
    ponto não monta mais a lista: a expressão antiga saiu do código."""
    migrados = {d.chave for d in ENVIOS if d.migrado}
    assert migrados == set(_ANTES_DA_F1)
    assert CATALOGO[K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR].legado.usuario_nome == "thatcher"
    fora = {
        "worker.py": "get_settings().nf_sem_estoque_threema_recipients",
        "services/devolucao_prazo.py": "get_settings().threema_recipients",
        "services/logistica_track_sync.py": "threema_recipients",
        "services/marketplace_shipment_check.py": "threema_recipients",
        "services/tuta_devolucoes.py": "User.name",
        # F1/C8b: nenhum dos dois monta lista nenhuma
        "services/nf_auto_enfileirar.py": "parse_recipients",
        "services/prioridade_estoque_movimentos.py": "parse_recipients",
        # F1/C8c: o `_destinatarios` do TikTok saiu (a varredura das aberturas
        # presas também o usava) e o vigia da soma não lê mais o .env
        "services/chamados_tiktok_reembolso.py": "parse_recipients",
        "services/chamados_pendencias.py": "_destinatarios",
        "services/vigia_estoque_familia.py": "threema_recipients",
    }
    for arquivo, expressao in fora.items():
        assert expressao not in (_APP / arquivo).read_text(encoding="utf-8"), arquivo
