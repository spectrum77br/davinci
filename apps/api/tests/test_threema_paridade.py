"""Paridade do legado (tarefa 16, C5): `threema_envios.legado(chave)` é igual
ao que cada ponto de envio monta HOJE, para TODOS os envios do servidor
gerenciados — quem recebe e se está ligado.

"Hoje" é a função antiga de verdade sempre que ela existe (o `send_to_all`
que cai no THREEMA_RECIPIENTS...). Onde a lista é montada no
meio de uma função maior, a expressão é copiada aqui E um teste confere que
ela continua no código — se alguém mudar o ponto de envio, este arquivo
reprova. Os botões que montam a lista dentro da rota (os 5 Informar e o
botão da regra do item 1) são conferidos também pela ROTA de verdade, com o
envio capturado.

Envio MIGRADO (o ponto passou a ler o Cadastro): a montagem antiga saiu do
código e fica aqui como CÓPIA CONGELADA do ponto antes do commit da fatia
(`_ANTES_DA_F1`, `_ANTES_DA_F2`, `_ANTES_DA_F3`, `_ANTES_DA_F4`). É ela que o `legado` tem
de continuar reproduzindo — é a reserva de emergência e a fonte da semente.

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
from app.models import OuvidoriaRobo, ThreemaInformarConfig, User, UserRole
from app.routers import informar as informar_router
from app.services import (
    denuncia_relatorio_threema,
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


async def _amazon_auto_antes_da_f2(db) -> list[str]:
    """logistica_amazon_avisos.recipients_auto antes da F2/C9a: a linha
    `logistica_amazon_auto` do Informar (sem linha = ninguém)."""
    row = (
        await db.execute(
            select(ThreemaInformarConfig).where(
                ThreemaInformarConfig.contexto == "logistica_amazon_auto"
            )
        )
    ).scalar_one_or_none()
    return P(row.recipients if row else "")


async def _devolucoes_auto_antes_da_f2(db) -> list[str]:
    """devolucao_acao_avisos.recipients_auto antes da F2/C9c: a linha
    `devolucoes_auto` do Informar (sem linha = ninguém)."""
    row = (
        await db.execute(
            select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == "devolucoes_auto")
        )
    ).scalar_one_or_none()
    return P(row.recipients if row else "")


async def _linha(db, ctx: str) -> list[str]:
    """`parse(row.recipients if row else "")` da linha do Informar: a
    expressão dos pontos que liam um contexto só (sem linha = ninguém)."""
    row = (
        await db.execute(select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == ctx))
    ).scalar_one_or_none()
    return P(row.recipients if row else "")


async def _denuncia_robo_antes_da_f2(db) -> list[str]:
    """denuncia_robo_aviso.destinatarios antes da F2/C9d: a linha
    `denuncia_robo`; sem linha salva, a da IA de Chamado (`chamados_ia`).
    Linha salva vazia = ninguém (não cai na reserva)."""
    for ctx in ("denuncia_robo", "chamados_ia"):
        row = (
            await db.execute(
                select(ThreemaInformarConfig).where(ThreemaInformarConfig.contexto == ctx)
            )
        ).scalar_one_or_none()
        if row is not None:
            return P(row.recipients)
    return []


# Cópias congeladas dos pontos que a F2 migrou. O "Enviar agora" dos Informar
# migrados (C9a, C9b e C9c) lia a linha do contexto na rota (`_config_row` + a
# expressão de `_EXPRESSOES`, que continua lá para os contextos ainda não
# migrados).
_ANTES_DA_F2 = {
    K.LOGISTICA_INFORMAR: lambda db: _informar(db, "logistica"),
    K.LOGISTICA_AMAZON_INFORMAR: lambda db: _informar(db, "logistica_amazon"),
    K.LOGISTICA_AMAZON_PRAZOS: _amazon_auto_antes_da_f2,
    # C9b
    K.CONTROLE_ESTOQUE_INFORMAR: lambda db: _informar(db, "controle_estoque"),
    # C9c: o prazo de resposta da loja (devolucao_acao_avisos.run) e o
    # Informar das Devoluções
    K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA: _devolucoes_auto_antes_da_f2,
    K.DEVOLUCOES_INFORMAR: lambda db: _informar(db, "devolucoes"),
    # C9d: chamados_ia_aviso.destinatarios (15), chamados_juridico.
    # recipients_juridico (16), denuncia_relatorio_threema.destinatarios (43)
    # e denuncia_robo_aviso.destinatarios com a reserva `chamados_ia` (44)
    K.CHAMADOS_IA_PARADA: lambda db: _linha(db, "chamados_ia"),
    K.CHAMADOS_ENCAMINHAR_JURIDICO: lambda db: _linha(db, "juridico"),
    K.DENUNCIA_RELATORIO_DIARIO: lambda db: _linha(db, "denuncia_relatorio"),
    K.DENUNCIA_ROBO_PRECISA_ALGUEM: _denuncia_robo_antes_da_f2,
    # C9e: conferencia_shopee/threema_aviso.destinatarios — a MESMA linha
    # `conferencia_shopee` para as três plataformas (45 a 47)
    **dict.fromkeys(
        (
            K.MARKETING_CONFERENCIA_SHOPEE,
            K.MARKETING_CONFERENCIA_ML,
            K.MARKETING_CONFERENCIA_AMAZON,
        ),
        lambda db: _linha(db, "conferencia_shopee"),
    ),
}


def _robo_antes_da_f3(robo: str):
    """ouvidoria.avisar_pendentes antes da F3/C10a: `ouvidoria.destinatarios`
    — a lista "Avisar" do robô na tela; sem ela, o .env do robô; sem esse, o
    OUVIDORIA_THREEMA_RECIPIENTS geral. (A função continua no código como
    a configuração antiga; a cópia congela o que ela fazia.)"""

    async def _hoje(db) -> list[str]:
        row = await db.get(OuvidoriaRobo, robo)
        if row is not None:
            ids = P(row.threema_recipients)
            if ids:
                return ids
        s = get_settings()
        d = ouvidoria.ROBOS.get(robo)
        if d is not None and d.env_threema_recipients:
            ids = P(getattr(s, d.env_threema_recipients, None))
            if ids:
                return ids
        return P(s.ouvidoria_threema_recipients)

    return _hoje


async def _margem_auto_antes_da_f3(db) -> list[str]:
    """margem_auto_hold._recipients_margem_auto antes da F3/C10b: a lista do
    Robô da Margem (a mesma `ouvidoria.destinatarios` do aviso das pendências
    dele), só com o robô `ligado` (sem linha = `ligado`). O modo é o "depende
    de" dos envios 25 a 27 (`test_margem_fora_do_modo_ligado_e_o_depende_de`)."""
    row = await db.get(OuvidoriaRobo, "vigia_margem")
    modo = row.modo if row is not None else await ouvidoria.modo(db, "vigia_margem")
    if modo != "ligado":
        return []
    return await _robo_antes_da_f3("vigia_margem")(db)


