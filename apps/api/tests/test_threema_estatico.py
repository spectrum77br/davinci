"""Travas estáticas do Cadastro dos envios Threema (tarefa 16, C12 — limpeza).

Depois da F4, todo envio gerenciado do servidor lê Cadastros › Envios Threema
(quem recebe é escolhido só lá, só por admin). Estas travas impedem que um
ponto de envio volte a montar a lista sozinho ou que uma tela volte a
escolher quem recebe por fora:

* `send_to_all(` fora do `services/threema.py` só no núcleo
  (`threema_envios.enviar`) ou dentro de uma marca `threema_registro.envio(…)`
  — e a única marca fora do núcleo é o ramo do Informar de contexto não
  migrado (o código de antes de cada fatia; nenhum contexto passa mais por
  ele);
* as listas do `.env` (`*_threema_recipients`) e os interruptores da
  Conferência (`conferencia_*_threema`) têm o nome só no catálogo
  (`threema_catalogo`, o `Legado` de cada envio) e só são lidas pela
  configuração antiga: o legado do núcleo (`threema_envios`, por `getattr` com
  o nome do catálogo), a lista antiga do robô da Ouvidoria e o próprio
  cliente do Threema;
* toda chave usada — no servidor e no front — existe no catálogo (todo
  `K.NOME` do servidor e toda chave escrita como texto nas leituras do núcleo:
  `resolver`, `ativo`, `quem_recebe`, `legado`, `escopo_das_regras`), e todo
  envio gerenciado do servidor é usado e lê o Cadastro;
* o que a limpeza tirou não volta: a lista de destinatários do `.env` da
  Logística (a rota de antes do seletor do 👤 fica até a limpeza das fontes
  antigas, só com o item do Cadastro, sem ID, para a aba aberta antes da F4)
  e o cadastro de contato avulso da Ouvidoria (contato externo é só do
  Cadastro);
* os espelhos do front da liberação do gerente (`_EMAILS_EXTRAS`) batem com o
  back, que só libera ler e enviar.

As travas de marca por ponto (a foto arquivo × função × chave) seguem em
`test_threema_registro`.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

import pytest

from app.services.threema_catalogo import CATALOGO
from tests.test_threema_registro import (
    _arvores as _arvores_com_marca,
)
from tests.test_threema_registro import (
    _chamadas,
    _chaves_das_marcas,
    _funcao,
    _marcada,
    _pais_de,
)

_API = Path(__file__).resolve().parent.parent
_APP = _API / "app"
_WEB = _API.parent / "web"


@functools.cache
def _fontes() -> dict[str, ast.Module]:
    """Todo o código do servidor, INCLUSIVE o cliente do Threema e o catálogo."""
    return {
        p.relative_to(_APP).as_posix(): ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for p in sorted(_APP.rglob("*.py"))
    }


def _mapa_de_pais(arvore: ast.AST) -> dict[ast.AST, ast.AST]:
    return {f: p for p in ast.walk(arvore) for f in ast.iter_child_nodes(p)}


@functools.cache
def _pais(rel: str) -> dict[ast.AST, ast.AST]:
    return _mapa_de_pais(_fontes()[rel])


def _modulo(fonte: str) -> ast.Module:
    """Um módulo de mentira para os casos negativos das travas."""
    import textwrap

    return ast.parse(textwrap.dedent(fonte))


# ─────────────────────────────────────────── send_to_all só no núcleo

# O núcleo do Cadastro: resolve a lista (nunca vazia) e manda com a marca.
_NUCLEO = ("services/threema_envios.py", "enviar")
# A única marca solta que sobrou: o "Enviar agora" do Informar de contexto
# ainda não migrado (o código de antes de cada fatia). Desde a F3/C10b os 5
# contextos mandam por `_enviar_pelo_cadastro` — conferido abaixo.
_MARCA_DE_ANTES = ("routers/informar.py", "enviar")


def test_send_to_all_so_no_nucleo_ou_numa_marca():
    onde: dict[tuple[str, str | None], list[bool]] = {}
    for rel, arvore in _arvores_com_marca().items():
        for no in _chamadas(arvore, "send_to_all"):
            pais = _pais_de(rel)
            onde.setdefault((rel, _funcao(no, pais)), []).append(_marcada(no, pais))
    assert set(onde) == {_NUCLEO, _MARCA_DE_ANTES}, (
        "send_to_all fora do núcleo: mande por threema_envios.enviar(K.X, texto)"
    )
    assert all(all(marcas) for marcas in onde.values()), onde
    assert len(onde[_NUCLEO]) == 2  # a lista de uma vez e o um_por_vez da Conferência

    from app.routers import informar

    assert all(CATALOGO[c].migrado for c in informar._CHAVE_ENVIO.values())


def test_o_nucleo_nunca_manda_para_a_lista_vazia():
    """`send_to_all([])` cai no THREEMA_RECIPIENTS (services/threema.py): o
    núcleo só chama depois do `if not res.ids: return`."""
    arvore = _fontes()[_NUCLEO[0]]
    (enviar,) = [
        n for n in ast.walk(arvore) if isinstance(n, ast.AsyncFunctionDef) and n.name == "enviar"
    ]
    retornos_sem_ids = [
        n
        for n in ast.walk(enviar)
        if isinstance(n, ast.If) and ast.unparse(n.test) == "not res.ids"
        and any(isinstance(x, ast.Return) for x in n.body)
    ]
    assert len(retornos_sem_ids) == 1
    portao = retornos_sem_ids[0].lineno
    chamadas = list(_chamadas(enviar, "send_to_all"))
    assert chamadas and all(c.lineno > portao for c in chamadas)
    assert [ast.unparse(c.args[1]) for c in chamadas] == ["list(res.ids)", "[rid]"]


# ─────────────────────────────────── as listas do .env só no legado


def _campos_de_lista() -> set[str]:
    from app.config import Settings

    return {
        n
        for n in Settings.model_fields
        if n.endswith("threema_recipients") or re.fullmatch(r"conferencia_[a-z]+_threema", n)
    }


def _e_get_settings(no: ast.AST) -> bool:
    if not isinstance(no, ast.Call):
        return False
    f = no.func
    return (isinstance(f, ast.Name) and f.id == "get_settings") or (
        isinstance(f, ast.Attribute) and f.attr == "get_settings"
    )


def _vem_de_get_settings(expr: ast.AST | None) -> bool:
    """`get_settings()`, ou um `x or get_settings()` / `a if c else
    get_settings()` (o `cfg = cfg or get_settings()` de quem recebe o
    settings como parâmetro opcional)."""
    if expr is None:
        return False
    if _e_get_settings(expr):
        return True
    if isinstance(expr, ast.BoolOp):
        return any(_vem_de_get_settings(v) for v in expr.values)
    if isinstance(expr, ast.IfExp):
        return _vem_de_get_settings(expr.body) or _vem_de_get_settings(expr.orelse)
    return False


def _anotacao_settings(anotacao: ast.AST | None) -> bool:
    """`Settings`, `Settings | None`, `Optional[Settings]`, `config.Settings`
    ou o texto `"Settings"`."""
    if anotacao is None:
        return False
    for n in ast.walk(anotacao):
        if (isinstance(n, ast.Name) and n.id == "Settings") or (
            isinstance(n, ast.Attribute) and n.attr == "Settings"
        ):
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if re.search(r"\bSettings\b", n.value):
                return True
    return False


def _nomes_de_settings(escopo: ast.AST, *, so_o_topo: bool = False) -> set[str]:
    """Os nomes que guardam o settings neste escopo: `s = get_settings()`,
    `cfg = cfg or get_settings()` e o parâmetro anotado `Settings`."""
    nos = escopo.body if so_o_topo else ast.walk(escopo)  # type: ignore[attr-defined]
    out: set[str] = set()
    for no in nos:
        if isinstance(no, ast.Assign) and _vem_de_get_settings(no.value):
            out |= {t.id for t in no.targets if isinstance(t, ast.Name)}
        elif (
            isinstance(no, ast.AnnAssign)
            and isinstance(no.target, ast.Name)
            and (_vem_de_get_settings(no.value) or _anotacao_settings(no.annotation))
        ):
            out.add(no.target.id)
        elif isinstance(no, ast.NamedExpr) and _vem_de_get_settings(no.value):
            out.add(no.target.id)
        elif no is escopo and isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef):
            # os parâmetros da própria função (as de dentro entram quando o
            # `_e_settings` sobe por elas)
            a = no.args
            todos = [*a.posonlyargs, *a.args, *a.kwonlyargs]
            out |= {x.arg for x in todos if _anotacao_settings(x.annotation)}
    return out


def _e_settings(expr: ast.AST, arvore: ast.Module, pais: dict, no: ast.AST) -> bool:
    """`get_settings()` direto, ou um nome que guarda o settings numa função
    em volta (ou no topo do módulo)."""
    if _vem_de_get_settings(expr):
        return True
    if not isinstance(expr, ast.Name):
        return False
    nomes = _nomes_de_settings(arvore, so_o_topo=True)
    atual = pais.get(no)
    while atual is not None:
        if isinstance(atual, ast.FunctionDef | ast.AsyncFunctionDef):
            nomes |= _nomes_de_settings(atual)
        atual = pais.get(atual)
    return expr.id in nomes


def _leituras_do_env(
    fontes: dict[str, ast.Module] | None = None,
) -> dict[tuple[str, str | None], set[str]]:
    """(arquivo, função) → campos lidos do Settings por nome: `s.campo`,
    `get_settings().campo` e `getattr(s, "campo")`.

    Os campos que só o Settings tem (`nf_sem_estoque_threema_recipients`,
    `conferencia_shopee_threema`…) contam em QUALQUER objeto: nenhum modelo
    tem esses nomes, e assim `cfg.campo` conta sem provar que `cfg` é o
    settings. `threema_recipients` sozinho também é a coluna das regras e dos
    robôs: conta só quando o objeto é o settings."""
    real = fontes is None
    fontes = _fontes() if real else fontes
    campos = _campos_de_lista()
    ambiguos = {"threema_recipients"}
    achadas: dict[tuple[str, str | None], set[str]] = {}
    for rel, arvore in fontes.items():
        if rel == "config.py":
            continue
        pais = _pais(rel) if real else _mapa_de_pais(arvore)
        for no in ast.walk(arvore):
            campo = alvo = None
            if isinstance(no, ast.Attribute) and no.attr in campos:
                campo, alvo = no.attr, no.value
            elif (
                isinstance(no, ast.Call)
                and isinstance(no.func, ast.Name)
                and no.func.id == "getattr"
                and len(no.args) >= 2
                and isinstance(no.args[1], ast.Constant)
                and no.args[1].value in campos
            ):
                campo, alvo = no.args[1].value, no.args[0]
            if campo and (campo not in ambiguos or _e_settings(alvo, arvore, pais, no)):
                achadas.setdefault((rel, _funcao(no, pais)), set()).add(campo)
    return achadas


# Quem ainda lê uma lista do .env pelo nome. Tudo é a configuração antiga
# (reserva de emergência: §0.4 do desenho), nunca o caminho de um envio que lê
# o Cadastro. Os outros nomes de lista (`nf_sem_estoque_…`, `tiktok_…`,
# `conferencia_*_threema`…) só existem no catálogo e o núcleo os lê por
# `getattr(s, nome)` em `threema_envios._ids_legado`/`_interruptor_legado`.
_LEITURAS_PERMITIDAS = {
    # o cliente: lista vazia cai no THREEMA_RECIPIENTS — por isso o núcleo
    # nunca chama `send_to_all([])`
    ("services/threema.py", "send_to_all"): {"threema_recipients"},
    # o diretório de quem pode receber (os apelidos do .env completam os nomes)
    ("services/threema.py", "diretorio"): {"threema_recipients"},
    # o legado do núcleo: regra sem 👤 cai no THREEMA_RECIPIENTS
    ("services/threema_envios.py", "_ids_legado"): {"threema_recipients"},
    # a lista antiga do robô da Ouvidoria (Avisar → .env do robô → geral)
    ("services/ouvidoria.py", "destinatarios"): {"ouvidoria_threema_recipients"},
}


def test_lista_do_env_so_e_lida_pela_configuracao_antiga():
    achadas = _leituras_do_env()
    assert achadas == _LEITURAS_PERMITIDAS, (
        "lista do .env lida por fora do Cadastro: o ponto manda por "
        "threema_envios.enviar(K.X, texto) e o nome da lista vai no Legado do catálogo"
    )


def _nomes_de_lista_em_texto(fontes: dict[str, ast.Module] | None = None) -> list[str]:
    campos = _campos_de_lista() - {"threema_recipients"}
    permitidos = {"config.py", "services/threema_catalogo.py"}
    achados = []
    for rel, arvore in (_fontes() if fontes is None else fontes).items():
        if rel in permitidos:
            continue
        eventos_de_log = {
            id(n.args[0])
            for n in ast.walk(arvore)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "logger"
            and n.args
        }
        robo = {
            id(k.value)
            for n in ast.walk(arvore)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "RoboDef"
            for k in n.keywords
            if k.arg == "env_threema_recipients"
        }
        for no in ast.walk(arvore):
            if (
                isinstance(no, ast.Constant)
                and no.value in campos
                and id(no) not in eventos_de_log
                and not (rel == "services/ouvidoria.py" and id(no) in robo)
            ):
                achados.append(f"{rel}:{no.lineno} {no.value}")
    return achados


def test_nome_da_lista_do_env_so_no_catalogo():
    """O nome de uma lista do .env escrito como texto (para um `getattr`) só no
    catálogo — e, na Ouvidoria, no `env_threema_recipients` do robô (a lista
    antiga dele). `threema_recipients` sozinho também é a coluna das regras e
    dos robôs, e o nome do evento de log da Conferência não é leitura."""
    assert _nomes_de_lista_em_texto() == []


# Voltas da lista do .env que a trava pega (casos negativos: módulos de
# mentira, nada do app muda). Cada um: (fonte, o campo lido).
_ENV_POR_FORA = {
    "get_settings direto": (
        """
        from app.config import get_settings
        def ids():
            return get_settings().nf_sem_estoque_threema_recipients
        """,
        "nf_sem_estoque_threema_recipients",
    ),
    "s = get_settings()": (
        """
        from app.config import get_settings
        def ids():
            s = get_settings()
            return s.threema_recipients
        """,
        "threema_recipients",
    ),
    "interruptor da Conferência": (
        """
        from app import config
        def ligado():
            return config.get_settings().conferencia_shopee_threema
        """,
        "conferencia_shopee_threema",
    ),
    "getattr com o nome": (
        """
        from app.config import get_settings
        def ids():
            return getattr(get_settings(), "tiktok_reembolso_threema_recipients", "")
        """,
        "tiktok_reembolso_threema_recipients",
    ),
    # revisão F4: o jeito do `nf_auto_enfileirar.plataformas_do_sweep`
    "cfg = cfg or get_settings()": (
        """
        from app.config import Settings, get_settings
        from app.services import threema
        def _destinatarios_sem_estoque(cfg: Settings | None = None) -> list[str]:
            cfg = cfg or get_settings()
            return threema.parse_recipients(cfg.nf_sem_estoque_threema_recipients)
        """,
        "nf_sem_estoque_threema_recipients",
    ),
    "cfg or get_settings() com a lista geral": (
        """
        from app.config import Settings, get_settings
        def ids(cfg: Settings | None = None):
            cfg = cfg or get_settings()
            return cfg.threema_recipients
        """,
        "threema_recipients",
    ),
    "parâmetro anotado Settings": (
        """
        from app.config import Settings
        def _ids(s: Settings, outro: int):
            return s.threema_recipients
        """,
        "threema_recipients",
    ),
    "parâmetro com nome qualquer": (
        """
        def _ids(conf):
            return conf.ouvidoria_threema_recipients
        """,
        "ouvidoria_threema_recipients",
    ),
}


@pytest.mark.parametrize("caso", sorted(_ENV_POR_FORA))
def test_a_trava_do_env_pega_a_volta_da_lista(caso):
    fonte, campo = _ENV_POR_FORA[caso]
    achadas = _leituras_do_env({"services/novo_ponto.py": _modulo(fonte)})
    assert set().union(*achadas.values()) == {campo}, achadas


def test_a_trava_do_env_nao_confunde_a_coluna_das_regras():
    """`threema_recipients` de uma regra ou de um robô (o 👤 e o "Avisar"
    congelados) não é a lista do .env."""
    fonte = """
        from app.config import Settings, get_settings
        def _robo(robo, cfg: Settings | None = None):
            cfg = cfg or get_settings()
            return robo.threema_recipients, cfg.threema_cadastro_ativo
        """
    assert _leituras_do_env({"services/novo_ponto.py": _modulo(fonte)}) == {}


def test_a_lista_antiga_do_robo_so_e_lida_pelo_legado_e_pela_tela():
    """`ouvidoria.destinatarios` é a configuração antiga dos robôs: o legado do
    núcleo (emergência, semente, conferir) e a tela dos robôs (a lista de
    sempre quando a configuração antiga vale)."""
    quem = set()
    for rel, arvore in _fontes().items():
        for no in _chamadas(arvore, "destinatarios"):
            if isinstance(no.func, ast.Attribute):
                quem.add((rel, _funcao(no, _pais(rel))))
    assert quem == {
        ("services/threema_envios.py", "_ids_legado"),
        ("routers/ouvidoria.py", "_robo_out"),
    }


# ─────────────────────────────────── chaves: existem e são usadas


def test_toda_chave_do_servidor_existe_e_todo_envio_gerenciado_le_o_cadastro():
    usadas, erradas = _chaves_das_marcas()
    assert erradas == []
    # e todo `K.NOME` fora das marcas (resolver, ativo, escopo_das_regras…)
    assert _chaves_fora_do_catalogo()[1] == []
    gerenciados = {d.chave for d in CATALOGO.values() if d.onde_roda == "servidor" and d.gerenciado}
    assert len(gerenciados) == 47
    # F1 a F4 publicadas: nenhum envio do servidor segue a configuração antiga
    nao_migrados = sorted(c for c in gerenciados if not CATALOGO[c].migrado)
    assert nao_migrados == []
    assert {CATALOGO[c].fatia for c in gerenciados} == {"F1", "F2", "F3", "F4"}
    # cada chave fixa das declarações é de um envio gerenciado do servidor (as
    # dinâmicas — Informar, Ouvidoria, Conferência — têm teste próprio em
    # `test_threema_registro`)
    assert usadas <= gerenciados


# As leituras do núcleo que recebem a chave (posição do argumento): a trava
# das marcas (`test_threema_registro._declaracoes`) só vê `enviar` e
# `threema_registro.envio`; `resolver(K.X)` fora do catálogo só quebraria
# quando a linha rodasse (AttributeError no laço do motor, revisão F4).
_LEITURAS_DO_NUCLEO = {
    "enviar": 0,
    "resolver": 0,
    "ativo": 0,
    "quem_recebe": 0,
    "legado": 0,
    "escopo_das_regras": 1,
}


def _e_o_k(expr: ast.AST) -> bool:
    """`K` (importado do catálogo) ou `threema_catalogo.K`."""
    return (isinstance(expr, ast.Name) and expr.id == "K") or (
        isinstance(expr, ast.Attribute)
        and expr.attr == "K"
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "threema_catalogo"
    )


def _chaves_fora_do_catalogo(fontes: dict[str, ast.Module] | None = None) -> tuple[int, list[str]]:
    """(quantos `K.NOME` o servidor usa, os que não existem): TODO acesso
    `K.NOME` (não só os das marcas) e toda chave escrita como texto numa
    leitura do núcleo (`threema_envios.resolver("logistica.x")`)."""
    from app.services.threema_catalogo import constantes

    nomes = constantes()
    usados = 0
    erradas: list[str] = []
    for rel, arvore in (_fontes() if fontes is None else fontes).items():
        for no in ast.walk(arvore):
            if isinstance(no, ast.Attribute) and _e_o_k(no.value):
                if no.attr.isupper():
                    usados += 1
                if no.attr not in nomes:
                    erradas.append(f"{rel}:{no.lineno} K.{no.attr}")
            elif (
                isinstance(no, ast.Call)
                and isinstance(no.func, ast.Attribute)
                and no.func.attr in _LEITURAS_DO_NUCLEO
                and isinstance(no.func.value, ast.Name)
                and no.func.value.id == "threema_envios"
            ):
                pos = _LEITURAS_DO_NUCLEO[no.func.attr]
                kw = {k.arg: k.value for k in no.keywords}
                arg = no.args[pos] if len(no.args) > pos else kw.get("chave")
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value not in CATALOGO
                ):
                    erradas.append(f"{rel}:{no.lineno} {arg.value!r}")
    return usados, erradas


def test_todo_acesso_k_do_servidor_existe_no_catalogo():
    usados, erradas = _chaves_fora_do_catalogo()
    assert erradas == [], "constante K.X que não existe no catálogo (threema_catalogo.K)"
    # a busca enxerga os pontos (marcas, envios e leituras do núcleo)
    assert usados >= 60, usados


# Casos negativos (revisão F4): chave errada fora da marca e do `enviar`.
_CHAVE_ERRADA = {
    "resolver": """
        from app.services import threema_envios
        from app.services.threema_catalogo import K
        async def avisar(texto):
            r = await threema_envios.resolver(K.NAO_EXISTE)
            return r.ids
        """,
    "ativo": """
        from app.services import threema_envios
        from app.services.threema_catalogo import K
        async def ligado():
            return await threema_envios.ativo(K.CONFERENCIA_NAO_EXISTE)
        """,
    "escopo_das_regras": """
        from app.services import threema_envios
        from app.services.threema_catalogo import K
        async def escopos(session):
            return await threema_envios.escopo_das_regras(session, K.NAO_EXISTE)
        """,
    "K pelo módulo": """
        from app.services import threema_catalogo, threema_envios
        async def avisar(texto):
            return await threema_envios.quem_recebe(threema_catalogo.K.NAO_EXISTE)
        """,
    "chave guardada numa constante": """
        from app.services.threema_catalogo import K
        CHAVE = K.LOGISTICA_NAO_EXISTE
        """,
    "texto no resolver": """
        from app.services import threema_envios
        async def avisar(texto):
            return await threema_envios.resolver("logistica.nao_existe")
        """,
    "texto no escopo_das_regras": """
        from app.services import threema_envios
        async def escopos(session):
            return await threema_envios.escopo_das_regras(session, chave="logistica.x")
        """,
}


@pytest.mark.parametrize("caso", sorted(_CHAVE_ERRADA))
def test_a_trava_das_chaves_pega_chave_errada_fora_da_marca(caso):
    _usados, erradas = _chaves_fora_do_catalogo(
        {"services/novo_ponto.py": _modulo(_CHAVE_ERRADA[caso])}
    )
    assert len(erradas) == 1, erradas
    # e a mesma fonte com a chave certa passa
    certa = re.sub(
        r"K\.[A-Z_]*NAO_EXISTE|\"logistica\.(nao_existe|x)\"",
        lambda m: "K.LOGISTICA_MENSAGEM_REGRA" if m.group(0).startswith("K.")
        else '"logistica.mensagem_regra"',
        _CHAVE_ERRADA[caso],
    )
    assert _chaves_fora_do_catalogo({"services/novo_ponto.py": _modulo(certa)})[1] == []


_ATRIBUTO_CHAVE = re.compile(r"(?<![:@\w.-])chave(?:-auto)?=\"([^\"]+)\"")
_PROPRIEDADE_CHAVE = re.compile(r"\bchave:\s*'([^']+)'")
_CONSTANTE_CHAVE = re.compile(r"\bCHAVE_[A-Z_]+(?:\s*:[^=]+)?\s*=\s*'([^']+)'")
_MAPA_CHAVE = re.compile(r"\bCHAVE_[A-Z_]+(?:\s*:[^=]+)?\s*=\s*\{([^}]*)\}")
_FORMATO = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def _chaves_do_front() -> dict[str, list[str]]:
    achadas: dict[str, list[str]] = {}
    for pasta in ("pages", "components", "lib", "composables"):
        for p in sorted((_WEB / pasta).rglob("*")):
            if p.suffix not in (".vue", ".ts") or "node_modules" in p.parts:
                continue
            fonte = p.read_text(encoding="utf-8")
            valores = (
                _ATRIBUTO_CHAVE.findall(fonte)
                + _PROPRIEDADE_CHAVE.findall(fonte)
                + _CONSTANTE_CHAVE.findall(fonte)
            )
            for bloco in _MAPA_CHAVE.findall(fonte):
                valores += re.findall(r"'([^']+)'", bloco)
            for v in valores:
                if _FORMATO.match(v):
                    achadas.setdefault(v, []).append(p.relative_to(_WEB).as_posix())
    return achadas


def test_toda_chave_do_front_existe_no_catalogo():
    if not (_WEB / "components").exists():
        pytest.skip("sem o apps/web ao lado (imagem só da api)")
    achadas = _chaves_do_front()
    # a busca enxerga os atalhos de todos os painéis (Informar, Margem,
    # Conferência, Denúncia, Chamados, a regra da Logística)
    assert len(achadas) >= 15, sorted(achadas)
    assert {c: arqs for c, arqs in achadas.items() if c not in CATALOGO} == {}
    # e só envios que o servidor manda (o Mac e os informativos não têm atalho)
    assert all(
        CATALOGO[c].onde_roda == "servidor" and CATALOGO[c].gerenciado for c in achadas
    ), sorted(achadas)


# ─────────────────────────────────── o que a limpeza tirou não volta


def test_rotas_e_funcoes_que_sairam_na_limpeza():
    from app.routers import logistica as logistica_router
    from app.routers import ouvidoria as ouvidoria_router
    from app.schemas import ouvidoria as schemas_ouvidoria
    from app.services import threema

    caminhos = {
        (metodo, r.path)
        for rotas in (logistica_router.router.routes, ouvidoria_router.router.routes)
        for r in rotas
        for metodo in getattr(r, "methods", ())
    }
    assert ("GET", "/api/logistica/status") in caminhos  # a busca enxerga as rotas
    # A lista do .env da Logística (o seletor do 👤, que virou etiquetas de
    # leitura) não volta: a rota de antes FICA até a limpeza das fontes
    # antigas (30 dias depois da F4), para a aba aberta antes da publicação
    # seguir enviando, mas só com o item do Cadastro, sem ID (a resposta é
    # conferida em test_threema_pontos_logistica_regras) e sem ler o .env (a
    # trava das listas do .env, acima). Sai no commit que a tirar: aí esta
    # linha vira `not in`.
    assert ("GET", "/api/logistica/threema/destinatarios") in caminhos
    # cadastro de contato avulso pela Ouvidoria: contato externo é só do
    # Cadastro (as rotas antigas seguem respondendo 410 para a aba velha)
    for nome in ("salvar_contato", "remover_contato", "ContatoInvalidoError"):
        assert not hasattr(threema, nome), nome
    assert not hasattr(schemas_ouvidoria, "ContatoThreemaIn")
    # o seletor da Ouvidoria (leitura) e o espelho da linha antiga continuam
    assert ("GET", "/api/ouvidoria/threema/destinatarios") in caminhos
    assert callable(threema.espelhar_contatos)
    if (_WEB / "pages").exists():
        # nenhuma tela de hoje usa a rota de antes (só a aba velha)
        telas = [
            p.relative_to(_WEB).as_posix()
            for pasta in ("pages", "components", "lib", "composables")
            for p in (_WEB / pasta).rglob("*")
            if p.suffix in (".vue", ".ts")
            and "/api/logistica/threema/destinatarios" in p.read_text(encoding="utf-8")
        ]
        assert telas == []


# ─────────────────────────────── espelhos do front da liberação do gerente

# A lista de e-mails de cada tela e o contexto do `_EMAILS_EXTRAS` que ela
# espelha (routers/informar.py: libera só ler e enviar).
_ESPELHOS = {
    "pages/logistica.vue": ("INFORMAR_AMAZON_USERS", "logistica_amazon"),
    "components/ChamadosPainel.vue": ("AVISO_IA_USERS", "chamados_ia"),
    "components/DenunciaRobo.vue": ("AVISO_ROBO_USERS", "denuncia_robo"),
    "components/MargemPainel.vue": ("INFORMAR_MARGEM_USERS", "margem"),
}


def test_espelhos_do_front_batem_com_o_back():
    if not (_WEB / "components").exists():
        pytest.skip("sem o apps/web ao lado (imagem só da api)")
    from app.routers import informar

    for rel, (nome, contexto) in _ESPELHOS.items():
        fonte = (_WEB / rel).read_text(encoding="utf-8")
        achou = re.search(rf"const {nome} = \[([^\]]*)\]", fonte)
        assert achou, (rel, nome)
        emails = set(re.findall(r"'([^']+)'", achou.group(1)))
        assert emails == set(informar._EMAILS_EXTRAS[contexto]), rel
        assert "_EMAILS_EXTRAS" in fonte, f"{rel}: o comentário diz o que espelha"
