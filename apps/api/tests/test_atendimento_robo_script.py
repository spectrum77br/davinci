"""Robô de leitura da Temu/AliExpress (scripts/atendimento_robo_adspower.py).

Sem AdsPower e sem navegador de verdade (a integração com os perfis é outra
etapa). Cobre:
- as partes puras: filtro de URL (só o chat sai do Mac; marcar-lido, envio e
  token nunca), limpeza de credencial da URL, lotes do contrato, detecção de
  login/verificação pela URL e pelo corpo, validação do JSON das lojas (URL
  de conversa específica é recusada);
- o gancho JS rodando no Node com fetch/XHR/WebSocket falsos (pulado sem
  `node`): copia só o que deve, não atrapalha a página, não duplica;
- o Leitor com um navegador FALSO: o gancho é registrado ANTES de navegar e
  só na aba própria (nunca na da equipe), sessão caída vira `sessao_caiu`
  sem o robô insistir, aba fechada é reaberta, --seco não posta nada, e ao
  encerrar só a aba do robô fecha.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import atendimento_robo_adspower as robo

TEMU_CHAT = "https://br.seller.temu.com/chat.html"
ALI_CHAT = "https://gsp.aliexpress.com/m_apps/im-chat/im#/window"
ALI_H5 = "https://seller-acs.aliexpress.com/h5/"


# ---------------------------------------------------------------------------
# Filtro de URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("https://br.seller.temu.com/api/plateau/conv/getConvList", True),
        ("https://br.seller.temu.com/api/plateau/sync/message", True),
        ("https://br.seller.temu.com/api/plateau/sync/getInitState", True),
        ("https://br.seller.temu.com/api/plateau/message/getHistoryMsg", True),
        ("https://br.seller.temu.com/api/plateau/conv/needReplyCount", True),
        # efeito colateral ou credencial: nunca
        ("https://br.seller.temu.com/api/plateau/conv/markRead", False),
        ("https://br.seller.temu.com/api/plateau/conv/enterConv", False),
        ("https://br.seller.temu.com/api/plateau/message/sendMessage", False),
        ("https://br.seller.temu.com/api/plateau/message/sendMessagePreCheck", False),
        ("https://br.seller.temu.com/api/plateau/sign/signUrl", False),
        ("https://br.seller.temu.com/latitude/markNoNeedReplyMsg", False),
        # fora do chat
        ("https://br.seller.temu.com/api/phantom/xg/pfb/b", False),
        ("https://us.pftk.temu.com/pmm/api/pmm/front_log", False),
        ("https://br.seller.temu.com/mms/poplar/message/unread_count", False),
        # outro domínio, domínio parecido, ou a rota só no parâmetro
        ("https://evil.com/api/plateau/conv/getConvList", False),
        ("https://br.seller.temu.com.evil.com/api/plateau/conv/getConvList", False),
        ("https://evil.com/x?u=https://br.seller.temu.com/api/plateau/conv/x", False),
        ("http://br.seller.temu.com/api/plateau/conv/getConvList", False),
    ],
)
def test_filtro_http_temu(url, esperado):
    assert robo.url_relevante("temu", "http", url) is esperado


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        (ALI_H5 + "mtop.gsp.web.seller.js.sync/1.0/?jsv=2.7.2&sign=x", True),
        (ALI_H5 + "mtop.gsp.im.use.web.seller.messagebox.querysessionlist/1.0/", True),
        (ALI_H5 + "mtop.gsp.im.web.seller.messagebox.direction.querybysessionid/1.0/", True),
        (ALI_H5 + "mtop.gsp.im.use.web.seller.unreadcount/1.0/", True),
        # do IM, mas sem mensagem (vistas no teste real de 30/09): não copia
        (ALI_H5 + "mtop.csp.im.web.seller.quickreply.getlist/1.0/", False),
        (ALI_H5 + "mtop.gsp.im.web.seller.userinfo.get/1.0/", False),
        (ALI_H5 + "mtop.gsp.im.biz.seller.group.list/1.0/", False),
        (ALI_H5 + "mtop.csp.im.biz.ai.seller.stats/1.0/", False),
        (ALI_H5 + "mtop.ae.im.biz.seller.something/1.0/", False),
        # prefixo de uma API boa não puxa outra para dentro
        (ALI_H5 + "mtop.gsp.web.seller.js.syncx/1.0/", False),
        # token do WebSocket, marcar lido, envio, link assinado: nunca
        (ALI_H5 + "mtop.gsp.im.use.web.seller.gettoken/1.0/", False),
        (ALI_H5 + "mtop.gsp.im.receiver.web.seller.imusermessage.putrangeread/1.0/", False),
        (ALI_H5 + "mtop.gsp.im.receiver.web.seller.immessage.sendimmessage/1.0/", False),
        (ALI_H5 + "mtop.ae.im.biz.seller.send.msg/1.0/", False),
        (ALI_H5 + "mtop.csp.im.web.seller.download.url.get/1.0/", False),
        # fora do IM
        (ALI_H5 + "mtop.gsp.merchant.menu.get/1.0/", False),
        (ALI_H5 + "mtop.global.im.biz.isgrayuser/1.0/", False),
        ("https://evil.com/h5/mtop.gsp.web.seller.js.sync/1.0/", False),
    ],
)
def test_filtro_http_aliexpress(url, esperado):
    assert robo.url_relevante("aliexpress", "http", url) is esperado


def test_filtro_websocket():
    assert robo.url_relevante("temu", "ws", "wss://br.seller.temu.com?ws-titan-request-sign=dee0")
    assert robo.url_relevante("temu", "ws", "wss://br.seller.temu.com/")
    assert not robo.url_relevante("temu", "ws", "wss://outro.com/?temu.com")
    assert robo.url_relevante("aliexpress", "ws", "wss://msgacs.m.aliexpress.com/accs/auth?token=x")
    assert robo.url_relevante("aliexpress", "ws", "wss://ws-msgacs.xx.taobao.com/accs/auth?token=")
    assert not robo.url_relevante("aliexpress", "ws", "wss://outro.aliexpress.com/accs/")
    # tipo errado ou plataforma desconhecida
    assert not robo.url_relevante("temu", "http", "wss://br.seller.temu.com/")
    assert not robo.url_relevante("shein", "http", "https://x.shein.com/api/plateau/")


def test_regex_valem_no_python_e_no_js():
    """O mesmo texto vai para o gancho (new RegExp) — sem sintaxe só do Python."""
    for cfg in robo.PLATAFORMAS.values():
        for r in cfg["http"] + cfg["http_nao"] + cfg["ws"] + cfg["vigiar"]:
            re.compile(r)
            assert "(?" not in r, r


# ---------------------------------------------------------------------------
# URL sem credencial, rótulo de rota
# ---------------------------------------------------------------------------


def test_limpar_url_tira_credencial():
    assert (
        robo.limpar_url("wss://msgacs.m.aliexpress.com/accs/auth?token=SEGREDO")
        == "wss://msgacs.m.aliexpress.com/accs/auth"
    )
    limpa = robo.limpar_url(
        ALI_H5 + "mtop.gsp.web.seller.js.sync/1.0/?jsv=2.7.2&appKey=1&t=9&sign=abc"
        "&api=mtop.gsp.web.seller.js.sync&accessToken=z&sessionId=s"
    )
    assert "sign=" not in limpa and "abc" not in limpa
    assert "appKey" not in limpa and "accessToken" not in limpa and "sessionId" not in limpa
    assert "jsv=2.7.2" in limpa and "api=mtop.gsp.web.seller.js.sync" in limpa
    assert robo.limpar_url("wss://br.seller.temu.com/?ws-titan-request-sign=dee") == (
        "wss://br.seller.temu.com/"
    )
    assert robo.limpar_url(ALI_CHAT) == ALI_CHAT


def test_rotulo_rota():
    ev = {"tipo": "http", "url": "https://br.seller.temu.com/api/plateau/conv/getConvList"}
    assert robo.rotulo_rota(ev) == "conv/getConvList"
    ev = {"tipo": "http", "url": ALI_H5 + "mtop.gsp.web.seller.js.sync/1.0/"}
    assert robo.rotulo_rota(ev) == "mtop.gsp.web.seller.js.sync"
    assert robo.rotulo_rota({"tipo": "ws", "metodo": "binario", "url": "wss://x/"}) == (
        "ws:binario"
    )


# ---------------------------------------------------------------------------
# Detecção de sessão caída
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "chat", "esperado"),
    [
        (TEMU_CHAT, TEMU_CHAT, "chat"),
        (TEMU_CHAT + "?x=1", TEMU_CHAT, "chat"),
        ("https://br.seller.temu.com/login.html?redirectUrl=%2Fchat.html", TEMU_CHAT, "login"),
        ("https://seller.temu.com/main/authentication?from=br", TEMU_CHAT, "login"),
        ("https://br.seller.temu.com/main/order-manager", TEMU_CHAT, "fora"),
        (ALI_CHAT, ALI_CHAT, "chat"),
        ("https://gsp.aliexpress.com/m_apps/im-chat/im#/setting", ALI_CHAT, "chat"),
        ("https://login.aliexpress.com/seller?return_url=https%3A%2F%2Fgsp", ALI_CHAT, "login"),
        ("https://passport.aliexpress.com/ac/pc_r_open.htm", ALI_CHAT, "login"),
        ("https://gsp.aliexpress.com/_____tmd_____/punish?x5secdata=abc", ALI_CHAT, "verificacao"),
        ("https://gsp.aliexpress.com/m_apps/home", ALI_CHAT, "fora"),
        ("about:blank", ALI_CHAT, "vazia"),
        ("chrome-error://chromewebdata/", TEMU_CHAT, "vazia"),
        (None, TEMU_CHAT, "vazia"),
    ],
)
def test_situacao_da_aba(url, chat, esperado):
    assert robo.situacao_da_aba(url, chat) == esperado


def test_classificar_corpo():
    c = robo.classificar_corpo
    assert c("temu", '{"success":false,"errorCode":54001,"errorMsg":"x"}') == "verificacao"
    assert c("temu", '{"success":true,"errorCode":1000000,"result":{}}') == "ok"
    assert c("temu", '{"success":false,"errorCode":2000}') is None
    # o mesmo texto DENTRO de uma mensagem (aspas escapadas) não é sinal
    msg = json.dumps({"success": False, "result": {"content": '"errorCode":54001'}})
    assert c("temu", msg) is None
    assert c("aliexpress", '{"api":"x","data":{},"ret":["SUCCESS::ok"]}') == "ok"
    assert c("aliexpress", '{"ret":["FAIL_SYS_SESSION_EXPIRED::Session expirou"]}') == "login"
    assert c("aliexpress", '{"ret":["RGV587_ERROR::SM::x"]}') == "verificacao"
    assert c("aliexpress", '{"ret":["FAIL_SYS_USER_VALIDATE"]}') == "verificacao"
    # token da mtop vencendo é rotina (a página renova sozinha)
    assert c("aliexpress", '{"ret":["FAIL_SYS_TOKEN_EXOIRED::expirou"]}') is None
    assert c("temu", "") is None


# ---------------------------------------------------------------------------
# Evento do contrato e lotes
# ---------------------------------------------------------------------------


def _ev(corpo="{}", url="https://br.seller.temu.com/api/plateau/conv/getConvList", **kw):
    return {"tipo": "http", "url": url, "metodo": "POST", "status": 200,
            "recebido_em": "2026-09-30T12:00:00.000Z", "corpo": corpo, **kw}


def test_preparar_evento():
    ev = robo.preparar_evento("temu", _ev(url=_ev()["url"] + "?sign=x"))
    assert ev == {
        "tipo": "http",
        "url": "https://br.seller.temu.com/api/plateau/conv/getConvList",
        "metodo": "POST",
        "status": 200,
        "recebido_em": "2026-09-30T12:00:00.000Z",
        "corpo": "{}",
    }
    ws = robo.preparar_evento("aliexpress", {
        "tipo": "ws", "url": "wss://msgacs.m.aliexpress.com/accs/auth?token=S",
        "metodo": "texto", "status": None, "recebido_em": "lixo", "corpo": "{}"})
    assert ws["url"] == "wss://msgacs.m.aliexpress.com/accs/auth"
    assert ws["status"] is None and ws["recebido_em"] != "lixo"
    # irrelevante, tipo errado, corpo não-texto, corpo acima do contrato
    assert robo.preparar_evento("temu", _ev(url="https://br.seller.temu.com/api/phantom/x")) is None
    assert robo.preparar_evento("temu", _ev(tipo="xhr")) is None
    assert robo.preparar_evento("temu", _ev(corpo=None)) is None
    assert robo.preparar_evento("temu", _ev(corpo="x" * (robo.MAX_CORPO + 1))) is None
    assert robo.preparar_evento("temu", "nada") is None
    # status que não é inteiro some (bool é int no Python)
    assert robo.preparar_evento("temu", _ev(status=True))["status"] is None
    # URL enorme (GET da mtop com o pedido na busca) perde a busca, não o evento
    longa = robo.preparar_evento("aliexpress", _ev(
        url=ALI_H5 + "mtop.gsp.web.seller.js.sync/1.0/?data=" + "x" * 20000))
    assert longa["url"] == ALI_H5 + "mtop.gsp.web.seller.js.sync/1.0/"


def test_montar_lotes_por_quantidade_e_ordem():
    evs = [_ev(corpo=str(i)) for i in range(450)]
    lotes = robo.montar_lotes(evs)
    assert [len(x) for x in lotes] == [200, 200, 50]
    assert [e["corpo"] for lote in lotes for e in lote] == [str(i) for i in range(450)]
    assert robo.montar_lotes([]) == []


def test_montar_lotes_por_tamanho():
    grande = "x" * robo.MAX_CORPO                      # o maior corpo que o contrato aceita
    lotes = robo.montar_lotes([_ev(corpo=grande) for _ in range(5)])
    assert [len(x) for x in lotes] == [2, 2, 1]        # até 6 MB por POST
    for lote in lotes:
        assert sum(robo._tamanho(e) for e in lote) <= robo.MAX_BYTES_LOTE
    # evento sozinho acima do limite do lote vai num lote só dele
    assert [len(x) for x in robo.montar_lotes([_ev(corpo="abc")] * 3, max_bytes=10)] == [1, 1, 1]


def test_dedup():
    t = [0.0]
    d = robo.Dedup(600, lambda: t[0])
    assert d.novo(_ev(corpo="a"))
    assert not d.novo(_ev(corpo="a"))
    assert not d.novo(_ev(corpo="a", url=_ev()["url"] + "?t=2"))    # a busca não conta
    assert d.novo(_ev(corpo="b"))
    t[0] = 601.0
    assert d.novo(_ev(corpo="a"))


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


def test_json_das_lojas():
    lojas = robo.carregar_lojas(robo.LOJAS_PADRAO)
    assert {(x.perfil_id, x.plataforma, x.loja) for x in lojas} == {
        ("k1dkegpc", "temu", "Barbosa"),
        ("k1dof4hk", "temu", "JLAS"),
        ("k1docw56", "temu", "Atv"),
        ("k1dkfq5y", "temu", "Eron"),
        ("k1do5vfw", "aliexpress", "Vita"),
    }
    for x in lojas:
        assert x.url == (TEMU_CHAT if x.plataforma == "temu" else ALI_CHAT)
    assert [x.loja for x in robo.carregar_lojas(robo.LOJAS_PADRAO, "barbosa, K1DO5VFW")] == [
        "Barbosa", "Vita"]
    with pytest.raises(ValueError):
        robo.carregar_lojas(robo.LOJAS_PADRAO, "ninguem")


@pytest.mark.parametrize(
    ("url", "plataforma"),
    [
        (TEMU_CHAT + "?posn=PO-076-123", "temu"),        # marca lido no carregamento
        ("https://gsp.aliexpress.com/m_apps/im-chat/im#/window?sessionId=1", "aliexpress"),
        ("https://gsp.aliexpress.com/m_apps/im-chat/im#/conversation/1", "aliexpress"),
        ("http://br.seller.temu.com/chat.html", "temu"),
        ("https://br.seller.temu.com.evil.com/chat.html", "temu"),
        (TEMU_CHAT, "aliexpress"),
    ],
)
def test_validar_loja_recusa_url_perigosa(url, plataforma):
    with pytest.raises(ValueError):
        robo.validar_loja({"perfil_id": "k1abc", "plataforma": plataforma, "loja": "X", "url": url})


def test_carregar_lojas_recusa_perfil_repetido(tmp_path):
    item = {"perfil_id": "k1abc", "plataforma": "temu", "loja": "X", "url": TEMU_CHAT}
    arq = tmp_path / "lojas.json"
    arq.write_text(json.dumps({"lojas": [item, {**item, "loja": "Y"}]}))
    with pytest.raises(ValueError, match="repetido"):
        robo.carregar_lojas(arq)


def test_url_do_davinci_e_adspower():
    assert robo.conferir_url_davinci("http://127.0.0.1:8011/") == "http://127.0.0.1:8011"
    assert robo.conferir_url_davinci("https://app.hadken.com") == "https://app.hadken.com"
    with pytest.raises(ValueError):
        robo.conferir_url_davinci("http://app.hadken.com")     # token em texto claro
    assert robo.endereco_depuracao({"ws": {"selenium": "127.0.0.1:5555"}}) == "127.0.0.1:5555"
    assert robo.endereco_depuracao({"debug_port": "4444"}) == "127.0.0.1:4444"
    with pytest.raises(ValueError):
        robo.endereco_depuracao({})


def test_tipo_de_falha_e_resumo_sem_segredo():
    NoSuchWindowException = type("NoSuchWindowException", (Exception,), {})  # noqa: N806
    WebDriverException = type("WebDriverException", (Exception,), {})  # noqa: N806
    TimeoutException = type("TimeoutException", (Exception,), {})  # noqa: N806
    assert robo.tipo_de_falha(NoSuchWindowException("no such window")) == "aba"
    assert robo.tipo_de_falha(WebDriverException("disconnected: not connected to DevTools")) == (
        "sessao"
    )
    assert robo.tipo_de_falha(WebDriverException("chrome not reachable")) == "sessao"
    assert robo.tipo_de_falha(ConnectionRefusedError()) == "sessao"
    assert robo.tipo_de_falha(TimeoutException("timeout")) == "lenta"
    # erro de rede da PÁGINA não é o navegador caído
    assert robo.tipo_de_falha(WebDriverException("net::ERR_INTERNET_DISCONNECTED")) == "outra"
    assert robo.tipo_de_falha(ValueError("x")) == "outra"
    r = robo.resumo_erro(
        WebDriverException("falhou em wss://msgacs.m.aliexpress.com/accs/auth?token=SEGREDO\nx")
    )
    assert "SEGREDO" not in r and "wss://msgacs.m.aliexpress.com/accs/auth" in r


# ---------------------------------------------------------------------------
# O gancho JS de verdade, no Node
# ---------------------------------------------------------------------------

_HARNESS_JS = r"""
const fs = require('fs');
const [gancho, host, cenario] = [fs.readFileSync(process.argv[2], 'utf8'), process.argv[3],
  JSON.parse(process.argv[4])];
