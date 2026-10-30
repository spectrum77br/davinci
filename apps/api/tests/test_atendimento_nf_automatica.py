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
# O menu do robô do Duoke (o texto de produção; não é resposta real da loja).
_MENU_DUOKE = (
    "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo! "
    "1 - Previsão de entrega / Envio 2 - Nota fiscal 3 - Encerramento da compra "
    "4 - Troca de endereço 5 - Garantia 6 - Falar com atendente"
)


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


@pytest.mark.parametrize(
    "texto",
    [
        # Revisão de 30/10: os casos de 29/10 nas lojas no automático (Barbosa e12c7799,
        # Vita 95704e8d), que ficaram como `nf:pedido_nao_claro`.
        "a notafiscal?",
        "O meu celular não veio com nota fiscal",
        "o pedido veio sem nota fiscal",
        "Nota fiscal por favor",
    ],
)
async def test_grafia_documento_sozinho_e_ausencia_mandam_a_nf(
    db, make_user, ligado, plataforma, texto
):
    conversa, gatilho = await _cenario(db, make_user, texto=texto)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert [s["tipo"] for s in plataforma] == ["imagem", "texto"]


async def test_foto_no_turno_do_pedido_escrito_fica_com_a_pessoa(db, make_user, ligado, plataforma):
    """Terceira revisão de 30/10: a foto (o defeito, o item errado) junto do pedido escrito."""
    conversa, _ = await _cenario(db, make_user, texto="boa noite")
    await _msg(db, conversa, None, tipo="imagem", ha=timedelta(minutes=2))
    gatilho = await _msg(db, conversa, "me manda a nota fiscal", ha=timedelta(minutes=1))
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:pedido_nao_claro" and plataforma == []


async def test_pedido_escrito_com_o_2_do_menu_no_meio_manda_a_nf(db, make_user, ligado, plataforma):
    """Revisão de 30/10 (Barbosa f1875dc1): o "2" do menu no meio do pedido escrito."""
    conversa, _ = await _cenario(
        db, make_user, texto="por favor, me envie a nota fiscal do produto"
    )
    await _msg(db, conversa, _MENU_DUOKE, autor="loja", origem="externo", ha=timedelta(minutes=3))
    await _msg(db, conversa, "2", ha=timedelta(minutes=2))
    gatilho = await _msg(
        db, conversa, "por favor, me envie a nota fiscal por aqui", ha=timedelta(minutes=1)
    )
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)


@pytest.mark.parametrize(
    ("antes", "tipo", "depois"),
    [
        # Revisão da revisão de 30/10: o Vita 95704e8d de 29/10 — o botão "Conversar com
        # vendedor" do FAQ da Shopee, o menu, o "2" e o pedido escrito.
        ("Conversar com vendedor", "faq_liveagent", "O meu celular não veio com nota fiscal"),
        # O elogio do que chegou antes do menu e o "2" sozinho.
        ("veio tudo certinho, muito obrigada!", None, None),
    ],
)
async def test_2_do_menu_com_o_botao_do_faq_ou_o_elogio_antes_manda_a_nf(
    db, make_user, ligado, plataforma, antes, tipo, depois
):
    conversa, primeira = await _cenario(db, make_user, texto=antes)
    if tipo is not None:
        primeira.payload = {"message_type": tipo}
        await db.commit()
    await _msg(db, conversa, _MENU_DUOKE, autor="loja", origem="externo", ha=timedelta(minutes=3))
    gatilho = await _msg(db, conversa, "2", ha=timedelta(minutes=2))
    if depois is not None:
        gatilho = await _msg(db, conversa, depois, ha=timedelta(minutes=1))
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "enviado_nf", _decisao(r)
    assert [s["tipo"] for s in plataforma] == ["imagem", "texto"]


async def test_2_do_menu_nao_libera_o_acessorio_escrito_depois(db, make_user, ligado, plataforma):
    """O "2" sai das falas, o resto passa pela régua: o carregador que não veio → pessoa."""
    conversa, _ = await _cenario(db, make_user, texto="oi")
    await _msg(db, conversa, _MENU_DUOKE, autor="loja", origem="externo", ha=timedelta(minutes=3))
    await _msg(db, conversa, "2", ha=timedelta(minutes=2))
    gatilho = await _msg(
        db, conversa, "o celular não veio com o carregador nem a nota", ha=timedelta(minutes=1)
    )
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:pedido_nao_claro" and plataforma == []


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
        # Revisão de segurança de 29/10: o conectivo solto (sem "não") e a "chave" sozinha.
        "recebi a caixa mas nada do produto, manda a nota fiscal",
        "o pedido nem chegou, manda a nota",
        "manda a nota fiscal, nem recebi ainda",
        "não veio a nota fiscal, o produto também não",
        "não recebi a nota fiscal ou o produto",
        "não veio a chave, manda a nota fiscal",
        "manda a chave de acesso do produto e a nota fiscal",
        # Segunda revisão de segurança de 29/10 (passavam pela lista).
        "o pedido chegou mas não veio a nota e um produto",
        "pode mandar a nota mas e o produto?",
        "quero meu produto e a nota fiscal",
        "o produto não estava na caixa manda a nota",
        "vou mandar o documento pra nota",
        "essa nota não é minha, manda a minha nota",
        "recebi o pedido mas a nota não é minha manda a minha nota",
        "não quero a nota fiscal",
        # Revisão de 30/10: o produto/acessório ou o problema ao lado da grafia nova.
        "não veio o carregador nem a notafiscal",
        "o celular veio sem nota e sem carregador",
        "o celular não chegou, manda a nota fiscal",
        "o celular veio sem nota, quero devolver",
        "a danfe veio com o nome errado",
        "tem nota fiscal?",
        # Revisão da revisão de 30/10: o "que" da lista não abre o produto que não veio.
        "manda a nota fiscal do produto que não veio",
        "me manda a nota fiscal só que ainda não chegou",
        "me manda a nota fiscal que o meu ainda não chegou",
        "o carregador não veio junto com o produto, manda a nota",
    ],
)
async def test_pedido_ambiguo_ou_que_exige_pessoa(db, make_user, ligado, plataforma, texto):
    conversa, gatilho = await _cenario(db, make_user, texto=texto)
    r = await _rascunho(db, conversa, gatilho)
    await ia._talvez_enviar(db, conversa, r)
    assert _decisao(r) == "nf:pedido_nao_claro" and plataforma == []


