"""SAC do site na garantia (28/10/2026; o caso do SAC desde 06/11/2026): o
formulário de uranyx.com.br/sac vira um caso (`garantia_sac`) e liga à
garantia pelo nº do pedido da plataforma, pela NF ou pelo CPF + o produto —
o 1º passo que acha UMA garantia; o resto fica em "SAC sem pedido"."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import BlingOrder, Garantia, GarantiaConversa, GarantiaLog, GarantiaSac, UserRole
from app.models.atendimento import AtendimentoConversa, AtendimentoMensagem
from app.services import garantia as svc
from app.services import garantia_sac as sac
from tests.test_garantia_auto import AGORA, COMPRA, CPF, _garantias, _nf, _pedido, _rodar

pytestmark = pytest.mark.asyncio

HOJE = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def _hoje(monkeypatch):
    monkeypatch.setattr(svc, "hoje", lambda: HOJE)


def _formulario(
    cpf="529.982.247-25",
    pedido="261001ABCD1234",
    nf="2611",
    local="Shopee",
    protocolo="US-26-0009",
    modelo="T8",
):
    """O corpo do e-mail do site como a ponte grava: o rótulo colado no valor,
    um campo por linha; as fotos com o nome do arquivo na linha de baixo."""
    return "\n".join(
        [
            "SAC de Produto · Uranyx · 123",
            protocolo,
            "Aberto em 20/10/2026 às 10:00 (Brasília).",
            "1. Dados do comprador",
            "Nome completo (conforme consta na compra)Maria da Nota (maria_n)",
            f"CPF{cpf}",
            "E-mailmaria@example.com",
            "Telefone/WhatsApp(19) 98888-7777",
            "CEP13400-000",
            "Endereço completoRua das Flores, 10",
            "Centro, Piracicaba/SP",
            "2. Dados da compra",
            f"Local da compra (site, marketplace ou loja física){local}",
            "Nome da loja/vendedor—",
            f"Número do pedido{pedido}",
            "Data da compra01/10/2026",
            "Data de recebimento05/10/2026",
            f"Número da nota fiscal{nf}",
            "Valor pago—",
            "3. Dados do produto",
            f"Modelo{modelo}",
            "CorPreto",
            "Voltagem (127V ou 220V)—",
            "Número de série ou lote (etiqueta localizada na base do aparelho, se houver)SN123",
            "4. Descrição do defeito",
            "Data em que o problema foi identificado18/10/2026",
            "Descrição detalhada do defeitoO celular não liga depois da atualização",
            "Correios entregou a caixa amassada",
            "O produto sofreu queda, contato com líquidos ou foi utilizado em voltagem"
            " diferente da indicada?Não",
            "Tipo de conexão utilizada (tomada direta, extensão ou adaptador)—",
            "5. Documentação",
            "Foto do produto",
            "IMG_0001.jpg",
            "Foto da etiqueta (com o número de série)",
            "etiqueta.jpg",
            "Foto da embalagem—",
            "Foto do defeito 1",
            "defeito-1.jpg",
            "Foto do defeito 2",
            "defeito-2.jpg",
            "Vídeo",
            "video.mp4",
            "Uranyx · atendimento ao cliente",
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


def test_le_o_formulario_inteiro():
    c = sac.campos_do_formulario(_formulario())
    assert c == {
        "protocolo": "US-26-0009",
        "aberto_em": "20/10/2026 às 10:00 (Brasília).",
        "nome": "Maria da Nota (maria_n)",
        "cpf": "529.982.247-25",
        "email": "maria@example.com",
        "telefone": "(19) 98888-7777",
        "cep": "13400-000",
        "endereco": "Rua das Flores, 10\nCentro, Piracicaba/SP",
        "local_compra": "Shopee",
        "loja_vendedor": None,
        "pedido": "261001ABCD1234",
        "data_compra": "01/10/2026",
        "data_recebimento": "05/10/2026",
        "nota_fiscal": "2611",
        "valor_pago": None,
        "modelo": "T8",
        "cor": "Preto",
        "voltagem": None,
        "serie": "SN123",
        "data_problema": "18/10/2026",
        # A continuação fica no campo ("Correios…" não vira "Cor").
        "descricao": "O celular não liga depois da atualização\nCorreios entregou a caixa amassada",
        "queda_liquido_voltagem": "Não",
        "tipo_conexao": None,
        "arquivos": {
            "Foto do produto": "IMG_0001.jpg",
            "Foto da etiqueta": "etiqueta.jpg",
            "Foto da embalagem": None,
            "Foto do defeito 1": "defeito-1.jpg",
            "Foto do defeito 2": "defeito-2.jpg",
            "Vídeo": "video.mp4",
        },
    }
    # Com linha em branco entre os campos e o nome do arquivo colado.
    espaçado = "\n\n".join(_formulario().splitlines()).replace(
        "Foto do produto\n\nIMG_0001.jpg", "Foto do produtoIMG_0001.jpg"
    )
    d = sac.campos_do_formulario(espaçado)
    assert (d["cpf"], d["serie"], d["arquivos"]["Foto do produto"]) == (
        "529.982.247-25",
        "SN123",
        "IMG_0001.jpg",
    )
    assert d["descricao"].startswith("O celular não liga") and "Correios" in d["descricao"]
    assert sac.campos_do_formulario(None)["arquivos"] == {}


def test_modelo_casa_com_o_produto():
    assert sac.modelo_casa("Celular Uranyx T8", ["T8 - Preto", None])
    assert sac.modelo_casa("t8", ["Celular Uranyx T8 128GB"])
    assert sac.modelo_casa("A17 Pro Max", ["Uranyx A17 Pro Max - Laranja"])
    assert sac.modelo_casa("a17promax", ["A17 Pro Max"])
    assert not sac.modelo_casa("Airfryer 4L", ["Celular Uranyx T8 128GB"])
    assert not sac.modelo_casa(None, ["T8"]) and not sac.modelo_casa("celular", ["T8"])


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


async def _casos(db):
    return list((await db.execute(select(GarantiaSac).order_by(GarantiaSac.id))).scalars())


async def test_liga_pelo_pedido_da_plataforma(db):
    """1º passo: o nº do pedido na plataforma (o que o cliente tem na mão)."""
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    cid = await _protocolo(db, _formulario(nf="—"))
    assert (await _rodar(db)).conversas_ligadas == 1
    assert await _ligadas(db) == {cid}
    [g] = await _garantias(db)
    [caso] = await _casos(db)
    assert (caso.garantia_id, caso.vinculo, caso.protocolo) == (g.id, "pedido", "US-26-0009")
    assert caso.vinculado_em is not None and caso.vinculado_por is None
    assert caso.confirmado_em is None  # a pessoa confirma
    assert caso.campos["modelo"] == "T8" and caso.recebido_em == COMPRA + timedelta(days=10)
    log = (
        await db.execute(select(GarantiaLog).where(GarantiaLog.acao == "vinculou_sac"))
    ).scalar_one()
    assert log.garantia_id == g.id and log.user_id is None and "US-26-0009" in log.detalhe
    # Rodar de novo não cria outro caso nem outra linha no log.
    await _rodar(db)
    assert len(await _casos(db)) == 1
    assert (
        len((await db.execute(select(GarantiaLog).where(GarantiaLog.acao == "vinculou_sac"))).all())
        == 1
    )


async def test_liga_pelo_cpf_e_pela_nota(db):
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    cid = await _protocolo(db, _formulario(pedido="—"))
    assert (await _rodar(db)).conversas_ligadas == 1
    assert await _ligadas(db) == {cid}
    assert [c.vinculo for c in await _casos(db)] == ["nf"]


async def test_liga_pela_nota_de_embalagem_do_mesmo_pedido(db):
    await _pedido(db)
    await _nf(db)  # produto 2611
    await _nf(db, numero="1946", valor=17)  # a de embalagem, que o cliente recebe por fora
    await _rodar(db)
    cid = await _protocolo(db, _formulario(pedido="—", nf="1946"))
    await _rodar(db)
    assert await _ligadas(db) == {cid}


async def test_cpf_e_produto_e_o_que_nao_liga_fica_sem_pedido(client, db, make_user, auth_as):
    """3º passo: o CPF + o produto (o "Modelo" do formulário casa com o
    produto da garantia). Nota e pedido de outra compra, e o modelo de outro
    produto: não liga — fica em "SAC sem pedido" (também a compra em outro
    vendedor)."""
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    # CPF certo, nota e pedido de outra compra, o modelo é o do produto: liga.
    c1 = await _protocolo(
        db,
        _formulario(pedido="999999XYZ", nf="777", protocolo="US-26-0011", modelo="Uranyx T8"),
        numero="US-26-0011",
    )
    # CPF certo, outro produto: não liga.
    await _protocolo(
        db,
        _formulario(pedido="—", nf="—", protocolo="US-26-0012", modelo="Airfryer 4L"),
        numero="US-26-0012",
    )
    await _rodar(db)
    assert await _ligadas(db) == {c1}
    casos = {c.protocolo: c for c in await _casos(db)}
    assert casos["US-26-0011"].vinculo == "cpf_produto"
    assert (casos["US-26-0012"].garantia_id, casos["US-26-0012"].vinculo) == (None, None)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = (await client.get("/api/garantias/sac/sem-pedido")).json()
    assert r["total"] == 1
    [item] = r["itens"]
    assert (item["protocolo"], item["modelo"], item["pedido_citado"]) == (
        "US-26-0012",
        "Airfryer 4L",
        None,
    )
    assert "cpf" not in item
    lista = (await client.post("/api/garantias/lista", json={})).json()
    assert lista["sac_sem_pedido"] == 1


async def test_mais_de_uma_garantia_nao_liga(db):
    """A NF de 2 a 4 dígitos se repete entre séries e emitentes: duas
    garantias com o mesmo número → ambíguo, a pessoa vincula à mão."""
    await _pedido(db)
    await _nf(db)
    await _pedido(db, numero="310009", numeroloja="261009XXXX0009")
    await _nf(db, pedido="310009", numero="2611", serie="3", doc="11144477735")
    await _rodar(db)
    assert len(await _garantias(db)) == 2
    await _protocolo(db, _formulario(pedido="—", cpf="—"))
    await _rodar(db)
    [caso] = await _casos(db)
    assert caso.garantia_id is None and await _ligadas(db) == set()


async def test_email_comum_de_sac_nao_abre_caso(db):
    """Só o formulário (o protocolo do SAC + "SAC de Produto" no 1º e-mail)
    abre caso. O e-mail que cita o protocolo sem o formulário, o das dúvidas
    (UDS) e o de sac@ sem protocolo ficam na fila de E-mail."""
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    await _protocolo(
        db, "Bom dia, sobre o protocolo US-26-0020: CPF 529.982.247-25", numero="US-26-0020"
    )
    await _protocolo(db, _formulario(protocolo="UDS-26-0021"), numero="UDS-26-0021")
    db.add(
        AtendimentoConversa(
            plataforma="site",
            canal="email",
            externo_id="mail:abc-reclame-aqui",
            ultima_mensagem_em=AGORA - timedelta(minutes=5),
        )
    )
    await db.commit()
    await _rodar(db)
    assert await _casos(db) == [] and await _ligadas(db) == set()


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


async def _mensagem(db, conversa_id, texto, *, autor="cliente", tipo="texto", dias=11):
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa_id,
            autor=autor,
            origem=autor if autor != "loja" else "externo",
            tipo=tipo,
            texto=texto,
            enviada_em=COMPRA + timedelta(days=dias),
        )
    )
    await db.commit()


async def test_formulario_que_nao_serve_nao_busca_nas_outras_mensagens(db):
    """Revisão de 28/10: o protocolo é sequencial — outra pessoa escreve com o
    protocolo de A e manda o CPF e o pedido DELA; a equipe cita outro cliente
    numa nota. Nada disso é o formulário: não liga."""
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    cid = await _protocolo(db, _formulario(cpf="111.444.777-35", pedido="—", nf="—"))
    await _mensagem(db, cid, "CPF: 529.982.247-25\nNúmero do pedido: 261001ABCD1234")
    await _mensagem(
        db,
        cid,
        "CPF 529.982.247-25\nNúmero da nota fiscal 2611",
        autor="loja",
        tipo="nota",
        dias=12,
    )
    await _rodar(db)
    assert await _ligadas(db) == set()


async def test_formulario_do_cliente_liga_e_so_ele_vira_o_cartao(client, db, make_user, auth_as):
    """A ponte grava o formulário (de sac@ para sac@ ou pelo Brevo) como fala do
    CLIENTE: liga igual, e o cartão sai pelo conteúdo, não pelo autor — o aviso
    de sistema que entra depois na conversa não vira cartão."""
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    cid = await _protocolo(db, _formulario(), autor="cliente")
    await _mensagem(db, cid, "Chamado US-26-0010 agrupado neste por Fulano.", autor="sistema")
    await _rodar(db)
    assert await _ligadas(db) == {cid}
    [g] = await _garantias(db)
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    assert [(m["autor"], m["formulario_sac"]) for m in h["mensagens"]] == [
        ("cliente", True),
        ("sistema", False),
    ]


async def test_outra_pessoa_escreveu_no_protocolo_sai_da_garantia(client, db, make_user, auth_as):
    """Ligada certa; depois outro endereço escreve com o protocolo (a ponte põe
    o alerta): some do histórico na hora, o robô desliga e não liga de novo."""
    auth_as(await make_user(role=UserRole.ADMIN))
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    cid = await _protocolo(db, _formulario())
    await _rodar(db)
    assert await _ligadas(db) == {cid}
    conv = await db.get(AtendimentoConversa, cid)
    conv.dados = {"mail": {**conv.dados["mail"], "alertas": ["outro_remetente"]}}
    await db.commit()
    [g] = await _garantias(db)
    h = (await client.get(f"/api/garantias/{g.id}/historico")).json()
    assert (h["conversas"], h["mensagens"]) == ([], [])
    await _rodar(db)
    assert await _ligadas(db) == set()
    # O caso (não confirmado) volta para SAC sem pedido.
    [caso] = await _casos(db)
    assert (caso.garantia_id, caso.vinculo) == (None, None)
    await _rodar(db)
    assert await _ligadas(db) == set()


async def test_email_do_site_nao_liga_pelo_pedido_citado(db):
    """O e-mail do site que cita o pedido (a ponte grava o pedido citado na
    conversa) não liga pelo pedido: no SAC só o CPF do formulário liga."""
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    db.add(
        AtendimentoConversa(
            plataforma="site",
            canal="email",
            externo_id="mail-site-teste",
            pedido_marketplace="261001ABCD1234",
            ultima_mensagem_em=AGORA - timedelta(minutes=5),
        )
    )
    await db.commit()
    await _rodar(db)
    assert await _ligadas(db) == set()


# ── Revisão de 06/11/2026: o formulário de uma pessoa na garantia de outra ──


def test_nf_do_formulario_nao_junta_numeros_separados():
    """ "NF 2611 série 1" juntava os dígitos (26111) — a NF de outra pessoa. Um
    número só (com ponto de milhar e zero à esquerda) continua valendo."""
    assert sac.nf_do_formulario({"nota_fiscal": "NF 2611 série 1"}) is None
    assert sac.nf_do_formulario({"nota_fiscal": "000.002.611"}) == "2611"
    assert sac.nf_do_formulario({"nota_fiscal": "NF-e nº 2611"}) == "2611"
    assert sac.nf_do_formulario({"nota_fiscal": None}) is None
    assert sac.campos_do_sac(_formulario(nf="NF 2611 série 1"))["nota_fiscal"] is None


async def test_nf_e_pedido_do_bling_so_ligam_com_o_cpf_da_garantia(db):
    """O nº da NF e o do Bling se repetem entre os 13 CNPJs e com outros
    vendedores (o formulário aceita a compra em outra loja). Sem o CPF do
    formulário — ou com o CPF de outra pessoa — eles não ligam: fica em "SAC
    sem pedido". O nº longo da plataforma liga sem o CPF."""
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    sem_cpf = {"cpf": "—", "pedido": "—", "nf": "2611"}
    for numero, campos in (
        ("US-26-0031", sem_cpf),
        ("US-26-0032", {"cpf": "111.444.777-35", "pedido": "310001", "nf": "2611"}),
        ("US-26-0033", {"cpf": "—", "pedido": "310001", "nf": "—"}),
    ):
        await _protocolo(db, _formulario(protocolo=numero, **campos), numero=numero)
    await _rodar(db)
    assert await _ligadas(db) == set()
    assert [c.garantia_id for c in await _casos(db)] == [None, None, None]
    # Com o CPF da garantia, o nº do Bling liga (pelo pedido)...
    pelo_bling = await _protocolo(
        db, _formulario(pedido="310001", nf="—", protocolo="US-26-0034"), numero="US-26-0034"
    )
    # ...e o nº da plataforma (longo) liga até sem o CPF.
    sem_cpf_longo = await _protocolo(
        db, _formulario(cpf="—", nf="—", protocolo="US-26-0035"), numero="US-26-0035"
    )
    await _rodar(db)
    assert await _ligadas(db) == {pelo_bling, sem_cpf_longo}
    vinculos = {c.protocolo: c.vinculo for c in await _casos(db)}
    assert (vinculos["US-26-0034"], vinculos["US-26-0035"]) == ("pedido", "pedido")


