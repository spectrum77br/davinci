"""Importação da planilha `redes sociais.xlsx` → marcas + redes_sociais +
padrão de e-mail SAC (scripts/import_marcas_redes_sociais.py) — Eduardo,
15/09/2026.

A planilha real fica fora do git (tem senha em texto puro) e NUNCA entra em
teste: aqui uma FAKE é gerada no tmp_path com a MESMA forma — aba `marcas`
com duas colunas 'email' (a 2ª é o lembrete), aba `r.social` com a pivot
(header na linha 2, coluna A sem título) e a normalizada (header na linha
cujo A == 'Plataforma') separadas por linhas em branco — e senhas "fake-…".

Cobre: mapeamento das colunas (atuação → classe, validade → dominio_validade,
'?' → NULL, fone int/str → dígitos, textos com espaço), inpi ok/aguardando/
outro, site derivado do 1º domínio de dominio_br; a linha da pivot como
credencial da MARCA (sac_fone/sac_email/sac_senha_enc cifrada), backfill
(pivot sem fone + linhas normalizadas iguais → sobe pra marca, contas NULL),
override por conta SÓ quando difere da marca (facebook, só na pivot, herda
tudo), linhas divergentes entre si → override + aviso; união pivot ∪
normalizada; login divergente em `usuario`; função/tipo/obs na marca com
dedupe; marca que só existe em r.social; linha normalizada sem marca;
padrão SAC criado uma vez por marca; idempotência (2ª rodada não cria nem
altera nada; nunca apaga dado do banco; verificação/ativo intocados); conta
divergente no banco → aviso e nada gravado; dry-run que não grava; e que
nenhum aviso/plano carrega senha.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import openpyxl
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Marca, MarcaEmailPadrao, RedeSocial
from app.security.cipher import decrypt
from scripts.import_marcas_redes_sociais import (
    PADRAO_SAC_ASSUNTO,
    PADRAO_SAC_CORPO,
    PADRAO_SAC_NOME,
    Resumo,
    _site_do_dominio,
    importar,
)

pytestmark = pytest.mark.asyncio

LEMBRETE = "colocar todos os email disponveis"
OBS_PIVOT = "colocar verificado instagram, face e zap"
# Mesmo texto da pivot com espaço duplo: tem que ser reconhecido como igual
# (dedupe lower/strip/espaços colapsados) e NÃO ir pra obs da conta.
OBS_PIVOT_ESPACADA = "colocar  verificado  instagram, face e zap"
OBS_NORMAL = "colocar verificado instagram e zap"

_CAB_MARCAS = [
    "marca", "inpi", "usuario", "senha", "email", "dominio br", "dominio",
    "dono dominio", "validade", "atuação", "email", "obs",
]
_CAB_PIVOT = [
    None, "fone", "usuario", "senha", "intagram", "facebook", "twitter",
    "tiktok", "youtube", "função", "obs",
]
_CAB_NORMAL = ["Plataforma", "Conta", "E-mail", "Fone", "Senha", "tipo", "obs"]

_PERM_REDES = {"redes_sociais": {"view": True, "edit": True, "delete": False}}


def _planilha(
    tmp_path: Path,
    *,
    classe_poofy: str = " malas ",
    extra_normalizadas: tuple[list, ...] = (),
) -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "marcas"
    ws.append(_CAB_MARCAS)  # linha 1
    ws.append([  # linha 2 — textos com espaço, e-mail vazio, dominio vazio
        " Poofy ", "aguardando", " juomar ", "fake-poofy", None, " poofy.com.br ", None,
        "omar", datetime(2034, 7, 4), classe_poofy, LEMBRETE, None,
    ])
    ws.append([  # linha 3 — dono '?', obs própria
        "locagil", "ok", "locagil", "fake-locagil", "Contato@Locagil.com.br",
        "locagil.com.br", "locagil.com", "?", datetime(2028, 3, 1), "locadora", LEMBRETE,
        "obs da marca",
    ])
    ws.append([  # linha 4 — inpi desconhecido, lista de domínios com vírgula
        "Charlots Park", "xx", "JESSICAMPG", "fake-charlots", "suporte@exemplo.com",
        "charlotspark.com.br, charlots.com.br", "charlotspark.com", "jessica",
        datetime(2034, 12, 16), "eletronicos", LEMBRETE, None,
    ])

    ws2 = wb.create_sheet("r.social")
    ws2.append([])  # linha 1 em branco (igual à real)
    ws2.append(_CAB_PIVOT)  # linha 2
    ws2.append([  # linha 3 — credencial completa na pivot
        "locagil", 19988840005, "sac@locagil.com.br", "fake-piv-locagil", "locagiloficial",
        "locagil", "locagiloficial", "locagiloficial", "locagiloficial", "locaçao celular",
        OBS_PIVOT,
    ])
    ws2.append([  # linha 4 — SEM fone na pivot (backfill das normalizadas); youtube '?'
        "poofy", None, "sac@poofy.com.br", "fake-piv-poofy", "poofy_brasil",
        "poofy.brasil", "poofy_brasil", "poofy_brasil", "?", "malas", OBS_PIVOT,
    ])
    ws2.append([  # linha 5 — marca que NÃO está na aba marcas; sem fone/senha/@
        "uranyx", None, "sac@uranyx.com.br", None, None, None, None, None, None,
        "eletronicos", "trocar nome para Charlots",
    ])
    ws2.append([])  # 6
    ws2.append([])  # 7
    ws2.append([])  # 8
    ws2.append(_CAB_NORMAL)  # linha 9
    ws2.append([  # linha 10 — login difere do @; senha própria; obs igual à da pivot
        "instagram", "locagil", "sac@locagil.com.br", 19988840005, "fake-ig-locagil", None,
        OBS_PIVOT_ESPACADA,
    ])
    ws2.append([  # linha 11 — senha própria; fone igual às outras linhas da poofy
        "instagram", "poofy", "sac@poofy.com.br", 11930000710, "fake-ig-poofy", "mala",
        OBS_NORMAL,
    ])
    ws2.append([  # linha 12 — senha IGUAL à da pivot → herda (NULL)
        "twitter", "poofy_brasil", "sac@poofy.com.br", 11930000710, "fake-piv-poofy", "mala",
        None,
    ])
    ws2.append([  # linha 13 — sem senha → herda
        "tiktok", "poofy_brasil", "sac@poofy.com.br", 11930000710, None, "mala", None,
    ])
    ws2.append([  # linha 14 — fone como texto, tipo com espaço, sem senha
        "instagram", "uranyx", "sac@uranyx.com.br", "11 98351-7003", None, "cel eletro ",
        OBS_NORMAL,
    ])
    ws2.append([  # linha 15 — Conta igual ao @ da pivot → usuario fica None
        "twitter", "locagiloficial", "sac@locagil.com.br", 19988840005, "fake-tw-locagil",
        "cel mala", None,
    ])
    ws2.append([  # linha 16 — sem Conta; fone DIFERENTE da linha 14 (uranyx diverge)
        "twitter", None, "sac@uranyx.com.br", "11 98351-7004", None, "cel eletro ", None,
    ])
    ws2.append([  # linha 17 — Conta '?' + @ '?' na pivot → linha sem conta
        "youtube", "?", "sac@poofy.com.br", 11930000710, "fake-yt-poofy", "mala", None,
    ])
    ws2.append([  # linha 18 — não casa com marca nenhuma
        "tiktok", "ninguem", "ninguem@exemplo.com", 5511999990000, "fake-nada", None, None,
    ])
    for extra in extra_normalizadas:
        ws2.append(extra)

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "redes sociais.xlsx"
    wb.save(path)
    return str(path)


async def _marcas(db: AsyncSession) -> dict[str, Marca]:
    rows = (await db.execute(select(Marca))).scalars().all()
    return {m.slug: m for m in rows}


async def _redes(db: AsyncSession) -> dict[tuple[UUID, str], RedeSocial]:
    rows = (await db.execute(select(RedeSocial))).scalars().all()
    out: dict[tuple[UUID, str], RedeSocial] = {}
    for r in rows:
        chave = (r.marca_id, r.plataforma)
        assert chave not in out, f"(marca, plataforma) duplicado: {chave}"
        out[chave] = r
    return out


async def _padroes(db: AsyncSession) -> dict[UUID, list[MarcaEmailPadrao]]:
    rows = (await db.execute(select(MarcaEmailPadrao))).scalars().all()
    out: dict[UUID, list[MarcaEmailPadrao]] = {}
    for p in rows:
        out.setdefault(p.marca_id, []).append(p)
    return out


async def _conta(db: AsyncSession, model: type) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


def _sem_senha_no_texto(resumo: Resumo) -> None:
    texto = "\n".join(resumo.avisos + resumo.plano + resumo.lembretes)
    assert "fake-" not in texto


def _herda_tudo(r: RedeSocial) -> None:
    """Conta sem override: e-mail/fone/senha NULL = herda os sac_* da marca."""
    assert (r.email, r.fone, r.senha_enc) == (None, None, None)


@pytest.mark.parametrize(
    ("dominio_br", "esperado"),
    [
        ("poofy.com.br", "https://poofy.com.br"),
        ("charlotspark.com.br, charlots.com.br", "https://charlotspark.com.br"),
        ("  A.com.br ; b.com ", "https://a.com.br"),
        ("http://ja.com/loja", "http://ja.com/loja"),
        ("?, x.com", "https://x.com"),
        (None, None),
        ("", None),
    ],
)
async def test_site_do_dominio(dominio_br, esperado):
    # async só por causa do pytestmark do módulo (função pura).
    assert _site_do_dominio(dominio_br) == esperado


async def test_importa_planilha_e_reexecucao_e_idempotente(
    client, db, make_user, auth_as, tmp_path
):
    path = _planilha(tmp_path)

    r1 = await importar(path, session=db)

    assert (r1.marcas_criadas, r1.marcas_atualizadas) == (4, 0)
    assert (r1.redes_criadas, r1.redes_atualizadas) == (12, 0)
    assert r1.padroes_criados == 4
    assert r1.linhas_sem_marca == 1
    assert any("linha 18" in a and "sem marca" in a for a in r1.avisos)
    # uranyx: fone vazio na pivot e as linhas normalizadas discordam → fica
    # por conta, com aviso (sem o valor do fone no texto).
    assert any(a.startswith("uranyx: fone vazio na pivot") for a in r1.avisos)
    assert len(r1.avisos) == 2
    assert not any("1198351700" in a for a in r1.avisos)
    assert r1.lembretes == [LEMBRETE]
    _sem_senha_no_texto(r1)

    marcas = await _marcas(db)
    assert set(marcas) == {"poofy", "locagil", "charlots-park", "uranyx"}
    redes = await _redes(db)
    assert len(redes) == 12

    # --- aba marcas: mapeamento das colunas
    poofy = marcas["poofy"]
    assert poofy.nome == "Poofy"  # strip
    assert poofy.inpi_status == "aguardando"
    assert poofy.usuario == "juomar"
    assert poofy.email is None
    assert poofy.dominio_br == "poofy.com.br"
    assert poofy.dominio is None
    assert poofy.dono_dominio == "omar"
    assert poofy.dominio_validade == date(2034, 7, 4)
    assert poofy.classe == "malas"  # 'atuação' → classe
    assert decrypt(poofy.senha_enc) == "fake-poofy"
    assert poofy.site == "https://poofy.com.br"  # derivado de dominio_br
    # função (pivot) e tipo (1º não vazio da normalizada) são por marca;
    # obs da pivot vai pra marca; o lembrete da 2ª coluna email NÃO.
    assert poofy.funcao == "malas"
    assert poofy.tipo == "mala"
    assert poofy.obs == OBS_PIVOT
    # --- linha da pivot = credencial da MARCA (sac_*). Fone vazio na pivot
    # e as 4 linhas normalizadas iguais → backfill pra marca.
    assert poofy.sac_fone == "11930000710"
    assert poofy.sac_email == "sac@poofy.com.br"
    assert decrypt(poofy.sac_senha_enc) == "fake-piv-poofy"
    assert poofy.whatsapp_verificacao_status == "nao_solicitado"

    locagil = marcas["locagil"]
    assert locagil.inpi_status == "registrado"  # 'ok'
    assert locagil.email == "contato@locagil.com.br"  # lower
    assert locagil.dono_dominio is None  # '?'
    assert locagil.obs.splitlines() == ["obs da marca", OBS_PIVOT]
    assert locagil.funcao == "locaçao celular"
    assert locagil.tipo == "cel mala"  # instagram sem tipo → twitter
    assert decrypt(locagil.senha_enc) == "fake-locagil"
    assert locagil.site == "https://locagil.com.br"
    assert locagil.sac_fone == "19988840005"  # int → dígitos
    assert locagil.sac_email == "sac@locagil.com.br"
    assert decrypt(locagil.sac_senha_enc) == "fake-piv-locagil"

    charlots = marcas["charlots-park"]
    assert charlots.nome == "Charlots Park"
    assert charlots.inpi_status == "nao_registrado"  # 'xx'
    assert charlots.obs == "inpi na planilha: xx"
    assert charlots.dominio_br == "charlotspark.com.br, charlots.com.br"
    assert charlots.site == "https://charlotspark.com.br"  # 1º domínio da lista
    assert charlots.funcao is None and charlots.tipo is None
    assert (charlots.sac_fone, charlots.sac_email, charlots.sac_senha_enc) == (None, None, None)

    # marca que só aparece em r.social: criada com nome/slug + função/tipo/obs
    uranyx = marcas["uranyx"]
    assert uranyx.nome == "uranyx"
    assert uranyx.inpi_status == "nao_registrado"
    assert uranyx.senha_enc is None and uranyx.email is None
    assert uranyx.site is None  # sem dominio_br
    assert uranyx.funcao == "eletronicos"
    assert uranyx.tipo == "cel eletro"
    assert uranyx.obs == "trocar nome para Charlots"
    assert uranyx.sac_email == "sac@uranyx.com.br"
    assert uranyx.sac_fone is None  # linhas divergem → não sobe pra marca
    assert uranyx.sac_senha_enc is None

    for m in marcas.values():
        assert LEMBRETE not in (m.obs or "")

    # --- redes: união pivot ∪ normalizada por (marca, plataforma)
    assert {p for (mid, p) in redes if mid == locagil.id} == {
        "instagram", "facebook", "twitter", "tiktok", "youtube",
    }
    assert {p for (mid, p) in redes if mid == poofy.id} == {
        "instagram", "facebook", "twitter", "tiktok", "youtube",
    }
    assert {p for (mid, p) in redes if mid == uranyx.id} == {"instagram", "twitter"}
    assert not [p for (mid, p) in redes if mid == charlots.id]
    # `verificado` não existe mais: verificacao_status fica no default.
    for r in redes.values():
        assert r.verificacao_status == "nao_solicitado"
        assert r.verificacao_obs is None
        assert r.ativo is True

    ig = redes[(locagil.id, "instagram")]
    assert ig.conta == "locagiloficial"  # @ da pivot
    assert ig.usuario == "locagil"  # login da normalizada (difere do @)
    assert ig.email is None and ig.fone is None  # iguais à marca → herda
    assert decrypt(ig.senha_enc) == "fake-ig-locagil"  # difere da marca → override
    assert ig.obs is None  # mesma obs da pivot (já na marca) → não repete

    fb = redes[(locagil.id, "facebook")]  # só na pivot: herda tudo da marca
    assert fb.conta == "locagil"
    assert fb.usuario is None
    _herda_tudo(fb)

    tw = redes[(locagil.id, "twitter")]
    assert tw.conta == "locagiloficial"
    assert tw.usuario is None  # Conta == @ → redundante
    assert tw.email is None and tw.fone is None
    assert decrypt(tw.senha_enc) == "fake-tw-locagil"

    _herda_tudo(redes[(locagil.id, "tiktok")])
    _herda_tudo(redes[(locagil.id, "youtube")])

    ig_poofy = redes[(poofy.id, "instagram")]
    assert ig_poofy.usuario == "poofy"
    assert ig_poofy.obs == OBS_NORMAL  # difere da obs da pivot → fica na conta
    assert ig_poofy.fone is None  # fone subiu pra marca (backfill) → herda
    assert ig_poofy.email is None
    assert decrypt(ig_poofy.senha_enc) == "fake-ig-poofy"

    _herda_tudo(redes[(poofy.id, "twitter")])  # senha igual à da pivot → herda
    _herda_tudo(redes[(poofy.id, "tiktok")])  # sem senha → herda
    _herda_tudo(redes[(poofy.id, "facebook")])

    yt_poofy = redes[(poofy.id, "youtube")]  # '?' nos dois lados
    assert yt_poofy.conta is None and yt_poofy.usuario is None
    assert yt_poofy.fone is None
    assert decrypt(yt_poofy.senha_enc) == "fake-yt-poofy"

    ig_uranyx = redes[(uranyx.id, "instagram")]
    assert ig_uranyx.conta is None  # pivot vazia
    assert ig_uranyx.usuario == "uranyx"
    assert ig_uranyx.fone == "11983517003"  # '11 98351-7003' → dígitos; override
    assert ig_uranyx.email is None  # igual ao sac_email → herda
    assert ig_uranyx.senha_enc is None
    assert ig_uranyx.obs == OBS_NORMAL
    tw_uranyx = redes[(uranyx.id, "twitter")]
    assert tw_uranyx.conta is None and tw_uranyx.usuario is None
    assert tw_uranyx.fone == "11983517004"  # a outra linha → override
    assert tw_uranyx.email is None and tw_uranyx.senha_enc is None

    # --- padrão de e-mail SAC: um por marca, com o texto genérico
    padroes = await _padroes(db)
    assert set(padroes) == {m.id for m in marcas.values()}
    for lista in padroes.values():
        assert len(lista) == 1
        p = lista[0]
        assert p.contexto == "sac"
        assert p.nome == PADRAO_SAC_NOME
        assert p.assunto == PADRAO_SAC_ASSUNTO == "{{ marca }} — atendimento"
        assert p.corpo == PADRAO_SAC_CORPO
        assert "{{ cliente }}" in p.corpo and "{{ whatsapp }}" in p.corpo
        assert p.remetente_nome is None and p.remetente_email is None
        assert p.incluir_logo is True and p.incluir_assinatura is True and p.ativo is True

    # --- pela API a herança aparece: senha EFETIVA com a origem
    auth_as(await make_user(permissions=_PERM_REDES))
    resp_fb = await client.get(f"/api/redes-sociais/{fb.id}/senha")
    assert resp_fb.status_code == 200, resp_fb.text
    assert resp_fb.json()["senha"] == "fake-piv-locagil"
    assert resp_fb.json()["origem"] == "marca"
    resp_ig = await client.get(f"/api/redes-sociais/{ig.id}/senha")
    assert resp_ig.status_code == 200, resp_ig.text
    assert resp_ig.json()["senha"] == "fake-ig-locagil"
    assert resp_ig.json()["origem"] == "conta"
    resp_ur = await client.get(f"/api/redes-sociais/{ig_uranyx.id}/senha")
    assert resp_ur.status_code == 200, resp_ur.text
    assert resp_ur.json()["senha"] == ""
    assert resp_ur.json()["origem"] is None

    # --- 2ª rodada: nada criado, nada alterado, mesmas linhas
    ids_antes = {chave: r.id for chave, r in redes.items()}
    senhas_antes = {chave: r.senha_enc for chave, r in redes.items()}
    sac_antes = {slug: m.sac_senha_enc for slug, m in marcas.items()}
    padroes_antes = {mid: lista[0].id for mid, lista in padroes.items()}

    r2 = await importar(path, session=db)

    assert (r2.marcas_criadas, r2.marcas_atualizadas) == (0, 0)
    assert (r2.redes_criadas, r2.redes_atualizadas) == (0, 0)
    assert r2.padroes_criados == 0
    assert r2.linhas_sem_marca == 1
    assert r2.lembretes == [LEMBRETE]
    db.expire_all()
    assert await _conta(db, Marca) == 4
    assert await _conta(db, MarcaEmailPadrao) == 4
    redes2 = await _redes(db)
    assert {chave: r.id for chave, r in redes2.items()} == ids_antes
    # Senha igual não é recifrada (senão toda rodada viraria "atualizada").
    assert {chave: r.senha_enc for chave, r in redes2.items()} == senhas_antes
    marcas2 = await _marcas(db)
    assert {slug: m.sac_senha_enc for slug, m in marcas2.items()} == sac_antes
    assert marcas2["locagil"].obs.splitlines() == ["obs da marca", OBS_PIVOT]
    assert marcas2["charlots-park"].obs == "inpi na planilha: xx"
    assert {mid: lista[0].id for mid, lista in (await _padroes(db)).items()} == padroes_antes


async def test_reexecucao_nao_apaga_dado_do_banco_nem_mexe_em_verificacao(db, tmp_path):
    """Célula vazia na planilha nunca zera o banco (nem um override posto na
    tela); verificação (conta e Zap da marca), ativo e site da tela não são
    tocados; célula preenchida que MUDOU atualiza."""
    path = _planilha(tmp_path)
    await importar(path, session=db)
    marcas = await _marcas(db)
    redes = await _redes(db)

    poofy = marcas["poofy"]
    poofy.email = "manual@exemplo.com"  # planilha tem e-mail vazio pra Poofy
    poofy.dominio = "poofy.com"  # idem
    poofy.site = "https://loja.poofy.com.br"  # site da tela ≠ derivado do domínio
    poofy.whatsapp_verificacao_status = "em_andamento"
    poofy.whatsapp_verificacao_obs = "protocolo 123"
    ig = redes[(poofy.id, "instagram")]
    ig.verificacao_status = "verificado"
    ig.verificacao_obs = "selo desde 10/09"
    ig.ativo = False
    ig.url = "https://instagram.com/poofy_brasil"
    fb = redes[(marcas["locagil"].id, "facebook")]
    fb.email = "face@locagil.com.br"  # override posto na tela; planilha = herda
    await db.commit()

    r2 = await importar(path, session=db)
    assert (r2.marcas_criadas, r2.marcas_atualizadas) == (0, 0)
    assert (r2.redes_criadas, r2.redes_atualizadas) == (0, 0)
    assert r2.padroes_criados == 0
    db.expire_all()
    marcas = await _marcas(db)
    redes = await _redes(db)
    poofy = marcas["poofy"]
    assert poofy.email == "manual@exemplo.com"
    assert poofy.dominio == "poofy.com"
    assert poofy.site == "https://loja.poofy.com.br"
    assert poofy.whatsapp_verificacao_status == "em_andamento"
    assert poofy.whatsapp_verificacao_obs == "protocolo 123"
    ig = redes[(poofy.id, "instagram")]
    assert ig.verificacao_status == "verificado"
    assert ig.verificacao_obs == "selo desde 10/09"
    assert ig.ativo is False
    assert ig.url == "https://instagram.com/poofy_brasil"
    assert redes[(marcas["locagil"].id, "facebook")].email == "face@locagil.com.br"

    # Planilha mudou a classe da Poofy → 1 marca atualizada, redes intactas.
    path2 = _planilha(tmp_path / "v2", classe_poofy="malas e mochilas")
    r3 = await importar(path2, session=db)
    assert (r3.marcas_criadas, r3.marcas_atualizadas) == (0, 1)
    assert (r3.redes_criadas, r3.redes_atualizadas) == (0, 0)
    assert r3.padroes_criados == 0
    db.expire_all()
    assert (await _marcas(db))["poofy"].classe == "malas e mochilas"


async def test_conta_divergente_no_banco_avisa_e_nada_e_gravado(db, tmp_path):
    """@ no banco ≠ @ da planilha = OUTRA conta: a equipe pode ter trocado a
    credencial na tela — a importação avisa e não sobrescreve nada."""
    path = _planilha(tmp_path)
    await importar(path, session=db)
    marcas = await _marcas(db)
    redes = await _redes(db)
    locagil_id = marcas["locagil"].id
    ig = redes[(locagil_id, "instagram")]
    ig.conta = "locagil_nova"
    ig.usuario = "login_novo"
    ig.email = "nova@locagil.com.br"
    senha_antes = ig.senha_enc
    await db.commit()

    r2 = await importar(path, session=db)

    assert (r2.redes_criadas, r2.redes_atualizadas) == (0, 0)
    assert (r2.marcas_criadas, r2.marcas_atualizadas) == (0, 0)
    assert any(
        a.startswith("locagil/instagram:") and "difere da planilha" in a and "nada gravado" in a
        for a in r2.avisos
    )
    _sem_senha_no_texto(r2)
    db.expire_all()
    ig = (await _redes(db))[(locagil_id, "instagram")]
    assert ig.conta == "locagil_nova"
    assert ig.usuario == "login_novo"
    assert ig.email == "nova@locagil.com.br"
    assert ig.senha_enc == senha_antes


async def test_dry_run_mostra_plano_e_nao_grava(db, tmp_path):
    path = _planilha(tmp_path)

    resumo = await importar(path, dry_run=True, session=db)

    assert resumo.dry_run is True
    assert (resumo.marcas_criadas, resumo.redes_criadas) == (4, 12)
    assert resumo.padroes_criados == 4
    assert await _conta(db, Marca) == 0
    assert await _conta(db, RedeSocial) == 0
    assert await _conta(db, MarcaEmailPadrao) == 0
    plano = "\n".join(resumo.plano)
    # linha da marca (sac_*): "sim" = valor da planilha (pivot ou backfill)
    assert "marca locagil / sac: fone sim, e-mail sim, senha sim" in plano
    assert "marca Poofy / sac: fone sim, e-mail sim, senha sim" in plano  # fone via backfill
    assert "marca uranyx / sac: fone não, e-mail sim, senha não" in plano
    # contas: senha própria × herdada da marca × nenhuma
    assert "locagil / instagram / @locagiloficial / senha: sim (própria)" in plano
    assert "locagil / facebook / @locagil / senha: sim (herda da marca)" in plano
    assert "uranyx / instagram / (sem conta) / senha: não" in plano
    assert "Poofy / youtube / (sem conta) / senha: sim (própria)" in plano
    # padrão SAC
    assert "marca uranyx / padrão de e-mail SAC: criar" in plano
    assert plano.count("padrão de e-mail SAC: criar") == 4
    _sem_senha_no_texto(resumo)


async def test_linha_invalida_sai_so_com_numero_e_campo(db, tmp_path):
    """Erro numa linha não derruba a importação e o aviso nunca traz o valor
    da célula (muito menos a senha) — só nº da linha e campo."""
    conta_gigante = "x" * 200  # > 128 → conta_too_long no _handle
    path = _planilha(
        tmp_path,
        extra_normalizadas=(
            ["tiktok", conta_gigante, "sac@locagil.com.br", 19988840005, "fake-pin", None, None],
        ),
    )

    resumo = await importar(path, session=db)

    assert resumo.redes_criadas == 12  # a linha 19 não virou conta nem override
    assert any("linha 19" in a and "conta" in a for a in resumo.avisos)
    assert not any(conta_gigante in a or "fake-pin" in a for a in resumo.avisos)
    marcas = await _marcas(db)
    redes = await _redes(db)
    tk = redes[(marcas["locagil"].id, "tiktok")]  # veio só da pivot: herda tudo
    assert tk.conta == "locagiloficial" and tk.usuario is None
    _herda_tudo(tk)
