"""Ouvidoria › Robôs — núcleo (services/ouvidoria), router e a mudança de casa
do vigia (migration 0300).

Vinicius, 21/09/2026: "ouvidoria pode ser o último no painel, acima apenas de
Admin". O que este arquivo trava:

- `registrar` é idempotente pela chave: a 2ª rodada só carimba
  `ultima_vista_em`; `ignorada` NUNCA reabre; `tratada` há < 24 h não reabre,
  depois disso abre linha NOVA (o histórico fica);
- `fechar_nao_vistas` fecha como `sumiu` o que a rodada não viu, MENOS as
  ocorrências das contas que falharam (não olhou ≠ sumiu);
- `avisar_pendentes` só manda em modo `ligado`, UMA mensagem por robô,
  re-avisa por `reaviso_horas`, e falha no Threema não carimba;
- `Rodada` grava a rodada e os `ultima_*` do robô mesmo quando o corpo
  levanta (a coluna Saúde depende disso);
- tratar / ignorar / reabrir e o 409 da chave duplicada;
- o SQL da 0300 copia `vigia_importacao` pra `ouvidoria_ocorrencias` do jeito
  descrito no cabeçalho da migração (rodado DE VERDADE, importado dela);
- o catálogo dos 6 robôs de 22/09/2026: nascem `silencioso` (registram no
  painel e não mandam Threema) e o `sincronizar_catalogo` não desliga de volta
  o que a pessoa ligou; toda chave de config tem `Parametro` (limite + rótulo);
- `fechar_por_chave` e o `prefixo` do `fechar_nao_vistas`: ocorrência de
  EVENTO (aberta por hook) não morre como "sumiu" na rodada; `ativo` é o que
  os hooks olham antes de registrar;
- a poda: `gc_rodadas` (30 dias) e `gc_ocorrencias` (fechadas há 180 dias),
  que NUNCA apaga uma `ignorada` — apagá-la faria o robô reabrir a linha;
- `cadencia_texto` de cada robô bate, minuto a minuto, com o cron do worker
  (é o texto que a coluna Cadência do painel mostra);
- o router: permissões, resumo/por_robo, PATCH de modo, "Rodar agora";
- config com limites (`Parametro`): PATCH recusa com 422 e frase pra tela,
  a leitura aperta valor absurdo pra dentro e a Saúde não cai com texto;
- "Rodar agora" recusa (409) rodada em andamento (lock do cron ou deste
  processo) e rodada há menos de 1 min; o aviso do Threema leva o link.

As tabelas da Ouvidoria não estão no `_CLEANUP_TABLES` do conftest (arquivo
de outro dono); a fixture `_limpa_ouvidoria` abaixo faz o papel.
"""
# ruff: noqa: S608
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OuvidoriaOcorrencia, OuvidoriaRobo, OuvidoriaRodada
from app.routers import ouvidoria as router_mod
from app.services import ouvidoria as svc

pytestmark = pytest.mark.asyncio

SCHEMA = "davinci_test"
ROBO = "vigia_importacao"

_caminho = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0300_ouvidoria.py"
_spec = importlib.util.spec_from_file_location("m0300", _caminho)
m0300 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0300)


# ─── fixtures ──────────────────────────────────────────────────────────────


async def _limpar(db: AsyncSession) -> None:
    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos"):
        await db.execute(text(f"DELETE FROM {tbl}"))
    await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _limpa_ouvidoria(db: AsyncSession):
    await _limpar(db)
    yield
    await _limpar(db)


class _Threema:
    """Cliente falso: guarda o texto em vez de mandar. `falhar` simula o
    gateway fora do ar (exceção) e `ninguem` o 200 sem nenhum destinatário ok."""

    enviados: list[tuple[str, list[str]]] = []
    falhar = False
    ninguem = False

    def __init__(self, *args, **kwargs) -> None:
        """Aceita `contexto=` como o cliente de verdade (conversas separadas)."""

    async def send_to_all(self, texto: str, recipients=None) -> dict:
        if self.falhar:
            raise RuntimeError("gateway fora")
        self.enviados.append((texto, list(recipients or [])))
        if self.ninguem:
            return {"sent": [], "failed": list(recipients or [])}
        return {"sent": list(recipients or []), "failed": []}


def _sem_threema(monkeypatch) -> list[tuple[str, list[str]]]:
    _Threema.enviados = []
    _Threema.falhar = False
    _Threema.ninguem = False
    monkeypatch.setattr(svc.threema, "ThreemaClient", _Threema)
    return _Threema.enviados


async def _robo(db: AsyncSession, *, modo: str = "ligado", **extra) -> OuvidoriaRobo:
    await svc.sincronizar_catalogo(db)
    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.modo = modo
    robo.threema_recipients = extra.pop("threema_recipients", "ABCDEFGH")
    for k, v in extra.items():
        setattr(robo, k, v)
    await db.commit()
    return robo


def _t(**kw) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


async def _reg(db, chave: str, **kw) -> OuvidoriaOcorrencia:
    kw.setdefault("titulo", f"Pago e não caiu no Bling ({chave})")
    kw.setdefault("plataforma", "tiktok")
    kw.setdefault("conta", "TikTok injox")
    kw.setdefault("pedido", chave.split(":", 1)[-1])
    return await svc.registrar(db, ROBO, chave, **kw)


async def _abertas(db) -> list[OuvidoriaOcorrencia]:
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(OuvidoriaOcorrencia.fechada_em.is_(None))
                .order_by(OuvidoriaOcorrencia.chave)
            )
        ).scalars()
    )


async def _todas(db, chave: str) -> list[OuvidoriaOcorrencia]:
    return list(
        (
            await db.execute(
                select(OuvidoriaOcorrencia)
                .where(OuvidoriaOcorrencia.chave == chave)
                .order_by(OuvidoriaOcorrencia.aberta_em)
            )
        ).scalars()
    )


# ─── catálogo ──────────────────────────────────────────────────────────────


async def test_sincronizar_catalogo_cria_e_nao_mexe_no_que_a_pessoa_salvou(db):
    await svc.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo.modo == "ligado"
    assert robo.nome == svc.ROBOS[ROBO].nome  # o catálogo é quem nomeia
    assert robo.config["tolerancia_min"] == 90
    assert robo.plataformas == ["ml", "shopee", "tiktok", "amazon"]

    # A pessoa desligou e mudou a tolerância; uma chave nova apareceu no código.
    robo.modo = "desligado"
    robo.config = {"tolerancia_min": 120}
    await db.commit()
    await svc.sincronizar_catalogo(db)
    await db.commit()
    await db.refresh(robo)
    assert robo.modo == "desligado"
    assert robo.config["tolerancia_min"] == 120
    assert robo.config["janela_horas"] == 72  # completou a chave que faltava
    assert await svc.modo(db, ROBO) == "desligado"
    assert await svc.modo(db, "robo_que_nao_existe") == "ligado"


