"""Nenhum aviso pode sair sem dizer de qual conversa ele é.

Eduardo (22/09/2026): as conversas do Threema foram separadas por assunto. O
remetente é o que define a conversa — quem chama `ThreemaClient()` sem
`contexto` cai no remetente geral e o aviso aparece na conversa errada.

Foi exatamente o que aconteceu com o trabalho de 11/09: entre ele e hoje
nasceram 9 avisos novos, todos sem contexto, e ninguém percebeu. Este teste
varre o código e reprova a próxima vez.
"""

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

# Único lugar que pode ficar sem contexto: o próprio módulo do Threema, onde a
# classe é definida e onde o fallback geral mora.
ISENTOS = {"services/threema.py"}


def _chamadas_sem_contexto(arquivo: pathlib.Path) -> list[int]:
    """Linhas onde `ThreemaClient(...)` é criado sem `contexto=`."""
    arvore = ast.parse(arquivo.read_text(), filename=str(arquivo))
    achados: list[int] = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        alvo = no.func
        nome = alvo.attr if isinstance(alvo, ast.Attribute) else getattr(alvo, "id", "")
        if nome != "ThreemaClient":
            continue
        if any(k.arg == "contexto" for k in no.keywords):
            continue
        achados.append(no.lineno)
    return achados


def test_todo_emissor_de_threema_declara_o_assunto():
    faltando: list[str] = []
    for arquivo in sorted(APP.rglob("*.py")):
        rel = arquivo.relative_to(APP).as_posix()
        if rel in ISENTOS:
            continue
        for linha in _chamadas_sem_contexto(arquivo):
            faltando.append(f"{rel}:{linha}")
    assert not faltando, (
        "Estes avisos sairiam pelo remetente geral e cairiam na conversa errada "
        "do Threema. Passe `contexto=` (logistica, margem, estoque, devolucoes, "
        "juridico, importacao, flex):\n  " + "\n  ".join(faltando)
    )


def test_a_varredura_realmente_enxerga_o_problema():
    """Guarda do próprio teste: se a varredura parar de achar, o teste acima
    viraria um 'passou' vazio e o buraco voltaria em silêncio."""
    exemplo = pathlib.Path(__file__).parent / "_exemplo_sem_contexto.py"
    exemplo.write_text("from app.services import threema\nthreema.ThreemaClient()\n")
    try:
        assert _chamadas_sem_contexto(exemplo) == [2]
    finally:
        exemplo.unlink()
