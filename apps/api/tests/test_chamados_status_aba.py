"""Coluna Status verdadeira. 18/09 (Eduardo: "atualmente está uma zona, precisamos
de mais organização com os status verdadeiros") e 19/09 (Vinicius: cinco status —
Análise Humano / Análise Robô / Aguard. Plataforma / Encerrado / Concluído; "primeiro
o robô, gente só quando o robô desiste"). Cada caso aqui é uma linha que aparecia
errada na aba."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import Chamado, ChamadoMensagem
from app.services import chamados as svc

T0 = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)


def _ch(**kw) -> Chamado:
    base = {"pedido_bling": "1", "origem": "devolucao", "canal": "api", "resolvido": False}
    base.update(kw)
    return Chamado(**base)


def _msg(direcao: str, *, status: str = "enviada", erro: str | None = None,
         canal: str = "api", minutos: int = 0, tipo: str = "abertura", texto: str = "x",
         tentativas: int = 0) -> ChamadoMensagem:
    quando = T0 + timedelta(minutes=minutos)
    return ChamadoMensagem(direcao=direcao, status=status, erro=erro, canal=canal, tipo=tipo,
                           texto=texto, created_at=quando, tentativas=tentativas,
                           enviada_at=quando if status == "enviada" else None)


def _status(ch, fala, analise=None, humano=False, esperar=False, instrucao=None):
    return svc.status_e_motivo_da_aba(ch, ultima_fala=fala, ultima_analise=analise,
                                      analise_pede_humano=humano, analise_pede_esperar=esperar,
                                      instrucao_pendente=instrucao)


def test_shopee_sem_motivo_liberado_e_analise_robo_por_outro_caminho():
    """292270/293406/292535/293135/293749: pendente com shopee_motivo_indisponivel.
    18/09 virou "esperando liberar"; 19/09 (Vinicius): o robô tem outro caminho —
    é Análise Robô, e a coluna diz o porquê."""
    fala = _msg("enviada", status="pendente", erro="shopee_motivo_indisponivel")
    cod, _, motivo = _status(_ch(), fala)
    assert cod == svc.ABA_ANALISE_ROBO
    assert "Shopee" in motivo and "outro caminho" in motivo


def test_bloqueio_que_o_robo_leu_e_decidiu_aguardar_e_aguard_plataforma():
    fala = _msg("enviada", status="pendente", erro="tiktok_aguardando_pacote", minutos=0)
    analise = _msg("sistema", tipo="analise", minutos=5, texto="… → aguardar a plataforma")
    cod, quando, motivo = _status(_ch(), fala, analise, esperar=True)
    assert cod == svc.ABA_AGUARD_PLATAFORMA and quando == analise.created_at
    assert "trânsito" in motivo and "aguardar" in motivo
    # análise ANTERIOR à fala presa não conta
    velha = _msg("sistema", tipo="analise", minutos=-5, texto="… → aguardar a plataforma")
    assert _status(_ch(), fala, velha, esperar=True)[0] == svc.ABA_ANALISE_ROBO


def test_falta_foto_e_quebra_cabeca_pedem_humano():
    cod, _, motivo = _status(_ch(), _msg("enviada", status="pendente", erro="devolucao_sem_foto"))
    assert (cod, motivo) == (svc.ABA_ANALISE_HUMANO, "falta foto na devolução")
    cod, _, motivo = _status(_ch(), _msg("enviada", status="falhou", erro="shopee_captcha_humano", canal="robo"))
    assert cod == svc.ABA_ANALISE_HUMANO and "quebra-cabeça" in motivo


def test_replica_com_foto_que_o_robo_do_ml_nao_anexa_pede_humano():
    """293800: "Envio falhou" — na verdade era coisa pra gente decidir."""
    erro = "tem foto anexada: o robô da página do ML ainda não anexa arquivo — reenvie sem foto"
    cod, _, _ = _status(_ch(), _msg("enviada", status="falhou", erro=erro, canal="robo", tipo="replica"))
    assert cod == svc.ABA_ANALISE_HUMANO


def test_fila_de_verdade_e_analise_robo_e_diz_se_e_robo_ou_api():
    cod, _, motivo = _status(_ch(), _msg("enviada", status="pendente", canal="robo"))
    assert (cod, motivo) == (svc.ABA_ANALISE_ROBO, "na fila do robô")
    cod, _, motivo = _status(_ch(), _msg("enviada", status="pendente", canal="api"))
    assert (cod, motivo) == (svc.ABA_ANALISE_ROBO, "saindo pela API")
    # 19/09: voltou pra fila depois de uma tentativa que falhou — a coluna conta
    fala = _msg("enviada", status="pendente", canal="robo", erro="formulário mudou", tentativas=1)
    cod, _, motivo = _status(_ch(), fala)
    assert cod == svc.ABA_ANALISE_ROBO
    assert motivo == "na fila do robô — tentativa 1 de 3 falhou: formulário mudou"


def test_envio_falhou_de_vez_e_humano():
    """Só fica `falhou` quando o robô esgotou as tentativas (ou o erro pede gente)."""
    fala = _msg("enviada", status="falhou", erro="formulário mudou", canal="robo")
    cod, _, motivo = _status(_ch(), fala)
    assert cod == svc.ABA_ANALISE_HUMANO and motivo == "envio falhou: formulário mudou"


def test_falha_que_o_acompanhamento_segue_nao_e_envio_falhou():
    cod, _, _ = _status(_ch(), _msg("enviada", status="falhou", erro="shopee_ja_contestada"))
    assert cod == svc.ABA_AGUARD_PLATAFORMA
    # 19/09: fala da API substituída pelo robô (outro caminho) também não é falha
    fala = _msg("enviada", status="falhou", erro="substituida_pelo_robo")
    assert _status(_ch(canal="robo"), fala)[0] == svc.ABA_AGUARD_PLATAFORMA


def test_tarefa_sem_api_registrada_pede_humano_e_nao_aguardando():
    """Amazon / réplica Shopee-TikTok sem API: a mensagem nunca saiu."""
    cod, _, motivo = _status(_ch(), _msg("enviada", status="registrada", erro="plataforma_sem_api"))
    assert cod == svc.ABA_ANALISE_HUMANO and "sem API" in motivo


def test_plataforma_respondeu_e_do_robo_se_ha_robo_senao_de_gente():
    """Canal robô e manual do ML têm robô (o cérebro assume os manuais do ML);
    canal api e manual de outra plataforma não — é gente no Seller Center."""
    fala = _msg("recebida", status="registrada")
    cod, _, motivo = _status(_ch(canal="robo", plataforma="ml"), fala)
    assert cod == svc.ABA_ANALISE_ROBO and "robô" in motivo
    cod, _, motivo = _status(_ch(canal="manual", plataforma="ml"), fala)
    assert cod == svc.ABA_ANALISE_ROBO
    cod, _, motivo = _status(_ch(canal="api", plataforma="shopee"), fala)
    assert cod == svc.ABA_ANALISE_HUMANO and "Seller Center" in motivo
    cod, _, _ = _status(_ch(canal="manual", plataforma="shopee"), fala)
    assert cod == svc.ABA_ANALISE_HUMANO


def test_robo_leu_a_resposta_e_decidiu_aguardar():
    """294068 / 288409: "Plataforma respondeu" mesmo depois de o robô decidir aguardar."""
    fala = _msg("recebida", status="registrada", minutos=0)
    analise = _msg("sistema", tipo="analise", minutos=5, texto="… → aguardar a plataforma")
    cod, _, motivo = _status(_ch(canal="robo"), fala, analise, esperar=True)
    assert cod == svc.ABA_AGUARD_PLATAFORMA and "aguardar" in motivo
    # sem análise depois da fala, continua com o robô
    cod, _, _ = _status(_ch(canal="robo"), fala)
    assert cod == svc.ABA_ANALISE_ROBO


def test_status_oficial_e_resposta_ja_analisada():
    ch = _ch(canal="robo", status_plataforma=svc.STATUS_EM_ANALISE,
             status_plataforma_at=T0 - timedelta(days=1))
    fala = _msg("recebida", status="registrada")
    analise = _msg("sistema", tipo="analise", minutos=2)
    cod, quando, motivo = _status(ch, fala, analise, esperar=True)
    assert (cod, quando) == (svc.ABA_AGUARD_PLATAFORMA, ch.status_plataforma_at)
    assert motivo == "em análise na plataforma"
    assert _status(ch, fala)[0] == svc.ABA_ANALISE_ROBO
    ch.status_plataforma = svc.STATUS_REEMBOLSO_PAGO
    assert _status(ch, _msg("enviada", minutos=-1))[2] == "reembolso pago — 24 h de carência"


def test_cerebro_pediu_humano_continua_valendo():
    fala = _msg("recebida", status="registrada")
    analise = _msg("sistema", tipo="analise", minutos=1)
    cod, _, motivo = _status(_ch(canal="robo"), fala, analise, humano=True)
    assert cod == svc.ABA_ANALISE_HUMANO and motivo == "o robô pediu revisão humana"


def test_shopee_pediu_prova_e_humano_ate_mandarmos_algo():
    """Decisão do Vinicius 19/09: prova é humano."""
    ch = _ch(status_plataforma=svc.STATUS_PROVA, status_plataforma_at=T0)
    cod, _, motivo = _status(ch, _msg("recebida", status="registrada", minutos=1))
    assert (cod, motivo) == (svc.ABA_ANALISE_HUMANO, "Shopee pediu prova adicional")
    # a prova saiu (o acompanhamento manda as fotos pela API) → bola com a Shopee
    cod, _, motivo = _status(ch, _msg("enviada", minutos=2, tipo="replica"))
    assert cod == svc.ABA_AGUARD_PLATAFORMA and "prova enviada" in motivo


def test_prova_ja_enviada_e_shopee_respondeu_cai_na_regra_7():
    """19/09 (ajuste A6): a prova saiu e a Shopee respondeu em seguida — a ÚLTIMA fala
    é dela, mas já mandamos algo depois do pedido; não é "pediu prova" de novo, é a
    regra 7 (plataforma respondeu → robô se há robô, senão gente). A listagem olha o
    histórico inteiro e passa `nossa_fala_apos_status`."""
    resposta = _msg("recebida", status="registrada", minutos=5, tipo="resposta")
    # canal api (devolução Shopee): sem robô que responda → gente, no Seller Center
    ch = _ch(canal="api", plataforma="shopee", status_plataforma=svc.STATUS_PROVA,
             status_plataforma_at=T0)
    cod, quando, motivo = _status(ch, resposta)
    assert (cod, motivo) == (svc.ABA_ANALISE_HUMANO, "Shopee pediu prova adicional")
    cod, quando, motivo = svc.status_e_motivo_da_aba(
        ch, ultima_fala=resposta, ultima_analise=None, analise_pede_humano=False,
        nossa_fala_apos_status=True,
    )
    assert cod == svc.ABA_ANALISE_HUMANO and quando == resposta.created_at
    assert motivo == "plataforma respondeu — responder no Seller Center"
    # canal robô: o robô analisa
    ch_robo = _ch(canal="robo", plataforma="shopee", status_plataforma=svc.STATUS_PROVA,
                  status_plataforma_at=T0)
    cod, _, motivo = svc.status_e_motivo_da_aba(
        ch_robo, ultima_fala=resposta, ultima_analise=None, analise_pede_humano=False,
        nossa_fala_apos_status=True,
    )
    assert cod == svc.ABA_ANALISE_ROBO and "robô analisa" in motivo
    # o robô já leu e decidiu aguardar → Aguard. Plataforma com o motivo oficial
    analise = _msg("sistema", tipo="analise", minutos=6, texto="… → aguardar a plataforma")
    cod, quando, motivo = svc.status_e_motivo_da_aba(
        ch_robo, ultima_fala=resposta, ultima_analise=analise, analise_pede_humano=False,
        analise_pede_esperar=True, nossa_fala_apos_status=True,
    )
    assert (cod, quando) == (svc.ABA_AGUARD_PLATAFORMA, T0)
    assert motivo == "prova enviada — a Shopee analisa"


def test_instrucao_pendente_manda_pro_robo_mesmo_com_humano_pedido():
    fala = _msg("recebida", status="registrada")
    analise = _msg("sistema", tipo="analise", minutos=1)
    texto = "Responde que o pacote foi entregue dia 12 e anexa o comprovante da transportadora"
    instrucao = _msg("sistema", tipo="instrucao", minutos=3, texto=texto)
    cod, quando, motivo = _status(_ch(canal="api"), fala, analise, humano=True, instrucao=instrucao)
    assert cod == svc.ABA_ANALISE_ROBO and quando == instrucao.created_at
    assert motivo.startswith("instrução pendente pro robô: Responde que o pacote")
    assert motivo.endswith("…")


def test_sem_fala_nenhuma_e_humano_registrado_a_mao():
    cod, _, motivo = _status(_ch(), None)
    assert cod == svc.ABA_ANALISE_HUMANO and "registrado à mão" in motivo


def test_encerrado_e_concluido():
    """Encerrado = a plataforma decidiu e falta gente fechar; Concluído = pessoa fechou."""
    ch = _ch(status_plataforma=svc.STATUS_GANHAMOS, status_plataforma_at=T0)
    cod, quando, motivo = _status(ch, _msg("recebida", status="registrada", minutos=5))
    assert (cod, quando, motivo) == (svc.ABA_ENCERRADO, T0, "ganhamos")
    ch.valor_sugerido = Decimal("728.22")
    assert _status(ch, None)[2] == "ganhamos · robô sugere lucro de R$ 728,22"
    ch.status_plataforma = svc.STATUS_ENCERRADO
    assert _status(ch, None)[2].startswith("plataforma encerrou sem decisão")
    # instrução pendente NÃO tira do Encerrado (só o cérebro consome; a linha fica lá)
    instrucao = _msg("sistema", tipo="instrucao", minutos=3, texto="olha de novo")
    assert _status(ch, None, instrucao=instrucao)[0] == svc.ABA_ENCERRADO
    # pessoa fechou
    ch.resolvido = True
    ch.resolvido_at = T0 + timedelta(days=1)
    ch.valor_recuperado = Decimal("-50")
    cod, quando, motivo = _status(ch, _msg("enviada"))
    assert (cod, quando) == (svc.ABA_CONCLUIDO, ch.resolvido_at)
    assert motivo == "sem decisão da plataforma — prejuízo de R$ 50,00"
    ch.status_plataforma = svc.STATUS_PERDEMOS
    assert _status(ch, None)[2] == "perdemos — prejuízo de R$ 50,00"


def test_wrapper_antigo_devolve_so_codigo_e_data():
    cod, quando = svc.status_da_aba(_ch(), ultima_fala=_msg("enviada"), ultima_analise=None,
                                    analise_pede_humano=False)
    assert cod == svc.ABA_AGUARD_PLATAFORMA and quando == T0


def test_recusa_registrada_e_aguardando_plataforma_com_motivo():
    """296936 (Vinicius 18/09): a recusa que mandamos virava "Ganhamos". O sync grava
    `aguardando` como status oficial e a coluna diz por quê."""
    ch = _ch(canal="api", plataforma="tiktok", status_plataforma=svc.STATUS_AGUARDANDO,
             status_plataforma_at=T0)
    cod, quando, motivo = _status(ch, _msg("enviada", status="enviada", minutos=-5))
    assert (cod, quando) == (svc.ABA_AGUARD_PLATAFORMA, T0)
    assert motivo == "nossa recusa registrada — o comprador ainda pode recorrer"
    # o comprador falou DEPOIS da recusa → é a vez de alguém ler (canal api: gente)
    cod, _, _ = _status(ch, _msg("recebida", status="registrada", minutos=30))
    assert cod == svc.ABA_ANALISE_HUMANO
    # nota antiga do comprador (anterior à recusa) não muda nada
    cod, _, _ = _status(ch, _msg("recebida", status="registrada", minutos=-600))
    assert cod == svc.ABA_AGUARD_PLATAFORMA
