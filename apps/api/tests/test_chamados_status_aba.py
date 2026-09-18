"""Coluna Status verdadeira (Eduardo 18/09: "atualmente está uma zona, precisamos
de mais organização com os status verdadeiros"). Cada caso aqui é uma linha que
aparecia errada na aba em 18/09."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import Chamado, ChamadoMensagem
from app.services import chamados as svc

T0 = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)


def _ch(**kw) -> Chamado:
    return Chamado(pedido_bling="1", origem="devolucao", canal="api", resolvido=False, **kw)


def _msg(direcao: str, *, status: str = "enviada", erro: str | None = None,
         canal: str = "api", minutos: int = 0, tipo: str = "abertura", texto: str = "x") -> ChamadoMensagem:
    quando = T0 + timedelta(minutes=minutos)
    return ChamadoMensagem(direcao=direcao, status=status, erro=erro, canal=canal, tipo=tipo,
                           texto=texto, created_at=quando,
                           enviada_at=quando if status == "enviada" else None)


def _status(ch, fala, analise=None, humano=False, esperar=False):
    return svc.status_e_motivo_da_aba(ch, ultima_fala=fala, ultima_analise=analise,
                                      analise_pede_humano=humano, analise_pede_esperar=esperar)


def test_shopee_sem_motivo_liberado_nao_e_fila_do_robo():
    """292270/293406/292535/293135/293749: pendente com shopee_motivo_indisponivel
    aparecia "Na fila do robô" — nenhum robô pega; é a Shopee que ainda não libera."""
    cod, _, motivo = _status(_ch(), _msg("enviada", status="pendente", erro="shopee_motivo_indisponivel"))
    assert cod == svc.STATUS_ESPERANDO_LIBERAR
    assert "Shopee" in motivo


def test_falta_foto_e_quebra_cabeca_pedem_humano():
    cod, _, motivo = _status(_ch(), _msg("enviada", status="pendente", erro="devolucao_sem_foto"))
    assert (cod, motivo) == (svc.STATUS_HUMANO, "falta foto na devolução")
    cod, _, motivo = _status(_ch(), _msg("enviada", status="falhou", erro="shopee_captcha_humano", canal="robo"))
    assert cod == svc.STATUS_HUMANO and "quebra-cabeça" in motivo


def test_replica_com_foto_que_o_robo_do_ml_nao_anexa_pede_humano():
    """293800: "Envio falhou" — na verdade era coisa pra gente decidir."""
    erro = "tem foto anexada: o robô da página do ML ainda não anexa arquivo — reenvie sem foto"
    cod, _, _ = _status(_ch(), _msg("enviada", status="falhou", erro=erro, canal="robo", tipo="replica"))
    assert cod == svc.STATUS_HUMANO


def test_fila_de_verdade_diz_se_e_robo_ou_api():
    cod, _, motivo = _status(_ch(), _msg("enviada", status="pendente", canal="robo"))
    assert (cod, motivo) == (svc.STATUS_FILA, "na fila do robô")
    cod, _, motivo = _status(_ch(), _msg("enviada", status="pendente", canal="api"))
    assert (cod, motivo) == (svc.STATUS_FILA, "saindo pela API")


def test_falha_que_o_acompanhamento_segue_nao_e_envio_falhou():
    cod, _, _ = _status(_ch(), _msg("enviada", status="falhou", erro="shopee_ja_contestada"))
    assert cod == svc.STATUS_AGUARDANDO


def test_tarefa_sem_api_registrada_pede_humano_e_nao_aguardando():
    """Amazon / réplica Shopee-TikTok sem API: a mensagem nunca saiu."""
    cod, _, motivo = _status(_ch(), _msg("enviada", status="registrada", erro="plataforma_sem_api"))
    assert cod == svc.STATUS_HUMANO and "sem API" in motivo


def test_robo_leu_a_resposta_e_decidiu_aguardar():
    """294068 / 288409: "Plataforma respondeu" mesmo depois de o robô decidir aguardar."""
    fala = _msg("recebida", status="registrada", minutos=0)
    analise = _msg("sistema", tipo="analise", minutos=5, texto="… → aguardar a plataforma")
    cod, _, motivo = _status(_ch(), fala, analise, esperar=True)
    assert cod == svc.STATUS_AGUARDANDO and "aguardar" in motivo
    # sem análise depois da fala, continua "respondeu"
    cod, _, _ = _status(_ch(), fala)
    assert cod == svc.STATUS_RESPONDEU


def test_status_oficial_e_resposta_ja_analisada():
    ch = _ch(status_plataforma=svc.STATUS_EM_ANALISE, status_plataforma_at=T0 - timedelta(days=1))
    fala = _msg("recebida", status="registrada")
    analise = _msg("sistema", tipo="analise", minutos=2)
    assert _status(ch, fala, analise, esperar=True)[0] == svc.STATUS_EM_ANALISE
    assert _status(ch, fala)[0] == svc.STATUS_RESPONDEU


def test_cerebro_pediu_humano_continua_valendo():
    fala = _msg("recebida", status="registrada")
    analise = _msg("sistema", tipo="analise", minutos=1)
    cod, _, motivo = _status(_ch(), fala, analise, humano=True)
    assert cod == svc.STATUS_HUMANO and motivo


def test_resolvido_e_sem_acompanhamento():
    assert _status(_ch(), None)[0] == svc.STATUS_SEM_ACOMPANHAMENTO
    ch = _ch()
    ch.resolvido = True
    assert _status(ch, _msg("enviada"))[0] == svc.STATUS_ENCERRADO


def test_wrapper_antigo_devolve_so_codigo_e_data():
    cod, quando = svc.status_da_aba(_ch(), ultima_fala=_msg("enviada"), ultima_analise=None,
                                    analise_pede_humano=False)
    assert cod == svc.STATUS_AGUARDANDO and quando == T0


def test_recusa_registrada_e_aguardando_plataforma_com_motivo():
    """296936 (Vinicius 18/09): a recusa que mandamos virava "Ganhamos". O sync grava
    `aguardando` como status oficial e a coluna diz por quê."""
    ch = _ch(status_plataforma=svc.STATUS_AGUARDANDO, status_plataforma_at=T0)
    cod, quando, motivo = _status(ch, _msg("enviada", status="enviada", minutos=-5))
    assert (cod, quando) == (svc.STATUS_AGUARDANDO, T0)
    assert motivo == "nossa recusa registrada — o comprador ainda pode recorrer"
    # o comprador falou DEPOIS da recusa → é a vez de alguém ler
    cod, _, _ = _status(ch, _msg("recebida", status="registrada", minutos=30))
    assert cod == svc.STATUS_RESPONDEU
    # nota antiga do comprador (anterior à recusa) não muda nada
    cod, _, _ = _status(ch, _msg("recebida", status="registrada", minutos=-600))
    assert cod == svc.STATUS_AGUARDANDO