globalThis.window = globalThis;
globalThis.top = globalThis;
globalThis.location = { href: `https://${host}/pagina`, hostname: host };
globalThis.fetch = async (u) => new Response('{"success":true}', { status: 200 });
class XHR {
  constructor() { this.ls = {}; this.responseType = ''; }
  open(m, u) { this._u = u; }
  send() {
    setTimeout(() => {
      this.status = 200; this.responseText = '{"ret":["SUCCESS::ok"]}';
      this.responseURL = new URL(this._u, location.href).href;
      (this.ls.load || []).forEach((f) => f());
    }, 0);
  }
  addEventListener(t, f) { (this.ls[t] ||= []).push(f); }
}
globalThis.XMLHttpRequest = XHR;
class WS {
  constructor(u) { this.u = u; this.ls = {}; }
  addEventListener(t, f) { (this.ls[t] ||= []).push(f); }
  emit(d) { (this.ls.message || []).forEach((f) => f({ data: d })); }
}
globalThis.WebSocket = WS;
(0, eval)(gancho);
(0, eval)(gancho);          // registrado duas vezes: não pode copiar em dobro
(async () => {
  const lidos = [];
  for (const [como, alvo, extra] of cenario) {
    if (como === 'fetch') {
      const r = await fetch(alvo, extra || {});
      lidos.push((await r.json()).success);
    }
    if (como === 'xhr') { const x = new XMLHttpRequest(); x.open('POST', alvo); x.send('{}'); }
    if (como === 'ws') {
      const w = new WebSocket(alvo);
      for (const q of extra) {
        w.emit(q.b ? new Uint8Array(q.b).buffer : (q.blob ? new Blob([q.blob]) : q));
      }
    }
    if (como === 'json') JSON.parse(alvo);
  }
  await new Promise((r) => setTimeout(r, 50));
  const um = window.__teste.drenar(200, 8e6);
  console.log(JSON.stringify({
    eventos: um.eventos, depois: window.__teste.drenar(200, 8e6).eventos.length,
    erros: um.erros, lidos, href: um.href, vetadas: um.vetadas, jsonp: um.jsonp,
    enumeravel: Object.keys(window).includes('__teste'),
    nativo: [window.fetch, XMLHttpRequest.prototype.open, XMLHttpRequest.prototype.send,
      window.WebSocket, JSON.parse]
      .every((f) => Function.prototype.toString.call(f).includes('[native code]')),
  }));
})();
"""


def _rodar_gancho(tmp_path: Path, plataforma: str, host: str, cenario: list) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("sem node nesta máquina")
    (tmp_path / "gancho.js").write_text(robo.montar_gancho(plataforma, "__teste"))
    (tmp_path / "harness.js").write_text(_HARNESS_JS)
    saida = subprocess.run(  # noqa: S603 — node local, arquivos do próprio teste
        [node, str(tmp_path / "harness.js"), str(tmp_path / "gancho.js"), host,
         json.dumps(cenario)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(saida.stdout)


def test_gancho_temu_no_node(tmp_path):
    r = _rodar_gancho(tmp_path, "temu", "br.seller.temu.com", [
        ["fetch", "/api/plateau/conv/getConvList", {"method": "POST"}],
        ["fetch", "/api/phantom/xg/pfb/b"],
        ["fetch", "/api/plateau/conv/markRead", {"method": "POST"}],
        ["xhr", "/api/plateau/sync/message"],
        ["ws", "wss://br.seller.temu.com?ws-titan-request-sign=dee0ea73",
         [{"b": [1, 2, 3, 255]}, "texto", {"blob": "xyz"}]],
        ["ws", "wss://outro.com/x", ["nao"]],
        ["json", '{"payload":{"push_type":2,"push_data":{"seq_type":1}}}'],
        ["json", '{"qualquer":1}'],
    ])
    vistos = sorted((e["tipo"], robo.limpar_url(e["url"]), e["metodo"], e["corpo"])
                    for e in r["eventos"])
    assert vistos == sorted([
        ("http", "https://br.seller.temu.com/api/plateau/conv/getConvList", "POST",
         '{"success":true}'),
        ("http", "https://br.seller.temu.com/api/plateau/sync/message", "POST",
         '{"ret":["SUCCESS::ok"]}'),
        ("ws", "wss://br.seller.temu.com/", "binario", "AQID/w=="),      # base64
        ("ws", "wss://br.seller.temu.com/", "binario", "eHl6"),          # Blob
        ("ws", "wss://br.seller.temu.com/", "texto", "texto"),
        ("ws", "wss://br.seller.temu.com/", "json",
         '{"payload":{"push_type":2,"push_data":{"seq_type":1}}}'),
    ])
    for e in r["eventos"]:
        assert robo.preparar_evento("temu", e) is not None, e      # o Python aceita tudo
    assert r["depois"] == 0 and r["erros"] == 0
    assert r["lidos"] == [True, True, True]        # a página lê a própria resposta
    assert r["enumeravel"] is False and r["nativo"] is True
    # VIGIA: o markRead não foi copiado, mas foi CONTADO (só origem + caminho)
    assert r["vetadas"] == {"https://br.seller.temu.com/api/plateau/conv/markRead": 1}
    assert r["jsonp"] == 0


def test_gancho_aliexpress_no_node(tmp_path):
    r = _rodar_gancho(tmp_path, "aliexpress", "gsp.aliexpress.com", [
        ["xhr", "//seller-acs.aliexpress.com/h5/mtop.gsp.web.seller.js.sync/1.0/?sign=abc"],
        ["xhr", "//seller-acs.aliexpress.com/h5/mtop.gsp.im.use.web.seller.gettoken/1.0/"],
        ["xhr", "//seller-acs.aliexpress.com/h5/mtop.gsp.merchant.menu.get/1.0/"],
        ["ws", "wss://msgacs.m.aliexpress.com/accs/auth?token=SEGREDO", ['{"type":"DATA"}']],
        ["json", '{"push_data":1}'],        # o JSON.parse só é copiado na Temu
        ["xhr", "//seller-acs.aliexpress.com/h5/"
                "mtop.gsp.im.receiver.web.seller.imusermessage.putrangeread/1.0/?sign=x"],
        ["fetch", "https://seller-acs.aliexpress.com/h5/"
                  "mtop.gsp.im.receiver.web.seller.immessage.sendimmessage/1.0/"],
    ])
    assert sorted((e["tipo"], e["metodo"]) for e in r["eventos"]) == [
        ("http", "POST"), ("ws", "texto")]
    assert r["vetadas"] == {
        "https://seller-acs.aliexpress.com/h5/"
        "mtop.gsp.im.receiver.web.seller.imusermessage.putrangeread/1.0/": 1,
        "https://seller-acs.aliexpress.com/h5/"
        "mtop.gsp.im.receiver.web.seller.immessage.sendimmessage/1.0/": 1,
    }
    for e in r["eventos"]:
        ev = robo.preparar_evento("aliexpress", e)
        assert "SEGREDO" not in ev["url"] and "sign" not in ev["url"]


# ---------------------------------------------------------------------------
# O Leitor com um navegador falso
# ---------------------------------------------------------------------------


class NoSuchWindowException(Exception):  # noqa: N818 — o nome da exceção do Selenium
    pass


class _Troca:
    def __init__(self, drv):
        self.drv = drv

    def window(self, h):
        if h not in self.drv.abas:
            raise NoSuchWindowException("no such window")
        self.drv.atual = h
        self.drv.log.append(("switch", h))

    def new_window(self, _tipo):
        h = f"ROBO{len(self.drv.log)}"
        self.drv.abas[h] = "about:blank"
        self.drv.atual = h
        self.drv.log.append(("nova_aba", h))


class NavegadorFalso:
    """Imita só o que o Leitor usa. Um documento novo (get) recebe os ganchos
    registrados NAQUELA aba — como o addScriptToEvaluateOnNewDocument."""

    def __init__(self):
        self.abas = {"EQUIPE": "https://br.seller.temu.com/main/orders"}
        self.atual = "EQUIPE"
        self.log: list[tuple] = []
        self.registros: dict[str, list[str]] = {}
        self.no_documento: dict[str, set[str]] = {}
        self.filas: dict[str, list[dict]] = {}
        self.digitando = False
        self.vetadas: dict[str, int] = {}     # o que a VIGIA do documento contou
        self.switch_to = _Troca(self)

    @property
    def window_handles(self):
        return list(self.abas)

    @property
    def current_window_handle(self):
        if self.atual not in self.abas:
            raise NoSuchWindowException("no such window: target window already closed")
        return self.atual

    def execute_cdp_cmd(self, cmd, params):
        self.log.append(("cdp", cmd, self.current_window_handle))
        nome = re.search(r'const NOME = "([^"]+)"', params["source"]).group(1)
        self.registros.setdefault(self.atual, []).append(nome)

    def get(self, url):
        h = self.current_window_handle
        self.log.append(("get", h, url))
        self.abas[h] = url
        self.no_documento[h] = set(self.registros.get(h, []))
        self.filas[h] = []
        self.vetadas = {}

    def execute_script(self, js, *args):
        h = self.current_window_handle
        if "drenar" in js:
            if args[0] not in self.no_documento.get(h, set()):
                return None
            eventos, self.filas[h] = self.filas[h], []
            return {"eventos": eventos, "resta": 0, "perdidos": 0, "grandes": 0, "erros": 0,
                    "jsonp": 0, "vetadas": dict(self.vetadas), "href": self.abas[h]}
        if "hasOwnProperty" in js:
            return args[0] in self.no_documento.get(h, set())
        if "activeElement" in js:
            return self.digitando
        raise AssertionError(js)

    def close(self):
        self.log.append(("close", self.current_window_handle))
        del self.abas[self.atual]

    def quit(self):
        self.log.append(("quit",))

    # para o teste: a página "recebe" respostas na aba do robô
    def pagina_recebe(self, *eventos):
        for h in self.filas:
            if h != "EQUIPE":
                self.filas[h].extend(eventos)


class AdsPowerFalso:
    def __init__(self, aberto=True):
        self.aberto = aberto
        self.abriu = 0

    def ativo(self, _perfil):
        return {"status": "Active", "ws": {"selenium": "127.0.0.1:1"}, "webdriver": "/x"} if (
            self.aberto) else None

    def abrir(self, _perfil):
        self.abriu += 1
        self.aberto = True
        return {}


class DaVinciFalso:
    def __init__(self):
        self.posts: list[tuple[str, dict]] = []

    def post(self, caminho, corpo, timeout=60):
        self.posts.append((caminho, json.loads(json.dumps(corpo))))
        return {"ok": True, "gravadas": 1, "conversas": 1, "ignorados": 0}

    def pulsos(self):
        return [c["estado"] for p, c in self.posts if p.endswith("/robo/pulso")]

    def eventos(self):
        return [e for p, c in self.posts if p.endswith("/robo/eventos") for e in c["eventos"]]


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _leitor(tmp_path, *, seco=False, aberto=True, loja=None):
    drv = NavegadorFalso()
    ctx = robo.Contexto(
        adspower=AdsPowerFalso(aberto),
        davinci=None if seco else DaVinciFalso(),
        abrir_perfil=True,
        estado=robo.EstadoAbas(tmp_path / "abas.json"),
        criar_driver=lambda info: drv,
        agora=Relogio(),
    )
    loja = loja or robo.Loja("k1dkegpc", "temu", "Barbosa", TEMU_CHAT)
    return robo.Leitor(loja, ctx), drv, ctx


def _http(caminho="/api/plateau/conv/getConvList", corpo='{"success":true,"result":{}}'):
    return {"tipo": "http", "url": "https://br.seller.temu.com" + caminho, "metodo": "POST",
            "status": 200, "recebido_em": "2026-09-30T12:00:00Z", "corpo": corpo}


def test_leitor_gancho_antes_de_navegar_e_so_na_aba_propria(tmp_path):
    leitor, drv, ctx = _leitor(tmp_path)
    leitor.passo()
    robo_aba = leitor.handle
    assert robo_aba and robo_aba != "EQUIPE"
    # registrou o gancho NA aba do robô e só depois navegou nela
    i_cdp = drv.log.index(("cdp", "Page.addScriptToEvaluateOnNewDocument", robo_aba))
    i_get = drv.log.index(("get", robo_aba, TEMU_CHAT))
    assert i_cdp < i_get
    assert not [x for x in drv.log if x[0] in ("get", "cdp", "close") and "EQUIPE" in x]
    assert drv.abas["EQUIPE"] == "https://br.seller.temu.com/main/orders"
    assert json.loads((tmp_path / "abas.json").read_text()) == {"k1dkegpc": robo_aba}
    assert leitor.estado == "lendo"
    assert ctx.davinci.pulsos() == ["iniciando", "lendo"]

    # a página recebe: o chat vai, o resto não; a mesma resposta não vai 2x
    drv.pagina_recebe(_http(), _http("/api/phantom/xg/pfb/b"), _http())
    leitor.passo()
    enviados = ctx.davinci.eventos()
    assert [e["url"] for e in enviados] == [
        "https://br.seller.temu.com/api/plateau/conv/getConvList"]
    _, corpo = ctx.davinci.posts[-1]
    assert (corpo["perfil_id"], corpo["plataforma"], corpo["loja"]) == (
        "k1dkegpc", "temu", "Barbosa")
    assert leitor.conta["repetidos"] == 1 and leitor.conta["recusados"] == 1
    assert leitor.conta["gravadas"] == 1


def test_leitor_vigia_conversa_aberta_volta_para_a_lista(tmp_path):
    """Alguém abriu uma conversa na aba do robô: a página chamou enterConv e
    markRead. O robô conta (só a rota), avisa no pulso e, passado um minuto
    sem ninguém digitando, volta para a LISTA — conversa aberta marcaria lida
    cada mensagem nova do comprador."""
    leitor, drv, ctx = _leitor(tmp_path)
    leitor.passo()
    gets = len([x for x in drv.log if x[0] == "get"])
    drv.vetadas = {"https://br.seller.temu.com/api/plateau/conv/enterConv": 1,
                   "https://br.seller.temu.com/api/plateau/conv/markRead": 2}
    leitor.passo()
    assert leitor.estado == "erro" and "conversa aberta" in leitor.detalhe
    assert ctx.davinci.pulsos()[-1] == "erro"
    assert leitor.vetadas == {"conv/enterConv": 1, "conv/markRead": 2}
    leitor.passo()                         # a mesma contagem de novo não soma
    assert leitor.conta["vetadas"] == 3
    assert len([x for x in drv.log if x[0] == "get"]) == gets      # ainda não
    ctx.agora.t += robo.NAVEGACAO_MIN_S
    drv.digitando = True
    leitor.passo()
    assert len([x for x in drv.log if x[0] == "get"]) == gets      # digitando: espera
    drv.digitando = False
    leitor.passo()
    assert drv.log[-1] == ("get", leitor.handle, TEMU_CHAT)
    leitor.passo()
    assert leitor.estado == "lendo" and leitor.conversa_aberta_em is None
    assert "VETADAS 3" in leitor._resumo()


def test_leitor_reaproveita_a_aba_salva(tmp_path):
    leitor, drv, ctx = _leitor(tmp_path)
    drv.abas["ROBO_ANTIGA"] = "about:blank"
    ctx.estado.guardar("k1dkegpc", "ROBO_ANTIGA")
    leitor.passo()
    assert leitor.handle == "ROBO_ANTIGA"
    assert not [x for x in drv.log if x[0] == "nova_aba"]


def test_leitor_sessao_caida_espera_uma_pessoa(tmp_path):
    leitor, drv, ctx = _leitor(tmp_path)
    leitor.passo()
    drv.abas[leitor.handle] = "https://br.seller.temu.com/login.html?redirectUrl=%2Fchat.html"
    navegacoes = len([x for x in drv.log if x[0] == "get"])
    leitor.passo()
    assert leitor.estado == "lendo"       # ainda pode ser o vaivém do login automático
    ctx.agora.t += robo.LOGIN_CONFIRMA_S
    leitor.passo()
    assert leitor.estado == "sessao_caiu"
    assert ctx.davinci.pulsos()[-1] == "sessao_caiu"
    # não fica recarregando a tela de login a cada rodada
    ctx.agora.t += 60
    leitor.passo()
    assert len([x for x in drv.log if x[0] == "get"]) == navegacoes
    # passado o intervalo, confere se a sessão voltou — mas não com alguém digitando
    ctx.agora.t += robo.REABRIR_LOGIN_S
    drv.digitando = True
    leitor.passo()
    assert len([x for x in drv.log if x[0] == "get"]) == navegacoes
    drv.digitando = False
    leitor.passo()
    assert drv.log[-1] == ("get", leitor.handle, TEMU_CHAT)
    # o chat.html carregou, mas ainda sem resposta do chat: não pisca "lendo"
    leitor.passo()
    assert leitor.estado == "sessao_caiu"
    drv.pagina_recebe(_http())            # a sessão voltou: o chat respondeu
    leitor.passo()
    assert leitor.estado == "lendo"


def test_leitor_login_automatico_nao_pisca_sessao_caiu(tmp_path):
    """Visto no teste real (30/09): com o cookie valendo, a Temu passa pelo
    login.html e volta sozinha ao chat.html em ~8 s. Isso não é sessão caída."""
    leitor, drv, ctx = _leitor(tmp_path, aberto=False)
    leitor.passo()
    drv.abas[leitor.handle] = "https://br.seller.temu.com/login.html?redirectUrl=%2Fchat.html"
    leitor.passo()
    ctx.agora.t += 8
    drv.abas[leitor.handle] = TEMU_CHAT + "?refer_page_name=login&_x_sessn_id=abc"
    drv.pagina_recebe(_http())
    leitor.passo()
    assert "sessao_caiu" not in ctx.davinci.pulsos()
    assert leitor.estado == "lendo"
    # o identificador de sessão que a Temu pendura na URL não sai no pulso
    assert robo.limpar_url(drv.abas[leitor.handle]) == TEMU_CHAT + "?refer_page_name=login"


def test_leitor_captcha_pelo_corpo(tmp_path):
    leitor, drv, _ = _leitor(tmp_path)
    leitor.passo()
    drv.pagina_recebe(_http(corpo='{"success":false,"errorCode":54001}'))
    # a rota sem assinatura continua "ok": não pode apagar o sinal da outra
    drv.pagina_recebe(_http("/api/plateau/conv/needReplyCount"))
    leitor.passo()
    assert leitor.estado == "sessao_caiu"
    assert "captcha" in leitor.detalhe
    drv.pagina_recebe(_http())            # getConvList voltou a responder
    leitor.passo()
    assert leitor.estado == "lendo"


def test_leitor_recarga_periodica(tmp_path):
    leitor, drv, ctx = _leitor(tmp_path)
    leitor.passo()
    ctx.agora.t += 5 * 60
    drv.pagina_recebe(_http())
    leitor.passo()
    antes = len([x for x in drv.log if x[0] == "get"])
    ctx.agora.t += robo.RECARREGAR_S["temu"] * 1.2
    drv.pagina_recebe(_http(corpo='{"success":true,"n":2}'))
    leitor.passo()
    gets = [x for x in drv.log if x[0] == "get"]
    assert gets[antes:] == [("get", leitor.handle, "about:blank"),
                            ("get", leitor.handle, TEMU_CHAT)]
    assert leitor.estado == "lendo"


def test_leitor_reabre_a_aba_fechada(tmp_path):
    leitor, drv, _ = _leitor(tmp_path)
    leitor.passo()
    velha = leitor.handle
    del drv.abas[velha]                    # alguém fechou a aba do robô
    leitor.passo()
    nova = leitor.handle
    assert nova and nova != velha and nova in drv.abas
    assert ("cdp", "Page.addScriptToEvaluateOnNewDocument", nova) in drv.log
    assert drv.log[-1] == ("get", nova, TEMU_CHAT)
    leitor.passo()
    assert leitor.estado == "lendo"


def test_leitor_abre_perfil_fechado(tmp_path):
    leitor, _, ctx = _leitor(tmp_path, aberto=False)
    leitor.passo()
    assert ctx.adspower.abriu == 1 and leitor.estado == "lendo"


def test_leitor_sem_abrir_perfil(tmp_path):
    leitor, drv, ctx = _leitor(tmp_path, aberto=False)
    ctx.abrir_perfil = False
    leitor.passo()
    assert ctx.adspower.abriu == 0 and leitor.estado == "erro"
    assert ctx.davinci.pulsos() == ["iniciando", "erro"]
    assert not drv.log


def test_leitor_seco_nao_posta_nada(tmp_path):
    leitor, drv, ctx = _leitor(tmp_path, seco=True)
    leitor.passo()
    drv.pagina_recebe(_http())
    leitor.passo()
    assert leitor.conta["seco"] == 1 and not leitor.pendentes


def test_leitor_encerrar_fecha_so_a_aba_do_robo(tmp_path):
    leitor, drv, ctx = _leitor(tmp_path)
    leitor.passo()
    aba = leitor.handle
    drv.pagina_recebe(_http())
    leitor._encerrar()
    assert ("close", aba) in drv.log and "EQUIPE" in drv.abas
    assert drv.log[-1] == ("quit",)
    assert ctx.davinci.eventos()                  # o que estava na fila foi entregue
    assert json.loads((tmp_path / "abas.json").read_text()) == {}


def test_leitor_rede_fora_nao_martela(tmp_path):
    """Página de erro de rede: o navegador está vivo (não religa a sessão) e
    o robô tenta voltar ao chat no máximo 1x por minuto."""
    leitor, drv, ctx = _leitor(tmp_path)
    leitor.passo()
    drv.abas[leitor.handle] = "chrome-error://chromewebdata/"
    get_original = drv.get
    tentativas = []

    def get_sem_rede(url):
        if url != "about:blank":
            tentativas.append(url)
            raise type("WebDriverException", (Exception,), {})(
                "unknown error: net::ERR_INTERNET_DISCONNECTED")
        get_original(url)

    drv.get = get_sem_rede
    ctx.agora.t += robo.NAVEGACAO_MIN_S + 1
    leitor.passo()                      # tenta e falha
    assert tentativas == [TEMU_CHAT]
    assert leitor.estado == "erro" and leitor.drv is drv       # sessão mantida
    for _ in range(10):                 # nas rodadas seguintes, espera
        ctx.agora.t += robo.DRENAR_S
        leitor.passo()
    assert tentativas == [TEMU_CHAT]
    assert ("quit",) not in drv.log
    ctx.agora.t += robo.NAVEGACAO_MIN_S
    drv.get = get_original              # a rede voltou
    leitor.passo()
    leitor.passo()
    assert leitor.estado == "lendo"


def test_leitor_encerrar_na_ultima_aba_nao_fecha_o_navegador(tmp_path):
    leitor, drv, _ = _leitor(tmp_path)
    leitor.passo()
    del drv.abas["EQUIPE"]
    leitor._encerrar()
    assert not [x for x in drv.log if x[0] == "close"]
    assert drv.abas[leitor.handle] == "about:blank"


def test_cliente_davinci_http_de_verdade():
    """O POST sai com o token no cabeçalho; os erros viram o tipo certo e o
    422 do FastAPI NÃO ecoa o `input` (pode ser texto de comprador)."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    recebidos = []

    class Servidor(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            recebidos.append((self.path, self.headers.get("Authorization"), corpo))
            codigo, resposta = {
                "/api/atendimento/robo/eventos": (200, {"ok": True, "gravadas": 2}),
                "/r401": (401, {"detail": "x"}),
                "/r404": (404, {"detail": "Not Found"}),
                "/r422": (422, {"detail": [{"loc": ["body", "eventos", 0, "tipo"],
                                            "msg": "Input should be 'http' or 'ws'",
                                            "input": "TEXTO DO COMPRADOR"}]}),
                "/r500": (500, {}),
            }[self.path]
            dados = json.dumps(resposta).encode()
            self.send_response(codigo)
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Servidor)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cli = robo.DaVinci(f"http://127.0.0.1:{srv.server_port}", "tok-123")
        assert cli.post("/api/atendimento/robo/eventos", {"eventos": [{"corpo": "olá"}]}) == {
            "ok": True, "gravadas": 2}
        assert recebidos[0] == ("/api/atendimento/robo/eventos", "Bearer tok-123",
                                {"eventos": [{"corpo": "olá"}]})
        for caminho, status in (("/r401", 401), ("/r404", 404), ("/r422", 422)):
            with pytest.raises(robo.DaVinciRecusou) as erro:
                cli.post(caminho, {})
            assert erro.value.status == status
            assert "TEXTO DO COMPRADOR" not in str(erro.value)
            assert "tok-123" not in str(erro.value)
        assert "body.eventos.0.tipo" in str(erro.value)
        with pytest.raises(robo.DaVinciFora):
            cli.post("/r500", {})
    finally:
        srv.shutdown()
    with pytest.raises(robo.DaVinciFora):
        robo.DaVinci("http://127.0.0.1:9", "t").post("/x", {}, timeout=3)