async def test_produto_que_nao_veio_na_fala_de_antes_fica_com_a_pessoa(
    db, make_user, ligado, plataforma
):
    """Revisão de segurança de 29/10: "nada do produto" numa fala e "manda a nota" na outra."""
    conversa, gatilho = await _cenario(db, make_user, texto="manda a nota fiscal")
    await _msg(db, conversa, "recebi a caixa mas nada do produto", ha=timedelta(minutes=7))
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
        ("manda a nota fiscal mas ainda não chegou", False),
        ("o pedido chegou mas não veio a nota fiscal", True),
        ("recebi o produto, mas não veio a nota fiscal ou recibo.", True),
        ("envia também a nota fiscal, por favor", True),
        ("veio a nota fiscal mas não veio o produto", False),
        ("boa tarde, não recebi a nota fiscal", True),
        ("a nota do pedido não veio", True),
        ("a nota fiscal da compra não chegou", True),
        # A chave de acesso está na DANFE (Barbosa 75fd5e5d, 28/10 17:03) — só com a nota.
        ("Boa tarde por favor pode mandar a chave de acesso para eu emitir a nota fiscal", True),
        ("pode me passar a chave de acesso da nf?", True),
        ("manda a chave de acesso", False),
        ("manda a chave de acesso para eu emitir a nota fiscal de devolução", False),
        ("", False),
        # Revisão de segurança de 29/10: "nem/nada/nenhum/ou/também" só grudados no papel.
        ("não veio nota fiscal nem recibo", True),
        ("não recebi nenhuma nota fiscal", True),
        ("pode mandar a nota fiscal ou o recibo?", True),
        ("não veio a nota fiscal nem o comprovante", True),
        ("nada do produto ainda, manda a nota fiscal", False),
        ("nem chegou ainda, manda a nota fiscal", False),
        ("ainda nem chegou o pedido, manda a nota", False),
        ("o produto nem chegou ainda, manda a nota fiscal", False),
        ("chegou nada, manda a nota fiscal", False),
        ("recebi nada, manda a nota fiscal", False),
        ("ainda nada, manda a nota fiscal", False),
        ("chegou nenhum, cadê a nota fiscal", False),
        ("nao veio nota fiscal e o produto tambem nao", False),
        ("não veio a nota fiscal e a caixa também não", False),
        ("manda a nota fiscal e também o produto", False),
        ("quero a nota fiscal e o pedido também", False),
        ("cadê a nota fiscal ou o produto", False),
        ("não chegou a nota fiscal ou o pedido", False),
        # O verbo subentendido sem o "também" (passava antes desta revisão).
        ("não veio a nota fiscal e o produto não", False),
        ("manda a nota fiscal mas o produto não", False),
        # A "chave" sozinha é a que não veio na caixa; só a "chave de acesso" (da nota).
        ("a chave não veio, manda a nota fiscal", False),
        ("não recebi a chave nem a nota fiscal", False),
        ("manda a chave e a nota fiscal", False),
        ("manda a chave de acesso da nota fiscal do pedido", True),
        # Segunda revisão de segurança de 29/10. O "mas" só antes do "não" (ou do lugar).
        ("o pedido chegou mas nao veio a nota e um produto", False),
        ("O PEDIDO CHEGOU MAS NAO VEIO A NOTA E UM PRODUTO", False),
        ("O pedido chegou, mas não veio a nota e o produto", False),
        ("pedido chegou mas nao veio a nf e o produto 😡", False),
        ("pode mandar a nota mas e o produto?", False),
        ("vcs podem mandar a nota mas e o meu pedido??", False),
        ("Manda a nota fiscal, mas e a minha compra?", False),
        ("chegou o pacote mas nao veio a nota fiscal e a caixa", False),
        ("manda a nota mas a minha", False),
        ("pode mandar a nota mas da minha compra", False),
        ("mas e aí, manda a nota", False),
        ("recebi a caixa mas na embalagem não tinha a nota", True),
        # O produto ao lado da nota, pedido ou com "não".
        ("quero meu produto e a nota fiscal", False),
        ("preciso do produto e da nota", False),
        ("Manda o produto e a nota fiscal", False),
        ("me envia o pedido e a nota", False),
        ("quero receber o produto e a nota", False),
        ("manda a nota e o pedido de novo", False),
        ("nao veio a nota e o produto", False),
        ("nao veio nota e pedido", False),
        ("cadê o pedido e a nota fiscal não veio", False),
        ("o produto nao estava na caixa manda a nota", False),
        ("o produto nao tinha na caixa manda a nota", False),
        ("nao veio a nota o produto nao", False),
        ("ainda nao chegou preciso da nota", False),
        ("recebi o pedido e a caixa não tinha a nota", True),
        ("o pedido não tinha a nota fiscal", True),
        ("recebi o produto e a nota não veio", True),
        # O recibo/documento/comprovante só grudado no "nem/nada/ou".
        ("vou mandar o documento pra nota", False),
        ("preciso mandar o documento pra nota?", False),
        ("vou enviar o comprovante pra receber a nota", False),
        ("manda a nota e os documentos", False),
        ("nao veio a nota fiscal e nem recibo do produto", True),
        # A nota que não é a dele, a "certa", o "não quero" e outra nota.
        ("essa nota nao e minha manda a minha nota", False),
        ("manda a nota de novo essa nao e minha", False),
        ("manda a nota o pedido nao e meu", False),
        ("manda a nota do pedido certo", False),
        ("manda a nota fiscal mas do pedido certo", False),
        ("nao quero a nota", False),
        ("nao gostaria de receber a nota", False),
        ("nao queria a nota, queria o produto", False),
        ("manda a segunda nota", False),
        ("nao veio a nota de um produto", False),
        ("manda a nota desse pedido e tambem a nota do pedido", False),
        ("preciso da segunda via da nota fiscal por favor", True),
        # O "emitir" só com o "eu"; a "chave de acesso" com o "nem" é outra coisa.
        ("manda a nota pra emitir", False),
        ("nao veio a chave de acesso nem a nota", False),
        # Revisão de 30/10 ("faça esse ajuste para termos mais entregas de NF automaticamente
        # quando é requisitado"). A grafia do documento: grudada, abreviada, a danfe, o cupom.
        ("a notafiscal?", True),
        ("me manda a notafiscal", True),
        ("preciso da nota fical", True),
        ("cadê a notafical?", True),
        ("não veio a nota fiscall", True),
        ("não veio a nota física na caixa", True),
        ("não veio a nfe", True),
        ("pode mandar a NF-e?", True),
        ("manda a danfe por favor", True),
        ("gostaria de receber a nota fiscal danfe por favor", True),
        ("preciso do cupom fiscal", True),
        ("manda a nota da compra", True),
        ("cadê minha nota fiscal?", True),
        ("o cupom não veio", False),
        ("manda o cupom", False),
        ("tem nfc?", False),
        ("o celular tem nfc?", False),
        ("manda as notas fiscais", False),
        ("manda o xml da danfe", False),
        ("a danfe veio com o nome errado", False),
        # O documento sozinho no turno (no lugar do "2" do menu) — só nomeado por inteiro.
        ("nota fiscal", True),
        ("Nota fiscal por favor", True),
        ("boa tarde, nota fiscal", True),
        ("a nota fiscal do pedido?", True),
        ("e a nota fiscal?", True),
        ("NF?", True),
        ("nota fiscal da minha compra, por gentileza", True),
        ("nota?", False),
        ("a nota?", False),
        ("tem nota fiscal?", False),
        ("nota fiscal vem?", False),
        ("com nota fiscal?", False),
        ("sem nota fiscal?", False),
        ("nota fiscal no meu nome", False),
        ("nota fiscal do outro pedido", False),
        ("a nota fiscal veio?", False),
        # A ausência com o documento como sujeito: "não veio COM", "veio/chegou SEM", "faltou".
        ("não veio com a nota fiscal", True),
        ("o pedido não veio com nota fiscal", True),
        ("o produto não veio com a nota", True),
        ("veio sem nota fiscal", True),
        ("o produto veio sem nota", True),
        ("o pedido chegou sem a nota fiscal", True),
        ("recebi sem nota fiscal", True),
        ("ficou faltando a nf", True),
        ("está faltando a nota fiscal do pedido", True),
        ("pode mandar sem nota", False),
        ("vocês vendem sem nota?", False),
        ("o celular veio com nota?", False),
        ("veio com a nota de outra pessoa", False),
        ("veio sem caixa e sem nota", False),
        ("veio sem nada nem nota", False),
        ("faltou o carregador e a nota", False),
        ("faltou a nota e o carregador", False),
        ("está faltando a nota de um produto", False),
        ("não veio sem nota, manda a nota", False),
        ("não está faltando a nota, manda a nota", False),
        ("a nota não faltou, manda a nota", False),
        # O aparelho é o produto: as mesmas travas do produto.
        ("O meu celular não veio com nota fiscal", True),
        ("o celular veio sem a nota fiscal", True),
        ("o relógio não veio com nota fiscal", True),
        ("o celular chegou mas não veio a nota fiscal", True),
        ("recebi o celular mas não veio a nota fiscal", True),
        ("o aparelho chegou, porém não veio a nota fiscal", True),
        ("bom dia preciso da nota do celular", True),
        ("o celular veio faltando a nota fiscal", True),
        ("o celular não veio", False),
        ("o celular não veio, cadê a nota?", False),
        ("o celular não chegou, manda a nota", False),
        ("o celular não veio nem a nota", False),
        ("o produto não veio nem a nota", False),
        ("não veio o celular nem a nota", False),
        ("quero o celular e a nota", False),
        ("o celular não veio com a nota e o carregador", False),
        ("o celular não funciona, manda a nota", False),
        ("os celulares não vieram com nota", False),
        ("o outro celular veio sem nota", False),
        ("não veio com a nota de um celular", False),
        ("o celular não veio com a nota do outro pedido", False),
        ("o telefone de contato não veio na nota", False),
        # O acessório que não veio e o problema ficam com a pessoa.
        ("não veio o carregador nem a nota", False),
        ("não veio a nota nem o carregador", False),
        ("o celular não veio com o carregador nem a nota", False),
        ("o celular veio sem nota e sem carregador", False),
        ("o celular veio sem a nota e sem o fone", False),
        ("o carregador não veio com a nota", False),
        ("o fone não veio", False),
        ("o celular veio quebrado e sem nota", False),
        ("o celular não liga e veio sem nota", False),
        ("o celular veio sem nota, quero devolver", False),
        ("o celular veio sem nota, preciso para a garantia", False),
        ("o celular veio sem nota, é original?", False),
        ("preciso da nota para acionar a garantia", False),
        ("preciso da nota pra trocar", False),
        ("não veio a nota, quero o reembolso", False),
        # A cortesia, o "que", o "só", o "mesmo", o subjuntivo, o "como faço", o "dela".
        ("vcs podem enviar a nota fiscal desde já agradeço muito obrigado", True),
        ("por gentileza me enviar a nota fiscal", True),
        ("manda a nota fiscal, fico no aguardo", True),
        ("sim, vc pode me enviar a nota fiscal por aqui mesmo", True),
        ("gostaria que me enviasse a nota fiscal", True),
        ("gostaria que vcs mandassem a nota fiscal", True),
        ("só não veio a nota fiscal", True),
        ("so quero minha nota fiscal", True),
        ("quero a nota fiscal que não veio", True),
        ("por que não veio a nota fiscal?", True),
        ("recebi meu pedido, porém não veio a nota fiscal e preciso dela", True),
        ("não veio a nota fiscal e vou precisar dela", True),
        ("pode me encaminhar a nota fiscal?", True),
        ("como faço para ter acesso à nota fiscal?", True),
        ("como faço pra receber a nota?", True),
        ("como consigo a nf do produto?", True),
        ("manda a nota dela", False),
        ("só veio a nota fiscal, cadê o produto?", False),
        ("só chegou a nota fiscal, o produto não", False),
        ("so recebi a nota, preciso do produto", False),
        ("gostaria que cancelasse e mandasse a nota", False),
        ("a nota que veio não é minha", False),
        ("acho que não veio a nota", False),
        ("manda a nota que eu preciso pra garantia", False),
        ("preciso que mande a nota de outro pedido", False),
        ("encaminha o pedido e a nota", False),
        ("como faço para trocar a nota?", False),
        ("como consigo a nota no cnpj?", False),
        ("como faço pra emitir a nota?", False),
        ("como faço para receber o produto e a nota?", False),
        ("como faço pra devolver e pegar a nota?", False),
        ("como posso ter nota fiscal?", False),
        # Revisão da revisão de 30/10: o "que (ainda) não" da oração do produto e o pronome no
        # lugar do produto ficam com a pessoa; o "só que" corta o trecho como o "mas".
        ("me manda a nota fiscal só que ainda não chegou", False),
        ("manda a nota fiscal do pedido que ainda não chegou", False),
        ("manda a nota fiscal do produto que não veio", False),
        ("manda a nota fiscal do celular que não veio", False),
        ("preciso da nota fiscal do produto que não recebi", False),
        ("quero a nota fiscal da compra que não recebi", False),
        ("cadê a nota fiscal do pedido que não veio", False),
        ("nota fiscal do pedido que não chegou", False),
        ("preciso da nota fiscal do relógio q não chegou", False),
        ("me manda a nota fiscal que o meu ainda não chegou", False),
        ("preciso da nota fiscal que o mesmo ainda não chegou", False),
        ("preciso da nota fiscal mesmo que ainda não chegou", False),
        ("manda a nota fiscal que a minha compra ainda não chegou", False),
        ("manda a nota fiscal so q ainda nao chegou", False),
        ("o pedido chegou só que não veio a nota fiscal", True),
        ("chegou o celular, só que não veio a nota fiscal", True),
        ("é que não veio a nota fiscal", True),
        ("manda a nf que não chegou", True),
        ("o meu não veio com nota fiscal", True),
        ("eu mesmo não recebi a nota fiscal", True),
        # Revisão da revisão de 30/10 (comparado com o código no ar): o elogio, o cumprimento
        # escrito de outro jeito, "enviar ela", "se puder", "junto com o produto" e "não
        # acompanhou a nota" — com as mesmas travas do produto, do acessório e do problema.
        ("veio tudo certinho, me manda a nota fiscal", True),
        ("chegou tudo perfeito, só não veio a nota fiscal", True),
        ("oie não recebi a nota fiscal", True),
        ("opa, tudo joia? por favor, preciso da nf", True),
        ("a nota fiscal do produto nao chegou junto com o mesmo, favor me enviar ela", True),
        ("não veio a nota fiscal junto com o produto. se puder enviar por aqui, por favor", True),
        ("a nota fiscal não veio com o relógio", True),
        ("o relógio não acompanhou a nota fiscal", True),
        ("manda a nota pra ela", False),
        ("o carregador não veio junto com o produto, manda a nota", False),
        ("o produto não veio junto com a caixa, manda a nota", False),
        ("não veio com o produto, manda a nota", False),
        ("a nota veio com o produto errado", False),
        ("chegou tudo certinho mas não veio a nota e o fone", False),
        ("se puder mandar a nota e o carregador", False),
        ("o produto acompanha nota fiscal?", False),
        ("o relógio não acompanhou o carregador nem a nota", False),
        # Terceira revisão de 30/10: o "comprei" e o "envio da nota" (o "envio do pedido", não).
        ("comprei o celular, e nao veio com nota. preciso que me envie a nota fiscal", True),
        ("me envia a nota fiscal, por favor. aguardo o envio da nota fiscal", True),
        ("comprei dois e só veio uma nota, manda a outra", False),
        ("comprei o celular e nao chegou, manda a nota", False),
        ("aguardo o envio do pedido e me manda a nota fiscal", False),
        ("boa tarde, comprei um relogio e nao veio a nota fiscal, pode me mandar?", True),
        # O "comprei" solto é o produto sem trava: fica fora da lista.
        ("nao veio o que comprei so a nota fiscal", False),
        ("o celular que comprei nao veio so a nota fiscal", False),
        ("quero o que comprei, me manda a nota fiscal", False),
        ("e o que eu comprei? manda a nota fiscal", False),
        ("comprei e nao veio so a nota fiscal", False),
        # O produto depois do "e do/da" também é o produto pedido.
        ("preciso da nota fiscal e do produto", False),
        ("preciso do envio da nota fiscal e do produto", False),
        ("quero o envio da nota fiscal e do pedido", False),
        ("preciso do envio da nota fiscal", True),
        # O "comprei" só some quando a nota é o que não veio; "<produto> que comprei" é o produto.
        ("comprei e não veio a nota fiscal, me manda por favor", True),
        ("comprei o produto mas não veio a nota fiscal, pode me enviar?", True),
        ("manda a nota fiscal do celular que comprei", True),
        ("me manda a nota fiscal do que eu comprei", True),
        ("comprei um relógio e veio um celular, manda a nota fiscal", False),
        ("comprei o celular e so veio a caixa, manda a nota fiscal", False),
        ("comprei o celular e ainda nao veio nem a nota fiscal", False),
        ("comprei o celular de novo, manda a nota fiscal", False),
        # O "comprei" antes do pedido escrito fica fora (amplificava as outras brechas).
        ("comprei o celular, pode me mandar a nota fiscal?", False),
        ("bom dia comprei um celular e queria a nota fiscal", False),
        ("comprei, me manda a nota fiscal, veio a caixa só", False),
        # A caixa em qualquer ordem; joia/top depois do "e"; o trecho que é só o produto.
        ("chegou a caixa só, manda a nota fiscal", False),
        ("só tinha a caixa, manda a nota fiscal", False),
        ("veio só um pacote, manda a nota fiscal", False),
        ("e a joia? manda a nota fiscal", False),
        ("manda a nota fiscal e o top", False),
        ("manda a nota fiscal - o pedido?", False),
        ("manda a nota fiscal. o pedido foi?", False),
        ("e o meu ainda? manda a nota fiscal", False),
        ("me envia a nota fiscal junto com o produto", False),
        ("preciso da nota fiscal e ainda do produto", False),
        ("vocês vão conseguir enviar a nota fiscal por aqui?", True),
        ("o celular que comprei veio so a caixa manda a nota fiscal", False),
        ("comprei e nao veio a nota, veio so a caixa", False),
        ("o celular so veio a caixa, manda a nota fiscal", False),
        ("e o meu? manda a nota fiscal", False),
        ("manda a nota fiscal, o pedido?", False),
        ("o meu não veio a nota fiscal, manda por favor", True),
        ("não veio o que comprei e não veio a nota, manda a nota", False),
        # O "&" e o "+" são o "e"; "e desse produto" também é o produto.
        ("preciso da nota fiscal & do produto", False),
        ("manda a nota fiscal + o produto", False),
        ("preciso da nota fiscal e desse produto", False),
    ],
)
def test_pedido_claro(texto, claro):
    assert nf_automatica.pedido_claro([texto]) is claro