# Os 6 robôs aprovados em 22/09: chave → (área, plataformas, config padrão).
# O teste existe pra uma mudança de tabela não passar calada: a cadência que
# está aqui é a mesma do cron no worker e a que a tela mostra.
SEIS_ROBOS = {
    "vigia_credenciais": ("contas", ["ml", "shopee", "tiktok", "amazon", "magalu", "bling"],
                          {"cadencia_min": 60, "vencimento_dias": 7}),
    "vigia_ingest_bling": ("pedidos", ["bling"], {"cadencia_min": 15, "idade_min": 30}),
    # As caixinhas "Olhar …" do Correio nascem com o que o Vinicius pediu em
    # 22/09/2026: apreensão, extravio, roubo/furto e avaria, e mais nada.
    "vigia_correios": ("logistica", ["ml", "shopee", "tiktok", "amazon"],
                       {"cadencia_min": 15, "olhar_apreensao": True,
                        "olhar_extravio": True, "olhar_roubo_furto": True,
                        "olhar_avaria": True, "olhar_devolvido_ao_remetente": False,
                        "olhar_nova_tentativa": False,
                        "olhar_ocorrencia_desconhecida": False}),
    "vigia_marketing_comandos": ("marketing", ["shopee", "ml"],
                                 {"cadencia_min": 10, "pendente_min": 30,
                                  "executor_offline_min": 10}),
    "vigia_margem": ("margem", ["ml", "shopee", "tiktok", "amazon"],
                     {"cadencia_min": 30, "segurado_horas": 24}),
    # 24/09/2026: o executor do "Suspender entrega" (Mac Santiago).
    "vigia_robo_melhorenvio": ("logistica", ["amazon"],
                               {"cadencia_min": 10, "pendente_min": 30,
                                "executor_offline_min": 10}),
    # 24/09/2026: o executor de leitura de chamados (Mac Santiago).
    "vigia_robo_leitura": ("chamados", ["shopee"],
                           {"cadencia_min": 10, "sem_sinal_min": 30, "atraso_horas": 3}),
}


async def test_catalogo_dos_seis_robos_novos_nasce_silencioso(db):
    """Robô novo NÃO pode começar mandando Threema: nasce `silencioso`
    (registra no painel e cala) e o Vinicius liga um a um na tela. O vigia de
    importação, que já era da casa, continua `ligado`."""
    await svc.sincronizar_catalogo(db)
    await db.commit()
    for chave, (area, plataformas, config) in SEIS_ROBOS.items():
        robo = await db.get(OuvidoriaRobo, chave)
        assert robo is not None, chave
        assert robo.modo == "silencioso", chave
        assert robo.area == area and robo.plataformas == plataformas
        assert robo.config == config
        assert robo.cadencia_texto and robo.descricao
    assert (await db.get(OuvidoriaRobo, ROBO)).modo == "ligado"

    # A pessoa ligou um deles: a sincronização seguinte NÃO desliga de volta.
    margem = await db.get(OuvidoriaRobo, "vigia_margem")
    margem.modo = "ligado"
    await db.commit()
    await svc.sincronizar_catalogo(db)
    await db.commit()
    await db.refresh(margem)
    assert margem.modo == "ligado"
    assert await svc.modo(db, "vigia_margem") == "ligado"


async def test_todo_robo_tem_limite_pra_cada_chave_de_config():
    """Chave de config sem `Parametro` passaria pelo PATCH sem limite e
    apareceria crua na tela — os dois defeitos de uma vez. Chave que não é
    número (as caixinhas "Olhar …") não tem limite pra checar, mas precisa do
    rótulo em `rotulos_extras`, senão aparece crua do mesmo jeito."""
    for d in svc.ROBOS.values():
        assert set(d.config_padrao) == set(d.parametros) | set(d.rotulos_extras), d.chave
        assert not (set(d.parametros) & set(d.rotulos_extras)), d.chave
        for chave in d.rotulos_extras:
            assert isinstance(d.config_padrao[chave], bool), (d.chave, chave)
        for chave, p in d.parametros.items():
            assert p.minimo <= int(d.config_padrao[chave]) <= p.maximo, (d.chave, chave)
        assert d.modo_padrao in ("ligado", "silencioso", "desligado"), d.chave


async def test_rotulos_config_traz_rotulo_com_unidade():
    # A tela não conhece robô por robô: o rótulo (e a unidade) vem daqui.
    assert svc.rotulos_config("vigia_margem") == {
        "cadencia_min": "Cadência esperada (min)",
        "segurado_horas": "Segurado sem decisão (h)",
    }
    # Parâmetro sem unidade sai só com o rótulo; robô fora do catálogo, vazio.
    assert svc.rotulos_config(ROBO)["amazon_a_cada_rodadas"] == "Amazon a cada N rodadas"
    # Caixinha (não é número): o rótulo vem de `rotulos_extras`.
    assert (
        svc.rotulos_config("vigia_correios")["olhar_apreensao"]
        == "Olhar apreensão / retenção fiscal"
    )
    assert svc.rotulos_config("robo_que_nao_existe") == {}


async def test_ativo_e_falso_so_no_desligado(db):
    """O que os HOOKS olham antes de abrir ocorrência no meio de outra
    operação: `silencioso` registra (o aviso é que não sai), `desligado` não
    grava nada. Robô que ainda não existe no banco conta como ativo — é o
    comportamento de sempre do `modo`."""
    await svc.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, "vigia_margem")
    assert await svc.ativo(db, "vigia_margem") is True  # nasce silencioso
    robo.modo = "ligado"
    await db.commit()
    assert await svc.ativo(db, "vigia_margem") is True
    robo.modo = "desligado"
    await db.commit()
    assert await svc.ativo(db, "vigia_margem") is False
    assert await svc.ativo(db, "robo_que_nao_existe") is True


# ─── registrar ─────────────────────────────────────────────────────────────


async def test_registrar_cria_e_a_segunda_vez_so_carimba(db):
    await _robo(db)
    antes = _t(hours=2)
    a = await _reg(db, "tiktok:1", agora=antes, dados={"valor": 100})
    assert a.aberta_em == antes and a.ultima_vista_em == antes

    depois = _t(minutes=1)
    b = await _reg(
        db, "tiktok:1", agora=depois, titulo="Pago 14:08 (R$ 100,00) e não caiu no Bling",
        dados={"sku": "X1"},
    )
    assert b.id == a.id
    assert b.aberta_em == antes  # não reabre
    assert b.ultima_vista_em == depois
    assert b.titulo.startswith("Pago 14:08")
    assert b.dados == {"valor": 100, "sku": "X1"}  # mescla, não substitui
    assert len(await _abertas(db)) == 1


async def test_registrar_nao_reabre_ignorada(db):
    await _robo(db)
    a = await _reg(db, "tiktok:2")
    await svc.tratar(db, a.id, usuario="Eduardo", fechamento="ignorada")

    b = await _reg(db, "tiktok:2")
    assert b.id == a.id and b.fechamento == "ignorada"
    assert await _abertas(db) == []


async def test_registrar_tratada_recente_nao_reabre_mas_velha_abre_linha_nova(db):
    await _robo(db)
    a = await _reg(db, "tiktok:3")
    await svc.tratar(db, a.id, usuario="Eduardo", fechamento="tratada", agora=_t(hours=2))

    # 2 h depois de tratada: o Bling ainda vai refletir — segura.
    b = await _reg(db, "tiktok:3")
    assert b.id == a.id and await _abertas(db) == []

    # Mais de 24 h depois e o problema voltou: linha nova, histórico fica.
    a.fechada_em = _t(hours=30)
    await db.commit()
    c = await _reg(db, "tiktok:3")
    assert c.id != a.id and c.fechada_em is None
    assert len(await _todas(db, "tiktok:3")) == 2


