"""`threema_envios.enviar` (tarefa 16, C5).

* chama o `send_to_all` com os IDs POSICIONAIS (fake com o parâmetro
  `destinos`, sem `**kw`, como os 24 fakes da bateria) e pelo remetente do
  catálogo;
* nunca chama `send_to_all([])`: sem ninguém grava o registro `ok=false`; se
  foi o Cadastro que disse ninguém válido, acende o sino dos admins ativos,
  com dedupe diário;
* desligado não chama nada e não grava registro;
* `forcar_para` ignora o `ativo` (via `forcado`, origem `teste`);
* exceção do envio sobe como hoje; o sino nunca derruba o envio.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from app.config import get_settings
from app.models import Alert, ThreemaEnvioRegistro, UserRole, UserStatus
from app.services import threema, threema_envios
from app.services.threema_catalogo import K
from tests.test_threema_resolver import _usuario, migrar, semeado

_CLIENTE_REAL = threema.ThreemaClient


class _Fake:
    """Igual aos fakes da bateria: `__init__` só com `contexto`, `send_to_all`
    com o 2º parâmetro chamado `destinos` e sem `**kw`."""

    criados: list[str | None] = []
    chamadas: list[list[str]] = []

    def __init__(self, *, contexto=None):
        self.contexto = contexto
        _Fake.criados.append(contexto)

    async def send_to_all(self, text, destinos):
        _Fake.chamadas.append(list(destinos))
        return {"sent": list(destinos), "failed": []}


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    _Fake.criados, _Fake.chamadas = [], []
    threema_envios.limpar_cache()
    s = get_settings()
    monkeypatch.setattr(s, "threema_cadastro_ativo", True)
    monkeypatch.setattr(s, "threema_registro_ativo", True)
    monkeypatch.setattr(threema, "ThreemaClient", _Fake)
    yield
    threema_envios.limpar_cache()


async def _registro(db) -> list[ThreemaEnvioRegistro]:
    await db.commit()
    return list(
        (await db.execute(select(ThreemaEnvioRegistro).order_by(ThreemaEnvioRegistro.id))).scalars()
    )


async def _alertas(db) -> list[Alert]:
    await db.commit()
    return list((await db.execute(select(Alert).order_by(Alert.created_at))).scalars())


@pytest.mark.asyncio
async def test_nao_migrado_manda_o_legado_posicional_pelo_remetente_do_catalogo(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "nf_sem_estoque_threema_recipients", "TSTA0001,TSTB0002")
    out = await threema_envios.enviar(K.LOGISTICA_VIGIA_AUTOMACAO, "automação parada", session=db)
    assert out == {
        "sent": ["TSTA0001", "TSTB0002"],
        "failed": [],
        "via": "legado",
        "motivo": None,
    }
    assert _Fake.chamadas == [["TSTA0001", "TSTB0002"]]
    assert _Fake.criados == ["logistica"]
    # contexto explícito (item 14, remetente dinâmico) vence o do catálogo
    monkeypatch.setattr(get_settings(), "tiktok_reembolso_threema_recipients", "TSTA0001")
    await threema_envios.enviar(K.CHAMADOS_ABERTURAS_PRESAS, "x", contexto="devolucoes", session=db)
    assert _Fake.criados[-1] == "devolucoes"


@pytest.mark.asyncio
async def test_ninguem_valido_nao_chama_grava_registro_e_acende_o_sino_uma_vez_por_dia(
    db, monkeypatch
):
    chave = K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING
    migrar(monkeypatch, chave)
    admin = await _usuario(db, "admin", "TSTA0001", role=UserRole.ADMIN)
    await _usuario(
        db, "admin suspenso", "TSTA0002", role=UserRole.ADMIN, status=UserStatus.SUSPENDED
    )
    await _usuario(db, "comum", "TSTA0003")
    sumido = await _usuario(db, "sumido", "TSTA0004", status=UserStatus.SUSPENDED)
    await semeado(db, chave, sumido)
    # o THREEMA_RECIPIENTS tem gente: lista vazia NUNCA pode cair nele
    monkeypatch.setattr(get_settings(), "threema_recipients", "TSTZ0009")

    out = await threema_envios.enviar(chave, "Bling sem responder\nlinha 2", session=db)
    assert out == {"sent": [], "failed": [], "via": "cadastro", "motivo": "sem_destinatarios"}
    assert _Fake.chamadas == []
    (linha,) = await _registro(db)
    assert (linha.envio_chave, linha.ok, linha.erro, linha.via, linha.destinatario_id) == (
        chave,
        False,
        "sem_destinatario_valido",
        "cadastro",
        None,
    )
    assert linha.contexto == "estoque" and linha.titulo == "Bling sem responder"
    alertas = await _alertas(db)
    assert [a.user_id for a in alertas] == [admin.id]
    a = alertas[0]
    assert a.dedupe_key == f"threema_ninguem:{chave}:{threema_envios._dia_brt()}"
    assert "Bling sem responder o estoque" in a.title
    assert a.payload["chave"] == chave

    # de novo no mesmo dia: mais um registro, nenhum sino a mais
    await threema_envios.enviar(chave, "de novo", session=db)
    assert len(await _registro(db)) == 2
    assert len(await _alertas(db)) == 1


@pytest.mark.asyncio
async def test_legado_vazio_grava_o_registro_mas_nao_acende_o_sino(db, monkeypatch):
    await _usuario(db, "admin", "TSTA0001", role=UserRole.ADMIN)
    monkeypatch.setattr(get_settings(), "nf_sem_estoque_threema_recipients", "")
    out = await threema_envios.enviar(K.LOGISTICA_VIGIA_AUTOMACAO, "x", session=db)
    assert (out["via"], out["motivo"]) == ("legado", "sem_destinatarios")
    assert _Fake.chamadas == []
    (linha,) = await _registro(db)
    assert (linha.ok, linha.via) == (False, "legado")
    assert await _alertas(db) == []


@pytest.mark.asyncio
async def test_desligado_nao_chama_nada_nem_grava(db, monkeypatch):
    chave = K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING
    migrar(monkeypatch, chave)
    a = await _usuario(db, "a", "TSTA0001")
    await semeado(db, chave, a, ativo=False)
    out = await threema_envios.enviar(chave, "x", session=db)
    assert out == {"sent": [], "failed": [], "via": "cadastro", "motivo": "desligado"}
    assert _Fake.chamadas == [] and _Fake.criados == []
    assert await _registro(db) == []


@pytest.mark.asyncio
async def test_forcar_para_ignora_o_ativo_e_registra_como_teste(db, monkeypatch):
    chave = K.DENUNCIA_RELATORIO_DIARIO
    migrar(monkeypatch, chave)
    a = await _usuario(db, "a", "TSTA0001")
    await semeado(db, chave, a, ativo=False)
    segredo = "s3cr3t-forcado"
    cli = _CLIENTE_REAL(gateway_id="*TSTGW01", secret=segredo, contexto="chamados")
    with respx.mock(base_url=threema.THREEMA_API_BASE) as router:
        router.post("/send_simple").mock(return_value=httpx.Response(200, text="abc123"))
        out = await threema_envios.enviar(
            chave, "[TESTE] Relatório", forcar_para=["tstb0002"], origem="teste", client=cli
        )
    assert out == {"sent": ["TSTB0002"], "failed": [], "via": "forcado", "motivo": None}
    (linha,) = await _registro(db)
    assert (linha.envio_chave, linha.via, linha.origem, linha.ok) == (
        chave,
        "forcado",
        "teste",
        True,
    )
    assert segredo not in (linha.erro or "")


@pytest.mark.asyncio
async def test_excecao_do_envio_sobe_como_hoje(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "nf_sem_estoque_threema_recipients", "TSTA0001")

    class _Quebra(_Fake):
        async def send_to_all(self, text, destinos):
            raise threema.ThreemaConfigError("threema_gateway_id_missing")

    monkeypatch.setattr(threema, "ThreemaClient", _Quebra)
    with pytest.raises(threema.ThreemaConfigError):
        await threema_envios.enviar(K.LOGISTICA_VIGIA_AUTOMACAO, "x", session=db)


@pytest.mark.asyncio
async def test_sino_quebrado_nao_derruba_o_envio(db, monkeypatch):
    chave = K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING
    migrar(monkeypatch, chave)
    await _usuario(db, "admin", "TSTA0001", role=UserRole.ADMIN)
    await semeado(db, chave)  # semeado sem ninguém

    async def _quebra(*a, **kw):
        raise RuntimeError("alerts fora")

    monkeypatch.setattr("app.services.alerts.emit_alert", _quebra)
    out = await threema_envios.enviar(chave, "x", session=db)
    assert out["motivo"] == "sem_destinatarios"
    assert await _alertas(db) == []


# Quem usa o núcleo nesta fase, e com quais funções. Nenhum é ponto de envio:
# o startup do worker sincroniza o catálogo, a tela de Cadastros › Envios
# Threema (C6) só LÊ (quem recebe, o legado, do que depende) e a edição (C7)
# só manda o "[TESTE]" do botão (conferido abaixo).
_USOS_DO_NUCLEO = {
    "worker.py": {"sincronizar"},
    # `_regras_com_pessoa` só LÊ as regras com 👤 (o "usa a configuração
    # antiga" dos contatos externos e das regras): não manda nada.
    "services/threema_envios_tela.py": {
        "resolver",
        "legado",
        "dependencias",
        "_dedupe",
        "_regras_com_pessoa",
        # Só LÊ a conversa de verdade de um remetente (monta o cliente, não envia).
        "canal",
    },
    "services/threema_envios_edicao.py": {
        "sincronizar",
        "_interruptor_legado",
        "_dedupe",
        "enviar",
        "limpar_cache",
        # Só LÊ a lista do Cadastro: o "Mandar teste" de envio desligado.
        "_ler_cadastro",
    },
}


def test_nenhum_ponto_de_envio_chama_o_nucleo_nesta_fase():
    """C5: o núcleo existe, mas nenhum ponto de envio o usa ainda (cada fatia
    troca o seu portão no commit em que vira `migrado=True`). Só o startup
    do worker e a tela do Cadastro o chamam, com as funções listadas."""
    import re

    app = Path(__file__).resolve().parent.parent / "app"
    uso = re.compile(
        r"threema_envios\.\w+\(|from app\.services\.threema_envios import"
        r"|from app\.services import [^\n]*\bthreema_envios\b(?!_)"
    )
    usam = sorted(
        p.relative_to(app).as_posix()
        for p in app.rglob("*.py")
        if p.name != "threema_envios.py" and uso.search(p.read_text(encoding="utf-8"))
    )
    assert usam == sorted(_USOS_DO_NUCLEO)
    for rel, permitidas in _USOS_DO_NUCLEO.items():
        chamadas = set(re.findall(r"threema_envios\.(\w+)\(", (app / rel).read_text()))
        assert chamadas <= permitidas, rel
    # O único `enviar` fora das fatias: o botão "Mandar teste" (origem teste,
    # para a lista resolvida com forcar_para).
    import ast

    arvore = ast.parse((app / "services/threema_envios_edicao.py").read_text())
    enviar = [
        n
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "enviar"
    ]
    assert len(enviar) == 1
    kw = {k.arg: ast.unparse(k.value) for k in enviar[0].keywords}
    assert kw["origem"] == "'teste'" and "forcar_para" in kw