def test_o_que_o_robo_manda_cabe_no_contrato(tmp_path):
    """Os corpos que o Leitor posta passam no schema da recepção (se ela já
    existe neste checkout — os dois lados foram feitos em paralelo)."""
    contrato = pytest.importorskip("app.schemas.atendimento_robo")
    leitor, drv, ctx = _leitor(tmp_path)
    leitor.passo()
    drv.pagina_recebe(
        _http(),
        {"tipo": "ws", "url": "wss://br.seller.temu.com/?ws-titan-request-sign=x",
         "metodo": "binario", "status": None, "recebido_em": "2026-09-30T12:00:00.123Z",
         "corpo": "AAoAZgAAAAAAAAAAAAAAAA=="},
        {"tipo": "ws", "url": "wss://br.seller.temu.com/", "metodo": "json", "status": None,
         "recebido_em": "2026-09-30T12:00:00.123Z", "corpo": '{"push_type":2,"push_data":{}}'},
    )
    leitor.passo()
    drv.abas[leitor.handle] = "https://br.seller.temu.com/login.html?redirectUrl=%2Fchat.html"
    leitor.passo()
    ctx.agora.t += robo.LOGIN_CONFIRMA_S
    leitor.passo()
    caminhos = {p for p, _ in ctx.davinci.posts}
    assert caminhos == {"/api/atendimento/robo/pulso", "/api/atendimento/robo/eventos"}
    for caminho, corpo in ctx.davinci.posts:
        modelo = contrato.PulsoIn if caminho.endswith("/pulso") else contrato.EventosIn
        modelo.model_validate(corpo)
    assert len(ctx.davinci.eventos()) == 3
    assert ctx.davinci.pulsos()[-1] == "sessao_caiu"
    pulso = [c for p, c in ctx.davinci.posts if p.endswith("/pulso")][-1]
    assert pulso["url"] == "https://br.seller.temu.com/login.html?redirectUrl=%2Fchat.html"


