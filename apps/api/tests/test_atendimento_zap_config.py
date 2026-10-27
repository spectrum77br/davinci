"""Zap (WhatsApp Cloud API, RF10): as chaves de §6.1 do desenho nascem DESLIGADAS.

docs/zap/desenho.md §6.1 e §11.6 ("Padrões das chaves"): um deploy nunca
liga nada do Zap. O webhook guarda o evento cru mesmo assim (quem decide
processar é o worker), e para sair mensagem precisam estar ligados ao mesmo
tempo o freio único, a chave do Zap, o botão do número e o modo do canal.
"""

from __future__ import annotations

from app.config import Settings

# (chave, padrão) — exatamente o bloco do §6.1.
PADROES = {
    "atendimento_zap_leitura_ativa": False,
    "atendimento_zap_envio_ativo": False,
    "atendimento_zap_destinos_teste": "",
    "atendimento_zap_max_dia_numero": 300,
    "atendimento_zap_max_contato_10min": 8,
    "atendimento_zap_midia_max_mb": 32,
    "meta_whatsapp_graph_version": "v26.0",
    "atendimento_zap_retencao_eventos_dias": 30,
    "atendimento_zap_retencao_midia_dias": 180,
    "atendimento_zap_marcar_lida": False,
    "atendimento_zap_modelos_ativo": False,
    "atendimento_zap_ia_ativa": False,
    # Onda 4 (27/10/2026): a tela "Números do WhatsApp" cadastra e fala com a Meta.
    "atendimento_zap_cadastro_ativo": False,
}


def test_chaves_do_zap_nascem_desligadas():
    campos = Settings.model_fields
    for chave, padrao in PADROES.items():
        assert chave in campos, chave
        assert campos[chave].default == padrao, chave
        assert type(campos[chave].default) is type(padrao), chave
    # Nenhuma chave do Zap esquecida fora da lista (e nenhuma nasce ligada).
    do_zap = {n for n in campos if n.startswith("atendimento_zap_")}
    assert do_zap == {n for n in PADROES if n.startswith("atendimento_zap_")}
    for chave in do_zap:
        assert campos[chave].default is not True, chave
    # O freio único continua desligado por padrão (o Zap não o liga).
    assert campos["atendimento_envio_ativo"].default is False
    assert campos["atendimento_leitura_ativa"].default is False


def test_versao_da_graph_do_zap_e_separada_da_do_instagram(monkeypatch):
    """Subir a versão do Instagram (`META_GRAPH_VERSION`) não mexe no Zap."""
    monkeypatch.setenv("META_GRAPH_VERSION", "v99.0")
    monkeypatch.delenv("META_WHATSAPP_GRAPH_VERSION", raising=False)
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x:y@localhost/z")
    assert s.meta_graph_version == "v99.0"
    assert s.meta_whatsapp_graph_version == "v26.0"
    monkeypatch.setenv("META_WHATSAPP_GRAPH_VERSION", "v27.0")
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x:y@localhost/z")
    assert (s.meta_graph_version, s.meta_whatsapp_graph_version) == ("v99.0", "v27.0")
