"""O JUIZ DA EDIÇÃO e a RÉGUA por tipo (13/11/2026): forma não é erro, conteúdo é.

- o código decide igual (enviou igual ou miolo idêntico), só saudação e forma (miolo ≥ 0,92);
- o resto vai ao modelo, com os textos mascarados; a resposta dele vira o julgamento;
- a rodada grava uma linha por avaliação e não repete;
- a régua: das últimas 30 decisões do tipo, a fração de acerto; a loja vale com 20, senão a
  plataforma; 80%, e 90% nos sensíveis.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AtendimentoAvaliacao, AtendimentoRascunho
from app.models.atendimento import AtendimentoJulgamento
from app.services.atendimento import juiz_edicao, regua
from tests.test_atendimento_ia import _conversa, _msg


def test_o_codigo_decide_o_que_da():
    r = "Olá! O F105 não tem NFC. Qualquer dúvida, estamos à disposição."
    assert juiz_edicao.pelo_codigo("enviou_igual", r, r) == juiz_edicao.IGUAL
    assert juiz_edicao.pelo_codigo("editou", r, "bom dia tudo bem?") == juiz_edicao.SO_SAUDACAO
    assert juiz_edicao.pelo_codigo("editou", r, "Bom dia [NOME]! o F105 não tem NFC!") in (
        juiz_edicao.IGUAL,
        juiz_edicao.FORMA,
    )
    assert juiz_edicao.pelo_codigo("editou", r, "Tem sim, o F105 tem NFC.") is None
    assert juiz_edicao.pelo_codigo("editou", r, "") is None


def test_entrada_e_argumentos_sem_parametro_proibido():
    texto = juiz_edicao.entrada("tem nfc?", "Não tem NFC.", "Tem sim.")
    assert texto.startswith("<dados>") and "TEXTO ENVIADO: Tem sim." in texto
    args = juiz_edicao.argumentos(texto, modelo="claude-sonnet-5-5")
    assert "thinking" not in args and "temperature" not in args and "tool_choice" not in args
    assert args["output_config"]["format"]["type"] == "json_schema"
    assert juiz_edicao.motivo_limpo("me liga no 11 98765-4321") == ""


class _ClienteFalso:
    def __init__(self, julgamento: str):
        self.chamadas = 0
        self.julgamento = julgamento
        self.messages = self

    async def create(self, **kwargs):
        self.chamadas += 1
        assert "Maria" not in str(kwargs["messages"])
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=f'{{"julgamento": "{self.julgamento}", "motivo": "mudou o NFC"}}',
                )
            ],
            stop_reason="end_turn",
            model="claude-sonnet-5-5",
            usage=SimpleNamespace(
                input_tokens=300,
                output_tokens=30,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


async def _decisao(db, conversa, *, acao, rascunho, final, categoria="espec_celular"):
    gatilho = await _msg(db, conversa, "Oi, sou a Maria. Esse celular tem NFC?")
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        texto=rascunho,
        categoria=categoria,
        mensagem_gatilho_id=gatilho.id,
        status="editado",
    )
    db.add(r)
    await db.flush()
    a = AtendimentoAvaliacao(rascunho_id=r.id, acao=acao, texto_final=final)
    db.add(a)
    await db.commit()
    return a


async def test_rodada_julga_grava_e_nao_repete(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user, comprador_nome="Maria Souza")
    r = "Olá! O F105 não tem NFC."
    await _decisao(db, conversa, acao="enviou_igual", rascunho=r, final=r)
    await _decisao(db, conversa, acao="editou", rascunho=r, final="bom dia tudo bem?")
    await _decisao(db, conversa, acao="editou", rascunho=r, final="Tem sim, Maria, o F105 tem NFC.")
    cliente = _ClienteFalso("conteudo")

    resumo = await juiz_edicao.rodada(db, cliente=cliente, modelo="claude-sonnet-5-5")
    assert resumo["gravados"] == 3 and cliente.chamadas == 1
    linhas = (await db.scalars(select(AtendimentoJulgamento))).all()
    assert sorted(j.julgamento for j in linhas) == ["conteudo", "igual", "so_saudacao"]
    modelo = next(j for j in linhas if j.julgamento == "conteudo")
    assert modelo.pelo_codigo is False and modelo.motivo == "mudou o NFC" and modelo.custo_usd
    assert all(j.tipo == "espec_celular" for j in linhas)

    de_novo = await juiz_edicao.rodada(db, cliente=cliente, modelo="claude-sonnet-5-5")
    assert de_novo["gravados"] == 0 and cliente.chamadas == 1

    sem_chave = await _decisao(
        db, conversa, acao="editou", rascunho=r, final="O F110 Pro tem NFC, o F105 não."
    )
    resumo = await juiz_edicao.rodada(db, cliente=None, modelo=None)
    assert resumo.get("sem_modelo") == 1 and sem_chave


async def test_regua_por_tipo_loja_e_plataforma(db: AsyncSession, make_user):
    conversa = await _conversa(db, make_user)
    loja = conversa.integration_id
    agora = datetime.now(UTC)

    async def julgamentos(tipo, acertos, erros, *, integ):
        for i in range(acertos + erros):
            a = await _decisao(db, conversa, acao="editou", rascunho="x", final="y", categoria=tipo)
            db.add(
                AtendimentoJulgamento(
                    avaliacao_id=a.id,
                    rascunho_id=a.rascunho_id,
                    plataforma="shopee",
                    integration_id=integ,
                    tipo=tipo,
                    julgamento="forma" if i < acertos else "conteudo",
                    decidido_em=agora - timedelta(minutes=i),
                )
            )
        await db.commit()

    await julgamentos("rastreio", 18, 2, integ=loja)  # 90% em 20 da loja: libera
    await julgamentos("reembolso", 17, 3, integ=loja)  # 85% num sensível: não libera
    await julgamentos("estoque_variacao", 5, 0, integ=loja)  # só 5 da loja: vale a plataforma
    taxas = await regua.taxas_da_loja(db, plataforma="shopee", integration_id=loja)
    assert taxas["rastreio"].liberado and taxas["rastreio"].escopo == "loja"
    assert taxas["reembolso"].fracao == 0.85 and not taxas["reembolso"].liberado
    assert regua.limiar("reembolso") == 0.90 and regua.limiar("rastreio") == 0.80
    assert taxas["estoque_variacao"].decisoes == 5 and not taxas["estoque_variacao"].liberado


async def test_migration_0480_bate_com_o_model_sobe_e_desce(db):
    caminho = (
        Path(__file__).resolve().parents[1] / "alembic/versions/0480_atendimento_julgamentos.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0480", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "0479_atendimento_respostas"
    schema = get_settings().database_schema
    mod.SCHEMA = schema

    async def catalogo():
        colunas = (
            await db.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default"
                    " FROM information_schema.columns WHERE table_schema = :s"
                    " AND table_name = 'atendimento_julgamentos' ORDER BY column_name"
                ),
                {"s": schema},
            )
        ).all()
        indices = (
            await db.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = :s"
                    " AND tablename = 'atendimento_julgamentos' ORDER BY indexname"
                ),
                {"s": schema},
            )
        ).all()
        restricoes = (
            await db.execute(
                text(
                    "SELECT conname, contype FROM pg_constraint"
                    " WHERE conrelid = CAST(:t AS regclass) ORDER BY conname"
                ),
                {"t": f'"{schema}".atendimento_julgamentos'},
            )
        ).all()
        return (
            [tuple(x) for x in colunas],
            [tuple(x) for x in indices],
            [tuple(x) for x in restricoes],
        )

    def _rodar(conn, funcao):
        with Operations.context(MigrationContext.configure(conn)):
            funcao()

    do_model = await catalogo()
    assert len(do_model[0]) == 13
    conn = await db.connection()
    await conn.run_sync(_rodar, mod.downgrade)
    await conn.run_sync(_rodar, mod.upgrade)
    da_migration = await catalogo()
    await db.commit()
    assert da_migration == do_model


def test_listas_liberadas_separam_produto_e_orientacao():
    taxas = {
        "rastreio": regua.Taxa("rastreio", 30, 27, "loja"),  # 90%: passa
        "espec_celular": regua.Taxa("espec_celular", 30, 25, "loja"),  # 83%: passa (produto)
        "reembolso": regua.Taxa("reembolso", 30, 26, "loja"),  # 87% num sensível: não passa
        "garantia": regua.Taxa("garantia", 30, 28, "loja"),  # 93%: passa (orientação)
        "agradecimento": regua.Taxa("agradecimento", 10, 10, "loja"),  # só 10 decisões
    }
    listas = regua.listas_liberadas(
        taxas, ["rastreio", "espec_celular", "reembolso", "garantia", "agradecimento"]
    )
    assert listas.auto_categorias == ("rastreio", "espec_celular")
    assert listas.auto_agente == ("rastreio",)
    assert listas.auto_orientacao == ("garantia",)


async def test_aplicar_guarda_o_retrato_e_so_grava_listas_com_a_chave(db: AsyncSession, make_user):
    from app.models import AtendimentoCanal
    from app.models.atendimento import AtendimentoRegua

    conversa = await _conversa(db, make_user, modo="auto", auto_categorias=["agradecimento"])
    canal = await db.get(AtendimentoCanal, conversa.canal_id)
    canal.agente = True
    await db.commit()
    agora = datetime.now(UTC)
    for i in range(22):
        a = await _decisao(
            db, conversa, acao="editou", rascunho="x", final="y", categoria="rastreio"
        )
        db.add(
            AtendimentoJulgamento(
                avaliacao_id=a.id,
                rascunho_id=a.rascunho_id,
                plataforma="shopee",
                integration_id=conversa.integration_id,
                tipo="rastreio",
                julgamento="forma" if i < 20 else "conteudo",
                decidido_em=agora - timedelta(minutes=i),
            )
        )
    await db.commit()

    resumo = await regua.aplicar(db, gravar_listas=False)
    assert resumo["canais"] == 1 and resumo["mudaram"] == 0
    [linha] = (await db.scalars(select(AtendimentoRegua))).all()
    assert (linha.tipo, linha.decisoes, linha.acertos, linha.liberado) == ("rastreio", 22, 20, True)
    assert linha.liberado_hoje is False
    await db.refresh(canal)
    assert canal.auto_categorias == ["agradecimento"]  # sem a chave, nada muda

    resumo = await regua.aplicar(db, gravar_listas=True)
    await db.refresh(canal)
    # O piso (13/11/2026, o Eduardo: nada piora na virada): o que já saía sozinho
    # (agradecimento) continua; a régua SOMA o rastreio.
    assert resumo["mudaram"] == 1 and canal.auto_categorias == ["agradecimento", "rastreio"]
    assert canal.auto_agente == ["rastreio"] and canal.auto_orientacao == []

    # Com prova de que erra (20+ decisões abaixo da régua), o assunto sai da lista.
    for i in range(20):
        a = await _decisao(
            db, conversa, acao="editou", rascunho="x", final="y", categoria="agradecimento"
        )
        db.add(
            AtendimentoJulgamento(
                avaliacao_id=a.id,
                rascunho_id=a.rascunho_id,
                plataforma="shopee",
                integration_id=conversa.integration_id,
                tipo="agradecimento",
                julgamento="conteudo",
                decidido_em=agora - timedelta(minutes=i),
            )
        )
    await db.commit()
    await regua.aplicar(db, gravar_listas=True)
    await db.refresh(canal)
    assert canal.auto_categorias == ["rastreio"]


async def test_a_regua_mede_so_desde_a_data_configurada(db: AsyncSession, make_user, monkeypatch):
    """A virada (13/11/2026): só o agente novo conta — o antigo não segura o novo."""
    conversa = await _conversa(db, make_user)
    agora = datetime.now(UTC)
    for i, quando in enumerate([agora - timedelta(days=2)] * 3 + [agora - timedelta(hours=1)] * 2):
        a = await _decisao(
            db, conversa, acao="editou", rascunho="x", final="y", categoria="rastreio"
        )
        db.add(
            AtendimentoJulgamento(
                avaliacao_id=a.id,
                rascunho_id=a.rascunho_id,
                plataforma="shopee",
                integration_id=conversa.integration_id,
                tipo="rastreio",
                julgamento="conteudo" if i < 3 else "igual",
                decidido_em=quando,
            )
        )
    await db.commit()
    taxas = await regua.taxas_da_loja(
        db, plataforma="shopee", integration_id=conversa.integration_id
    )
    assert (taxas["rastreio"].decisoes, taxas["rastreio"].acertos) == (5, 2)
    monkeypatch.setattr(
        get_settings(), "atendimento_regua_desde", (agora - timedelta(days=1)).isoformat()
    )
    taxas = await regua.taxas_da_loja(
        db, plataforma="shopee", integration_id=conversa.integration_id
    )
    assert (taxas["rastreio"].decisoes, taxas["rastreio"].acertos) == (2, 2)
    monkeypatch.setattr(get_settings(), "atendimento_regua_desde", "lixo")
    assert regua.desde_da_regua() is None


def test_o_piso_soma_e_so_tira_com_prova():
    atuais = regua.Listas(("agradecimento",), (), ("garantia",))
    da_regua = regua.Listas(("rastreio",), ("rastreio",), ())
    assert regua.com_o_piso(atuais, da_regua, set()) == regua.Listas(
        ("agradecimento", "rastreio"), ("rastreio",), ("garantia",)
    )
    assert regua.com_o_piso(atuais, da_regua, {"garantia", "rastreio"}) == regua.Listas(
        ("agradecimento",), (), ()
    )
    taxas = {
        "a": regua.Taxa("a", 25, 10, "loja"),  # medido e abaixo: reprovado
        "b": regua.Taxa("b", 5, 0, "loja"),  # sem medida: fica
        "c": regua.Taxa("c", 25, 24, "loja"),  # medido e acima: liberado
    }
    assert regua.reprovados(taxas) == {"a"}


def test_a_revisao_do_que_saiu_sozinho_e_julgada_pelo_codigo():
    assert juiz_edicao.pela_revisao("ok") == juiz_edicao.IGUAL
    assert juiz_edicao.pela_revisao("erro") == juiz_edicao.CONTEUDO
    assert juiz_edicao.pela_revisao(None) is None


async def test_a_revisao_conta_na_regua_e_a_nota_trocada_troca_o_julgamento(
    db: AsyncSession, make_user
):
    """O envio da IA não tem decisão de pessoa: o 👍/👎 da revisão diária é a decisão
    (`AVALIACAO_AUTOMATICO`). O código julga — o modelo nunca é chamado — e a nota
    trocada depois troca o julgamento."""
    from app.services.atendimento import constantes

    conversa = await _conversa(db, make_user, comprador_nome="Maria Souza")
    r = "O F105 tem NFC."
    a = await _decisao(db, conversa, acao=constantes.AVALIACAO_AUTOMATICO, rascunho=r, final=None)
    # Sem nota ainda: não é decisão (nada a julgar).
    cliente = _ClienteFalso("conteudo")
    resumo = await juiz_edicao.rodada(db, cliente=cliente, modelo="claude-sonnet-5-5")
    assert resumo["gravados"] == 0 and cliente.chamadas == 0

    a.nota = constantes.NOTA_OK
    await db.commit()
    resumo = await juiz_edicao.rodada(db, cliente=cliente, modelo="claude-sonnet-5-5")
    assert resumo["gravados"] == 1 and cliente.chamadas == 0
    j = await db.scalar(select(AtendimentoJulgamento))
    assert (j.julgamento, j.pelo_codigo, j.modelo) == ("igual", True, None)
    assert j.motivo == juiz_edicao.MOTIVO_REVISAO_OK
    # Julgada e igual à nota: não volta.
    assert (await juiz_edicao.rodada(db, cliente=cliente, modelo="x"))["gravados"] == 0

    a.nota = constantes.NOTA_ERRO
    a.correcao = "Tem NFC sim."
    await db.commit()
    resumo = await juiz_edicao.rodada(db, cliente=cliente, modelo="claude-sonnet-5-5")
    assert resumo["gravados"] == 1 and cliente.chamadas == 0
    db.expire_all()
    j = await db.scalar(select(AtendimentoJulgamento))
    assert (j.julgamento, j.motivo) == ("conteudo", juiz_edicao.MOTIVO_REVISAO_ERRO)
