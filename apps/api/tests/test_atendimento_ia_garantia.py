# ruff: noqa: S105, F811 — chaves de teste de um provedor falso; fixtures importadas dos testes da IA
"""A IA e a garantia (07/10/2026) — e o aviso do provedor sem crédito.

Combinado com o dono: "no Comunicador, quando perguntarem de validade, a IA
vai puxar ali na tabela e dizer se tem validade ou não". Sem agente: o
DaVinci consulta o Painel de Garantia e entrega o bloco pronto; o modelo
(FALSO em todos os testes — nada sai para provedor nenhum) só redige; o
validador confere que as datas da resposta são as do bloco.

- `contexto.garantia_para_ia`: acha pelo pedido da conversa, pelo CPF do
  pedido e pelo pedido/NF que o comprador citou; NUNCA a garantia de outro
  comprador por número que se chuta — de outro CPF, só o pedido da própria
  conversa e o nº LONGO de marketplace citado; sem o CPF da conversa, NF,
  nº curto do Bling e CPF digitado não acham nada; o bloco não leva CPF nem
  nome;
- `validador.conferir_garantia`: data de garantia fora do bloco reprova,
  com a redação de verdade (07/01/2027, "até 7 de janeiro", "janeiro de
  2027"...); "está coberto" sem garantia ativa reprova;
- a IA com o bloco nos FATOS e a regra no prompt; garantia segue só de pessoa;
- provedor sem crédito (OpenAI `insufficient_quota`, 402, Anthropic "credit
  balance is too low"): definitivo, sem nova tentativa, sem rascunho, a
  frase certa na tela; o limite por minuto continua com a frase de antes.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import httpx2
import pytest
import respx
import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingNota, BlingNotaEmitida, BlingOrder, Garantia, IntegrationPlatform
from app.services import garantia as garantia_svc
from app.services.atendimento import contexto, ia, validador
from app.services.atendimento.constantes import (
    ORIGEM_IA,
    RASCUNHO_BLOQUEADO,
    RASCUNHO_PENDENTE,
)
from tests.test_atendimento_ia import PEDIDO_BLING as PEDIDO_BLING_IA
from tests.test_atendimento_ia import (  # noqa: F401 — fixtures
    PEDIDO_MKT,
    RASTREIO,
    _cenario_auto,
    _conversa,
    _msg,
    _pedido_completo,
    _rascunhos,
    _saida,
    envios,
    esperas,
    ia_ligada,
    modelo,
)
from tests.test_atendimento_ia_claude import api, claude, sem_groq  # noqa: F401 — fixtures

HOJE = date(2026, 10, 8)
CPF = "52998224725"
CPF_OUTRO = "11144477735"
NOME = "João da Silva"
PEDIDO_BLING = "300001"

ATIVA = {
    "status": "ativa",
    "inicio": "2026-10-07",
    "fim_hardware": "2027-01-07",
    "fim_software": "2027-10-07",
}
SO_SOFTWARE = {
    "status": "somente_software",
    "inicio": date(2026, 6, 1),
    "fim_hardware": date(2026, 9, 1),
    "fim_software": date(2027, 6, 1),
}


@pytest.fixture(autouse=True)
def _hoje(monkeypatch):
    monkeypatch.setattr(garantia_svc, "hoje", lambda: HOJE)


@pytest.fixture(autouse=True)
def _sem_limites():
    ia.esquecer_limites()
    yield
    ia.esquecer_limites()


def _conferir(texto: str, garantias: list[dict] | None, **kw) -> list[str]:
    return validador.conferir_garantia(texto, garantias, **kw)


# ═══════════════ validador: datas de garantia (redação de verdade) ═══════════════


@pytest.mark.parametrize(
    "texto",
    [
        "Sua garantia de hardware vai até 07/01/2027 e a de software até 07/10/2027.",
        "A garantia de hardware vale até 7/1/27.",
        "A garantia de hardware vale até 07-01-2027.",
        "Sua garantia de hardware vai até 7 de janeiro e a de software até outubro de 2027.",
        "A garantia de hardware vale até janeiro de 2027.",
        "O hardware fica coberto até jan/2027 e o software até 10/2027.",
        "A garantia começou na entrega, em 07/10/2026, e o software vai até 07/10/2027.",
        "Sua garantia está ativa até o dia 07/01/2027.",
        "A garantia de software vale até 7 de outubro de 2027.",
        "A garantia de software termina em 2027.",
        "Sua garantia começou em 7 de outubro de 2026 (entrega).",
        # ISO, dia por extenso, intervalo e comparação.
        "A garantia de hardware vai até 2027-01-07.",
        "A garantia de hardware vai até sete de janeiro de 2027.",
        "Sua garantia de hardware vale de 07/10/2026 até 07/01/2027.",
        "A garantia de software é de 07/10/2026 a 07/10/2027.",
        "A garantia de software dura mais que a de hardware: vai até 07/10/2027.",
    ],
)
def test_datas_do_bloco_passam(texto):
    motivos = _conferir(texto, [ATIVA])
    assert not any(m.startswith(validador.MOTIVO_DATA_GARANTIA) for m in motivos), motivos


@pytest.mark.parametrize(
    "texto,trecho",
    [
        ("A garantia de hardware vale até 08/01/2027.", "08/01/2027"),
        ("A garantia vai até 7 de fevereiro de 2027.", "7 de fevereiro de 2027"),
        ("Sua garantia de hardware vai até 7 de fevereiro.", "7 de fevereiro"),
        ("A garantia de hardware vale até fevereiro de 2027.", "fevereiro de 2027"),
        ("A garantia é válida até 07/01/2028.", "07/01/2028"),
        ("Sua garantia segue valendo até o dia 10.", "dia 10"),
        ("Seu aparelho tem cobertura até 2028.", "2028"),
        # ISO confere dia e mês (não só o ano); o dia por extenso também.
        ("A garantia de hardware vai até 2027-01-08.", "2027-01-08"),
        ("A garantia de hardware vai até oito de janeiro de 2027.", "oito de janeiro de 2027"),
        # A frase seguinte, sem a palavra "garantia", também é conferida.
        ("Sua garantia está ativa. Ela vai até 07/02/2027.", "07/02/2027"),
    ],
)
def test_data_fora_do_bloco_e_inventada(texto, trecho):
    motivos = _conferir(texto, [ATIVA])
    assert f"{validador.MOTIVO_DATA_GARANTIA} ({trecho})" in motivos
    assert validador.so_da_ia(motivos)


def test_data_das_lacunas_vale_fora_da_frase_de_garantia_e_nao_dentro():
    # O envio ({data_envio} preenchida) numa frase; a garantia em outra.
    ok = "Seu pedido foi enviado em 01/10/2026. A garantia conta a partir da entrega."
    assert _conferir(ok, [ATIVA], outras_datas=("01/10/2026",)) == []
    # A data do envio como fim da garantia: inventada.
    errado = "A garantia de hardware vai até 01/10/2026."
    assert _conferir(errado, [ATIVA], outras_datas=("01/10/2026",)) == [
        f"{validador.MOTIVO_DATA_GARANTIA} (01/10/2026)"
    ]


def test_sem_bloco_nenhuma_data_de_garantia_e_resposta_sem_garantia_nao_e_conferida():
    assert _conferir("A garantia vai até 07/01/2027.", None) == [
        f"{validador.MOTIVO_DATA_GARANTIA} (07/01/2027)"
    ]
    assert _conferir("A garantia vai até 07/01/2027.", []) == [
        f"{validador.MOTIVO_DATA_GARANTIA} (07/01/2027)"
    ]
    # Sem frase de garantia, nada a conferir (o envio tem as regras dele).
    assert _conferir("Seu pedido foi enviado em 01/10/2026.", None) == []
    assert _conferir("Seu pedido foi enviado em 01/10/2026.", []) == []


def test_aguardando_entrega_nao_tem_data_para_citar():
    aguardando = {"status": "aguardando_entrega", "inicio": None}
    assert _conferir("Sua garantia vai até 07/01/2027.", [aguardando]) == [
        f"{validador.MOTIVO_DATA_GARANTIA} (07/01/2027)",
    ]


def test_datas_citadas_no_texto():
    plano = validador.plano_de(
        "até 1º de jan. de 2027, 7/1/27, 07-01-2027, 01/2027, em março, dia 10, até 2027, 32/13"
    )
    assert [d for _, d in validador.datas_citadas(plano)] == [
        (1, 1, 2027),
        (7, 1, 2027),
        (7, 1, 2027),
        (None, 1, 2027),
        (None, 3, None),
        (10, None, None),
        (None, None, 2027),
    ]


def test_ate_o_dia_com_data_completa_na_frase_de_garantia_nao_e_prazo_de_entrega():
    def prazo(texto: str) -> bool:
        return "prazo em números só pode vir do sistema" in validador.validar(
            texto, plataforma="shopee", canal="chat", origem=ORIGEM_IA
        )

    assert not prazo("Sua garantia de hardware vai até o dia 07/01/2027.")
    assert not prazo("A garantia vale até o dia 7 de janeiro de 2027.")
    assert not prazo("A garantia do seu pedido vai até o dia 07/01/2027.")
    # Entrega continua prazo; e "até o dia 10" (sem a data inteira) também.
    assert prazo("Seu pedido chega até o dia 07/01/2027.")
    assert prazo("Sua garantia vai até o dia 10.")
    # ...mesmo numa frase que cita a garantia (a do aparelho ou a da
    # plataforma): o assunto do "até o dia" é a entrega.
    assert prazo("Seu pedido chega até o dia 07/01/2027 e está coberto pela Garantia Shopee.")
    assert prazo("Seu pedido chega até o dia 07/01/2027 e a garantia começa na entrega.")
    assert prazo(
        "Seu pedido será entregue até o dia 15/10/2026 e a Compra Garantida cobre qualquer "
        "problema."
    )
    assert prazo("Seu pedido chega até o dia 15/10/2026, com a proteção da Garantia Shopee.")


@pytest.mark.parametrize(
    "texto,trecho",
    [
        # O fim do software dito como do hardware (e o contrário).
        ("A garantia de hardware vai até 07/10/2027.", "07/10/2027"),
        ("Sua garantia de hardware vai até 07/10/2027 e a de software até 07/01/2027.", None),
        # O início dito como fim, e o fim dito como início.
        ("Sua garantia vai até 07/10/2026.", "07/10/2026"),
        ("A garantia termina em 07/10/2026.", "07/10/2026"),
        ("A garantia começou em 07/01/2027.", "07/01/2027"),
        # "da sua compra"/"do pedido" depois de "garantia" não muda o assunto.
        ("A garantia da sua compra vai até 30/09/2026.", "30/09/2026"),
        ("A garantia do pedido: 07/01/2028.", "07/01/2028"),
        # "Hardware e software até X": X tem de ser o fim dos dois.
        ("O hardware e o software estão cobertos até 07/10/2027.", "07/10/2027"),
        ("Hardware e software: garantia até 07/10/2027.", "07/10/2027"),
        # "De X a Y": X é o início, Y o fim (da mesma parte).
        ("A garantia de hardware é de 07/10/2026 a 07/10/2027.", "07/10/2027"),
        ("Sua garantia é válida de 07/01/2027 até 07/10/2027.", "07/01/2027"),
        ("A garantia de hardware vai até 2027-10-07.", "2027-10-07"),
        # A parte escrita DEPOIS da data, com as datas trocadas.
        ("Hardware até 07/10/2027; software até 07/01/2027.", None),
        ("Sua garantia vale até 07/10/2027 para hardware e até 07/01/2027 para software.", None),
        ("Seu aparelho está coberto até 07/10/2027 (hardware) e 07/01/2027 (software).", None),
    ],
)
def test_data_do_bloco_no_lugar_errado_e_inventada(texto, trecho):
    motivos = _conferir(texto, [ATIVA], outras_datas=("30/09/2026",))
    assert motivos and motivos[0].startswith(validador.MOTIVO_DATA_GARANTIA), motivos
    if trecho:
        assert motivos == [f"{validador.MOTIVO_DATA_GARANTIA} ({trecho})"]


@pytest.mark.parametrize(
    "texto",
    [
        "Sua garantia de hardware, que começou em 07/10/2026, vai até 07/01/2027.",
        "A garantia vale a partir de 07/10/2026; o software, até 07/10/2027.",
        "O hardware vale até 07/01/2027, ou seja, 7 de janeiro.",
        "Até 07/01/2027 vale a garantia de hardware.",
        # A parte (hardware/software) é a de depois da data anterior.
        "O hardware vai até 07/01/2027 e a garantia completa, até 07/10/2027.",
        "O hardware vai até 07/01/2027, e até 07/10/2027 o software.",
        # A parte escrita LOGO DEPOIS da data é dela, não da seguinte.
        "Sua garantia vale até 07/01/2027 para hardware e até 07/10/2027 para software.",
        "Seu aparelho está coberto até 07/01/2027 (hardware) e 07/10/2027 (software).",
        "Ele está na garantia até 07/01/2027 (defeitos de hardware) e até 07/10/2027 (software).",
        "Seu aparelho está coberto: 07/01/2027 para hardware e 07/10/2027 para software.",
        "A garantia vale até 07 de janeiro de 2027 para hardware e até 07 de outubro de 2027 "
        "para software.",
        # A entrega colada na data é o assunto dela.
        "A garantia começa a contar a partir da entrega em 07/10/2026.",
    ],
)
def test_data_do_bloco_no_lugar_certo_passa(texto):
    assert _conferir(texto, [ATIVA]) == []


@pytest.mark.parametrize(
    "texto,outras",
    [
        # A proteção da plataforma não é a garantia do aparelho.
        ("Seu pedido está coberto pela Garantia Shopee até a entrega.", ()),
        ("Fique tranquilo, sua compra está coberta pelo Mercado Livre.", ()),
        (
            "A Compra Garantida do Mercado Livre cobre sua compra; a previsão de entrega é "
            "30/09/2026.",
            ("30/09/2026",),
        ),
        # A data do sistema na frase de garantia, quando é de OUTRO assunto.
        (
            "Seu pedido foi enviado em 24/09/2026 e a garantia começa a contar na entrega.",
            ("24/09/2026",),
        ),
        (
            "Sua compra foi feita em 28/09/2026 e a garantia conta a partir da entrega.",
            ("28/09/2026",),
        ),
        (
            "Seu pedido chega até 30/09/2026, e a garantia começa na entrega.",
            ("30/09/2026",),
        ),
        # Número de NF não é ano.
        (
            "A nota fiscal 2045 do seu pedido já foi emitida. Sobre a garantia, a equipe vai "
            "verificar.",
            (),
        ),
        ("A NF nº 000.002.026 saiu; a garantia a equipe confere.", ()),
        # Não é o aparelho que está coberto; nem é a promessa de que esta
        # compra está coberta.
        ("O frete da devolução está coberto pela loja.", ()),
        ("Sua garantia vale a partir da entrega.", ()),
        ("Atualize o software em Configurações. Seu pedido foi enviado em 24/09/2026.", ()),
        # A data da ENTREGA (lacuna) na frase de garantia.
        (
            "O prazo de entrega é 30/09/2026 e a garantia começa a contar na entrega.",
            ("30/09/2026",),
        ),
        ("A entrega é 30/09/2026 e a garantia começa nela.", ("30/09/2026",)),
        (
            "Sua entrega está marcada para 30/09/2026 e a garantia conta a partir dela.",
            ("30/09/2026",),
        ),
        (
            "Data estimada de entrega: 30/09/2026. A garantia do aparelho conta a partir "
            "da entrega.",
            ("30/09/2026",),
        ),
        (
            "Pedido postado em 24/09/2026. A garantia começa a contar na entrega, prevista para "
            "30/09/2026.",
            ("24/09/2026", "30/09/2026"),
        ),
        # A proteção da plataforma e a de terceiros (seguro, Correios).
        ("Seu pedido está coberto pelo programa de proteção do Mercado Livre.", ()),
        ("O pedido está coberto pela política de devolução da Shopee.", ()),
        ("Seu pedido está coberto pela garantia de reembolso da Shopee.", ()),
        ("Seu pedido segue coberto pela proteção da Shopee.", ()),
        ("Seu pedido está coberto pelo seguro da transportadora.", ()),
        ("Sua encomenda está segurada e coberta pelos Correios.", ()),
        ("A transportadora tem cobertura na sua cidade.", ()),
        # O seguro na voz ativa ("o seguro cobre", "tem cobertura do seguro").
        ("Dia 30/09/2026 é a previsão, e o seguro cobre qualquer extravio.", ("30/09/2026",)),
        (
            "Seu pedido chega até 30/09/2026; o seguro cobre extravio até 30/09/2026.",
            ("30/09/2026",),
        ),
        (
            "Sinto muito! Seu pedido tem cobertura do seguro da transportadora; a equipe vai "
            "verificar.",
            (),
        ),
        ("Em caso de extravio, a transportadora cobre o valor.", ()),
        ("Seu pedido está segurado contra extravio.", ()),
        # O ano do modelo do aparelho não é data.
        ("Seu aparelho Redmi 13C 2024 foi enviado e a garantia começa na entrega.", ()),
    ],
)
def test_rastreio_e_plataforma_nao_sao_garantia_inventada(texto, outras):
    for bloco in ([], [{"status": "aguardando_entrega"}]):
        assert _conferir(texto, bloco, outras_datas=outras or ("24/09/2026",)) == [], texto


# ═══════════════ validador: promessa de cobertura ═══════════════


@pytest.mark.parametrize(
    "texto",
    [
        "Seu aparelho está coberto pela garantia, pode ficar tranquilo.",
        "Fique tranquilo, ainda está na garantia.",
        "A garantia cobre esse defeito.",
        "Sua garantia está ativa.",
        "Seu celular continua dentro do prazo de garantia.",
        "Você tem direito à garantia, é só mandar o vídeo.",
        "Você pode acionar a garantia por aqui.",
        "Sim, ainda tem garantia!",
        "Tem garantia sim, pode mandar o vídeo do defeito.",
        "Ele tem garantia, fique tranquilo.",
        "Pode ficar tranquilo, a garantia vale.",
        "A garantia está garantida.",
        "Fique tranquilo: é coberto pela garantia.",
        # Como se escreve no chat.
        "Tá na garantia, pode mandar!",
        "Ainda tá na garantia sim.",
        "Pode mandar que cobre.",
        "Seu aparelho ainda está no prazo de garantia.",
        "Seu aparelho ainda está no período de garantia.",
        "Ainda está valendo a garantia.",
        "Isso entra na garantia, pode mandar.",
        "Seu caso entra na garantia.",
        "Cobrimos pela garantia, pode mandar.",
        "Sim, a gente cobre pela garantia.",
        "Dentro da garantia, sim! Pode enviar.",
        "Tem cobertura sim.",
        "Seu aparelho é coberto, pode mandar.",
        "Fazemos a troca pela garantia.",
        "Seu aparelho está garantido.",
        "Seu aparelho segue garantido.",
        "Fica tranquilo que a garantia resolve.",
        # A negação do vencimento é a promessa; "validade" é o termo do dono.
        "Sua garantia ainda não venceu, pode mandar o vídeo do defeito.",
        "A garantia ainda não expirou, pode enviar o aparelho.",
        "Sua garantia ainda não acabou.",
        "Não venceu a garantia ainda, pode mandar.",
        "Seu aparelho ainda está dentro da validade da garantia.",
        "Ainda está na validade, pode mandar o vídeo.",
        "Seu aparelho está protegido pela garantia, pode mandar.",
        "Esse defeito é coberto, pode mandar o vídeo.",
        "O problema fica coberto, fique tranquilo.",
        "Fique tranquilo que está garantido.",
        "A gente cobre defeito de fábrica.",
        "Cobrimos o conserto, pode mandar.",
        "A loja garante o conserto.",
        "Garantimos o reparo do aparelho.",
        "A gente resolve pela garantia.",
        "A garantia se aplica sim.",
        "A garantia te atende, pode mandar.",
        "A garantia ainda está de pé.",
        "Ainda dá tempo de acionar a garantia.",
        "Garantia ok! Pode mandar o vídeo.",
        "Sim! Está tudo certo com a garantia, pode mandar.",
        "Defeito de fábrica a gente cobre sim.",
        "Assistência da fábrica cobre, pode levar.",
        # O "programa de garantia da loja" é a garantia, não um terceiro.
        "Seu aparelho está coberto pelo programa de garantia da loja.",
    ],
)
def test_promessa_sem_garantia_ativa_reprova(texto):
    for bloco in (
        [],
        [
            {
                "status": "expirada",
                "inicio": "2025-01-01",
                "fim_hardware": "2025-04-01",
                "fim_software": "2026-01-01",
            }
        ],
        [{"status": "aguardando_entrega"}],
        [{"status": "entregue_sem_data"}],
    ):
        assert validador.MOTIVO_COBERTURA_SEM_GARANTIA in _conferir(texto, bloco), (texto, bloco)
    assert validador.MOTIVO_COBERTURA_SEM_GARANTIA not in _conferir(texto, [ATIVA])
    assert validador.so_da_ia([validador.MOTIVO_COBERTURA_SEM_GARANTIA])


@pytest.mark.parametrize(
    "texto",
    [
        "Vamos verificar se o seu aparelho ainda está na garantia.",
        "Não há garantia cadastrada para este pedido; a equipe vai verificar.",
        "O seu aparelho não está mais coberto pela garantia.",
        "A garantia de hardware não está ativa.",
        "Seu aparelho está coberto?",
        "Assim que a equipe conferir, avisamos se está coberto.",
        "Vou verificar se tem garantia.",
        "Infelizmente a garantia não vale mais.",
        "Caso esteja na garantia, a equipe te orienta.",
        "Isso não entra na garantia.",
        "A transportadora não tem cobertura na sua região.",
        "Seu CEP é atendido pela transportadora, que tem cobertura na sua cidade.",
        # Não é o aparelho, ou não promete nada.
        "Cobrimos o frete da devolução.",
        "A loja garante o envio rápido.",
        "A capinha cobre a tela toda.",
        "Garantimos que o aparelho é original.",
        "Seu pedido está garantido e chega amanhã.",
        "A troca é garantida pela política de troca da Shopee.",
        "Infelizmente a garantia já venceu.",
        "Vou verificar se está tudo certo com a garantia.",
        "Não dá mais tempo de acionar a garantia.",
        "Defeito por mau uso a gente não cobre.",
        "Garantia ok?",
    ],
)
def test_negacao_pergunta_e_verificacao_nao_sao_promessa(texto):
    assert validador.MOTIVO_COBERTURA_SEM_GARANTIA not in _conferir(texto, [])


def test_so_software_cobre_software_e_nao_hardware():
    for certa in (
        "O hardware não está mais coberto, mas o software segue coberto até 01/06/2027.",
        "Sua garantia é só de software, até 01/06/2027. O defeito de hardware não está mais "
        "coberto.",
        "O software segue coberto até 01/06/2027; defeito de hardware não está mais coberto.",
        "A garantia cobre defeitos de software até 01/06/2027.",
    ):
        assert _conferir(certa, [SO_SOFTWARE]) == [], certa
    assert _conferir("O hardware está coberto até 01/09/2026.", [SO_SOFTWARE]) == [
        validador.MOTIVO_COBERTURA_HARDWARE
    ]
    assert _conferir("O hardware está coberto até 07/01/2027.", [ATIVA]) == []
    assert _conferir("O software segue coberto até 01/06/2027.", [SO_SOFTWARE]) == []


@pytest.mark.parametrize(
    "texto",
    [
        # O caso real mais comum: defeito depois dos 3 meses do hardware. A
        # promessa que não diz "software" é do aparelho — só com Ativa.
        "Seu aparelho ainda está na garantia até 01/06/2027, pode mandar o vídeo do defeito.",
        "Pode mandar que o defeito está coberto pela garantia.",
        "Seu celular está coberto, pode enviar para análise do defeito.",
        "A tela quebrada está coberta pela garantia.",
        "A garantia de fábrica cobre esse defeito.",
        "Tá na garantia, pode mandar o vídeo do defeito.",
        # Dizer "software" não basta: a frase oferece o defeito, a tela, o conserto.
        "Seu aparelho ainda tem garantia de software até 01/06/2027, pode mandar o vídeo do "
        "defeito.",
        "A garantia de software cobre o defeito da tela até 01/06/2027.",
        "O software está coberto até 01/06/2027, pode mandar o vídeo do defeito da tela.",
        "Seu aparelho tem garantia de software até 01/06/2027, pode mandar o aparelho para "
        "conserto.",
    ],
)
def test_so_software_nao_cobre_defeito_nem_tela(texto):
    assert _conferir(texto, [SO_SOFTWARE]) == [validador.MOTIVO_COBERTURA_HARDWARE]
    # Com a garantia Ativa, a promessa cabe.
    assert not any(m.startswith("promessa") for m in _conferir(texto, [ATIVA]))


def test_com_garantias_de_status_diferentes_a_promessa_diz_de_qual_compra():
    expirada = {
        "status": "expirada",
        "inicio": "2025-01-01",
        "fim_hardware": "2025-04-01",
        "fim_software": "2026-01-01",
        "produto": "Celular Uranyx U0",
    }
    ativa = {**ATIVA, "produto": "Celular Uranyx U1 128GB"}
    qual = validador.MOTIVO_COBERTURA_QUAL_COMPRA
    assert _conferir("Seu aparelho está coberto pela garantia.", [expirada, ativa]) == [qual]
    assert validador.so_da_ia([qual])
    # Pela data (só da que cobre) ou pelo produto, passa.
    assert (
        _conferir("Seu aparelho está coberto pela garantia até 07/01/2027.", [expirada, ativa])
        == []
    )
    assert (
        _conferir("O Celular Uranyx U1 128GB está coberto pela garantia.", [expirada, ativa]) == []
    )
    # Hardware: só software valendo numa, ativa na outra — diga qual.
    assert _conferir("O hardware está coberto.", [SO_SOFTWARE, ATIVA]) == [qual]


# Paráfrases e o verbo "garantir" (também ditos junto da proteção da
# plataforma ou de um terceiro): falam da garantia do aparelho.
PARAFRASES_DE_GARANTIA = [
    "Fique tranquilo, seu aparelho está protegido contra defeitos.",
    "Seu celular tem proteção contra defeitos por um ano.",
    "Se apresentar qualquer defeito, é só nos chamar que a gente troca.",
    "O fabricante dá suporte de um ano para o aparelho.",
    "Em caso de defeito, o conserto é por nossa conta.",
    "Seu celular está assegurado contra defeitos.",
    "Seu celular está coberto pelo seguro contra defeitos.",
    "Seu celular está coberto pela política da loja contra defeitos.",
    "Seu celular está protegido pelo programa contra defeitos.",
    "Seu aparelho está coberto pela plataforma contra defeitos.",
    "A Garantia Shopee cobre defeitos do seu aparelho.",
    "Seu celular tem garantia da plataforma contra defeitos.",
    "Devolução garantida em caso de defeito.",
    "A entrega é garantida, assim como o aparelho.",
    "Pode levar o aparelho na assistência técnica autorizada.",
    "Sua garan-tia está ativa.",
    "Nós garantimos o funcionamento do aparelho.",
    "A loja garante o seu aparelho contra qualquer problema.",
    "A Shopee garante a troca do aparelho com defeito.",
    "Qualquer problema com o celular nos primeiros meses, a gente troca.",
]


@pytest.mark.parametrize(
    "texto",
    [
        "Fique tranquilo: seu aparelho é garantido contra defeito de fábrica.",
        "Seu celular conta com assistência da fábrica por um ano.",
        "Seu aparelho tem suporte do fabricante até janeiro.",
        "Qualquer defeito de fábrica, a troca é por nossa conta.",
        "Seu celular tem assistência técnica do fabricante.",
        "Pode mandar que cobre.",
        *PARAFRASES_DE_GARANTIA,
    ],
)
def test_fala_de_garantia_por_parafrase(texto):
    assert validador.fala_de_garantia(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "A entrega é garantida pela transportadora.",
        "Seu reembolso está garantido pela plataforma.",
        "Seu pedido está coberto pelo seguro da transportadora.",
        "Sua encomenda está segurada e coberta pelos Correios.",
        "A transportadora tem cobertura na sua cidade.",
        "Fique tranquilo, você está coberto pela Compra Garantida do Mercado Livre.",
        # O seguro na voz ativa e o risco do transporte.
        "O seguro da transportadora cobre extravio.",
        "Seu pedido tem cobertura do seguro da transportadora.",
        "Em caso de extravio, a transportadora cobre o valor.",
        "Seu pedido está segurado contra extravio.",
        "Sua compra está protegida contra fraudes pela Shopee.",
        "Seu pedido está garantido e chega amanhã.",
        "A Shopee garante a entrega até 15/10/2026.",
    ],
)
def test_garantido_do_envio_e_cobertura_de_terceiro_nao_falam_de_garantia(texto):
    assert not validador.fala_de_garantia(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "Você está coberto: se o pedido não chegar, a plataforma faz o reembolso.",
        "Lembrando que o aparelho tem garantia de fábrica.",
        "Seu aparelho está coberto pela garantia.",
    ],
)
def test_conversa_que_nao_e_de_garantia_promessa_vai_para_pessoa_sem_bloquear(texto):
    # Bloco não consultado: a promessa não reprova (não bloqueia a sugestão)...
    assert _conferir(texto, [], conferir_promessa=False) == []
    # ...mas fala de garantia: vai para pessoa (ia.py).
    assert validador.fala_de_garantia(texto)
    # Com o bloco consultado e sem garantia ativa, reprova.
    assert validador.MOTIVO_COBERTURA_SEM_GARANTIA in _conferir(texto, [])


def test_sem_conferir_promessa_a_data_inventada_continua_reprovando():
    assert _conferir("A garantia vai até 07/01/2027.", [], conferir_promessa=False) == [
        f"{validador.MOTIVO_DATA_GARANTIA} (07/01/2027)"
    ]


def test_datas_iso_e_por_extenso():
    plano = validador.plano_de(
        "até 2027-01-07, sete de janeiro de 2027, vinte e um de março, primeiro de jan. de 2027"
    )
    assert [d for _, d in validador.datas_citadas(plano)] == [
        (7, 1, 2027),
        (7, 1, 2027),
        (21, 3, None),
        (1, 1, 2027),
    ]
    # O ano sozinho só depois de "até/em/de...": o do modelo do aparelho não.
    assert validador.datas_citadas(validador.plano_de("Redmi 13C 2024")) == []
    assert validador.datas_citadas(validador.plano_de("vale até 2027")) == [
        ("2027", (None, None, 2027))
    ]


def test_fala_de_garantia():
    assert validador.fala_de_garantia("A garantia começa a contar na entrega.")
    assert validador.fala_de_garantia("Qual a validade? A equipe confere.")
    # A data do bloco, mesmo sem a palavra.
    assert validador.fala_de_garantia("O hardware vale até 07/01/2027.", [ATIVA])
    assert validador.fala_de_garantia("Vale até 7 de janeiro de 2027.", [ATIVA])
    assert not validador.fala_de_garantia("Vale até 7 de janeiro de 2027.", [])
    # Rastreio, a proteção da plataforma e dúvida de uso não.
    assert not validador.fala_de_garantia("Seu pedido está coberto pela Garantia Shopee.")
    assert not validador.fala_de_garantia("Atualize o software em Configurações.")
    assert not validador.fala_de_garantia("Seu pedido foi enviado em 24/09/2026.", [ATIVA])


def test_pre_venda_nao_confere_promessa():
    # Pergunta antes da compra (bloco não consultado): não há compra a cobrir.
    assert _conferir("Sim, todos os aparelhos estão cobertos pela garantia.", None) == []


# ═══════════════ contexto: de onde vem a garantia ═══════════════


async def _pedido(
    db: AsyncSession,
    numero: str = PEDIDO_BLING,
    numeroloja: str = PEDIDO_MKT,
    *,
    cpf: str | None = CPF,
    situacao: str = "15",
) -> None:
    db.add(
        BlingOrder(
            numero=numero,
            numeroloja=numeroloja,
            loja="L1",
            data=datetime(2026, 9, 28, 15, tzinfo=UTC),
            situacao=situacao,
            item_index=0,
            item_codigo="dg053",
            item_descricao="Celular Uranyx U1 128GB",
            item_quantidade=1,
            documento_destinatario=cpf,
            nome_destinatario=NOME,
        )
    )
    await db.commit()


async def _garantia(
    db: AsyncSession,
    make_user,
    *,
    pedido_bling: str = PEDIDO_BLING,
    pedido_mkt: str | None = PEDIDO_MKT,
    nf: str = "1234",
    cpf: str = CPF,
    entrega: datetime | None = datetime(2026, 10, 7, 18, tzinfo=UTC),
    origem: str = "shopee",
) -> Garantia:
    user = await make_user()
    g = Garantia(
        pedido_bling=pedido_bling,
        pedido_marketplace=pedido_mkt,
        loja="L1",
        nf_numero=nf,
        nf_serie="1",
        cliente_nome=NOME,
        cpf=cpf,
        criado_por=user.id,
        itens=[{"descricao": "Celular Uranyx U1 128GB", "sku": "dg053", "quantidade": 1}],
    )
    if entrega is not None:
        garantia_svc.aplicar_entrega(g, garantia_svc.Entrega(entrega, origem))
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return g


async def _ctx(db, conversa, textos):
    ctx = await contexto.contexto_da_conversa(db, conversa)
    return await contexto.garantia_para_ia(db, conversa, textos, ctx.get("pedido"))


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("meu pedido é 297840", {"pedidos": ["297840"]}),
        ("Pedido nº 300001, comprei semana passada", {"pedidos": ["300001"]}),
        ("o pedido 251007abcd1234 chegou com defeito", {"pedidos": ["251007ABCD1234"]}),
        ("2000012345678901", {"pedidos": ["2000012345678901"]}),
        ("tiktok 576543210987654321", {"pedidos": ["576543210987654321"]}),
        ("Amazon 702-1234567-1234567", {"pedidos": ["702-1234567-1234567"]}),
        ("Temu PO-211-12345678901234567", {"pedidos": ["PO-211-12345678901234567"]}),
        ("a NF 1234 está aqui", {"nfs": ["1234"]}),
        ("nota fiscal nº 000.001.234", {"nfs": ["1234"]}),
        ("NF-e: 98765", {"nfs": ["98765"]}),
        ("meu cpf é 529.982.247-25", {"cpfs": [CPF]}),
        ("cpf 52998224725", {"cpfs": [CPF]}),
        # Não é: CPF com dígito errado, quantidade depois de "nota"/"pedido",
        # o pedaço de um número maior, a data.
        ("cpf 529.982.247-26", {}),
        ("a nota fiscal de 2 aparelhos", {}),
        ("o pedido de 10 unidades", {}),
        ("pedido 251007ABCD1234", {"pedidos": ["251007ABCD1234"]}),
        ("comprei em 07/10/2026", {}),
    ],
)
def test_citacoes_do_comprador(texto, esperado):
    achado = contexto.citacoes_do_comprador([texto])
    assert achado == {"pedidos": [], "nfs": [], "cpfs": []} | esperado


async def test_garantia_do_pedido_da_conversa_sem_cpf_nem_nome(db, make_user):
    await _pedido(db)
    g = await _garantia(db, make_user)
    conversa = await _conversa(db, make_user)

    bloco = await _ctx(db, conversa, ["meu celular ainda tem garantia?"])

    assert bloco == {
        "consultada": True,
        "garantias": [
            {
                "id": g.id,
                "de_onde": contexto.DE_ONDE_PEDIDO,
                "status": "ativa",
                "status_rotulo": "Ativa",
                "inicio": "2026-10-07",
                "inicio_origem": "evento de entrega da transportadora na Shopee",
                "fim_hardware": "2027-01-07",
                "fim_software": "2027-10-07",
                "produto": "Celular Uranyx U1 128GB",
            }
        ],
    }
    texto = json.dumps(bloco, ensure_ascii=False)
    assert CPF not in texto and "529.982" not in texto and "João" not in texto


async def test_outra_compra_do_mesmo_cpf_entra_depois_da_do_pedido(db, make_user):
    await _pedido(db)
    await _pedido(db, "300002", "251001XYZW9876")
    do_pedido = await _garantia(db, make_user)
    outra = await _garantia(
        db, make_user, pedido_bling="300002", pedido_mkt="251001XYZW9876", nf="2222", entrega=None
    )
    conversa = await _conversa(db, make_user)

    bloco = await _ctx(db, conversa, ["oi"])

    assert [(x["id"], x["de_onde"], x["status"]) for x in bloco["garantias"]] == [
        (do_pedido.id, contexto.DE_ONDE_PEDIDO, "ativa"),
        (outra.id, contexto.DE_ONDE_MESMO_COMPRADOR, "aguardando_entrega"),
    ]
    assert bloco["garantias"][1]["inicio"] is None
    assert bloco["garantias"][1]["status_rotulo"] == "Aguardando entrega"


async def test_cpf_citado_de_outra_pessoa_nao_puxa_garantia(db, make_user):
    await _pedido(db)
    await _garantia(db, make_user, pedido_bling="399999", pedido_mkt=None, nf="7777", cpf=CPF_OUTRO)
    conversa = await _conversa(db, make_user)

    bloco = await _ctx(db, conversa, ["o cpf da minha esposa é 111.444.777-35"])

    assert bloco == {"consultada": True, "garantias": []}


async def test_pedido_longo_citado_entra_mesmo_de_outro_cpf(db, make_user):
    # O nº LONGO do marketplace ninguém adivinha: quem cita tem o pedido.
    await _pedido(db)
    await _pedido(db, "300003", "2000012345678901", cpf=CPF_OUTRO)
    pelo_pedido = await _garantia(
        db,
        make_user,
        pedido_bling="300003",
        pedido_mkt="2000012345678901",
        nf="5555",
        cpf=CPF_OUTRO,
    )
    conversa = await _conversa(db, make_user)

    bloco = await _ctx(db, conversa, ["comprei no ML também, pedido 2000012345678901"])

    assert [(x["id"], x["de_onde"]) for x in bloco["garantias"]] == [
        (pelo_pedido.id, contexto.DE_ONDE_CITADA)
    ]


async def test_com_cpf_da_conversa_nf_e_bling_curto_de_outro_cpf_nao_entram(db, make_user):
    # NF (1 a 4 dígitos) e nº do Bling (sequencial) se chutam: com o CPF da
    # conversa conhecido, só valem os do mesmo CPF.
    await _pedido(db)
    await _pedido(db, "301234", "251001ZZZZ9999", cpf=CPF_OUTRO)
    await _garantia(db, make_user, pedido_bling="399002", pedido_mkt=None, nf="777", cpf=CPF_OUTRO)
    await _garantia(
        db, make_user, pedido_bling="301234", pedido_mkt="251001ZZZZ9999", nf="888", cpf=CPF_OUTRO
    )
    propria = await _garantia(
        db, make_user, pedido_bling="300002", pedido_mkt="251001XYZW9876", nf="2222"
    )
    conversa = await _conversa(db, make_user)

    bloco = await _ctx(db, conversa, ["a nota fiscal é 777", "meu pedido 301234", "e a NF 2222"])

    # Só a do próprio comprador (outra compra do mesmo CPF, e a NF dele).
    assert [x["id"] for x in bloco["garantias"]] == [propria.id]


async def test_nf_citada_do_proprio_comprador_que_tambem_e_de_outro_cpf(db, make_user):
    # A NF 1234 é do comprador da conversa E de outro CPF (outra série ou
    # outro emitente): a do outro nunca entra.
    await _pedido(db)
    propria = await _garantia(db, make_user)  # NF 1234, CPF da conversa, pedido da conversa
    await _garantia(db, make_user, pedido_bling="399001", pedido_mkt=None, nf="1234", cpf=CPF_OUTRO)
    conversa = await _conversa(db, make_user)

    bloco = await _ctx(db, conversa, ["minha NF é 1234"])

    assert [(x["id"], x["de_onde"]) for x in bloco["garantias"]] == [
        (propria.id, contexto.DE_ONDE_PEDIDO)
    ]


async def test_cpf_da_conversa_pela_garantia_do_pedido_quando_o_bling_nao_tem(db, make_user):
    # O pedido no Bling sem CPF (ou CNPJ): o CPF da conversa é o da garantia
    # do pedido — e a NF citada de outro CPF não entra.
    await _pedido(db, cpf=None)
    propria = await _garantia(db, make_user)
    await _garantia(db, make_user, pedido_bling="399003", pedido_mkt=None, nf="4444", cpf=CPF_OUTRO)
    conversa = await _conversa(db, make_user)

    bloco = await _ctx(db, conversa, ["a NF é 4444"])

    assert [x["id"] for x in bloco["garantias"]] == [propria.id]


async def test_sem_cpf_da_conversa_nf_bling_curto_e_cpf_citados_nao_entram(db, make_user):
    # Pedido da conversa sem CPF no Bling e sem garantia: a NF (1 a 4
    # dígitos, mesmo de UM CPF só), o nº curto do Bling (sequencial) e o CPF
    # digitado são de OUTRA pessoa até prova em contrário — nada entra.
    await _pedido(db, cpf=None)
    await _pedido(db, "301234", "251001ZZZZ9999", cpf=CPF_OUTRO)
    await _garantia(db, make_user, pedido_bling="300040", pedido_mkt=None, nf="6666", cpf=CPF_OUTRO)
    await _garantia(
        db, make_user, pedido_bling="301234", pedido_mkt="251001ZZZZ9999", nf="888", cpf=CPF_OUTRO
    )
    conversa = await _conversa(db, make_user)

    for textos in (
        ["a NF 6666"],
        ["a nota fiscal 888, tem garantia?"],
        ["e a garantia do pedido 301234?"],
        ["meu cpf é 111.444.777-35, tem garantia?"],
        ["a NF 6666", "e o pedido 301234", "cpf 11144477735"],
    ):
        assert await _ctx(db, conversa, textos) == {"consultada": True, "garantias": []}, textos


async def test_conversa_sem_pedido_bling_curto_de_outro_cpf_nao_entra(db, make_user):
    await _pedido(db, "301234", "251001ZZZZ9999", cpf=CPF_OUTRO)
    await _garantia(
        db, make_user, pedido_bling="301234", pedido_mkt="251001ZZZZ9999", nf="888", cpf=CPF_OUTRO
    )
    conversa = await _conversa(db, make_user, pedido=None)

    for textos in (["garantia do meu pedido 301234"], ["a NF 888"], ["cpf 111.444.777-35"]):
        assert await _ctx(db, conversa, textos) == {"consultada": True, "garantias": []}, textos


async def test_cpf_citado_sem_pedido_na_conversa_nao_acha_nada(db, make_user):
    # Mesmo o CPF "certo": ninguém puxa garantia digitando um CPF. O nº LONGO
    # do marketplace, sim (quem cita tem o pedido em mãos).
    g = await _garantia(db, make_user, pedido_mkt="251001XYZW9876", nf="2222")
    conversa = await _conversa(db, make_user, pedido=None)

    assert await _ctx(db, conversa, ["meu cpf: 529.982.247-25"]) == {
        "consultada": True,
        "garantias": [],
    }
    bloco = await _ctx(db, conversa, ["o pedido é 251001XYZW9876"])
    assert [(x["id"], x["de_onde"]) for x in bloco["garantias"]] == [
        (g.id, contexto.DE_ONDE_CITADA)
    ]


async def test_cpf_da_conversa_so_do_pedido_casado_pelo_numero_do_bling(db, make_user):
    # Outro pedido, mais novo e de outro CPF, tem `numeroloja` igual ao nº do
    # Bling da conversa: o `buscar_pedido` o acharia primeiro. O CPF dele não
    # vira o da conversa — a garantia do outro não entra.
    await _pedido(db)
    db.add(
        BlingOrder(
            numero="412345",
            numeroloja=PEDIDO_BLING,
            loja="L9",
            data=datetime(2026, 10, 1, 15, tzinfo=UTC),
            situacao="15",
            item_index=0,
            item_codigo="dg053",
            item_descricao="Celular Uranyx U1 128GB",
            item_quantidade=1,
            documento_destinatario=CPF_OUTRO,
            nome_destinatario="Outra Pessoa",
        )
    )
    await db.commit()
    propria = await _garantia(db, make_user)
    await _garantia(db, make_user, pedido_bling="412345", pedido_mkt=None, nf="555", cpf=CPF_OUTRO)
    conversa = await _conversa(db, make_user)

    bloco = await _ctx(db, conversa, ["meu celular tem garantia?"])

    assert [(x["id"], x["de_onde"]) for x in bloco["garantias"]] == [
        (propria.id, contexto.DE_ONDE_PEDIDO)
    ]


async def _notas_emitidas(db, cpfs: list[str]) -> int:
    """Notas emitidas com o complemento = o nº do marketplace da conversa, uma
    por CPF (a mais nova por último). Devolve o id da conta (para limpar: o
    conftest não limpa essas tabelas)."""
    conta = BlingNota(nome=f"emissao-teste-{len(cpfs)}", client_id="c", basic_auth_b64="b")
    db.add(conta)
    await db.flush()
    for i, cpf in enumerate(cpfs):
        db.add(
            BlingNotaEmitida(
                conta_id=conta.id,
                bling_id=990_000 + i,
                numero=str(701 + i),
                situacao=6,
                complemento=PEDIDO_MKT,
                cpf_dest=cpf,
                data_emissao=datetime(2026, 9, 23 + i, 10, 0),
            )
        )
    await db.commit()
    return conta.id


async def _limpar_notas_emitidas(db, conta_id: int) -> None:
    await db.execute(delete(BlingNotaEmitida).where(BlingNotaEmitida.conta_id == conta_id))
    await db.execute(delete(BlingNota).where(BlingNota.id == conta_id))
    await db.commit()


async def test_cpf_so_da_nota_emitida_com_complemento_de_dois_cpfs_nao_vale(db, make_user):
    # O pedido da conversa sem CPF no Bling e sem NF de produto: o CPF viria
    # da nota emitida casada pelo COMPLEMENTO do endereço (= nº do
    # marketplace). Com notas de 2 CPFs nesse complemento, não se sabe de
    # quem é o pedido: as compras do mais novo (outra pessoa) não entram.
    await _pedido(db, cpf=None)
    conta_id = await _notas_emitidas(db, [CPF, CPF_OUTRO])
    try:
        outra = await _garantia(
            db,
            make_user,
            pedido_bling="301234",
            pedido_mkt="251001ZZZZ9999",
            nf="888",
            cpf=CPF_OUTRO,
        )
        conversa = await _conversa(db, make_user)

        bloco = await _ctx(db, conversa, ["meu celular tem garantia?"])

        assert outra.id not in [x["id"] for x in bloco["garantias"]]
        assert bloco == {"consultada": True, "garantias": []}
    finally:
        await _limpar_notas_emitidas(db, conta_id)


async def test_cpf_so_da_nota_emitida_com_um_cpf_vale(db, make_user):
    # O mesmo caminho com UM CPF só no complemento: é o do pedido — as outras
    # compras dele entram como "outra compra do mesmo comprador".
    await _pedido(db, cpf=None)
    conta_id = await _notas_emitidas(db, [CPF])
    try:
        outra_compra = await _garantia(
            db, make_user, pedido_bling="300002", pedido_mkt="251001XYZW9876", nf="2222"
        )
        conversa = await _conversa(db, make_user)

        bloco = await _ctx(db, conversa, ["oi"])

        assert [(x["id"], x["de_onde"]) for x in bloco["garantias"]] == [
            (outra_compra.id, contexto.DE_ONDE_MESMO_COMPRADOR)
        ]
    finally:
        await _limpar_notas_emitidas(db, conta_id)


async def test_entregue_no_bling_sem_data_e_expirada(db, make_user):
    await _pedido(db, situacao="83953")  # Entregue no Bling
    await _garantia(db, make_user, entrega=None)
    conversa = await _conversa(db, make_user)
    [g] = (await _ctx(db, conversa, ["oi"]))["garantias"]
    assert g["status"] == "entregue_sem_data"
    assert g["status_rotulo"] == "Entregue sem data no DaVinci"

    # Entregue há mais de 12 meses: expirada; a data do rastreio é aproximada.
    await _pedido(db, "300020", "250101AAAA0000", cpf=CPF_OUTRO)
    await _garantia(
        db,
        make_user,
        pedido_bling="300020",
        pedido_mkt="250101AAAA0000",
        nf="9999",
        cpf=CPF_OUTRO,
        entrega=datetime(2025, 1, 10, 15, tzinfo=UTC),
        origem="rastreio",
    )
    outra = await _conversa(db, make_user, pedido="250101AAAA0000", externo_id="conv-exp")
    [g] = (await _ctx(db, outra, ["oi"]))["garantias"]
    assert g["status"] == "expirada"
    assert g["inicio_origem"] == "rastreio da transportadora (data aproximada)"


async def test_sem_garantia_cadastrada(db, make_user):
    await _pedido(db)
    conversa = await _conversa(db, make_user)
    assert await _ctx(db, conversa, ["tem garantia?"]) == {"consultada": True, "garantias": []}


def test_bloco_para_o_modelo():
    assert ia.bloco_garantia(None) is None
    assert ia.bloco_garantia({"consultada": True, "garantias": []}) == "sem garantia cadastrada"
    assert ia.bloco_garantia({"consultada": False, "garantias": []}).startswith(
        "não deu para consultar"
    )
    [linha] = ia.bloco_garantia(
        {
            "consultada": True,
            "garantias": [
                {
                    "id": 1,
                    "de_onde": contexto.DE_ONDE_PEDIDO,
                    "status": "somente_software",
                    "status_rotulo": "Somente software",
                    "inicio": "2026-06-01",
                    "inicio_origem": "data oficial da entrega no Mercado Livre",
                    "fim_hardware": "2026-09-01",
                    "fim_software": "2027-06-01",
                    "produto": None,
                }
            ],
        }
    )
    assert linha == {
        "de_onde": "pedido desta conversa",
        "status": "Somente software",
        "cobre_hoje": "só software (o hardware já venceu)",
        "inicio": "01/06/2026",
        "inicio_veio_de": "data oficial da entrega no Mercado Livre",
        "fim_hardware": "01/09/2026",
        "fim_software": "01/06/2027",
    }


# ═══════════════ a IA com a garantia ═══════════════


async def test_ia_recebe_o_bloco_e_responde_com_as_datas_dele(db, make_user, ia_ligada, modelo):
    await _pedido(db)
    g = await _garantia(db, make_user)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "meu celular ainda tem garantia? qual a validade? cpf 529.982.247-25")
    modelo.saida = _saida(
        "Olá! A garantia do seu aparelho está ativa: o hardware vale até 7 de janeiro de 2027 "
        "e o software até 07/10/2027.",
        categoria="garantia",
        precisa_humano=True,
    )

    r = await ia.gerar_rascunho(db, conversa)

    # Os FATOS que o modelo recebeu: o bloco, sem CPF nem nome.
    assert '"fim_hardware": "07/01/2027"' in modelo.usuario
    assert '"status": "Ativa"' in modelo.usuario
    assert "52998224725" not in modelo.usuario and "529.982.247-25" not in modelo.usuario
    assert "João" not in modelo.usuario
    assert "GARANTIA (validade, se ainda está coberto)" in modelo.sistema
    # A data "7 de janeiro de 2027" não é número inventado; o validador aprova.
    assert r.status == RASCUNHO_PENDENTE, r.motivo
    assert r.validador_ok is True and r.validador_erros == []
    assert "número que não veio do sistema" not in (r.motivo or "")
    # Garantia continua só de pessoa: a IA sugere, não envia.
    assert r.precisa_humano is True
    assert "assunto só para pessoa (garantia)" in r.motivo
    assert r.fatos["garantia_ids"] == [g.id]
    assert r.fatos["garantia"][0]["fim_software"] == "07/10/2027"
    assert CPF not in json.dumps(r.fatos, ensure_ascii=False)


async def test_ia_com_data_inventada_fica_bloqueada(db, make_user, ia_ligada, modelo):
    await _pedido(db)
    await _garantia(db, make_user)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "até quando vai a garantia?")
    modelo.saida = _saida(
        "Olá! A garantia de hardware vai até 07/02/2027.",
        categoria="garantia",
        precisa_humano=True,
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_BLOQUEADO
    assert f"{validador.MOTIVO_DATA_GARANTIA} (07/02/2027)" in r.validador_erros


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! Sua garantia vale até 07/01/2027 para hardware e até 07/10/2027 para software.",
        "Olá! Seu aparelho está coberto até 07/01/2027 (hardware) e 07/10/2027 (software).",
    ],
)
async def test_ia_com_a_parte_depois_da_data_nao_bloqueia(db, make_user, ia_ligada, modelo, texto):
    # A redação mais natural para "hardware até X; software até Y", com a
    # parte depois da data: a resposta CERTA não fica bloqueada.
    await _pedido(db)
    await _garantia(db, make_user)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "até quando vai a garantia do meu celular?")
    modelo.saida = _saida(texto, categoria="garantia", precisa_humano=True)

    r = await ia.gerar_rascunho(db, conversa)

    assert r.fatos["garantia"][0]["status"] == "Ativa"
    assert r.status == RASCUNHO_PENDENTE, r.motivo
    assert r.validador_erros == []
    assert r.precisa_humano is True


@pytest.mark.parametrize(
    "texto",
    [
        "Olá! Sua garantia ainda não venceu, pode mandar o vídeo do defeito.",
        "Olá! Seu aparelho ainda está dentro da validade da garantia, pode mandar.",
        "Olá! Seu aparelho está protegido pela garantia, pode mandar o vídeo.",
        "Olá! Esse defeito é coberto, pode mandar o vídeo.",
        "Olá! A gente cobre defeito de fábrica, pode mandar.",
        "Olá! Fique tranquilo que está garantido, pode mandar o vídeo.",
    ],
)
async def test_ia_sem_garantia_promessa_comum_fica_bloqueada(
    db, make_user, ia_ligada, modelo, texto
):
    await _pedido(db)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "o celular parou, está na garantia?")
    modelo.saida = _saida(texto, categoria="garantia", precisa_humano=True)

    r = await ia.gerar_rascunho(db, conversa)

    assert '"garantia": "sem garantia cadastrada"' in modelo.usuario
    assert r.status == RASCUNHO_BLOQUEADO
    assert validador.MOTIVO_COBERTURA_SEM_GARANTIA in r.validador_erros


async def test_ia_sem_garantia_nao_promete_cobertura(db, make_user, ia_ligada, modelo):
    await _pedido(db)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "o celular parou, está na garantia?")
    modelo.saida = _saida(
        "Olá! Fique tranquilo, seu aparelho está coberto pela garantia.",
        categoria="garantia",
        precisa_humano=True,
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert '"garantia": "sem garantia cadastrada"' in modelo.usuario
    assert r.status == RASCUNHO_BLOQUEADO
    assert validador.MOTIVO_COBERTURA_SEM_GARANTIA in r.validador_erros

    # A resposta certa (a equipe verifica) passa.
    await _msg(db, conversa, "e aí?", ha=timedelta(minutes=1))
    modelo.saida = _saida(
        "Olá! Não encontrei a garantia deste pedido cadastrada; a equipe vai verificar e "
        "te responde por aqui.",
        categoria="garantia",
        precisa_humano=True,
    )
    r = await ia.gerar_rascunho(db, conversa, forcar=True)
    assert r.status == RASCUNHO_PENDENTE and r.validador_erros == []


async def test_pergunta_pre_venda_nem_consulta_a_garantia(db, make_user, ia_ligada, modelo):
    conversa = await _conversa(
        db,
        make_user,
        pedido=None,
        canal="pergunta",
        plataforma="ml",
        platform=IntegrationPlatform.ML,
    )
    await _msg(db, conversa, "qual a garantia do celular?")
    modelo.saida = _saida("Olá! A equipe vai te responder sobre a garantia.", categoria="garantia")

    r = await ia.gerar_rascunho(db, conversa)

    assert '"garantia"' not in modelo.usuario
    assert "garantia" not in r.fatos


def test_conversa_de_garantia():
    assert ia.conversa_de_garantia(["meu celular ainda tem garantia?"])
    assert ia.conversa_de_garantia(["oi", "qual a validade do aparelho?"])
    assert ia.conversa_de_garantia(["o celular veio com defeito"])
    assert ia.conversa_de_garantia(["oi"], "garantia")
    assert ia.conversa_de_garantia(["oi"], "defeito")
    assert not ia.conversa_de_garantia(["cadê meu pedido?"], "rastreio")
    assert not ia.conversa_de_garantia(["qual a validade do cupom?"])


async def _garantia_do_pedido_ia(db, make_user) -> Garantia:
    """Garantia ATIVA do pedido de `_pedido_completo` (Bling 90001)."""
    return await _garantia(
        db,
        make_user,
        pedido_bling=PEDIDO_BLING_IA,
        pedido_mkt=PEDIDO_MKT,
        cpf="12345678909",
    )


async def test_conversa_de_rastreio_nao_recebe_a_garantia_e_data_dela_fica_bloqueada(
    db, make_user, ia_ligada, modelo, envios
):
    # Canal no automático, rastreio liberado, garantia ATIVA cadastrada: o
    # cliente só perguntou do pedido. O modelo não recebe o bloco — e se
    # escrever a data da garantia mesmo assim, é número inventado: bloqueia.
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    await _garantia_do_pedido_ia(db, make_user)
    modelo.saida = _saida(
        "Seu pedido foi enviado pela {transportadora}; o rastreio é {rastreio}. "
        "Sua garantia de hardware vai até 07/01/2027."
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert '"garantia"' not in modelo.usuario
    assert "garantia" not in r.fatos
    assert r.status == RASCUNHO_BLOQUEADO
    assert f"{validador.MOTIVO_DATA_GARANTIA} (07/01/2027)" in r.validador_erros
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in r.motivo
    assert r.precisa_humano is True
    assert envios == []


async def test_resposta_que_fala_de_garantia_nunca_sai_no_automatico(
    db, make_user, ia_ligada, modelo, envios
):
    # Sem data nem promessa (o validador aprova): falou de garantia → pessoa.
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    modelo.saida = _saida(
        "Seu pedido foi enviado pela {transportadora}; o rastreio é {rastreio}. "
        "A garantia começa a contar na entrega."
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_PENDENTE and r.validador_ok and r.validador_erros == []
    assert r.precisa_humano is True
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in r.motivo
    assert envios == []


async def test_rastreio_com_a_garantia_da_plataforma_continua_no_automatico(
    db, make_user, ia_ligada, modelo, envios
):
    # "Coberto pela Garantia Shopee" é da plataforma, não do aparelho: nem
    # reprova nem tira do automático (como era antes).
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    modelo.saida = _saida(
        "Seu pedido foi enviado pela {transportadora}; o rastreio é {rastreio}. "
        "Fique tranquilo, ele está coberto pela Garantia Shopee até a entrega."
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_PENDENTE and r.validador_erros == [], r.motivo
    assert r.precisa_humano is False
    assert [e["texto"] for e in envios] == [
        f"Seu pedido foi enviado pela SEDEX; o rastreio é {RASTREIO}. "
        "Fique tranquilo, ele está coberto pela Garantia Shopee até a entrega."
    ]


async def test_rastreio_com_data_de_envio_e_garantia_na_mesma_frase_nao_bloqueia(
    db, make_user, ia_ligada, modelo, envios
):
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    modelo.saida = _saida(
        "Seu pedido foi enviado em {data_envio} e a garantia começa a contar na entrega."
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_PENDENTE and r.validador_erros == [], r.motivo
    assert r.precisa_humano is True  # fala de garantia
    assert envios == []


@pytest.mark.parametrize(
    "extra",
    [
        "Fique tranquilo: seu aparelho é garantido contra defeito de fábrica.",
        "Seu celular conta com assistência da fábrica por um ano.",
        "Seu aparelho tem suporte do fabricante até janeiro.",
        *PARAFRASES_DE_GARANTIA,
    ],
)
async def test_parafrase_de_garantia_nunca_sai_no_automatico(
    db, make_user, ia_ligada, modelo, envios, extra
):
    # Conversa de rastreio (sem bloco), canal no automático: o modelo fala de
    # garantia sem a palavra — vai para pessoa (pendente), não sai sozinho.
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    modelo.saida = _saida(
        "Seu pedido foi enviado pela {transportadora}; o rastreio é {rastreio}. " + extra
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_PENDENTE, r.motivo
    assert r.precisa_humano is True
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in r.motivo
    assert envios == []


async def test_prazo_ate_o_dia_com_garantia_shopee_continua_bloqueado(
    db, make_user, ia_ligada, modelo, envios
):
    # "Até o dia {previsao_entrega}" é promessa de ENTREGA, mesmo numa frase
    # que cita a Garantia Shopee (como antes da garantia na IA).
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    modelo.saida = _saida(
        "Seu pedido chega até o dia {previsao_entrega} e está coberto pela Garantia Shopee."
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_BLOQUEADO
    assert "prazo em números só pode vir do sistema" in r.validador_erros
    assert envios == []


@pytest.mark.parametrize(
    "texto",
    [
        "Seu aparelho ainda está na garantia até 01/05/2027, pode mandar o vídeo do defeito.",
        # Dizer "software" não basta: a frase oferece o defeito, a tela.
        "Seu aparelho ainda tem garantia de software até 01/05/2027, pode mandar o vídeo do "
        "defeito.",
        "O software está coberto até 01/05/2027, pode mandar o vídeo do defeito da tela.",
    ],
)
async def test_somente_software_com_defeito_nao_promete_cobertura(
    db, make_user, ia_ligada, modelo, texto
):
    # Hardware venceu (3 meses), software vale até 01/05/2027: "pode mandar o
    # vídeo do defeito, está na garantia" promete o que não cobre.
    await _pedido_completo(db)
    await _garantia(
        db,
        make_user,
        pedido_bling=PEDIDO_BLING_IA,
        pedido_mkt=PEDIDO_MKT,
        cpf="12345678909",
        entrega=datetime(2026, 5, 1, 18, tzinfo=UTC),
    )
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "a tela do meu celular parou de funcionar, ainda tem garantia?")
    modelo.saida = _saida(texto, categoria="defeito", precisa_humano=True)

    r = await ia.gerar_rascunho(db, conversa)

    assert r.fatos["garantia"][0]["status"] == "Somente software"
    assert r.status == RASCUNHO_BLOQUEADO
    assert validador.MOTIVO_COBERTURA_HARDWARE in r.validador_erros


@pytest.mark.parametrize(
    "texto,sai_sozinho",
    [
        (
            "O prazo de entrega é {previsao_entrega} e a garantia começa a contar na entrega.",
            False,
        ),
        ("O rastreio é {rastreio}. Seu pedido está coberto pelo seguro da transportadora.", True),
        ("O rastreio é {rastreio}. Seu pedido segue coberto pela proteção da Shopee.", True),
        (
            "Seu pedido foi enviado em {data_envio} pela {transportadora}. Lembrando que o "
            "aparelho tem garantia de fábrica.",
            False,
        ),
        # O seguro na voz ativa: sai como antes da garantia na IA.
        ("Dia {previsao_entrega} é a previsão, e o seguro cobre qualquer extravio.", True),
        (
            "Seu pedido chega até {previsao_entrega}; o seguro cobre extravio até "
            "{previsao_entrega}.",
            True,
        ),
        ("O rastreio é {rastreio}. Seu pedido tem cobertura do seguro da transportadora.", True),
        ("O rastreio é {rastreio}. O seguro da transportadora cobre extravio.", True),
        ("O rastreio é {rastreio}. Seu pedido está segurado contra extravio.", True),
        # "Garantia da transportadora": a palavra vai para pessoa, sem bloquear.
        (
            "A previsão é {previsao_entrega}. Em caso de extravio, a garantia da transportadora "
            "cobre.",
            False,
        ),
    ],
)
async def test_resposta_normal_de_rastreio_nao_e_bloqueada(
    db, make_user, ia_ligada, modelo, envios, texto, sai_sozinho
):
    # Conversa de rastreio (a garantia nem é consultada): a resposta de
    # rastreio/prazo não é BLOQUEADA. A que fala da garantia do aparelho vai
    # para pessoa; o seguro/proteção da plataforma sai como antes.
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    modelo.saida = _saida(texto)

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_PENDENTE, r.motivo
    assert r.validador_erros == []
    assert bool(envios) is sai_sozinho
    if not sai_sozinho:
        assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in r.motivo


async def test_conversa_de_defeito_com_o_seguro_da_transportadora_nao_e_bloqueada(
    db, make_user, ia_ligada, modelo
):
    # Chegou quebrado (avaria do transporte), sem garantia cadastrada: o
    # seguro da transportadora não é promessa da garantia do aparelho.
    await _pedido(db)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "o celular chegou com a tela quebrada, a caixa veio amassada")
    modelo.saida = _saida(
        "Sinto muito! Seu pedido tem cobertura do seguro da transportadora; a equipe vai "
        "verificar.",
        categoria="defeito",
        precisa_humano=True,
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert r.status == RASCUNHO_PENDENTE, r.motivo
    assert r.validador_erros == []


async def test_ia_cpf_ambiguo_da_nota_emitida_nao_leva_a_garantia_do_outro(
    db, make_user, ia_ligada, modelo
):
    # Ponta a ponta do caso acima: o modelo não recebe o produto nem as
    # datas da compra da outra pessoa.
    await _pedido(db, cpf=None)
    conta_id = await _notas_emitidas(db, [CPF, CPF_OUTRO])
    try:
        outra = await _garantia(
            db,
            make_user,
            pedido_bling="301234",
            pedido_mkt="251001ZZZZ9999",
            nf="888",
            cpf=CPF_OUTRO,
        )
        outra.itens = [{"descricao": "Tablet Outro Comprador X9", "sku": "x9", "quantidade": 1}]
        await db.commit()
        conversa = await _conversa(db, make_user)
        await _msg(db, conversa, "meu celular ainda tem garantia?")
        modelo.saida = _saida(
            "Olá! A equipe vai verificar.", categoria="garantia", precisa_humano=True
        )

        r = await ia.gerar_rascunho(db, conversa)

        assert '"garantia": "sem garantia cadastrada"' in modelo.usuario
        assert "Tablet Outro Comprador X9" not in modelo.usuario
        assert r.fatos["garantia_ids"] == []
    finally:
        await _limpar_notas_emitidas(db, conta_id)


async def test_promessa_na_conversa_que_nao_e_de_garantia_vai_para_pessoa(
    db, make_user, ia_ligada, modelo, envios
):
    # O bloco não foi consultado (o cliente só perguntou do pedido): a
    # promessa não BLOQUEIA a sugestão — vai para pessoa conferir.
    conversa = await _cenario_auto(db, make_user, ia_ligada, modelo)
    modelo.saida = _saida(
        "Seu pedido foi enviado pela {transportadora}; o rastreio é {rastreio}. "
        "Fique tranquilo, seu aparelho está coberto pela garantia."
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert '"garantia"' not in modelo.usuario
    assert r.status == RASCUNHO_PENDENTE and r.validador_erros == [], r.motivo
    assert r.precisa_humano is True
    assert ia.MOTIVO_RESPOSTA_DE_GARANTIA in r.motivo
    assert envios == []


async def test_conversa_sem_pedido_citando_bling_de_outro_nao_recebe_a_garantia_dele(
    db, make_user, ia_ligada, modelo
):
    await _pedido(db, "301234", "251001ZZZZ9999", cpf=CPF_OUTRO)
    await _garantia(
        db, make_user, pedido_bling="301234", pedido_mkt="251001ZZZZ9999", nf="888", cpf=CPF_OUTRO
    )
    conversa = await _conversa(db, make_user, pedido=None)
    await _msg(db, conversa, "qual a garantia do pedido 301234? a NF é 888, cpf 111.444.777-35")
    modelo.saida = _saida(
        "Olá! Não encontrei a garantia cadastrada; a equipe vai verificar.",
        categoria="garantia",
        precisa_humano=True,
    )

    r = await ia.gerar_rascunho(db, conversa)

    assert '"garantia": "sem garantia cadastrada"' in modelo.usuario
    assert "Uranyx U1" not in modelo.usuario
    assert r.fatos["garantia_ids"] == []


def test_validade_e_pista_de_garantia_menos_a_do_cupom():
    assert "garantia" in ia.pistas_de_categoria("qual a validade do meu celular?")
    assert "garantia" not in ia.pistas_de_categoria("qual a validade do cupom?")


# ═══════════════ provedor sem crédito ═══════════════


OPENAI = "https://api.openai.com/v1"
GROQ = "https://api.groq.com/openai/v1"
CHAVE = "sk-proj-chave-de-teste-nao-real"


@pytest.mark.parametrize(
    "status,corpo,esperado",
    [
        # OpenAI sem crédito.
        (
            429,
            {
                "error": {
                    "message": "You exceeded your current quota, please check your plan "
                    "and billing details.",
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                }
            },
            "insufficient_quota",
        ),
        (429, {"error": {"code": "credit_balance_exhausted"}}, "credit_balance_exhausted"),
        (429, {"error": {"type": "billing_hard_limit_reached"}}, "billing_hard_limit_reached"),
        # 402 de qualquer um.
        (402, {"error": {"message": "Payment Required"}}, "payment_required"),
        (402, None, "payment_required"),
        # Anthropic: 400 com a frase do crédito, ou o billing_error.
        (
            400,
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API. "
                    "Please go to Plans & Billing to upgrade or purchase credits.",
                },
            },
            "credit_balance_too_low",
        ),
        (
            402,
            {"type": "error", "error": {"type": "billing_error", "message": "x"}},
            "billing_error",
        ),
        # Limite por minuto: NÃO é falta de crédito (o do Groq traz o link de billing).
        (
            429,
            {
                "error": {
                    "message": "Rate limit reached for model `openai/gpt-oss-120b` on "
                    "tokens per minute (TPM): Limit 8000, Used 7000, Requested 2000. Please "
                    "try again in 7.5s. Need more tokens? Upgrade to Dev Tier today at "
                    "https://console.groq.com/settings/billing",
                    "type": "tokens",
                    "code": "rate_limit_exceeded",
                }
            },
            None,
        ),
        (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}, None),
        # Limite por minuto cuja mensagem sugere subir o teto de gasto: é limite.
        (
            429,
            {
                "error": {
                    "message": "Rate limit reached on requests per minute (RPM). Raise your "
                    "spend limit at https://console.groq.com/settings/billing",
                    "type": "requests",
                    "code": "rate_limit_exceeded",
                }
            },
            None,
        ),
        (429, None, None),
        (400, {"error": {"message": "invalid model"}}, None),
        (500, {"error": {"code": "insufficient_quota"}}, None),
    ],
)
def test_falta_de_credito(status, corpo, esperado):
    assert ia.falta_de_credito(status, corpo) == esperado


def test_nome_do_provedor():
    assert ia.nome_do_provedor(ia.Provedor(OPENAI, "gpt-5", "k")) == "OpenAI"
    assert ia.nome_do_provedor(ia.Provedor(GROQ, "openai/gpt-oss-120b", "k")) == "Groq"
    assert (
        ia.nome_do_provedor(ia.Provedor(ia.BASE_CLAUDE, "claude-opus-5", "k", ia.PROVEDOR_CLAUDE))
        == "Anthropic (Claude)"
    )
    assert ia.nome_do_provedor(ia.Provedor("https://llm.exemplo.com/v1", "m", "k")) == (
        "llm.exemplo.com"
    )


@pytest.fixture
def openai(ia_ligada, monkeypatch):
    monkeypatch.setattr(ia_ligada, "atendimento_llm_base_url", OPENAI)
    monkeypatch.setattr(ia_ligada, "atendimento_llm_api_key", CHAVE)
    monkeypatch.setattr(ia_ligada, "atendimento_llm_model", "gpt-5-mini")
    return ia_ligada


async def test_openai_sem_credito_definitivo_sem_rascunho_e_frase_na_tela(
    db, make_user, openai, esperas
):
    await _pedido(db)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "cadê meu pedido?")
    corpo = {
        "error": {
            "message": "You exceeded your current quota, please check your plan and billing "
            "details.",
            "type": "insufficient_quota",
            "param": None,
            "code": "insufficient_quota",
        }
    }
    with respx.mock(assert_all_called=True) as rota, structlog.testing.capture_logs() as logs:
        chamada = rota.post(f"{OPENAI}/chat/completions").mock(
            return_value=httpx.Response(429, json=corpo)
        )
        assert await ia.gerar_rascunho(db, conversa, forcar=True) is None

    # Definitivo: UMA chamada, sem a espera/nova tentativa do limite.
    assert chamada.call_count == 1 and esperas == []
    # Sem rascunho: quando o crédito voltar, a mesma mensagem ganha sugestão.
    assert await _rascunhos(db, conversa) == []
    frase = "A IA está sem crédito no provedor (OpenAI) — adicione créditos ou troque a chave"
    assert ia.motivo_sem_credito("OpenAI") == frase
    assert await ia.motivo_sem_rascunho(db, conversa) == frase
    # No log, só o provedor e o código — nunca a chave nem o corpo.
    evento = next(x for x in logs if x["event"] == "atendimento_ia_provedor_sem_credito")
    assert (evento["provedor"], evento["codigo"]) == ("OpenAI", "insufficient_quota")
    assert CHAVE not in repr(logs) and "exceeded your current quota" not in repr(logs)
    # A rodada do cron para (as outras conversas bateriam no mesmo muro).
    assert ia._no_limite()


async def test_groq_limite_por_minuto_continua_com_a_frase_de_sempre(
    db, make_user, ia_ligada, monkeypatch, esperas
):
    monkeypatch.setattr(ia_ligada, "atendimento_llm_base_url", GROQ)
    await _pedido(db)
    conversa = await _conversa(db, make_user)
    await _msg(db, conversa, "cadê meu pedido?")
    corpo = {
        "error": {
            "message": "Rate limit reached for model `openai/gpt-oss-120b` on tokens per minute "
            "(TPM). Upgrade to Dev Tier today at https://console.groq.com/settings/billing",
            "type": "tokens",
            "code": "rate_limit_exceeded",
        }
    }
    with respx.mock(assert_all_called=True) as rota:
        chamada = rota.post(f"{GROQ}/chat/completions").mock(
            return_value=httpx.Response(429, json=corpo)
        )
        assert await ia.gerar_rascunho(db, conversa, forcar=True) is None

    assert chamada.call_count == 2 and esperas == [ia.ESPERA_LIMITE_S]
    assert await ia.motivo_sem_rascunho(db, conversa) == ia.MOTIVO_LIMITE_PROVEDOR


async def test_groq_402_sem_credito(db, make_user, ia_ligada, monkeypatch, esperas):
    monkeypatch.setattr(ia_ligada, "atendimento_llm_base_url", GROQ)
    conversa = await _conversa(db, make_user, pedido=None)
    await _msg(db, conversa, "vocês têm a mala azul?")
    with respx.mock(assert_all_called=True) as rota:
        rota.post(f"{GROQ}/chat/completions").mock(
            return_value=httpx.Response(402, json={"error": {"message": "Payment Required"}})
        )
        assert await ia.gerar_rascunho(db, conversa, forcar=True) is None
    assert await ia.motivo_sem_rascunho(db, conversa) == (
        "A IA está sem crédito no provedor (Groq) — adicione créditos ou troque a chave"
    )


async def test_rodada_do_cron_para_sem_credito(db, make_user, ia_ligada, modelo, esperas):
    for i in range(3):
        c = await _conversa(db, make_user, pedido=None, externo_id=f"c-credito-{i}")
        await _msg(db, c, "vocês têm a mala azul?", ha=timedelta(minutes=10))
    modelo.erro = ia.ErroProvedor(
        "provedor sem crédito (429 insufficient_quota)",
        definitivo=True,
        sem_credito=True,
        provedor="OpenAI",
        codigo="insufficient_quota",
    )

    with structlog.testing.capture_logs() as logs:
        assert await ia.gerar_pendentes(db) == 0

    # Só a primeira chamou, uma vez; nenhuma ganhou rascunho (nem bloqueado).
    assert len(modelo.chamadas) == 1 and esperas == []
    parou = next(x for x in logs if x["event"] == "atendimento_ia_rodada_parou_no_limite")
    assert parou["adiadas"] == 2 and "sem crédito" in parou["motivo"]


@pytest.mark.parametrize(
    "resposta,codigo",
    [
        (
            httpx2.Response(
                400,
                json={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "Your credit balance is too low to access the Anthropic "
                        "API. Please go to Plans & Billing to upgrade or purchase credits.",
                    },
                },
            ),
            "credit_balance_too_low",
        ),
        (
            httpx2.Response(
                402, json={"type": "error", "error": {"type": "billing_error", "message": "x"}}
            ),
            "billing_error",
        ),
    ],
)
async def test_claude_sem_credito_definitivo_sem_nova_tentativa(
    claude, api, sem_groq, esperas, resposta, codigo
):
    api.responder(resposta)
    with pytest.raises(ia.ErroProvedor) as exc:
        await ia._chamar("S", "U", max_tokens=ia.MAX_TOKENS_RESPOSTA)
    e = exc.value
    assert (e.sem_credito, e.definitivo, e.limite) == (True, True, False)
    assert (e.provedor, e.codigo) == ("Anthropic (Claude)", codigo)
    assert len(api.pedidos) == 1 and esperas == []