# Cópias congeladas dos pontos que a F3 migrou: o aviso das pendências de cada
# robô da Ouvidoria (C10a, itens 29 a 33 e 35 a 42; o 34, do Robô da Margem,
# na C10b), os avisos na hora do Robô da Margem (C10b, 25 a 27) e o "Enviar
# agora" do Informar da Margem (C10b, 28; a rota lia a linha do contexto).
_ANTES_DA_F3 = {
    **{
        c: _robo_antes_da_f3(r)
        for r in ouvidoria.ROBOS
        if (c := chave_ouvidoria(r)) is not None and CATALOGO[c].migrado
    },
    **dict.fromkeys(
        (K.MARGEM_PEDIDO_SEGURADO, K.MARGEM_REAVALIACAO, K.MARGEM_MARGEM_ALTA),
        _margem_auto_antes_da_f3,
    ),
    K.MARGEM_INFORMAR: lambda db: _informar(db, "margem"),
}
# Cópia congelada dos 3 pontos que a F4 migrou (C11, item 1): o motor da
# Logística, o botão da regra e o da linha mandavam
# `send_to_all(texto, recipients=parse_recipients(rule.threema_recipients) or
# None)` — com 👤, a lista da regra; sem, o `send_to_all` caía no
# THREEMA_RECIPIENTS (services/threema.py). Aqui, a lista padrão (sem regra);
# a de cada regra, pelo `send_to_all` de verdade, está em
# `test_item_1_por_regra_pelo_send_to_all_de_verdade`.
_ANTES_DA_F4 = {
    K.LOGISTICA_MENSAGEM_REGRA: lambda db: P(get_settings().threema_recipients),
}
_ANTES = {**_ANTES_DA_F1, **_ANTES_DA_F2, **_ANTES_DA_F3, **_ANTES_DA_F4}


