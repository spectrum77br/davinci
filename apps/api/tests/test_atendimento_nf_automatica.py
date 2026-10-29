"""A NF pelo agente, sozinha, nas lojas no automático (28/10/2026, `nf_automatica.py`).

O que estes testes seguram, na ordem em que um erro custaria mais caro:

- LGPD: a NF de OUTRO cliente nunca sai — o pedido de outro comprador, o
  destinatário da NF diferente do pedido, a chave que não bate com o número;
- a NF nunca sai DUAS vezes na mesma conversa (a mesma chave/imagem);
- só a nota cheia, autorizada, do pedido da conversa: embalagem, cancelado,
  carrinho, comprador com mais de um pedido → pessoa;
- só no canal em `auto`, só com o pedido claro e o rascunho só com a NF;
- o caminho feliz: a imagem da DANFE e depois a frase, com a marca da chave.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoRascunho,
    BlingOrder,
    NfNota,
)
from app.services.atendimento import enviar, gravar, ia, nf_automatica
from app.services.atendimento.constantes import (
    PROMPT_VERSAO_AGENTE,
    RASCUNHO_ENVIADO,
    RASCUNHO_PENDENTE,
    ResultadoEnvio,
)
from tests.test_atendimento_agente_ligacao import _do_agente, agente_ligado  # noqa: F401
from tests.test_atendimento_danfe import _xml
from tests.test_atendimento_ia import PEDIDO_MKT, _msg, ia_ligada  # noqa: F401

PEDIDO_BLING = "910001"
COMPRADOR = "joaosilva88"  # o `comprador_id` da conversa do `_conversa`
CPF = "12345678909"  # o destinatário do XML de teste


def _chave(numero: int) -> str:
    """cUF AAMM CNPJ mod série nNF tpEmis cNF cDV — o emitente do XML de teste."""
    return f"352610{'00000000000191'}55001{numero:09d}1{numero % 10**8:08d}0"


@pytest.fixture
def ligado(agente_ligado, monkeypatch):  # noqa: F811
    s = agente_ligado
    monkeypatch.setattr(s, "atendimento_auto_ativo", True)
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "atendimento_nf_automatica", True)
    monkeypatch.setattr(s, "atendimento_agente_verificador", True)
    return s


class _Saidas(list):
    """O que saiu (imagem/texto) + o que a plataforma diz do pedido (comprador, status)."""

    comprador: str | None = COMPRADOR
    status: str | None = "COMPLETED"
    falhar_imagem: bool = False
    pausar_no_envio: bool = False


@pytest.fixture
def plataforma(monkeypatch):
    """A plataforma falsa: grava o que saiu (a imagem e o texto), nada de rede."""
    saidas = _Saidas()

    async def foto(session, conversa, integration, anexo, legenda, **kw):
        saidas.append(
            {"tipo": "imagem", "mime": anexo.mime, "sha": anexo.sha256, "legenda": legenda}
        )
        if saidas.pausar_no_envio:
            # Uma pessoa pausa a IA enquanto a imagem sobe (a frase não pode sair).
            conversa.ia_pausada = True
        if saidas.falhar_imagem:
            return ResultadoEnvio(ok=False, erro="shopee error_param")
        return ResultadoEnvio(
            ok=True,
            externo_id=f"img-{len(saidas)}",
            payload={"imagem_url": "https://cf.shopee.com.br/file/nf"},
        )

    async def texto(session, conversa, integration, t, **kw):
        saidas.append({"tipo": "texto", "texto": t})
        return ResultadoEnvio(ok=True, externo_id=f"txt-{len(saidas)}")

    async def pedido(session, conversa):
        # O pedido NA PLATAFORMA (ao vivo): de quem é e o status.
        return saidas.comprador, saidas.status

    monkeypatch.setattr(enviar, "_chamar_plataforma_foto", foto)
    monkeypatch.setattr(enviar, "_chamar_plataforma", texto)
    monkeypatch.setattr(nf_automatica, "pedido_na_plataforma", pedido)
    return saidas


async def _cenario(
    db: AsyncSession,
    make_user,
    *,
    modo: str = "auto",
    pedido_mkt: str = PEDIDO_MKT,
    dono: str | None = COMPRADOR,
    total: str = "424.00",
    situacao: str = "9",
    documento: str = CPF,
    notas: tuple[tuple[str, int, str], ...] = (("cheia", 1883, "424.00"),),
    outros_pedidos: int = 0,
    texto: str = "boa tarde, pode me mandar a nota fiscal do pedido?",
    categorias: tuple[str, ...] = ("rastreio", "nota_fiscal"),
    onde: dict | None = None,
):
    conversa = await _do_agente(
        db, make_user, modo=modo, auto_categorias=list(categorias), **(onde or {})
    )
    db.add(
        BlingOrder(
            bling_id=int(PEDIDO_BLING),
            item_index=0,
            numero=PEDIDO_BLING,
            numeroloja=pedido_mkt,
            total=Decimal(total),
            situacao=situacao,
            item_codigo="b096.20",
            documento_destinatario=documento,
            data=datetime.now(UTC) - timedelta(days=5),
        )
    )
    for _tipo, numero, valor in notas:
        db.add(
            NfNota(
                chave=_chave(numero),
                pedido_bling=PEDIDO_BLING,
                numero=str(numero),
                serie="1",
                emitente_cnpj="00000000000191",
                valor=Decimal(valor),
                data_emissao=datetime.now(UTC) - timedelta(days=4, minutes=numero % 60),
                situacao="100",
                xml=_xml(chave=_chave(numero), numero=str(numero), v_nf=valor),
            )
        )
    if dono is not None:
        db.add(
            AtendimentoPedidoComprador(
                integration_id=conversa.integration_id,
                plataforma="shopee",
                comprador_id=dono,
                pedido=pedido_mkt,
                criado_em=datetime.now(UTC) - timedelta(days=5),
            )
        )
    for i in range(outros_pedidos):
        db.add(
            AtendimentoPedidoComprador(
                integration_id=conversa.integration_id,
                plataforma="shopee",
                comprador_id=COMPRADOR,
                pedido=f"OUTRO{i}",
                criado_em=datetime.now(UTC) - timedelta(days=10),
            )
        )
    await db.commit()
    gatilho = await _msg(db, conversa, texto)
    return conversa, gatilho


async def _rascunho(db: AsyncSession, conversa, gatilho, **agente: Any) -> AtendimentoRascunho:
    fatos_agente = {
        "acoes_pessoa": ["enviar_arquivo_nf"],
        "sem_auto": [nf_automatica.MOTIVO_SEM_AUTO_NF],
        "avisos_auto": [],
        "pistas_turno": ["nota_fiscal"],
        "pistas_alem": [],
        "pistas_orientacao": [],
        "checagens": [],
        "modo": "auto",
        **agente,
    }
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=gatilho.id,
        texto="Vamos conferir e já te enviamos a nota fiscal.",
        categoria="nota_fiscal",
        confianca=0.9,
        precisa_humano=True,
        validador_ok=True,
        validador_erros=[],
        status=RASCUNHO_PENDENTE,
        prompt_versao=PROMPT_VERSAO_AGENTE,
        fatos={"chamados_abertos": 0, "devolucoes": 0, "agente": fatos_agente},
    )
    db.add(r)
    await db.commit()
    return r


def _decisao(r: AtendimentoRascunho) -> str | None:
    return ((r.fatos or {}).get(ia.FATO_AUTO_ENVIO) or {}).get("motivo")


async def _da_loja(db: AsyncSession, conversa) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.autor == "loja",
                )
                .order_by(AtendimentoMensagem.created_at)
            )
        )
        .scalars()
        .all()
    )


# ─────────────── o caminho feliz ───────────────


async def test_manda_a_imagem_da_nf_e_a_frase(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)

    assert await ia._talvez_enviar(db, conversa, r) is True

    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert [s["tipo"] for s in plataforma] == ["imagem", "texto"]
    assert plataforma[0]["mime"] == "image/jpeg"
    assert plataforma[1]["texto"] == nf_automatica.LEGENDA
    imagem, frase = await _da_loja(db, conversa)
    assert imagem.status == "enviada" and imagem.origem == "davinci_ia"
    assert imagem.payload["nf"] == {"chave": _chave(1883), "automatica": True}
    assert imagem.anexos[0]["nome"] == "NF_1883.jpg"
    assert frase.status == "enviada" and frase.texto == nf_automatica.LEGENDA
    await db.refresh(r)
    assert r.status == RASCUNHO_ENVIADO


async def test_documento_que_faltou_na_caixa_manda_a_nf(db, make_user, ligado, plataforma):
    """Revisão de 29/10 (Barbosa e5fe56d4): "nem recibo nada documento" é o pedido da NF."""
    conversa, gatilho = await _cenario(
        db, make_user, texto="Não veio nota fiscal..nem recibo nada documento"
    )
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert [s["tipo"] for s in plataforma] == ["imagem", "texto"]


async def test_a_nf_nunca_sai_duas_vezes_na_conversa(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert len(plataforma) == 2

    # O cliente pede de novo: um rascunho novo, a mesma NF → pessoa.
    gatilho2 = await _msg(db, conversa, "manda a nota fiscal de novo por favor", ha=timedelta(0))
    r2 = await _rascunho(db, conversa, gatilho2)
    await ia._talvez_enviar(db, conversa, r2)
    assert _decisao(r2) == "nf:nf_ja_enviada"
    assert len(plataforma) == 2

    # E o mesmo rascunho de novo (rodada repetida): já não está pendente.
    await ia._talvez_enviar(db, conversa, r)
    assert len(plataforma) == 2


async def test_nf_mandada_pela_pessoa_no_botao_tambem_conta(db, make_user, ligado, plataforma):
    """A DANFE é determinística: a imagem que a pessoa mandou pelo botão "NF" tem o
    mesmo sha256 — o agente não manda de novo."""
    from app.services.atendimento import danfe, foto

    conversa, gatilho = await _cenario(db, make_user)
    pdf = danfe.gerar_pdf(_xml(chave=_chave(1883), numero="1883", v_nf="424.00"))
    anexo = foto.validar_para_a_caixa(conversa, pdf, "NF_1883.pdf", foto.PDF)
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id="pessoa-nf",
            autor="loja",
            origem="davinci_humano",
            tipo="imagem",
            status="enviada",
            enviada_em=datetime.now(UTC) - timedelta(days=1),
            payload={"foto": {"sha256": anexo.sha256}},
        )
    )
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_ja_enviada" and plataforma == []


# ─────────────── LGPD: nunca a NF de outro cliente ───────────────


@pytest.mark.parametrize(
    ("cenario", "esperado"),
    [
        # O pedido da conversa é de OUTRO comprador no índice da Shopee.
        ({"dono": "outrapessoa77"}, "nf:pedido_de_outro_comprador"),
        # Sem o índice do pedido: não dá para conferir o comprador.
        ({"dono": None}, "nf:comprador_nao_conferido"),
        # O destinatário da NF não é o do pedido (o CPF do Bling é outro).
        ({"documento": "98765432100"}, "nf:destinatario_diferente"),
        # O comprador tem mais de um pedido recente na loja: qual NF?
        ({"outros_pedidos": 1}, "nf:varios_pedidos_do_comprador"),
        # Só a NF de embalagem (1%): a cheia ainda não existe.
        ({"notas": (("embalagem", 1884, "4.24"),)}, "nf:nf_so_embalagem"),
        # Pedido cancelado no Bling.
        ({"situacao": "12"}, "nf:nf_pedido_cancelado"),
        # Sem NF nenhuma do pedido.
        ({"notas": ()}, "nf:nf_nao_chegou"),
    ],
)
async def test_nunca_a_nf_errada(db, make_user, ligado, plataforma, cenario, esperado):
    conversa, gatilho = await _cenario(db, make_user, **cenario)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == esperado
    assert plataforma == []
    assert await _da_loja(db, conversa) == []


async def test_carrinho_com_varios_pedidos_no_bling_fica_com_a_pessoa(
    db, make_user, ligado, plataforma
):
    conversa, gatilho = await _cenario(db, make_user)
    db.add(
        BlingOrder(
            bling_id=910002,
            item_index=0,
            numero="910002",
            numeroloja=PEDIDO_MKT,
            total=Decimal("100.00"),
            situacao="9",
            documento_destinatario=CPF,
            data=datetime.now(UTC) - timedelta(days=5),
        )
    )
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) in ("nf:nf_varios_pedidos", "nf:pedido_do_bling_diferente")
    assert plataforma == []


async def test_chave_que_nao_bate_com_o_numero_segura(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user)
    nota = await db.scalar(select(NfNota).where(NfNota.pedido_bling == PEDIDO_BLING))
    nota.numero = "1999"  # a linha diz um número, a chave e o XML outro
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:chave_numero" and plataforma == []


# ─────────────── só no automático, só o pedido claro ───────────────


async def test_canal_fora_do_auto_fica_com_a_pessoa(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user, modo="humano")
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:canal_fora_do_auto" and plataforma == []


async def test_chave_geral_desligada(db, make_user, ligado, plataforma, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_nf_automatica", False)
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_automatica_desligada" and plataforma == []


@pytest.mark.parametrize(
    "texto",
    [
        "vem com nota fiscal?",
        "voces mandam nota fiscal?",
        "preciso da nota fiscal no cnpj da minha empresa",
        "a nota veio com o nome errado, pode corrigir?",
        "manda o xml da nota",
        "quero cancelar e preciso da nota fiscal",
        # Revisão de 28/10: o que sobrava fora da lista do que pode.
        "manda a nota fiscal no meu cpf 123.456.789-09",
        "manda a nota fiscal no nome da minha esposa",
        "manda a nota fiscal, o meu cpf mudou",
        "manda a nota fiscal do outro pedido",
        "manda a nota fiscal dos dois pedidos",
        "não recebi a nota fiscal nem o produto",
        "não veio a nota fiscal e veio faltando o cabo",
        # Revisão de 29/10: o produto que não veio, com as palavras da lista.
        "não chegou nada, cadê a nota fiscal?",
        "não recebi o pedido, cadê a nota fiscal?",
        "o produto ainda não chegou, me manda a nota fiscal",
        "não veio nenhum produto nem nota fiscal",
        "não veio nota fiscal nem nada",
    ],
)
async def test_pedido_ambiguo_ou_que_exige_pessoa(db, make_user, ligado, plataforma, texto):
    conversa, gatilho = await _cenario(db, make_user, texto=texto)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:pedido_nao_claro" and plataforma == []


@pytest.mark.parametrize(
    ("agente", "esperado"),
    [
        ({"pistas_turno": ["nota_fiscal", "rastreio"]}, "nf:outro_assunto"),
        ({"avisos_auto": ["algo"]}, "nf:avisos_auto"),
        ({"modo": "copiloto"}, "nf:gerado_no_copiloto"),
        ({"sem_auto": [nf_automatica.MOTIVO_SEM_AUTO_NF, "validador: x"]}, "nf:sem_auto"),
    ],
)
async def test_rascunho_que_nao_e_so_a_nf(db, make_user, ligado, plataforma, agente, esperado):
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho, **agente)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == esperado and plataforma == []


async def test_nf_com_outra_acao_segue_o_caminho_de_sempre(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho, acoes_pessoa=["enviar_arquivo_nf", "abrir_chamado"])
    await ia._talvez_enviar(db, conversa, r)
    assert not str(_decisao(r)).startswith("nf:") and plataforma == []


# ─────────────── as partes puras ───────────────


@pytest.mark.parametrize(
    ("texto", "claro"),
    [
        ("pode me mandar a nota fiscal?", True),
        ("Boa tarde! Poderia enviar a NF do meu pedido por aqui?", True),
        ("preciso da nota fiscal do produto", True),
        ("não veio a nota fiscal na caixa", True),
        ("a nota fiscal não veio junto", True),
        ("cadê a nf?", True),
        ("vem com nota?", False),
        ("tem nota fiscal?", False),
        ("já recebi a nota, obrigado", False),
        ("manda a nota no cnpj", False),
        ("a nf está errada", False),
        ("qual o número de série?", False),
        ("dei nota 5 pra vocês", False),
        ("eu vou querer a nota fiscal", True),
        ("solicito a nota fiscal do pedido", True),
        # Revisão de 29/10 (Barbosa e5fe56d4): o documento que faltou na caixa.
        ("Não veio nota fiscal..nem recibo nada documento", True),
        ("não recebi nota fiscal nenhuma", True),
        ("não veio nada", False),
        ("não veio nada, cadê a nota?", False),
        ("não recebi a nota fiscal nem o produto", False),
        ("nao recebi o pedido, cade a nota fiscal", False),
        ("não chegou ainda o pedido, manda a nota", False),
        ("o pacote não chegou, preciso da nota fiscal", False),
        ("veio vazio, cadê a nota", False),
        ("ainda não chegou, preciso da nota fiscal", False),
        ("boa tarde, não recebi a nota fiscal", True),
        ("a nota do pedido não veio", True),
        ("a nota fiscal da compra não chegou", True),
        # A chave de acesso está na DANFE (Barbosa 75fd5e5d, 28/10 17:03) — só com a nota.
        ("Boa tarde por favor pode mandar a chave de acesso para eu emitir a nota fiscal", True),
        ("pode me passar a chave de acesso da nf?", True),
        ("manda a chave de acesso", False),
        ("manda a chave de acesso para eu emitir a nota fiscal de devolução", False),
        ("", False),
    ],
)
def test_pedido_claro(texto, claro):
    assert nf_automatica.pedido_claro([texto]) is claro


def test_conferir_xml_pega_chave_trocada():
    from types import SimpleNamespace

    xml = _xml(chave=_chave(1883), numero="1883")
    ok = SimpleNamespace(chave=_chave(1883), numero="1883", emitente_cnpj="00000000000191")
    assert nf_automatica.conferir_xml(xml, ok) is None
    assert (
        nf_automatica.conferir_xml(xml, SimpleNamespace(**{**vars(ok), "chave": _chave(1884)}))
        == "chave_do_xml"
    )
    assert (
        nf_automatica.conferir_xml(xml, SimpleNamespace(**{**vars(ok), "emitente_cnpj": "1"}))
        == "chave_emitente"
    )
    assert nf_automatica.conferir_xml(_xml(chave=_chave(1883), numero="1883", tp_amb="2"), ok) == (
        "nao_autorizada"
    )


# ─────────────── a regra genérica (complemento do Eduardo) ───────────────


async def test_comprador_diferente_na_plataforma_segura(db, make_user, ligado, plataforma):
    """O índice pode estar errado; a plataforma, ao vivo, diz de quem é o pedido."""
    plataforma.comprador = "outrapessoa77"
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:comprador_diferente_na_plataforma" and plataforma == []


async def test_sem_resposta_da_plataforma_segura(db, make_user, ligado, plataforma):
    plataforma.comprador = None
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:comprador_diferente_na_plataforma" and plataforma == []


@pytest.mark.parametrize(
    ("categorias", "esperado"),
    [(("rastreio",), "nf:assunto_nao_liberado")],
)
async def test_nota_fiscal_tem_que_estar_marcada_na_loja(
    db, make_user, ligado, plataforma, categorias, esperado
):
    conversa, gatilho = await _cenario(db, make_user, categorias=categorias)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == esperado and plataforma == []


async def test_nota_fiscal_tem_que_estar_marcada_para_o_agente(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user)
    canal = await db.get(AtendimentoCanal, conversa.canal_id)
    canal.auto_agente = ["rastreio"]
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:assunto_nao_liberado_agente" and plataforma == []


async def test_cartao_de_outro_pedido_na_conversa_segura(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user)
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="cartao-outro",
        autor="cliente",
        texto=None,
        enviada_em=datetime.now(UTC) - timedelta(days=2),
        tipo="pedido",
        anexos=[{"tipo": "pedido", "pedido": "OUTROPEDIDO1"}],
    )
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:varios_pedidos_do_comprador" and plataforma == []


async def test_tiktok_manda_a_imagem_e_a_frase(db, make_user, ligado, plataforma):
    from app.models import IntegrationPlatform

    conversa, gatilho = await _cenario(
        db,
        make_user,
        dono=None,  # o TikTok não tem o índice: vale a conferência ao vivo
        onde={"plataforma": "tiktok", "platform": IntegrationPlatform.TIKTOK},
    )
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert [s["tipo"] for s in plataforma] == ["imagem", "texto"]
    assert plataforma[0]["mime"] == "image/jpeg"


async def test_ml_pos_venda_manda_o_pdf_com_a_frase_junto(db, make_user, ligado, plataforma):
    from app.models import IntegrationPlatform

    conversa, gatilho = await _cenario(
        db,
        make_user,
        dono=None,
        onde={
            "plataforma": "ml",
            "canal": "pos_venda",
            "platform": IntegrationPlatform.ML,
            "externo_id": PEDIDO_MKT,
        },
    )
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    # Uma mensagem só: o PDF dentro da mensagem com a frase (o ML exige texto).
    assert len(plataforma) == 1
    assert plataforma[0]["mime"] == "application/pdf"
    assert plataforma[0]["legenda"] == nf_automatica.LEGENDA
    (nf,) = await _da_loja(db, conversa)
    assert nf.tipo == "arquivo" and nf.texto == nf_automatica.LEGENDA


# ─────────────── a revisão de 28/10 ───────────────


async def test_cancelado_na_plataforma_segura(db, make_user, ligado, plataforma):
    plataforma.status = "IN_CANCEL"
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:pedido_cancelado_na_plataforma" and plataforma == []


@pytest.mark.parametrize(
    ("notas", "esperado"),
    [
        # Duas cheias (reemitida, ou a de outro pedido do mesmo CPF): qual?
        ((("cheia", 1883, "424.00"), ("cheia", 1890, "424.00")), "nf:mais_de_uma_nf_cheia"),
        # A "cheia" que passa do total do pedido não é a deste pedido.
        ((("cheia", 1883, "600.00"),), "nf:nf_acima_do_pedido"),
    ],
)
async def test_nota_unica_e_do_tamanho_do_pedido(
    db, make_user, ligado, plataforma, notas, esperado
):
    conversa, gatilho = await _cenario(db, make_user, notas=notas)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == esperado and plataforma == []


async def test_nf_de_antes_do_pedido_segura(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user)
    nota = await db.scalar(select(NfNota).where(NfNota.pedido_bling == PEDIDO_BLING))
    nota.data_emissao = datetime.now(UTC) - timedelta(days=30)
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_antes_do_pedido" and plataforma == []


async def test_assunto_so_humano_no_manual(db, make_user, ligado, plataforma, monkeypatch):
    from app.services.atendimento import manual as manual_svc

    monkeypatch.setattr(manual_svc, "ids_so_humano", lambda categorias: {"nota_fiscal"})
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:assunto_so_humano" and plataforma == []


async def test_verificador_desligado_e_confianca_baixa(
    db, make_user, ligado, plataforma, monkeypatch
):
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    r.confianca = 0.2
    await db.commit()
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:confianca_baixa"
    monkeypatch.setattr(get_settings(), "atendimento_agente_verificador", False)
    r.confianca = 0.9
    await db.commit()
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:verificador_desligado" and plataforma == []


async def test_cliente_escreveu_de_novo_segura(db, make_user, ligado, plataforma):
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await _msg(db, conversa, "e qual o prazo de entrega?", ha=timedelta(0))
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:turno_mudou" and plataforma == []


async def test_imagem_que_falhou_devolve_a_sugestao_para_a_pessoa(
    db, make_user, ligado, plataforma
):
    plataforma.falhar_imagem = True
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    await db.refresh(r)
    assert r.status == RASCUNHO_PENDENTE
    assert _decisao(r) == "nf:falhou"
    assert [x["tipo"] for x in plataforma] == ["imagem"]  # a frase não sai


async def test_frase_nao_sai_se_a_ia_foi_pausada_no_meio(db, make_user, ligado, plataforma):
    plataforma.pausar_no_envio = True
    conversa, gatilho = await _cenario(db, make_user)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert [x["tipo"] for x in plataforma] == ["imagem"]


@pytest.mark.parametrize(
    "marca",
    [
        {"anexos": [{"tipo": "imagem", "nome": "NF_1883.jpg"}], "payload": {}},
        {"anexos": [], "payload": {"nf": {"chave": _chave(1883), "automatica": False}}},
    ],
)
async def test_nf_da_pessoa_pelo_nome_ou_pela_chave(db, make_user, ligado, plataforma, marca):
    conversa, gatilho = await _cenario(db, make_user)
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id="pessoa-nf2",
            autor="loja",
            origem="davinci_humano",
            tipo="imagem",
            status="enviada",
            enviada_em=datetime.now(UTC) - timedelta(days=1),
            **marca,
        )
    )
    await db.commit()
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:nf_ja_enviada" and plataforma == []


def test_pedido_pelo_menu():
    from types import SimpleNamespace as M

    menu = M(id=1, autor="loja", texto="Escolha: 1 - Rastreio 2 - Nota fiscal 3 - Encerramento")
    oi, dois = M(id=0, autor="cliente", texto="oi"), M(id=2, autor="cliente", texto="2")
    assert nf_automatica.pedido_pelo_menu([oi, menu, dois], dois) is True
    outro = M(id=1, autor="loja", texto="Escolha: 1 - Rastreio 2 - Garantia")
    assert nf_automatica.pedido_pelo_menu([oi, outro, dois], dois) is False
    tres = M(id=2, autor="cliente", texto="3")
    assert nf_automatica.pedido_pelo_menu([oi, menu, tres], tres) is False
    mais = M(id=3, autor="cliente", texto="e o rastreio?")
    assert nf_automatica.pedido_pelo_menu([oi, menu, dois, mais], dois) is False
    # O gatilho de antes do menu não é a resposta a ele.
    assert nf_automatica.pedido_pelo_menu([oi, menu, dois], oi) is False


async def test_resposta_2_ao_menu_manda_a_nf(db, make_user, ligado, plataforma):
    conversa, _ = await _cenario(db, make_user, texto="oi")
    await _msg(
        db,
        conversa,
        "Escolha uma opção: 1 - Rastreio 2 - Nota fiscal 3 - Encerramento da compra",
        autor="loja",
        origem="davinci_auto",
        ha=timedelta(minutes=2),
    )
    gatilho = await _msg(db, conversa, "2", ha=timedelta(minutes=1))
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
