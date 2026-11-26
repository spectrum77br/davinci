"""Registro de cada envio do Threema (tarefa 16, C1b).

`ThreemaClient.send_to_all` grava uma linha por destinatário em
`threema_envio_registro`, com a marca do envio (`threema_registro.envio`) que
estiver ativa. Aqui:

* o registro em si (httpx falso pelo respx): ok, status, message_id, erro de
  configuração, erro de rede no meio, banco fora, desligado, segredo, ator,
  marcas aninhadas e sem marca;
* o teste ESTÁTICO: todo `send_to_all(` do servidor está dentro de uma marca
  (ou numa função de envio marcada por TODOS os que a chamam), a chave de cada
  marca existe no catálogo e todo envio gerenciado do servidor tem marca;
* a migration 0505 cria a tabela igual ao modelo e desce;
* a limpeza de 180 dias.
"""

# ruff: noqa: S608
from __future__ import annotations

import ast
import asyncio
import functools
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.historico import contexto as historico_contexto
from app.historico import sql as historico_sql
from app.models import Base, ThreemaContato, ThreemaEnvioRegistro, User, UserRole, UserStatus
from app.services import threema, threema_registro
from app.services.threema_catalogo import CATALOGO, K, constantes

SEGREDO = "s3cr3t-registro-xyz"
_API = Path(__file__).resolve().parent.parent
_APP = _API / "app"


def _cliente(contexto: str | None = "logistica") -> threema.ThreemaClient:
    return threema.ThreemaClient(gateway_id="*TSTGW01", secret=SEGREDO, contexto=contexto)


async def _linhas(db: AsyncSession) -> list[ThreemaEnvioRegistro]:
    # fecha a transação da sessão do teste (sem expirar os objetos): o
    # registro grava em sessão própria e a próxima leitura enxerga o commit.
    await db.commit()
    return list(
        (await db.execute(select(ThreemaEnvioRegistro).order_by(ThreemaEnvioRegistro.id)))
        .scalars()
        .all()
    )