async def test_nº_curto_da_plataforma_so_liga_com_o_cpf(db):
    """O nº da plataforma curto (menos de 10 caracteres) pode ser o pedido de
    outra loja: sem o CPF não liga; com o CPF da garantia, liga."""
    await _pedido(db, numeroloja="AB12345")
    await _nf(db)
    await _rodar(db)
    await _protocolo(
        db,
        _formulario(cpf="—", pedido="AB-12345", nf="—", protocolo="US-26-0036"),
        numero="US-26-0036",
    )
    await _rodar(db)
    assert await _ligadas(db) == set()
    cid = await _protocolo(
        db, _formulario(pedido="ab12345", nf="—", protocolo="US-26-0037"), numero="US-26-0037"
    )
    await _rodar(db)
    assert await _ligadas(db) == {cid}


async def test_passo_ambiguo_segue_para_o_proximo(db):
    """Duas garantias do mesmo pedido da plataforma (duas NFs de produto): o
    pedido é ambíguo e não liga — a NF (com o CPF) acha uma só e liga."""
    ids = []
    for nf in ("2611", "2612"):
        g = Garantia(
            origem="robo",
            pedido_bling="310001",
            pedido_marketplace="261001ABCD1234",
            loja="L1",
            plataforma="shopee",
            conta="loja teste",
            nf_numero=nf,
            nf_serie="2",
            cliente_nome="Maria da Nota",
            cpf=CPF,
            itens=[{"sku": "dg053", "descricao": "Celular Uranyx T8", "quantidade": 1}],
        )
        db.add(g)
        await db.flush()
        ids.append(g.id)
    await db.commit()
    cid = await _protocolo(db, _formulario(nf="2612"))
    await sac.sincronizar_sac(db)
    await db.commit()
    [caso] = await _casos(db)
    assert (caso.garantia_id, caso.vinculo) == (ids[1], "nf")
    assert await _ligadas(db) == {cid}


