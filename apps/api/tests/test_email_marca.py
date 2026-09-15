"""services/email_marca.py — renderização dos padrões de e-mail (sem banco).

Eduardo (15/09/2026): "o envio automático de e-mail dos SAC tem que ser
padronizado com logo da marca, assinatura da empresa, site, logo do zap e o
zap". Aqui é só o serviço: formatação de fone/CNPJ/link wa.me, validação do
template (só expressões `{{ }}`), sandbox que NUNCA executa atributo
perigoso, assinatura que OMITE as linhas sem dado, texto puro sem tags,
escape sem duplo `&amp;`, imagens cid:/data:, teto de saída e o
From/Reply-To. Marca/Company/MarcaEmailPadrao são instanciadas em memória
(SQLAlchemy não precisa de sessão pra isso) — o router é coberto em
test_email_padroes_router.py.

Nada aqui manda e-mail nem lê a planilha real.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models import Company, Marca, MarcaEmailPadrao
from app.services.email_marca import (
    MAX_RENDER_CHARS,
    PLACEHOLDERS,
    PLACEHOLDERS_CONTEXTO,
    PLACEHOLDERS_MARCA,
    WHATSAPP_ICON,
    EmailRenderizado,
    TemplateInvalidoError,
    formatar_cnpj,
    formatar_fone,
    render_email,
    render_padrao,
    validar_template,
    variaveis_da_marca,
    variaveis_desconhecidas,
    whatsapp_link,
)

PNG_FAKE = b"\x89PNG\r\n\x1a\n" + b"logo-fake-" * 8
CNPJ_DIGITOS = "12345678000195"
CNPJ_FORMATADO = "12.345.678/0001-95"


def _marca(**kw: object) -> Marca:
    base: dict = {"nome": "Poofy", "slug": "poofy"}
    base.update(kw)
    return Marca(**base)


def _marca_completa(**kw: object) -> Marca:
    """Marca com tudo que a assinatura usa (fone/e-mail SAC, site, logo)."""
    return _marca(
        sac_fone="11983517003",
        sac_email="sac@poofy.com",
        site="https://poofy.com.br",
        logo_mime="image/png",
        logo=PNG_FAKE,
        **kw,
    )


def _empresa(**kw: object) -> Company:
    base: dict = {"razao_social": "Poofy Comércio LTDA", "apelido": "Poofy", "cnpj": CNPJ_DIGITOS}
    base.update(kw)
    return Company(**base)


def _render(marca: Marca, company: Company | None = None, **kw: object) -> EmailRenderizado:
    kw.setdefault("assunto", "{{ marca }} — atendimento")
    kw.setdefault("corpo", "Olá {{ cliente }},\nseu pedido {{ pedido }} está a caminho.")
    return render_email(marca=marca, company=company, **kw)  # type: ignore[arg-type]


def _assinatura_texto(text: str) -> str:
    """Parte do texto puro depois do separador `--` da assinatura."""
    assert "\n--\n" in text, text
    return text.split("\n--\n", 1)[1]


# ================================================================ formatação


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("11983517003", "(11) 98351-7003"),
        ("1133334444", "(11) 3333-4444"),
        # Com DDI: tira o 55 e formata igual.
        ("5511983517003", "(11) 98351-7003"),
        ("551133334444", "(11) 3333-4444"),
        # Aceita máscara/espaços (só os dígitos contam).
        ("(11) 98351-7003", "(11) 98351-7003"),
        ("+55 11 98351-7003", "(11) 98351-7003"),
        # Tamanho fora do padrão fica como está (só dígitos).
        ("123", "123"),
        ("551198", "551198"),
        ("", ""),
        (None, ""),
    ],
)
def test_formatar_fone(bruto, esperado):
    assert formatar_fone(bruto) == esperado


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("11983517003", "https://wa.me/5511983517003"),
        # Já com DDI não duplica o 55.
        ("5511983517003", "https://wa.me/5511983517003"),
        ("(11) 98351-7003", "https://wa.me/5511983517003"),
        ("", ""),
        (None, ""),
        ("abc", ""),
    ],
)
def test_whatsapp_link(bruto, esperado):
    assert whatsapp_link(bruto) == esperado


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        (CNPJ_DIGITOS, CNPJ_FORMATADO),
        (CNPJ_FORMATADO, CNPJ_FORMATADO),
        # Não é CNPJ (14 dígitos): devolve o que veio.
        ("123", "123"),
        ("", ""),
        (None, ""),
    ],
)
def test_formatar_cnpj(bruto, esperado):
    assert formatar_cnpj(bruto) == esperado


def test_variaveis_da_marca_com_e_sem_empresa():
    v = variaveis_da_marca(_marca_completa(), _empresa())
    assert v == {
        "marca": "Poofy",
        "empresa": "Poofy Comércio LTDA",
        "cnpj": CNPJ_FORMATADO,
        "site": "https://poofy.com.br",
        "whatsapp": "(11) 98351-7003",
        "whatsapp_link": "https://wa.me/5511983517003",
        "email_sac": "sac@poofy.com",
    }
    # Só as chaves documentadas (PLACEHOLDERS_MARCA) — a tela lista essas.
    assert set(v) == set(PLACEHOLDERS_MARCA)

    # Sem empresa/site/fone/e-mail: tudo "" — nunca None (vai pro template).
    seca = variaveis_da_marca(_marca(), None)
    assert seca == {**dict.fromkeys(PLACEHOLDERS_MARCA, ""), "marca": "Poofy"}


def test_placeholders_batem_com_a_lista_do_web():
    """A tela (lib/redesSociais.ts EMAIL_PLACEHOLDERS) lista os placeholders
    pro operador copiar — tem que ser a MESMA lista do serviço, senão a tela
    oferece `{{ x }}` que renderiza vazio (ou esconde um que existe)."""
    assert PLACEHOLDERS == PLACEHOLDERS_MARCA + PLACEHOLDERS_CONTEXTO
    ts = Path(__file__).resolve().parents[3] / "apps" / "web" / "lib" / "redesSociais.ts"
    if not ts.exists():
        pytest.skip("web fora do checkout")
    fonte = ts.read_text(encoding="utf-8")
    # O primeiro "]" depois do nome é o do tipo (`{...}[]`): corta no "= [".
    bloco = fonte.split("EMAIL_PLACEHOLDERS", 1)[1].split("= [", 1)[1].split("\n]", 1)[0]
    chaves = re.findall(r"chave:\s*'([a-z_]+)'", bloco)
    assert chaves == list(PLACEHOLDERS)


# ============================================================ validar_template


@pytest.mark.parametrize("filtro", ["upper", "lower", "title", "trim", "capitalize"])
def test_validar_template_aceita_expressoes_e_filtros_basicos(filtro):
    validar_template(f"Olá {{{{ cliente | {filtro} }}}}, da {{{{ marca }}}}")
    r = _render(_marca(nome="poofy"), corpo=f"{{{{ marca | {filtro} }}}}", incluir_assinatura=False)
    assert r.text.strip() != ""
    assert "{{" not in r.text


def test_validar_template_texto_puro_e_vazio_passam():
    validar_template("Sem placeholder nenhum.")
    validar_template("")
    validar_template(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("template", "codigo"),
    [
        # Bloco (for/if/macro) não passa — laço/bomba de saída fora.
        ("{% for i in range(3) %}x{% endfor %}", "template_bloco_nao_permitido"),
        ("{% if marca %}x{% endif %}", "template_bloco_nao_permitido"),
        # Não compila: expressão aberta, filtro vazio.
        ("Olá {{ marca", "template_invalido"),
        ("{{ marca | upper | }}", "template_invalido"),
        # Whitelist da AST (revisão 15/09): só {{ placeholder }} e filtros da
        # lista — filtro estranho, placeholder inexistente, aritmética,
        # chamada, atributo e constante são barrados ANTES de rodar.
        ("{{ marca | nao_existe }}", "filtro_nao_permitido"),
        ("{{ marca | replace('a', 'b') }}", "filtro_nao_permitido"),
        ("{{ marcaa }}", "placeholder_desconhecido"),
        ("{{ 'x' * 10**9 }}", "template_bloco_nao_permitido"),
        ("{{ 2 ** (2 ** 31) }}", "template_bloco_nao_permitido"),
        ("{{ range(10**9) }}", "template_bloco_nao_permitido"),
        ("{{ marca.__class__ }}", "template_bloco_nao_permitido"),
        ("{{ ''.__class__.__mro__ }}", "template_bloco_nao_permitido"),
        ("{{ cliente if cliente else marca }}", "template_bloco_nao_permitido"),
    ],
)
def test_validar_template_rejeita_bloco_e_sintaxe(template, codigo):
    with pytest.raises(TemplateInvalidoError) as exc:
        validar_template(template)
    assert str(exc.value).split(":", 1)[0] == codigo

    # render_email levanta o MESMO erro (o router traduz pra 422).
    with pytest.raises(TemplateInvalidoError) as exc2:
        _render(_marca(), corpo=template)
    assert str(exc2.value).split(":", 1)[0] == codigo


# ==================================================================== sandbox


def test_sandbox_nunca_executa_atributo_perigoso():
    """Acesso a atributo/índice/chamada não chega nem a compilar: a whitelist
    da AST barra antes (o SandboxedEnvironment seria a segunda linha de
    defesa). Vale pro envio e pra prévia."""
    for corpo in ("{{ ''.__class__.__mro__ }}", "x{{ marca.__class__ }}y", "{{ marca['nome'] }}"):
        with pytest.raises(TemplateInvalidoError) as exc:
            _render(_marca(), corpo=corpo)
        assert str(exc.value).startswith("template_bloco_nao_permitido"), corpo
        with pytest.raises(TemplateInvalidoError):
            _render(_marca(), corpo=corpo, incluir_assinatura=False, previa=True)


def test_range_e_aritmetica_sao_barrados_antes_de_rodar():
    """`range(...)`, `'x' * 10**9` e `2 ** (2 ** 31)` nunca executam (o
    sandbox não intercepta aritmética e alocaria/travaria o event loop) —
    a whitelist rejeita a chamada/operação na compilação."""
    for corpo in ("{{ range(100000) | list }}", "{{ 'x' * 10**9 }}", "{{ 2 ** (2 ** 31) }}"):
        with pytest.raises(TemplateInvalidoError) as exc:
            _render(_marca(), corpo=corpo)
        # `| list` cai primeiro no filtro fora da lista; o resto na operação.
        assert str(exc.value).split(":")[0] in (
            "template_bloco_nao_permitido", "filtro_nao_permitido"
        ), corpo


def test_filtros_permitidos_funcionam():
    r = _render(
        _marca(), None, corpo="{{ cliente | title }} / {{ marca | upper }} / {{ pedido | trim }}",
        variaveis={"cliente": "maria silva", "pedido": "  123 "}, incluir_assinatura=False,
    )
    assert r.text == "Maria Silva / POOFY / 123"


# ============================================================== render_email


def test_render_monta_html_com_logo_assinatura_e_texto_puro():
    """Caminho feliz: logo no topo (cid:logo), corpo com os placeholders,
    assinatura completa (razão social + CNPJ, site, WhatsApp com ícone
    cid:whatsapp e link wa.me, e-mail SAC) e a versão texto equivalente.
    inline_images só traz o que o HTML referencia."""
    r = _render(
        _marca_completa(),
        _empresa(),
        variaveis={"cliente": "Maria", "pedido": "2000123456789"},
    )

    assert r.assunto == "Poofy — atendimento"
    assert r.from_email is None
    assert r.from_name == "Poofy"
    assert r.reply_to == ("sac@poofy.com", "Poofy")
    assert r.avisos == []
    assert r.variaveis_desconhecidas == []

    html = r.html
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Poofy — atendimento</title>" in html
    assert 'max-width:600px;background:#ffffff' in html
    assert '<img src="cid:logo" alt="Poofy"' in html
    assert "Olá Maria,<br>\nseu pedido 2000123456789 está a caminho." in html
    assert "Poofy Comércio LTDA &middot; CNPJ 12.345.678/0001-95" in html
    assert '<a href="https://poofy.com.br"' in html
    assert '<img src="cid:whatsapp" width="28" height="28" alt="WhatsApp"' in html
    assert '<a href="https://wa.me/5511983517003"' in html
    assert "WhatsApp (11) 98351-7003</a>" in html
    assert '<a href="mailto:sac@poofy.com"' in html
    assert "data:" not in html

    assert set(r.inline_images) == {"logo", "whatsapp"}
    assert r.inline_images["logo"] == ("image/png", PNG_FAKE)
    mime_zap, bytes_zap = r.inline_images["whatsapp"]
    assert mime_zap == "image/png"
    assert bytes_zap == WHATSAPP_ICON.read_bytes()

    assert r.text == (
        "Olá Maria,\nseu pedido 2000123456789 está a caminho."
        "\n\n--\nPoofy"
        "\nPoofy Comércio LTDA · CNPJ 12.345.678/0001-95"
        "\nhttps://poofy.com.br"
        "\nWhatsApp (11) 98351-7003 — https://wa.me/5511983517003"
        "\nsac@poofy.com"
    )
    assert "<" not in r.text


def test_icone_do_whatsapp_existe_e_e_png():
    """app/email_templates/whatsapp.png (150x150, gerado do SVG) — sem ele a
    assinatura sai sem o ícone e o inline 'whatsapp' some em silêncio."""
    assert WHATSAPP_ICON.exists()
    raw = WHATSAPP_ICON.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(raw) < 20_000


def test_assinatura_omite_as_linhas_sem_dado():
    """Marca sem empresa/site/fone/e-mail: a assinatura é só o nome — nada de
    "CNPJ", "WhatsApp" vazio, ícone sem número ou mailto: sem endereço. Os
    avisos dizem o que falta (a prévia mostra)."""
    r = _render(_marca(), None)

    assert set(r.inline_images) == set()
    assert "cid:" not in r.html
    assert "CNPJ" not in r.html
    assert "wa.me" not in r.html
    assert "WhatsApp" not in r.html
    assert "mailto:" not in r.html
    assert "<a " not in r.html
    assert '>Poofy</div>' in r.html
    assert _assinatura_texto(r.text) == "Poofy"
    assert r.reply_to is None
    assert r.avisos == [
        "marca sem empresa vinculada: assinatura sai sem razão social/CNPJ",
        "marca sem Fone/WhatsApp: assinatura sai sem WhatsApp",
        "marca sem e-mail SAC: sem Reply-To e sem e-mail na assinatura",
        "marca sem site",
        "marca sem logo",
    ]

    # Só o fone: linha do WhatsApp (com ícone) e mais nada.
    so_fone = _render(_marca(sac_fone="11983517003"), None)
    assert set(so_fone.inline_images) == {"whatsapp"}
    assert "wa.me/5511983517003" in so_fone.html
    assert "mailto:" not in so_fone.html
    assert _assinatura_texto(so_fone.text) == (
        "Poofy\nWhatsApp (11) 98351-7003 — https://wa.me/5511983517003"
    )
    assert "marca sem Fone/WhatsApp: assinatura sai sem WhatsApp" not in so_fone.avisos

    # Empresa sem CNPJ: razão social sem o " · CNPJ".
    sem_cnpj = _render(_marca(), _empresa(cnpj=None))
    assert "Poofy Comércio LTDA</div>" in sem_cnpj.html
    assert "CNPJ" not in sem_cnpj.html
    assert _assinatura_texto(sem_cnpj.text) == "Poofy\nPoofy Comércio LTDA"


def test_sem_assinatura_e_sem_logo():
    """incluir_assinatura=False: nem bloco de assinatura nem ícone nem aviso
    sobre ela; incluir_logo=False: sem cid:logo mesmo com logo na marca."""
    r = _render(_marca_completa(), _empresa(), incluir_assinatura=False, incluir_logo=False)

    assert r.inline_images == {}
    assert "cid:" not in r.html
    assert "border-top" not in r.html
    assert "--" not in r.text
    assert r.text == "Olá ,\nseu pedido  está a caminho."
    assert r.avisos == []
    # Reply-To continua no SAC (é do envelope, não da assinatura).
    assert r.reply_to == ("sac@poofy.com", "Poofy")

    # Sem logo na marca + incluir_logo=True → só o aviso; sem assinatura.
    sem_logo = _render(_marca(), None, incluir_assinatura=False, incluir_logo=True)
    assert sem_logo.inline_images == {}
    assert sem_logo.avisos == ["marca sem logo"]


def test_corpo_e_escapado_e_nl2br_sem_duplo_escape():
    """O corpo é TEXTO: `<b>` do operador vira &lt;b&gt; no HTML (e fica
    literal no texto puro); quebra vira <br>; o `&` do nome da marca sai
    como &amp; UMA vez (escapa depois de renderizar, não antes)."""
    r = _render(
        _marca(nome="Poofy & Cia"),
        None,
        corpo="<b>negrito</b> & tal\r\nlinha 2\n{{ marca }} agradece",
        incluir_assinatura=True,
    )

    assert (
        "&lt;b&gt;negrito&lt;/b&gt; &amp; tal<br>\nlinha 2<br>\nPoofy &amp; Cia agradece" in r.html
    )
    assert "<b>negrito</b>" not in r.html
    assert "&amp;amp;" not in r.html
    # Assinatura também escapa o nome uma vez só (e o alt do logo idem).
    assert '>Poofy &amp; Cia</div>' in r.html
    # (o Jinja normaliza CRLF pra "\n" no texto — o HTML acima já mostra isso)
    assert r.text.startswith("<b>negrito</b> & tal\nlinha 2\nPoofy & Cia agradece")
    assert _assinatura_texto(r.text) == "Poofy & Cia"

    com_logo = _render(_marca_completa(nome="Poofy & Cia"), None, corpo="x")
    assert 'alt="Poofy &amp; Cia"' in com_logo.html


def test_placeholder_desconhecido_e_422_no_envio_e_na_previa():
    """`{{ marcaa }}` (erro de digitação) não passa nem no envio nem na
    prévia: a whitelist aponta o nome (placeholder_desconhecido: marcaa) —
    o operador corrige antes de salvar. O helper variaveis_desconhecidas
    continua listando os nomes fora da lista."""
    for previa in (False, True):
        with pytest.raises(TemplateInvalidoError) as exc:
            _render(
                _marca(), None, corpo="Olá {{ marcaa }}!", incluir_assinatura=False, previa=previa
            )
        assert str(exc.value) == "placeholder_desconhecido: marcaa"
    with pytest.raises(TemplateInvalidoError) as exc:
        _render(_marca(), None, assunto="{{ assuntoo }} {{ marca }}", corpo="x")
    assert str(exc.value) == "placeholder_desconhecido: assuntoo"

    # Os placeholders conhecidos (inclusive os de contexto ausentes) não
    # contam como desconhecidos.
    assert variaveis_desconhecidas("{{ marca }} {{ cliente }} {{ pedido }}") == []
    assert variaveis_desconhecidas("{{ x }}", "{{ y | upper }}", "{{ marca") == ["x", "y"]


def test_variaveis_de_contexto_viram_texto():
    """cliente/pedido/produto/plataforma vêm do caller: ausentes = "";
    None = ""; qualquer outra coisa vira str (nada de objeto no sandbox).
    Chave que NÃO é de contexto (site, whatsapp_link, email_sac, marca…) é
    ignorada — ninguém troca o href da assinatura por `javascript:`."""
    r = _render(
        _marca(),
        None,
        corpo="[{{ cliente }}][{{ pedido }}][{{ produto }}][{{ plataforma }}][{{ site }}]",
        variaveis={
            "cliente": "Maria", "pedido": None, "produto": 42,
            "site": "javascript:alert(1)", "whatsapp_link": "javascript:alert(2)",
        },
        incluir_assinatura=True,
    )
    assert r.text.startswith("[Maria][][42][][]")
    assert "javascript:" not in r.html

    # Um valor de contexto com HTML também sai escapado no HTML.
    x = _render(
        _marca(), None, corpo="{{ cliente }}", variaveis={"cliente": "<i>M</i>"},
        incluir_assinatura=False,
    )
    assert "&lt;i&gt;M&lt;/i&gt;" in x.html
    assert "<i>M</i>" not in x.html


def test_assunto_e_aparado():
    """Assunto renderizado sai sem espaços/quebras nas pontas (CRLF do
    textarea não vai pro header)."""
    r = _render(_marca(), None, assunto="  {{ marca }} — atendimento \r\n", corpo="x")
    assert r.assunto == "Poofy — atendimento"
    assert "<title>Poofy — atendimento</title>" in r.html


def test_teto_de_saida():
    """Template curto + placeholder de contexto (cortado em 500 chars)
    repetido passa do teto (MAX_RENDER_CHARS) → template_saida_muito_grande.
    Texto literal no limite passa."""
    assert MAX_RENDER_CHARS == 20_000
    grande = "x" * 600  # vira 500 no _variaveis
    repeticoes = MAX_RENDER_CHARS // 500 + 1  # 41 × 500 = 20.500 > teto

    with pytest.raises(TemplateInvalidoError) as exc:
        _render(
            _marca(), None, corpo="{{ cliente }}" * repeticoes, variaveis={"cliente": grande},
            incluir_assinatura=False,
        )
    assert str(exc.value) == "template_saida_muito_grande"

    # O assunto tem o mesmo teto.
    with pytest.raises(TemplateInvalidoError):
        _render(
            _marca(), None, assunto="{{ cliente }}" * repeticoes, corpo="x",
            variaveis={"cliente": grande},
        )

    no_limite = _render(
        _marca(), None, corpo="y" * MAX_RENDER_CHARS, incluir_assinatura=False,
    )
    assert len(no_limite.text) == MAX_RENDER_CHARS


def test_imagens_data_para_previa():
    """imagens="data": cid: vira data:<mime>;base64 (iframe sandbox da prévia
    não resolve cid: e uma URL da API sairia sem cookie). inline_images
    continua listando o que foi embutido."""
    r = _render(_marca_completa(), None, imagens="data")

    assert "cid:" not in r.html
    assert "data:image/png;base64," in r.html
    from base64 import b64encode

    assert f'src="data:image/png;base64,{b64encode(PNG_FAKE).decode()}"' in r.html
    assert set(r.inline_images) == {"logo", "whatsapp"}


def test_from_e_reply_to():
    """From explícito só quando o operador preencheu remetente_email
    (precisa estar validado no Mailjet); o nome cai pro da marca e, sem
    marca?, pro EMAIL_FROM_NAME. Reply-To = SAC da marca."""
    r = _render(
        _marca_completa(), None, remetente_nome="  Equipe Poofy ", remetente_email=" sac@poofy.com "
    )
    assert r.from_name == "Equipe Poofy"
    assert r.from_email == "sac@poofy.com"
    assert r.reply_to == ("sac@poofy.com", "Poofy")

    vazio = _render(_marca(nome="Locagil"), None, remetente_nome="   ", remetente_email="")
    assert vazio.from_name == "Locagil"
    assert vazio.from_email is None
    assert vazio.reply_to is None


def test_render_padrao_usa_os_campos_do_padrao():
    padrao = MarcaEmailPadrao(
        contexto="sac",
        nome="Resposta padrão SAC",
        remetente_nome="SAC Poofy",
        remetente_email=None,
        assunto="{{ marca }}: pedido {{ pedido }}",
        corpo="Olá {{ cliente }}",
        incluir_logo=False,
        incluir_assinatura=True,
    )
    r = render_padrao(padrao, _marca_completa(), _empresa(), {"cliente": "Maria", "pedido": "1"})

    assert r.assunto == "Poofy: pedido 1"
    assert r.text.startswith("Olá Maria\n\n--\nPoofy\n")
    assert r.from_name == "SAC Poofy"
    assert r.from_email is None
    assert set(r.inline_images) == {"whatsapp"}  # incluir_logo=False
    assert "cid:logo" not in r.html
    assert "cid:whatsapp" in r.html
    # render_padrao é envio (não prévia): sem DebugUndefined.
    assert r.variaveis_desconhecidas == []

    em_data = render_padrao(padrao, _marca_completa(), None, imagens="data")
    assert "cid:" not in em_data.html
    assert "data:image/png;base64," in em_data.html