async def test_fechar_nao_vistas_respeita_excluir_contas(db):
    LUCAS = "Mercado Livre lucas"  # noqa: N806
    await _robo(db)
    async with svc.Rodada(db, ROBO) as r:
        await r.registrar("tiktok:10", titulo="a", conta="TikTok injox")
        await r.registrar("tiktok:11", titulo="b", conta="TikTok injox")
        await r.registrar("shopee:20", titulo="c", conta="Shopee mini", plataforma="shopee")
        await r.registrar("conta:ml-1", titulo="Conta sem acesso à API", conta=LUCAS)
        await r.registrar("ml:30", titulo="d", conta=LUCAS, plataforma="ml")

    async with svc.Rodada(db, ROBO) as r:
        # Nesta rodada só o 10 continua; a conta "lucas" falhou de novo (e o
        # robô nem conseguiu olhar o 30 dela).
        await r.registrar("tiktok:10", titulo="a", conta="TikTok injox")
        await r.registrar("conta:ml-1", titulo="Conta sem acesso à API", conta=LUCAS)
        n = await r.fechar_nao_vistas(excluir_contas={LUCAS})

    assert n == 2  # 11 e 20 sumiram
    abertas = {o.chave for o in await _abertas(db)}
    assert abertas == {"tiktok:10", "conta:ml-1", "ml:30"}
    fechada = (await _todas(db, "tiktok:11"))[0]
    assert (fechada.fechamento, fechada.fechada_por) == ("sumiu", "robô")


async def test_fechar_por_chave_e_o_par_das_ocorrencias_de_evento(db):
    """Ocorrência que um hook abriu no ponto da falha (a réplica não foi, o
    hold não pegou) não é re-vista por rodada nenhuma: quem fecha é o ponto de
    SUCESSO da mesma operação."""
    await _robo(db)
    await _reg(db, "falha:295070", titulo="Não consegui segurar o pedido no Bling")
    assert await svc.fechar_por_chave(db, ROBO, "falha:295070") is True
    fechada = (await _todas(db, "falha:295070"))[0]
    assert (fechada.fechamento, fechada.fechada_por) == ("sumiu", "robô")
    # Sem aberta não há o que fechar (o robô pode acertar duas vezes seguidas).
    assert await svc.fechar_por_chave(db, ROBO, "falha:295070") is False
    assert await svc.fechar_por_chave(db, ROBO, "falha:nunca-vista") is False


async def test_fechar_nao_vistas_com_prefixo_nao_mata_as_de_evento(db):
    """Robô que mistura ESTADO (a rodada re-vê) com EVENTO (hook abre): sem o
    prefixo, o fechamento da rodada mataria como "sumiu" justamente a falha
    que ninguém tratou ainda."""
    await _robo(db)
    async with svc.Rodada(db, ROBO) as r:
        await r.registrar("segurado:295070", titulo="Pedido segurado há 26 h sem decisão")
        await r.registrar("segurado:295071", titulo="Pedido segurado há 30 h sem decisão")
        await r.registrar("falha:295072", titulo="Não consegui liberar o pedido no Bling")

    async with svc.Rodada(db, ROBO) as r:
        # Nesta rodada só o 295070 continua segurado; a `falha:` não é re-vista.
        await r.registrar("segurado:295070", titulo="Pedido segurado há 26 h sem decisão")
        n = await r.fechar_nao_vistas(prefixo="segurado:")

    assert n == 1
    assert {o.chave for o in await _abertas(db)} == {"segurado:295070", "falha:295072"}

    # Sem prefixo, a mesma rodada teria levado a `falha:` junto.
    async with svc.Rodada(db, ROBO) as r:
        await r.registrar("segurado:295070", titulo="Pedido segurado há 26 h sem decisão")
        assert await r.fechar_nao_vistas() == 1
    assert {o.chave for o in await _abertas(db)} == {"segurado:295070"}


# ─── rodada ────────────────────────────────────────────────────────────────


async def test_rodada_grava_rodada_e_ultima_no_robo(db):
    await _robo(db)
    async with svc.Rodada(db, ROBO) as r:
        r.contadores["pedidos"] += 230
        r.contadores["contas"] += 31
        r.resumo = "230 pedidos conferidos · 0 novas · 0 conta falhou"

    rod = (await db.execute(select(OuvidoriaRodada))).scalars().one()
    assert rod.ok is True and rod.contadores == {"pedidos": 230, "contas": 31}
    assert rod.resumo.startswith("230 pedidos")
    robo = await db.get(OuvidoriaRobo, ROBO)
    await db.refresh(robo)
    assert robo.ultima_rodada_ok is True
    assert robo.ultima_rodada_resumo == rod.resumo
    assert robo.ultima_rodada_duracao_ms is not None and robo.ultima_rodada_duracao_ms >= 0
    assert robo.ultima_falha_em is None


async def test_rodada_sem_robo_cadastrado_sincroniza_o_catalogo(db):
    # Worker subiu antes do 1º tick sincronizar: a Rodada cria a linha.
    assert await db.get(OuvidoriaRobo, ROBO) is None
    async with svc.Rodada(db, ROBO) as r:
        await r.registrar("tiktok:1", titulo="a")
    assert (await db.get(OuvidoriaRobo, ROBO)).ultima_rodada_ok is True


async def test_rodada_com_erro_grava_falha_e_re_levanta(db):
    await _robo(db)
    with pytest.raises(RuntimeError, match="token vencido"):
        async with svc.Rodada(db, ROBO) as r:
            r.contadores["pedidos"] += 5
            raise RuntimeError("token vencido")

    rod = (await db.execute(select(OuvidoriaRodada))).scalars().one()
    assert rod.ok is False and rod.erro == "token vencido"
    robo = await db.get(OuvidoriaRobo, ROBO)
    await db.refresh(robo)
    assert robo.ultima_rodada_ok is False
    assert robo.ultima_falha_erro == "token vencido"
    assert robo.ultima_falha_em is not None
    assert svc.saude(robo) == "falhando"


async def test_saude():
    # `saude` é pura — objeto em memória, sem banco.
    robo = OuvidoriaRobo(chave=ROBO, nome="Vigia", modo="ligado", config={})
    assert svc.saude(robo) == "parado"  # nunca rodou
    robo.ultima_rodada_em = _t(minutes=5)
    robo.ultima_rodada_ok = True
    assert svc.saude(robo) == "ok"
    robo.ultima_rodada_ok = False
    assert svc.saude(robo) == "falhando"
    # cadencia_min = 60 no catálogo → parado depois de 180 min sem rodar.
    robo.ultima_rodada_ok = True
    robo.ultima_rodada_em = _t(minutes=100)
    assert svc.saude(robo) == "ok"
    robo.ultima_rodada_em = _t(minutes=190)
    assert svc.saude(robo) == "parado"
    robo.modo = "desligado"
    assert svc.saude(robo) == "desligado"


async def test_gc_rodadas(db):
    await _robo(db)
    db.add_all([
        OuvidoriaRodada(robo_chave=ROBO, iniciada_em=_t(days=40), ok=True),
        OuvidoriaRodada(robo_chave=ROBO, iniciada_em=_t(days=1), ok=True),
    ])
    await db.commit()
    assert await svc.gc_rodadas(db, dias=30) == 1
    await db.commit()
    assert len((await db.execute(select(OuvidoriaRodada))).scalars().all()) == 1


async def test_gc_ocorrencias_poda_as_fechadas_velhas_e_nunca_as_ignoradas(db):
    """As fechadas antigas saem (com 7 robôs a rotatividade é diária), menos as
    `ignorada`: elas são a memória de "não cobre mais isto" que o `registrar`
    lê — apagar faria o robô reabrir a linha na rodada seguinte."""
    await _robo(db)
    velha = await _reg(db, "tiktok:1")
    ignorada = await _reg(db, "tiktok:2")
    recente = await _reg(db, "tiktok:3")
    aberta = await _reg(db, "tiktok:4")
    for row, quando, fechamento in (
        (velha, _t(days=200), "sumiu"),
        (ignorada, _t(days=200), "ignorada"),
        (recente, _t(days=10), "tratada"),
    ):
        row.fechada_em = quando
        row.fechamento = fechamento
    await db.commit()

    assert await svc.gc_ocorrencias(db) == 1
    await db.commit()

    restantes = {
        o.chave
        for o in (await db.execute(select(OuvidoriaOcorrencia))).scalars().all()
    }
    assert restantes == {"tiktok:2", "tiktok:3", "tiktok:4"}
    assert aberta.fechada_em is None


