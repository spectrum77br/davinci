"""O prazo da garantia como a equipe diz, POR MODELO (16/11/2026): `agente.garantia_equipe`.

O Eduardo: "diz como a equipe diz; se está no banco, está certo, ele busca lá e envia". A equipe
dá o prazo por modelo (A18 Pro Max 1 ano: 90 dias pela loja e 9 meses pela marca; A17 Pro Max 90
dias) e se contradiz noutros (F112: ora 90 dias, ora 1 ano) — o prazo só sai quando ela concorda.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.atendimento import constantes, ia, validador
from app.services.atendimento.agente import garantia_equipe as g
from tests.test_atendimento_ia import _conversa, _msg


@pytest.mark.parametrize(
    ("texto", "classe"),
    [
        ("90 dias de garantia pela loja e 9 meses pela marca completando 1 ano!", g.UM_ANO),
        ("tem garantia de 90 dia pela loja e 9 meses pela marca uranyx!", g.UM_ANO),
        ("o modelo a18 pro max uranyx tem 1 ano de garantia!", g.UM_ANO),
        ("sim com garantia 90 dias pela loja e 9 meses pela uranyx!", g.UM_ANO),
        ("o modelo a17 pro max tem somente 90 dias de garantia!", g.NOVENTA_DIAS),
        ("sim o aparelho tem garantia de 90dias!", g.NOVENTA_DIAS),
        # o "1 ano" do pedido não é o prazo da garantia
        ("garantia de 90 dias seu pedido tem mais de 1 ano!", g.NOVENTA_DIAS),
        # a Apple tem o fato aprovado dela
        ("sim tem 1 ano de garantia apple com nota fiscal!", None),
        ("o aparelho tem garantia!", None),
        ("chega em 90 dias", None),
    ],
)
def test_a_classe_do_prazo_como_a_equipe_escreve(texto, classe):
    assert g.classe_do_prazo(texto) == classe


def test_o_modelo_da_resposta_e_o_da_compra_sao_um_so():
    assert g.modelo_da_resposta("o wp60 tem garantia de 90 dias!", None) == "WP60"
    titulo_wp60 = "Uranyx WP60 5G 24/256 ou 36/512 GB Ram NFC"
    assert g.modelo_da_resposta("tem 90 dias de garantia!", titulo_wp60) == "WP60"
    # dois modelos no texto, ou no título sem código no texto: de nenhum
    dois = "foi comprado o modelo a17 pro max somente o a18 pro max tem 1 ano de garantia!"
    assert g.modelo_da_resposta(dois, None) is None
    titulo_misto = "Uranyx A17 Pro Max 12.64 GB Memoria / Uranyx S5 128.16 Ram NFC"
    assert g.modelo_da_resposta("tem 90 dias de garantia!", titulo_misto) is None
    assert g.modelo_da_resposta("tem 90 dias de garantia!", None) is None
    a17 = ["Hotwav A17 Pro Max 12.128 - Preto", "Hotwav A17 Pro Max 12.64 - Branco"]
    assert g.modelo_unico(a17) == "A17"
    assert g.modelo_unico(["Uranyx A18 Pro Max 16.128 - Azul", "Hotwav A17 Pro Max 12.64"]) is None
    assert g.modelo_unico(["Celular Uranyx A18 Pro Max S5"]) is None
    assert g.modelo_unico([None, ""]) is None


def test_o_prazo_do_modelo_so_quando_a_equipe_concorda():
    # Da mais nova para a mais velha.
    respostas = [
        ("o modelo a18 pro max uranyx tem 1 ano de garantia!", None),
        ("90 dias de garantia pela loja e 9 meses pela marca!", "Uranyx A18 Pro Max 16.128 NFC"),
        ("tem 90 dias de garantia!", "Hotwav A17 Pro Max 12.128"),
        ("o modelo a17 pro max tem somente 90 dias de garantia!", None),
        ("90 dias de garantia!", "Uranyx F112 Pro 5g 256 Gb Memoria 24gb NFC"),
        ("90 dias pela loja e 9 meses pela marca!", "Uranyx F112 Pro 5g 256 Gb Memoria 24gb NFC"),
        ("tem 90 dias de garantia!", "Uranyx S7 16.128 GB ou 32.256 GB"),  # uma resposta só
        # a mala tem os códigos dela (o P1 da Charlots): não conta para o aparelho
        ("a mala P1 tem 1 ano de garantia!", None),
        ("sim garantia de 90 dias!", "Mala Brilho Listrada P1 tamanho 14.20"),
    ]
    assert g.prazos_por_modelo(respostas) == {"A18": g.UM_ANO, "A17": g.NOVENTA_DIAS}
    # As últimas respostas decidem: a equipe que passou a dizer outra coisa.
    novas = [("o wp60 tem 1 ano de garantia!", None)] * g.ULTIMAS_DO_MODELO
    velhas = [("o wp60 tem garantia de 90 dias!", None)] * 5
    assert g.prazos_por_modelo([*novas, *velhas]) == {"WP60": g.UM_ANO}
    assert g.prazos_por_modelo([novas[0], *velhas]) == {}


def _barra(texto: str, regra: dict | None) -> list[str]:
    prazos = validador.prazos_da_regra([{"regra": regra}]) if regra else frozenset()
    final = validador.normalizar(texto, plataforma="shopee", canal="chat")
    return validador.validar(
        final, plataforma="shopee", canal="chat", origem=ia.ORIGEM_IA, prazos_de_garantia=prazos
    )


def test_a_regra_com_o_prazo_libera_so_o_prazo_do_modelo():
    base = {"a_partir_de": "entrega", "nunca": "90 dias, 1 ano, 9 meses"}
    assert g.com_o_prazo(base, None, "A18") == base
    assert g.com_o_prazo(base, g.UM_ANO, None) == base
    um = g.com_o_prazo(base, g.UM_ANO, "A18")
    assert (um["garantia_anos"], um["loja_dias"], um["uranyx_meses"]) == (1, 90, 9)
    assert um["prazo_como_a_equipe_diz"] == (
        "«90 dias de garantia pela loja e 9 meses pela marca, completando 1 ano» "
        "(o A18, a partir da entrega)"
    )
    assert um["nunca"] == g.NUNCA_COM_O_PRAZO
    noventa = g.com_o_prazo(base, g.NOVENTA_DIAS, "A17")
    assert noventa["dias"] == 90 and "garantia_anos" not in noventa

    frase_a18 = (
        "O A18 Pro Max tem 90 dias de garantia pela loja e 9 meses pela marca, completando 1 ano."
    )
    assert _barra(frase_a18, um) == []
    assert _barra("Tem 1 ano de garantia: 90 dias pela loja e 9 meses pela marca.", um) == []
    assert _barra("O A18 tem 90 dias de garantia.", um)  # o do A18 é 1 ano
    assert _barra("O A17 Pro Max tem 90 dias de garantia, a partir da entrega.", noventa) == []
    assert _barra("O A17 Pro Max tem 1 ano de garantia.", noventa)
    assert _barra("O A17 tem 90 dias pela loja e 9 meses pela marca.", noventa)
    assert _barra("O A17 Pro Max tem 90 dias de garantia.", None)  # sem o prazo da equipe
    assert validador.MOTIVO_PRAZO in _barra("Chega em 90 dias.", noventa)


async def test_os_prazos_vem_do_que_a_equipe_enviou(db: AsyncSession, make_user):
    async def conversa(titulo: str, externo: str):
        c = await _conversa(db, make_user, pedido=None, externo_id=externo)
        c.anuncio_titulo = titulo
        await db.commit()
        return c

    a18 = await conversa("Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium", "c-a18")
    a17 = await conversa("Uranyx Hotwav A17 Pro Max 64/128 Gb Memoria 12Gb", "c-a17")
    f112 = await conversa("Uranyx F112 Pro 5g 256 Gb Memoria 24gb NFC", "c-f112")
    humano = {"autor": "loja", "origem": constantes.ORIGEM_HUMANO}
    await _msg(
        db,
        a18,
        "90 dias de garantia pela loja e 9 meses pela marca!",
        ha=timedelta(hours=3),
        **humano,
    )
    await _msg(db, a18, "sim tem 1 ano de garantia!", ha=timedelta(hours=4), **humano)
    await _msg(db, a17, "tem 90 dias de garantia!", ha=timedelta(hours=3), **humano)
    await _msg(db, a17, "sim o aparelho tem garantia de 90 dias!", ha=timedelta(days=2), **humano)
    # Não contam: o que não saiu da equipe pelo DaVinci (o robô, o cliente) e o modelo em que a
    # equipe se contradiz.
    await _msg(
        db, a17, "tem 1 ano de garantia!", ha=timedelta(hours=5), autor="loja", origem="externo"
    )
    await _msg(db, a17, "tem 1 ano de garantia?", ha=timedelta(hours=5))
    await _msg(db, f112, "90 dias de garantia!", ha=timedelta(hours=3), **humano)
    await _msg(db, f112, "90 dias pela loja e 9 meses pela marca!", ha=timedelta(hours=6), **humano)

    agora = datetime.now(UTC)
    assert await g.prazos_da_equipe(db, as_of=agora) == {"A18": g.UM_ANO, "A17": g.NOVENTA_DIAS}
    # O teste de uma conversa antiga só vê o que a equipe já tinha dito.
    assert await g.prazos_da_equipe(db, as_of=agora - timedelta(days=1)) == {}

    base = {"a_partir_de": "entrega"}
    regra = await g.regra_com_o_prazo(db, base, ["Hotwav A17 Pro Max 12.128 - Preto"], as_of=agora)
    assert regra["dias"] == 90 and "(o A17," in regra["prazo_como_a_equipe_diz"]
    # Dois modelos no nome do item: a regra como veio.
    assert (
        await g.regra_com_o_prazo(db, base, ["Celular Uranyx A18 Pro Max S5"], as_of=agora) == base
    )


def test_o_pacote_traz_a_garantia_quando_o_turno_fala_dela():
    """Na virada, com "garantia" no turno, o `ver_garantia` vem pronto no pacote (no teste de
    16/11 o agente não o chamou nos 4 casos de garantia e respondeu sem a regra)."""
    from types import SimpleNamespace

    from app.services.atendimento.agente import pacote

    turno = SimpleNamespace(textos_rajada=["Qual a GARANTIA desse celular?"], textos_cliente=[])
    assert pacote._fala_de_garantia(turno)
    outro = SimpleNamespace(textos_rajada=["vem carregador?"], textos_cliente=["oi", None])
    assert not pacote._fala_de_garantia(outro)