def test_pedido_claro_em_duas_falas():
    """O turno junta as falas: o produto que não veio na de antes segura a NF."""
    assert nf_automatica.pedido_claro(["oi", "não veio a nota fiscal"]) is True
    assert nf_automatica.pedido_claro(["o produto nem veio", "cadê a nota fiscal?"]) is False
    assert nf_automatica.pedido_claro(["recebi a caixa mas nada do produto", "manda a nf"]) is False
    # Segunda revisão de 29/10: cada fala é um trecho.
    assert nf_automatica.pedido_claro(["Não veio a nota", "O produto não"]) is False
    assert nf_automatica.pedido_claro(["Ainda não chegou", "Manda a nota fiscal pfv"]) is False
    assert nf_automatica.pedido_claro(["nao veio a nota e", "o produto"]) is False
    assert (
        nf_automatica.pedido_claro(
            ["chegou o pedido mas a nota nao e minha", "manda a minha nota pfv"]
        )
        is False
    )
    assert (
        nf_automatica.pedido_claro(
            ["boa tarde", "recebi a caixa mas nao veio a nota e o produto", "pfv"]
        )
        is False
    )
    assert nf_automatica.pedido_claro(["boa tarde", "não veio a nota fiscal", "pode mandar?"])
    # Revisão de 30/10: o documento sozinho e o aviso da fala apagada da Shopee.
    assert nf_automatica.pedido_claro(["boa noite", "a notafiscal?"]) is True
    assert nf_automatica.pedido_claro(["oi", "nota fiscal", "é original?"]) is False
    assert (
        nf_automatica.pedido_claro(
            ["bom dia", "This message was deleted", "o pedido chegou", "mas não veio a nota fiscal"]
        )
        is True
    )
    assert (
        nf_automatica.pedido_claro(["This message was deleted", "o celular não veio, manda a nota"])
        is False
    )


