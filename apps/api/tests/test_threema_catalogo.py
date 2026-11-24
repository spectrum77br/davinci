"""Catálogo dos envios do Threema (tarefa 16, C1a): só dado, sem envio.

Congela as chaves (o registro e o cadastro guardam a chave como texto) e o
remetente literal de cada envio do servidor — quem muda um dos dois tem de
mudar aqui de propósito.
"""

from __future__ import annotations

import re

import pytest

from app.config import Settings
from app.routers import informar as informar_router
from app.services import ouvidoria, threema
from app.services import threema_catalogo as cat
from app.services.threema_catalogo import CATALOGO, ENVIOS, K

# Foto congelada das 60 chaves. Renomear uma chave exige migração com UPDATE
# no registro (e no cadastro, quando existir) e uma entrada em RENOMEADAS.
CHAVES = {
    "logistica.mensagem_regra",
    "logistica.informar",
    "logistica.amazon_informar",
    "logistica.amazon_prazos",
    "logistica.correios_grave",
    "logistica.vigia_automacao",
    "logistica.loja_muda",
    "devolucoes.pacote_volta_grave",
    "devolucoes.prazo_contestacao",
    "devolucoes.prazo_resposta_loja",
    "devolucoes.informar",
    "devolucoes.codigos_tuta_servidor",
    "chamados.tiktok_so_reembolso",
    "chamados.aberturas_presas",
    "chamados.ia_parada",
    "chamados.encaminhar_juridico",
    "controle_estoque.informar",
    "controle_estoque.nf_sem_estoque_movido",
    "controle_estoque.nf_negativo_fora_do_lote",
    "controle_estoque.nf_kit_sem_composicao",
    "controle_estoque.nf_sem_leitura_bling",
    "controle_estoque.nf_recusa_aguardando_cancelamento",
    "controle_estoque.prioridade_kit_conferencia",
    "produtos.vigia_soma_familia",
    "margem.pedido_segurado",
    "margem.reavaliacao",
    "margem.margem_alta",
    "margem.informar",
    "ouvidoria.vigia_importacao",
    "ouvidoria.vigia_credenciais",
    "ouvidoria.vigia_ingest_bling",
    "ouvidoria.vigia_correios",
    "ouvidoria.vigia_marketing_comandos",
    "ouvidoria.vigia_margem",
    "ouvidoria.vigia_robo_melhorenvio",
    "ouvidoria.vigia_robo_leitura",
    "ouvidoria.vigia_etiquetas",
    "ouvidoria.vigia_nota_cheia",
    "ouvidoria.vigia_conciliacao",
    "ouvidoria.robo_juridico",
    "ouvidoria.vigia_flex",
    "ouvidoria.chamado_frete_ml",
    "denuncia.relatorio_diario",
    "denuncia.robo_precisa_alguem",
    "marketing.conferencia_shopee",
    "marketing.conferencia_ml",
    "marketing.conferencia_amazon",
    "pedidos.correcao_dia_envio",
    "controle_estoque.hermes_estoque_negativo",
    "chamados.frete_ml_relatorio_noite",
    "chamados.frete_ml_anuncio_nao_encontrado",
    "chamados.frete_ml_resposta_recebida",
    "devolucoes.codigos_ml_tuta",
    "devolucoes.codigos_ml_tuta_falhou",
    "chamados.agente_resumo_diario",
    "chamados.agente_ml_avisos",
    "chamados.formulario_ml_sem_protocolo",
    "notas_fiscais.lote100_resumo",
    "notas_fiscais.lote100_parou",
    "chamados.robo_tuta_legado",
}

