"""VOLUME da aba E-mail › Caixas: decifrar tudo × o índice leve (09/10/2026).

Por que existe `mail_caixa_indice`: a pasta e a loja de cada e-mail estão
dentro do conteúdo cifrado. Este teste monta uma caixa com N e-mails como os
do leitor dele (40 pastas, ~2,5 KB de texto cada, como em produção em 09/10)
e mede:

  1. SEM índice — o que cada clique custaria: decifrar e classificar todos
     (pasta, loja provável, segurança) para contar as pastas ou filtrar uma
     loja; e só decifrar (o mínimo, para saber a pasta);
  2. o preenchimento do índice pelo job (e-mails por segundo);
  3. COM índice — as rotas de verdade (pastas, 1ª página, pasta, loja, a 10ª
     página pelo cursor) e o EXPLAIN ANALYZE das consultas.

Padrão: N = 300 (rápido, só confere que tudo bate). Para a medição de
verdade (20 mil e-mails, com o relatório):

    MAIL_CAIXA_VOLUME=20000 pytest tests/test_mail_caixa_volume.py -s

(`MAIL_CAIXA_VOLUME_SAIDA=/caminho/relatorio.txt` grava o relatório também.)
"""

from __future__ import annotations

import os
import random
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, insert, select, text

from app.models import MailMessage
from app.models.mail_atendimento import MailCaixaIndice, MailMessageMeta
from app.security.cipher import encrypt_json
from app.services.mail_atendimento import indice
from tests.test_mail_ponte import AGORA, ALIASES_GERAL, GERAL
from tests.test_mail_ponte import (
    _config as _config_ponte,  # noqa: F401 — a config autouse da ponte vale aqui
)
from tests.test_mail_ponte import cena as cena  # noqa: F401 — o mundo da ponte

N = int(os.environ.get("MAIL_CAIXA_VOLUME") or 300)
MEDIR = "MAIL_CAIXA_VOLUME" in os.environ
SAIDA = os.environ.get("MAIL_CAIXA_VOLUME_SAIDA")
URL = "/api/mail/mailboxes"

SISTEMA = ["INBOX", "Sent", "Drafts", "Trash", "Archive", "Spam"]
PESSOAIS = [
    "vendas ml",
    "mensagens ml",
    "problema ml",
    "reclamação ml",
    "vendas shopee",
    "mensagens shopee",
    "problema shopee",
    "vendas tiktok",
    "problema tiktok",
    "vendas amazon",
    "mensagens amazon",
    "vendas temu",
    "vendas magalu",
    "vendas ali",
    "*7buyers",
    "*avisos",
    "*makisa",
    "*charlots",
    "*uranyx sac",
    "*uranyx atacado",
    "*poofy",
    "financeiro",
    "contabilidade",
    "devoluções ml",
    "envio",
    "retido",
    "DNP",
    "Bancos",
    "Fornecedores",
    "Marketing",
    "Notas fiscais",
    "Transportadoras",
    "Contratos",
    "Diversos",
]
PASTAS = SISTEMA + PESSOAIS  # 40
PALAVRAS = (
    "pedido entrega prazo produto envio rastreio cliente compra nota fiscal troca"
    " devolução pagamento confirmado aguardando transportadora código postagem"
    " embalagem obrigado atenciosamente bom dia boa tarde segue anexo valor frete"
).split()


def _texto(rnd: random.Random, tamanho: int) -> str:
    linhas = []
    total = 0
    while total < tamanho:
        linha = " ".join(rnd.choice(PALAVRAS) for _ in range(rnd.randint(8, 16))).capitalize()
        if rnd.random() < 0.1:
            linha += f" Pedido {rnd.randint(2000000000000, 2999999999999)}."
        linhas.append(linha + ".")
        total += len(linha) + 2
    corpo = "\n".join(linhas)
    return corpo + "\n\nEm 01/10/2026 10:00, Loja escreveu:\n> " + "\n> ".join(linhas[:3])


def _conteudos(n: int) -> list[dict]:
    rnd = random.Random(42)  # noqa: S311 — dados de teste, repetíveis
    nossos = [GERAL, *ALIASES_GERAL]
    pesos = [30, 6, 1, 2, 3, 1] + [57 / len(PESSOAIS)] * len(PESSOAIS)
    saida = []
    for k in range(n):
        pasta = rnd.choices(PASTAS, weights=pesos)[0]
        seguranca = rnd.random() < 0.02
        de = (
            rnd.choice(["no-reply@mercadolivre.com.br", "naoresponda@bling.com.br"])
            if seguranca or rnd.random() < 0.3
            else f"cliente{k}@gmail.com"
        )
        saida.append(
            {
                "folder": pasta,
                "subject": "Seu código de verificação"
                if seguranca
                else f"Pedido {rnd.randint(1000, 9999)} — {rnd.choice(PALAVRAS)}",
                "from_address": de,
                "from_name": "Cliente",
                "to": [rnd.choice(nossos)],
                "cc": [],
                "reply_to": None,
                "message_id": f"<vol{k}@mail.teste>",
                "in_reply_to": None,
                "references": [],
                "text": f"Use {rnd.randint(100000, 999999)} para entrar na sua conta."
                if seguranca
                else _texto(rnd, rnd.randint(1500, 3500)),
            }
        )
    return saida