# ---------------------------------------------------------------------------
# Revisão de 30/09 (achados SEG-1..3, LOGICA-4..5)
# ---------------------------------------------------------------------------


def _lista_ae_corpo(conteudo: str, ret: str = "SUCCESS::调用成功") -> str:
    return json.dumps({
        "api": "mtop.gsp.im.use.web.seller.messagebox.querySessionList",
        "ret": [ret],
        "data": {"x": {"sessionViewDTOList": [{"sessionId": "s1", "sessionData": {
            "title": "John", "content": conteudo, "senderAccountType": "11"}}]}},
    })


def test_classificar_corpo_le_o_envelope_nao_o_texto_do_comprador():
    """O comprador escreve o que quiser: um texto que COMEÇA com o código de
    erro (ou tem ele entre aspas) dentro de uma resposta SUCCESS não é sessão
    caída. Só o envelope conta (ret[0] no AliExpress, errorCode na Temu)."""
    c = robo.classificar_corpo
    for texto in ("RGV587_ERROR", "SESSION_EXPIRED kkk", 'meu pedido "FAIL_SYS_USER_VALIDATE"',
                  "FAIL_SYS_SESSION_EXPIRED::x"):
        assert c("aliexpress", _lista_ae_corpo(texto)) == "ok", texto
        # templateData do js.sync: JSON dentro de string dentro de string
        modelo = json.dumps({"txt": texto})
        sync = json.dumps({"ret": ["SUCCESS::ok"], "data": {"syncDataValuesMap": {"im": [
            {"bizData": json.dumps({"body": {"templateData": modelo}})}]}}})
        assert c("aliexpress", sync) == "ok", texto
    temu = json.dumps({"success": True, "result": {"data": [{"message": {
        "content": '{"errorCode": 54001}'}}]}})
    assert c("temu", temu) == "ok"
    # O envelope de verdade continua valendo (inclusive como JSONP).
    assert c("aliexpress", "mtopjsonp3(" + _lista_ae_corpo("x", "RGV587_ERROR::SM") + ")") == (
        "verificacao")
    assert c("aliexpress", '{"ret":["FAIL_SYS_SESSION_EXPIRED::Sessão"],"data":{}}') == "login"
    assert c("temu", '{"success":false,"errorCode":"54001"}') == "verificacao"
    assert c("temu", "não é json") is None and c("aliexpress", "[1,2]") is None


