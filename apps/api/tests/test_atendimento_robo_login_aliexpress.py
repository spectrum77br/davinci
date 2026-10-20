"""Login AliExpress: executa o JavaScript real com DOM e dados fictícios."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import atendimento_robo_adspower as robo

_JSDOM = Path(__file__).resolve().parents[2] / "web/node_modules/jsdom"
_CHAT_URL = "https://gsp.aliexpress.com/m_apps/im-chat/im#/window"
_LOGIN_URL = "https://login.aliexpress.com/user/seller/login"
_LOGIN = """
<form id="login-form" class="next-form next-medium">
  <input id="loginName" name="loginName" type="text" placeholder="Email" autocomplete="off">
  <input id="password" name="password" type="password">
  <input id="remember" type="checkbox"><label for="remember">Lembrar conta</label>
  <button class="gsp-button-login loginButton gsp-btn" type="submit" disabled
    data-action="submit">Assinar em</button>
  <button class="gsp-button-register" type="button" data-action="register">Registo</button>
</form>
"""

_HARNESS = r"""
const {JSDOM} = require(process.argv[2]);
const fs = require('node:fs');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM(p.html, {url: p.url, runScripts: 'outside-only'});
const w = dom.window, d = w.document;
const usuario = 'usuario.ficticio@example.com', senha = 'senha-ficticia-ali-!';
const clicks = [], eventos = [], campos = new Set();
const rects = new Map(); let next = 0;
function rect(e) {
  if (!rects.has(e)) rects.set(e, {left: ++next * 100, top: 10, width: 50, height: 30});
  return rects.get(e);
}
w.HTMLElement.prototype.getBoundingClientRect = function () {return rect(this);};
w.HTMLElement.prototype.getClientRects = function () {
  return this.hidden || this.style.display === 'none' ? [] : [rect(this)];
};
Object.defineProperty(w.HTMLElement.prototype, 'innerText', {get() {return this.textContent;}});
d.elementFromPoint = (x, y) => {
  for (const [e, r] of rects) if (r.left + r.width / 2 === x) return e;
  return null;
};
d.hasFocus = () => !!p.focus;
if (p.focus) {
  const input = d.querySelector('#loginName');
  input.value = 'uma pessoa digitando'; input.focus();
}
d.addEventListener('click', e => {
  const a = e.target.closest('[data-action]');
  if (!a) return;
  e.preventDefault(); clicks.push(a.dataset.action);
});
d.addEventListener('submit', e => e.preventDefault());
for (const tipo of ['input', 'change']) d.addEventListener(tipo, e => {
  campos.add(e.target.id);
  eventos.push({tipo, id: e.target.id, valorCorreto: e.target.value ===
    (e.target.type === 'password' ? senha : usuario)});
  if (!p.manterDesabilitado && d.querySelector('#loginName').value === usuario &&
      d.querySelector('#password').value === senha)
    d.querySelector('.gsp-button-login').disabled = false;
});
const checksAntes = Array.from(d.querySelectorAll('input[type="checkbox"]')).map(e => e.checked);
const executar = w.Function(p.source);
const resultados = p.modos.map(modo => {
  if (modo === 'confirmar' && p.alterarAntesConfirmar)
    d.querySelector('#loginName').value = 'alterado por uma pessoa';
  return executar(modo, usuario, senha);
});
const checksDepois = Array.from(d.querySelectorAll('input[type="checkbox"]')).map(e => e.checked);
process.stdout.write(JSON.stringify({resultados, clicks, eventos, campos: [...campos],
  checksAntes, checksDepois}));