# ─── aviso ─────────────────────────────────────────────────────────────────


async def test_avisar_pendentes_uma_mensagem_por_robo_e_carimba(db, monkeypatch):
    enviados = _sem_threema(monkeypatch)
    await _robo(db, threema_recipients="ABCDEFGH, IJKLMNOP")
    await _reg(db, "tiktok:1", titulo="Pago 14:08 (R$ 739,19) e não caiu no Bling",
               acao="Importar manualmente: Bling › Vendas › Pedidos de lojas virtuais")
    await _reg(db, "conta:ml-1", titulo="Conta sem acesso à API", conta="Mercado Livre lucas",
               plataforma="ml", pedido=None, acao="Reautorizar em Sistema › Integrações")
    await _reg(db, "tiktok:9", titulo="só registro", precisa_pessoa=False)

    r = await svc.avisar_pendentes(db, ROBO)

    assert r == {"avisadas": 2}
    assert len(enviados) == 1
    texto, alvos = enviados[0]
    assert alvos == ["ABCDEFGH", "IJKLMNOP"]
    assert texto.startswith(f"{svc.ROBOS[ROBO].nome} — 2 ocorrências:")
    assert "TikTok injox · 1 · Pago 14:08 (R$ 739,19) e não caiu no Bling" in texto
    assert "→ Importar manualmente" in texto
    assert "Mercado Livre lucas · Conta sem acesso à API" in texto
    assert "→ Reautorizar" in texto
    assert "só registro" not in texto
    # Última linha: o link da aba Ocorrências já filtrada no robô.
    assert texto.splitlines()[-1].endswith(f"/ouvidoria/robos?aba=ocorrencias&robo={ROBO}")
    for o in await _abertas(db):
        if o.precisa_pessoa:
            assert o.avisada_em is not None and o.reavisada_em is None
        else:
            assert o.avisada_em is None

    # Segunda chamada logo em seguida: nada novo, nada mandado.
    assert await svc.avisar_pendentes(db, ROBO) == {"avisadas": 0}
    assert len(enviados) == 1


async def test_avisar_pendentes_so_em_modo_ligado(db, monkeypatch):
    enviados = _sem_threema(monkeypatch)
    await _robo(db, modo="silencioso")
    await _reg(db, "tiktok:1")
    assert await svc.avisar_pendentes(db, ROBO) == {"avisadas": 0, "motivo": "modo_silencioso"}
    assert enviados == []

    robo = await db.get(OuvidoriaRobo, ROBO)
    robo.modo = "desligado"
    await db.commit()
    assert (await svc.avisar_pendentes(db, ROBO))["motivo"] == "modo_desligado"

    robo.modo = "ligado"
    await db.commit()
    assert await svc.avisar_pendentes(db, ROBO) == {"avisadas": 1}
    assert len(enviados) == 1


async def test_avisar_pendentes_reavisa_por_reaviso_horas(db, monkeypatch):
    enviados = _sem_threema(monkeypatch)
    await _robo(db, reaviso_horas=6)
    a = await _reg(db, "tiktok:1")
    a.avisada_em = _t(hours=3)
    await db.commit()
    assert await svc.avisar_pendentes(db, ROBO) == {"avisadas": 0}  # ainda dentro das 6 h

    a.avisada_em = _t(hours=7)
    await db.commit()
    assert await svc.avisar_pendentes(db, ROBO) == {"avisadas": 1}
    await db.refresh(a)
    assert a.reavisada_em is not None and a.avisada_em < a.reavisada_em
    assert len(enviados) == 1

    # O re-aviso conta a partir do ÚLTIMO carimbo (reavisada_em), não do 1º.
    a.avisada_em = _t(hours=20)
    a.reavisada_em = _t(hours=2)
    await db.commit()
    assert await svc.avisar_pendentes(db, ROBO) == {"avisadas": 0}


async def test_avisar_pendentes_falha_no_threema_nao_carimba(db, monkeypatch):
    _sem_threema(monkeypatch)
    await _robo(db)
    a = await _reg(db, "tiktok:1")

    _Threema.falhar = True
    r = await svc.avisar_pendentes(db, ROBO)
    assert r["avisadas"] == 0 and r["motivo"] == "threema_falhou"
    await db.refresh(a)
    assert a.avisada_em is None

    _Threema.falhar = False
    _Threema.ninguem = True
    assert (await svc.avisar_pendentes(db, ROBO))["avisadas"] == 0
    await db.refresh(a)
    assert a.avisada_em is None


async def test_avisar_pendentes_sem_destinatario_nem_manda(db, monkeypatch):
    enviados = _sem_threema(monkeypatch)
    await _robo(db, threema_recipients=None)

    class _S:
        vigia_importacao_threema_recipients = ""
        ouvidoria_threema_recipients = ""

    monkeypatch.setattr(svc, "get_settings", lambda: _S())
    await _reg(db, "tiktok:1")
    assert (await svc.avisar_pendentes(db, ROBO))["motivo"] == "sem_destinatarios"
    assert enviados == []

    # Sem override na tela, cai no env do robô; sem esse, no geral da Ouvidoria.
    _S.ouvidoria_threema_recipients = "GERAL001"
    assert svc.destinatarios(await db.get(OuvidoriaRobo, ROBO), ROBO) == (["GERAL001"], "geral")
    _S.vigia_importacao_threema_recipients = "VIGIA001"
    assert svc.destinatarios(await db.get(OuvidoriaRobo, ROBO), ROBO) == (["VIGIA001"], "env")


def _oc(i: int, acao: str = "Importar") -> OuvidoriaOcorrencia:
    return OuvidoriaOcorrencia(
        robo_chave=ROBO, chave=f"x:{i}", conta="Conta", pedido=str(i), titulo="t" * 40,
        acao=acao, aberta_em=_t(), ultima_vista_em=_t(),
    )


async def test_texto_aviso_corta_em_15_linhas_e_no_limite_de_bytes():
    texto = svc.texto_aviso("Robô", [_oc(i) for i in range(40)])
    linhas = texto.splitlines()
    assert linhas[0] == "Robô — 40 ocorrências:"
    assert linhas[-1] == "… e mais 25"
    assert sum(1 for ln in linhas if ln.startswith("Conta · ")) == 15
    # A mesma ação em todas as linhas sai UMA vez.
    assert sum(1 for ln in linhas if ln.startswith("→ ")) == 1

    # Ações diferentes: cada uma sai uma vez, na troca de grupo.
    texto = svc.texto_aviso("Robô", [_oc(1, "A"), _oc(2, "B"), _oc(3, "A")])
    assert texto.count("→ A") == 1 and texto.count("→ B") == 1

    # Ações enormes e todas diferentes (cada uma sai): o corte por bytes tira
    # linhas do fim até caber nos 3500 do Threema — e o link entra na conta.
    gordas = [_oc(i, acao="Z" * 600 + str(i)) for i in range(15)]
    texto = svc.texto_aviso("Robô", gordas, link="https://painel/ouvidoria/robos?aba=ocorrencias")
    assert len(texto.encode("utf-8")) <= svc.threema._MAX_TEXT_BYTES
    assert "… e mais" in texto
    assert texto.splitlines()[-1] == "https://painel/ouvidoria/robos?aba=ocorrencias"


