"""Catálogo dos envios do Threema (Cadastros › Envios Threema, tarefa 16).

O código DESCREVE cada envio que existe hoje; o banco (fases seguintes) vai
DECIDIR quem recebe, se está ligado e a observação. Aqui ficam, por envio:
painel e aba a que pertence, nome, descrição, frequência (evento, horário,
manual ou misto), onde roda (servidor, Mac do escritório ou sistema externo),
o remetente de hoje (o `contexto` literal passado ao `ThreemaClient`), de onde
vem hoje a lista de quem recebe (`Legado`), como se liga e desliga, do que ele
depende e o que acontece com os avisos enquanto estiver desligado.

São 60 envios (levantamento de 17–18/11/2026, `docs` da tarefa 16):

* 47 do servidor gerenciados (itens 1 a 47; os 14 robôs da Ouvidoria, itens 29
  a 42, saem do `ROBOS` de `services/ouvidoria.py`, menos o Relatório da
  Denúncia, que tem envio próprio, item 43);
* 1 do servidor só informativo (48, a fila sem consumidor) e 1 externo (49,
  o job do Hermes);
* 11 do Mac (9 gerenciados e 2 informativos: 56*, os 13 avisos do agente
  pausado, e 72, o robô do Tuta fora do cron).

As chaves são CONSTANTES (`K.LOGISTICA_VIGIA_AUTOMACAO`): um erro de digitação
vira erro no import (AttributeError), nunca um KeyError engolido num aviso.
`_validar()` roda no import e confere formato, tamanho e repetição. A chave
NUNCA muda depois de publicada: o registro de cada envio e o cadastro de
quem recebe guardam a chave como texto. Renomear exige migração com UPDATE e
uma entrada em `RENOMEADAS` (o teste congela a lista).

Este módulo não envia nem lê o banco: é só dado. Cada fatia (F1 a F4) liga
`migrado=True` nos envios cujo ponto passou a ler o Cadastro, no mesmo commit
que troca o ponto; os demais seguem a configuração de hoje.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.services.ouvidoria import ROBOS

FrequenciaTipo = Literal["evento", "horario", "manual", "misto"]
OndeRoda = Literal["servidor", "mac", "externo"]
Interruptor = Literal["proprio", "robo_modo", "robo_config", "fixo"]
AoDesligar = Literal["descarta", "acumula", "recusa", "—"]

FATIAS = ("F1", "F2", "F3", "F4", "mac")
PAINEIS = (
    "Logística",
    "Devoluções",
    "Chamados",
    "Controle de Estoque",
    "Produtos",
    "Margem",
    "Ouvidoria",
    "Denúncia",
    "Marketing",
    "Pedidos",
)

_FORMATO_CHAVE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_TAMANHO_CHAVE = 60

# O robô da Ouvidoria que tem envio próprio (item 43): não vira linha
# `ouvidoria.<chave>`.
_ROBO_COM_ENVIO_PROPRIO = "denuncia_relatorio_threema"


class K:
    """As chaves dos envios. Use sempre a constante, nunca o texto."""

    # Logística
    LOGISTICA_MENSAGEM_REGRA = "logistica.mensagem_regra"
    LOGISTICA_INFORMAR = "logistica.informar"
    LOGISTICA_AMAZON_INFORMAR = "logistica.amazon_informar"
    LOGISTICA_AMAZON_PRAZOS = "logistica.amazon_prazos"
    LOGISTICA_CORREIOS_GRAVE = "logistica.correios_grave"
    LOGISTICA_VIGIA_AUTOMACAO = "logistica.vigia_automacao"
    LOGISTICA_LOJA_MUDA = "logistica.loja_muda"
    # Devoluções
    DEVOLUCOES_PACOTE_VOLTA_GRAVE = "devolucoes.pacote_volta_grave"
    DEVOLUCOES_PRAZO_CONTESTACAO = "devolucoes.prazo_contestacao"
    DEVOLUCOES_PRAZO_RESPOSTA_LOJA = "devolucoes.prazo_resposta_loja"
    DEVOLUCOES_INFORMAR = "devolucoes.informar"
    DEVOLUCOES_CODIGOS_TUTA_SERVIDOR = "devolucoes.codigos_tuta_servidor"
    # Chamados
    CHAMADOS_TIKTOK_SO_REEMBOLSO = "chamados.tiktok_so_reembolso"
    CHAMADOS_ABERTURAS_PRESAS = "chamados.aberturas_presas"
    CHAMADOS_IA_PARADA = "chamados.ia_parada"
    CHAMADOS_ENCAMINHAR_JURIDICO = "chamados.encaminhar_juridico"
    # Controle de Estoque e Produtos
    CONTROLE_ESTOQUE_INFORMAR = "controle_estoque.informar"
    CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO = "controle_estoque.nf_sem_estoque_movido"
    CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE = "controle_estoque.nf_negativo_fora_do_lote"
    CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO = "controle_estoque.nf_kit_sem_composicao"
    CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING = "controle_estoque.nf_sem_leitura_bling"
    CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO = (
        "controle_estoque.nf_recusa_aguardando_cancelamento"
    )
    CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA = "controle_estoque.prioridade_kit_conferencia"
    PRODUTOS_VIGIA_SOMA_FAMILIA = "produtos.vigia_soma_familia"
    # Margem
    MARGEM_PEDIDO_SEGURADO = "margem.pedido_segurado"
    MARGEM_REAVALIACAO = "margem.reavaliacao"
    MARGEM_MARGEM_ALTA = "margem.margem_alta"
    MARGEM_INFORMAR = "margem.informar"
    # Ouvidoria (uma por robô de `ROBOS`, menos o Relatório da Denúncia)
    OUVIDORIA_VIGIA_IMPORTACAO = "ouvidoria.vigia_importacao"
    OUVIDORIA_VIGIA_CREDENCIAIS = "ouvidoria.vigia_credenciais"
    OUVIDORIA_VIGIA_INGEST_BLING = "ouvidoria.vigia_ingest_bling"
    OUVIDORIA_VIGIA_CORREIOS = "ouvidoria.vigia_correios"
    OUVIDORIA_VIGIA_MARKETING_COMANDOS = "ouvidoria.vigia_marketing_comandos"
    OUVIDORIA_VIGIA_MARGEM = "ouvidoria.vigia_margem"
    OUVIDORIA_VIGIA_ROBO_MELHORENVIO = "ouvidoria.vigia_robo_melhorenvio"
    OUVIDORIA_VIGIA_ROBO_LEITURA = "ouvidoria.vigia_robo_leitura"
    OUVIDORIA_VIGIA_ETIQUETAS = "ouvidoria.vigia_etiquetas"
    OUVIDORIA_VIGIA_NOTA_CHEIA = "ouvidoria.vigia_nota_cheia"
    OUVIDORIA_VIGIA_CONCILIACAO = "ouvidoria.vigia_conciliacao"
    OUVIDORIA_ROBO_JURIDICO = "ouvidoria.robo_juridico"
    OUVIDORIA_VIGIA_FLEX = "ouvidoria.vigia_flex"
    OUVIDORIA_CHAMADO_FRETE_ML = "ouvidoria.chamado_frete_ml"
    # Denúncia, Marketing, Pedidos e externo
    DENUNCIA_RELATORIO_DIARIO = "denuncia.relatorio_diario"
    DENUNCIA_ROBO_PRECISA_ALGUEM = "denuncia.robo_precisa_alguem"
    MARKETING_CONFERENCIA_SHOPEE = "marketing.conferencia_shopee"
    MARKETING_CONFERENCIA_ML = "marketing.conferencia_ml"
    MARKETING_CONFERENCIA_AMAZON = "marketing.conferencia_amazon"
    PEDIDOS_CORRECAO_DIA_ENVIO = "pedidos.correcao_dia_envio"
    CONTROLE_ESTOQUE_HERMES_ESTOQUE_NEGATIVO = "controle_estoque.hermes_estoque_negativo"
    # Mac do escritório
    CHAMADOS_FRETE_ML_RELATORIO_NOITE = "chamados.frete_ml_relatorio_noite"
    CHAMADOS_FRETE_ML_ANUNCIO_NAO_ENCONTRADO = "chamados.frete_ml_anuncio_nao_encontrado"
    CHAMADOS_FRETE_ML_RESPOSTA_RECEBIDA = "chamados.frete_ml_resposta_recebida"
    DEVOLUCOES_CODIGOS_ML_TUTA = "devolucoes.codigos_ml_tuta"
    DEVOLUCOES_CODIGOS_ML_TUTA_FALHOU = "devolucoes.codigos_ml_tuta_falhou"
    CHAMADOS_AGENTE_RESUMO_DIARIO = "chamados.agente_resumo_diario"
    CHAMADOS_AGENTE_ML_AVISOS = "chamados.agente_ml_avisos"
    CHAMADOS_FORMULARIO_ML_SEM_PROTOCOLO = "chamados.formulario_ml_sem_protocolo"
    NOTAS_FISCAIS_LOTE100_RESUMO = "notas_fiscais.lote100_resumo"
    NOTAS_FISCAIS_LOTE100_PAROU = "notas_fiscais.lote100_parou"
    CHAMADOS_ROBO_TUTA_LEGADO = "chamados.robo_tuta_legado"


# Chave antiga → chave nova. Só cresce: renomear exige migração com UPDATE no
# registro e no cadastro, e a chave antiga continua reconhecida por aqui.
RENOMEADAS: dict[str, str] = {}


@dataclass(frozen=True)
class Legado:
    """De onde vem hoje a lista de quem recebe (antes do Cadastro).

    Só declara a fonte; quem monta a lista é o núcleo (fase C5), com a mesma
    regra do ponto de envio de hoje."""

    # Campos de Settings (`getattr(settings, nome, "")`): o 1º não vazio vence.
    envs: tuple[str, ...] = ()
    # Linhas de `threema_informar_config`: a 1ª LINHA EXISTENTE vence; linha
    # vazia = ninguém (era a regra do `denuncia_robo_aviso.destinatarios`,
    # antes da F2/C9d).
    informar: tuple[str, ...] = ()
    # Robô da Ouvidoria: `ouvidoria.destinatarios(robo, chave)` (lista da tela,
    # depois o env do robô, depois o OUVIDORIA_THREEMA_RECIPIENTS).
    robo: str | None = None
    # Usuário do DaVinci achado pelo nome (item 12: "thatcher").
    usuario_nome: str | None = None
    # 👤 da regra de status da Logística; sem 👤 → THREEMA_RECIPIENTS.
    regra_status: bool = False
    # Nome do grupo que a semente cria com esta lista (fase C5).
    grupo: str | None = None
    # Liga/desliga de hoje no .env (itens 45–47: "conferencia_*_threema").
    interruptor_env: str | None = None
    # Mac: a variável do .env do robô que vale de reserva (nunca o padrão
    # fixo do `threema_send.py`).
    env_mac: str | None = None


@dataclass(frozen=True)
class Depende:
    """Algo fora do Cadastro que também precisa estar ligado para o aviso
    sair. Aparece na tela, sem edição."""

    tipo: Literal["robo_modo", "env", "canal"]
    # robo_modo: chave do robô da Ouvidoria (precisa estar `ligado`);
    # env: campo booleano de Settings; canal: contexto do Threema que não
    # pode estar `desativado` no THREEMA_CONTEXT_CHANNELS.
    alvo: str
    texto: str


@dataclass(frozen=True)
class EnvioDef:
    chave: str
    painel: str
    aba: str | None
    nome: str
    descricao: str
    frequencia_tipo: FrequenciaTipo
    frequencia_texto: str
    onde_roda: OndeRoda
    # O `contexto` literal passado ao ThreemaClient hoje. None = dinâmico
    # (item 14) ou fora do servidor (Mac, externo, informativo).
    remetente: str | None
    legado: Legado
    interruptor: Interruptor = "proprio"
    # 43: "denuncia_relatorio_threema"; 27: "vigia_margem.avisar_margem_alta".
    interruptor_alvo: str | None = None
    depende_de: tuple[Depende, ...] = ()
    ao_desligar: AoDesligar = "descarta"
    # Porta da leitura leve de quem recebe: "admin", "perm:<recurso>:view"
    # ou "informar:<contexto>" (mesma regra do botão Informar).
    leitura: str = "admin"
    # Só o item 1: lista por regra de status da Logística.
    escopo_tipo: str | None = None
    # F1..F4 (servidor), "mac", ou None (informativo/externo).
    fatia: str | None = None
    gerenciado: bool = True
    # Vira True no commit da fatia que passa o envio a ler o Cadastro.
    migrado: bool = False


def _dep_robo(chave: str) -> Depende:
    nome = ROBOS[chave].nome if chave in ROBOS else chave
    return Depende("robo_modo", chave, f"{nome} ligado em Ouvidoria › Robôs")


_DEP_NF_AUTO = Depende(
    "env", "nf_auto_enfileirar", "NF automática ligada no servidor (NF_AUTO_ENFILEIRAR)"
)
_DEP_FAMILIA = Depende(
    "env", "estoque_familia_ativo", "Soma por família ligada no servidor (ESTOQUE_FAMILIA_ATIVO)"
)
_DEP_CANAL_JURIDICO = Depende(
    "canal", "juridico", "Canal 'juridico' do Threema liberado no servidor"
)
_DEP_ROBO_MARGEM = _dep_robo("vigia_margem")

# Grupos que a semente vai criar (§2 do desenho).
G_AVISOS_GERAIS = "Avisos gerais"
G_ESTOQUE_NF = "Estoque e NF"
G_TIKTOK_CHAMADOS = "TikTok e chamados"
G_AMAZON = "Amazon"
G_IA_CHAMADO = "IA de Chamado"
G_CONFERENCIA = "Conferência"
G_ROBO_MARGEM = "Robô da Margem"


def _servidor() -> list[EnvioDef]:
    E = EnvioDef  # noqa: N806 — apelido curto da tabela
    L = Legado  # noqa: N806
    return [
        # ── Logística ──────────────────────────────────────────────────────
        E(
            K.LOGISTICA_MENSAGEM_REGRA, "Logística", "Status",
            "Mensagem da regra de status",
            "Texto 'Mensagem Threema' da regra da aba Status, com 'Pedido X | Loja Y' no "
            "topo. Sai pelo motor da Logística a cada 5 min, pelo botão Enviar da regra e "
            "pelo botão da linha do pedido. Quem recebe: a lista padrão ou uma lista própria "
            "por regra.",
            "misto", "motor a cada 5 min + botões da regra e da linha", "servidor",
            "logistica", L(regra_status=True, grupo=G_AVISOS_GERAIS),
            # Sem interruptor (o liga/desliga de cada regra é a própria
            # Mensagem Threema dela, F4/C11): o motor, o botão da regra e o da
            # linha mandam pelo Cadastro, com a lista da regra (escopo) ou a
            # padrão.
            interruptor="fixo", ao_desligar="—", escopo_tipo="regra_status", fatia="F4",
            leitura="perm:logistica:view", migrado=True,
        ),
        E(
            K.LOGISTICA_INFORMAR, "Logística", None, "Informar da Logística",
            "Botão Informar: a lista dos pedidos acompanhados no painel (pedido, conta, "
            "status da plataforma e do Bling).",
            "manual", "botão Informar › Enviar agora", "servidor", "logistica",
            L(informar=("logistica",)), ao_desligar="recusa", fatia="F2",
            leitura="informar:logistica", migrado=True,
        ),
        E(
            K.LOGISTICA_AMAZON_INFORMAR, "Logística", "Amazon", "Informar da aba Amazon",
            "Botão Informar da aba Amazon: os pedidos de envio próprio em trânsito, com a "
            "previsão dos Correios e a data máxima da Amazon.",
            "manual", "botão Informar › Enviar agora", "servidor", "logistica_amazon",
            L(informar=("logistica_amazon",), grupo=G_AMAZON), ao_desligar="recusa",
            fatia="F2", leitura="informar:logistica_amazon", migrado=True,
        ),
        E(
            K.LOGISTICA_AMAZON_PRAZOS, "Logística", "Amazon", "Prazos da Amazon",
            "Avisos do robô da aba Amazon: previsão de entrega vencida, 3 dias antes do "
            "prazo e prazo vencido. Uma mensagem por aviso.",
            "horario", "08:06 e 16:06", "servidor", "logistica",
            # Desligado, nada é carimbado: ao religar sai uma mensagem por
            # aviso pendente (os carimbos `aviso_*_at` só gravam o que saiu).
            L(informar=("logistica_amazon_auto",), grupo=G_AMAZON), ao_desligar="acumula",
            fatia="F2", leitura="informar:logistica_amazon_auto", migrado=True,
        ),
        E(
            K.LOGISTICA_CORREIOS_GRAVE, "Logística", None, "Ocorrência grave dos Correios",
            "Primeira leitura de ocorrência grave no rastreio (apreensão, extravio, roubo, "
            "avaria). Chega pelo rastreio das :05/:20/:35/:50, pelo webhook do 17track e pelo "
            "botão ⟳ da Localização. Repete o aviso do robô 'Vigia Ocorrências Correio' da "
            "Ouvidoria (mesma ocorrência; quem está nos dois recebe em dobro): decida qual "
            "manter.",
            "evento", "na leitura do rastreio (:05/:20/:35/:50, webhook, botão ⟳)", "servidor",
            "logistica", L(envs=("threema_recipients",), grupo=G_AVISOS_GERAIS), fatia="F1",
            migrado=True,
        ),
        E(
            K.LOGISTICA_VIGIA_AUTOMACAO, "Logística", None, "Automação parada",
            "Vigia da Logística: alguma automação parou de rodar. Repete a cada rodada "
            "enquanto estiver parada (sem dedupe no Threema).",
            "evento", "conferido às :13 e :43", "servidor", "logistica",
            L(envs=("nf_sem_estoque_threema_recipients",), grupo=G_ESTOQUE_NF), fatia="F1",
            migrado=True,
        ),
        E(
            K.LOGISTICA_LOJA_MUDA, "Logística", None, "Loja sem responder sobre o envio",
            "A loja do marketplace parou de responder sobre o envio dos pedidos (30 "
            "conferências seguidas sem resposta). Aviso técnico.",
            "evento", "no 30º minuto seguido sem resposta", "servidor", "logistica",
            L(envs=("shipment_muda_threema_recipients", "threema_recipients")), fatia="F1",
            migrado=True,
        ),
        # ── Devoluções ─────────────────────────────────────────────────────
        E(
            K.DEVOLUCOES_PACOTE_VOLTA_GRAVE, "Devoluções", None,
            "Pacote de devolução com ocorrência grave",
            "O pacote que volta do comprador teve ocorrência grave no rastreio. Sai pelo "
            "mesmo remetente dos Correios da Logística.",
            "evento", "no rastreio das devoluções (:10/:40)", "servidor", "logistica",
            L(envs=("threema_recipients",), grupo=G_AVISOS_GERAIS), fatia="F1", migrado=True,
        ),
        E(
            K.DEVOLUCOES_PRAZO_CONTESTACAO, "Devoluções", None, "Prazo de contestação",
            "Devolução com menos de 24 h para contestar na plataforma.",
            "evento", "conferido às :20 e :50", "servidor", "devolucoes",
            L(envs=("threema_recipients",), grupo=G_AVISOS_GERAIS), ao_desligar="acumula",
            fatia="F1", migrado=True,
        ),
        E(
            K.DEVOLUCOES_PRAZO_RESPOSTA_LOJA, "Devoluções", None, "Prazo de resposta da loja",
            "Prazo da loja para responder a devolução: avisos de 24 h e de 4 h.",
            "evento", "depois do rastreio das devoluções (:10/:40)", "servidor", "devolucoes",
            # Desligado, nada é carimbado: ao religar sai um aviso por caso
            # ainda na janela (`aviso_prazo_acao_*` só grava o que saiu).
            L(informar=("devolucoes_auto",)), ao_desligar="acumula", fatia="F2",
            leitura="informar:devolucoes_auto", migrado=True,
        ),
        E(
            K.DEVOLUCOES_INFORMAR, "Devoluções", None, "Informar de Devoluções",
            "Botão Informar: os pedidos em Aguardando Devolução, com o tempo parado e a "
            "última localização.",
            "manual", "botão Informar › Enviar agora", "servidor", "devolucoes",
            L(informar=("devolucoes",)), ao_desligar="recusa", fatia="F2",
            leitura="informar:devolucoes", migrado=True,
        ),
        E(
            K.DEVOLUCOES_CODIGOS_TUTA_SERVIDOR, "Devoluções", None,
            "Códigos de devolução do Tuta (servidor)",
            "Códigos de autorização de devolução do ML lidos no Tuta pelo executor do "
            "servidor. Parado: nenhum executor puxa o comando (quem entrega é o Mac, "
            "item 53).",
            "horario", "07:00 (comando para o executor)", "servidor", "devolucoes",
            # Desligado, o resumo do dia se perde: o comando já foi marcado como
            # feito quando o resultado chega (logistica_robo.registrar_resultado)
            # e o do dia seguinte só lê as últimas 24 h.
            L(usuario_nome="thatcher"), ao_desligar="descarta", fatia="F1", migrado=True,
        ),
        # ── Chamados ───────────────────────────────────────────────────────
        E(
            K.CHAMADOS_TIKTOK_SO_REEMBOLSO, "Chamados", None, "TikTok só reembolso",
            "Comprador pediu só reembolso na TikTok: avisos de 12 h e de 3 h antes do prazo "
            "de contestar.",
            "evento", "conferido às :10 e :40", "servidor", "devolucoes",
            L(
                envs=("tiktok_reembolso_threema_recipients", "nf_sem_estoque_threema_recipients"),
                grupo=G_TIKTOK_CHAMADOS,
            ),
            # Desligado, o aviso daquela faixa se perde: `run_vigia` carimba
            # `aviso_prazo_acao_at` mesmo sem envio (a C0 é tarefa à parte).
            fatia="F1", migrado=True,
        ),
        E(
            K.CHAMADOS_ABERTURAS_PRESAS, "Chamados", None, "Abertura de chamado presa",
            "Chamado cuja abertura ficou presa e precisa de gente (falta foto, número do "
            "pedido, ou o motivo não abre chamado). O remetente segue a origem do chamado.",
            "evento", "varredura das :25", "servidor", None,
            L(
                envs=("tiktok_reembolso_threema_recipients", "nf_sem_estoque_threema_recipients"),
                grupo=G_TIKTOK_CHAMADOS,
            ),
            # O carimbo da rota vai para o histórico do chamado antes do aviso.
            fatia="F1", migrado=True,
        ),
        E(
            K.CHAMADOS_IA_PARADA, "Chamados", "IA de Chamado", "IA de Chamado parada",
            "O robô da IA de Chamado parou em captcha ou em tela de login da loja.",
            "evento", "quando o robô devolve o resultado", "servidor", "chamados",
            # Desligado, o aviso daquela parada se perde: sem retentativa (o
            # resultado vai para o histórico do chamado).
            L(informar=("chamados_ia",), grupo=G_IA_CHAMADO), fatia="F2",
            leitura="informar:chamados_ia", migrado=True,
        ),
        E(
            K.CHAMADOS_ENCAMINHAR_JURIDICO, "Chamados", None, "Encaminhar ao jurídico",
            "Botão Encaminhar ao jurídico do chamado: manda o dossiê para quem cuida do "
            "jurídico.",
            "manual", "botão Encaminhar ao jurídico", "servidor", "juridico",
            # Desligado, o botão responde 422 (`envio_desligado`) sem mexer no
            # chamado.
            L(informar=("juridico",)), depende_de=(_DEP_CANAL_JURIDICO,),
            ao_desligar="recusa", fatia="F2", leitura="informar:juridico", migrado=True,
        ),
        # ── Controle de Estoque e Produtos ─────────────────────────────────
        E(
            K.CONTROLE_ESTOQUE_INFORMAR, "Controle de Estoque", None,
            "Informar do Controle de Estoque",
            "Botão Informar: os pedidos movidos para Aguardando Cancelamento por falta de "
            "estoque que continuam lá no Bling.",
            "manual", "botão Informar › Enviar agora", "servidor", "controle_estoque",
            L(informar=("controle_estoque",)), ao_desligar="recusa", fatia="F2",
            leitura="informar:controle_estoque", migrado=True,
        ),
        E(
            K.CONTROLE_ESTOQUE_NF_SEM_ESTOQUE_MOVIDO, "Controle de Estoque", None,
            "Pedido movido para Aguardando Cancelamento",
            "A NF automática moveu pedidos para Aguardando Cancelamento por estoque "
            "negativo.",
            "evento", "na passada da NF automática (2 min)", "servidor", "estoque",
            L(envs=("nf_sem_estoque_threema_recipients",), grupo=G_ESTOQUE_NF),
            depende_de=(_DEP_NF_AUTO,), fatia="F1", migrado=True,
        ),
        E(
            K.CONTROLE_ESTOQUE_NF_NEGATIVO_FORA_DO_LOTE, "Controle de Estoque", None,
            "Negativo fora da passada",
            "O FIFO aponta como negativo um pedido que não está na passada (ex.: ML e "
            "Amazon). Um aviso por pedido, SKU e dia.",
            "evento", "na passada da NF automática (2 min)", "servidor", "estoque",
            L(envs=("nf_sem_estoque_threema_recipients",), grupo=G_ESTOQUE_NF),
            depende_de=(_DEP_NF_AUTO,), fatia="F1", migrado=True,
        ),
        E(
            K.CONTROLE_ESTOQUE_NF_KIT_SEM_COMPOSICAO, "Controle de Estoque", None,
            "Kit sem composição",
            "Kit sem a composição cadastrada no Bling: a NF não sai. Um aviso por kit por "
            "dia, pela passada ou pelo botão Enfileirar. Pela passada, só com a NF automática "
            "ligada no servidor; pelo botão Enfileirar, sempre.",
            "misto", "passada da NF automática e botão Enfileirar", "servidor", "estoque",
            L(envs=("nf_sem_estoque_threema_recipients",), grupo=G_ESTOQUE_NF), fatia="F1",
            migrado=True,
        ),
        E(
            K.CONTROLE_ESTOQUE_NF_SEM_LEITURA_BLING, "Controle de Estoque", None,
            "Bling sem responder o estoque",
            "O Bling não respondeu a leitura de estoque da NF automática. No máximo um "
            "aviso por hora.",
            "evento", "na passada da NF automática (2 min)", "servidor", "estoque",
            L(envs=("nf_sem_estoque_threema_recipients",), grupo=G_ESTOQUE_NF),
            depende_de=(_DEP_NF_AUTO,), fatia="F1", migrado=True,
        ),
        E(
            K.CONTROLE_ESTOQUE_NF_RECUSA_AGUARDANDO_CANCELAMENTO, "Controle de Estoque", None,
            "Bling recusou Aguardando Cancelamento",
            "O Bling recusou mover o pedido para Aguardando Cancelamento (83955). Um aviso "
            "por pedido por dia.",
            "evento", "na passada da NF automática (2 min)", "servidor", "estoque",
            L(envs=("nf_sem_estoque_threema_recipients",), grupo=G_ESTOQUE_NF),
            depende_de=(_DEP_NF_AUTO,), fatia="F1", migrado=True,
        ),
        E(
            K.CONTROLE_ESTOQUE_PRIORIDADE_KIT_CONFERENCIA, "Controle de Estoque", None,
            "Kit precisa de conferência",
            "Movimento de prioridade de estoque de kit que falhou e precisa de conferência.",
            "evento", "conferido às :16 e :46", "servidor", "estoque",
            L(envs=("nf_sem_estoque_threema_recipients",), grupo=G_ESTOQUE_NF),
            ao_desligar="acumula", fatia="F1", migrado=True,
        ),
        E(
            K.PRODUTOS_VIGIA_SOMA_FAMILIA, "Produtos", None, "Soma por família atrasada",
            "Anúncio com o número antigo da soma por família por mais tempo que a "
            "tolerância. Repete nas duas rodadas enquanto o problema existir.",
            "horario", "09:20 e 16:20", "servidor", "estoque",
            L(envs=("vigia_estoque_familia_threema_recipients",)),
            depende_de=(_DEP_FAMILIA,), fatia="F1", migrado=True,
        ),
        # ── Margem ─────────────────────────────────────────────────────────
        # F3/C10b: os avisos na hora do Robô da Margem (25 a 27) e o Informar
        # (28) leem o Cadastro. O modo do robô continua valendo antes (o
        # "depende de": silencioso age e cala); a caixinha "avisar margem
        # alta" é o liga/desliga do 27, gravado direto no robô. Quem vê a
        # Margem pelo botão Informar (admin e o gerente) vê quem recebe.
        E(
            K.MARGEM_PEDIDO_SEGURADO, "Margem", None, "Pedido segurado ou reprovado",
            "O Robô da Margem segurou ou reprovou um pedido, com o link de aprovar pelo "
            "celular. Uma mensagem por pedido.",
            "evento", "na rodada do robô (:15/:45)", "servidor", "margem",
            L(robo="vigia_margem", grupo=G_ROBO_MARGEM), depende_de=(_DEP_ROBO_MARGEM,),
            fatia="F3", leitura="informar:margem_auto", migrado=True,
        ),
        E(
            K.MARGEM_REAVALIACAO, "Margem", None, "Reavaliação de reprovados",
            "Pedido reprovado que a revisão liberou ou devolveu para análise.",
            "evento", "na revisão das :35", "servidor", "margem",
            L(robo="vigia_margem", grupo=G_ROBO_MARGEM), depende_de=(_DEP_ROBO_MARGEM,),
            fatia="F3", leitura="informar:margem_auto", migrado=True,
        ),
        E(
            K.MARGEM_MARGEM_ALTA, "Margem", None, "Margem fora do normal",
            "Pedido com margem acima de 60%: costuma ser custo ou preço errado.",
            "evento", "na rodada do robô (:15/:45)", "servidor", "margem",
            L(robo="vigia_margem", grupo=G_ROBO_MARGEM), interruptor="robo_config",
            interruptor_alvo="vigia_margem.avisar_margem_alta",
            # Desligado (a caixinha), nada é gravado na auditoria: ao religar,
            # avisa o que estiver alto naquela hora.
            depende_de=(_DEP_ROBO_MARGEM,), ao_desligar="acumula", fatia="F3",
            leitura="informar:margem_auto", migrado=True,
        ),
        E(
            K.MARGEM_INFORMAR, "Margem", None, "Informar da Margem",
            "Os pedidos pendentes de análise da Margem, um por mensagem, com conta, "
            "produto, motivo, margem e lucro.",
            "manual", "Informar › Enviar agora (só pela API hoje)", "servidor", "margem",
            L(informar=("margem",)), ao_desligar="recusa", fatia="F3",
            leitura="informar:margem", migrado=True,
        ),
    ]


# Repetições que o dono decide na tela (§12.3 do desenho): a frase vai na
# descrição dos dois lados.
_REPETICOES = {
    "vigia_correios": (
        " Repete o aviso 'Ocorrência grave dos Correios' da Logística (mesma ocorrência; "
        "quem está nos dois recebe em dobro): decida qual manter."
    ),
}


def _ouvidoria() -> list[EnvioDef]:
    """Itens 29 a 42: um envio por robô da Ouvidoria (o aviso das ocorrências
    pendentes, `ouvidoria.avisar_pendentes`). O remetente é o `contexto` do
    robô no catálogo da Ouvidoria.

    F3: todos leem o Cadastro (C10a; o do Robô da Margem, 34, com a Margem
    na C10b). Quem vê a tela dos robôs (`ouvidoria:view`) vê quem recebe (só
    nomes), como via a lista "Avisar" antes; só admin altera."""
    out = []
    for chave, robo in ROBOS.items():
        if chave == _ROBO_COM_ENVIO_PROPRIO:
            continue
        out.append(
            EnvioDef(
                chave=f"ouvidoria.{chave}",
                painel="Ouvidoria",
                aba="Robôs",
                nome=robo.nome,
                descricao=(
                    f"Ocorrências pendentes do robô '{robo.nome}', numa mensagem só. "
                    f"{robo.descricao}{_REPETICOES.get(chave, '')}"
                ),
                frequencia_tipo="evento",
                frequencia_texto=f"quando há pendência; confere {robo.cadencia_texto}",
                onde_roda="servidor",
                remetente=robo.contexto,
                legado=Legado(
                    robo=chave, grupo=G_ROBO_MARGEM if chave == "vigia_margem" else None
                ),
                depende_de=(_dep_robo(chave),),
                ao_desligar="acumula",
                leitura="perm:ouvidoria:view",
                fatia="F3",
                migrado=True,
            )
        )
    return out


def _demais() -> list[EnvioDef]:
    E = EnvioDef  # noqa: N806
    L = Legado  # noqa: N806
    conferencia = []
    for chave, nome, env in (
        (K.MARKETING_CONFERENCIA_SHOPEE, "Shopee", "conferencia_shopee_threema"),
        (K.MARKETING_CONFERENCIA_ML, "Mercado Livre", "conferencia_ml_threema"),
        (K.MARKETING_CONFERENCIA_AMAZON, "Amazon", "conferencia_amazon_threema"),
    ):
        conferencia.append(
            E(
                chave, "Marketing", "Conferência", f"Conferência {nome}",
                f"Resumo da rodada da Conferência {nome} quando ela fecha, com o link do "
                "Excel e o da aba no DaVinci. Reenvia por até 6 h se falhar.",
                "evento", "quando a rodada fecha (varredor de 10 min)", "servidor", "chamados",
                # Desligado, a rodada não é carimbada: religado em até 6 h
                # (JANELA_REENVIO), o varredor ainda manda; depois disso, o
                # aviso daquela rodada se perde.
                L(informar=("conferencia_shopee",), grupo=G_CONFERENCIA, interruptor_env=env),
                fatia="F2", leitura="informar:conferencia_shopee", migrado=True,
            )
        )
    return [
        E(
            K.DENUNCIA_RELATORIO_DIARIO, "Denúncia", "Robô", "Relatório da Denúncia",
            "Relatório do dia anterior do robô de Denúncia. O botão de teste da tela sai "
            "por aqui também, como origem 'teste'.",
            "horario", "a partir das 07:10", "servidor", "chamados",
            # Desligado (o modo do robô), o dia não é carimbado, mas só sai o
            # relatório de ONTEM: os dias do período se perdem.
            L(informar=("denuncia_relatorio",)), interruptor="robo_modo",
            interruptor_alvo="denuncia_relatorio_threema", fatia="F2",
            leitura="informar:denuncia_relatorio", migrado=True,
        ),
        E(
            K.DENUNCIA_ROBO_PRECISA_ALGUEM, "Denúncia", "Robô",
            "Robô de Denúncia precisa de alguém",
            "Captcha na tela, robô parado, mini sem notícia ou SEI pedindo código.",
            "evento", "conferido a cada 2 min", "servidor", "chamados",
            # Desligado, cada ocorrência nova é marcada no Redis mesmo sem envio
            # (3 dias; o estado do momento, enquanto durar): ao religar só avisa
            # o que for novo.
            L(informar=("denuncia_robo", "chamados_ia"), grupo=G_IA_CHAMADO), fatia="F2",
            leitura="informar:denuncia_robo", migrado=True,
        ),
        *conferencia,
        E(
            K.PEDIDOS_CORRECAO_DIA_ENVIO, "Pedidos", None, "Correção do dia de envio",
            "Fila de correções do dia de envio (bling_envio_correcao). Ninguém consome a "
            "fila: nada sai daqui hoje.",
            "evento", "sem consumidor", "servidor", None, L(), interruptor="fixo",
            ao_desligar="—", gerenciado=False,
        ),
        E(
            K.CONTROLE_ESTOQUE_HERMES_ESTOQUE_NEGATIVO, "Controle de Estoque", None,
            "Estoque virtual negativo (Hermes)",
            "Job do Hermes 'Bling estoque virtual negativo <=3', com os IDs fixos no "
            "próprio Hermes. Fica fora do DaVinci.",
            "horario", "a cada 30 min", "externo", None, L(), interruptor="fixo",
            ao_desligar="—", gerenciado=False,
        ),
    ]


def _mac() -> list[EnvioDef]:
    E = EnvioDef  # noqa: N806
    L = Legado  # noqa: N806
    frete = L(env_mac="ML_FRETE_THREEMA_TO")
    return [
        E(
            K.CHAMADOS_FRETE_ML_RELATORIO_NOITE, "Chamados", None,
            "Relatório da noite do frete ML",
            "Resumo da rodada da noite do robô de chamados de frete do ML: abertos, pulados "
            "e erros.",
            "horario", "03:30 (repescagem 03:45)", "mac", None, frete, fatia="mac",
        ),
        E(
            K.CHAMADOS_FRETE_ML_ANUNCIO_NAO_ENCONTRADO, "Chamados", None,
            "Anúncio não encontrado no frete ML",
            "Pedidos da rodada do frete sem captura do anúncio (excluído, pausado, não "
            "encontrado ou erro), para conferir à mão.",
            "evento", "na rodada das 03:30", "mac", None, frete, fatia="mac",
        ),
        E(
            K.CHAMADOS_FRETE_ML_RESPOSTA_RECEBIDA, "Chamados", None,
            "ML respondeu chamado de frete",
            "Uma mensagem por chamado de frete que o ML respondeu, com o resumo e o link.",
            "horario", "04:30", "mac", None, frete, fatia="mac",
        ),
        E(
            K.DEVOLUCOES_CODIGOS_ML_TUTA, "Devoluções", None, "Códigos de devolução do ML",
            "Códigos de autorização de devolução e retirada Full do ML lidos no Tuta. É o "
            "único caminho que entrega os códigos. Guarda o que cada pessoa já recebeu. "
            "Repete o 'Códigos de devolução do Tuta (servidor)', que está parado: decida "
            "qual manter.",
            "horario", "08:00, 11:00 e 14:30", "mac", None,
            L(env_mac="DEVOL_CODIGOS_THREEMA_TO"), ao_desligar="acumula", fatia="mac",
        ),
        E(
            K.DEVOLUCOES_CODIGOS_ML_TUTA_FALHOU, "Devoluções", None,
            "Códigos de devolução não saíram",
            "O Tuta pediu login ou a pasta de devoluções não apareceu: os códigos do dia "
            "não saíram.",
            "evento", "em cada execução que falhar", "mac", None, frete, fatia="mac",
        ),
        E(
            K.CHAMADOS_AGENTE_RESUMO_DIARIO, "Chamados", None, "Resumo diário do agente",
            "Resumo de 24 h do agente de chamados do ML.",
            "horario", "22:30", "mac", None, frete, fatia="mac",
        ),
        E(
            K.CHAMADOS_AGENTE_ML_AVISOS, "Chamados", None, "Avisos do agente de chamados ML",
            "Os 13 avisos do agente de chamados do ML, pausado desde 25/09: rodada falhou "
            "(run_agente_ml.sh), agente parado (sentinela_agente_ml.sh), perfil em login, "
            "envio incerto, consulta morta, decisão do caso, sugestão de fechar, humano no "
            "ML e humano nas outras plataformas (ml_caso_autonomo.py), cérebro das "
            "mediações (tuta_cerebro.py), formulário já tentado e resumo do Seller Center "
            "(seller_center_abridor.py) e conta cega (ml_casos_manuais_monitor.py). Migram "
            "quando o agente voltar.",
            "evento", "rodadas do agente (:05/:35), pausado", "mac", None, frete,
            interruptor="fixo", ao_desligar="—", gerenciado=False,
        ),
        E(
            K.CHAMADOS_FORMULARIO_ML_SEM_PROTOCOLO, "Chamados", None,
            "Formulário do ML sem protocolo",
            "O formulário do ML foi enviado mas o protocolo não apareceu: não reenviar, "
            "conferir em Consultas recentes.",
            "evento", "na rodada das :25 e :55", "mac", None, frete, fatia="mac",
        ),
        E(
            K.NOTAS_FISCAIS_LOTE100_RESUMO, "Logística", "Notas fiscais",
            "Resumo do lote de NF 100%",
            "Resumo final do lote de NF 100% da noite: fatias, NFs certas e com falha, e "
            "a subida.",
            "horario", "22:30", "mac", None, frete, fatia="mac",
        ),
        E(
            K.NOTAS_FISCAIS_LOTE100_PAROU, "Logística", "Notas fiscais",
            "Lote de NF 100% parou",
            "O lote de NF 100% parou ou precisa de alguém (13 pontos do lote da noite).",
            "evento", "durante o lote das 22:30 e das retomadas", "mac", None, frete,
            fatia="mac",
        ),
        E(
            K.CHAMADOS_ROBO_TUTA_LEGADO, "Chamados", None, "Robô do Tuta (antigo)",
            "Aviso do robô antigo do Tuta (run_tuta.sh), fora do cron desde 13/09.",
            "manual", "fora do cron", "mac", None, frete, interruptor="fixo",
            ao_desligar="—", gerenciado=False,
        ),
    ]


ENVIOS: tuple[EnvioDef, ...] = (*_servidor(), *_ouvidoria(), *_demais(), *_mac())
CATALOGO: dict[str, EnvioDef] = {d.chave: d for d in ENVIOS}


def chave_ouvidoria(robo_chave: str) -> str | None:
    """A chave do envio de um robô da Ouvidoria, ou None se o robô não tem
    envio no catálogo (o Relatório da Denúncia tem envio próprio)."""
    chave = f"ouvidoria.{robo_chave}"
    return chave if chave in CATALOGO else None


def constantes() -> dict[str, str]:
    """{NOME: chave} de todas as constantes de `K`."""
    return {n: v for n, v in vars(K).items() if n.isupper() and isinstance(v, str)}


def _validar() -> None:
    """Roda no import: chave com formato errado, longa demais ou repetida, e
    constante que não existe no catálogo, quebram o import (e o CI e o
    startup), em vez de virar aviso perdido."""
    vistas: set[str] = set()
    for d in ENVIOS:
        if not _FORMATO_CHAVE.match(d.chave) or len(d.chave) > _TAMANHO_CHAVE:
            raise ValueError(f"threema_catalogo: chave inválida {d.chave!r}")
        if d.chave in vistas:
            raise ValueError(f"threema_catalogo: chave repetida {d.chave!r}")
        vistas.add(d.chave)
        if d.fatia is not None and d.fatia not in FATIAS:
            raise ValueError(f"threema_catalogo: fatia inválida em {d.chave!r}")
    valores = list(constantes().values())
    if len(valores) != len(set(valores)):
        raise ValueError("threema_catalogo: duas constantes com a mesma chave")
    faltando = sorted(set(valores) - vistas)
    if faltando:
        raise ValueError(f"threema_catalogo: constante sem envio no catálogo: {faltando}")


_validar()
