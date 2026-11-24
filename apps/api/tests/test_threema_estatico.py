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
* toda chave usada — no servidor e no front — existe no catálogo, e todo
  envio gerenciado do servidor é usado e lê o Cadastro;
* o que a limpeza tirou não volta: a lista de destinatários do `.env` da
  Logística e o cadastro de contato avulso da Ouvidoria (contato externo é só
  do Cadastro);
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


@functools.cache
def _pais(rel: str) -> dict[ast.AST, ast.AST]:
    return {f: p for p in ast.walk(_fontes()[rel]) for f in ast.iter_child_nodes(p)}


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


def _nomes_de_settings(escopo: ast.AST, *, so_o_topo: bool = False) -> set[str]:
    """Os nomes que recebem `get_settings()` neste escopo (`s = get_settings()`)."""
    nos = escopo.body if so_o_topo else ast.walk(escopo)  # type: ignore[attr-defined]
    out: set[str] = set()
    for no in nos:
        if isinstance(no, ast.Assign) and _e_get_settings(no.value):
            out |= {t.id for t in no.targets if isinstance(t, ast.Name)}
        elif (
            isinstance(no, ast.AnnAssign)
            and no.value is not None
            and _e_get_settings(no.value)
            and isinstance(no.target, ast.Name)
        ):
            out.add(no.target.id)
    return out


def _e_settings(expr: ast.AST, rel: str, no: ast.AST) -> bool:
    """`get_settings()` direto, ou um nome que o recebeu numa função em volta
    (ou no topo do módulo)."""
    if _e_get_settings(expr):
        return True
    if not isinstance(expr, ast.Name):
        return False
    nomes = _nomes_de_settings(_fontes()[rel], so_o_topo=True)
    atual = _pais(rel).get(no)
    while atual is not None:
        if isinstance(atual, ast.FunctionDef | ast.AsyncFunctionDef):
            nomes |= _nomes_de_settings(atual)
        atual = _pais(rel).get(atual)
    return expr.id in nomes


def _leituras_do_env() -> dict[tuple[str, str | None], set[str]]:
    """(arquivo, função) → campos lidos do Settings por nome: `s.campo`,
    `get_settings().campo` e `getattr(s, "campo")`."""
    campos = _campos_de_lista()
    achadas: dict[tuple[str, str | None], set[str]] = {}
    for rel, arvore in _fontes().items():
        if rel == "config.py":
            continue
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
            if campo and _e_settings(alvo, rel, no):
                achadas.setdefault((rel, _funcao(no, _pais(rel))), set()).add(campo)
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


def test_nome_da_lista_do_env_so_no_catalogo():
    """O nome de uma lista do .env escrito como texto (para um `getattr`) só no
    catálogo — e, na Ouvidoria, no `env_threema_recipients` do robô (a lista
    antiga dele). `threema_recipients` sozinho também é a coluna das regras e
    dos robôs, e o nome do evento de log da Conferência não é leitura."""
    campos = _campos_de_lista() - {"threema_recipients"}
    permitidos = {"config.py", "services/threema_catalogo.py"}
    achados = []
    for rel, arvore in _fontes().items():
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
    assert achados == []


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
    # a lista do .env da Logística (o seletor do 👤, que virou etiquetas de leitura)
    assert ("GET", "/api/logistica/threema/destinatarios") not in caminhos
    # cadastro de contato avulso pela Ouvidoria: contato externo é só do
    # Cadastro (as rotas antigas seguem respondendo 410 para a aba velha)
    for nome in ("salvar_contato", "remover_contato", "ContatoInvalidoError"):
        assert not hasattr(threema, nome), nome
    assert not hasattr(schemas_ouvidoria, "ContatoThreemaIn")
    # o seletor da Ouvidoria (leitura) e o espelho da linha antiga continuam
    assert ("GET", "/api/ouvidoria/threema/destinatarios") in caminhos
    assert callable(threema.espelhar_contatos)
    if (_WEB / "pages").exists():
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