# ─── config: limites e leitura tolerante ───────────────────────────────────


async def test_validar_config_recusa_fora_dos_limites_com_frase_pra_tela():
    ok = svc.validar_config(ROBO, {"tolerancia_min": "120", "janela_horas": 48, "livre": "x"})
    assert ok == {"tolerancia_min": 120, "janela_horas": 48, "livre": "x"}
    with pytest.raises(svc.OuvidoriaError) as e:
        svc.validar_config(ROBO, {"tolerancia_min": 100000})
    assert e.value.code == "config_invalida"
    assert e.value.message == "Tolerância precisa ser entre 5 e 1440 min"
    with pytest.raises(svc.OuvidoriaError, match="Janela precisa ser entre 1 e 168 h"):
        svc.validar_config(ROBO, {"janela_horas": -1})
    with pytest.raises(svc.OuvidoriaError, match="Cadência esperada precisa ser um número"):
        svc.validar_config(ROBO, {"cadencia_min": "trinta"})
    with pytest.raises(svc.OuvidoriaError, match="Amazon a cada N rodadas precisa ser entre"):
        svc.validar_config(ROBO, {"amazon_a_cada_rodadas": 0})
    with pytest.raises(svc.OuvidoriaError, match="número inteiro"):
        svc.validar_config(ROBO, {"tolerancia_min": 12.5})
    # Robô fora do catálogo: nada a validar.
    assert svc.validar_config("outro", {"x": "y"}) == {"x": "y"}


async def test_config_do_robo_le_tolerante_e_a_saude_nao_cai():
    # Gravado por fora (ou por uma tela antiga): texto e valor absurdo.
    robo = OuvidoriaRobo(
        chave=ROBO, nome="Vigia", modo="ligado",
        config={"cadencia_min": "trinta", "tolerancia_min": 100000, "janela_horas": 0},
        ultima_rodada_em=_t(minutes=5), ultima_rodada_ok=True,
    )
    cfg = svc.config_do_robo(robo, ROBO)
    assert cfg["cadencia_min"] == 60  # não converteu → padrão do catálogo
    assert cfg["tolerancia_min"] == 1440 and cfg["janela_horas"] == 1  # apertados
    assert cfg["amazon_a_cada_rodadas"] == 3
    assert svc.saude(robo) == "ok"


async def test_validar_destinatarios():
    assert svc.validar_destinatarios("") is None
    assert svc.validar_destinatarios(" abcdefgh, IJKLMNOP;abcdefgh ") == "ABCDEFGH, IJKLMNOP"
    assert svc.validar_destinatarios("*GATEWAY") == "*GATEWAY"
    with pytest.raises(svc.OuvidoriaError, match="Threema ID inválido: ABC"):
        svc.validar_destinatarios("ABC")
    with pytest.raises(svc.OuvidoriaError, match="No máximo 20"):
        svc.validar_destinatarios(",".join(f"ID{i:06d}" for i in range(21)))


# ─── tratar / ignorar / reabrir ────────────────────────────────────────────


async def test_tratar_ignorar_reabrir(db):
    await _robo(db)
    a = await _reg(db, "tiktok:1")
    t = await svc.tratar(db, a.id, usuario="Eduardo", fechamento="tratada")
    assert (t.fechamento, t.fechada_por) == ("tratada", "Eduardo")
    assert t.fechada_em is not None

    with pytest.raises(svc.OuvidoriaError, match="ocorrencia_ja_fechada"):
        await svc.tratar(db, a.id, usuario="Eduardo", fechamento="ignorada")
    with pytest.raises(svc.OuvidoriaError, match="fechamento_invalido"):
        await svc.tratar(db, a.id, usuario="Eduardo", fechamento="sumiu")
    with pytest.raises(svc.OuvidoriaError, match="ocorrencia_nao_encontrada"):
        await svc.tratar(db, uuid4(), usuario="Eduardo", fechamento="tratada")

    r = await svc.reabrir(db, a.id, usuario="Vinicius")
    assert r.fechada_em is None and r.fechamento is None and r.fechada_por is None
    assert r.dados["reaberta_por"] == "Vinicius"
    with pytest.raises(svc.OuvidoriaError, match="ocorrencia_aberta"):
        await svc.reabrir(db, a.id, usuario="Vinicius")


async def test_reabrir_recusa_se_o_robo_ja_abriu_outra_da_mesma_chave(db):
    await _robo(db)
    a = await _reg(db, "tiktok:1")
    await svc.tratar(db, a.id, usuario="Eduardo", fechamento="tratada", agora=_t(days=2))
    b = await _reg(db, "tiktok:1")  # > 24 h: linha nova
    assert b.id != a.id
    with pytest.raises(svc.OuvidoriaError, match="ocorrencia_duplicada"):
        await svc.reabrir(db, a.id, usuario="Vinicius")


# ─── resumo ────────────────────────────────────────────────────────────────


async def test_resumo_ocorrencias(db):
    await _robo(db)
    await _reg(db, "tiktok:1")
    await _reg(db, "tiktok:2", precisa_pessoa=False, severidade="info")
    await _reg(db, "conta:ml-1", titulo="Conta sem acesso à API", conta="ML lucas", pedido=None)
    s = await _reg(db, "tiktok:3", agora=_t(days=3))
    await svc.fechar_nao_vistas(db, ROBO, {"tiktok:1", "tiktok:2", "conta:ml-1"})
    t = await _reg(db, "tiktok:4", agora=_t(days=3))
    await svc.tratar(db, t.id, usuario="Eduardo", fechamento="tratada")
    v = await _reg(db, "tiktok:5", agora=_t(days=20))
    await svc.tratar(db, v.id, usuario="Eduardo", fechamento="tratada", agora=_t(days=10))
    await db.commit()

    r = await svc.resumo_ocorrencias(db)
    assert r == {
        "abertas": 3,
        "abertas_pessoa": 2,
        "novas_hoje": 3,
        "sumiram_7d": 1,
        "tratadas_7d": 1,
        "contas_sem_vigilancia": 1,
    }
    assert s.fechamento == "sumiu"
    c = await svc.contagens_por_robo(db)
    assert c[ROBO]["abertas"] == 3 and c[ROBO]["abertas_pessoa"] == 2


# ─── ticks do worker (os 6 robôs de 22/09) ─────────────────────────────────


@pytest.mark.parametrize("chave", sorted(SEIS_ROBOS))
async def test_tick_respeita_o_modo_e_aguenta_servico_faltando(chave, db, monkeypatch):
    """O gate mora no tick (o botão "Rodar agora" não passa por aqui), e o
    import do serviço é tardio e tolerante: um robô que não veio no deploy
    derrubaria o import do worker e com ele TODOS os crons da casa."""
    import sys
    import types

    from app import worker

    tick = getattr(worker, f"{chave}_tick")
    chamadas: list[str] = []
    falso = types.ModuleType(f"app.services.{chave}")

    async def _sweep() -> dict:
        chamadas.append(chave)
        return {"novas": 0}

    setattr(falso, f"{chave}_sweep", _sweep)
    monkeypatch.setitem(sys.modules, f"app.services.{chave}", falso)

    await svc.sincronizar_catalogo(db)
    await db.commit()
    robo = await db.get(OuvidoriaRobo, chave)
    # Nasce silencioso: RODA (registra no painel), só não avisa.
    await tick({})
    assert chamadas == [chave]

    robo.modo = "desligado"
    await db.commit()
    await tick({})
    assert chamadas == [chave]  # nem rodou nem gravou rodada
    assert (await db.execute(select(OuvidoriaRodada))).scalars().all() == []

    # Serviço fora do deploy: o tick avisa no log e sai inteiro.
    robo.modo = "ligado"
    await db.commit()
    monkeypatch.setitem(sys.modules, f"app.services.{chave}", None)
    await tick({})
    assert chamadas == [chave]