def test_leitor_conversa_aberta_volta_para_a_lista_mesmo_com_sessao_caida(tmp_path):
    """Com a loja em "sessão caiu" (pelo corpo), alguém abre uma conversa na
    aba do robô: a volta à lista em 1 min vale do mesmo jeito."""
    loja = robo.Loja("k1do5vfw", "aliexpress", "Vita", ALI_CHAT)
    leitor, drv, ctx = _leitor(tmp_path, loja=loja)
    leitor.passo()
    url = ALI_H5 + "mtop.gsp.web.seller.js.sync/1.0/"
    drv.pagina_recebe({"tipo": "http", "url": url, "metodo": "POST", "status": 200,
                       "recebido_em": "2026-09-30T12:00:00Z",
                       "corpo": '{"ret":["RGV587_ERROR::SM::x"],"data":{}}'})
    leitor.passo()
    assert leitor.estado == "sessao_caiu"
    gets = len([x for x in drv.log if x[0] == "get"])
    drv.vetadas = {ALI_H5 + "mtop.gsp.im.use.web.seller.messagebox.putrangeread/1.0/": 3}
    leitor.passo()
    assert leitor.conversa_aberta_em is not None and "conversa aberta" in leitor.detalhe
    ctx.agora.t += robo.NAVEGACAO_MIN_S
    leitor.passo()
    assert len([x for x in drv.log if x[0] == "get"]) == gets + 2
    assert drv.log[-1] == ("get", leitor.handle, ALI_CHAT)