def test_o_2_do_menu_sai_das_falas_do_turno():
    """Revisão de 30/10: o "2" que escolheu "Nota fiscal" não é palavra fora da lista."""
    from types import SimpleNamespace as M

    pede = M(id=1, autor="cliente", texto="por favor, me envie a nota fiscal do produto")
    menu = M(id=2, autor="loja", texto=_MENU_DUOKE, origem="externo")
    dois = M(id=3, autor="cliente", texto="2")
    de_novo = M(id=4, autor="cliente", texto="por favor, me envie a nota fiscal por aqui")
    recentes = [pede, menu, dois, de_novo]
    textos = nf_automatica.textos_do_turno(recentes, [pede, dois, de_novo])
    assert textos == [pede.texto, de_novo.texto]
    assert nf_automatica.pedido_claro(textos) is True
    # Sem as falas do turno, o "2" sozinho é com o `pedido_pelo_menu`.
    assert nf_automatica.textos_do_turno(recentes, [dois]) == []
    # O "2" sem o menu, os outros números do menu e o menu antes da resposta real ficam.
    assert nf_automatica.textos_do_turno([pede, dois], [pede, dois]) == [pede.texto, "2"]
    tres = M(id=3, autor="cliente", texto="3")
    assert nf_automatica.textos_do_turno([pede, menu, tres], [pede, tres]) == [pede.texto, "3"]
    loja = M(id=5, autor="loja", texto="Olá! Já verificamos o seu pedido.", origem="externo")
    assert nf_automatica.textos_do_turno([menu, loja, dois], [dois]) == ["2"]
    # O que veio antes do menu passa pela mesma régua.
    vem = M(id=1, autor="cliente", texto="vem com nota fiscal?")
    textos = nf_automatica.textos_do_turno([vem, menu, dois, de_novo], [vem, dois, de_novo])
    assert nf_automatica.pedido_claro(textos) is False