async def test_todo_robo_do_catalogo_tem_tick_e_runner():
    """Robô no catálogo sem tick no worker nasce "parado" na coluna Saúde e
    ninguém entende por quê; sem runner, o botão "Rodar agora" some."""
    from app import worker

    for chave in svc.ROBOS:
        assert hasattr(worker, f"{chave}_tick"), chave
        assert chave in router_mod.RUNNERS and chave in router_mod.LOCKS, chave


async def test_cadencia_texto_bate_com_os_minutos_do_cron():
    """`cadencia_texto` é o que a coluna Cadência do painel mostra ("a cada 30
    min (:17/:47)"). Se alguém mexer no minuto do cron e esquecer o catálogo, a
    tela passa a mentir — e a diferença só aparece meses depois, quando alguém
    for entender por que um robô parece atrasado."""
    import re

    from app import worker

    por_funcao = {c.coroutine.__name__: c for c in worker.WorkerSettings.cron_jobs}
    for chave, d in svc.ROBOS.items():
        job = por_funcao.get(f"{chave}_tick")
        assert job is not None, chave
        minuto = job.minute
        do_cron = sorted(minuto) if isinstance(minuto, (set, frozenset, list, tuple)) else [minuto]
        do_texto = sorted(int(m) for m in re.findall(r":(\d{2})", d.cadencia_texto))
        assert do_cron == do_texto, f"{chave}: cron {do_cron} × tela {do_texto}"


# ─── migration 0300: o vigia muda de casa ──────────────────────────────────


async def _vigia_antigo(db: AsyncSession) -> None:
    """Monta `vigia_importacao` no schema de teste com as colunas da 0230 —
    o modelo sai do código, então o create_all não a cria mais."""
    await db.execute(text(f"DROP TABLE IF EXISTS {SCHEMA}.vigia_importacao"))
    await db.commit()
    tabela = sa.Table(
        "vigia_importacao", sa.MetaData(schema=SCHEMA), *m0300.colunas_vigia_importacao()
    )
    await db.run_sync(lambda s: tabela.create(s.connection()))
    await db.commit()


async def _linha_antiga(db, numero: str, **kw) -> None:
    cols = {
        "plataforma": "ml", "conta": "Mercado Livre marquezini", "numero_loja": numero,
        "pack_id": None, "pago_em": None, "detectado_em": _t(hours=5),
        "ultima_verificacao": None, "avisado_em": None, "resolvido_em": None,
    }
    cols.update(kw)
    await db.execute(
        text(
            f"INSERT INTO {SCHEMA}.vigia_importacao (plataforma, conta, numero_loja, pack_id, "
            "pago_em, detectado_em, ultima_verificacao, avisado_em, resolvido_em) VALUES "
            "(:plataforma, :conta, :numero_loja, :pack_id, :pago_em, :detectado_em, "
            ":ultima_verificacao, :avisado_em, :resolvido_em)"
        ),
        cols,
    )


async def test_migration_0300_copia_o_vigia_antigo(db):
    await _vigia_antigo(db)
    pago = datetime(2026, 9, 16, 13, 20, tzinfo=UTC)  # 10:20 em São Paulo
    # 1. Aberta e viva (vista há 1 h): fica aberta, com o último aviso.
    await _linha_antiga(
        db, "2000018488660892", pack_id="2000007", pago_em=pago,
        ultima_verificacao=_t(hours=1), avisado_em=_t(hours=3),
    )
    # 2. Resolvida no Bling: fecha como sumiu em resolvido_em.
    resolvido = _t(days=2)
    await _linha_antiga(
        db, "2000018488660893", resolvido_em=resolvido, ultima_verificacao=resolvido
    )
    # 3. Aberta mas parada há 10 dias (saiu da janela do ML): sumiu em ultima_verificacao.
    parada = _t(days=10)
    await _linha_antiga(db, "2000018488660894", ultima_verificacao=parada, detectado_em=_t(days=12))
    # 4. Aberta, nunca verificada, detectada há 9 dias: cai no fallback detectado_em.
    velha = _t(days=9)
    await _linha_antiga(db, "2000018488660895", detectado_em=velha)
    await db.commit()

    for sentenca in m0300.sentencas_copia(SCHEMA):
        await db.execute(sentenca)
    await db.commit()

    robo = await db.get(OuvidoriaRobo, ROBO)
    assert robo is not None and robo.modo == "ligado"
    assert robo.config["tolerancia_min"] == 90 and robo.cadencia_texto
    por_chave = {o.chave: o for o in (await db.execute(select(OuvidoriaOcorrencia))).scalars()}
    assert set(por_chave) == {
        "ml:2000018488660892", "ml:2000018488660893", "ml:2000018488660894",
        "ml:2000018488660895",
    }
    viva = por_chave["ml:2000018488660892"]
    assert viva.fechada_em is None
    assert viva.plataforma == "ml" and viva.pedido == "2000018488660892"
    assert viva.conta == "Mercado Livre marquezini"
    assert viva.titulo == "Pago 16/09 10:20 e não caiu no Bling"
    assert viva.dados["pack_id"] == "2000007" and viva.dados["pago_em"].startswith("2026-09-16")
    assert viva.avisada_em is not None and viva.reavisada_em is None
    assert viva.acao.startswith("Importar manualmente")
    assert viva.severidade == "pessoa" and viva.precisa_pessoa is True

    res = por_chave["ml:2000018488660893"]
    assert (res.fechamento, res.fechada_por) == ("sumiu", "robô")
    assert abs((res.fechada_em - resolvido).total_seconds()) < 1
    assert res.titulo == "Pago e não caiu no Bling"  # sem pago_em

    par = por_chave["ml:2000018488660894"]
    assert par.fechamento == "sumiu" and abs((par.fechada_em - parada).total_seconds()) < 1
    vel = por_chave["ml:2000018488660895"]
    assert vel.fechamento == "sumiu" and abs((vel.fechada_em - velha).total_seconds()) < 1

    # Re-rodar não duplica (id determinístico + NOT EXISTS / ON CONFLICT).
    for sentenca in m0300.sentencas_copia(SCHEMA):
        await db.execute(sentenca)
    await db.commit()
    assert len((await db.execute(select(OuvidoriaOcorrencia))).scalars().all()) == 4
    await db.execute(text(f"DROP TABLE {SCHEMA}.vigia_importacao"))
    await db.commit()


# ─── router ────────────────────────────────────────────────────────────────


def _perms(*, view: bool = True, edit: bool = False) -> dict:
    return {"ouvidoria": {"view": view, "edit": edit, "delete": False}}


async def test_router_permissoes(client, make_user, auth_as, db):
    await _robo(db)
    sem = await make_user(permissions={})
    auth_as(sem)
    assert (await client.get("/api/ouvidoria/robos")).status_code == 403

    so_ve = await make_user(permissions=_perms())
    auth_as(so_ve)
    assert (await client.get("/api/ouvidoria/robos")).status_code == 200
    assert (await client.get("/api/ouvidoria/ocorrencias")).status_code == 200
    r = await client.patch(f"/api/ouvidoria/robos/{ROBO}", json={"modo": "desligado"})
    assert r.status_code == 403
    assert (await client.post(f"/api/ouvidoria/robos/{ROBO}/rodar")).status_code == 403