class DaVinciRoteiro(DaVinciFalso):
    """Responde conforme uma função do lote (para 5xx, 413, resposta torta)."""

    def __init__(self, resposta):
        super().__init__()
        self.resposta = resposta

    def post(self, caminho, corpo, timeout=60):
        if caminho.endswith("/robo/eventos"):
            r = self.resposta(corpo["eventos"])
            if isinstance(r, Exception):
                raise r
            self.posts.append((caminho, json.loads(json.dumps(corpo))))
            return r
        return super().post(caminho, corpo, timeout)


def _aceito(eventos):
    return {"ok": True, "gravadas": 0, "conversas": 0, "ignorados": 0, "erros": 0}


def test_leitor_lote_que_sempre_da_500_nao_trava_a_fila(tmp_path):
    """Um evento que o DaVinci nunca consegue gravar (5xx em toda tentativa)
    não pode segurar a fila da loja para sempre, com o pulso dizendo "lendo":
    depois de algumas tentativas, o robô manda evento a evento, descarta o que
    sempre falha, e enquanto o envio está parado o pulso diz "erro"."""
    def resposta(eventos):
        if any("VENENO" in e["corpo"] for e in eventos):
            return robo.DaVinciFora("HTTP 500", status=500)
        return _aceito(eventos)

    leitor, drv, ctx = _leitor(tmp_path)
    ctx.davinci = DaVinciRoteiro(resposta)
    leitor.passo()
    drv.pagina_recebe(_http(corpo='{"success":true,"x":"VENENO"}'))
    leitor.passo()
    for i in range(10):
        drv.pagina_recebe(_http(corpo=f'{{"success":true,"n":{i}}}'))
        ctx.agora.t += robo.ESPERA_ENVIO_FALHOU_S + 1
        leitor.passo()
    assert "erro" in ctx.davinci.pulsos()
    assert not leitor.pendentes
    assert leitor.conta["rejeitados"] == 1
    assert len(ctx.davinci.eventos()) == 10
    assert all("VENENO" not in e["corpo"] for e in ctx.davinci.eventos())
    ctx.agora.t += robo.PULSO_S
    leitor.passo()
    assert ctx.davinci.pulsos()[-1] == "lendo"


