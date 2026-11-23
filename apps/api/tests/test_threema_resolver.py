"""`threema_envios.resolver` (tarefa 16, C5): quem recebe AGORA.

* não migrado = o legado exato, sem ler o Cadastro;
* migrado: o Cadastro é a fonte; o legado só volta na emergência (linha ou
  env), com o banco fora sem cópia boa recente, ou sem semente;
* teto de 2 s, sessão própria (a de quem chama não fica envenenada) e a cópia
  da última resolução boa por até 24 h;
* liga/desliga: `ativo` NULL segue a chave do .env (conferência), modo e
  config do robô; quem fica de fora (suspenso, desativado, sem Threema,
  Threema inválido, contato inativo) vira aviso; cenário M5 = ninguém, sem
  cair no legado; escopo vazio cai no padrão; grupo abre; dedupe na ordem.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.models import (
    OuvidoriaRobo,
    ThreemaCadastroEstado,
    ThreemaContato,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaGrupo,
    ThreemaGrupoMembro,
    User,
    UserRole,
    UserStatus,
)
from app.services import threema_catalogo, threema_envios
from app.services.threema_catalogo import CATALOGO, K
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)


def migrar(monkeypatch, *chaves: str) -> None:
    """O commit da fatia vira `migrado=True` no código; aqui, no catálogo vivo."""
    for chave in chaves:
        monkeypatch.setitem(
            threema_catalogo.CATALOGO, chave, replace(CATALOGO[chave], migrado=True)
        )


def desmigrar(monkeypatch, *chaves: str) -> None:
    """O código de ANTES da fatia (a semente roda com ele, e os testes do
    envio não migrado não dependem de qual fatia já foi publicada)."""
    for chave in chaves:
        monkeypatch.setitem(
            threema_catalogo.CATALOGO, chave, replace(CATALOGO[chave], migrado=False)
        )


async def semeado(db, chave: str, *refs, regra=None, ativo=None) -> None:
    """Põe linhas de quem recebe e carimba a semente (como o `semear`)."""
    await threema_envios.sincronizar(db)
    linha = await db.get(ThreemaEnvio, chave)
    linha.semeado_em = datetime.now(UTC)
    linha.ativo = ativo
    for i, ref in enumerate(refs):
        kw = (
            {"tipo": "usuario", "user_id": ref.id}
            if isinstance(ref, User)
            else {"tipo": "contato", "contato_id": ref.id}
            if isinstance(ref, ThreemaContato)
            else {"tipo": "grupo", "grupo_id": ref.id}
        )
        db.add(
            ThreemaEnvioDestinatario(envio_chave=chave, logistica_status_id=regra, ordem=i, **kw)
        )
    await db.commit()


async def _usuario(db, nome, threema_id, **kw) -> User:
    email = f"{nome.replace(' ', '.')}@davinci-test.com"
    u = User(
        open_id=kw.pop("open_id", f"email:{email}"),
        email=email,
        name=nome,
        role=kw.pop("role", UserRole.USER),
        status=kw.pop("status", UserStatus.ACTIVE),
        threema=threema_id,
        **kw,
    )
    db.add(u)
    await db.commit()
    return u


@pytest.fixture(autouse=True)
def _cache_limpo():
    threema_envios.limpar_cache()
    yield
    threema_envios.limpar_cache()


@pytest.fixture
def _settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "threema_recipients", "TSTL0001,TSTM0005")
    monkeypatch.setattr(s, "threema_recipient_names", "TSTV0009:Só no env")
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    return s


def _sem_ler_o_cadastro(monkeypatch):
    async def _proibido(*a, **kw):
        raise AssertionError("não devia ler o Cadastro")

    monkeypatch.setattr(threema_envios, "_ler_cadastro_isolado", _proibido)


# ─────────────────────────────────────────────────────────── legado


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_nao_migrado_e_o_legado_exato_sem_ler_o_cadastro(db, monkeypatch):
    _sem_ler_o_cadastro(monkeypatch)
    # mesmo com Cadastro semeado e diferente: não migrado não lê o banco
    roma = ThreemaContato(nome="Roma", threema_id="TSTR0008", origem="tela")
    db.add(roma)
    await db.commit()
    await semeado(db, K.LOGISTICA_CORREIOS_GRAVE, roma)
    desmigrar(monkeypatch, K.LOGISTICA_CORREIOS_GRAVE)  # o código antes da F1
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (r.via, r.motivo, r.ativo) == ("legado", threema_envios.NAO_MIGRADO, True)
    assert r.ids == ("TSTL0001", "TSTM0005")
    assert r.fonte == "THREEMA_RECIPIENTS do servidor"
    assert r.destinos == ()  # sem nomes: o envio não precisa


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_sem_sessao_o_legado_abre_a_propria(db, monkeypatch):
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE)
    assert r.ids == ("TSTL0001", "TSTM0005")


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_quem_recebe_traz_nomes_tambem_no_legado(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "threema_recipients", "TSTL0001,TSTR0008,TSTV0009,TSTX0010")
    await _usuario(db, "londres", "tstl0001")
    db.add(ThreemaContato(nome="Roma", threema_id="TSTR0008", origem="tela"))
    await db.commit()
    r = await threema_envios.quem_recebe(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert [(x.id, x.nome, x.tipo) for x in r.destinos] == [
        ("TSTL0001", "londres", "usuario"),
        ("TSTR0008", "Roma", "contato"),
        ("TSTV0009", "Só no env", "env"),
        ("TSTX0010", None, "desconhecido"),
    ]
    assert await threema_envios.ativo(K.LOGISTICA_CORREIOS_GRAVE, session=db) is True


# ─────────────────────────────────────────────────────────── migrado


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_migrado_le_o_cadastro_e_nao_o_env(db, monkeypatch):
    migrar(monkeypatch, K.LOGISTICA_CORREIOS_GRAVE)
    harry = await _usuario(db, "harry", "TSTH0002")
    roma = ThreemaContato(nome="Roma", threema_id="TSTR0008", origem="tela")
    db.add(roma)
    await db.commit()
    await semeado(db, K.LOGISTICA_CORREIOS_GRAVE, harry, roma)
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (r.via, r.motivo, r.ativo) == ("cadastro", None, True)
    assert r.ids == ("TSTH0002", "TSTR0008")
    assert [x.nome for x in r.destinos] == ["harry", "Roma"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_emergencia_pela_linha_volta_ao_legado_sem_restart(db, monkeypatch):
    migrar(monkeypatch, K.LOGISTICA_CORREIOS_GRAVE)
    harry = await _usuario(db, "harry", "TSTH0002")
    await semeado(db, K.LOGISTICA_CORREIOS_GRAVE, harry)
    db.add(ThreemaCadastroEstado(id=1, usar_legado=True))
    await db.commit()
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (r.via, r.motivo) == ("legado", threema_envios.EMERGENCIA)
    assert r.ids == ("TSTL0001", "TSTM0005")
    # desligar a emergência vale no próximo resolver
    await db.execute(text("UPDATE threema_cadastro_estado SET usar_legado = false"))
    await db.commit()
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (r.via, r.ids) == ("cadastro", ("TSTH0002",))


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_emergencia_pelo_env_e_o_piso_mesmo_sem_banco(db, monkeypatch):
    migrar(monkeypatch, K.LOGISTICA_CORREIOS_GRAVE)
    monkeypatch.setattr(get_settings(), "threema_cadastro_ativo", False)
    _sem_ler_o_cadastro(monkeypatch)
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE)
    assert (r.via, r.motivo, r.ids) == (
        "legado",
        threema_envios.EMERGENCIA,
        ("TSTL0001", "TSTM0005"),
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_sem_semente_usa_o_legado(db, monkeypatch):
    migrar(monkeypatch, K.LOGISTICA_CORREIOS_GRAVE)
    # sem linha nenhuma
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (r.via, r.motivo) == ("legado", threema_envios.SEM_SEMENTE)
    # linha sincronizada, sem `semeado_em`
    await threema_envios.sincronizar(db)
    await db.commit()
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (r.via, r.motivo, r.ids) == (
        "legado",
        threema_envios.SEM_SEMENTE,
        ("TSTL0001", "TSTM0005"),
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_banco_fora_usa_a_copia_boa_e_sem_copia_o_legado(db, monkeypatch):
    migrar(monkeypatch, K.LOGISTICA_CORREIOS_GRAVE)
    harry = await _usuario(db, "harry", "TSTH0002")
    await semeado(db, K.LOGISTICA_CORREIOS_GRAVE, harry)
    boa = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert boa.via == "cadastro"

    async def _fora(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(threema_envios, "_ler_cadastro_isolado", _fora)
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (r.via, r.ids, r.ativo) == ("cache", ("TSTH0002",), True)
    assert any("fora do ar" in a for a in r.avisos)

    # cópia velha (mais de 24 h) não vale
    monkeypatch.setattr(threema_envios, "CACHE_VALIDADE_S", 0)
    time.sleep(0.01)
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert (r.via, r.motivo, r.ids) == (
        "legado",
        threema_envios.CADASTRO_INDISPONIVEL,
        ("TSTL0001", "TSTM0005"),
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_teto_de_tempo_na_leitura_do_cadastro(db, monkeypatch):
    migrar(monkeypatch, K.LOGISTICA_CORREIOS_GRAVE)

    async def _lento(*a, **kw):
        await asyncio.sleep(5)  # pool esgotado / banco travado

    monkeypatch.setattr(threema_envios, "_ler_cadastro_isolado", _lento)
    monkeypatch.setattr(threema_envios, "TIMEOUT_CADASTRO_S", 0.2)
    inicio = time.monotonic()
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert time.monotonic() - inicio < 2
    assert (r.via, r.motivo) == ("legado", threema_envios.CADASTRO_INDISPONIVEL)
    assert threema_envios.TIMEOUT_CADASTRO_S == 0.2


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_statement_timeout_do_cadastro_nao_envenena_a_sessao_de_quem_chama(db, monkeypatch):
    migrar(monkeypatch, K.LOGISTICA_CORREIOS_GRAVE)
    await threema_envios.sincronizar(db)
    await db.commit()
    # a sessão de quem chama está no meio de uma transação
    assert (await db.execute(text("SELECT 41 + 1"))).scalar_one() == 42

    original = threema_envios._ler_cadastro

    async def _demorado(d, escopo, session, **kw):
        await session.execute(text("SELECT pg_sleep(3)"))  # o banco corta em 2 s
        return await original(d, escopo, session, **kw)

    monkeypatch.setattr(threema_envios, "_ler_cadastro", _demorado)
    monkeypatch.setattr(threema_envios, "TIMEOUT_CADASTRO_S", 10)
    inicio = time.monotonic()
    r = await threema_envios.resolver(K.LOGISTICA_CORREIOS_GRAVE, session=db)
    assert time.monotonic() - inicio < 3.5  # foi o statement_timeout de 2 s
    assert (r.via, r.motivo) == ("legado", threema_envios.CADASTRO_INDISPONIVEL)
    # e a sessão de quem chama segue boa
    assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
    await db.commit()


# ─────────────────────────────────────────────────────────── liga/desliga


@pytest.mark.asyncio
async def test_ativo_nulo_segue_a_chave_do_env_da_conferencia(db, monkeypatch):
    chave = K.MARKETING_CONFERENCIA_SHOPEE
    migrar(monkeypatch, chave)
    harry = await _usuario(db, "harry", "TSTH0002")
    await semeado(db, chave, harry)
    s = get_settings()
    for env, ativo, esperado in (
        (False, None, False),
        (True, None, True),
        (True, False, False),
        (False, True, True),
    ):
        monkeypatch.setattr(s, "conferencia_shopee_threema", env)
        await db.execute(
            text("UPDATE threema_envio SET ativo = :a WHERE chave = :c"), {"a": ativo, "c": chave}
        )
        await db.commit()
        r = await threema_envios.resolver(chave, session=db)
        assert r.ativo is esperado, (env, ativo)
        assert r.ids == (("TSTH0002",) if esperado else ())
        assert r.motivo == (None if esperado else threema_envios.DESLIGADO)


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_robo_modo_e_robo_config_leem_a_fonte_de_hoje(db, monkeypatch):
    c = await montar(db, monkeypatch)
    migrar(monkeypatch, K.DENUNCIA_RELATORIO_DIARIO, K.MARGEM_MARGEM_ALTA)
    harry = c.usuarios["harry"]
    await semeado(db, K.DENUNCIA_RELATORIO_DIARIO, harry)
    await semeado(db, K.MARGEM_MARGEM_ALTA, harry)
    # cenário: relatório desligado na Ouvidoria e caixinha da margem alta desligada
    for chave in (K.DENUNCIA_RELATORIO_DIARIO, K.MARGEM_MARGEM_ALTA):
        r = await threema_envios.resolver(chave, session=db)
        assert (r.ativo, r.motivo) == (False, threema_envios.DESLIGADO), chave
    rel = await db.get(OuvidoriaRobo, "denuncia_relatorio_threema")
    rel.modo = "ligado"
    margem = await db.get(OuvidoriaRobo, "vigia_margem")
    margem.config = {**margem.config, "avisar_margem_alta": True}
    await db.commit()
    for chave in (K.DENUNCIA_RELATORIO_DIARIO, K.MARGEM_MARGEM_ALTA):
        r = await threema_envios.resolver(chave, session=db)
        assert (r.ativo, r.ids) == (True, (ID["harry"],)), chave


# ─────────────────────────────────────────────────────────── expansão


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_quem_nao_pode_receber_fica_de_fora_com_aviso(db, monkeypatch):
    chave = K.LOGISTICA_VIGIA_AUTOMACAO
    migrar(monkeypatch, chave)
    ok = await _usuario(db, "valido", "TSTA0001")
    suspenso = await _usuario(db, "suspenso", "TSTA0002", status=UserStatus.SUSPENDED)
    desativado = await _usuario(db, "desativado", "TSTA0003", disabled_at=datetime.now(UTC))
    sem = await _usuario(db, "sem", None)
    torto = await _usuario(db, "torto", "TSTA04")
    sistema = await _usuario(db, "sistema", "TSTA0005", open_id="system:robo")
    inativo = ThreemaContato(nome="Inativo", threema_id="TSTA0006", origem="tela", ativo=False)
    db.add(inativo)
    await db.commit()
    await semeado(db, chave, suspenso, desativado, sem, torto, sistema, inativo, ok)
    r = await threema_envios.resolver(chave, session=db)
    assert r.ids == ("TSTA0001",)
    assert set(r.avisos) == {
        "suspenso está suspenso",
        "desativado está desativado",
        "sem sem Threema",
        "torto com Threema inválido",
        "sistema é usuário do sistema",
        "contato Inativo inativo",
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_cenario_m5_suspenso_nao_recebe_nem_pela_reserva(db, monkeypatch):
    """ "cairo sa" suspenso: o ID dele está no THREEMA_RECIPIENTS (e num
    "cairo" antigo também suspenso), mas o Cadastro diz ninguém — e ninguém
    é ninguém: sem cair no legado."""
    chave = K.LOGISTICA_CORREIOS_GRAVE
    migrar(monkeypatch, chave)
    await _usuario(db, "cairo", "TSTM0005", status=UserStatus.SUSPENDED)
    cairo_sa = await _usuario(db, "cairo sa", "TSTM0005")
    await semeado(db, chave, cairo_sa)
    assert (await threema_envios.resolver(chave, session=db)).ids == ("TSTM0005",)
    cairo_sa.status = UserStatus.SUSPENDED
    await db.commit()
    threema_envios.limpar_cache()
    r = await threema_envios.resolver(chave, session=db)
    assert (r.via, r.ativo, r.ids, r.motivo) == (
        "cadastro",
        True,
        (),
        threema_envios.NINGUEM_VALIDO,
    )
    assert "TSTM0005" in get_settings().threema_recipients
    assert r.avisos == ("cairo sa está suspenso",)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_settings")
async def test_grupo_abre_em_usuarios_e_contatos_com_dedupe_na_ordem(db, monkeypatch):
    chave = K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING
    migrar(monkeypatch, chave)
    a = await _usuario(db, "a", "TSTA0001")
    b = await _usuario(db, "b", "TSTB0002")
    roma = ThreemaContato(nome="Roma", threema_id="TSTR0008", origem="tela")
    g = ThreemaGrupo(nome="Estoque e NF")
    db.add_all([roma, g])
    await db.flush()
    db.add_all(
        [
            ThreemaGrupoMembro(grupo_id=g.id, tipo="usuario", user_id=b.id, ordem=0),
            ThreemaGrupoMembro(grupo_id=g.id, tipo="contato", contato_id=roma.id, ordem=1),
            ThreemaGrupoMembro(grupo_id=g.id, tipo="usuario", user_id=a.id, ordem=2),
        ]
    )
    await db.commit()
    # `a` direto antes do grupo, e `roma` direto depois: cada um sai uma vez,
    # na 1ª posição em que apareceu
    await semeado(db, chave, a, g, roma)
    r = await threema_envios.resolver(chave, session=db)
    assert r.ids == ("TSTA0001", "TSTB0002", "TSTR0008")
    # grupo vazio vira aviso
    vazio = ThreemaGrupo(nome="Vazio")
    db.add(vazio)
    await db.commit()
    await db.execute(text("DELETE FROM threema_envio_destinatario"))
    await db.commit()
    await semeado(db, chave, vazio)
    threema_envios.limpar_cache()
    r = await threema_envios.resolver(chave, session=db)
    assert (r.ids, r.motivo, r.avisos) == (
        (),
        threema_envios.NINGUEM_VALIDO,
        ("grupo Vazio sem ninguém",),
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("limpar_ouvidoria")
async def test_escopo_da_regra_e_a_lista_padrao(db, monkeypatch):
    c = await montar(db, monkeypatch)
    chave = K.LOGISTICA_MENSAGEM_REGRA
    migrar(monkeypatch, chave)
    u = c.usuarios
    await semeado(db, chave, u["churchill"])  # padrão
    await semeado(db, chave, u["harry"], u["joana"], regra=c.regras["com_pessoa"].id)
    # regra com lista em que ninguém é válido → padrão, com aviso
    await semeado(db, chave, u["cairo"], regra=c.regras["pessoa_sem_mensagem"].id)

    r = await threema_envios.resolver(chave, escopo=c.regras["com_pessoa"].id, session=db)
    assert r.ids == (ID["harry"], ID["joana"])
    assert r.escopo == str(c.regras["com_pessoa"].id)
    r = await threema_envios.resolver(chave, escopo=c.regras["pessoa_sem_mensagem"].id, session=db)
    assert r.ids == (ID["churchill"],)
    assert "ninguém válido na lista da regra: vale a lista padrão do envio" in r.avisos
    r = await threema_envios.resolver(chave, escopo=c.regras["sem_pessoa"].id, session=db)
    assert (r.ids, r.avisos) == ((ID["churchill"],), ())
    r = await threema_envios.resolver(chave, session=db)
    assert r.ids == (ID["churchill"],)


@pytest.mark.asyncio
async def test_chave_desconhecida_quebra_alto(db):
    with pytest.raises(KeyError, match="envio desconhecido"):
        await threema_envios.resolver("logistica.nao_existe", session=db)