# O `contexto` literal que cada envio do servidor passa ao ThreemaClient hoje
# (o remetente não é o painel: o pacote de devolução sai por `logistica`, o TikTok
# só reembolso por `devolucoes`, a Denúncia e a Conferência por `chamados`).
REMETENTES = {
    K.LOGISTICA_MENSAGEM_REGRA: "logistica",
    K.LOGISTICA_INFORMAR: "logistica",
    K.LOGISTICA_AMAZON_INFORMAR: "logistica_amazon",
    K.LOGISTICA_AMAZON_PRAZOS: "logistica",
    K.LOGISTICA_CORREIOS_GRAVE: "logistica",
    K.LOGISTICA_VIGIA_AUTOMACAO: "logistica",
    K.LOGISTICA_LOJA_MUDA: "logistica",
    K.DEVOLUCOES_PACOTE_VOLTA_GRAVE: "logistica",
    K.DEVOLUCOES_PRAZO_CONTESTACAO: "devolucoes",
    K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA: "devolucoes",
    K.DEVOLUCOES_INFORMAR: "devolucoes",
    K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR: "devolucoes",
    K.CHAMADOS_TIKTOK_SO_REEMBOLSO: "devolucoes",
    K.CHAMADOS_ABERTURAS_PRESAS: None,  # dinâmico: devolucoes ou logistica
    K.CHAMADOS_IA_PARADA: "chamados",
    K.CHAMADOS_ENCAMINHAR_JURIDICO: "juridico",
    K.CONTROLE_ESTOQUE_INFORMAR: "controle_estoque",
    K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO: "estoque",
    K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE: "estoque",
    K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO: "estoque",
    K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING: "estoque",
    K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO: "estoque",
    K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA: "estoque",
    K.PRODUTOS_VIGIA_SOMA_FAMILIA: "estoque",
    K.MARGEM_PEDIDO_SEGURADO: "margem",
    K.MARGEM_REAVALIACAO: "margem",
    K.MARGEM_MARGEM_ALTA: "margem",
    K.MARGEM_INFORMAR: "margem",
    K.OUVIDORIA_VIGIA_IMPORTACAO: "importacao",
    K.OUVIDORIA_VIGIA_CREDENCIAIS: "chamados",
    K.OUVIDORIA_VIGIA_INGEST_BLING: "importacao",
    K.OUVIDORIA_VIGIA_CORREIOS: "logistica",
    K.OUVIDORIA_VIGIA_MARKETING_COMANDOS: "chamados",
    K.OUVIDORIA_VIGIA_MARGEM: "margem",
    K.OUVIDORIA_VIGIA_ROBO_MELHORENVIO: "logistica",
    K.OUVIDORIA_VIGIA_ROBO_LEITURA: "devolucoes",
    K.OUVIDORIA_VIGIA_ETIQUETAS: "estoque",
    K.OUVIDORIA_VIGIA_NOTA_CHEIA: "estoque",
    K.OUVIDORIA_VIGIA_CONCILIACAO: "margem",
    K.OUVIDORIA_ROBO_JURIDICO: "chamados",
    K.OUVIDORIA_VIGIA_FLEX: "logistica",
    K.OUVIDORIA_CHAMADO_FRETE_ML: "chamados",
    K.DENUNCIA_RELATORIO_DIARIO: "chamados",
    K.DENUNCIA_ROBO_PRECISA_ALGUEM: "chamados",
    K.MARKETING_CONFERENCIA_SHOPEE: "chamados",
    K.MARKETING_CONFERENCIA_ML: "chamados",
    K.MARKETING_CONFERENCIA_AMAZON: "chamados",
}


def _gerenciados_servidor():
    return [d for d in ENVIOS if d.onde_roda == "servidor" and d.gerenciado]


def test_constantes_tem_formato_e_nao_repetem():
    const = cat.constantes()
    assert len(const) == 60
    assert len(set(const.values())) == 60
    for nome, chave in const.items():
        assert re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", chave), chave
        assert len(chave) <= 60, chave
        # A constante é o nome da chave em maiúsculas (sem surpresa na busca).
        assert nome == chave.upper().replace(".", "_"), (nome, chave)


def test_foto_congelada_das_chaves():
    assert set(CATALOGO) == CHAVES
    assert len(ENVIOS) == len(CATALOGO) == 60
    # Toda constante aponta para um envio e todo envio tem constante.
    assert set(cat.constantes().values()) == set(CATALOGO)
    # Renomeada aponta para chave que existe e não é ela mesma.
    for antiga, nova in cat.RENOMEADAS.items():
        assert antiga not in CATALOGO and nova in CATALOGO


def test_constante_errada_quebra_no_import_nao_no_aviso():
    with pytest.raises(AttributeError):
        _ = K.LOGISTICA_VIGIA_AUTOMACAOO  # type: ignore[attr-defined]


def test_contagem_por_onde_roda():
    def n(onde, gerenciado):
        return sum(1 for d in ENVIOS if d.onde_roda == onde and d.gerenciado is gerenciado)

    assert n("servidor", True) == 47
    assert n("servidor", False) == 1  # a fila sem consumidor
    assert n("externo", False) == 1  # o job do Hermes
    assert n("mac", True) == 9
    assert n("mac", False) == 2  # 56* (agente pausado) e 72 (robô do Tuta antigo)
    assert n("externo", True) == 0