def test_leitor_so_da_o_lote_por_entregue_com_a_resposta_do_contrato(tmp_path):
    """200 com qualquer coisa (proxy, página de erro, `{}`) não é entrega."""
    respostas = [{}, {"ok": True}, _aceito([])]
    leitor, drv, ctx = _leitor(tmp_path)
    ctx.davinci = DaVinciRoteiro(lambda _e: respostas.pop(0))
    leitor.passo()
    drv.pagina_recebe(_http())
    leitor.passo()
    assert len(leitor.pendentes) == 1
    ctx.agora.t += robo.ESPERA_ENVIO_FALHOU_S + 1
    leitor.passo()
    assert len(leitor.pendentes) == 1
    ctx.agora.t += robo.ESPERA_ENVIO_FALHOU_S + 1
    leitor.passo()
    assert not leitor.pendentes and leitor.conta["enviados"] == 1


def test_leitor_413_parte_o_lote_em_vez_de_descartar(tmp_path):
    def resposta(eventos):
        if len(eventos) > 3:
            return robo.DaVinciRecusou(413, "corpo grande demais")
        return _aceito(eventos)

    leitor, drv, ctx = _leitor(tmp_path)
    ctx.davinci = DaVinciRoteiro(resposta)
    leitor.passo()
    drv.pagina_recebe(*[_http(corpo=f'{{"success":true,"n":{i}}}') for i in range(10)])
    leitor.passo()
    assert not leitor.pendentes and leitor.conta["rejeitados"] == 0
    assert [json.loads(e["corpo"])["n"] for e in ctx.davinci.eventos()] == list(range(10))