def test_o_2_que_responde_a_loja_ou_vem_com_foto_fica_com_a_pessoa():
    """Terceira revisão de 30/10: o "2" depois da resposta real da loja responde a ela quando o
    cliente escreveu entre o menu e a resposta ou ela tem o próprio número; e a foto junto do "2"
    não é lida."""
    from types import SimpleNamespace as M

    def cliente(i, t, **k):
        return M(id=i, autor="cliente", texto=t, origem="externo", payload={}, **k)

    def loja(i, t):
        return M(id=i, autor="loja", texto=t, origem="externo", payload={})

    menu, dois = loja(2, _MENU_DUOKE), cliente(9, "2")
    oi = cliente(1, "oi")
    faltou = cliente(3, "veio faltando um item")
    quantos = loja(4, "Olá! Quantos itens chegaram para você?")
    assert nf_automatica.pedido_pelo_menu([oi, menu, faltou, quantos, dois], dois) is False
    opcoes = loja(4, "Prefere 1 - aguardar ou 2 - cancelar?")
    assert nf_automatica.pedido_pelo_menu([oi, menu, opcoes, dois], dois) is False
    # A saudação da equipe (sem número, sem fala do cliente no meio) não muda o "2".
    ola = loja(4, "Olá! Em que posso ajudar?")
    assert nf_automatica.pedido_pelo_menu([oi, menu, ola, dois], dois) is True
    foto = cliente(10, None, tipo="imagem")
    assert nf_automatica.pedido_pelo_menu([oi, menu, dois, foto], foto) is False
    assert nf_automatica.pedido_pelo_menu([oi, menu, foto, dois], dois) is False
    assert nf_automatica.pedido_pelo_menu([foto, menu, dois], dois) is False
    # O cartão do pedido/produto não é a mídia: o pedido de que se fala.
    cartao = cliente(11, None, tipo="pedido")
    assert nf_automatica.pedido_pelo_menu([oi, menu, cartao, dois], dois) is True
    # A loja pergunta o número ou a escolha: o "2" responde a ela.
    quantas = loja(4, "Quantas unidades vieram com defeito?")
    assert nf_automatica.pedido_pelo_menu([oi, menu, quantas, dois], dois) is False
    for pergunta in ("Pode me informar a quantidade de itens?", "Em que dia chegou?", "Qtd?"):
        p = loja(4, pergunta)
        assert nf_automatica.pedido_pelo_menu([oi, menu, p, dois], dois) is False, pergunta
    # A foto com legenda (Shopee "image_with_text") e o áudio também são mídia.
    legenda = M(
        id=12,
        autor="cliente",
        texto="olha isso",
        origem="externo",
        tipo="outro",
        payload={"message_type": "image_with_text"},
    )
    assert nf_automatica.pedido_pelo_menu([oi, menu, legenda, dois], dois) is False
    audio = cliente(13, None, tipo="audio")
    assert nf_automatica.pedido_pelo_menu([oi, menu, dois, audio], audio) is False
    # O "sim" que responde a pergunta da loja antes do menu não é a cortesia.
    cancelar = loja(0, "Deseja cancelar o pedido?")
    sim = cliente(1, "sim")
    assert nf_automatica.pedido_pelo_menu([cancelar, sim, menu, dois], dois) is False
    pede = cliente(1, "oi, pode me mandar a nota fiscal?")
    assert nf_automatica.pedido_pelo_menu([cancelar, pede, menu, dois], dois) is True


