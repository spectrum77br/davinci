"""Personagem ganha voz, vídeo de referência e um perfil de verdade

Eduardo, 22/09/2026, mostrando a pasta `Personas` que a equipe já usa: cada
persona tem MUITO mais do que a 0299 modelou. Medido nas 8 pastas:

- as 8 têm um MP3 de voz ("voz leonardo", "Voz Maisa", "dona nene", "Tcar");
- as 8 têm um `.textClipping` com link de referência em vídeo (YouTube Shorts);
- 7 têm 1 imagem, 1 tem 4 (variações de expressão);
- os textos descrevem perfil, aplicação em venda e expressões disponíveis —
  "pedreiro autônomo brasileiro, 38 anos, negro, magro e resistente",
  "funciona bem em anúncios que usam a fórmula 'descoberta genuína'".

## Por que a tabela muda de nome

`marketing_personagem_imagens` nasceu ontem guardando só imagem. Agora guarda
o MP3 da voz também, e tabela chamada "imagens" cheia de áudio é o tipo de
nome que engana quem chegar depois. Renomear é de graça: medido em produção
hoje, a tabela tem **0 linhas** (nenhum personagem foi cadastrado ainda) — o
rename não move um byte.

## A etiqueta deixa de ser o centro

Eduardo: "não daria pra upar a imagem no marketing ali e a pessoa simplesmente
baixar ela, pois essa etiqueta tem chance de quebrar ou não?". Tem. Os nomes
`hf_20260918_…` e `exec-8a44219f-…` são ids DENTRO do gerador que produziu a
imagem: somem se o asset for apagado lá, se trocar de conta ou se a agência
usar outra ferramenta. O ARQUIVO é o que dura e funciona em qualquer lugar.

A coluna `referencia` continua — vira atalho pra quem usa a mesma ferramenta,
não a fonte da verdade. Quem manda agora é o arquivo, e por isso a rota de
bytes passou a aceitar download.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0301_personagem_voz_video"
down_revision: str | None = "0300_ouvidoria"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "davinci"


def upgrade() -> None:
    # A tabela está vazia em produção (conferido em 22/09/2026), então o
    # rename é instantâneo e não há linha pra classificar.
    op.rename_table(
        "marketing_personagem_imagens", "marketing_personagem_arquivos", schema=SCHEMA
    )
    # "imagem" | "voz". Server default pra linha antiga (não há nenhuma) e pro
    # INSERT que não informe.
    op.add_column(
        "marketing_personagem_arquivos",
        sa.Column("tipo", sa.String(16), server_default=sa.text("'imagem'"), nullable=False),
        schema=SCHEMA,
    )
    # O link do vídeo de referência. Só http/https, validado na escrita — ele
    # vira href no portal PHP das agências.
    op.add_column(
        "marketing_personagens",
        sa.Column("video_url", sa.Text(), nullable=True),
        schema=SCHEMA,
    )

    # Os índices e constraints carregam o nome antigo depois do rename; o
    # `create_all` dos testes gera com o nome novo, e schema divergente entre
    # teste e produção é o que faz o teste passar enquanto o servidor quebra.
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_marketing_personagem_imagens_personagem_id "
        f"RENAME TO ix_marketing_personagem_arquivos_personagem_id"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.marketing_personagem_arquivos "
        f"RENAME CONSTRAINT pk_marketing_personagem_imagens "
        f"TO pk_marketing_personagem_arquivos"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.marketing_personagem_arquivos "
        f"RENAME CONSTRAINT fk_marketing_personagem_imagens_personagem_id_marketing_b60b "
        f"TO fk_marketing_personagem_arquivos_personagem_id_marketin_d745"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.marketing_personagem_arquivos "
        f"RENAME CONSTRAINT fk_marketing_personagem_imagens_created_by_users "
        f"TO fk_marketing_personagem_arquivos_created_by_users"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.marketing_personagem_arquivos "
        f"RENAME CONSTRAINT fk_marketing_personagem_arquivos_created_by_users "
        f"TO fk_marketing_personagem_imagens_created_by_users"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.marketing_personagem_arquivos "
        f"RENAME CONSTRAINT fk_marketing_personagem_arquivos_personagem_id_marketin_d745 "
        f"TO fk_marketing_personagem_imagens_personagem_id_marketing_b60b"
    )
    op.execute(
        f"ALTER INDEX {SCHEMA}.ix_marketing_personagem_arquivos_personagem_id "
        f"RENAME TO ix_marketing_personagem_imagens_personagem_id"
    )
    op.drop_column("marketing_personagens", "video_url", schema=SCHEMA)
    op.drop_column("marketing_personagem_arquivos", "tipo", schema=SCHEMA)
    op.rename_table(
        "marketing_personagem_arquivos", "marketing_personagem_imagens", schema=SCHEMA
    )
