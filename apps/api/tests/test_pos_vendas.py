"""Casamento pedido ↔ NF-e do Pós Vendas (`match_notas`, função pura).

Regras cobertas (medidas em produção, ago/2026):
- 1ª chave: complemento do endereço da nota == numeroloja do pedido
  (o fluxo de emissão grava o pedido do marketplace no complemento);
- 2ª chave: CPF do destinatário + janela [envio−10d, envio+3d];
- classificação: CNPJ da conta emissora == CNPJ da empresa dona da loja
  → NF EMBALAGEM; conta diferente → NF PRODUTO (avulsa);
- cada nota é usada por no máximo UM pedido;
- notas com situação fora de {5, 6, 7} (não emitidas) ficam de fora.
"""

from datetime import date, datetime, timedelta

from app.services.pos_vendas import (
    JANELA_ANTES_DIAS,
    JANELA_DEPOIS_DIAS,
    NotaIn,
    PedidoIn,
    match_notas,
)

# CNPJ da empresa dona da loja (== conta de emissão da NF embalagem).
CNPJ_LOJA = "59882322000154"
# Conta avulsa (NF produto) — CNPJ diferente do da loja.
CNPJ_AVULSA = "11222333000144"

CPF = "12345678901"


def _pedido(
    numero: str = "292001",
    numeroloja: str | None = "2026082412345678",
    cpf: str | None = CPF,
    envio: date | None = date(2026, 8, 20),
    store_cnpj: str | None = CNPJ_LOJA,
) -> PedidoIn:
    return PedidoIn(
        numero=numero,
        numeroloja=numeroloja,
        cpf=cpf,
        envio=envio,
        store_cnpj=store_cnpj,
    )


def _nota(
    key: str,
    conta_cnpj: str = CNPJ_LOJA,
    cpf: str | None = CPF,
    complemento: str | None = "2026082412345678",
    data_emissao: datetime | None = datetime(2026, 8, 19, 10, 0),
    situacao: int | None = 5,
) -> NotaIn:
    return NotaIn(
        key=key,
        conta_cnpj=conta_cnpj,
        cpf=cpf,
        complemento=complemento,
        data_emissao=data_emissao,
        situacao=situacao,
    )


# ─── passada 1: chave exata (complemento == numeroloja) ───────────────


def test_match_exato_classifica_embalagem_e_produto():
    """As duas notas do envio casam pela chave exata e cada uma cai no lado
    certo: conta da loja → embalagem, conta avulsa → produto."""
    r = match_notas(
        [_pedido()],
        [
            _nota("emb", conta_cnpj=CNPJ_LOJA),
            _nota("prod", conta_cnpj=CNPJ_AVULSA),
        ],
    )["292001"]
    assert r.embalagem == "emb"
    assert r.produto == "prod"
    assert r.embalagem_via == "pedido"
    assert r.produto_via == "pedido"


def test_match_exato_ignora_envio_ausente():
    """A chave exata casa mesmo sem data de envio (janela é só do fallback)."""
    r = match_notas([_pedido(envio=None)], [_nota("emb")])["292001"]
    assert r.embalagem == "emb"


def test_complemento_diferente_nao_casa_na_exata():
    r = match_notas(
        [_pedido(cpf=None)],
        [_nota("x", complemento="OUTRO-PEDIDO")],
    )["292001"]
    assert r.embalagem is None
    assert r.produto is None


# ─── passada 2: CPF + janela ──────────────────────────────────────────


def test_fallback_cpf_dentro_da_janela():
    """Sem complemento casável, o CPF + janela resolve (via='cpf')."""
    r = match_notas(
        [_pedido(numeroloja=None)],
        [
            _nota("emb", complemento=None),
            _nota("prod", conta_cnpj=CNPJ_AVULSA, complemento=None),
        ],
    )["292001"]
    assert r.embalagem == "emb"
    assert r.produto == "prod"
    assert r.embalagem_via == "cpf"
    assert r.produto_via == "cpf"


def test_fallback_cpf_aceita_pontuacao_no_pedido():
    """O CPF do pedido pode vir formatado (123.456.789-01)."""
    r = match_notas(
        [_pedido(numeroloja=None, cpf="123.456.789-01")],
        [_nota("emb", complemento=None)],
    )["292001"]
    assert r.embalagem == "emb"


