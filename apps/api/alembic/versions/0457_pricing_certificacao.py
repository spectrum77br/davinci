"""pricing_products.certificacao_id — o certificado do produto gravado

Tabela de Preços › Produtos (05/11/2026): as colunas Modelo / Nº certificado /
Validade vinham da Certificações pelo código no FIM do nome ("… - USM003").
No mesmo dia o Eduardo limpou os nomes (16:33–16:48, "uranyx A17 pro max
12.128") e as colunas esvaziaram. Agora o vínculo é gravado: a coluna Modelo
vira uma lista para escolher o certificado, e o código no nome só vale para
produto ainda sem vínculo.

O preenchimento é um RETRATO dos 41 produtos que casavam pelo nome
antigo (calculado em 05/11 sobre a cópia de antes da limpeza) + a airfryer
barbecue, cujo SKU (uaf002m1) é o modelo do certificado "airfryer barbecue".
Só grava quando produto e certificado existem e o produto ainda está sem
vínculo; em banco de teste não acha ninguém e não faz nada.

DOWNGRADE: tira a coluna (some o vínculo; o nome volta a ser a única fonte).

Revision ID: 0457_pricing_certificacao
Revises: 0456_cairo_atendimento_todas_lojas
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0457_pricing_certificacao"
down_revision: str | None = "0456_cairo_atendimento_todas_lojas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constante de módulo: o teste troca pelo schema de teste.
SCHEMA = "davinci"

# (pricing_products.id, financeiro_suprimentos.id) — produção, 05/11/2026.
VINCULOS: list[tuple[str, str]] = [
    # uaf001m1.110 airfryer vidro UAF001 M1 MODERNA-0069/25 → UAF001-M1
    ("e6ddc82b-c3e0-45e1-bf55-89b807b08c62", "819b3cf6-76bb-4eb5-839b-c42c2b8dd78b"),
    # ucm001m1.110 cafeteira UCM001 M1 MODERNA-0070/25 → UCM001-M1
    ("5d81668a-5374-4622-8f9b-427cc0403566", "4453be71-7288-42b3-9874-bc0caadce035"),
    # ucw001m1 cookware UCW001 → UCW001
    ("8c52d2a7-0428-4498-928a-bf0d36c2c40a", "e7c8edef-1709-4a0d-a042-b99a15c5237f"),
    # usl001m1.110 sluchie USL001 M1 MODERNA-0071/25 → USL001-M1
    ("664b12bb-0d8e-440a-a1da-f63f71fb5035", "b28531c6-33b4-48e4-a7cb-b4d3d3c74256"),
    # uco001m1.110 smart cooking UCO001 M1 MODERNA-0072/26 → UCO001-M1
    ("1c7779a2-617d-41e8-b41e-5b24ca7542db", "9bd880ca-7523-4bff-ae9a-61cf5144e252"),
    # dg052 uranyx A17 pro max 12.128 prata/preto/laranja -  → USM003
    ("92068562-3ef7-46b7-bad2-e383849c740a", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # dg055 uranyx A17 pro max 12.64 prata/preto/laranja - U → USM003
    ("28a62e2f-d379-4c8d-8ea6-13fe11f6e88d", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # dg088 uranyx A18 pro max - S5 edition 16.128 laranja/p → USM003
    ("70b8a0bd-e019-4b80-8dc4-5b1c0551cab5", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # dg109 uranyx A18 pro max 16.128 burgundy/azul - USM003 → USM003
    ("bca0a4b1-b969-4e95-ae3b-d09c9aeb0642", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # a019 uranyx airtag - UAT001 → AirTag UAT001
    ("7e3aedac-7828-46a2-aafd-fdc50a0375a0", "58cfbe8f-3913-499e-b197-7cecd256d36c"),
    # dg106 uranyx C17 16.128 azul/laranja/prata - USM003 → USM003
    ("b0ebaefc-e8ce-4d64-b170-fe5a96a7e4e9", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # dg074 uranyx C2 16.128 - USM002 → USM002
    ("665b362b-28fd-4d11-afb9-6862c4e9fffb", "07eb2d83-a13a-4369-9523-b47d4eac17b7"),
    # dg078 uranyx C3 16.128 - USM002 → USM002
    ("6ffbb5c1-59f3-4c57-aeab-f309e1cb9caf", "07eb2d83-a13a-4369-9523-b47d4eac17b7"),
    # dg060 uranyx C68 plus 16.128 - USM002 → USM002
    ("95f3fe18-398e-4a74-bbdb-c44d4416ea70", "07eb2d83-a13a-4369-9523-b47d4eac17b7"),
    # dg064 uranyx C68 plus 24.256 - USM002 → USM002
    ("1a73a86a-b540-4670-8b73-618ec87105ad", "07eb2d83-a13a-4369-9523-b47d4eac17b7"),
    # dg097 uranyx C68 plus 24.512 - USM002 → USM002
    ("58b687a9-d2aa-4a7c-a78b-dadb66b38978", "07eb2d83-a13a-4369-9523-b47d4eac17b7"),
    # a017 uranyx carregador 20w - QZ-0180AE2H → QZ-0180AE2H
    ("1a8ad495-cd1d-44b1-9c92-0ec946192ba8", "422f3e43-7cdf-4733-9c30-480757737746"),
    # dg019 uranyx F105 12.64+64 Preto - USM001 → USM001
    ("177e8f77-302b-4e92-ab04-ed2f70d3c040", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg018 uranyx F109 5G 24.256 Preto - USM001 → USM001
    ("7f6cdefb-c23b-4981-bc95-ef7f673dfbde", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg017 uranyx F109S 24.256 Preto - USM001 → USM001
    ("d43fb4c7-62bf-4242-b92c-8a2c7042c512", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg047 uranyx F110 pro 5G 20.128 - USM002 → USM002
    ("af2ff045-c236-460d-ab00-37fc6afaa051", "07eb2d83-a13a-4369-9523-b47d4eac17b7"),
    # dg046 uranyx F110L 8.128 - USM002 → USM002
    ("17684e1e-3577-4be0-80d7-a9555e31530f", "07eb2d83-a13a-4369-9523-b47d4eac17b7"),
    # dg082 uranyx F112 pro 5G 24.256 vermelho/azul/verde/la → USM003
    ("09a1cb09-547e-4af9-b8c1-dc2fa56178a2", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # dg048 uranyx F117 24.256 - USM002 → USM002
    ("3c292f59-9de9-4b37-944e-dd65fd3a1358", "07eb2d83-a13a-4369-9523-b47d4eac17b7"),
    # dg094 uranyx Flat 3C 12.128 Preto/Cinza/Laranja - USM0 → USM003
    ("5cc45710-4bb3-4d46-9cb8-67ccee7312f4", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # a003 uranyx fone - UFB10 → A9 Pro
    ("c66c5a3c-a7f7-4611-b1b2-fd439aaa75c2", "22128d04-dd2e-4a5c-8544-c3fe2de57b3f"),
    # a001 uranyx fone com fio - UFF001 → UFF001
    ("652bdb13-9176-4fa0-bfa6-5a5806775e6b", "e095ad11-0c9f-47fe-b878-67690ef31133"),
    # dg087 uranyx hyper 7s 16.256 - USM003 → USM003
    ("a36e71f4-d695-47ff-9043-a797e40d3e93", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # dg093 uranyx Marine 1 16.128 Preto - USM003 → USM003
    ("b448783c-ffb5-4d42-a600-fe36924c4b54", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # a004 uranyx relogio - USW10 → W69
    ("c01dc46e-07d3-448c-8c43-e834588048ea", "296287e6-ff0a-4a1f-9f5e-597b08fed0c4"),
    # dg103 uranyx S3 20.128 verde/cinza - USM001 → USM001
    ("1ba6e98f-119d-4274-b574-cb113f3916e1", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg002 uranyx S7 16.128 - USM001 → USM001
    ("a3f072e9-8cc9-401e-8488-c1c413c6d183", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg007 uranyx S7 32.256 - USM001 → USM001
    ("64771368-e62c-43fe-8e4a-7a2692c137f0", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # a020 uranyx smart glass - USG001 → USG001
    ("d93bf4fc-5743-45c4-8bc0-d5182f4e6b9a", "f95fbe13-0df7-4972-9c02-ae30497ccb64"),
    # dg101 uranyx t8 12.64+64 preto - USM003 → USM003
    ("a17203a4-7f0b-4fbc-96cd-7b4f07fba05d", "d5829fed-61c8-4c9c-b9a5-d0c212c947aa"),
    # dg023 uranyx wp53 24.128 Preto - USM001 → USM001
    ("c08b85c7-a0ca-4a86-90db-7f73721c4485", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg102 uranyx wp53 pro 24.256 Preto - USM001 → USM001
    ("5db93018-d587-4d40-9307-514f57096501", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg105 uranyx wp53s 16.128 Preto - USM001 → USM001
    ("2c8a579d-a018-43d1-87a2-ba686e72c57e", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg010 uranyx wp60 5g 24.256 cinza/amarelo/branco - USM → USM001
    ("d0127e0d-9eec-4044-a5a2-ee2af049f048", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # dg090 uranyx wp60 5g 36.512 cinza/amarelo/branco - USM → USM001
    ("0c69fc7d-fba6-4801-9fff-e6a31a3d7807", "462a4850-fc23-49c0-8bb1-879710f1f6c0"),
    # uaf002m1 airfryer barbecue → UAF002-M1 (SKU = modelo; nome dizia UAF001 M2)
    ("18f2a042-a063-4c60-ae88-22c0246b0487", "85996e56-daad-4e0e-913e-f0e6b56007d6"),
]


def upgrade() -> None:
    op.add_column(
        "pricing_products",
        sa.Column(
            "certificacao_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.financeiro_suprimentos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    conn = op.get_bind()
    gravados = 0
    for produto, cert in VINCULOS:
        gravados += conn.execute(
            sa.text(
                f'UPDATE "{SCHEMA}".pricing_products p SET certificacao_id = CAST(:cert AS uuid) '  # noqa: S608
                "WHERE p.id = CAST(:produto AS uuid) AND p.certificacao_id IS NULL "
                f'AND EXISTS (SELECT 1 FROM "{SCHEMA}".financeiro_suprimentos s '
                "WHERE s.id = CAST(:cert AS uuid))"
            ),
            {"produto": produto, "cert": cert},
        ).rowcount
    print(f"0457: {gravados} de {len(VINCULOS)} produtos ligados ao certificado")


def downgrade() -> None:
    op.drop_column("pricing_products", "certificacao_id", schema=SCHEMA)