async def _semear(db, caixa_id, n: int) -> None:
    linhas = []
    for k, conteudo in enumerate(_conteudos(n)):
        linhas.append(
            {
                "id": uuid4(),
                "mailbox_id": caixa_id,
                "source_id": f"vol:{k}",
                "direction": "sent" if conteudo["folder"] == "Sent" else "inbound",
                # Antes do corte da ponte: tudo pelo provável (o caso das ~40 pastas antigas).
                "received_at": AGORA - timedelta(days=3, minutes=k),
                "content_enc": encrypt_json(conteudo),
                "attachment_count": 0,
            }
        )
    for i in range(0, len(linhas), 1000):
        await db.execute(insert(MailMessage), linhas[i : i + 1000])
    await db.commit()
    await db.execute(text("ANALYZE mail_messages"))
    await db.commit()


def _ms(inicio: float) -> float:
    return (time.perf_counter() - inicio) * 1000


async def _explain(db, sql_obj) -> str:
    compilado = sql_obj.compile(dialect=db.bind.dialect, compile_kwargs={"literal_binds": True})
    linhas = (await db.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) {compilado}"))).all()
    return "\n".join(r[0] for r in linhas)


async def test_volume_decifrar_tudo_x_indice(db, cena, client, auth_as):
    caixa = cena.geral
    await _semear(db, caixa.id, N)
    rel: list[str] = [f"VOLUME — {N} e-mails, {len(PASTAS)} pastas (caixa Geral do teste)"]
    tamanho = await db.scalar(
        select(func.avg(func.octet_length(MailMessage.content_enc))).where(
            MailMessage.mailbox_id == caixa.id
        )
    )
    rel.append(f"conteúdo cifrado médio: {float(tamanho):.0f} bytes")

    # 1. SEM índice: o que cada clique custaria.
    base = await indice.base_da_caixa(db, await db.get(type(caixa), caixa.id))
    t = time.perf_counter()
    mensagens = (
        await db.scalars(select(MailMessage).where(MailMessage.mailbox_id == caixa.id))
    ).all()
    t_ler = _ms(t)
    t = time.perf_counter()
    from app.services.mail_atendimento import pastas as pastas_svc
    from app.services.mail_atendimento import ponte

    contagem: dict[str, int] = {}
    for m in mensagens:
        chave = indice.chave_da_pasta(caixa.id, pastas_svc.do_conteudo(ponte.decifrar(m).conteudo))
        contagem[chave] = contagem.get(chave, 0) + 1
    t_so_pasta = _ms(t)
    t = time.perf_counter()
    calculadas = [indice.calcular(m, base) for m in mensagens]
    t_tudo = _ms(t)
    rel.append(
        f"SEM índice, por clique: ler do banco {t_ler:.0f} ms + só a pasta {t_so_pasta:.0f} ms"
        f" = {t_ler + t_so_pasta:.0f} ms; pasta+loja+segurança {t_tudo:.0f} ms"
        f" (= {t_ler + t_tudo:.0f} ms por clique, {t_tudo / len(mensagens):.2f} ms/e-mail)"
    )
    assert len(contagem) == len(PASTAS)
    del mensagens

    # 2. O job preenche (em voltas de JOB_MAXIMO).
    t = time.perf_counter()
    voltas = 0
    while True:
        feitos = (await indice.rodar(db))["indexados"]
        voltas += 1
        if feitos == 0:
            break
    t_job = _ms(t)
    assert await db.scalar(select(func.count()).select_from(MailCaixaIndice)) == N
    await db.execute(text("ANALYZE mail_caixa_indice"))
    await db.commit()
    rel.append(
        f"job: {N} e-mails em {t_job / 1000:.1f} s ({voltas - 1} volta(s) com trabalho),"
        f" {N / (t_job / 1000):.0f} e-mails/s"
    )

    # 3. COM índice: as rotas de verdade.
    auth_as(cena.dono)
    medidas: dict[str, float] = {}

    async def medir(nome: str, url: str, **params):
        await client.get(url, params=params)  # aquece
        tempos = []
        for _ in range(5):
            t = time.perf_counter()
            r = await client.get(url, params=params)
            tempos.append(_ms(t))
            assert r.status_code == 200, r.text
        medidas[nome] = sorted(tempos)[2]
        return r.json()

    corpo = await medir("GET /pastas", f"{URL}/{caixa.id}/pastas")
    assert corpo["faltam"] == 0 and corpo["pastas"][0]["total"] == N
    assert sum(p["total"] for p in corpo["pastas"][1:]) == N
    assert sum(x["total"] for x in corpo["lojas"]) == N
    loja = next(x for x in corpo["lojas"] if x["tipo"] == "loja")
    pasta = next(p for p in corpo["pastas"] if p["tipo"] == "pessoal")
    await medir("GET /pastas?loja", f"{URL}/{caixa.id}/pastas", loja=loja["chave"])
    pagina = await medir("GET /lista (Todas, 50)", f"{URL}/{caixa.id}/lista")
    assert len(pagina["itens"]) == min(50, N)
    await medir("GET /lista?pasta", f"{URL}/{caixa.id}/lista", pasta=pasta["id"])
    await medir("GET /lista?loja", f"{URL}/{caixa.id}/lista", loja=loja["chave"])
    await medir(
        "GET /lista?pasta&loja",
        f"{URL}/{caixa.id}/lista",
        pasta=pasta["id"],
        loja=loja["chave"],
    )
    # A 10ª página pelo cursor.
    antes = None
    for _ in range(9):
        p = (
            await client.get(f"{URL}/{caixa.id}/lista", params={"antes": antes} if antes else {})
        ).json()
        antes = p["proximo"]
        if antes is None:
            break
    if antes:
        await medir("GET /lista (10ª página)", f"{URL}/{caixa.id}/lista", antes=antes)
    # A lista da loja inteira, paginada, bate com a contagem.
    vistos, antes = 0, None
    while True:
        p = (
            await client.get(
                f"{URL}/{caixa.id}/lista",
                params={
                    "loja": loja["chave"],
                    "limite": 100,
                    **({"antes": antes} if antes else {}),
                },
            )
        ).json()
        vistos += len(p["itens"])
        antes = p["proximo"]
        if antes is None:
            break
    assert vistos == loja["total"]
    for nome, ms in medidas.items():
        rel.append(f"COM índice: {nome}: {ms:.0f} ms (mediana de 5, pelo ASGI)")

    # O EXPLAIN das consultas da rota.
    base = await indice.base_da_caixa(db, await db.get(type(caixa), caixa.id))
    cx = indice.linhas_da_caixa(caixa.id)
    consultas = {
        "faltam (o que a rota confere a cada chamada)": select(func.count()).select_from(
            indice._faltando(base).subquery()
        ),
        "contar pastas (sem loja)": select(MailCaixaIndice.pasta_chave, func.count())
        .where(MailCaixaIndice.mailbox_id == caixa.id)
        .group_by(MailCaixaIndice.pasta_chave),
        "contar lojas (índice ⟕ meta)": select(cx.c.chave, func.count()).group_by(cx.c.chave),
        "lista Todas, 1ª página": select(cx)
        .order_by(cx.c.recebido_em.desc(), cx.c.message_id.desc())
        .limit(51),
        "lista da pasta": select(cx)
        .where(cx.c.pasta_chave == pasta["id"])
        .order_by(cx.c.recebido_em.desc(), cx.c.message_id.desc())
        .limit(51),
        "lista da loja": select(cx)
        .where(cx.c.chave.in_(indice.equivalentes(loja["chave"], base.cad)))
        .order_by(cx.c.recebido_em.desc(), cx.c.message_id.desc())
        .limit(51),
    }
    planos = {}
    for nome, consulta in consultas.items():
        planos[nome] = await _explain(db, consulta)
        rel.append(f"\n── EXPLAIN: {nome}\n{planos[nome]}")
    if MEDIR:
        # Com volume, a lista e a pasta andam pelos índices da tabela, sem ler tudo.
        assert "ix_mail_caixa_indice_caixa_recebido" in planos["lista Todas, 1ª página"]
        assert "ix_mail_caixa_indice_caixa_pasta" in planos["lista da pasta"]
    # Nada da ponte foi tocado.
    assert await db.scalar(select(func.count()).select_from(MailMessageMeta)) == 0
    del calculadas

    # Meses de ponte ligada: METADE dos e-mails com meta (a loja da ponte).
    # As consultas que juntam a meta continuam baratas.
    metade = (
        await db.scalars(
            select(MailCaixaIndice.message_id)
            .where(MailCaixaIndice.mailbox_id == caixa.id)
            .limit(N // 2)
        )
    ).all()
    metas = [
        {
            "message_id": mid,
            "mailbox_id": caixa.id,
            "estado": "gravado",
            "store_info_id": cena.loja_ml.id,
            "integration_id": cena.ml.id,
        }
        for mid in metade
    ]
    for i in range(0, len(metas), 2000):
        await db.execute(insert(MailMessageMeta), metas[i : i + 2000])
    await db.commit()
    await db.execute(text("ANALYZE mail_message_meta"))
    await db.commit()
    for nome in ("contar lojas (índice ⟕ meta)", "lista da loja"):
        plano = await _explain(db, consultas[nome])
        rel.append(f"\n── EXPLAIN com {len(metas)} metas: {nome}\n{plano}")
    await medir("GET /pastas (com metas)", f"{URL}/{caixa.id}/pastas")
    await medir("GET /lista?loja (com metas)", f"{URL}/{caixa.id}/lista", loja=loja["chave"])
    for nome in ("GET /pastas (com metas)", "GET /lista?loja (com metas)"):
        rel.append(f"COM índice: {nome}: {medidas[nome]:.0f} ms (mediana de 5, pelo ASGI)")

    relatorio = "\n".join(rel)
    print("\n" + relatorio)
    if SAIDA:
        with open(SAIDA, "w", encoding="utf-8") as f:
            f.write(relatorio + "\n")