def test_fallback_cpf_fora_da_janela_nao_casa():
    envio = date(2026, 8, 20)
    meio_dia = datetime(2026, 8, 20, 12, 0)
    antes_demais = meio_dia - timedelta(days=JANELA_ANTES_DIAS + 1)
    depois_demais = meio_dia + timedelta(days=JANELA_DEPOIS_DIAS + 1)
    r = match_notas(
        [_pedido(numeroloja=None, envio=envio)],
        [
            _nota("a", complemento=None, data_emissao=antes_demais),
            _nota("b", complemento=None, data_emissao=depois_demais),
        ],
    )["292001"]
    assert r.embalagem is None


def test_fallback_cpf_bordas_da_janela_casam():
    envio = date(2026, 8, 20)
    borda_antes = datetime(2026, 8, 20, 8, 0) - timedelta(days=JANELA_ANTES_DIAS)
    borda_depois = datetime(2026, 8, 20, 8, 0) + timedelta(days=JANELA_DEPOIS_DIAS)
    r = match_notas(
        [_pedido(numeroloja=None, envio=envio)],
        [
            _nota("emb", complemento=None, data_emissao=borda_antes),
            _nota(
                "prod",
                conta_cnpj=CNPJ_AVULSA,
                complemento=None,
                data_emissao=borda_depois,
            ),
        ],
    )["292001"]
    assert r.embalagem == "emb"
    assert r.produto == "prod"


def test_fallback_sem_envio_nao_casa():
    r = match_notas(
        [_pedido(numeroloja=None, envio=None)],
        [_nota("emb", complemento=None)],
    )["292001"]
    assert r.embalagem is None


# ─── exclusividade e prioridade ───────────────────────────────────────


def test_nota_usada_por_um_pedido_so():
    """Dois pedidos com o mesmo CPF disputando uma nota: só um leva."""
    p1 = _pedido(numero="A", numeroloja=None, envio=date(2026, 8, 20))
    p2 = _pedido(numero="B", numeroloja=None, envio=date(2026, 8, 21))
    r = match_notas([p1, p2], [_nota("unica", complemento=None)])
    donos = [n for n in ("A", "B") if r[n].embalagem == "unica"]
    assert len(donos) == 1


def test_exata_tem_prioridade_sobre_cpf():
    """A nota com complemento do pedido A fica com A (chave exata), mesmo que
    o pedido B (mesmo CPF, envio mais recente) rode antes no fallback."""
    p_a = _pedido(numero="A", numeroloja="LOJA-A", envio=date(2026, 8, 18))
    p_b = _pedido(numero="B", numeroloja=None, envio=date(2026, 8, 21))
    nota = _nota("da-loja-a", complemento="LOJA-A")
    r = match_notas([p_a, p_b], [nota])
    assert r["A"].embalagem == "da-loja-a"
    assert r["A"].embalagem_via == "pedido"
    assert r["B"].embalagem is None


def test_empate_resolve_pela_emissao_mais_proxima_do_envio():
    """Duas notas candidatas da mesma conta: ganha a emitida mais perto."""
    envio = date(2026, 8, 20)
    r = match_notas(
        [_pedido(numeroloja=None, envio=envio)],
        [
            _nota(
                "longe", complemento=None, data_emissao=datetime(2026, 8, 12, 9, 0)
            ),
            _nota(
                "perto", complemento=None, data_emissao=datetime(2026, 8, 19, 9, 0)
            ),
        ],
    )["292001"]
    assert r.embalagem == "perto"


# ─── filtros ──────────────────────────────────────────────────────────


def test_situacao_nao_emitida_fica_de_fora():
    """Situações fora de {5,6,7} (pendente=1, cancelada=2...) não casam nem
    pela chave exata."""
    r = match_notas(
        [_pedido()],
        [
            _nota("pendente", situacao=1),
            _nota("cancelada", situacao=2),
            _nota("ok", situacao=6),
        ],
    )["292001"]
    assert r.embalagem == "ok"
    assert r.produto is None


def test_sem_store_cnpj_toda_nota_vira_produto():
    """Pedido de loja sem CNPJ cadastrado: não dá pra dizer que a nota é da
    empresa dona → classifica como produto (não inventa embalagem)."""
    r = match_notas(
        [_pedido(store_cnpj=None)],
        [_nota("n1", conta_cnpj=CNPJ_LOJA)],
    )["292001"]
    assert r.embalagem is None
    assert r.produto == "n1"


def test_pedido_sem_cpf_e_sem_numeroloja_nao_casa():
    r = match_notas(
        [_pedido(numeroloja=None, cpf=None)],
        [_nota("n1", complemento=None)],
    )["292001"]
    assert r.embalagem is None
    assert r.produto is None