async def test_router_robos_lista_e_patch(client, make_user, auth_as, db):
    await _robo(db, threema_recipients="ABCDEFGH")
    async with svc.Rodada(db, ROBO) as r:
        r.resumo = "10 pedidos conferidos · 1 nova · 0 conta falhou"
    await _reg(db, "tiktok:1")
    await db.commit()
    editor = await make_user(email="eduardo@davinci-test.com", permissions=_perms(edit=True))
    auth_as(editor)

    r = await client.get("/api/ouvidoria/robos")
    assert r.status_code == 200, r.text
    # O catálogo tem os 7 robôs; este teste é sobre o vigia.
    por_chave = {b["chave"]: b for b in r.json()}
    assert set(por_chave) == set(svc.ROBOS)
    robo = por_chave[ROBO]
    assert robo["modo"] == "ligado"
    assert por_chave["vigia_margem"]["modo"] == "silencioso"  # robô novo nasce calado
    assert robo["config_rotulos"]["cadencia_min"] == "Cadência esperada (min)"
    assert robo["abertas"] == 1 and robo["abertas_pessoa"] == 1
    assert robo["rodadas_hoje"] == 1 and robo["rodadas_hoje_ok"] == 1
    assert robo["saude"] == "ok"
    assert robo["ultima_rodada_resumo"].startswith("10 pedidos")
    # `origem` só vem preenchida na LISTA do seletor (/threema/destinatarios);
    # aqui é quem o robô já avisa, não de onde a pessoa saiu.
    assert robo["threema_destinatarios"] == [
        {"id": "ABCDEFGH", "nome": "ABCDEFGH", "origem": None}
    ]
    assert robo["threema_origem"] == "robo"
    assert robo["config"]["cadencia_min"] == 60

    r = await client.patch(
        f"/api/ouvidoria/robos/{ROBO}",
        json={"modo": "silencioso", "reaviso_horas": 12, "threema_recipients": ""},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["modo"] == "silencioso" and body["reaviso_horas"] == 12
    assert body["modo_alterado_por"] == "eduardo@davinci-test.com"
    assert body["modo_alterado_em"] is not None
    assert body["threema_recipients"] is None  # "" limpou o override
    assert body["threema_origem"] in (None, "env", "geral")
    r = await client.patch(f"/api/ouvidoria/robos/{ROBO}", json={"modo": "xyz"})
    assert r.status_code == 422
    r = await client.patch("/api/ouvidoria/robos/nao-existe", json={"modo": "ligado"})
    assert r.status_code == 404

    # Config: fora do limite é 422 com a frase pra tela e NADA é gravado
    # (o modo do mesmo body não muda); dentro, mescla por cima do salvo.
    r = await client.patch(
        f"/api/ouvidoria/robos/{ROBO}",
        json={"modo": "ligado", "config": {"tolerancia_min": 100000, "janela_horas": 72}},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {
        "code": "config_invalida", "message": "Tolerância precisa ser entre 5 e 1440 min",
    }
    assert (await client.get(f"/api/ouvidoria/robos/{ROBO}")).json()["modo"] == "silencioso"
    r = await client.patch(
        f"/api/ouvidoria/robos/{ROBO}", json={"config": {"tolerancia_min": "120"}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["config"] == {
        "tolerancia_min": 120, "janela_horas": 72, "cadencia_min": 60, "amazon_a_cada_rodadas": 3,
    }
    r = await client.patch(f"/api/ouvidoria/robos/{ROBO}", json={"threema_recipients": "ABC"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "destinatarios_invalidos"
    r = await client.patch(
        f"/api/ouvidoria/robos/{ROBO}", json={"threema_recipients": "abcdefgh ijklmnop"}
    )
    assert r.status_code == 200 and r.json()["threema_recipients"] == "ABCDEFGH, IJKLMNOP"

    r = await client.get(f"/api/ouvidoria/robos/{ROBO}")
    assert r.status_code == 200
    det = r.json()
    assert len(det["rodadas"]) == 1 and det["rodadas"][0]["ok"] is True
    assert det["contas"] == []


async def test_router_cadastra_contato_avulso_do_threema(client, make_user, auth_as, db):
    """Vinicius, 22/09/2026: o "roma" (VBS64V3S) precisa receber aviso e não
    tem login no DaVinci. Cadastrado na própria tela, ele entra no seletor,
    dá pra escolher pelo NOME no campo do robô, e sai por ali mesmo."""
    await _robo(db)
    await db.commit()
    editor = await make_user(email="eduardo@davinci-test.com", permissions=_perms(edit=True))
    auth_as(editor)

    r = await client.post(
        "/api/ouvidoria/threema/destinatarios", json={"id": "vbs64v3s", "nome": "roma"}
    )
    assert r.status_code == 200, r.text
    por_id = {d["id"]: d for d in r.json()}
    assert por_id["VBS64V3S"]["nome"] == "roma"  # ID normalizado pra maiúsculo
    assert por_id["VBS64V3S"]["origem"] == "contato"

    # Entrou no diretório que a tela lê.
    lista = (await client.get("/api/ouvidoria/threema/destinatarios")).json()
    assert {"id": "VBS64V3S", "nome": "roma", "origem": "contato"} in lista

    # E o campo do robô aceita pelo nome, como com qualquer pessoa da lista.
    r = await client.patch(
        f"/api/ouvidoria/robos/{ROBO}", json={"threema_recipients": "roma"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["threema_recipients"] == "VBS64V3S"

    # ID torto não entra (e a frase explica o formato).
    r = await client.post(
        "/api/ouvidoria/threema/destinatarios", json={"id": "roma", "nome": "roma"}
    )
    assert r.status_code == 422
    assert "8 letras" in r.json()["detail"]["message"]

    r = await client.delete("/api/ouvidoria/threema/destinatarios/vbs64v3s")
    assert r.status_code == 200, r.text
    assert all(d["id"] != "VBS64V3S" for d in r.json())


async def test_contato_avulso_perde_pro_cadastro_do_usuario(client, make_user, auth_as, db):
    """Mesmo ID cadastrado nos dois lugares: quem manda no nome é Usuários —
    senão o apelido digitado aqui esconderia o nome real da pessoa."""
    editor = await make_user(email="eduardo@davinci-test.com", permissions=_perms(edit=True))
    editor.name = "Eduardo"
    editor.threema = "VBS64V3S"
    await db.commit()
    auth_as(editor)

    r = await client.post(
        "/api/ouvidoria/threema/destinatarios", json={"id": "VBS64V3S", "nome": "roma"}
    )
    assert r.status_code == 200, r.text
    linhas = [d for d in r.json() if d["id"] == "VBS64V3S"]
    assert len(linhas) == 1
    assert linhas[0]["origem"] == "usuario" and linhas[0]["nome"] == "Eduardo"


async def test_router_arquivar_tira_da_lista_sem_mexer_no_robo(
    client, make_user, auth_as, db
):
    """A lixeira do painel (Vinicius, 22/09): some da lista e NADA mais — o
    robô não é excluído, o modo fica como está e ele continua rodando e
    avisando. A listagem devolve todos (a tela é quem esconde), e a sincronia
    do catálogo não pode desarquivar sozinha."""
    await _robo(db, modo="ligado")
    await db.commit()
    editor = await make_user(email="eduardo@davinci-test.com", permissions=_perms(edit=True))
    auth_as(editor)

    r = await client.patch(f"/api/ouvidoria/robos/{ROBO}", json={"arquivado": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["arquivado_em"] is not None
    assert body["arquivado_por"] == "eduardo@davinci-test.com"
    assert body["modo"] == "ligado"  # arquivar não desliga

    # A linha continua no banco e na listagem (quem esconde é a tela), e o
    # GET /robos roda o sincronizar_catalogo antes de listar.
    lista = (await client.get("/api/ouvidoria/robos")).json()
    arquivado = {b["chave"]: b for b in lista}[ROBO]
    assert arquivado["arquivado_em"] is not None and arquivado["modo"] == "ligado"

    # O robô arquivado continua rodando: uma rodada grava normalmente.
    async with svc.Rodada(db, ROBO) as rod:
        rod.resumo = "rodou arquivado"
    await db.commit()
    robo = await db.get(OuvidoriaRobo, ROBO)
    await db.refresh(robo)
    assert robo.ultima_rodada_resumo == "rodou arquivado" and robo.arquivado_em is not None

    r = await client.patch(f"/api/ouvidoria/robos/{ROBO}", json={"arquivado": False})
    assert r.status_code == 200, r.text
    assert r.json()["arquivado_em"] is None and r.json()["arquivado_por"] is None


async def test_router_rodar_agora_agenda_o_runner(client, make_user, auth_as, db, monkeypatch):
    await _robo(db)
    chamadas: list[str] = []

    async def _sweep() -> dict:
        chamadas.append("rodou")
        return {"pedidos": 1}

    monkeypatch.setitem(router_mod.RUNNERS, ROBO, lambda: _sweep)
    editor = await make_user(permissions=_perms(edit=True))
    auth_as(editor)
    r = await client.post(f"/api/ouvidoria/robos/{ROBO}/rodar")
    assert r.status_code == 200, r.text
    assert r.json() == {"agendado": True, "chave": ROBO}
    assert chamadas == ["rodou"]  # o BackgroundTask roda antes da resposta sair no ASGITransport

    monkeypatch.delitem(router_mod.RUNNERS, ROBO)
    assert (await client.post(f"/api/ouvidoria/robos/{ROBO}/rodar")).status_code == 409


async def test_router_rodar_agora_recusa_em_andamento_e_cooldown(
    client, make_user, auth_as, db, monkeypatch
):
    """Uma rodada custa ~150 chamadas de marketplace: clique repetido não
    pode virar rodada repetida. Rodada do cron em andamento (advisory lock),
    rodada deste processo em andamento e rodada recente → 409 com código
    que a tela traduz."""
    from app.db import session_scope
    from app.services.advisory_lock import SYNC_NAMESPACE
    from app.services.vigia_importacao import _SWEEP_LOCK_KEY

    robo = await _robo(db)
    chamadas: list[str] = []

    async def _sweep() -> dict:
        chamadas.append("rodou")
        return {"pedidos": 1}

    monkeypatch.setitem(router_mod.RUNNERS, ROBO, lambda: _sweep)
    editor = await make_user(permissions=_perms(edit=True))
    auth_as(editor)

    # O cron está no meio de uma rodada (lock preso noutra sessão — uma só
    # pra isso, como no sweep; sai do `with` e o lock solta).
    async with session_scope() as trava:
        got = (
            await trava.execute(
                text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
                {"ns": SYNC_NAMESPACE, "key": _SWEEP_LOCK_KEY},
            )
        ).scalar()
        assert got
        r = await client.post(f"/api/ouvidoria/robos/{ROBO}/rodar")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "rodada_em_andamento"
    assert chamadas == []

    # Este processo já está rodando o robô.
    router_mod._EM_EXECUCAO.add(ROBO)
    try:
        r = await client.post(f"/api/ouvidoria/robos/{ROBO}/rodar")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "rodada_em_andamento"
    finally:
        router_mod._EM_EXECUCAO.discard(ROBO)

    # Rodada terminou há 10 s: espera.
    robo.ultima_rodada_em = _t(seconds=10)
    await db.commit()
    r = await client.post(f"/api/ouvidoria/robos/{ROBO}/rodar")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "rodada_recente"
    robo.ultima_rodada_em = _t(minutes=5)
    await db.commit()
    r = await client.post(f"/api/ouvidoria/robos/{ROBO}/rodar")
    assert r.status_code == 200 and chamadas == ["rodou"]
    assert ROBO not in router_mod._EM_EXECUCAO  # liberado no finally

    # O runner devolveu lock_busy (o cron pegou o lock no meio): só log.
    async def _ocupado() -> dict:
        return {"skipped": "lock_busy"}

    monkeypatch.setitem(router_mod.RUNNERS, ROBO, lambda: _ocupado)
    r = await client.post(f"/api/ouvidoria/robos/{ROBO}/rodar")
    assert r.status_code == 200 and ROBO not in router_mod._EM_EXECUCAO


async def test_router_ocorrencias_resumo_por_robo_e_botoes(client, make_user, auth_as, db):
    await _robo(db)
    a = await _reg(db, "tiktok:1", titulo="Pago 14:08 (R$ 739,19) e não caiu no Bling")
    await _reg(db, "shopee:2", plataforma="shopee", conta="Shopee mini")
    s = await _reg(db, "tiktok:3", agora=_t(days=1))
    await svc.fechar_nao_vistas(db, ROBO, {"tiktok:1", "shopee:2"})
    await db.commit()
    editor = await make_user(permissions=_perms(edit=True))
    auth_as(editor)

    r = await client.get("/api/ouvidoria/ocorrencias")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and {i["chave"] for i in body["itens"]} == {"tiktok:1", "shopee:2"}
    assert body["itens"][0]["robo_nome"] == svc.ROBOS[ROBO].nome
    assert body["resumo"]["abertas"] == 2 and body["resumo"]["sumiram_7d"] == 1
    # Os chips da tela trazem TODOS os robôs (inclusive os que não acharam
    # nada), em ordem de nome; as abertas são as do vigia.
    por_robo = {p["chave"]: p for p in body["por_robo"]}
    assert set(por_robo) == set(svc.ROBOS)
    assert por_robo[ROBO] == {"chave": ROBO, "nome": svc.ROBOS[ROBO].nome, "abertas": 2}
    assert por_robo["vigia_correios"]["abertas"] == 0

    r = await client.get("/api/ouvidoria/ocorrencias", params={"status": "fechadas"})
    assert [i["id"] for i in r.json()["itens"]] == [str(s.id)]
    r = await client.get(
        "/api/ouvidoria/ocorrencias", params={"status": "todas", "plataforma": "tiktok"}
    )
    assert {i["chave"] for i in r.json()["itens"]} == {"tiktok:1", "tiktok:3"}
    r = await client.get("/api/ouvidoria/ocorrencias", params={"q": "739,19"})
    assert [i["chave"] for i in r.json()["itens"]] == ["tiktok:1"]
    r = await client.get("/api/ouvidoria/ocorrencias", params={"conta": "mini"})
    assert [i["chave"] for i in r.json()["itens"]] == ["shopee:2"]

    r = await client.post(f"/api/ouvidoria/ocorrencias/{a.id}/tratar")
    assert r.status_code == 200, r.text
    assert r.json()["fechamento"] == "tratada" and r.json()["fechada_por"]
    assert (await client.post(f"/api/ouvidoria/ocorrencias/{a.id}/ignorar")).status_code == 409
    assert (await client.post(f"/api/ouvidoria/ocorrencias/{uuid4()}/tratar")).status_code == 404
    r = await client.post(f"/api/ouvidoria/ocorrencias/{a.id}/reabrir")
    assert r.status_code == 200 and r.json()["fechamento"] is None
    r = await client.post(f"/api/ouvidoria/ocorrencias/{s.id}/ignorar")
    assert r.status_code == 409  # já fechada (sumiu)
    assert (await client.get("/api/ouvidoria/ocorrencias")).json()["resumo"]["abertas"] == 2
