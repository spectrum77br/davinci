"""equipes de vendas: Londres (101), Israel (102) e Marrocos (103) — só dado

Eduardo, 30/10/2026: "agora vai ter 3 equipes: 1 - londres, 2 - Israel,
3 - marrocos" (a "Separação de contas": Vendedor 1 e 2 Celular/Eletro,
Vendedor 3 Mala). Os números são os de sempre (`store_info.sales_team` /
`users.sales_teams`); muda QUEM está em cada um — até hoje 101, 102 e 103
eram códigos de acesso soltos e o londres tinha os três.

* `store_info.sales_team`: as 77 contas da planilha da separação (30 na
  Londres, 29 na Israel, 18 na Marrocos) e duas do ML arquivadas que vão para
  a Israel: o nexus (com a Amazon nexus) e o atv (estava sem equipe; com as
  outras ATV). 36 mudam; 43 já estavam na equipe certa e só são conferidas
  (sem gravar — o gatilho mexeria no `updated_at`).
* `users.sales_teams`: londres [101] (tinha [101, 102, 103]), israel [102] e
  marrocos [103] (os dois sem equipe = viam tudo). As tags de estoque
  (`stock_tags`) não mudam: a equipe filtra POR CIMA delas.
* Fica como está: venda presencial (103), as internas, makisa e agil (sem
  equipe), as arquivadas fora da planilha (amazon jlas 101, amazon inova e ml
  luno 102) e o `commercial_team`. Admin e quem não tem equipe: nada muda.

Tudo pelo id, nunca pelo nome — barbosa, mega, eron e inova existem em mais de
uma equipe. Cada linha confere o valor lido na produção em 30/10: se o id não
existe, não é o usuário certo ou o valor é outro (nem o de 30/10, nem o novo),
a migration para inteira e nada muda; a mensagem diz qual linha. Quem já
estiver no valor novo (mudado à mão na tela) só é contado.

O `integration_id` não muda: as Shopee atlas e fiore, cuja integração tem
outro nome ("Jlas", " Kia"), chegam à equipe pela ponte do cadastro de
Empresas em `team_scope.resolve_team_scope` — gravar o FK trocaria o nome
delas no Atendimento, que desde a 0440 é separado por pessoa.

DOWNGRADE: desfaz só o que a 0441 mudou e ninguém mexeu depois. "Mudou" vem do
retrato de 30/10 embutido nas listas (o valor de 30/10 diferente do novo — as
que já estavam certas a 0441 não gravou e a volta não "devolve"); "ninguém
mexeu" = a linha ainda tem o valor que a 0441 gravou. A que foi mexida depois
fica como está e sai na mensagem. A identidade é conferida como na ida (o id
existe; o usuário tem o nome e o papel `user`): se falha, para inteira e nada
muda. Limite: quem foi posto à mão no valor novo ANTES da 0441 não se distingue
de quem ela gravou e volta para o de 30/10 junto (a ida lista esses na saída
do deploy: "já estava ... (mudada à mão)").

Revision ID: 0441_equipes_londres_israel_marrocos
Revises: 0440_atendimento_lojas_pessoa
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0441_equipes_londres_israel_marrocos"
down_revision: str | None = "0440_atendimento_lojas_pessoa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"
LONDRES, ISRAEL, MARROCOS = 101, 102, 103

# (id em store_info, "plataforma conta" — só para a mensagem, sales_team em
# 30/10, sales_team novo), na ordem da planilha. Os de 30/10 foram lidos na
# produção (BEGIN READ ONLY): os 79 ids existem e batem com a planilha.
LOJAS: tuple[tuple[str, str, int | None, int], ...] = (
    # 1 - Londres (30)
    ("cc193d4b-e938-430e-9453-bb7793f31f97", "shopee vortan", 101, LONDRES),
    ("8119afde-68e8-4b23-a4f3-ae9b9d5b808d", "shopee barbosa", 101, LONDRES),
    ("47746477-f191-47fe-9d52-8a8f84fe00ff", "shopee fiore", 102, LONDRES),
    ("40c6640d-da6b-4f1e-9986-4ff08613d7be", "shopee mini", 101, LONDRES),
    ("c76e998a-2371-4f45-937c-48301cbdeb6a", "shopee eron", None, LONDRES),
    ("2641589a-f7fc-42ef-a804-bd0dcdb2f172", "shopee lucas mei", 103, LONDRES),
    ("2c761d06-6c37-4646-ad42-5e244a39766b", "shopee oliveira", 101, LONDRES),
    ("4d633217-bb26-4599-871d-bb4959ee2428", "ml jlas2", 102, LONDRES),
    ("53c872e5-b6ec-47c0-bf12-02c36894e3bd", "ml kfa2", 101, LONDRES),
    ("5e4f4e12-4be5-4731-9f35-98be4c6db1f1", "ml mini", 102, LONDRES),
    ("435f6a44-a640-4761-95fe-62d75570fb84", "ml velasco", 102, LONDRES),
    ("1ae35639-b498-4e3a-8884-ee199e983ed1", "ml kia", 102, LONDRES),
    ("168ab928-71a5-46d0-b18c-ae454916f903", "ml zorvex", 101, LONDRES),
    ("72bbd71e-0363-420f-960d-efbf5d71ae24", "ml jlas", 101, LONDRES),
    ("bad659bb-a30c-4ef4-8c3d-6b9bf9022e9f", "ml counhago", 101, LONDRES),
    ("a6efe85e-257b-4676-aad0-263a1bfd0a45", "ml mega", 102, LONDRES),
    ("26fd89a8-0226-4216-a80a-54510789d507", "ml eron", 101, LONDRES),
    ("79044eae-87e2-4fbf-b31a-6d2661962fc7", "ml eron2", 101, LONDRES),
    ("6d8ab77a-629d-4bbd-bd0a-82f2274354ee", "ml fiore", None, LONDRES),
    ("9a73d666-675c-4ccf-901c-4e53b469fe95", "ml lucas mei", 102, LONDRES),
    ("dabfdad7-4e25-47d5-bf52-6627addbb48d", "ml oliveira", 101, LONDRES),
    ("1742e6b8-49a7-4cd3-a001-c31b985bf2b1", "tiktok mini", 102, LONDRES),
    ("9600456d-a48e-4369-adb0-1bc1e2bf462b", "tiktok barbosa", 101, LONDRES),
    ("eab34e81-942d-489b-b23d-d44048bcfa76", "tiktok jlas", 101, LONDRES),
    ("65d51421-f2ec-41cc-b521-87d6c62afc77", "tiktok eron", 101, LONDRES),
    ("26b64883-bfd8-402e-8612-3474c4a81881", "amazon kia", 102, LONDRES),
    ("fa9aedcc-4127-409e-a9b3-7837da9d6960", "amazon vortan", 102, LONDRES),
    ("0b1af682-ca23-449b-9bbe-68f5f1769c65", "magalu vortan", 102, LONDRES),
    ("24e0535c-5495-4a4e-a657-a43834c1e3c8", "shein kia", 102, LONDRES),
    ("0b28e760-0701-47bc-bea8-e4f6497435c1", "temu jlas", 102, LONDRES),
    # 2 - Israel (29 da planilha + o nexus e o atv do ML, arquivados)
    ("3ff5a177-2cd1-4cdb-9704-44c2ba9678b6", "shopee atv", 102, ISRAEL),
    ("732c820d-c03a-4b8b-8ff4-f9b6c512fa3e", "shopee atlas", 102, ISRAEL),
    ("1ef01e6d-7056-4ff4-a975-e2f0637affa2", "shopee mega", 101, ISRAEL),
    ("c7072c6f-78ae-4461-a89a-efdec41c9a0e", "shopee vita", 101, ISRAEL),
    ("684497ca-d5fe-46d7-a9a5-55474a1f7b09", "shopee victor mei", 102, ISRAEL),
    ("60e47872-f90c-45f8-9865-6090344b405b", "shopee aguiar", 101, ISRAEL),
    ("291bd71a-c1d2-47f8-a789-f7a24c21d160", "shopee vr", None, ISRAEL),
    ("cee7b392-e740-4e6c-bbde-97a741abe6c3", "shopee injox", 102, ISRAEL),
    ("3547237d-179c-4558-8c08-ecd0d7e00c8c", "shopee luno", 102, ISRAEL),
    ("cddb957d-b096-49cc-9a98-b34b720c09f3", "ml aguiar", 101, ISRAEL),
    ("d0243d20-7725-47d0-9afd-237078e6f662", "ml injox", 102, ISRAEL),
    ("a186f05e-ec79-446e-a091-c8a3a8d0477d", "ml aguiar2", 102, ISRAEL),
    ("4b202e35-7aa8-4f96-aafc-af113153d1a6", "ml inova", 101, ISRAEL),
    ("c5a14e1d-c665-48a3-9a58-120d0e50febd", "ml victor mei", 101, ISRAEL),
    ("9d99689b-6e9f-43cd-91fa-feab243007da", "ml barbosa", 102, ISRAEL),
    ("adac3d3b-86df-47fa-b948-9eccf1750e06", "ml vita", 101, ISRAEL),
    ("00ab5a2b-db5c-401e-a186-c3ebfae3de96", "ml dream2", 102, ISRAEL),
    ("ce850160-2b1f-414d-b3f4-9171554f2033", "ml vr", None, ISRAEL),
    ("e88fbc79-d125-4d4b-bcc1-3225e2633009", "ml atlas", None, ISRAEL),
    ("59ddaa0f-c9cd-4f14-8a6b-8d16ccab0b59", "tiktok atv", 102, ISRAEL),
    ("754afe28-99aa-40ad-a32b-7e3292b70cea", "tiktok injox", 102, ISRAEL),
    ("7f2afb59-130f-4a83-98e4-e976f506e564", "tiktok vr", None, ISRAEL),
    ("025044b9-aeeb-4ac1-8e7a-f8322644c738", "amazon vr", None, ISRAEL),
    ("d0691dd8-593e-4f06-ba84-a0162ea7d973", "amazon atlas", None, ISRAEL),
    ("0935d97f-6691-4dbf-bb01-e86a5881ba49", "amazon nexus", 102, ISRAEL),
    ("c487d36c-c412-4891-8718-ff0770c01522", "magalu vr", None, ISRAEL),
    ("48283d21-2d67-413e-89f7-0d19051dbcf8", "carrefour vr", None, ISRAEL),
    ("c893e1bd-dfec-4b48-8ac3-d294c4e419bd", "temu atv", 102, ISRAEL),
    ("27ff9036-1382-4f17-9556-eeb992dc8029", "aliexpress vita", 103, ISRAEL),
    # Arquivada; vai junto com a Amazon nexus (30/10).
    ("ee2fedb3-d706-448d-a3d9-70a5651b74f7", "ml nexus", 103, ISRAEL),
    # Arquivada, sem equipe; vai com as outras ATV (shopee, tiktok e temu, todas
    # na Israel), como o nexus (30/10).
    ("5fd84133-5a17-4785-ba40-04e486b4638a", "mercadolivre atv", None, ISRAEL),
    # 3 - Marrocos (18)
    ("93dea000-6875-4957-9480-123299b14603", "shopee inova", 103, MARROCOS),
    ("fcc226b8-dd01-4822-87c5-fd7c169a167e", "shopee minas", 103, MARROCOS),
    ("9ced0bf8-b998-494e-bf6c-8a55f37c02a8", "shopee kfa", 103, MARROCOS),
    ("7435637a-2c97-4d81-84e7-3fa515a9a476", "shopee poofy", 103, MARROCOS),
    ("d2256e0c-1d27-4c4c-83ab-0bce70b99946", "ml marquezini", 103, MARROCOS),
    ("c336a175-4866-4d43-9529-2c5a7ce1976e", "ml forpaper", 103, MARROCOS),
    ("98b1a62c-3cc3-4665-8e1a-08f328ee7857", "ml kfa", 103, MARROCOS),
    ("7a3040c2-0cb3-41e1-aa15-e9815b17f5a5", "ml poofy", 103, MARROCOS),
    ("281a6c6d-6b70-477f-869d-6d8e6b12623c", "tiktok poofy", 103, MARROCOS),
    ("2145c9ec-dbea-48ef-a187-dbcc9505e2a6", "tiktok inova", 103, MARROCOS),
    ("567b13c5-1b12-47a1-b3c2-a25a109ef487", "amazon kfa", 103, MARROCOS),
    ("6f5e99c8-b598-4b88-b9b5-9123ac4fc97e", "amazon poofy", 103, MARROCOS),
    ("f071e96a-ba39-4ab0-b55a-f0ad7575373f", "magalu poofy", 103, MARROCOS),
    ("6a5307ec-25e2-4701-ba88-42a17ed2e680", "carrefour poofy", None, MARROCOS),
    ("9b4c286c-c2ba-4d9e-b074-41d7a32e5b31", "netshoes poofy", None, MARROCOS),
    ("794c39af-22a5-4ba1-b352-1c42c8550de9", "shein kfa", 103, MARROCOS),
    ("2cff3c97-51d7-41f9-b7c4-3295e130b8af", "temu barbosa", 103, MARROCOS),
    ("551e40cf-6b7f-46e9-951b-67c82bf26456", "temu eron", 103, MARROCOS),
)

# (id em users, nome, sales_teams em 30/10 — None é SQL NULL, sem equipe — e o
# novo). Lidos na produção em 30/10, só com papel `user` e o nome exato.
USUARIOS: tuple[tuple[str, str, list[int] | None, list[int]], ...] = (
    ("8315dcfd-8290-453c-a3d4-39cf470b1770", "londres", [101, 102, 103], [LONDRES]),
    ("09660c5d-6c3b-40d8-af15-c8e815148994", "israel", None, [ISRAEL]),
    ("8731f741-396d-4b86-9969-b4f2638d2cc6", "marrocos", None, [MARROCOS]),
)


def _t(nome: str) -> str:
    return f'"{SCHEMA}"."{nome}"'


def _equipes(valor: list[int] | None) -> list[int]:
    # SQL NULL, JSON null e [] são o mesmo "sem equipe": a tela de Usuários
    # grava JSON null quando esvazia a lista.
    return sorted({int(v) for v in valor}) if valor else []


def _loja(conn: sa.Connection, id_: str, conta: str) -> int | None:
    linha = conn.execute(
        sa.text(
            f"SELECT sales_team FROM {_t('store_info')} "  # noqa: S608
            "WHERE id = CAST(:id AS uuid) FOR UPDATE"
        ),
        {"id": id_},
    ).first()
    if linha is None:
        raise RuntimeError(f"[0441] a loja {conta} ({id_}) não existe — nada mudou")
    return linha.sales_team


def _gravar_loja(conn: sa.Connection, id_: str, conta: str, equipe: int | None) -> None:
    n = conn.execute(
        sa.text(
            f"UPDATE {_t('store_info')} SET sales_team = :equipe "  # noqa: S608
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": id_, "equipe": equipe},
    ).rowcount
    if n != 1:
        raise RuntimeError(f"[0441] a loja {conta} ({id_}): {n} linha(s), esperava 1 — nada mudou")


def _usuario(conn: sa.Connection, id_: str, nome: str) -> list[int]:
    linha = conn.execute(
        sa.text(
            f"SELECT name, role::text AS papel, sales_teams FROM {_t('users')} "  # noqa: S608
            "WHERE id = CAST(:id AS uuid) FOR UPDATE"
        ),
        {"id": id_},
    ).first()
    if linha is None:
        raise RuntimeError(f"[0441] o usuário {nome} ({id_}) não existe — nada mudou")
    if (linha.name or "").strip().lower() != nome or linha.papel != "user":
        raise RuntimeError(
            f"[0441] o id {id_} não é o {nome} (nome {linha.name!r}, papel {linha.papel})"
            " — nada mudou"
        )
    return _equipes(linha.sales_teams)


def _gravar_usuario(conn: sa.Connection, id_: str, nome: str, equipes: list[int] | None) -> None:
    n = conn.execute(
        sa.text(
            f"UPDATE {_t('users')} SET sales_teams = CAST(:equipes AS jsonb) "  # noqa: S608
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": id_, "equipes": None if equipes is None else json.dumps(equipes)},
    ).rowcount
    if n != 1:
        raise RuntimeError(
            f"[0441] o usuário {nome} ({id_}): {n} linha(s), esperava 1 — nada mudou"
        )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '30s'")
    conn = op.get_bind()
    mudaram = certas = 0
    for id_, conta, antes, depois in LOJAS:
        atual = _loja(conn, id_, conta)
        if atual == depois:
            if antes != depois:
                print(f"[0441] a loja {conta} já estava na {depois} (mudada à mão)")
            certas += 1
            continue
        if atual != antes:
            raise RuntimeError(
                f"[0441] a loja {conta} ({id_}) está na equipe {atual}; esperava {antes}"
                f" (30/10) ou {depois} — nada mudou"
            )
        _gravar_loja(conn, id_, conta, depois)
        mudaram += 1
    print(f"[0441] lojas: {mudaram} mudaram de equipe, {certas} já estavam na certa")

    for id_, nome, antes, depois in USUARIOS:
        atual = _usuario(conn, id_, nome)
        if atual == depois:
            print(f"[0441] {nome}: já estava em {depois}")
            continue
        if atual != _equipes(antes):
            raise RuntimeError(
                f"[0441] o {nome} ({id_}) está com {atual or 'sem equipe'}; esperava"
                f" {antes or 'sem equipe'} (30/10) ou {depois} — nada mudou"
            )
        _gravar_usuario(conn, id_, nome, depois)
        print(f"[0441] {nome}: {antes or 'sem equipe'} → {depois}")


def downgrade() -> None:
    # Volta só o que a 0441 mudou (o de 30/10 ≠ o novo) e ainda está como ela
    # deixou; a linha mexida depois fica como está. Ver o docstring.
    op.execute("SET LOCAL lock_timeout = '30s'")
    conn = op.get_bind()
    voltaram = ficaram = 0
    for id_, conta, antes, depois in LOJAS:
        if antes == depois:
            continue  # já estava na certa em 30/10: a 0441 não gravou nesta
        atual = _loja(conn, id_, conta)
        if atual != depois:
            print(
                f"[0441] volta: a loja {conta} está na {atual}, não na {depois}"
                " (mexida depois) — fica como está"
            )
            ficaram += 1
            continue
        _gravar_loja(conn, id_, conta, antes)
        voltaram += 1
    print(
        f"[0441] volta: {voltaram} loja(s) de volta à equipe de 30/10,"
        f" {ficaram} mexida(s) depois ficaram como estão"
    )

    for id_, nome, antes, depois in USUARIOS:
        if _equipes(antes) == _equipes(depois):
            continue  # já estava certo em 30/10: a 0441 não gravou neste
        atual = _usuario(conn, id_, nome)
        if atual != _equipes(depois):
            print(
                f"[0441] volta: {nome} está com {atual or 'sem equipe'}, não {depois}"
                " (mexido depois) — fica como está"
            )
            continue
        _gravar_usuario(conn, id_, nome, antes)
        print(f"[0441] volta: {nome} de volta a {antes or 'sem equipe'}")
