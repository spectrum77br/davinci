"""Limpa o TEXTO legado gravado no "Link envio" das devoluções

Caso 292128 (Shopee ATV, 22/09/2026): a linha tinha o texto "nao ha" gravado no
`link_envio`, de antes da trava de formato (8426c57, 21/09). A tela reenvia o
campo em todo salvamento, então a linha inteira ficou impossível de salvar — o
produto não voltava ao estoque e o único sinal era um banner no topo da página.
6699f53 destravou a tela (texto legado não barra mais o save e fica de fora do
payload); esta migração tira o texto do banco pra a marca vermelha sumir.

DUAS TRAVAS, porque limpar demais criaria um congelamento novo:

1. Só mexe onde o Link de envio NÃO é obrigatório. Em mala/eletro com motivo que
   abre chamado (`chamados_devolucao.link_envio_obrigatorio`) o texto lixo é hoje
   o que faz a linha passar na trava de obrigatoriedade — apagar deixaria a linha
   presa do outro lado, pedindo um link que só a expedição tem. Essas ficam como
   estão (vermelhas, mas editáveis desde 6699f53) e saem contadas no log, pra a
   equipe repor o link de verdade.
2. Texto que pode ser informação de verdade (tem "/", "http", "drive", "@" ou
   passa de 30 caracteres) não é jogado fora: vai pro fim da Observação como
   "[link envio antigo: …]". O resto é o lixo conhecido ("nao ha", "sem link",
   "nao recebido") e sai sem deixar rastro na tela — mas TODOS os valores
   apagados são impressos no log do deploy, então nada é irrecuperável.

Migração de dados: o downgrade não recria o texto (não há de onde), e não
precisa — o campo voltar a ser nulo não quebra nada.
"""

import unicodedata
from collections.abc import Sequence
from types import SimpleNamespace

import sqlalchemy as sa

from alembic import op

revision: str = "0304_link_envio_texto_legado"
down_revision: str | None = "0303_devolucao_mensagem_comprador"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"

# Marca na Observação pra a operação saber de onde veio o texto.
PREFIXO_OBS = "[link envio antigo: "


def _pode_ser_informacao(texto: str) -> bool:
    """Texto que talvez diga alguma coisa (caminho, endereço, anotação longa) —
    esse a gente guarda na Observação em vez de apagar. O lixo que a operadora
    digitava só pra passar na trava é curto e não tem caminho nenhum."""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    if any(marca in t for marca in ("/", "http", "drive", "@", "\\")):
        return True
    return len(t.strip()) > 30


def upgrade() -> None:
    # Import tardio: o alembic carrega TODAS as versões em qualquer comando
    # (`heads`, `current`, `upgrade`); um rename futuro no serviço não pode
    # travar o grafo inteiro de migrações.
    from app.services.chamados_devolucao import link_envio_obrigatorio

    bind = op.get_bind()
    linhas = bind.execute(
        sa.text(
            f"SELECT id, pedido_bling, sku, motivo_devolucao, link_envio, observacao "  # noqa: S608
            f"FROM {SCHEMA}.devolutions "
            "WHERE link_envio IS NOT NULL AND link_envio !~* '^https?://' "
            "ORDER BY created_at"
        )
    ).all()

    limpas = guardadas = mantidas_obrigatorias = 0
    for linha in linhas:
        texto = (linha.link_envio or "").strip()
        if not texto:
            continue
        dev = SimpleNamespace(sku=linha.sku, motivo_devolucao=linha.motivo_devolucao)
        if link_envio_obrigatorio(dev):
            mantidas_obrigatorias += 1
            print(
                f"[0304] MANTIDO (link obrigatório) pedido={linha.pedido_bling} "
                f"sku={linha.sku} motivo={linha.motivo_devolucao} texto={texto!r}"
            )
            continue

        observacao = linha.observacao
        if _pode_ser_informacao(texto):
            marca = f"{PREFIXO_OBS}{texto}]"
            observacao = f"{observacao.rstrip()}\n{marca}" if observacao else marca
            guardadas += 1
        limpas += 1
        print(f"[0304] LIMPO pedido={linha.pedido_bling} texto={texto!r}")
        bind.execute(
            sa.text(
                f"UPDATE {SCHEMA}.devolutions "  # noqa: S608
                "SET link_envio = NULL, observacao = :obs WHERE id = :id"
            ),
            {"obs": observacao, "id": linha.id},
        )

    print(
        f"[0304] link_envio com texto: {len(linhas)} | limpos: {limpas} "
        f"(desses, {guardadas} com o texto guardado na Observação) | "
        f"mantidos por serem de mala/eletro com link obrigatório: {mantidas_obrigatorias}"
    )


def downgrade() -> None:
    # Sem volta: o texto apagado existe só no log do deploy, e recriá-lo a
    # partir da marca na Observação seria inventar dado. Voltar o campo pra nulo
    # não quebra nada — a trava de obrigatoriedade já respeitou quem precisava
    # do link (esses não foram tocados).
    pass