def test_todo_robo_da_ouvidoria_tem_o_seu_envio():
    for chave, robo in ouvidoria.ROBOS.items():
        if chave == "denuncia_relatorio_threema":
            # Envio próprio (item 43), não pelo aviso das ocorrências.
            assert cat.chave_ouvidoria(chave) is None
            continue
        envio = CATALOGO[f"ouvidoria.{chave}"]
        assert cat.chave_ouvidoria(chave) == envio.chave
        assert envio.remetente == robo.contexto
        assert envio.legado.robo == chave
        assert envio.nome == robo.nome
        # "Evento" = sai quando há pendência; a cadência é quando o robô confere
        assert envio.frequencia_texto == f"quando há pendência; confere {robo.cadencia_texto}"
        assert [x.alvo for x in envio.depende_de] == [chave]


def test_remetente_literal_de_cada_envio_do_servidor():
    assert {d.chave: d.remetente for d in _gerenciados_servidor()} == REMETENTES
    # Todo remetente de hoje é um contexto que o ThreemaClient aceita.
    aceitos = threema._CONTEXTOS | set(threema._CONTEXTOS_ALIASES)
    for chave, rem in REMETENTES.items():
        if rem is not None:
            assert rem in aceitos, chave


def test_envio_do_servidor_gerenciado_tem_fatia_e_ao_desligar():
    for d in _gerenciados_servidor():
        assert d.fatia in {"F1", "F2", "F3", "F4"}, d.chave
        if d.interruptor == "fixo":
            assert d.ao_desligar == "—", d.chave
        else:
            assert d.ao_desligar in {"descarta", "acumula", "recusa"}, d.chave
    # Só o item 1 não desliga: o interruptor de cada regra é a própria Mensagem.
    fixos = [d.chave for d in _gerenciados_servidor() if d.interruptor == "fixo"]
    assert fixos == [K.LOGISTICA_MENSAGEM_REGRA]
    assert CATALOGO[K.LOGISTICA_MENSAGEM_REGRA].escopo_tipo == "regra_status"
    assert [d.chave for d in ENVIOS if d.escopo_tipo] == [K.LOGISTICA_MENSAGEM_REGRA]


def test_informativos_e_externos_nao_sao_gerenciados():
    informativos = {d.chave for d in ENVIOS if not d.gerenciado}
    assert informativos == {
        K.PEDIDOS_CORRECAO_DIA_ENVIO,
        K.CONTROLE_ESTOQUE_HERMES_ESTOQUE_NEGATIVO,
        K.CHAMADOS_AGENTE_ML_AVISOS,
        K.CHAMADOS_ROBO_TUTA_LEGADO,
    }
    for chave in informativos:
        d = CATALOGO[chave]
        assert d.fatia is None and d.interruptor == "fixo" and d.ao_desligar == "—"


def test_mac_tem_reserva_do_env_do_mac_e_nenhum_remetente_do_servidor():
    for d in ENVIOS:
        if d.onde_roda != "mac":
            assert d.legado.env_mac is None, d.chave
            continue
        assert d.remetente is None, d.chave
        assert d.legado.env_mac in {"ML_FRETE_THREEMA_TO", "DEVOL_CODIGOS_THREEMA_TO"}
        if d.gerenciado:
            assert d.fatia == "mac", d.chave
    assert CATALOGO[K.DEVOLUCOES_CODIGOS_ML_TUTA].legado.env_mac == "DEVOL_CODIGOS_THREEMA_TO"