def test_o_2_de_um_menu_anterior_e_a_mesma_escolha():
    from types import SimpleNamespace as M

    def cliente(i, t):
        return M(id=i, autor="cliente", texto=t, origem="externo", payload={})

    def loja(i, t):
        return M(id=i, autor="loja", texto=t, origem="externo", payload={})

    menu1, menu2 = loja(1, _MENU_DUOKE), loja(3, _MENU_DUOKE)
    um, dois = cliente(2, "2"), cliente(4, "2")
    assert nf_automatica.pedido_pelo_menu([menu1, um, menu2, dois], dois) is True
    # O "2" que respondeu a pergunta da loja antes do menu não é a escolha da NF.
    faltou = cliente(0, "veio faltando item")
    quantos = loja(1, "Quantos faltaram?")
    assert nf_automatica.pedido_pelo_menu([faltou, quantos, um, menu2, dois], dois) is False


def test_a_fala_que_e_so_o_produto_pede_o_produto():
    assert nf_automatica.pedido_claro(["manda a nota fiscal", "o pedido?"]) is False
    assert nf_automatica.pedido_claro(["preciso da nota fiscal", "e o produto também"]) is False
    assert nf_automatica.pedido_claro(["preciso da nota fiscal", "do pedido"]) is True


def test_figurinha_e_conversar_com_vendedor_nao_contam_como_fala():
    """Revisão da revisão de 30/10: a figurinha e o botão do FAQ da Shopee não são fala."""
    from types import SimpleNamespace as M

    menu = M(id=90, autor="loja", texto=_MENU_DUOKE, origem="externo")
    dois = M(id=91, autor="cliente", texto="2")
    figurinha = M(id=1, autor="cliente", texto="[Figurinha]", payload={"message_type": "sticker"})
    vendedor = M(
        id=2,
        autor="cliente",
        texto="Conversar com vendedor",
        payload={"message_type": "faq_liveagent"},
    )
    assert nf_automatica.pedido_pelo_menu([figurinha, menu, dois], dois) is True
    assert nf_automatica.pedido_pelo_menu([vendedor, menu, dois], dois) is True
    depois = M(id=95, autor="cliente", texto="[Figurinha]")
    assert nf_automatica.pedido_pelo_menu([menu, dois, depois], depois) is True
    # Terceira revisão de 30/10: o "2" que a loja já respondeu não vale de novo para a figurinha
    # ou o botão do FAQ que vêm depois (é outro turno).
    resposta = M(id=96, autor="loja", texto="Segue em anexo a sua nota fiscal", origem="externo")
    tarde = M(id=97, autor="cliente", texto="[Figurinha]", payload={"message_type": "sticker"})
    assert nf_automatica.pedido_pelo_menu([menu, dois, resposta, tarde], tarde) is False
    botao = M(
        id=98,
        autor="cliente",
        texto="Conversar com Vendedor",
        payload={"message_type": "faq_liveagent"},
    )
    assert nf_automatica.pedido_pelo_menu([menu, dois, resposta, botao], botao) is False
    # Escrito pelo cliente (sem o botão do FAQ) e o "Falar com atendente" continuam contando.
    digitado = M(id=3, autor="cliente", texto="conversar com vendedor")
    assert nf_automatica.pedido_pelo_menu([digitado, menu, dois], dois) is False
    atendente = M(id=4, autor="cliente", texto="Falar com atendente", payload={})
    assert nf_automatica.pedido_pelo_menu([atendente, menu, dois], dois) is False
    # No turno: o pedido escrito depois do "2" passa (o Vita 95704e8d de 29/10); o acessório
    # que não veio continua com a pessoa.
    celular = M(id=92, autor="cliente", texto="O meu celular não veio com nota fiscal")
    textos = nf_automatica.textos_do_turno(
        [vendedor, menu, dois, celular], [vendedor, dois, celular]
    )
    assert textos == [celular.texto] and nf_automatica.pedido_claro(textos) is True
    nota = M(id=93, autor="cliente", texto="nota fiscal")
    textos = nf_automatica.textos_do_turno([figurinha, menu, dois, nota], [figurinha, dois, nota])
    assert textos == [nota.texto] and nf_automatica.pedido_claro(textos) is True
    carregador = M(id=94, autor="cliente", texto="não veio o carregador nem a nota")
    textos = nf_automatica.textos_do_turno(
        [figurinha, menu, dois, carregador], [figurinha, dois, carregador]
    )
    assert nf_automatica.pedido_claro(textos) is False


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


