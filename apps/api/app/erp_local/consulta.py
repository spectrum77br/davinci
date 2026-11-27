"""Consulta: the ERP open to the team while Bling still owns stock, orders and the register.

While the worker mirrors Bling (stock and order situations) and no store has been switched, any
change made on the screen would be overwritten by the next mirror round or would drift from Bling.
So the screen only reads: every change is refused with the same message, and the page says why.
The worker keeps reading and mirroring as before, since it does not go through these routes.
The only writes let through are the two of CONSULTA_WRITES (Produtos 2 › Configuração).

On by itself on the real base while Bling owns it (mirror kept, no store switched). Off in the
homologation (a copy where every store is the ERP's) and in the demo (no mirror). ERP_CONSULTA=1
forces it on, ERP_CONSULTA=0 off.
"""

import os
import sqlite3

ENV = "ERP_CONSULTA"
MESSAGE = (
    "Modo consulta: o Bling manda em estoque, pedidos e cadastro até a troca. "
    "Faça a alteração no Bling."
)
# Posts that only read: a price quote, papers made from what the ERP already has, the tracking
# the store shows and a fresh read of DaVinci's copy of Bling. Labels stay out: downloading one at
# Mercado Livre marks it printed there.
READ_POSTS = (
    "/api/erp-local/price-preview",
    "/api/erp-local/store-sales/tracking",
    "/api/erp-local/reference-refresh",
    # Orders and buyers from UpSeller's export (Temu, Amazon): intake, nothing goes to Bling.
    "/api/erp-local/upseller-import",
    "/api/erp-local/upseller-import/rodada-vazia",
)
READ_PREFIXES = ("/api/erp-local/store-sales/papers/",)
# The only changes accepted while Bling owns the base, by exact path (M-45 and M-47, asked by the
# administrator on 25/11/2026 for Produtos 2 › Configuração › Alterar em massa):
# - the category of the selected products: it is DaVinci's own classification (the Categoria
#   filter of Produtos 2 reads it); Bling keeps its own, no mirror round copies it back, and the
#   manual register load leaves a category chosen here alone (catalog_metadata);
# - the ERP's own sale price of the selected products: it goes to no listing and no store (the
#   listings get the Tabela de Preços), only suggests the price of a manual sale and is the base
#   of the ERP's price lists; Bling's price does not change and the mirror never copies it back.
# Everything else, /api/erp-local/commands included (category.assign, product.price_adjust), is
# still refused.
CONSULTA_WRITES = (
    "/api/erp-local/catalog/bulk-category",
    "/api/erp-local/catalog/bulk-price",
)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _owned_by_bling(db):
    try:
        if db.execute("SELECT 1 FROM homolog_info LIMIT 1").fetchone():
            return False  # the homologation: a copy made to be changed
    except sqlite3.OperationalError:
        pass
    try:
        mirrored = db.execute("SELECT 1 FROM bling_mirror_stock LIMIT 1").fetchone()
    except sqlite3.OperationalError:  # never mirrored: a demo or a new base
        return False
    if not mirrored:
        return False
    try:
        switched = db.execute(
            "SELECT 1 FROM ingest_accounts WHERE authority='own' LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        switched = None
    return not switched


def active(db):
    flag = os.environ.get(ENV, "").strip()
    if flag in {"0", "1"}:
        return flag == "1"
    return _owned_by_bling(db)


def state(db):
    flag = os.environ.get(ENV, "").strip()
    return {
        "active": active(db),
        "reason": "forçado" if flag == "1" else "desligado" if flag == "0" else "automático",
        "message": MESSAGE,
        "read_posts": [*READ_POSTS, *READ_PREFIXES],
        "writes": list(CONSULTA_WRITES),
    }


def refuses(method, path):
    """Whether this request is a change (consulta refuses it) rather than a read or one of the
    writes let through by exact path."""
    if method.upper() in SAFE_METHODS or not path.startswith("/api/erp-local/"):
        return False
    if path in CONSULTA_WRITES:
        return False
    return path not in READ_POSTS and not path.startswith(READ_PREFIXES)
