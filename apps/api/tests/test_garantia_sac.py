"""SAC do site na garantia (28/10/2026): o protocolo do formulário de
uranyx.com.br/sac liga à garantia pelo CPF + NF (ou pedido) do formulário."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import GarantiaConversa, UserRole
from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.services import garantia as svc
from app.services import garantia_sac as sac
from tests.test_garantia_auto import AGORA, COMPRA, CPF, _garantias, _nf, _pedido, _rodar

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def _hoje(monkeypatch):
    monkeypatch.setattr(svc, "hoje", lambda: HOJE)


def _formulario(cpf="529.982.247-25", pedido="261001ABCD1234", nf="2611", local="Shopee"):
    """O corpo do e-mail do site como a ponte grava: o rótulo colado no valor."""
    return "\n".join(
        [
            "SAC de Produto · Uranyx · 123",
            "US-26-0009",
            "Aberto em 20/10/2026 às 10:00 (Brasília).",
            "1. Dados do comprador",
            "Nome completo (conforme consta na compra)Maria da Nota",
            f"CPF{cpf}",
            "E-mailmaria@example.com",
            "2. Dados da compra",
            f"Local da compra (site, marketplace ou loja física){local}",
            "Nome da loja/vendedor—",
            f"Número do pedido{pedido}",
            "Data da compra01/10/2026",
            f"Número da nota fiscal{nf}",
            "Valor pago—",
            "4. Descrição do defeito",
            "Descrição detalhada do defeitoO celular não liga depois da atualização",
        ]
    )


def test_le_os_campos_do_formulario():
    c = sac.campos_do_sac(_formulario())
    assert c == {
        "cpf": CPF,
        "nota_fiscal": "2611",
        "pedido": "261001ABCD1234",
        "local_compra": "Shopee",
    }
    # O formulário de teste da equipe (22/10): pedido "9" e NF vazia.
    t = sac.campos_do_sac(_formulario(pedido="9", nf=" (se disponível)—"))
    assert (t["pedido"], t["nota_fiscal"]) == (None, None)
    # CPF inválido não vale; zero à esquerda da NF sai.
    assert sac.campos_do_sac(_formulario(cpf="111.111.111-11"))["cpf"] is None
    assert sac.campos_do_sac(_formulario(nf="0002611"))["nota_fiscal"] == "2611"


async def _protocolo(db, texto, numero="US-26-0009", autor="sistema"):
    c = AtendimentoConversa(
        plataforma="site",
        canal="email",
        externo_id=f"mail-protocolo:{numero}",
        dados={"mail": {"protocolo": numero, "marca": "uranyx"}},
        ultima_mensagem_em=AGORA - timedelta(minutes=5),
    )
    db.add(c)
    await db.flush()
    db.add(
        AtendimentoMensagem(
            conversa_id=c.id,
            autor=autor,
            origem=autor,
            tipo="texto",
            texto=texto,
            enviada_em=COMPRA + timedelta(days=10),
        )
    )
    await db.commit()
    return c.id


async def _ligadas(db):
    return set((await db.execute(select(GarantiaConversa.conversa_id))).scalars())


async def test_liga_pelo_cpf_e_pela_nota(db):
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    cid = await _protocolo(db, _formulario(pedido="—"))
    assert (await _rodar(db)).conversas_ligadas == 1
    assert await _ligadas(db) == {cid}


async def test_liga_pela_nota_de_embalagem_do_mesmo_pedido(db):
    await _pedido(db)
    await _nf(db)  # produto 2611
    await _nf(db, numero="1946", valor=17)  # a de embalagem, que o cliente recebe por fora
    await _rodar(db)
    cid = await _protocolo(db, _formulario(pedido="—", nf="1946"))
    await _rodar(db)
    assert await _ligadas(db) == {cid}


async def test_pedido_sem_o_cpf_nao_liga_e_cpf_sem_nota_nem_pedido_tambem_nao(db):
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    # Pedido certo, CPF de outra pessoa: nunca liga.
    await _protocolo(db, _formulario(cpf="111.444.777-35"), numero="US-26-0010")
    # CPF certo, nota e pedido de outra compra: não liga.
    await _protocolo(db, _formulario(pedido="999999XYZ", nf="777"), numero="US-26-0011")
    await _rodar(db)
    assert await _ligadas(db) == set()


async def test_historico_mostra_o_protocolo(client, db, make_user, auth_as):
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    await _protocolo(db, _formulario())
    await _rodar(db)
    [g] = await _garantias(db)
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    [conv] = h["conversas"]
    assert (conv["protocolo"], conv["plataforma"], conv["canal"]) == ("US-26-0009", "site", "email")
    assert "CPF529.982.247-25" in h["mensagens"][0]["texto"]
    assert datetime.fromisoformat(h["mensagens"][0]["enviada_em"]) > COMPRA.replace(tzinfo=UTC)