async def test_cancelado_com_sac_confirmado_fica(db):
    """O pedido cancelado no Bling depois do reembolso: a garantia com o caso
    do SAC confirmado ("Garantia acionada") não é descartada — o caso não some
    da aba Atendimento sem encerramento. O caso ainda não confirmado da
    descartada volta para "SAC sem pedido"."""
    await _pedido(db)
    await _nf(db)
    await _pedido(db, numero="310002", numeroloja="261002ABCD2222")
    await _nf(db, pedido="310002", numero="2622")
    await _rodar(db)
    por_pedido = {g.pedido_bling: g for g in await _garantias(db)}
    g1, g2 = por_pedido["310001"], por_pedido["310002"]
    c1 = await _protocolo(db, _formulario(protocolo="US-26-0041"), numero="US-26-0041")
    await _protocolo(
        db,
        # Outro produto: o CPF + produto não acha a outra garantia da Maria.
        _formulario(pedido="261002ABCD2222", nf="2622", protocolo="US-26-0042", modelo="Fone"),
        numero="US-26-0042",
    )
    await _rodar(db)
    casos = {c.protocolo: c for c in await _casos(db)}
    assert (casos["US-26-0041"].garantia_id, casos["US-26-0042"].garantia_id) == (g1.id, g2.id)
    casos["US-26-0041"].confirmado_em = AGORA
    for linha in (await db.execute(select(BlingOrder))).scalars():
        linha.situacao = "12"
    await db.commit()
    assert (await _rodar(db)).descartadas == 1
    await db.refresh(g1)
    await db.refresh(g2)
    assert g1.descartada_em is None and g2.descartada_em is not None
    gid1 = g1.id
    db.expire_all()
    k1, k2 = await _casos(db)
    assert (k1.garantia_id, k2.garantia_id, k2.vinculo) == (gid1, None, None)
    assert await _ligadas(db) == {c1}
    # A descartada não volta a ligar.
    await sac.sincronizar_sac(db)
    await db.commit()
    await db.refresh(k2)
    assert k2.garantia_id is None