@pytest.mark.parametrize(
    ("antes", "pelo_menu"),
    [
        ([], True),
        (["oi"], True),
        (["boa tarde, tudo bem?", "por favor"], True),
        (["não veio a nota fiscal"], True),
        # O "2" sem nada da loja antes dele é a escolha de um menu de antes (o que respondeu a
        # pergunta da loja não: test_o_2_de_um_menu_anterior_e_a_mesma_escolha); o "6" segura.
        (["oi", "2"], True),
        (["6"], False),
        # Segunda revisão de 29/10: o problema escrito ANTES do menu, na mesma rajada.
        (["Recebi a nota de outra pessoa, com outro nome"], False),
        (["essa nota que veio na caixa nao e minha"], False),
        (["o pedido chegou mas nao veio a nota e um produto"], False),
        (["comprei 2, chegou so 1"], False),
        (["oi", "o produto veio quebrado"], False),
        # Revisão da revisão de 30/10: o elogio do que chegou vale como a cortesia; com o
        # problema ou outra pergunta, não.
        (["oi! tudo bem e vc?", "veio tudo certinho, muito obrigada!"], True),
        (["bom dia! chegou tudo certinho, amei o aparelho muito bom"], True),
        (["opa, tudo joia?"], True),
        (["chegou tudo certo mas o fone não"], False),
        (["veio tudo certinho, só faltou o carregador"], False),
        (["chegou tudo certinho", "é original?"], False),
        # Terceira revisão de 30/10: a pergunta de como receber a NF, com o "2" logo depois, é o
        # pedido; a pergunta da compra e a que traz outro assunto, não.
        (["oi bom dia ! a nota fiscal vocês envia por aqui ?"], True),
        (["queria saber se vocês enviam a nota fiscal"], True),
        (["preciso da nota fiscal, onde ela está disponível?"], True),
        (["Favor enviasse a NF"], True),
        (["vem com nota fiscal?"], False),
        (["acompanha nota fiscal?"], False),
        (["gostaria de saber se tem nota fiscal"], False),
        (["vocês emitem nota fiscal?"], False),
        (["a nf será emitida no meu cpf?"], False),
        (["vocês enviam a nota fiscal no email?"], False),
        (["vocês enviam a nota fiscal e a garantia?"], False),
        (["onde está o meu pedido? e a nota fiscal?"], False),
        (["vocês enviam a nota fiscal do outro pedido?"], False),
        (["vocês enviam a nota fiscal?", "e é original?"], False),
        (["a nota fiscal já está disponível?"], True),
        (["a nota fiscal?", "boa tarde", "poderia me passar aqui"], True),
        # A chegada, a posse, o estoque e o pedido junto não são a pergunta da NF; e a pergunta
        # da entrega ou do produto não é o elogio.
        (["veio so a nota fiscal"], False),
        (["recebi só a nota fiscal"], False),
        (["chegou so a nota fiscal", "e o produto?"], False),
        (["queria saber do meu pedido e da nota fiscal"], False),
        (["e o meu pedido?", "e a nota fiscal?"], False),
        (["esta disponivel? voces enviam a nota fiscal?"], False),
        (["ainda tem? voces enviam nota fiscal?"], False),
        (["o meu pedido chegou?"], False),
        (["veio o relógio?"], False),
        (["o celular?"], False),
        (["oi! tudo bem e vc?"], True),
        # A chegada sem pergunta é o elogio; com a entrega pedida junto ou o "muito tarde", não.
        # A chegada sozinha não é o elogio (pode ser a queixa: "chegou 😡").
        (["chegou, obrigada"], False),
        (["Recebi o produto, tudo certo"], False),
        (["Chegou! A nota fiscal vocês enviam por aqui?"], False),
        (["chegou 😡"], False),
        (["veio tudo certinho, só veio a caixa"], False),
        (["onde está a nota fiscal?"], True),
        (["onde consigo ver a nota fiscal?"], True),
        (["vocês enviam nota fiscal? ela está disponível?"], False),
        (["está disponível a nota fiscal?"], False),
        (["show, e o meu"], False),
        (["lindo, o pedido foi"], False),
        (["2"], True),
        (["chegou hoje, muito tarde"], False),
        (["show, e o pedido"], False),
        (["otimo, aguardo o meu pedido"], False),
        # O "ela" e o "disponível" só da nota; o "meu" sem o nome; a posse.
        (["a nota fiscal já foi emitida?"], True),
        (["como tenho acesso a nota fiscal?"], True),
        (["onde ela esta? e a nota fiscal?"], False),
        (["onde ela esta", "e a nota fiscal"], False),
        (["e o meu? e a nota fiscal?"], False),
        (["ela esta disponivel? voces enviam nota fiscal?"], False),
        (["eu tenho a nota fiscal"], False),
    ],
)
def test_pedido_pelo_menu_olha_o_que_veio_antes_do_menu(antes, pelo_menu):
    from types import SimpleNamespace as M

    falas = [M(id=i, autor="cliente", texto=t) for i, t in enumerate(antes)]
    menu = M(id=90, autor="loja", texto="Escolha: 1 - Rastreio 2 - Nota fiscal 6 - Atendente")
    dois = M(id=91, autor="cliente", texto="2")
    assert nf_automatica.pedido_pelo_menu([*falas, menu, dois], dois) is pelo_menu
    # A resposta real da loja antes corta a rajada: o que veio antes dela não conta.
    loja = M(id=89, autor="loja", texto="Olá! Já verificamos o seu pedido.", origem="externo")
    assert nf_automatica.pedido_pelo_menu([*falas, loja, menu, dois], dois) is True


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


async def test_2_do_menu_com_o_problema_escrito_antes_fica_com_a_pessoa(
    db, make_user, ligado, plataforma
):
    """Segunda revisão de 29/10: o menu sai logo depois da 1ª fala — ela também conta."""
    conversa, _ = await _cenario(
        db, make_user, texto="Recebi a nota de outra pessoa, com outro nome"
    )
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
    assert _decisao(r) == "nf:pedido_nao_claro" and plataforma == []