"""


def _rodar(tmp_path, html=_LOGIN, modos=("enviar", "confirmar"), *, url=None, focus=False,
           alterar_antes_confirmar=False, manter_desabilitado=False):
    node = shutil.which("node")
    if not node or not _JSDOM.exists():
        pytest.skip("testes DOM precisam do node e jsdom das dependências web")
    harness = tmp_path / "formulario-aliexpress.cjs"
    harness.write_text(_HARNESS)
    entrada = {
        "source": robo._ALI_LOGIN_JS,
        "html": html,
        "url": url or _LOGIN_URL,
        "modos": modos,
        "focus": focus,
        "alterarAntesConfirmar": alterar_antes_confirmar,
        "manterDesabilitado": manter_desabilitado,
    }
    saida = subprocess.run(  # noqa: S603 — harness local e dados de teste
        [node, str(harness), str(_JSDOM)], input=json.dumps(entrada),
        text=True, capture_output=True, timeout=15, check=True,
    )
    assert "senha-ficticia" not in saida.stdout
    assert "usuario.ficticio@example.com" not in saida.stdout
    return json.loads(saida.stdout)


def test_formulario_login_preenche_somente_usuario_e_senha(tmp_path):
    r = _rodar(tmp_path, modos=("inspecionar", "enviar", "confirmar"))
    assert r["resultados"] == ["pronto", "preenchido", "enviado"]
    assert r["clicks"] == ["submit"]
    assert r["campos"] == ["loginName", "password"]
    assert len(r["eventos"]) == 4 and all(e["valorCorreto"] for e in r["eventos"])
    assert r["checksAntes"] == r["checksDepois"] == [False]


@pytest.mark.parametrize("url", [
    "http://login.aliexpress.com/user/seller/login",
    "https://login.aliexpress.com.evil.test/user/seller/login",
    "https://outra.aliexpress.com/user/seller/login",
    "https://login.aliexpress.com:8443/user/seller/login",
    "https://login.aliexpress.com/qualquer-login",
    "https://gsp.aliexpress.com/m_apps/im-chat/im#/window",
])
def test_url_nao_autorizada_nao_recebe_credenciais(tmp_path, url):
    r = _rodar(tmp_path, url=url)
    assert r["resultados"][0] not in ("pronto", "enviado")
    assert not r["campos"] and not r["clicks"]


@pytest.mark.parametrize("fragmento", ["/window", "verification"])
def test_hash_nao_reconhecido_no_login_nao_submete_formulario(tmp_path, fragmento):
    r = _rodar(tmp_path, url=_LOGIN_URL + "#" + fragmento)
    assert r["resultados"][0] not in ("pronto", "enviado")
    assert not r["campos"] and not r["clicks"]


@pytest.mark.parametrize("desafio", [
    '<iframe src="https://login.aliexpress.com/captcha"></iframe>',
    '<input autocomplete="one-time-code">',
    '<input name="verificationCode">',
    '<input placeholder="Código de verificação">',
])
def test_desafio_nao_submete_senha_mesmo_com_password_visivel(tmp_path, desafio):
    r = _rodar(tmp_path, _LOGIN.replace('<input id="password"', desafio + '<input id="password"'))
    assert r["resultados"] == ["verificacao", "verificacao"]
    assert not r["campos"] and not r["clicks"]


def test_digitacao_manual_impede_preenchimento_e_envio(tmp_path):
    r = _rodar(tmp_path, focus=True)
    assert r["resultados"] == ["digitando", "digitando"]
    assert not r["campos"] and not r["clicks"]


def test_senha_recusada_so_retorna_estado(tmp_path):
    r = _rodar(tmp_path, _LOGIN + '<p>Senha incorreta</p>', modos=("resultado",))
    assert r["resultados"] == ["credenciais_recusadas"]
    assert not r["campos"] and not r["clicks"]


def test_checkbox_marcado_pela_pessoa_permanece_marcado(tmp_path):
    html = _LOGIN.replace('id="remember" type="checkbox"', 'id="remember" type="checkbox" checked')
    r = _rodar(tmp_path, html)
    assert r["resultados"] == ["preenchido", "enviado"]
    assert r["checksAntes"] == r["checksDepois"] == [True]
    assert "remember" not in r["campos"]


def test_preenchimento_nao_envia_enquanto_botao_ainda_desabilitado(tmp_path):
    r = _rodar(tmp_path, manter_desabilitado=True)
    assert r["resultados"][0] == "preenchido"
    assert r["resultados"][1] != "enviado"
    assert not r["clicks"]


def test_confirmar_recusa_formulario_alterado_depois_do_preenchimento(tmp_path):
    r = _rodar(tmp_path, alterar_antes_confirmar=True)
    assert r["resultados"][0] == "preenchido"
    assert r["resultados"][1] != "enviado"
    assert not r["clicks"]


def test_confirmar_sozinho_nao_envia_formulario_vazio(tmp_path):
    r = _rodar(tmp_path, modos=("confirmar",))
    assert r["resultados"][0] != "enviado"
    assert not r["clicks"] and not r["campos"]


def test_cadastro_nova_senha_nao_recebe_credenciais(tmp_path):
    html = _LOGIN.replace('id="password" name="password"',
                          'id="password" name="password" autocomplete="new-password"')
    r = _rodar(tmp_path, html)
    assert r["resultados"] == ["formulario_desconhecido", "formulario_desconhecido"]
    assert not r["clicks"] and not r["campos"]


def test_limpa_apenas_rota_chat_herdada_e_preserva_return_url(tmp_path):
    query = "?return_url=https%3A%2F%2Fgsp.aliexpress.com%2Fim%23%2Fwindow&lang=pt_BR"
    url = _LOGIN_URL + query + "#/window"
    assert robo.login_aliexpress_sem_rota_chat(url, _CHAT_URL) == _LOGIN_URL + query


@pytest.mark.parametrize("url", [
    _LOGIN_URL,
    _LOGIN_URL + "#/",
    _LOGIN_URL + "#verification",
    "https://login.aliexpress.com/_____tmd_____/punish#/window",
    "https://login.aliexpress.com.evil.test/user/seller/login#/window",
    "http://login.aliexpress.com/user/seller/login#/window",
    _CHAT_URL,
    "about:blank",
    None,
])
def test_nao_limpa_rota_que_nao_seja_chat_herdado_no_login_conhecido(url):
    assert robo.login_aliexpress_sem_rota_chat(url, _CHAT_URL) is None


def test_hash_nao_vem_do_chat_configurado_nao_e_removido():
    assert robo.login_aliexpress_sem_rota_chat(
        _LOGIN_URL + "#/window", "https://gsp.aliexpress.com/m_apps/im-chat/im#/outra"
    ) is None
