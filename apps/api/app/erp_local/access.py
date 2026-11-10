"""Who may do what, by module, like the user profiles in Bling.

Only applies behind the gate, where the proxy says who is acting. An unknown person sees nothing,
and a command this map does not know belongs to administrators alone.
"""

import json
import os

from app.erp_local.core import ERPError, dumps, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS access_users (
 actor TEXT PRIMARY KEY, profile TEXT NOT NULL, active INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS access_profiles (
 name TEXT PRIMARY KEY, areas TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
COMMANDS = {"access.user.save", "access.profile.save"}
ADMIN = "administrador"
ADMINS_ENV = "ERP_ADMINS"
AREAS = {
    "cadastros": (
        "product.",
        "category.",
        "contact.",
        "seller.",
        "catalog.",
        "price_list.",
        "payment_method.",
        "depot.",
        "valuation.",
    ),
    "vendas": ("order.", "orders.", "proposal.", "pos.", "cash."),
    "estoque": ("stock.", "inventory.", "production."),
    "compras": ("purchase.", "purchases.", "inbound."),
    "fiscal": ("fiscal.",),
    "financeiro": ("finance.", "bank.", "account."),
    "expedicao": ("logistics.", "returns."),
    "integracoes": ("ingest.", "native.", "channel.", "recipient."),
}
EXACT = {"kit": "cadastros", "service": "cadastros"}
PROFILES = {
    ADMIN: sorted(AREAS),
    "vendas": ["cadastros", "vendas"],
    "estoque": ["cadastros", "compras", "estoque"],
    "fiscal": ["fiscal", "vendas"],
    "financeiro": ["financeiro"],
    "expedicao": ["expedicao"],
    "consulta": [],
    # Active DaVinci users while Bling owns the stores: they see and print labels, never post.
    "acompanhamento": ["cadastros", "vendas", "estoque", "expedicao", "integracoes"],
    # The same DaVinci users once a store is switched: they run orders, situations, stock and
    # shipping as they do in Bling today. Fiscal and finance stay with a profile given here.
    "operacao": ["cadastros", "vendas", "estoque", "expedicao", "integracoes"],
}
FOLLOW = "acompanhamento"
OPERATE = "operacao"
# Reading actions a follower may still run: labels, tracking and papers of the stores' sales.
FOLLOW_POSTS = ("/api/erp-local/store-sales/",)
# What each area keeps out of the page data for people without it.
STATE_AREAS = {
    "finance": "financeiro",
    "statements": "financeiro",
    "cash_register": "financeiro",
    "procurement": "compras",
    "inbound": "compras",
    "fiscal": "fiscal",
    "invoicing": "fiscal",
    "multi_company": "fiscal",
    "logistics": "expedicao",
    "returns": "expedicao",
    "native_orders": "integracoes",
    "ingest": "integracoes",
    "integration_registry": "integracoes",
    "production": "estoque",
}
ROUTE_AREAS = {
    "/api/erp-local/fiscal": "fiscal",
    "/api/erp-local/inbound": "compras",
    "/api/erp-local/finance": "financeiro",
    "/api/erp-local/store-sales": "expedicao",
    "/api/erp-local/store-objects": "expedicao",
    "/api/erp-local/invoices": "fiscal",
    "/api/erp-local/catalog": "cadastros",
    "/api/erp-local/contacts": "cadastros",
    "/api/erp-local/logistics": "expedicao",
    "/api/erp-local/price-preview": "vendas",
}


def enforced():
    return os.environ.get("ERP_TRUST_PROXY") == "1"


def area_of(kind):
    if kind in EXACT:
        return EXACT[kind]
    return next((a for a, prefixes in AREAS.items() if kind.startswith(prefixes)), None)


def route_area(path):
    return next((area for route, area in ROUTE_AREAS.items() if path.startswith(route)), None)


def profiles(db):
    stored = {
        row["name"]: json.loads(row["areas"]) for row in db.execute("SELECT * FROM access_profiles")
    }
    return {**PROFILES, **stored, ADMIN: PROFILES[ADMIN]}


def who(db, actor, role=None):
    """Administrators named on the server, or admins in DaVinci, always get in, so nobody is locked
    out of granting. Other active DaVinci users without a profile here follow, read-only, while
    Bling owns the stores, and operate once a store is switched."""
    admins = {a.strip().casefold() for a in os.environ.get(ADMINS_ENV, "").split(",") if a.strip()}
    if actor and (actor.casefold() in admins or role == "admin"):
        return {"actor": actor, "profile": ADMIN, "areas": PROFILES[ADMIN], "admin": True}
    row = db.execute(
        "SELECT profile FROM access_users WHERE actor=? AND active=1", (actor or "",)
    ).fetchone()
    if not row:
        if actor and role == "user":
            from app.erp_local.bling_mirror import switched

            if switched(db):
                return {
                    "actor": actor,
                    "profile": OPERATE,
                    "areas": PROFILES[OPERATE],
                    "admin": False,
                }
            return {
                "actor": actor,
                "profile": FOLLOW,
                "areas": PROFILES[FOLLOW],
                "admin": False,
                "read_only": True,
            }
        return None
    known = profiles(db)
    return {
        "actor": actor,
        "profile": row["profile"],
        "areas": known.get(row["profile"], []),
        "admin": row["profile"] == ADMIN,
    }


def require(db, actor, area, *, admin_only=False, role=None, write=False):
    person = who(db, actor, role)
    if not person:
        raise ERPError("access_unknown", "Usuário sem acesso ao ERP.", 403)
    if person["admin"]:
        return person
    if write and person.get("read_only"):
        raise ERPError(
            "access_follow", "Enquanto o Bling cuida das lojas, só administradores lançam.", 403
        )
    if admin_only or area is None or area not in person["areas"]:
        raise ERPError("access_denied", "Seu perfil não permite esta operação.", 403)
    return person


def check_command(db, actor, kind, role=None):
    return require(db, actor, area_of(kind), role=role, write=True)


def blank(value):
    """Same shape, no content: the page keeps working without what this person cannot see."""
    if isinstance(value, dict):
        return {key: blank(item) for key, item in value.items()}
    return [] if isinstance(value, list) else None


def filter_state(payload, person):
    if person["admin"]:
        return payload
    allowed = set(person["areas"])
    result = {
        key: blank(value) if STATE_AREAS.get(key) not in (None, *allowed) else value
        for key, value in payload.items()
    }
    if "access" in result:
        result["access"] = blank(result["access"])
    return result


def apply(erp, db, kind, p):
    now = erp.now()
    if kind == "access.profile.save":
        if not isinstance(p, dict) or set(p) != {"name", "areas"}:
            raise ERPError("access_fields", "Informe nome e módulos do perfil.")
        name = text(p["name"], "perfil")
        if name in PROFILES:
            raise ERPError("access_builtin", "Este perfil é fixo. Crie outro nome.", 409)
        areas = p["areas"]
        if not isinstance(areas, list) or set(areas) - set(AREAS):
            raise ERPError("access_areas", "Módulo desconhecido.")
        db.execute(
            "INSERT OR REPLACE INTO access_profiles VALUES (?,?,?)",
            (name, dumps(sorted(set(areas))), now),
        )
        return {"name": name}
    if not isinstance(p, dict) or set(p) != {"actor", "profile", "active"}:
        raise ERPError("access_fields", "Informe usuário, perfil e situação.")
    actor, profile = text(p["actor"], "usuário").casefold(), text(p["profile"], "perfil")
    if profile not in profiles(db):
        raise ERPError("access_profile", "Perfil desconhecido.")
    if type(p["active"]) is not bool:
        raise ERPError("access_fields", "Situação inválida.")
    db.execute(
        "INSERT OR REPLACE INTO access_users VALUES (?,?,?,?)",
        (actor, profile, int(p["active"]), now),
    )
    return {"actor": actor}


def snapshot(db):
    return {
        "enforced": enforced(),
        "users": [
            {"actor": r["actor"], "profile": r["profile"], "active": bool(r["active"])}
            for r in db.execute("SELECT * FROM access_users ORDER BY actor")
        ],
        "profiles": profiles(db),
        "areas": sorted(AREAS),
    }