def test_montar_lotes_mede_o_json_que_vai_no_post():
    """O corpo vai como string JSON: aspas e barras escapadas contam. O js.sync
    do AliExpress tem JSON dentro de string dentro de string (1,3x)."""
    modelo = json.dumps({"txt": "Oi, qual o prazo?", "extra": {"k": "v"}})
    item = json.dumps({"header": {"type": 1}, "body": {"templateData": modelo}})
    corpo = json.dumps({"ret": ["SUCCESS::ok"], "data": {"syncDataValuesMap": {"im": [
        {"bizData": item}] * 400}}})
    url = ALI_H5 + "mtop.gsp.web.seller.js.sync/1.0/"
    lotes = robo.montar_lotes([_ev(corpo=corpo, url=url) for _ in range(200)])
    for lote in lotes:
        post = json.dumps({"perfil_id": "k1do5vfw", "plataforma": "aliexpress", "loja": "Vita",
                           "eventos": lote}, ensure_ascii=False).encode()
        assert len(post) <= robo.MAX_BYTES_LOTE


def test_cliente_davinci_nao_segue_redirecionamento():
    """Um 3xx (domínio, www, proxy, http) levaria o Bearer para outra origem —
    e o POST viraria GET, com o lote "entregue" sem ter chegado."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    chegou_em_b = []

    class B(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            chegou_em_b.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Length", "11")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        do_POST = do_GET  # noqa: N815

        def log_message(self, *a):
            pass

    srv_b = HTTPServer(("127.0.0.1", 0), B)

    class A(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{srv_b.server_port}/x")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv_a = HTTPServer(("127.0.0.1", 0), A)
    for s in (srv_a, srv_b):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        cli = robo.DaVinci(f"http://127.0.0.1:{srv_a.server_port}", "tok-redir")
        with pytest.raises(robo.DaVinciRecusou) as erro:
            cli.post("/api/atendimento/robo/eventos", {"eventos": []})
        assert erro.value.status == 302
        assert "tok-redir" not in str(erro.value)
        assert chegou_em_b == []
    finally:
        srv_a.shutdown()
        srv_b.shutdown()


def test_leitor_captcha_que_persiste_nao_pisca_lendo_na_recarga(tmp_path):
    """Com o captcha pendente, só as rotas ASSINADAS (getConvList, sync) caem
    no 54001. Na recarga, o needReplyCount (sem assinatura) responde primeiro:
    isso não prova que a sessão voltou."""
    leitor, drv, ctx = _leitor(tmp_path)
    leitor.passo()
    captcha = '{"success":false,"errorCode":54001}'
    drv.pagina_recebe(_http(corpo=captcha))
    leitor.passo()
    assert leitor.estado == "sessao_caiu"
    antes = len(ctx.davinci.pulsos())
    ctx.agora.t += robo.RECARREGAR_S["temu"] * 1.2
    leitor.passo()                                  # recarrega
    ctx.agora.t += 3
    drv.pagina_recebe(_http("/api/plateau/conv/needReplyCount"))
    leitor.passo()
    ctx.agora.t += 2
    drv.pagina_recebe(_http(corpo=captcha))
    leitor.passo()
    assert "lendo" not in ctx.davinci.pulsos()[antes:]
    assert leitor.estado == "sessao_caiu"
    # Resolvido o captcha, a rota assinada responde: volta a ler.
    ctx.agora.t += 2
    drv.pagina_recebe(_http(corpo='{"success":true,"result":{"data":[]}}'))
    leitor.passo()
    assert leitor.estado == "lendo"


# ---------------------------------------------------------------------------
# Perfil fechado por fora do robô: espera e desiste, nunca disputa o perfil
# ---------------------------------------------------------------------------


def test_espera_reabrir_primeira_vez_e_depois_da_espera():
    assert robo.espera_reabrir([], 1000.0) == 0.0
    # Fechou agora: espera o prazo inteiro antes de reabrir.
    assert robo.espera_reabrir([1000.0], 1000.0) == robo.ESPERA_FECHADO_FORA_S
    # Passado o prazo, pode reabrir.
    assert robo.espera_reabrir([1000.0], 1000.0 + robo.ESPERA_FECHADO_FORA_S + 1) == 0.0


def test_espera_reabrir_desiste_depois_de_varias_vezes():
    agora = 10_000.0
    tres = [agora - 3000, agora - 2000, agora - 10]
    assert robo.espera_reabrir(tres, agora) is None
    # Fechamentos antigos (fora da janela) não contam.
    antigos = [agora - robo.JANELA_FECHADO_FORA_S - 50] * 5
    assert robo.espera_reabrir(antigos, agora) == 0.0