async def _usuario(db: AsyncSession, *, nome: str, threema_id: str, ativo: bool = True) -> User:
    email = f"reg-{uuid.uuid4().hex[:8]}@davinci-test.com"
    u = User(
        open_id=f"email:{email}",
        email=email,
        name=nome,
        role=UserRole.USER,
        status=UserStatus.ACTIVE if ativo else UserStatus.SUSPENDED,
        threema=threema_id,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ─────────────────────────────────────────────────────────── o registro


@pytest.mark.asyncio
async def test_uma_linha_por_destinatario_com_status_e_message_id(db, monkeypatch):
    londres = await _usuario(db, nome="Londres Teste", threema_id="tsta0001")
    roma = ThreemaContato(nome="Roma contato", threema_id="TSTD0004", origem="tela")
    db.add(roma)
    await db.commit()
    monkeypatch.setattr(
        threema_registro,
        "get_settings",
        lambda: SimpleNamespace(
            threema_registro_ativo=True, threema_recipient_names="TSTC0003:Roma do env"
        ),
    )

    def _resp(request: httpx.Request) -> httpx.Response:
        if b"to=TSTB0002" in request.content:
            return httpx.Response(402, text="no credits")
        return httpx.Response(200, text="0a1b2c3d4e5f6789")

    texto = "Logística — automação parada:\nrecarregar: último sucesso há 99 min"
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(side_effect=_resp)
        with threema_registro.envio(K.LOGISTICA_VIGIA_AUTOMACAO):
            out = await _cliente().send_to_all(
                texto, ["TSTA0001", "TSTB0002", "TSTC0003", "TSTD0004"]
            )
    assert out == {"sent": ["TSTA0001", "TSTC0003", "TSTD0004"], "failed": ["TSTB0002"]}

    linhas = await _linhas(db)
    assert len(linhas) == 4
    assert len({r.lote_id for r in linhas}) == 1
    a, b, c, d = linhas
    for r in linhas:
        assert r.envio_chave == K.LOGISTICA_VIGIA_AUTOMACAO
        assert r.contexto == "logistica"
        assert r.canal
        assert r.onde_roda == "servidor"
        assert r.origem == "automatico"
        assert r.via is None
        assert r.ator_id is None and r.ator_nome is None
        # só a 1ª linha e o tamanho, nunca o texto inteiro
        assert r.titulo == "Logística — automação parada:"
        assert r.bytes == len(texto.encode("utf-8"))
    assert (a.destinatario_id, a.ok, a.http_status, a.message_id) == (
        "TSTA0001",
        True,
        200,
        "0a1b2c3d4e5f6789",
    )
    assert a.destinatario_nome == "Londres Teste" and a.user_id == londres.id
    assert a.contato_id is None
    # 402 (sem crédito) fica como falha com o status
    assert (b.destinatario_id, b.ok, b.http_status, b.erro) == ("TSTB0002", False, 402, "http_402")
    assert b.destinatario_nome is None and b.message_id is None
    # sem usuário: o nome do THREEMA_RECIPIENT_NAMES
    assert c.destinatario_nome == "Roma do env" and c.user_id is None and c.ok
    assert c.contato_id is None
    # contato externo: o nome e o id dele (renomear não perde a ligação)
    assert (d.destinatario_nome, d.user_id, d.contato_id) == ("Roma contato", None, roma.id)


@pytest.mark.asyncio
async def test_usuario_ativo_vence_o_suspenso_no_nome(db):
    await _usuario(db, nome="Cairo antigo", threema_id="TSTM0005", ativo=False)
    ativo = await _usuario(db, nome="Cairo SA", threema_id="TSTM0005")
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
        await _cliente().send_to_all("oi", ["TSTM0005"])
    (r,) = await _linhas(db)
    assert (r.destinatario_nome, r.user_id) == ("Cairo SA", ativo.id)


@pytest.mark.asyncio
async def test_erro_de_configuracao_grava_uma_linha_e_relanca(db):
    cli = _cliente()
    cli.gateway_id = ""
    with threema_registro.envio(K.DEVOLUCOES_PRAZO_CONTESTACAO):
        with pytest.raises(threema.ThreemaConfigError):
            await cli.send_to_all("aviso", ["TSTA0001"])
    (r,) = await _linhas(db)
    assert r.envio_chave == K.DEVOLUCOES_PRAZO_CONTESTACAO
    assert r.destinatario_id is None and r.ok is False
    assert r.erro == "threema_gateway_id_missing"


@pytest.mark.asyncio
async def test_rede_cai_no_meio_grava_o_que_tem_e_relanca(db):
    chamadas = []

    def _resp(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        if len(chamadas) == 2:
            raise httpx.ReadTimeout("tempo esgotado", request=request)
        return httpx.Response(200, text="mid-1")

    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(side_effect=_resp)
        with pytest.raises(httpx.ReadTimeout):
            await _cliente().send_to_all("aviso", ["TSTA0001", "TSTB0002", "TSTC0003"])
    linhas = await _linhas(db)
    # o 1º chegou, o 2º caiu, o 3º nem foi tentado (como hoje: a exceção sobe)
    assert [(r.destinatario_id, r.ok) for r in linhas] == [
        ("TSTA0001", True),
        ("TSTB0002", False),
    ]
    assert linhas[1].erro.startswith("ReadTimeout")
    assert linhas[1].http_status is None


@pytest.mark.asyncio
async def test_banco_fora_nao_quebra_o_envio(db, monkeypatch):
    async def _quebra(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(threema_registro, "_inserir", _quebra)
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
        out = await _cliente().send_to_all("aviso", ["TSTA0001"])
    assert out == {"sent": ["TSTA0001"], "failed": []}
    assert await _linhas(db) == []


@pytest.mark.asyncio
async def test_banco_lento_tem_teto(db, monkeypatch):
    async def _lento(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(threema_registro, "_inserir", _lento)
    monkeypatch.setattr(threema_registro, "_TIMEOUT_S", 0.05)
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
        out = await asyncio.wait_for(_cliente().send_to_all("aviso", ["TSTA0001"]), timeout=5)
    assert out["sent"] == ["TSTA0001"]


@pytest.mark.asyncio
async def test_banco_lento_em_laco_paga_o_teto_uma_vez_so(db, monkeypatch):
    """Disjuntor: os pontos que mandam em laço (Informar da Margem, 1 por
    pedido, dentro da requisição) não somam o teto de cada envio. Depois da
    1ª falha, o registro fica pulado por `_PAUSA_S`; passado isso, volta."""
    tentativas = 0

    async def _lento(*a, **k):
        nonlocal tentativas
        tentativas += 1
        await asyncio.sleep(30)

    relogio = [1000.0]
    original = threema_registro._inserir
    monkeypatch.setattr(threema_registro, "_relogio", lambda: relogio[0])
    monkeypatch.setattr(threema_registro, "_inserir", _lento)
    monkeypatch.setattr(threema_registro, "_TIMEOUT_S", 0.2)
    inicio = asyncio.get_running_loop().time()
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
        cli = _cliente()
        for _ in range(30):
            out = await cli.send_to_all("aviso", ["TSTA0001"])
            assert out["sent"] == ["TSTA0001"]
        gasto = asyncio.get_running_loop().time() - inicio
        assert tentativas == 1, "só a 1ª chamada tentou gravar"
        assert gasto < 3, f"30 envios com o banco lento levaram {gasto:.1f} s"
        # passada a pausa, tenta de novo (e o banco voltou)
        relogio[0] += threema_registro._PAUSA_S + 1
        monkeypatch.setattr(threema_registro, "_inserir", original)
        monkeypatch.setattr(threema_registro, "_TIMEOUT_S", 3.0)
        await cli.send_to_all("de volta", ["TSTA0001"])
    linhas = await _linhas(db)
    assert [r.titulo for r in linhas] == ["de volta"]


@pytest.mark.asyncio
async def test_desligado_nao_grava(db, monkeypatch):
    monkeypatch.setattr(
        threema_registro,
        "get_settings",
        lambda: SimpleNamespace(threema_registro_ativo=False, threema_recipient_names=""),
    )
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
        out = await _cliente().send_to_all("aviso", ["TSTA0001"])
    assert out["sent"] == ["TSTA0001"]
    assert await _linhas(db) == []


@pytest.mark.asyncio
async def test_segredo_nunca_vai_para_o_registro(db):
    def _resp(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"falhou com secret={SEGREDO}", request=request)

    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(side_effect=_resp)
        with pytest.raises(httpx.ConnectError):
            await _cliente().send_to_all(f"texto\ncom {SEGREDO} no meio", ["TSTA0001"])
    (r,) = await _linhas(db)
    valores = [str(getattr(r, c.key)) for c in ThreemaEnvioRegistro.__table__.columns]
    assert all(SEGREDO not in v for v in valores)
    assert "***" in r.erro


@pytest.mark.asyncio
async def test_envio_de_pessoa_grava_ator_e_req_id(db):
    pessoa = await _usuario(db, nome="Fulano Admin", threema_id="TSTZ0009")
    ator = historico_contexto.Ator(metodo="POST", caminho="/api/informar/logistica/enviar")
    ator.user_id, ator.nome = pessoa.id, "Fulano Admin"
    token = historico_contexto.abrir(ator)
    try:
        with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
            router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
            with threema_registro.envio(K.LOGISTICA_INFORMAR):
                await _cliente().send_to_all("Informar", ["TSTA0001"])
    finally:
        historico_contexto.fechar(token)
    (r,) = await _linhas(db)
    assert r.origem == "manual"
    assert (r.ator_id, r.ator_nome, r.req_id) == (pessoa.id, "Fulano Admin", ator.req_id)


@pytest.mark.asyncio
async def test_marcas_aninhadas_vale_a_mais_interna_e_sem_marca_fica_nulo(db):
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(200, text="mid"))
        with threema_registro.envio(K.LOGISTICA_CORREIOS_GRAVE):
            with threema_registro.envio(K.DEVOLUCOES_PACOTE_VOLTA_GRAVE, escopo="regra-1"):
                await _cliente().send_to_all("interna", ["TSTA0001"])
            await _cliente().send_to_all("externa", ["TSTA0001"])
            # a marca atravessa tarefas criadas dentro do bloco
            await asyncio.gather(_cliente().send_to_all("tarefa", ["TSTA0001"]))
        with threema_registro.envio(K.DENUNCIA_RELATORIO_DIARIO, origem="teste"):
            await _cliente("chamados").send_to_all("teste", ["TSTA0001"])
        await _cliente().send_to_all("sem marca", ["TSTA0001"])
    assert threema_registro.marca_atual() is None
    linhas = await _linhas(db)
    assert [(r.titulo, r.envio_chave, r.escopo, r.origem) for r in linhas] == [
        ("interna", K.DEVOLUCOES_PACOTE_VOLTA_GRAVE, "regra-1", "automatico"),
        ("externa", K.LOGISTICA_CORREIOS_GRAVE, None, "automatico"),
        ("tarefa", K.LOGISTICA_CORREIOS_GRAVE, None, "automatico"),
        ("teste", K.DENUNCIA_RELATORIO_DIARIO, None, "teste"),
        ("sem marca", None, None, "automatico"),
    ]
    assert len({r.lote_id for r in linhas}) == 5


def test_marca_com_valor_fora_da_lista_nao_derruba():
    with threema_registro.envio(K.LOGISTICA_LOJA_MUDA, origem="robo", via="xyz") as m:
        assert (m.origem, m.via) == (None, None)
    assert threema_registro.marca_atual() is None


@pytest.mark.asyncio
async def test_limpeza_apaga_so_o_que_passou_de_180_dias(db):
    agora = datetime.now(UTC)
    for dias in (181, 179):
        db.add(
            ThreemaEnvioRegistro(
                criado_em=agora - timedelta(days=dias),
                lote_id=uuid.uuid4(),
                envio_chave=K.LOGISTICA_LOJA_MUDA,
                origem="automatico",
                ok=True,
            )
        )
    await db.commit()
    assert await threema_registro.limpar(db) == 1
    await db.commit()
    (r,) = await _linhas(db)
    assert r.criado_em > agora - timedelta(days=180)


def test_registro_fica_fora_do_historico_de_alteracoes():
    assert historico_sql.EXCLUIDAS.search("threema_envio_registro")


# ─────────────────────────────────────────────────────────── teste estático

# Funções que chamam `send_to_all` SEM marca própria, porque servem a mais de
# um envio: TODA chamada delas tem de estar dentro de uma marca.
_FUNCOES_DE_ENVIO = {
    ("services/logistica_track_sync.py", "avisar_graves"),
    ("services/nf_auto_enfileirar.py", "_enviar_aviso"),
    ("services/conferencia_shopee/threema_aviso.py", "_enviar_um_a_um"),
}
# Marca cuja chave não é uma constante `K.X` escrita ali: (arquivo, expressão).
# Cada uma tem o seu teste de cobertura logo abaixo.
_CHAVES_DINAMICAS = {
    ("routers/informar.py", "_CHAVE_ENVIO[contexto]"),
    ("services/ouvidoria.py", "chave_ouvidoria(robo_chave)"),
    ("services/conferencia_shopee/threema_aviso.py", "chave"),
    # O núcleo do Cadastro (C5): `enviar(chave, …)` valida a chave no catálogo.
    ("services/threema_envios.py", "chave"),
}
_FORA = {"services/threema.py", "services/threema_registro.py"}


@functools.cache
def _arvores() -> dict[str, ast.Module]:
    out = {}
    for p in sorted(_APP.rglob("*.py")):
        rel = p.relative_to(_APP).as_posix()
        if rel in _FORA:
            continue
        out[rel] = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
    return out


@functools.cache
def _pais_de(rel: str) -> dict[ast.AST, ast.AST]:
    return {f: p for p in ast.walk(_arvores()[rel]) for f in ast.iter_child_nodes(p)}


def _e_marca(expr: ast.AST) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    f = expr.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "envio"
        and isinstance(f.value, ast.Name)
        and f.value.id == "threema_registro"
    )


def _marcada(no: ast.AST, pais: dict) -> bool:
    atual = pais.get(no)
    while atual is not None:
        if isinstance(atual, (ast.With, ast.AsyncWith)) and any(
            _e_marca(i.context_expr) for i in atual.items
        ):
            return True
        atual = pais.get(atual)
    return False


def _funcao(no: ast.AST, pais: dict) -> str | None:
    atual = pais.get(no)
    while atual is not None:
        if isinstance(atual, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return atual.name
        atual = pais.get(atual)
    return None


def _chamadas(arvore: ast.AST, nome: str):
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            f = no.func
            if (isinstance(f, ast.Attribute) and f.attr == nome) or (
                isinstance(f, ast.Name) and f.id == nome
            ):
                yield no


def test_todo_send_to_all_do_servidor_tem_marca():
    sem_marca = []
    wrappers_achados = set()
    for rel, arvore in _arvores().items():
        for no in _chamadas(arvore, "send_to_all"):
            pais = _pais_de(rel)
            if _marcada(no, pais):
                continue
            fn = _funcao(no, pais)
            if (rel, fn) in _FUNCOES_DE_ENVIO:
                wrappers_achados.add((rel, fn))
                continue
            sem_marca.append(f"{rel}:{no.lineno} ({fn})")
    assert sem_marca == [], (
        "send_to_all sem `with threema_registro.envio(K.X):` — o registro gravaria "
        f"envio_chave NULL: {sem_marca}"
    )
    assert wrappers_achados == _FUNCOES_DE_ENVIO


def test_toda_chamada_das_funcoes_de_envio_tem_marca():
    arvores = _arvores()
    for arq, fn in _FUNCOES_DE_ENVIO:
        chamadas = []
        for rel, arvore in arvores.items():
            for no in _chamadas(arvore, fn):
                pais = _pais_de(rel)
                chamadas.append((rel, no.lineno, _marcada(no, pais)))
        assert chamadas, f"{arq}:{fn} não é chamada por ninguém"
        sem = [f"{r}:{n}" for r, n, ok in chamadas if not ok]
        assert sem == [], f"chamada de {fn} sem marca: {sem}"


def test_ninguem_fura_o_registro_pelo_send_simple():
    fora = [
        f"{rel}:{no.lineno}"
        for rel, arvore in _arvores().items()
        for no in _chamadas(arvore, "send_simple")
    ]
    assert fora == []


def test_quem_cria_um_threema_client_envia_por_send_to_all():
    for rel, arvore in _arvores().items():
        if any(True for _ in _chamadas(arvore, "ThreemaClient")):
            assert any(True for _ in _chamadas(arvore, "send_to_all")), rel


def _chaves_das_marcas() -> tuple[set[str], list[str]]:
    usadas: set[str] = set()
    erradas: list[str] = []
    nomes = constantes()
    for rel, arvore in _arvores().items():
        for no in ast.walk(arvore):
            if not _e_marca(no):
                continue
            arg = no.args[0] if no.args else None
            if (
                isinstance(arg, ast.Attribute)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "K"
            ):
                if arg.attr in nomes:
                    usadas.add(nomes[arg.attr])
                else:
                    erradas.append(f"{rel}:{no.lineno} K.{arg.attr}")
            elif (rel, ast.unparse(arg) if arg is not None else "") not in _CHAVES_DINAMICAS:
                erradas.append(f"{rel}:{no.lineno} {ast.unparse(arg) if arg else '(vazio)'}")
    return usadas, erradas


def test_chave_de_toda_marca_existe_no_catalogo():
    _usadas, erradas = _chaves_das_marcas()
    assert erradas == []


def test_chaves_dinamicas_cobrem_o_que_existe():
    from app.routers import informar as informar_router
    from app.services import ouvidoria
    from app.services.conferencia_shopee import plataformas, threema_aviso
    from app.services.threema_catalogo import chave_ouvidoria

    assert set(informar_router._CHAVE_ENVIO) == set(informar_router._CONTEXTOS_ENVIO)
    assert set(informar_router._CHAVE_ENVIO.values()) <= set(CATALOGO)
    assert set(threema_aviso.CHAVE_ENVIO) == set(plataformas.PLATAFORMAS)
    assert set(threema_aviso.CHAVE_ENVIO.values()) <= set(CATALOGO)
    for robo in ouvidoria.ROBOS:
        if robo == "denuncia_relatorio_threema":  # envio próprio (item 43)
            assert chave_ouvidoria(robo) is None
        else:
            assert chave_ouvidoria(robo) in CATALOGO
    # o núcleo (threema_envios.enviar) recusa chave fora do catálogo antes de marcar
    from app.services import threema_envios

    with pytest.raises(KeyError, match="envio desconhecido"):
        threema_envios._envio("logistica.nao_existe")


def test_todo_envio_gerenciado_do_servidor_tem_marca():
    from app.routers import informar as informar_router
    from app.services import ouvidoria
    from app.services.conferencia_shopee import threema_aviso
    from app.services.threema_catalogo import chave_ouvidoria

    usadas, _ = _chaves_das_marcas()
    usadas |= set(informar_router._CHAVE_ENVIO.values())
    usadas |= set(threema_aviso.CHAVE_ENVIO.values())
    usadas |= {c for r in ouvidoria.ROBOS if (c := chave_ouvidoria(r))}
    gerenciados = {d.chave for d in CATALOGO.values() if d.onde_roda == "servidor" and d.gerenciado}
    assert len(gerenciados) == 47
    assert gerenciados - usadas == set()
    # e nenhuma marca usa envio do Mac, externo ou informativo
    assert usadas - gerenciados == set()


# Cada marca do servidor, (arquivo, função, chave) → quantas: a FOTO conferida
# uma a uma contra a tabela do desenho (seção 2). Os testes acima garantem que
# toda chave existe e todo envio é usado, mas não que cada ponto usa a chave
# CERTA — trocar as duas marcas da Margem (25 × 26), ou as do item 1 e do 43,
# passaria calado e a linha de base por chave sairia trocada. Mudou um ponto
# de envio de propósito? Atualize aqui junto.
_MARCAS = {
    # item 1: motor (5 min), botão da regra, botão da linha do pedido
    ("services/logistica_bling.py", "enviar_threema_em_lote", "K.LOGISTICA_MENSAGEM_REGRA"): 1,
    ("routers/logistica.py", "enviar_threema", "K.LOGISTICA_MENSAGEM_REGRA"): 1,
    ("routers/logistica.py", "enviar_threema_pedido", "K.LOGISTICA_MENSAGEM_REGRA"): 1,
    # itens 2, 3, 11, 17 e 28 (o mapa contexto → chave tem teste próprio)
    ("routers/informar.py", "enviar", "_CHAVE_ENVIO[contexto]"): 1,
    ("services/logistica_amazon_avisos.py", "run", "K.LOGISTICA_AMAZON_PRAZOS"): 1,  # 4
    # 5: cron, botão ⟳ e webhook do 17track (pelo avisar_graves)
    ("services/logistica_track_sync.py", "_aplicar_localizacoes", "K.LOGISTICA_CORREIOS_GRAVE"): 1,
    ("services/logistica_track_sync.py", "atualizar_linha", "K.LOGISTICA_CORREIOS_GRAVE"): 1,
    ("routers/logistica_track.py", "receive_17track_push", "K.LOGISTICA_CORREIOS_GRAVE"): 1,
    ("worker.py", "logistica_vigia", "K.LOGISTICA_VIGIA_AUTOMACAO"): 1,  # 6
    ("services/marketplace_shipment_check.py", "_avisar_threema", "K.LOGISTICA_LOJA_MUDA"): 1,
    # 8: o mesmo avisar_graves, chamado pelo rastreio das devoluções
    ("services/devolucao_rastreio_sync.py", "run", "K.DEVOLUCOES_PACOTE_VOLTA_GRAVE"): 1,
    ("services/devolucao_prazo.py", "avisar_prazos", "K.DEVOLUCOES_PRAZO_CONTESTACAO"): 1,  # 9
    ("services/devolucao_acao_avisos.py", "run", "K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA"): 1,  # 10
    ("services/tuta_devolucoes.py", "entregar_resultado", "K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR"): 1,
    ("services/chamados_tiktok_reembolso.py", "_avisar", "K.CHAMADOS_TIKTOK_SO_REEMBOLSO"): 1,
    ("services/chamados_pendencias.py", "varrer", "K.CHAMADOS_ABERTURAS_PRESAS"): 1,  # 14
    ("services/chamados_ia_aviso.py", "avisar", "K.CHAMADOS_IA_PARADA"): 1,  # 15
    ("services/chamados_juridico.py", "encaminhar", "K.CHAMADOS_ENCAMINHAR_JURIDICO"): 1,  # 16
    # 18 a 22 (NF automática)
    (
        "services/nf_auto_enfileirar.py",
        "_notificar_sem_estoque",
        "K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO",
    ): 1,
    (
        "services/nf_auto_enfileirar.py",
        "_avisar_negativo_fora_do_lote",
        "K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE",
    ): 1,
    (
        "services/nf_auto_enfileirar.py",
        "avisar_kit_sem_composicao",
        "K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO",
    ): 1,
    (
        "services/nf_auto_enfileirar.py",
        "_avisar_sem_leitura",
        "K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING",
    ): 1,
    (
        "services/nf_auto_enfileirar.py",
        "_avisar_recusa_83955",
        "K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO",
    ): 1,
    (
        "services/prioridade_estoque_movimentos.py",
        "avisar_falhas",
        "K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA",
    ): 1,  # 23
    (
        "services/vigia_estoque_familia.py",
        "vigia_estoque_familia_sweep",
        "K.PRODUTOS_VIGIA_SOMA_FAMILIA",
    ): 1,  # 24
    # 25, 26 e 27 (Margem)
    ("services/margem_auto_hold.py", "_avisar_threema", "K.MARGEM_PEDIDO_SEGURADO"): 1,
    (
        "services/margem_auto_hold.py",
        "_avisar_threema_reavaliacao",
        "K.MARGEM_REAVALIACAO",
    ): 1,
    ("services/margem_auto_hold.py", "_alertar_margem_alta", "K.MARGEM_MARGEM_ALTA"): 1,
    # 29 a 42 (um envio por robô; o mapa robô → chave tem teste próprio)
    ("services/ouvidoria.py", "avisar_pendentes", "chave_ouvidoria(robo_chave)"): 1,
    # 43: o relatório do dia e o teste dele
    ("services/denuncia_relatorio_threema.py", "enviar_pendente", "K.DENUNCIA_RELATORIO_DIARIO"): 1,
    ("services/denuncia_relatorio_threema.py", "enviar_teste", "K.DENUNCIA_RELATORIO_DIARIO"): 1,
    ("services/denuncia_robo_aviso.py", "rodar", "K.DENUNCIA_ROBO_PRECISA_ALGUEM"): 1,  # 44
    # 45 a 47 (o mapa plataforma → chave tem teste próprio)
    ("services/conferencia_shopee/threema_aviso.py", "enviar_pendente", "chave"): 1,
    # o núcleo do Cadastro (C5): forcar_para e lista resolvida
    ("services/threema_envios.py", "enviar", "chave"): 2,
}


def test_cada_marca_usa_a_chave_certa():
    achadas: dict[tuple[str, str | None, str], int] = {}
    for rel, arvore in _arvores().items():
        pais = _pais_de(rel)
        for no in ast.walk(arvore):
            if _e_marca(no):
                arg = ast.unparse(no.args[0]) if no.args else ""
                k = (rel, _funcao(no, pais), arg)
                achadas[k] = achadas.get(k, 0) + 1
    faltam = {k: v for k, v in _MARCAS.items() if achadas.get(k) != v}
    sobram = {k: v for k, v in achadas.items() if _MARCAS.get(k) != v}
    assert (faltam, sobram) == ({}, {})


# ─────────────────────────────────────────────────────────── migration 0504

_MIGRATION = _API / "alembic" / "versions" / "0512_threema_envio_registro.py"
_TABELA = "threema_envio_registro"


def _carregar():
    spec = importlib.util.spec_from_file_location("migration_0504", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _catalogo(db, schema: str) -> dict:
    def limpo(s: str | None) -> str | None:
        return s.replace(f"{schema}.", "") if s else s

    colunas = (
        await db.execute(
            text(
                "SELECT column_name, udt_name, character_maximum_length, is_nullable, "
                "column_default, is_identity, identity_generation "
                "FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t ORDER BY column_name"
            ),
            {"s": schema, "t": _TABELA},
        )
    ).all()
    constraints = (
        await db.execute(
            text(
                "SELECT co.conname, co.contype::text, pg_get_constraintdef(co.oid) "
                "FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid "
                "JOIN pg_namespace n ON n.oid = cl.relnamespace "
                "WHERE n.nspname = :s AND cl.relname = :t ORDER BY co.conname"
            ),
            {"s": schema, "t": _TABELA},
        )
    ).all()
    indices = (
        await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = :s AND tablename = :t ORDER BY indexname"
            ),
            {"s": schema, "t": _TABELA},
        )
    ).all()
    return {
        "colunas": [tuple(r[:4]) + (limpo(r[4]),) + tuple(r[5:]) for r in colunas],
        "constraints": [(r[0], r[1], limpo(r[2])) for r in constraints],
        "indices": [(r[0], limpo(r[1])) for r in indices],
    }


@pytest.mark.asyncio
async def test_0505_cria_igual_ao_model_desce_e_sobe(db):
    from app.models import threema_envio as modelo_registro

    mod = _carregar()
    assert mod.revision == "0512_threema_envio_registro"
    assert mod.down_revision == "0511_contas_ml_par_custo_total"
    assert mod.ORIGENS == modelo_registro.ORIGENS_REGISTRO == threema_registro.ORIGENS
    assert mod.VIAS == modelo_registro.VIAS_REGISTRO == threema_registro.VIAS
    assert mod.ONDE_RODA == modelo_registro.ONDE_RODA_REGISTRO

    modelo = Base.metadata.schema
    rascunho = f"{modelo}_m0504"
    await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
    await db.execute(text(f'CREATE SCHEMA "{rascunho}"'))
    await db.execute(text(f'CREATE TABLE "{rascunho}".users (id uuid PRIMARY KEY)'))
    await db.commit()

    def _rodar(conn, passo: str) -> None:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, passo)()

    mod.SCHEMA = rascunho
    try:
        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        da_migration = await _catalogo(db, rascunho)
        do_model = await _catalogo(db, modelo)
        assert da_migration["colunas"], "a migration não criou a tabela"
        assert da_migration == do_model
        assert any(
            nome == "ix_threema_envio_registro_falhas" and "WHERE (NOT ok)" in definicao
            for nome, definicao in da_migration["indices"]
        )

        # o CHECK de origem e de via valem
        base = (
            f'INSERT INTO "{rascunho}".{_TABELA} (lote_id, origem, via, ok) '
            "VALUES (gen_random_uuid(), :o, :v, true)"
        )
        await db.execute(text(base), {"o": "manual", "v": None})
        await db.commit()
        for o, v in (("robo", None), ("manual", "outra")):
            with pytest.raises(Exception):  # noqa: B017,PT011 — CHECK do banco
                await db.execute(text(base), {"o": o, "v": v})
            await db.rollback()

        conn = await db.connection()
        await conn.run_sync(_rodar, "downgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho))["colunas"] == []

        conn = await db.connection()
        await conn.run_sync(_rodar, "upgrade")
        await db.commit()
        assert (await _catalogo(db, rascunho)) == do_model
    finally:
        await db.rollback()
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{rascunho}" CASCADE'))
        await db.commit()