# Os envios que já leem o Cadastro, fatia a fatia (cada commit de fatia
# acrescenta os seus aqui, na ordem do catálogo, junto com o `migrado=True` e
# os testes por ponto).
MIGRADOS = [
    # F2/C9a: Informar da Logística e da aba Amazon, prazos da Amazon (2, 3 e 4)
    K.LOGISTICA_INFORMAR,
    K.LOGISTICA_AMAZON_INFORMAR,
    K.LOGISTICA_AMAZON_PRAZOS,
    # F1/C8a: Logística e Devoluções do .env (itens 5, 6, 7, 8, 9 e 12)
    K.LOGISTICA_CORREIOS_GRAVE,
    K.LOGISTICA_VIGIA_AUTOMACAO,
    K.LOGISTICA_LOJA_MUDA,
    K.DEVOLUCOES_PACOTE_VOLTA_GRAVE,
    K.DEVOLUCOES_PRAZO_CONTESTACAO,
    # F2/C9c: prazo de resposta da loja e Informar das Devoluções (10 e 11)
    K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA,
    K.DEVOLUCOES_INFORMAR,
    # F1/C8a (cont.)
    K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR,
    # F1/C8c: Chamados (itens 13 e 14)
    K.CHAMADOS_TIKTOK_SO_REEMBOLSO,
    K.CHAMADOS_ABERTURAS_PRESAS,
    # F2/C9d: IA de Chamado parada e Encaminhar ao jurídico (15 e 16)
    K.CHAMADOS_IA_PARADA,
    K.CHAMADOS_ENCAMINHAR_JURIDICO,
    # F2/C9b: Informar do Controle de Estoque (item 17)
    K.CONTROLE_ESTOQUE_INFORMAR,
    # F1/C8b: Estoque e NF (itens 18 a 23)
    K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO,
    K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE,
    K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO,
    K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING,
    K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO,
    K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA,
    # F1/C8c: Produtos (item 24)
    K.PRODUTOS_VIGIA_SOMA_FAMILIA,
    # F2/C9d: relatório da Denúncia e robô de Denúncia precisa de alguém (43
    # e 44, junto com o 15 por causa da reserva `chamados_ia`)
    K.DENUNCIA_RELATORIO_DIARIO,
    K.DENUNCIA_ROBO_PRECISA_ALGUEM,
    # F2/C9e: Conferência Shopee, Mercado Livre e Amazon (45 a 47)
    K.MARKETING_CONFERENCIA_SHOPEE,
    K.MARKETING_CONFERENCIA_ML,
    K.MARKETING_CONFERENCIA_AMAZON,
]


def test_so_os_envios_das_fatias_publicadas_estao_migrados():
    assert [d.chave for d in ENVIOS if d.migrado] == MIGRADOS
    for chave in MIGRADOS:
        d = CATALOGO[chave]
        assert d.onde_roda == "servidor" and d.gerenciado, chave


def test_ao_desligar_dos_migrados_e_o_comportamento_real_do_ponto():
    """O que acontece com os avisos enquanto o envio está desligado é o que
    o ponto JÁ faz (o Cadastro não muda o carimbo de ninguém): cada um tem o
    seu teste pelo caminho real em `test_threema_pontos_logistica_devolucoes`
    (C8a), `test_threema_pontos_estoque_nf` (C8b),
    `test_threema_pontos_chamados_produtos` (C8c),
    `test_threema_pontos_informar` (C9a, C9b e C9c),
    `test_threema_pontos_chamados_denuncia` (C9d) e
    `test_threema_pontos_conferencia` (C9e)."""
    assert {c: CATALOGO[c].ao_desligar for c in MIGRADOS} == {
        # o "Enviar agora" responde 422 enquanto estiver desligado
        K.LOGISTICA_INFORMAR: "recusa",
        K.LOGISTICA_AMAZON_INFORMAR: "recusa",
        K.CONTROLE_ESTOQUE_INFORMAR: "recusa",
        K.DEVOLUCOES_INFORMAR: "recusa",
        # o botão Encaminhar ao jurídico responde 422 sem mexer no chamado
        K.CHAMADOS_ENCAMINHAR_JURIDICO: "recusa",
        # os carimbos `aviso_*_at` só gravam o que saiu
        K.LOGISTICA_AMAZON_PRAZOS: "acumula",
        # `aviso_prazo_acao_at/_para` só gravam o que saiu
        K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA: "acumula",
        # o carimbo da ocorrência grave é gravado antes do aviso
        K.LOGISTICA_CORREIOS_GRAVE: "descarta",
        # sem carimbo: repete a cada rodada enquanto a automação estiver parada
        K.LOGISTICA_VIGIA_AUTOMACAO: "descarta",
        # só no 30º minuto mudo
        K.LOGISTICA_LOJA_MUDA: "descarta",
        K.DEVOLUCOES_PACOTE_VOLTA_GRAVE: "descarta",
        # `aviso_prazo_at` só é carimbado quando o aviso sai
        K.DEVOLUCOES_PRAZO_CONTESTACAO: "acumula",
        # o comando do dia já está feito quando o resultado chega
        K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR: "descarta",
        # o pedido já foi movido para Aguardando Cancelamento
        K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO: "descarta",
        # 19 a 22: desligado sai antes das marcas de "já avisei"; nada fica
        # guardado (o que continuar valendo é apontado de novo na passada)
        K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE: "descarta",
        K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO: "descarta",
        K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING: "descarta",
        # o pedido já saiu da fila com 'erro'
        K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO: "descarta",
        # `avisado_at` só é carimbado quando o aviso sai
        K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA: "acumula",
        # `aviso_prazo_acao_at` é carimbado mesmo sem envio (C0 à parte)
        K.CHAMADOS_TIKTOK_SO_REEMBOLSO: "descarta",
        # o carimbo da rota vai para o histórico do chamado antes do aviso
        K.CHAMADOS_ABERTURAS_PRESAS: "descarta",
        # sem carimbo: repete a cada rodada enquanto o problema existir
        K.PRODUTOS_VIGIA_SOMA_FAMILIA: "descarta",
        # evento sem retentativa: o histórico do chamado diz que não saiu
        K.CHAMADOS_IA_PARADA: "descarta",
        # só o relatório de ONTEM sai: os dias desligados se perdem
        K.DENUNCIA_RELATORIO_DIARIO: "descarta",
        # as marcas do Redis são gravadas mesmo sem envio
        K.DENUNCIA_ROBO_PRECISA_ALGUEM: "descarta",
        # a rodada não é carimbada, mas o varredor só reenvia até 6 h depois
        # de ela fechar (JANELA_REENVIO): desligado por mais que isso, perde
        K.MARKETING_CONFERENCIA_SHOPEE: "descarta",
        K.MARKETING_CONFERENCIA_ML: "descarta",
        K.MARKETING_CONFERENCIA_AMAZON: "descarta",
    }


