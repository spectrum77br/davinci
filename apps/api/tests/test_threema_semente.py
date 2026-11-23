"""`semear` e `conferir` do Cadastro dos envios Threema (tarefa 16, C5).

* invariante: depois de `semear --aplicar` de todas as fatias, `conferir` dá 0
  diferenças em todas as chaves e escopos (regras com 👤 inclusive);
* os grupos da semente nascem com a lista de hoje; o ID compartilhado fica com
  o usuário ativo (admin, depois o mais antigo); ID desconhecido vira contato
  com o nome do .env (ou "Sem nome"); ID torto fica de fora e aparece;
* sem `--aplicar` nada é gravado; rodar duas vezes não muda nada;
  `--sobrescrever` é recusado em chave migrada e refaz a semente velha;
  grupo com pessoas diferentes para o comando sem gravar nada;
* regra com 👤 e sem mensagem também é semeada; o Mac não é tocado;
* a linha de comando mascara os IDs e sai com 1 quando há diferença.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import (
    ThreemaContato,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaGrupo,
    ThreemaGrupoMembro,
    ThreemaInformarConfig,
    User,
    UserRole,
    UserStatus,
)
from app.services import threema_envios
from app.services.threema_catalogo import ENVIOS, G_AVISOS_GERAIS, G_ESTOQUE_NF, K
from tests.test_threema_resolver import desmigrar, migrar
from tests.threema_cadastro_apoio import (  # noqa: F401 — fixture
    ID,
    limpar_ouvidoria,
    montar,
)

pytestmark = pytest.mark.usefixtures("limpar_ouvidoria")


def _codigo_antes_da(monkeypatch, fatia: str) -> None:
    """A semente roda com o código de ANTES da fatia (§3.4 do desenho): as
    chaves dela ainda `migrado=False` — é o que deixa o `--sobrescrever`."""
    desmigrar(monkeypatch, *(d.chave for d in ENVIOS if d.fatia == fatia and d.migrado))


async def _semear(db, fatia, **kw):
    res = await threema_envios.semear(db, fatia=fatia, **kw)
    await db.commit()
    return res


async def _todas(db) -> None:
    for fatia in ("F1", "F2", "F3", "F4"):
        await _semear(db, fatia, aplicar=True)


async def _contagem(db) -> dict[str, int]:
    await db.commit()
    out = {}
    for modelo in (ThreemaEnvioDestinatario, ThreemaGrupo, ThreemaGrupoMembro, ThreemaContato):
        out[modelo.__tablename__] = (
            await db.execute(select(func.count()).select_from(modelo))
        ).scalar_one()
    out["semeados"] = (
        await db.execute(
            select(func.count())
            .select_from(ThreemaEnvio)
            .where(ThreemaEnvio.semeado_em.is_not(None))
        )
    ).scalar_one()
    return out


@pytest.mark.asyncio
async def test_semente_de_todas_as_fatias_e_conferir_sem_diferenca(db, monkeypatch):
    c = await montar(db, monkeypatch)
    # antes: tudo sem semente, e a conferência sem filtro não reprova
    antes = await threema_envios.conferir(db)
    assert {x.situacao for x in antes} == {"sem_semente"}
    await _todas(db)
    itens = await threema_envios.conferir(db)
    diferentes = [
        (x.chave, x.escopo, x.so_no_legado, x.so_no_cadastro)
        for x in itens
        if x.situacao != "igual"
    ]
    assert diferentes == []
    # todas as chaves do servidor gerenciadas, e o item 1 com as 2 regras de 👤
    gerenciados = {d.chave for d in ENVIOS if d.onde_roda == "servidor" and d.gerenciado}
    assert {x.chave for x in itens} == gerenciados
    escopos = {x.escopo for x in itens if x.chave == K.LOGISTICA_MENSAGEM_REGRA}
    assert escopos == {
        None,
        str(c.regras["com_pessoa"].id),
        str(c.regras["pessoa_sem_mensagem"].id),
    }
    # o Mac e os informativos não foram tocados
    linhas = {r.chave: r for r in (await db.execute(select(ThreemaEnvio))).scalars()}
    for d in ENVIOS:
        assert (linhas[d.chave].semeado_em is not None) == (d.chave in gerenciados), d.chave
    # a semente não mexe no liga/desliga, fora o envio cujo desligado de hoje
    # é o .env vazio: no cenário, só o da soma por família (o .env dele não
    # tem ninguém, como em produção) nasce desligado
    assert {d.chave for d in ENVIOS if linhas[d.chave].ativo is not None} == {
        K.PRODUTOS_VIGIA_SOMA_FAMILIA
    }
    assert linhas[K.PRODUTOS_VIGIA_SOMA_FAMILIA].ativo is False


@pytest.mark.asyncio
async def test_os_grupos_da_semente_nascem_com_a_lista_de_hoje(db, monkeypatch):
    c = await montar(db, monkeypatch)
    await _semear(db, "F1", aplicar=True)
    grupos = {g.nome: g for g in (await db.execute(select(ThreemaGrupo))).scalars()}
    assert set(grupos) == {G_AVISOS_GERAIS, G_ESTOQUE_NF, "TikTok e chamados"}

    async def membros(nome):
        ms = (
            await db.execute(
                select(ThreemaGrupoMembro)
                .where(ThreemaGrupoMembro.grupo_id == grupos[nome].id)
                .order_by(ThreemaGrupoMembro.ordem)
            )
        ).scalars()
        return [m.user_id for m in ms]

    u = c.usuarios
    assert await membros(G_ESTOQUE_NF) == [
        u[q].id for q in ("londres", "harry", "heisenberg", "cairo_sa")
    ]
    assert await membros(G_AVISOS_GERAIS) == [u[q].id for q in ("churchill", "londres", "cairo_sa")]
    # os envios do grupo apontam para o grupo (uma linha)
    linhas = (
        (
            await db.execute(
                select(ThreemaEnvioDestinatario).where(
                    ThreemaEnvioDestinatario.envio_chave == K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(x.tipo, x.grupo_id, x.criado_por) for x in linhas] == [
        ("grupo", grupos[G_ESTOQUE_NF].id, "semente F1")
    ]
    # envio sem grupo: uma linha por pessoa, na ordem (o Tuta: o thatcher)
    tuta = (
        (
            await db.execute(
                select(ThreemaEnvioDestinatario).where(
                    ThreemaEnvioDestinatario.envio_chave == K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(x.tipo, x.user_id) for x in tuta] == [("usuario", u["thatcher"].id)]


@pytest.mark.asyncio
async def test_id_compartilhado_fica_com_o_usuario_ativo_admin_primeiro(db, monkeypatch):
    c = await montar(db, monkeypatch)
    # M5: "cairo" suspenso e "cairo sa" ativo → cairo sa (sem aviso: só 1 ativo)
    # e dois ATIVOS com o mesmo ID: o admin vence, e o caso é impresso
    outro = User(
        open_id="email:outro@davinci-test.com",
        email="outro@davinci-test.com",
        name="outro londres",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        threema=ID["londres"],
    )
    db.add(outro)
    await db.commit()
    res = await _semear(db, "F1", aplicar=True)
    g = (
        await db.execute(select(ThreemaGrupo).where(ThreemaGrupo.nome == G_AVISOS_GERAIS))
    ).scalar_one()
    membros = (
        (
            await db.execute(
                select(ThreemaGrupoMembro.user_id).where(ThreemaGrupoMembro.grupo_id == g.id)
            )
        )
        .scalars()
        .all()
    )
    assert c.usuarios["londres"].id in membros and outro.id not in membros
    assert c.usuarios["cairo_sa"].id in membros and c.usuarios["cairo"].id not in membros
    assert any("2 usuários ativos" in caso and "ficou com londres" in caso for caso in res.casos)
    assert not any("cairo" in caso for caso in res.casos)


@pytest.mark.asyncio
async def test_id_desconhecido_vira_contato_e_id_torto_fica_de_fora(db, monkeypatch):
    await montar(db, monkeypatch)
    s = get_settings()
    monkeypatch.setattr(
        s, "shipment_muda_threema_recipients", f"{ID['so_env']},TSTX0010,ROMA,{ID['roma']}"
    )
    res = await _semear(db, "F1", chaves=[K.LOGISTICA_LOJA_MUDA], aplicar=True)
    contatos = {
        c.threema_id: (c.nome, c.origem)
        for c in (await db.execute(select(ThreemaContato))).scalars()
    }
    assert contatos[ID["so_env"]] == ("Só no env", "semeadura")
    assert contatos["TSTX0010"] == ("Sem nome (TS…)", "semeadura")
    assert contatos[ID["roma"]] == ("Roma", "migracao")  # o que já existia é reaproveitado
    # o contato novo também entra no espelho antigo (linha `diretorio`)
    espelho = (
        await db.execute(
            select(ThreemaInformarConfig.recipients).where(
                ThreemaInformarConfig.contexto == "diretorio"
            )
        )
    ).scalar_one()
    assert f"{ID['so_env']}:Só no env" in espelho.split(",")
    assert any("não tem o formato" in caso for caso in res.casos)
    # o ID torto aparece como diferença na conferência
    (item,) = await threema_envios.conferir(db, chaves=[K.LOGISTICA_LOJA_MUDA])
    assert item.situacao == "diferente"
    assert [x.id for x in item.so_no_legado] == ["ROMA"] and item.so_no_cadastro == []


@pytest.mark.asyncio
async def test_sem_aplicar_nada_e_gravado(db, monkeypatch):
    await montar(db, monkeypatch)
    antes = await _contagem(db)
    res = await _semear(db, "F2")
    assert not res.aplicado
    assert any(p.situacao == "semear" for p in res.planos)
    assert await _contagem(db) == antes


@pytest.mark.asyncio
async def test_rodar_duas_vezes_nao_muda_nada(db, monkeypatch):
    await montar(db, monkeypatch)
    await _semear(db, "F1", aplicar=True)
    depois_1 = await _contagem(db)
    ids_1 = sorted(
        str(x) for x in (await db.execute(select(ThreemaEnvioDestinatario.id))).scalars()
    )
    res = await _semear(db, "F1", aplicar=True)
    assert {p.situacao for p in res.planos} == {"ja_semeado"}
    assert await _contagem(db) == depois_1
    ids_2 = sorted(
        str(x) for x in (await db.execute(select(ThreemaEnvioDestinatario.id))).scalars()
    )
    assert ids_1 == ids_2


@pytest.mark.asyncio
async def test_sobrescrever_recusado_em_chave_migrada(db, monkeypatch):
    await montar(db, monkeypatch)
    migrar(monkeypatch, K.LOGISTICA_LOJA_MUDA)
    antes = await _contagem(db)
    with pytest.raises(threema_envios.CadastroError) as erro:
        await threema_envios.semear(db, fatia="F1", aplicar=True, sobrescrever=True)
    await db.rollback()
    res = erro.value.args[1]
    assert any("--sobrescrever recusado" in e and K.LOGISTICA_LOJA_MUDA in e for e in res.erros)
    assert await _contagem(db) == antes


@pytest.mark.asyncio
async def test_sobrescrever_refaz_a_semente_velha(db, monkeypatch):
    await montar(db, monkeypatch)
    _codigo_antes_da(monkeypatch, "F1")
    await _semear(db, "F1", aplicar=True)
    # o .env mudou depois da semente: a semente envelheceu
    monkeypatch.setattr(get_settings(), "nf_sem_estoque_threema_recipients", ID["joana"])
    itens = await threema_envios.conferir(db, fatia="F1")
    velhas = {x.chave for x in itens if x.situacao == "diferente"}
    assert K.LOGISTICA_VIGIA_AUTOMACAO in velhas
    # sem --sobrescrever: já semeado, nada muda
    res = await _semear(db, "F1", aplicar=True)
    assert {p.situacao for p in res.planos} == {"ja_semeado"}
    await _semear(db, "F1", aplicar=True, sobrescrever=True)
    itens = await threema_envios.conferir(db, fatia="F1")
    assert [x.chave for x in itens if x.situacao != "igual"] == []


@pytest.mark.asyncio
async def test_sobrescrever_nao_refaz_grupo_usado_por_outra_fatia(db, monkeypatch):
    await montar(db, monkeypatch)
    _codigo_antes_da(monkeypatch, "F1")
    await _semear(db, "F1", aplicar=True)
    await _semear(db, "F4", aplicar=True)  # item 1 usa "Avisos gerais" também
    monkeypatch.setattr(get_settings(), "threema_recipients", ID["joana"])
    with pytest.raises(threema_envios.CadastroError) as erro:
        await threema_envios.semear(db, fatia="F1", aplicar=True, sobrescrever=True)
    await db.rollback()
    erros = erro.value.args[1].erros
    assert any(G_AVISOS_GERAIS in e and K.LOGISTICA_MENSAGEM_REGRA in e for e in erros)


@pytest.mark.asyncio
async def test_grupo_com_pessoas_diferentes_para_sem_gravar(db, monkeypatch):
    await montar(db, monkeypatch)
    row = (
        await db.execute(
            select(ThreemaInformarConfig).where(
                ThreemaInformarConfig.contexto == "logistica_amazon"
            )
        )
    ).scalar_one()
    row.recipients = ID["joana"]
    await db.commit()
    antes = await _contagem(db)
    with pytest.raises(threema_envios.CadastroError) as erro:
        await threema_envios.semear(db, fatia="F2", aplicar=True)
    await db.rollback()
    assert any("'Amazon' sairia com pessoas diferentes" in e for e in erro.value.args[1].erros)
    assert await _contagem(db) == antes
    # e a linha de comando diz que nada foi gravado, com IDs mascarados
    saida: list[str] = []
    assert (
        await threema_envios.executar(["semear", "--fatia", "F2", "--aplicar"], saida.append) == 1
    )
    assert "RECUSADO, nada foi gravado" in saida[-1]
    assert not any(ID["joana"] in linha for linha in saida)


@pytest.mark.asyncio
async def test_regra_com_pessoa_e_sem_mensagem_tambem_e_semeada(db, monkeypatch):
    c = await montar(db, monkeypatch)
    await _semear(db, "F4", aplicar=True)
    linhas = (
        (
            await db.execute(
                select(ThreemaEnvioDestinatario).where(
                    ThreemaEnvioDestinatario.envio_chave == K.LOGISTICA_MENSAGEM_REGRA
                )
            )
        )
        .scalars()
        .all()
    )
    por_regra: dict = {}
    for x in linhas:
        por_regra.setdefault(x.logistica_status_id, []).append(x)
    assert set(por_regra) == {
        None,
        c.regras["com_pessoa"].id,
        c.regras["pessoa_sem_mensagem"].id,
    }
    assert [x.user_id for x in por_regra[c.regras["pessoa_sem_mensagem"].id]] == [
        c.usuarios["heisenberg"].id
    ]
    assert [x.tipo for x in por_regra[None]] == ["grupo"]  # o padrão: Avisos gerais


@pytest.mark.asyncio
async def test_linha_de_comando_mascara_e_reprova_a_diferenca(db, monkeypatch):
    await montar(db, monkeypatch)
    saida: list[str] = []
    # conferir sem filtro antes de semear: tudo sem semente, sai 0
    assert await threema_envios.executar(["conferir"], saida.append) == 0
    assert "sem semente: " in saida[-1]
    # com --fatia, sem semente reprova (é o portão da fatia)
    assert await threema_envios.executar(["conferir", "--fatia", "F1"], saida.append) == 1
    # semear: dry-run imprime o plano mascarado
    saida.clear()
    assert await threema_envios.executar(["semear", "--fatia", "F1"], saida.append) == 0
    texto = "\n".join(saida)
    assert "só o plano" in texto and "TS…" in texto
    for rid in ID.values():
        assert rid not in texto
    assert (
        await threema_envios.executar(["semear", "--fatia", "F1", "--aplicar"], saida.append) == 0
    )
    assert await threema_envios.executar(["conferir", "--fatia", "F1"], saida.append) == 0
    # alguém tira uma pessoa do grupo no Cadastro: a conferência mostra e reprova
    await db.execute(
        text(
            "DELETE FROM threema_grupo_membro WHERE user_id = "
            "(SELECT id FROM users WHERE name = 'heisenberg')"
        )
    )
    await db.commit()
    saida.clear()
    assert await threema_envios.executar(["conferir", "--fatia", "F1"], saida.append) == 1
    texto = "\n".join(saida)
    assert "só no legado: heisenberg (TS…)" in texto
    for rid in ID.values():
        assert rid not in texto
    assert await threema_envios.executar(["sincronizar"], saida.append) == 0
    assert "60 envios" in saida[-1]


@pytest.mark.asyncio
async def test_fatia_do_mac_e_chave_de_outra_fatia_sao_recusadas(db, monkeypatch):
    await montar(db, monkeypatch)
    with pytest.raises(threema_envios.CadastroError, match="fatia inválida"):
        await threema_envios.semear(db, fatia="mac")
    with pytest.raises(threema_envios.CadastroError, match="fora da seleção"):
        await threema_envios.semear(db, fatia="F1", chaves=[K.MARGEM_INFORMAR])
    with pytest.raises(threema_envios.CadastroError, match="fora da seleção"):
        await threema_envios.semear(db, fatia="F1", chaves=[K.DEVOLUCOES_CODIGOS_ML_TUTA])


@pytest.mark.asyncio
async def test_conferir_mostra_desligado_no_cadastro(db, monkeypatch):
    await montar(db, monkeypatch)
    await _semear(db, "F1", chaves=[K.LOGISTICA_LOJA_MUDA], aplicar=True)
    await db.execute(
        text("UPDATE threema_envio SET ativo = false WHERE chave = :c"),
        {"c": K.LOGISTICA_LOJA_MUDA},
    )
    await db.commit()
    (item,) = await threema_envios.conferir(db, chaves=[K.LOGISTICA_LOJA_MUDA])
    assert (item.situacao, item.ativo_legado, item.ativo_cadastro) == ("diferente", True, False)
    assert item.so_no_legado == [] and item.so_no_cadastro == []


@pytest.mark.asyncio
async def test_env_vazio_nasce_desligado_e_o_sobrescrever_acompanha_a_lista(db, monkeypatch):
    """Envio cujo desligado de hoje é o .env vazio (os portões de .env): a
    semente grava `ativo=false` com a lista vazia — NULL seria ligado com
    ninguém, com registro e sino a cada rodada — e a conferência dá igual.
    Refeita com gente no .env (`--sobrescrever`, código de antes da fatia),
    volta a NULL (o interruptor de hoje). Envio de outra fonte (o usuário pelo
    nome, o Informar) não é tocado mesmo vazio."""
    await montar(db, monkeypatch, variante="vazias")
    _codigo_antes_da(monkeypatch, "F1")
    chave = K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING
    res = await _semear(db, "F1", aplicar=True)
    planos = {p.chave: p for p in res.planos}
    assert (planos[chave].refs, planos[chave].ativo, planos[chave].grava_ativo) == ([], False, True)
    tuta = planos[K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR]  # "thatcher" sem Threema
    assert (tuta.refs, tuta.grava_ativo) == ([], False)
    linhas = {r.chave: r for r in (await db.execute(select(ThreemaEnvio))).scalars()}
    assert linhas[chave].ativo is False
    assert linhas[K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR].ativo is None
    itens = await threema_envios.conferir(db, fatia="F1")
    assert [x.chave for x in itens if x.situacao != "igual"] == []
    item = next(x for x in itens if x.chave == chave)
    assert (item.ativo_legado, item.ativo_cadastro, item.desligado_pela_lista) == (
        False,
        False,
        True,
    )
    # o .env ganhou gente: a semente envelheceu (lista e liga/desliga)
    monkeypatch.setattr(get_settings(), "nf_sem_estoque_threema_recipients", ID["joana"])
    (item,) = await threema_envios.conferir(db, chaves=[chave])
    assert (item.situacao, item.ativo_legado, item.ativo_cadastro) == ("diferente", True, False)
    await _semear(db, "F1", aplicar=True, sobrescrever=True)
    linha = await db.get(ThreemaEnvio, chave, populate_existing=True)
    assert linha.ativo is None
    (item,) = await threema_envios.conferir(db, chaves=[chave])
    assert (item.situacao, item.ativo_legado, item.ativo_cadastro) == ("igual", True, True)