async def test_vinculo_a_mao_e_confirmado_ficam_com_outra_pessoa(client, db, make_user, auth_as):
    """A decisão da pessoa (vincular à mão, confirmar) vale mais que o alerta
    de outra pessoa no protocolo: o robô não desfaz (antes o à mão voltava
    para "SAC sem pedido" toda rodada) e a conversa continua no histórico. O
    vínculo à mão de uma conversa com outra pessoa é recusado (409)."""
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    await _pedido(db)
    await _nf(db)
    await _rodar(db)
    [g] = await _garantias(db)
    gid = g.id
    sem = {"cpf": "—", "pedido": "—", "nf": "—"}
    a_mao = await _protocolo(db, _formulario(protocolo="US-26-0051", **sem), numero="US-26-0051")
    confirmado = await _protocolo(db, _formulario(protocolo="US-26-0052"), numero="US-26-0052")
    outra = await _protocolo(db, _formulario(protocolo="US-26-0053", **sem), numero="US-26-0053")
    await _rodar(db)
    casos = {c.protocolo: c for c in await _casos(db)}
    r = await client.post(
        f"/api/garantias/sac/{casos['US-26-0051'].id}/vincular", json={"garantia_id": gid}
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/garantias/sac/{casos['US-26-0052'].id}/confirmar", json={})
    assert r.status_code == 200, r.text
    for cid in (a_mao, confirmado, outra):
        conv = await db.get(AtendimentoConversa, cid)
        await db.refresh(conv)
        conv.dados = {"mail": {**conv.dados["mail"], "alertas": ["outro_remetente"]}}
    await db.commit()
    db.expire_all()  # o Confirmar e o vínculo foram gravados pela rota
    await _rodar(db)
    await sac.sincronizar_sac(db)
    await db.commit()
    db.expire_all()
    await db.refresh(admin)
    casos = {c.protocolo: c for c in await _casos(db)}
    assert (casos["US-26-0051"].garantia_id, casos["US-26-0051"].vinculo) == (gid, "manual")
    assert casos["US-26-0052"].garantia_id == gid
    assert await _ligadas(db) == {a_mao, confirmado}
    h = (await client.get(f"/api/garantias/{gid}/historico")).json()
    assert {c["id"] for c in h["conversas"]} == {str(a_mao), str(confirmado)}
    assert len(h["mensagens"]) == 2
    r = await client.post(
        f"/api/garantias/sac/{casos['US-26-0053'].id}/vincular", json={"garantia_id": gid}
    )
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sac_outra_pessoa")
    db.expire_all()
    await db.refresh(admin)
    assert {c.protocolo: c.garantia_id for c in await _casos(db)}["US-26-0053"] is None


def test_comprovante_e_mais_fotos_do_defeito():
    """O /sac aceita até 4 fotos do defeito e o comprovante (06/11/2026)."""
    texto = _formulario().replace(
        "Vídeo\nvideo.mp4",
        "Foto do defeito 3\ndefeito-3.jpg\nComprovante de compra\nnota.pdf\nVídeo\nvideo.mp4",
    )
    arquivos = sac.campos_do_formulario(texto)["arquivos"]
    assert arquivos["Foto do defeito 3"] == "defeito-3.jpg"
    assert arquivos["Comprovante"] == "nota.pdf"
    assert arquivos["Vídeo"] == "video.mp4"