def _conferencia_ligada_antes_da_f2(chave: str) -> bool:
    """conferencia_shopee/threema_aviso.ligado antes da F2/C9e: a chave da
    plataforma no .env (desligadas de fábrica)."""
    cfg = get_settings()
    return bool(
        {
            K.MARKETING_CONFERENCIA_SHOPEE: cfg.conferencia_shopee_threema,
            K.MARKETING_CONFERENCIA_ML: cfg.conferencia_ml_threema,
            K.MARKETING_CONFERENCIA_AMAZON: cfg.conferencia_amazon_threema,
        }[chave]
    )


async def _hoje(chave: str, db, monkeypatch) -> list[str]:
    """Quem recebe HOJE o envio `chave`, pelo código de hoje."""
    robo = chave.removeprefix("ouvidoria.")
    if chave in _ANTES:
        r = _ANTES[chave](db)
        return list(await r if hasattr(r, "__await__") else r)
    if chave.startswith("ouvidoria."):
        # ouvidoria.avisar_pendentes → ouvidoria.destinatarios(robo, chave)
        return ouvidoria.destinatarios(await db.get(OuvidoriaRobo, robo), robo)[0]
    # Desde a F4/C11 todo envio do servidor gerenciado migrou: o "hoje" de
    # cada um é a cópia congelada do ponto antes da fatia.
    raise KeyError(chave)


