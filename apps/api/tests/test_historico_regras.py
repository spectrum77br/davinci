"""Regras do Histórico que precisam continuar valendo quando o sistema mudar.

- a máscara de segredo do banco (SQL) e a do servidor (Python) concordam;
- as ações e revelações listadas em nomes.py existem de verdade;
- toda tabela de negócio tem o gatilho, e as do próprio Histórico não.
"""

import pytest
from sqlalchemy import text

from app.historico import nomes
from app.historico import sql as hsql
from app.historico.mascara import e_segredo, limpar_texto
from app.main import app
from app.models.base import Base

SEGREDOS = [
    "password", "password_hash", "password_enc", "senha", "senha_enc", "sac_senha_enc",
    "code_hash", "session_nonce", "credentials", "code_verifier", "basic_auth_b64",
    "authorization_code", "access_token", "refresh_token", "token_enc", "token_hash",
    "juridico_token", "proxy_password", "proxy_user", "blob", "client_secret", "api_key",
    "partner_key", "app_secret", "lwa_client_secret", "planilha_b64", "turnstile_token",
    "secret_hint",
]
NAO_SEGREDOS = [
    "token_expires_at", "has_senha", "senha_status", "senha_enviada_at", "senha_erro",
    "senha_origem", "senha_extra", "ultima_passada", "certificado", "chave", "chave_acesso",
    "cartao", "assinatura", "dedupe_key", "push_key", "name", "sku", "price_override",
    "commission", "observation", "email", "cnpj", "ip", "bling_login", "last_login_at",
    "token_conta_externa", "status",
]
TEXTOS = [
    ("senha do aparelho: 4821", "senha do aparelho: ***"),
    ("a senha é 4821, entregar amanhã", "a senha é ***, entregar amanhã"),
    ("Senha extra liberada", "Senha extra liberada"),
    ("senha do cliente não informada, pedido 2939", "senha do cliente não informada, pedido 2939"),
    ("Authorization: Bearer abcdefgh12345678", "Authorization: Bearer ***"),
    ("https://x.com/cb?code=AbC123&state=zz", "https://x.com/cb?code=***&state=***"),
    ("proxy socks5://joao:s3nh4@1.2.3.4", "proxy socks5://***:***@1.2.3.4"),
    ("token APP_USR-1234567890-abcdef", "token APP_USR-***"),
    ("pedido 293114 enviado", "pedido 293114 enviado"),
]


def test_regra_de_segredo_python():
    assert [n for n in SEGREDOS if not e_segredo(n)] == []
    assert [n for n in NAO_SEGREDOS if e_segredo(n)] == []


@pytest.mark.asyncio
async def test_banco_e_servidor_concordam(db):
    for nome in SEGREDOS + NAO_SEGREDOS:
        r = (await db.execute(text("SELECT historico_e_segredo(:n)"), {"n": nome})).scalar_one()
        assert r is e_segredo(nome), nome
    for entrada, esperado in TEXTOS:
        assert limpar_texto(entrada) == esperado, entrada
        r = (await db.execute(text("SELECT historico_limpa_texto(:t)"), {"t": entrada})).scalar_one()
        assert r == esperado, entrada


def _rotas() -> set[tuple[str, str]]:
    saida = set()
    for r in app.routes:
        for m in getattr(r, "methods", None) or ():
            saida.add((m, getattr(r, "path", "")))
    return saida


def test_acoes_listadas_existem():
    """Rota renomeada sem atualizar nomes.py = ação que some do Histórico."""
    existentes = _rotas()
    listadas = [*nomes.ACOES, *nomes.REVELACOES, *nomes.ROTAS_DE_ROBO]
    faltando = [
        k for k in listadas
        if k not in existentes and not k[1].startswith("/api/marketing/")  # módulo opcional
    ]
    assert faltando == []


@pytest.mark.asyncio
async def test_toda_tabela_de_negocio_tem_o_gatilho(db):
    sem = [
        r[0]
        for r in await db.execute(
            text(hsql.TABELAS_SEM_GATILHO),
            {"schema": "davinci_test", "gatilho": hsql.NOME_GATILHO},
        )
    ]
    # Só as tabelas dos models: outros testes criam tabelas avulsas no meio
    # da bateria (depois da instalação), e em produção o worker cobre essas.
    dos_models = {t.name for t in Base.metadata.sorted_tables}
    assert [t for t in hsql.a_cobrir(sem) if t in dos_models] == []
    com = {
        r[0]
        for r in await db.execute(
            text(
                "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'davinci_test' AND t.tgname = :g"
            ),
            {"g": hsql.NOME_GATILHO},
        )
    }
    for tabela in ("pricing_overrides", "companies", "store_info", "users", "historico_acesso"):
        assert tabela in com, tabela
    for tabela in ("historico_evento", "historico_alteracao", "background_jobs", "alerts"):
        assert tabela not in com, tabela