def test_fontes_do_legado_existem():
    campos = set(Settings.model_fields)
    contextos = set(informar_router._CONTEXTOS)
    for d in ENVIOS:
        lg = d.legado
        for env in lg.envs:
            assert env in campos, (d.chave, env)
        for ctx in lg.informar:
            assert ctx in contextos, (d.chave, ctx)
        if lg.interruptor_env:
            assert lg.interruptor_env in campos, d.chave
        if lg.robo:
            assert lg.robo in ouvidoria.ROBOS, d.chave
        for dep in d.depende_de:
            if dep.tipo == "env":
                assert dep.alvo in campos, (d.chave, dep.alvo)
            elif dep.tipo == "robo_modo":
                assert dep.alvo in ouvidoria.ROBOS, (d.chave, dep.alvo)
            else:
                assert dep.alvo in threema._CONTEXTOS, (d.chave, dep.alvo)
        if d.gerenciado and d.onde_roda == "servidor":
            # Todo envio gerenciado do servidor sabe de onde vem a lista de hoje.
            assert lg.envs or lg.informar or lg.robo or lg.usuario_nome or lg.regra_status


def test_interruptores_que_gravam_na_fonte_de_hoje():
    relatorio = CATALOGO[K.DENUNCIA_RELATORIO_DIARIO]
    assert relatorio.interruptor == "robo_modo"
    assert relatorio.interruptor_alvo in ouvidoria.ROBOS
    alta = CATALOGO[K.MARGEM_MARGEM_ALTA]
    assert alta.interruptor == "robo_config"
    robo, _, campo = alta.interruptor_alvo.partition(".")
    assert robo == "vigia_margem" and campo == "avisar_margem_alta"
    for chave in (
        K.MARKETING_CONFERENCIA_SHOPEE,
        K.MARKETING_CONFERENCIA_ML,
        K.MARKETING_CONFERENCIA_AMAZON,
    ):
        assert CATALOGO[chave].legado.interruptor_env.startswith("conferencia_")


def test_paineis_frequencia_e_leitura():
    for d in ENVIOS:
        assert d.painel in cat.PAINEIS, d.chave
        assert d.frequencia_tipo in {"evento", "horario", "manual", "misto"}, d.chave
        assert d.frequencia_texto and d.nome and d.descricao, d.chave
        assert d.leitura == "admin" or re.fullmatch(
            r"(perm:[a-z_]+:view|informar:[a-z_]+)", d.leitura
        ), d.chave
        if d.leitura.startswith("informar:"):
            assert d.leitura.split(":", 1)[1] in informar_router._CONTEXTOS, d.chave