async def _ativo_hoje(chave: str, db) -> bool:
    """O liga/desliga de HOJE de cada envio (o resto não tem interruptor
    próprio: depende só do que está em `depende_de`)."""
    if chave == K.DENUNCIA_RELATORIO_DIARIO:
        # denuncia_relatorio_threema.enviar_pendente
        return await ouvidoria.modo_efetivo(db, denuncia_relatorio_threema.ROBO) == "ligado"
    if chave == K.MARGEM_MARGEM_ALTA:
        # margem_auto_hold._alertar_margem_alta antes da F3/C10b: a caixinha
        robo = await db.get(OuvidoriaRobo, "vigia_margem")
        return bool(ouvidoria.config_do_robo(robo, "vigia_margem").get("avisar_margem_alta", False))
    if chave in threema_aviso.CHAVE_ENVIO.values():
        return _conferencia_ligada_antes_da_f2(chave)
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
            # `_recipients_margem_auto` já devolvia vazio fora do modo ligado: é
            # o "depende de" do envio (o cenário tem o robô ligado).
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
    assert await _margem_auto_antes_da_f3(db) == []
    for chave in (K.MARGEM_PEDIDO_SEGURADO, K.MARGEM_REAVALIACAO, K.MARGEM_MARGEM_ALTA):
        leg = await threema_envios.legado(chave, session=db)
        deps = await threema_envios.dependencias(chave, db)
        assert leg.ids  # a lista continua a mesma
        assert deps and not all(ok for _, ok in deps)  # e o envio depende do modo
        efetivo = list(leg.ids) if all(ok for _, ok in deps) else []
        assert efetivo == await _margem_auto_antes_da_f3(db)
    # sem linha do robô no banco: `ligado` (o `ouvidoria.modo`), como antes
    await db.delete(robo)
    await db.commit()
    assert await _margem_auto_antes_da_f3(db) == list(
        (await threema_envios.legado(K.MARGEM_PEDIDO_SEGURADO, session=db)).ids
    )
    deps = await threema_envios.dependencias(K.MARGEM_PEDIDO_SEGURADO, db)
    assert all(ok for _, ok in deps)


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_item_1_por_regra_pelo_send_to_all_de_verdade(db, monkeypatch):
    """Com 👤: a lista da regra. Sem 👤: o `send_to_all(texto, None)` cai no
    THREEMA_RECIPIENTS (services/threema.py) — conferido pelo envio real, com
    a expressão dos 3 pontos do item 1 antes da F4/C11 (cópia congelada)."""
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
            # a expressão dos 3 pontos do item 1 antes da F4/C11
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
    """`POST /api/logistica/status/{id}/enviar-threema` sem semente (o código
    da F4 publicado antes do `semear`) manda para `legado(item 1,
    escopo=regra)`: o 👤 da regra, ou o THREEMA_RECIPIENTS quando ela não tem
    👤 — pelo `send_to_all` de verdade; lista vazia → 422."""
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
    """Todo envio migrado tem a cópia do ponto de antes (`_ANTES_DA_F1`,
    `_ANTES_DA_F2`, `_ANTES_DA_F3`), e o ponto não monta mais a lista: a expressão antiga
    saiu do código (no Informar ela fica só para os contextos não migrados:
    a rota manda o migrado pelo Cadastro, conferido pela rota de verdade em
    `test_threema_pontos_informar`)."""
    migrados = {d.chave for d in ENVIOS if d.migrado}
    assert migrados == set(_ANTES)
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
        # F2/C9a: os prazos da Amazon não leem mais a linha do Informar
        "services/logistica_amazon_avisos.py": "parse_recipients",
        # F2/C9c: nem o prazo de resposta das Devoluções
        "services/devolucao_acao_avisos.py": "parse_recipients",
        # F2/C9d: nem a IA de Chamado parada, o jurídico, o relatório e o robô
        # da Denúncia (nenhum lê mais a linha do Informar)
        "services/chamados_ia_aviso.py": "ThreemaInformarConfig",
        "services/chamados_juridico.py": "ThreemaInformarConfig",
        "services/denuncia_relatorio_threema.py": "ThreemaInformarConfig",
        "services/denuncia_robo_aviso.py": "ThreemaInformarConfig",
        # F2/C9e: nem a Conferência (a linha do Informar e as chaves do .env)
        "services/conferencia_shopee/threema_aviso.py": "ThreemaInformarConfig",
        # F4/C11: o motor da Logística não lê mais o 👤 das regras
        "services/logistica_bling.py": "threema_recipients",
    }
    for arquivo in (
        "services/chamados_ia_aviso.py",
        "services/chamados_juridico.py",
        "services/denuncia_relatorio_threema.py",
        "services/denuncia_robo_aviso.py",
        "services/conferencia_shopee/threema_aviso.py",
    ):
        assert "parse_recipients" not in (_APP / arquivo).read_text(encoding="utf-8"), arquivo
    for arquivo, expressao in fora.items():
        assert expressao not in (_APP / arquivo).read_text(encoding="utf-8"), arquivo
    # F4/C11: os 3 pontos do item 1 não montam mais a lista nem criam o
    # cliente, e o POST/PATCH das regras não gravam mais o 👤
    for arquivo in ("services/logistica_bling.py", "routers/logistica.py"):
        fonte = (_APP / arquivo).read_text(encoding="utf-8")
        for expressao in ("parse_recipients(", "send_to_all", "ThreemaClient", "threema_registro"):
            assert expressao not in fonte, (arquivo, expressao)
    rota = (_APP / "routers/logistica.py").read_text(encoding="utf-8")
    assert "threema_recipients=_clean(" not in rota
    assert "s.threema_recipients = " not in rota
    # F3/C10a: o aviso dos robôs não monta mais a lista (o `destinatarios`
    # fica no módulo só como a configuração antiga) nem cria o cliente
    import inspect

    aviso = inspect.getsource(ouvidoria.avisar_pendentes)
    for expressao in ("destinatarios(", "send_to_all", "ThreemaClient", "parse_recipients"):
        assert expressao not in aviso, expressao
    # F3/C10b: os avisos na hora do Robô da Margem também não (nem o cliente,
    # nem a lista do robô, nem a caixinha da margem alta, que o núcleo lê)
    margem = (_APP / "services/margem_auto_hold.py").read_text(encoding="utf-8")
    for expressao in (
        "destinatarios(",
        "send_to_all",
        "ThreemaClient",
        "parse_recipients",
        "threema_recipients",
        "avisar_margem_alta",
        "_recipients_margem_auto",
    ):
        assert expressao not in margem, expressao
    # F2/C9e: o liga/desliga da Conferência (as chaves do .env) só no catálogo
    # e no núcleo — nem o ponto nem o varredor do worker leem mais
    for arquivo in ("services/conferencia_shopee/threema_aviso.py", "worker.py"):
        fonte = (_APP / arquivo).read_text(encoding="utf-8")
        for plataforma in ("shopee", "ml", "amazon"):
            assert f".conferencia_{plataforma}_threema" not in fonte, (arquivo, plataforma)
