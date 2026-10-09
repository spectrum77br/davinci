//! Código de verificação, senha e link de acesso: mascarados NO MAC, antes do DaVinci.
//!
//! O mesmo que `apps/api/app/services/mail_atendimento/codigos.py` (D8 + as
//! críticas de 08/10) — as listas e as regras são as mesmas, e o mesmo texto
//! sai igual dos dois lados: os casos de `tests/dados/protecao-casos.json`
//! rodam aqui (`casos_iguais_ao_davinci`) e no teste Python
//! (`test_mail_atendimento_puro.py`). O código mascarado aqui ainda faz o
//! e-mail ser "de segurança" lá (nunca vira conversa).
//!
//! 1. em TODO e-mail, o link que dá ACESSO (login, senha, confirmação, token
//!    na URL, valor opaco longo) vira "[link de acesso removido]" — e, no
//!    e-mail que FALA de senha, código ou entrada ("entrar", "acesse sua
//!    conta", "sign in", "reset"…), TODOS os links (o link curto não tem cara
//!    de acesso);
//! 2. o VALOR da senha escrita ("Senha gerada: Kx81mq2z", "Password for
//!    login: X", "sua senha Kx81mq2z", "Tu clave temporal es X") vira "•";
//! 3. se o e-mail FALA de código ou de acesso, o CÓDIGO SOLTO vira "•" do
//!    mesmo tamanho: o FORTE sempre (4 a 8 dígitos, "AB12CD", "G-482913",
//!    "482 913", "48 29 13", "4829 1300", "123-456"); o FRACO ("k7x9q2",
//!    número com cara de ano) só perto (até 4 pedaços) de uma palavra de
//!    acesso ("código", "code", "token", "PIN", "senha", "clave"…). Nunca é
//!    código: o número de um maior ("123.456.789-09", "11 98765 4321"), o
//!    valor ("R$ 1500", "1500,00", "15%"), a data ("08/10/2026"), o "#1234",
//!    o telefone "(11) 3456-7890", o que vem depois de "pedido", "CEP",
//!    "CPF", "nota"… e antes de uma unidade ("5000 mAh"). O número do pedido
//!    (16+ dígitos) não é tocado.
//!
//! O `regex` do Rust não tem lookaround: as bordas de palavra (o `(?<![\w-])`
//! do Python) são feitas à mão, por "pedaços" de letras/números/_/-/•.

use regex::Regex;
use std::sync::OnceLock;

pub const LINK_REMOVIDO: &str = "[link de acesso removido]";
const BOLINHA: char = '•';

/// Sem acento, minúsculo (comparado com o texto plano).
const GATILHOS: &[&str] = &[
	"codigo de verificacao",
	"codigo de seguranca",
	"codigo de acesso",
	"codigo de confirmacao",
	"codigo de login",
	"codigo para entrar",
	"codigo para acessar",
	"codigo de autenticacao",
	"codigo de uso unico",
	"seu codigo",
	"o codigo e",
	"codigo:",
	"senha temporaria",
	"senha de uso unico",
	"verification code",
	"security code",
	"login code",
	"confirmation code",
	"access code",
	"sign-in code",
	"signin code",
	"one-time",
	"one time password",
	"otp",
	"2fa",
	"two-factor",
	"authentication code",
	"your code",
	"codigo otp",
	"token de acesso",
	// senha escrita no e-mail
	"nova senha",
	"senha de acesso",
	"senha provisoria",
	"senha gerada",
	"senha:",
	"password:",
	// espanhol
	"codigo de verificacion",
	"codigo de seguridad",
	"codigo de acceso",
	"codigo de confirmacion",
	"codigo de inicio de sesion",
	"tu codigo",
	"token de acceso",
	"contrasena",
	"clave temporal",
	"clave de acceso",
];

/// O CÓDIGO de acesso — o `GATILHOS_CODIGO_DE_ACESSO` do Python.
const GATILHOS_CODIGO_DE_ACESSO: &[&str] = &[
	"codigo de verificacao",
	"codigo de seguranca",
	"codigo de acesso",
	"codigo de confirmacao",
	"codigo de login",
	"codigo para entrar",
	"codigo para acessar",
	"codigo de autenticacao",
	"codigo de uso unico",
	"codigo otp",
	"senha temporaria",
	"senha de uso unico",
	"verification code",
	"security code",
	"login code",
	"authentication code",
	"confirmation code",
	"access code",
	"sign-in code",
	"signin code",
	"one-time password",
	"one time password",
	"one-time code",
	"otp",
	"2fa",
	"two-factor",
	"two-step",
	"dois fatores",
	"duas etapas",
	"token de acesso",
	"codigo de verificacion",
	"codigo de seguridad",
	"codigo de acceso",
	"codigo de confirmacion",
	"codigo de inicio de sesion",
	"token de acceso",
	"verificacion en dos pasos",
	// crítica pré-subida 2 de 08/10
	"chave de seguranca",
	"numero de verificacao",
	"numero de verificacion",
	"verification number",
	"contrasena de un solo uso",
	"clave dinamica",
];

/// O e-mail de SEGURANÇA (senha, novo acesso, confirmar e-mail, link de
/// entrada) — o resto do `GATILHOS_SEGURANCA` do Python (que começa com os
/// `GATILHOS_CODIGO_DE_ACESSO`): com um destes, TODOS os links do e-mail saem
/// (o servidor ainda o põe em "segurança").
const GATILHOS_SEGURANCA: &[&str] = &[
	"redefinir senha",
	"redefinir sua senha",
	"redefinicao de senha",
	"redefina sua senha",
	"recuperar senha",
	"recuperar sua senha",
	"recuperacao de senha",
	"recupere sua senha",
	"alterar senha",
	"alterar sua senha",
	"alteracao de senha",
	"senha alterada",
	"senha foi alterada",
	"sua nova senha",
	"nova senha",
	"senha de acesso",
	"senha provisoria",
	"senha gerada",
	"criar nova senha",
	"troca de senha",
	"trocar sua senha",
	"esqueceu sua senha",
	"esqueceu a senha",
	"esqueci minha senha",
	"reset your password",
	"reset password",
	"password reset",
	"change your password",
	"password changed",
	"password was changed",
	"forgot your password",
	"new password",
	"temporary password",
	// a senha escrita em frase (crítica pré-subida 2 de 08/10)
	"senha redefinida",
	"senha foi redefinida",
	"senha foi gerada",
	"senha foi criada",
	"password has been reset",
	"password was reset",
	"password has been changed",
	"your password is",
	"contrasena restablecida",
	"novo acesso",
	"novo login",
	"login detectado",
	"acesso detectado",
	"novo dispositivo",
	"dispositivo novo",
	"tentativa de login",
	"tentativa de acesso",
	"new sign-in",
	"new sign in",
	"new login",
	"sign-in attempt",
	"login attempt",
	"new device",
	"confirme seu e-mail",
	"confirme seu email",
	"confirmar seu e-mail",
	"confirmar seu email",
	"confirmacao de e-mail",
	"confirmacao de email",
	"verifique seu e-mail",
	"verifique seu email",
	"verificar seu e-mail",
	"verificar seu email",
	"verify your email",
	"verify your e-mail",
	"confirm your email",
	"confirm your e-mail",
	"email verification",
	"ative sua conta",
	"ativar sua conta",
	"ativacao da conta",
	"ativacao de conta",
	"activate your account",
	"link de acesso",
	"link magico",
	"magic link",
	"link de login",
	"login link",
	"sign-in link",
	"desbloquear sua conta",
	"unlock your account",
	"link para entrar",
	"link para acessar",
	"restablecer contrasena",
	"restablecer tu contrasena",
	"cambiar tu contrasena",
	"cambio de contrasena",
	"nueva contrasena",
	"olvidaste tu contrasena",
	"contrasena temporal",
	"clave temporal",
	"clave de acceso",
	"inicio de sesion",
	"iniciar sesion",
	"nuevo dispositivo",
	"verifica tu correo",
	"verificar tu correo",
	"confirma tu correo",
	"confirmar tu correo",
	"activa tu cuenta",
	"enlace de acceso",
	"enlace para entrar",
	"enlace de inicio de sesion",
];
/// Palavra que, sozinha (palavra inteira), faz o e-mail "falar de senha".
const PALAVRAS_DE_SENHA: &[&str] = &["senha", "password", "passcode", "contrasena"];
/// A palavra (pedaço inteiro) que diz "código/segredo".
const PALAVRAS_DE_CODIGO: &[&str] = &[
	"codigo",
	"codigos",
	"code",
	"codes",
	"token",
	"pin",
	"clave",
	"senha",
	"password",
	"contrasena",
	"passcode",
	"otp",
];
/// "código"/"code" que NÃO é de acesso: o que vem logo DEPOIS…
const NEUTROS_DEPOIS: &[&[&str]] = &[
	&["de", "rastreio"],
	&["de", "rastreamento"],
	&["do", "rastreio"],
	&["do", "rastreamento"],
	&["de", "rastreo"],
	&["de", "seguimiento"],
	&["do", "produto"],
	&["dos", "produtos"],
	&["del", "producto"],
	&["do", "anuncio"],
	&["do", "item"],
	&["do", "pedido"],
	&["del", "pedido"],
	&["de", "barras"],
	&["da", "nota"],
	&["do", "cupom"],
	&["de", "cupom"],
	&["de", "desconto"],
	&["de", "envio"],
	&["de", "postagem"],
	&["do", "objeto"],
	&["de", "objeto"],
	&["postal"],
	&["fiscal"],
	&["sku"],
	&["ean"],
	&["ncm"],
	&["cfop"],
	&["promocional"],
];
/// …ou logo ANTES ("tracking code", "postal code").
const NEUTROS_ANTES: &[&str] = &[
	"tracking", "postal", "zip", "product", "promo", "coupon", "discount", "qr", "bar", "hs", "country",
	"area", "source",
];
/// O número logo DEPOIS destes pedaços não é código (pedido, CEP, nota…).
const NAO_E_CODIGO_DEPOIS_DE: &[&str] = &[
	"cep", "cpf", "cnpj", "pedido", "pedidos", "order", "nf", "nfe", "nota", "fiscal", "rastreio",
	"rastreamento", "tracking", "sku", "ean", "anuncio", "item", "protocolo", "telefone", "tel", "fone",
	"celular", "pacote", "pergunta", "reclamacao", "devolucao", "chamado", "serie", "serial", "imei", "modelo",
	"model", "invoice", "shipment", "danfe",
];
/// O TELEFONE só logo antes ("liberar o novo celular é 7 3 1 8 2 0" é código).
const TELEFONES: &[&str] = &["telefone", "tel", "fone", "celular"];
/// Entre um destes e o número podem vir até 3 pedaços "de enchimento" ("pedido
/// número 48291375", "o rastreio, o número 48291375", "Your order number is 12345678").
const ENCHIMENTO_ANTES: &[&str] = &[
	"e", "eh", "de", "do", "da", "n", "no", "nº", "nr", "num", "numero", "number", "is", "es", "o", "a", "the",
	"seu", "sua", "meu", "minha", "your", "my", "tu", "mi", "su",
];
/// …nem o que vem logo ANTES de uma unidade ("5000 mAh", "1500 reais").
const NAO_E_CODIGO_ANTES_DE: &[&str] = &[
	"reais", "real", "centavos", "dolares", "dollars", "usd", "brl", "mah", "gb", "mb", "tb", "kb", "mp",
	"hz", "ghz", "mhz", "w", "v", "kg", "g", "mm", "cm", "m", "km", "ml", "dias", "horas", "minutos",
	"unidades", "un", "pecas", "itens", "parcelas", "pontos", "anos", "meses",
];
/// Fala de ENTRAR na conta (frase inteira): "entrar em contato" não conta.
const PALAVRAS_DE_ENTRADA: &[&str] = &[
	"entrar",
	"acessar",
	"acesse sua conta",
	"acesse a sua conta",
	"acesse a conta",
	"access your account",
	"login",
	"log in",
	"log-in",
	"logon",
	"sign in",
	"sign-in",
	"signin",
	"iniciar sesion",
	"inicia sesion",
	"ingresa",
	"ingresar",
	"reset",
	// crítica pré-subida 2 de 08/10 (o link mágico sem "entrar")
	"log into",
	"logged in",
	"accede",
	"acceder",
	"accede a tu cuenta",
	"logado",
	"logada",
	"ja logado",
	"continue to your account",
];
const ENTRAR_EM_CONTATO: &[&str] = &["entrar em contato", "entrar em contacto"];
/// CÓDIGO ÚNICO sem a palavra "código": o FORTE de 5+ com uma destas a até
/// `PERTO_OTP` pedaços ("digite este número 731 604", "Enter 604 381 … to
/// approve") é mascarado.
const PALAVRAS_DE_OTP: &[&str] = &[
	"verificacao", "verificacion", "verification", "verify", "verifique", "verifica", "verificar", "confirme",
	"confirmar", "confirm", "confirma", "confirmacao", "confirmacion", "confirmation", "identidade", "identidad",
	"identity", "digite", "insira", "informe", "enter", "ingresa", "ingrese", "introduce", "introduzca", "aprovar",
	"aprove", "approve", "autorizar", "autorize", "autoriza", "authorize", "expira", "expiram", "expires", "expire",
	"vence", "caduca", "valido", "valida", "valid", "compartilhe", "share", "compartas", "comparta",
];
/// …e estas só bem perto (até `PERTO`): "digite o número 615 029", "informe a chave 804 117".
const PALAVRAS_DE_OTP_PERTO: &[&str] = &["numero", "number", "chave", "key"];
const PERTO_OTP: usize = 10;
/// O LINK COM PRAZO ("Click here … (expires in 15 minutes)"): todos os links saem.
const PALAVRAS_DE_LINK: &[&str] = &["link", "links", "enlace", "botao", "button", "clique", "click", "toque", "tap"];
const PALAVRAS_DE_PRAZO: &[&str] = &[
	"expira", "expiram", "expirar", "expires", "expire", "caduca", "vence", "valido", "valida", "valid", "minutos",
	"minuto", "minutes", "minute",
];
const PERTO_LINK: usize = 12;
/// As palavras no meio que dizem que o valor é OUTRA coisa ("senha do pedido: 2000…").
/// "A senha do celular é 1234" é senha: o celular, o telefone e o WhatsApp não estão aqui.
const NAO_E_SENHA_NO_MEIO: &[&str] =
	&["pedido", "pedidos", "order", "rastreio", "rastreamento", "cpf", "cnpj", "cep", "nota", "nf", "valor", "total", "preco", "protocolo"];
/// A senha escrita em FRASE (o `TERMOS_DE_SENHA` do Python): a de cara forte
/// até `PERTO_SENHA` palavras depois do termo; com o termo no assunto, a de
/// cara forte logo depois de um conector; a de cara comum depois de "para",
/// "ficou", "como"… com o termo antes.
const TERMOS_DE_SENHA: &[&str] = &["senha", "senhas", "password", "passwords", "passcode", "contrasena", "clave"];
const CONECTORES_DE_SENHA: &[&str] = &["e", "eh", "is", "es", "para", "pra", "to", "como", "ficou", "fica", "sera", "quedo"];
const CONECTORES_DE_VALOR: &[&str] = &["para", "pra", "to", "como", "ficou", "fica", "sera", "quedo"];
const SINAIS_DE_SENHA: &[&str] = &[":", "=", "-", "–", "—", "→", "->", "=>"];
const SIMBOLOS_DE_SENHA: &[char] = &['!', '#', '$', '%', '&', '*', '@', '+', '?', '=', '~', '^'];
const PERTO_SENHA: usize = 8;
const ABRE_A_PALAVRA: &[char] = &['<', '(', '"', '\'', '«', '['];
const FIM_DA_PALAVRA: &[char] = &['.', ',', ';', ')', '!', '?', '"', '\'', '»', ':', '>', ']'];
const FIM_DO_VALOR: &[char] = &['.', ')', '!', '?', '"', '\'', '»', ':', '>'];
const ABRE_O_VALOR: &[char] = &['<', '(', '"', '\'', '«'];
/// Até 4 pedaços entre a palavra de acesso e o código FRACO.
const PERTO: usize = 4;

/// O pedaço do link (sem acento, minúsculo) que COMEÇA com um destes é de acesso…
const PREFIXOS_LINK_DE_ACESSO: &[&str] = &[
	"reset", "password", "passwd", "redefin", "recuper", "verify", "verific", "confirm", "token", "magic", "login",
	"logon", "signin", "unlock", "desbloq", "validat", "activat", "authent", "authoriz", "oauth", "onetime", "session",
	"sessao", "invite", "convite", "ativac", "ativar",
];
/// …ou É um destes ("acessorios" não é "acesso").
const PEDACOS_LINK_DE_ACESSO: &[&str] =
	&["senha", "auth", "otp", "2fa", "mfa", "sso", "acesso", "access", "valida", "validar", "sign"];
/// Parâmetro com nome de segredo (o valor nunca aparece).
const PARAMETROS_SECRETOS: &[&str] = &[
	"token", "code", "codigo", "key", "chave", "auth", "sig", "signature", "hash", "otp", "session", "sid", "ticket",
	"secret", "pass", "pwd", "jwt", "access", "t", "k",
];

/// As classes das regex, ESCRITAS À MÃO e iguais às do Python (o `\b`, o `\w`
/// e o `\s` dos dois motores não são iguais: "²" é letra num e não no outro).
/// O espaço (o White_Space do Unicode) e a letra de palavra (latim, número, _).
const ESPACO: &str =
	" \t\n\r\u{b}\u{c}\u{85}\u{a0}\u{1680}\u{2000}-\u{200a}\u{2028}\u{2029}\u{202f}\u{205f}\u{3000}";
const LETRA: &str = "0-9A-Za-z_\u{c0}-\u{24f}";
// No "(?i:…)", o "i" é escrito "[iİı]": o Python casa o "i" com "İ" e "ı", o
// Rust não (o "s"/"ſ" e o "k"/"K" casam igual nos dois).

/// "Senha gerada: X", "Password for login: X", "Tu clave temporal es X",
/// "Token: X", "senha do painel da Nuvemshop, agora é X", "Senha (temporária): X",
/// "Senha → X" (até 6 palavras no meio). Os grupos: 1 a borda, 2 o termo, 3 as
/// palavras, 4 o separador, 5 o valor.
fn re_senha() -> &'static Regex {
	static RE: OnceLock<Regex> = OnceLock::new();
	RE.get_or_init(|| {
		Regex::new(&format!(
			r"(^|[^{L}])((?i:senha|password|passcode|contrase[nñ]a|clave|token))((?:(?:[{E}]*[,(][{E}]*|[{E}]+)[{L}]+\)?){{0,6}}?)([{E}]*(?:→|->|=>)[{E}]*|[{E}]*[:=][{E}]*|[{E}]+[-–—][{E}]+|[{E}]+(?i:é|e|eh|[iİı]s|es)(?:[{E}]*[:=][{E}]*|[{E}]+))([^{E},;]{{1,64}})",
			L = LETRA,
			E = ESPACO
		))
		.expect("regex fixa")
	})
}

/// "sua senha Kx81mq2z", "Sua senha provisória Kx81mq2z" (sem separador).
fn re_senha_solta() -> &'static Regex {
	static RE: OnceLock<Regex> = OnceLock::new();
	RE.get_or_init(|| {
		Regex::new(&format!(
			r"(^|[^{L}])((?i:senha|password|contrase[nñ]a|clave))((?:[{E}]+(?i:prov[iİı]s[oó]r[iİı]a|tempor[aá]r[iİı]a|temporal|temporary|nova|nueva|new|gerada|[iİı]n[iİı]c[iİı]al|atual))?)([{E}]+)([^{E},;]{{4,64}})",
			L = LETRA,
			E = ESPACO
		))
		.expect("regex fixa")
	})
}

/// "Use o código … para entrar", "code … to log in" (no texto plano).
fn re_codigo_para_entrar() -> &'static Regex {
	static RE: OnceLock<Regex> = OnceLock::new();
	RE.get_or_init(|| {
		Regex::new(&format!(
			r"(?:^|[^{L}])(?:codigo|code)(?:[{E}]+[^{E}]+){{0,3}}[{E}]+(?:para|to)[{E}]+(?:entrar|acessar|logar|fazer[{E}]+login|iniciar[{E}]+sessao|iniciar[{E}]+sesion|log[{E}]+in|sign[{E}]+in|login)(?:$|[^{L}])",
			L = LETRA,
			E = ESPACO
		))
		.expect("regex fixa")
	})
}

fn re_url() -> &'static Regex {
	static RE: OnceLock<Regex> = OnceLock::new();
	RE.get_or_init(|| {
		Regex::new(&format!(r#"(?i:https?://|www\.)[^{E}<>"'()\[\]{{}}]+"#, E = ESPACO)).expect("regex fixa")
	})
}

/// Valor opaco longo (no caminho ou num parâmetro): pode ser um token.
fn opaco(v: &str) -> bool {
	v.chars().count() >= 24 && v.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.' | '%' | '=' | '+'))
}

/// Sem acento e minúsculo (o `_plano` do Python, para o que aparece em e-mail).
#[must_use]
pub fn plano(texto: &str) -> String {
	texto
		.chars()
		.flat_map(char::to_lowercase)
		// O acento solto (U+0300–U+036F) some, como no Python.
		.filter(|c| !('\u{300}'..='\u{36f}').contains(c))
		.map(|c| match c {
			'á' | 'à' | 'â' | 'ã' | 'ä' | 'å' => 'a',
			'é' | 'è' | 'ê' | 'ë' => 'e',
			'í' | 'ì' | 'î' | 'ï' => 'i',
			'ó' | 'ò' | 'ô' | 'õ' | 'ö' => 'o',
			'ú' | 'ù' | 'û' | 'ü' => 'u',
			'ç' => 'c',
			'ñ' => 'n',
			'ý' | 'ÿ' => 'y',
			outro => outro,
		})
		.collect()
}

/// Letra ou número (o `isalnum` do Python: o acento solto U+0300–U+036F não é
/// letra lá, mesmo o que o Unicode chama de "alfabético", como o U+0345).
fn alfanumerico(c: char) -> bool {
	c.is_alphanumeric() && !('\u{300}'..='\u{36f}').contains(&c)
}

fn letra_de_palavra(c: char) -> bool {
	alfanumerico(c) || c == '_'
}

/// O gatilho está no texto plano? Os curtos ("otp", "2fa") só como palavra inteira.
fn tem(plano: &str, gatilho: &str) -> bool {
	if gatilho.chars().count() > 4 {
		return plano.contains(gatilho);
	}
	tem_palavra(plano, gatilho)
}

/// A palavra (ou frase) inteira está no texto plano (o `\b` do Python, à mão).
fn tem_palavra(plano: &str, palavra: &str) -> bool {
	let mut de = 0;
	while let Some(p) = plano[de..].find(palavra) {
		let ini = de + p;
		let fim = ini + palavra.len();
		let antes = plano[..ini].chars().next_back();
		let depois = plano[fim..].chars().next();
		if !antes.is_some_and(letra_de_palavra) && !depois.is_some_and(letra_de_palavra) {
			return true;
		}
		de = ini + palavra.chars().next().map_or(1, char::len_utf8);
	}
	false
}

/// O texto sem as URLs (o que está DENTRO do link não é palavra nem código do e-mail).
fn sem_links(texto: &str) -> String {
	re_url().replace_all(texto, " ").into_owned()
}

// ── Os pedaços do texto e os códigos soltos ───────────────────────────────

/// Um pedaço do texto: letras/números/_/-/• juntos (a "palavra").
struct Pedaco<'a> {
	ini: usize,
	fim: usize,
	texto: &'a str,
	plano: String,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tipo {
	Forte,
	Fraco,
	Mascarado,
}

struct Codigo {
	tipo: Tipo,
	primeiro: usize,
	ultimo: usize,
	/// O que vira "•" (posições em bytes no texto).
	trechos: Vec<(usize, usize)>,
}

fn de_pedaco(c: char) -> bool {
	letra_de_palavra(c) || c == '-' || c == BOLINHA
}

fn pedacos(texto: &str) -> Vec<Pedaco<'_>> {
	let mut saida = Vec::new();
	let mut ini: Option<usize> = None;
	for (i, c) in texto.char_indices() {
		match (de_pedaco(c), ini) {
			(true, None) => ini = Some(i),
			(false, Some(a)) => {
				saida.push(Pedaco { ini: a, fim: i, texto: &texto[a..i], plano: plano(&texto[a..i]) });
				ini = None;
			},
			_ => {},
		}
	}
	if let Some(a) = ini {
		saida.push(Pedaco { ini: a, fim: texto.len(), texto: &texto[a..], plano: plano(&texto[a..]) });
	}
	saida
}

fn digitos(t: &str) -> bool {
	!t.is_empty() && t.chars().all(|c| c.is_ascii_digit())
}

fn bolinhas(t: &str) -> bool {
	!t.is_empty() && t.chars().all(|c| c == BOLINHA)
}

fn forma_partida(forma: &[usize]) -> bool {
	matches!(forma, [3, 3] | [2, 2, 2] | [4, 4])
}

/// "482 913", "48 29 13", "4829 1300" ou um dígito por vez ("4 8 2 9 1 3").
fn forma_de_codigo(forma: &[usize]) -> bool {
	forma_partida(forma) || ((4..=8).contains(&forma.len()) && forma.iter().all(|n| *n == 1))
}

/// "G-482913" / "G-••••••" → (onde começa o número em bytes, é bolinha?).
fn prefixo(t: &str) -> Option<(usize, bool)> {
	let (letras, resto) = t.split_once('-')?;
	let n = resto.chars().count();
	let ok = (1..=3).contains(&letras.len())
		&& letras.bytes().all(|b| b.is_ascii_uppercase())
		&& (4..=8).contains(&n)
		&& (digitos(resto) || bolinhas(resto));
	ok.then_some((letras.len() + 1, bolinhas(resto)))
}

/// "123-456", "48-29-13", "1234-5678" (ou de bolinhas) no mesmo pedaço.
fn partido_no_pedaco(t: &str) -> Option<Tipo> {
	let partes: Vec<&str> = t.split('-').collect();
	let forma: Vec<usize> = partes.iter().map(|p| p.chars().count()).collect();
	if partes.len() < 2 || !forma_partida(&forma) {
		return None;
	}
	if partes.iter().all(|p| digitos(p)) {
		return Some(Tipo::Forte);
	}
	if partes.iter().all(|p| bolinhas(p)) {
		return Some(Tipo::Mascarado);
	}
	None
}

/// "QXF-7KT", "AB1-C2D": duas partes de 2 a 4 maiúsculas/dígitos, com letra e dígito.
fn letras_com_hifen(t: &str) -> bool {
	let partes: Vec<&str> = t.split('-').collect();
	partes.len() == 2
		&& partes.iter().all(|p| {
			(2..=4).contains(&p.chars().count()) && p.chars().all(|c| c.is_ascii_digit() || c.is_ascii_uppercase())
		})
		&& t.chars().any(|c| c.is_ascii_uppercase())
		&& t.chars().any(|c| c.is_ascii_digit())
}

fn tipo_do_pedaco(t: &str) -> Option<Tipo> {
	let n = t.chars().count();
	if bolinhas(t) {
		return (4..=8).contains(&n).then_some(Tipo::Mascarado);
	}
	if let Some(tipo) = partido_no_pedaco(t) {
		return Some(tipo);
	}
	if letras_com_hifen(t) {
		return Some(Tipo::Fraco);
	}
	if n == 5 && t.chars().all(|c| c.is_ascii_alphanumeric()) && !digitos(t) {
		// "F4K2T": o de 5 com letra e dígito (fraco: só perto da palavra).
		return t.chars().any(|c| c.is_ascii_digit()).then_some(Tipo::Fraco);
	}
	if digitos(t) {
		if !(4..=8).contains(&n) {
			return None;
		}
		let ano = n == 4 && t.parse::<u32>().is_ok_and(|v| (1900..=2099).contains(&v));
		return Some(if ano { Tipo::Fraco } else { Tipo::Forte });
	}
	if !((6..=8).contains(&n) && t.chars().all(|c| c.is_ascii_alphanumeric())) {
		return None;
	}
	let tem_digito = t.chars().any(|c| c.is_ascii_digit());
	let tem_maiuscula = t.chars().any(|c| c.is_ascii_uppercase());
	let tem_minuscula = t.chars().any(|c| c.is_ascii_lowercase());
	if !tem_digito || !(tem_maiuscula || tem_minuscula) {
		return None;
	}
	Some(if tem_minuscula { Tipo::Fraco } else { Tipo::Forte })
}

/// O número é valor, data, caminho, telefone, pedido, CEP…? (então não é código)
fn contexto_exclui(texto: &str, ps: &[Pedaco<'_>], primeiro: usize, ultimo: usize) -> bool {
	let (ini, fim) = (ps[primeiro].ini, ps[ultimo].fim);
	let mut antes_it = texto[..ini].chars().rev();
	let antes = antes_it.next();
	let mut depois_it = texto[fim..].chars();
	let depois = depois_it.next();
	if matches!(antes, Some('/' | '#' | '@')) || matches!(depois, Some('/' | '%' | '@')) {
		return true; // caminho, data, "#1234", "15%" e o endereço ("21max@tuta.com")
	}
	if depois == Some(',') && depois_it.next().is_some_and(|c| c.is_ascii_digit()) {
		return true;
	}
	let antes_do_espaco = if antes == Some(' ') { antes_it.next() } else { antes };
	if matches!(antes_do_espaco, Some('$' | '€' | '£' | ')')) {
		return true;
	}
	if depois_de_pedido(ps, primeiro) {
		return true;
	}
	ultimo + 1 < ps.len() && NAO_E_CODIGO_ANTES_DE.contains(&ps[ultimo + 1].plano.as_str())
}

/// Antes do número: "pedido", "CEP", "rastreio"… — com até 3 pedaços de
/// enchimento no meio ("pedido número", "rastreio, o número", "order number is").
fn depois_de_pedido(ps: &[Pedaco<'_>], primeiro: usize) -> bool {
	let mut i = primeiro;
	for passo in 0..4 {
		if i == 0 {
			return false;
		}
		i -= 1;
		let p = ps[i].plano.as_str();
		if NAO_E_CODIGO_DEPOIS_DE.contains(&p) && (passo == 0 || !TELEFONES.contains(&p)) {
			return true;
		}
		if !ENCHIMENTO_ANTES.contains(&p) {
			return false;
		}
	}
	false
}

/// Os pedaços a e b (= a+1) estão separados por UM espaço ou ponto?
fn junto(texto: &str, ps: &[Pedaco<'_>], a: usize, b: usize) -> bool {
	matches!(&texto[ps[a].fim..ps[b].ini], " " | ".")
}

fn comeca_com_digito(t: &str) -> bool {
	t.chars().next().is_some_and(|c| c.is_ascii_digit())
}

fn codigos(texto: &str, ps: &[Pedaco<'_>]) -> Vec<Codigo> {
	let mut saida = Vec::new();
	let mut k = 0;
	while k < ps.len() {
		let t = ps[k].texto;
		// Um grupo de números (ou de bolinhas) separados por UM espaço/ponto.
		if digitos(t) || bolinhas(t) {
			let mut fim = k;
			while fim + 1 < ps.len()
				&& junto(texto, ps, fim, fim + 1)
				&& (digitos(ps[fim + 1].texto) || bolinhas(ps[fim + 1].texto))
			{
				fim += 1;
			}
			if fim > k {
				let grupo = &ps[k..=fim];
				let forma: Vec<usize> = grupo.iter().map(|p| p.texto.chars().count()).collect();
				// Parte de um número maior ("123.456.789-09"): nenhum é código.
				let colado_antes = k > 0 && junto(texto, ps, k - 1, k) && comeca_com_digito(ps[k - 1].texto);
				let colado_depois =
					fim + 1 < ps.len() && junto(texto, ps, fim, fim + 1) && comeca_com_digito(ps[fim + 1].texto);
				if forma_de_codigo(&forma) && !colado_antes && !colado_depois {
					let trechos: Vec<(usize, usize)> = grupo.iter().map(|p| (p.ini, p.fim)).collect();
					if grupo.iter().all(|p| bolinhas(p.texto)) {
						saida.push(Codigo { tipo: Tipo::Mascarado, primeiro: k, ultimo: fim, trechos });
					} else if grupo.iter().all(|p| digitos(p.texto)) && !contexto_exclui(texto, ps, k, fim) {
						saida.push(Codigo { tipo: Tipo::Forte, primeiro: k, ultimo: fim, trechos });
					}
				}
				k = fim + 1;
				continue;
			}
		}
		if let Some((onde, mascarado)) = prefixo(t) {
			let tipo = if mascarado { Tipo::Mascarado } else { Tipo::Forte };
			saida.push(Codigo { tipo, primeiro: k, ultimo: k, trechos: vec![(ps[k].ini + onde, ps[k].fim)] });
			k += 1;
			continue;
		}
		if let Some(tipo) = tipo_do_pedaco(t) {
			if tipo == Tipo::Mascarado || !contexto_exclui(texto, ps, k, k) {
				saida.push(Codigo { tipo, primeiro: k, ultimo: k, trechos: vec![(ps[k].ini, ps[k].fim)] });
			}
		}
		k += 1;
	}
	saida
}

/// O "código"/"code" de rastreio, do produto, postal…: não é de acesso.
fn neutro(ps: &[Pedaco<'_>], i: usize) -> bool {
	if !matches!(ps[i].plano.as_str(), "codigo" | "codigos" | "code" | "codes") {
		return false;
	}
	if i > 0 && NEUTROS_ANTES.contains(&ps[i - 1].plano.as_str()) {
		return true;
	}
	let seguintes: Vec<&str> = ps.iter().skip(i + 1).take(2).map(|p| p.plano.as_str()).collect();
	NEUTROS_DEPOIS.iter().any(|n| seguintes.len() >= n.len() && seguintes[..n.len()] == **n)
}

fn palavras_de_codigo(ps: &[Pedaco<'_>]) -> Vec<usize> {
	(0..ps.len()).filter(|&i| PALAVRAS_DE_CODIGO.contains(&ps[i].plano.as_str()) && !neutro(ps, i)).collect()
}

fn perto(c: &Codigo, palavras: &[usize]) -> bool {
	palavras.iter().any(|&i| i + PERTO >= c.primeiro && i <= c.ultimo + PERTO)
}

/// O texto tem uma palavra de acesso ("código", "token", "PIN", "senha"…, fora
/// do "código de rastreio" e parecidos)?
fn tem_palavra_de_codigo(texto: &str) -> bool {
	!palavras_de_codigo(&pedacos(texto)).is_empty()
}

/// Quantos caracteres viram "•" (o tamanho do código).
fn tamanho(texto: &str, c: &Codigo) -> usize {
	c.trechos.iter().map(|&(a, b)| texto[a..b].chars().count()).sum()
}

/// O código FORTE (ou já mascarado) de 5+ com uma palavra de CÓDIGO ÚNICO perto.
fn codigo_unico(texto: &str, ps: &[Pedaco<'_>], c: &Codigo) -> bool {
	if c.tipo == Tipo::Fraco || tamanho(texto, c) < 5 {
		return false;
	}
	let ini = c.primeiro.saturating_sub(PERTO_OTP);
	let fim = ps.len().min(c.ultimo + PERTO_OTP + 1);
	(ini..fim).any(|i| {
		if (c.primeiro..=c.ultimo).contains(&i) {
			return false;
		}
		let p = ps[i].plano.as_str();
		PALAVRAS_DE_OTP.contains(&p)
			|| (PALAVRAS_DE_OTP_PERTO.contains(&p) && i + PERTO >= c.primeiro && i <= c.ultimo + PERTO)
	})
}

/// O texto tem um código único ("digite este número 731 604", "Enter 604 381 … to approve")?
fn tem_codigo_unico(texto: &str) -> bool {
	let ps = pedacos(texto);
	codigos(texto, &ps).iter().any(|c| codigo_unico(texto, &ps, c))
}

/// Link e prazo juntos ("Click here … (expires in 15 minutes)"), num e-mail com link.
fn link_com_prazo(textos: &[&str]) -> bool {
	if !textos.iter().any(|t| re_url().is_match(t)) {
		return false;
	}
	textos.iter().any(|t| {
		let sem = sem_links(t);
		let ps = pedacos(&sem);
		let links: Vec<usize> = (0..ps.len()).filter(|&i| PALAVRAS_DE_LINK.contains(&ps[i].plano.as_str())).collect();
		let prazos: Vec<usize> = (0..ps.len()).filter(|&i| PALAVRAS_DE_PRAZO.contains(&ps[i].plano.as_str())).collect();
		links.iter().any(|a| prazos.iter().any(|b| a.abs_diff(*b) <= PERTO_LINK))
	})
}

fn fala_de_entrada(plano: &str) -> bool {
	let mut p = plano.to_owned();
	for frase in ENTRAR_EM_CONTATO {
		p = p.replace(frase, " ");
	}
	PALAVRAS_DE_ENTRADA.iter().any(|e| tem_palavra(&p, e))
}

/// (o plano do texto inteiro, o plano sem as URLs)
fn planos(textos: &[&str]) -> (String, String) {
	(
		textos.iter().map(|t| plano(t)).collect::<Vec<_>>().join(" "),
		textos.iter().map(|t| plano(&sem_links(t))).collect::<Vec<_>>().join(" "),
	)
}

/// O e-mail fala de senha, de código ou de entrar na conta? Então TODOS os links saem.
#[must_use]
pub fn fala_de_acesso(textos: &[&str]) -> bool {
	let (p, sem) = planos(textos);
	GATILHOS_CODIGO_DE_ACESSO.iter().chain(GATILHOS_SEGURANCA).any(|g| tem(&p, g))
		|| PALAVRAS_DE_SENHA.iter().any(|g| tem_palavra(&p, g))
		|| fala_de_entrada(&sem)
		|| re_codigo_para_entrar().is_match(&sem)
		|| textos.iter().any(|t| tem_palavra_de_codigo(&sem_links(t)))
		|| link_com_prazo(textos)
}

/// O e-mail fala de CÓDIGO DE ACESSO ("código de verificação", "sign-in code",
/// "use o código … para entrar")? Então até o código fraco some.
#[must_use]
pub fn e_de_codigo_de_acesso(textos: &[&str]) -> bool {
	let (p, sem) = planos(textos);
	re_codigo_para_entrar().is_match(&sem) || GATILHOS_CODIGO_DE_ACESSO.iter().any(|g| tem(&p, g))
}

/// O e-mail fala de código (pelo assunto ou pelo texto, ou tem um código único:
/// "digite este número 731 604")? Então os códigos são mascarados.
#[must_use]
pub fn fala_de_codigo(textos: &[&str]) -> bool {
	let (p, sem) = planos(textos);
	GATILHOS.iter().any(|g| tem(&p, g))
		|| re_codigo_para_entrar().is_match(&sem)
		|| textos.iter().any(|t| {
			let sem_t = sem_links(t);
			tem_palavra_de_codigo(&sem_t) || tem_codigo_unico(&sem_t)
		})
}

/// Troca os códigos do texto por "•" (mesmo tamanho; o separador fica). Sem código, igual.
/// O FORTE some sempre; o FRACO só perto de uma palavra de acesso.
#[must_use]
pub fn mascarar(texto: &str) -> String {
	mascarar_com(texto, false)
}

/// `mascarar`, e com `todos_os_fracos` (o e-mail é de código de acesso) o
/// FRACO some em qualquer lugar.
fn mascarar_com(texto: &str, todos_os_fracos: bool) -> String {
	let ps = pedacos(texto);
	let palavras = palavras_de_codigo(&ps);
	let mut trechos: Vec<(usize, usize)> = codigos(texto, &ps)
		.into_iter()
		.filter(|c| c.tipo == Tipo::Forte || (c.tipo == Tipo::Fraco && (todos_os_fracos || perto(c, &palavras))))
		.flat_map(|c| c.trechos)
		.collect();
	if trechos.is_empty() {
		return texto.to_owned();
	}
	trechos.sort_unstable();
	let mut saida = String::with_capacity(texto.len() + trechos.len() * 16);
	let mut ultimo = 0;
	for (a, b) in trechos {
		saida.push_str(&texto[ultimo..a]);
		for c in texto[a..b].chars() {
			saida.push(if c.is_ascii_alphanumeric() { BOLINHA } else { c });
		}
		ultimo = b;
	}
	saida.push_str(&texto[ultimo..]);
	saida
}

/// Decodifica %xx e "+" (o `parse_qsl` do Python); bytes inválidos viram "?".
fn decodificar_url(v: &str) -> String {
	let b = v.as_bytes();
	let mut saida = Vec::with_capacity(b.len());
	let mut i = 0;
	while i < b.len() {
		match b[i] {
			b'+' => saida.push(b' '),
			b'%' if i + 2 < b.len() && b[i + 1].is_ascii_hexdigit() && b[i + 2].is_ascii_hexdigit() => {
				let alto = (b[i + 1] as char).to_digit(16).unwrap_or(0);
				let baixo = (b[i + 2] as char).to_digit(16).unwrap_or(0);
				saida.push(u8::try_from(alto * 16 + baixo).unwrap_or(b'?'));
				i += 3;
				continue;
			},
			x => saida.push(x),
		}
		i += 1;
	}
	String::from_utf8_lossy(&saida).into_owned()
}

/// A URL pode dar acesso a uma conta (login, senha, confirmação, token)?
#[must_use]
pub fn link_de_acesso(url: &str) -> bool {
	// Só o esquema do COMEÇO conta ("www.x/auth!https://y" é um link só, como no DaVinci).
	let minusculo = url.to_lowercase();
	let bruto = if minusculo.starts_with("http://") || minusculo.starts_with("https://") {
		url.to_owned()
	} else {
		format!("https://{url}")
	};
	let depois_do_esquema = bruto.split_once("://").map_or(bruto.as_str(), |(_, r)| r);
	// netloc até o primeiro / ? #; o resto é caminho?consulta#fragmento.
	let corte = depois_do_esquema.find(['/', '?', '#']).unwrap_or(depois_do_esquema.len());
	let resto = &depois_do_esquema[corte..];
	let (antes_frag, fragmento) = resto.split_once('#').unwrap_or((resto, ""));
	let (caminho, consulta) = antes_frag.split_once('?').unwrap_or((antes_frag, ""));
	let tudo = plano(&format!("{caminho} {consulta} {fragmento}"));
	let mut pedaco = String::new();
	let checar = |p: &str| {
		!p.is_empty() && (PEDACOS_LINK_DE_ACESSO.contains(&p) || PREFIXOS_LINK_DE_ACESSO.iter().any(|x| p.starts_with(x)))
	};
	for c in tudo.chars().chain(std::iter::once(' ')) {
		if c.is_ascii_lowercase() || c.is_ascii_digit() {
			pedaco.push(c);
		} else {
			if checar(&pedaco) {
				return true;
			}
			pedaco.clear();
		}
	}
	for par in consulta.split('&').filter(|p| !p.is_empty()) {
		let (nome, valor) = par.split_once('=').unwrap_or((par, ""));
		// Os espaços e o \x1c–\x1f saem das pontas (o `strip()` do Python).
		let nome = decodificar_url(nome)
			.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
			.to_lowercase();
		if PARAMETROS_SECRETOS.contains(&nome.as_str()) || opaco(&decodificar_url(valor)) {
			return true;
		}
	}
	caminho.split('/').any(opaco)
}

/// O valor tem cara de senha? (dígito, maiúscula no meio ou símbolo)
fn cara_de_senha(valor: &str) -> bool {
	valor.chars().any(|c| c.is_ascii_digit())
		|| valor.chars().skip(1).any(char::is_uppercase)
		|| valor.chars().any(|c| !alfanumerico(c))
}

/// (o que abre, o valor, o que sobra no fim) se o achado é mesmo uma senha; senão None.
fn valor_da_senha<'a>(m: &regex::Captures<'a>, solta: bool) -> Option<(&'a str, &'a str, &'a str)> {
	let bruto = m.get(5)?.as_str();
	let sem_abre = bruto.trim_start_matches(ABRE_O_VALOR);
	let abre = &bruto[..bruto.len() - sem_abre.len()];
	// Nunca um link nem o "[link de acesso removido]" que já está lá (nem o
	// "<url>" que o HTML vira: "Redefinir senha <[link de acesso removido]>").
	let limpo = sem_abre.to_lowercase();
	if limpo.starts_with('[') || ["http://", "https://", "www."].iter().any(|p| limpo.starts_with(p)) {
		return None;
	}
	let valor = sem_abre.trim_end_matches(FIM_DO_VALOR);
	let sobra = &sem_abre[valor.len()..];
	let n = valor.chars().count();
	if n == 0 || e_email(valor) {
		return None;
	}
	if solta {
		if n < 4 || !cara_de_senha(valor) {
			return None;
		}
	} else {
		// As palavras do meio (só as letras: "Nuvemshop," → "nuvemshop"), como no Python.
		let no_meio: Vec<String> = m
			.get(3)
			.map_or("", |x| x.as_str())
			.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || ('\u{c0}'..='\u{24f}').contains(&c)))
			.filter(|x| !x.is_empty())
			.map(plano)
			.collect();
		if no_meio.iter().any(|p| NAO_E_SENHA_NO_MEIO.contains(&p.as_str())) {
			return None;
		}
		let com_dois_pontos = m.get(4).is_some_and(|x| x.as_str().contains([':', '=']));
		if com_dois_pontos {
			if n < 3 {
				return None;
			}
		} else if n < 4 || !cara_de_senha(valor) {
			return None;
		}
		if !no_meio.is_empty() && digitos(valor) && n >= 9 {
			return None; // "senha do app 2000012345678901": é número de outra coisa
		}
	}
	if solta && digitos(valor) && n >= 9 {
		return None;
	}
	Some((abre, valor, sobra))
}

fn trocar_senhas(texto: &str, re: &Regex, solta: bool, trocados: &mut usize) -> String {
	re.replace_all(texto, |m: &regex::Captures<'_>| {
		let Some((abre, valor, sobra)) = valor_da_senha(m, solta) else {
			return m[0].to_owned();
		};
		*trocados += 1;
		let bolinhas = BOLINHA.to_string().repeat(valor.chars().count());
		format!("{}{}{}{}{abre}{bolinhas}{sobra}", &m[1], &m[2], &m[3], &m[4])
	})
	.into_owned()
}

/// "joao.silva@gmail.com" é endereço, não senha ("Alfa@2026x" é senha).
fn e_email(valor: &str) -> bool {
	valor.split_once('@').is_some_and(|(_, dominio)| dominio.contains('.'))
}

/// O que fica entre espaços: `nucleo` sem a pontuação das pontas (posições em bytes).
struct Palavra<'a> {
	ini: usize,
	fim: usize,
	bruto: &'a str,
	nucleo: &'a str,
	plano: String,
}

/// O que fica entre espaços (a classe `ESPACO`, a mesma do Python).
fn re_palavra() -> &'static Regex {
	static RE: OnceLock<Regex> = OnceLock::new();
	RE.get_or_init(|| Regex::new(&format!("[^{ESPACO}]+")).expect("regex fixa"))
}

fn palavras(texto: &str) -> Vec<Palavra<'_>> {
	re_palavra()
		.find_iter(texto)
		.map(|m| {
			let bruto = m.as_str();
			let sem_abre = bruto.trim_start_matches(ABRE_A_PALAVRA);
			let nucleo = sem_abre.trim_end_matches(FIM_DA_PALAVRA);
			let ini = m.start() + (bruto.len() - sem_abre.len());
			Palavra { ini, fim: ini + nucleo.len(), bruto, nucleo, plano: plano(nucleo) }
		})
		.collect()
}

/// O texto (o assunto) tem o termo ("senha", "password", "contraseña", "clave")?
fn fala_de_senha(texto: &str) -> bool {
	pedacos(texto).iter().any(|p| TERMOS_DE_SENHA.contains(&p.plano.as_str()))
}

fn de_link(valor: &str) -> bool {
	let baixo = valor.to_lowercase();
	["http://", "https://", "www."].iter().any(|p| baixo.starts_with(p))
}

/// Cara FORTE de senha: letra e dígito, e símbolo ou maiúscula com minúscula; a
/// já mascarada ("••••") também. Nunca e-mail, link, data ou caminho.
fn forte_de_senha(valor: &str) -> bool {
	let n = valor.chars().count();
	if bolinhas(valor) {
		return n >= 4;
	}
	if !(6..=64).contains(&n) || valor.contains('/') || e_email(valor) || de_link(valor) {
		return false;
	}
	let letra = valor.chars().any(|c| c.is_ascii_alphabetic());
	let digito = valor.chars().any(|c| c.is_ascii_digit());
	if !(letra && digito) {
		return false;
	}
	valor.contains(SIMBOLOS_DE_SENHA)
		|| (valor.chars().any(|c| c.is_ascii_uppercase()) && valor.chars().any(|c| c.is_ascii_lowercase()))
}

/// Cara de senha comum (depois de "para", "ficou"…): nunca o número longo, e-mail ou link.
fn comum_de_senha(valor: &str) -> bool {
	let n = valor.chars().count();
	if bolinhas(valor) {
		return n >= 4;
	}
	if !(4..=64).contains(&n) || valor.contains('/') || e_email(valor) || de_link(valor) {
		return false;
	}
	if digitos(valor) && n >= 9 {
		return false;
	}
	cara_de_senha(valor)
}

fn conector(p: &Palavra<'_>) -> bool {
	CONECTORES_DE_SENHA.contains(&p.plano.as_str())
		|| SINAIS_DE_SENHA.contains(&p.bruto)
		|| p.bruto.ends_with([':', '='])
}

fn termo(p: &Palavra<'_>) -> bool {
	TERMOS_DE_SENHA.contains(&p.plano.as_str())
}

/// As senhas escritas em FRASE (as regras do `TERMOS_DE_SENHA`): os índices das palavras.
fn senhas_em_frase(ps: &[Palavra<'_>], termo_no_assunto: bool) -> Vec<usize> {
	let mut achadas = Vec::new();
	for (j, p) in ps.iter().enumerate() {
		if p.nucleo.is_empty() || bolinhas(p.nucleo) {
			continue;
		}
		let anterior = j.checked_sub(1).map(|k| &ps[k]);
		if anterior.is_some_and(|a| {
			NAO_E_CODIGO_DEPOIS_DE.contains(&a.plano.as_str()) || NAO_E_SENHA_NO_MEIO.contains(&a.plano.as_str())
		}) {
			continue;
		}
		let termo_perto = ps[j.saturating_sub(PERTO_SENHA)..j].iter().any(termo);
		let forte = forte_de_senha(p.nucleo);
		if (forte && termo_perto) || (forte && termo_no_assunto && anterior.is_some_and(conector)) {
			achadas.push(j);
		} else if let Some(a) = anterior {
			if CONECTORES_DE_VALOR.contains(&a.plano.as_str())
				&& ps[j.saturating_sub(PERTO_SENHA)..j - 1].iter().any(termo)
				&& comum_de_senha(p.nucleo)
			{
				achadas.push(j);
			}
		}
	}
	achadas
}

/// O VALOR da senha escrita no texto vira "•" → (texto, quantos). Vale em todo e-mail.
#[must_use]
pub fn mascarar_senha(texto: &str) -> (String, usize) {
	mascarar_senha_com(texto, false)
}

/// `mascarar_senha`, e com `termo_no_assunto` (o assunto fala de senha: "senha
/// nova do ML") a de cara forte logo depois de um conector também some ("pra MlBarb0sa#26").
fn mascarar_senha_com(texto: &str, termo_no_assunto: bool) -> (String, usize) {
	let mut trocados = 0;
	let saida = trocar_senhas(texto, re_senha(), false, &mut trocados);
	let saida = trocar_senhas(&saida, re_senha_solta(), true, &mut trocados);
	let ps = palavras(&saida);
	let achadas = senhas_em_frase(&ps, termo_no_assunto);
	if achadas.is_empty() {
		return (saida, trocados);
	}
	let mut nova = String::with_capacity(saida.len() + achadas.len() * 16);
	let mut ultimo = 0;
	for &j in &achadas {
		let p = &ps[j];
		nova.push_str(&saida[ultimo..p.ini]);
		nova.push_str(&BOLINHA.to_string().repeat(p.nucleo.chars().count()));
		ultimo = p.fim;
	}
	nova.push_str(&saida[ultimo..]);
	(nova, trocados + achadas.len())
}

/// Os links de acesso (com `todos`, TODOS os links) viram "[link de acesso removido]" → (texto, quantos).
#[must_use]
pub fn mascarar_links(texto: &str) -> (String, usize) {
	mascarar_links_de(texto, false)
}

fn mascarar_links_de(texto: &str, todos: bool) -> (String, usize) {
	let mut removidos = 0;
	let saida = re_url().replace_all(texto, |m: &regex::Captures<'_>| {
		let inteiro = &m[0];
		let url = inteiro.trim_end_matches(['.', ',', ';', ':', '!', '?']);
		let sobra = &inteiro[url.len()..];
		if todos || link_de_acesso(url) {
			removidos += 1;
			format!("{LINK_REMOVIDO}{sobra}")
		} else {
			inteiro.to_owned()
		}
	});
	(saida.into_owned(), removidos)
}

/// O assunto e o texto que sobem para o DaVinci.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Protegido {
	pub assunto: String,
	pub texto: String,
	pub codigo_mascarado: bool,
	pub links_removidos: usize,
}

/// Links de acesso fora sempre (TODOS, se o e-mail fala de senha, código ou
/// acesso); a senha escrita mascarada; códigos mascarados se o e-mail fala de
/// código ou de acesso.
#[must_use]
pub fn proteger(assunto: &str, texto: &str) -> Protegido {
	let todos = fala_de_acesso(&[assunto, texto]);
	let codigo = todos || fala_de_codigo(&[assunto, texto]);
	let fracos = codigo && e_de_codigo_de_acesso(&[assunto, texto]);
	let no_assunto = fala_de_senha(assunto);
	let (assunto_ok, n1) = mascarar_links_de(assunto, todos);
	let (texto_ok, n2) = mascarar_links_de(texto, todos);
	let (mut assunto_ok, s1) = mascarar_senha_com(&assunto_ok, no_assunto);
	let (mut texto_ok, s2) = mascarar_senha_com(&texto_ok, no_assunto);
	if codigo {
		assunto_ok = mascarar_com(&assunto_ok, fracos);
		texto_ok = mascarar_com(&texto_ok, fracos);
	}
	Protegido {
		assunto: assunto_ok,
		texto: texto_ok,
		codigo_mascarado: codigo || s1 + s2 > 0,
		links_removidos: n1 + n2,
	}
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn codigos_como_o_davinci() {
		assert_eq!(mascarar("Seu código é 482913."), "Seu código é ••••••.");
		assert_eq!(mascarar("Código: AB12CD"), "Código: ••••••");
		assert_eq!(mascarar("use 123 456 ou 123.456 ou 123-456"), "use ••• ••• ou •••.••• ou •••-•••");
		// O pedido (16 dígitos), o ano com hífen e a palavra com acento ficam.
		assert_eq!(mascarar("Pedido 2000012345678901"), "Pedido 2000012345678901");
		assert_eq!(mascarar("2026-10"), "2026-10");
		assert_eq!(mascarar("ABCDEF"), "ABCDEF");
		assert_eq!(mascarar("x1234y 1234 é1234"), "x1234y •••• é1234");
		assert_eq!(mascarar("ação 4821"), "ação ••••");
	}

	#[test]
	fn fala_de_codigo_pelos_gatilhos() {
		assert!(fala_de_codigo(&["Seu código de verificação", ""]));
		assert!(fala_de_codigo(&["", "Your OTP is 1234"]));
		assert!(!fala_de_codigo(&["", "hotpot e 2fast"]));
		assert!(fala_de_codigo(&["CÓDIGO: 1234", ""]));
		assert!(!fala_de_codigo(&["Pedido 123456 enviado", "Obrigado"]));
		// "código de rastreio" não é palavra de acesso.
		assert!(!fala_de_codigo(&["", "O código de rastreio é NX1234567BR"]));
	}

	#[test]
	fn links_de_acesso() {
		for url in [
			"https://www.mercadolivre.com.br/login?x=1",
			"https://contas.shopee.com.br/redefinir-senha",
			"https://x.example/confirm/abc",
			"https://x.example/a?token=1",
			"https://x.example/a?t=1",
			"https://x.example/p/aB3dE5fG7hJ9kL1mN3pQ5rS7tU9",
			"https://x.example/a?v=aB3dE5fG7hJ9kL1mN3pQ5rS7tU9",
			"www.x.example/auth/x",
			"https://x.example/#/verify-email",
		] {
			assert!(link_de_acesso(url), "{url}");
		}
		for url in [
			"https://www.mercadolivre.com.br/vendas/2000012345/detalhe",
			"https://uranyx.com.br/acessorios",
			"https://x.example/rastreio?codigo_postal=1&pedido=2",
			"https://x.example/",
		] {
			assert!(!link_de_acesso(url), "{url}");
		}
	}

	#[test]
	fn proteger_assunto_e_texto() {
		let p = proteger(
			"Seu código de acesso",
			"Use 123456. Ou clique em https://x.example/login?u=1. Pedido: https://loja.example/pedido/9",
		);
		assert!(p.codigo_mascarado);
		// O e-mail fala de acesso: TODOS os links saem (o do pedido também).
		assert_eq!(p.links_removidos, 2);
		assert_eq!(
			p.texto,
			"Use ••••••. Ou clique em [link de acesso removido]. Pedido: [link de acesso removido]"
		);
		// Sem falar de senha/acesso, só o link de acesso sai.
		let p = proteger("Pedido", "Clique em https://x.example/login?u=1. Pedido: https://loja.example/pedido/9");
		assert_eq!(p.links_removidos, 1);
		assert_eq!(p.texto, "Clique em [link de acesso removido]. Pedido: https://loja.example/pedido/9");
		let p = proteger("Pedido 4821 chegou", "Rastreie em https://x.example/r/BR123");
		assert!(!p.codigo_mascarado);
		assert_eq!(p.assunto, "Pedido 4821 chegou");
		assert_eq!(p.links_removidos, 0);
		// O link mostrado pelo HTML ("texto <url>") também some.
		let p = proteger("Entrar", "Clique aqui <https://x.example/magic/abc>.");
		assert_eq!(p.texto, "Clique aqui <[link de acesso removido]>.");
	}

	#[test]
	fn lacunas_da_critica_de_08_10() {
		// Senha em claro, link curto de reset, código com hífen, espanhol.
		let p = proteger("Acesso", "Sua nova senha de acesso é: Ab12cd34");
		assert_eq!(p.texto, "Sua nova senha de acesso é: ••••••••");
		assert!(p.codigo_mascarado);
		let p = proteger("Redefinição de senha", "Clique para redefinir sua senha: https://www.bling.com.br/r/AbCdEf");
		assert_eq!(p.texto, "Clique para redefinir sua senha: [link de acesso removido]");
		assert_eq!(p.links_removidos, 1);
		assert_eq!(proteger("x", "G-482913 é o seu código de verificação do Google").texto, "G-•••••• é o seu código de verificação do Google");
		assert_eq!(proteger("x", "Tu código de verificación es 482913").texto, "Tu código de verificación es ••••••");
		// O que não é senha nem código fica.
		assert_eq!(proteger("x", "A senha é importante e o login também").texto, "A senha é importante e o login também");
		assert_eq!(mascarar_senha("senha=abc12345 e Password: hunter2").0, "senha=•••••••• e Password: •••••••");
		assert_eq!(mascarar_senha("contraseña: Qwerty12").0, "contraseña: ••••••••");
		assert_eq!(mascarar("PO-211-12345678"), "PO-211-12345678");
		assert_eq!(mascarar("US-26-0014"), "US-26-0014");
		// Sem falar de senha/acesso, o link comum fica.
		assert_eq!(proteger("Pedido", "Veja https://loja.example/pedido/9").links_removidos, 0);
		assert!(fala_de_acesso(&["", "Nuevo inicio de sesión en tu cuenta"]));
		assert!(!fala_de_acesso(&["Pedido", "Quando chega?"]));
	}

	#[test]
	fn lacunas_da_conferencia_pre_subida() {
		// Os 14 textos do cético (08/10, pré-subida): a senha ou o código some.
		for (assunto, texto, esperado) in [
			("Dados de acesso", "Senha gerada: Kx81mq2z", "Senha gerada: ••••••••"),
			("Painel", "Senha do painel: Kx81mq2z", "Senha do painel: ••••••••"),
			("Painel", "Senha - Kx81mq2z", "Senha - ••••••••"),
			("Bem-vindo", "Seu login é barbosa e sua senha Kx81mq2z", "Seu login é barbosa e sua senha ••••••••"),
			("Dados", "Login: barbosa Senha: xyz", "Login: barbosa Senha: •••"),
			("Painel", "Sua senha provisória Kx81mq2z", "Sua senha provisória ••••••••"),
			("Tu cuenta", "Tu contraseña temporal es: Kx81mq2z", "Tu contraseña temporal es: ••••••••"),
			("Tu cuenta", "Tu clave temporal es Kx81mq2z", "Tu clave temporal es ••••••••"),
			("Account", "Temporary password - Kx81mq2z", "Temporary password - ••••••••"),
			("Account", "Password for login: Kx81mq2z", "Password for login: ••••••••"),
			("Seu código de verificação", "Código: k7x9q2", "Código: ••••••"),
			("Seu código de verificação", "Seu código: 48 29 13", "Seu código: •• •• ••"),
			("WhatsApp", "Your WhatsApp code: 123-456", "Your WhatsApp code: •••-•••"),
			("Acceso", "Ingresa el código 482913 para iniciar sesión", "Ingresa el código •••••• para iniciar sesión"),
		] {
			let p = proteger(assunto, texto);
			assert_eq!(p.texto, esperado, "{texto}");
			assert!(p.codigo_mascarado, "{texto}");
		}
		// Os 3 links curtos de entrar: no e-mail que fala de acesso, TODOS os links saem.
		for texto in
			["Clique para entrar: https://bit.ly/3xYz", "Acesse sua conta: https://lnk.to/aB3", "Reset here: https://t.co/AbCd"]
		{
			let p = proteger("Conta", texto);
			assert!(!p.texto.contains("http"), "{texto}");
			assert_eq!(p.links_removidos, 1, "{texto}");
		}
		// Os 7 da cena da ponte (o servidor põe em "segurança"): o código ou a senha nunca sobe.
		for (assunto, texto) in [
			("Acesso à conta", "Use o código 482913 para entrar na sua conta."),
			("Token", "Seu token de acesso: 482913"),
			("Dados de acesso", "Usuário: barbosa\nSenha gerada: Kx81mq2z"),
			("482913 is your Facebook confirmation code", "Enter this code: 482913"),
			("Sign in", "Your sign-in code is 482913"),
			("Código de confirmación", "El código de confirmación es 482913"),
			("Tu cuenta", "Tu contraseña temporal es: Kx81mq2z"),
		] {
			let p = proteger(assunto, texto);
			let tudo = format!("{} {}", p.assunto, p.texto);
			assert!(!tudo.contains("482913") && !tudo.contains("Kx81mq2z"), "{tudo}");
		}
	}

	#[test]
	fn texto_normal_do_cliente_fica() {
		// Pedido do ML/Shopee/Amazon, rastreio, CEP, CPF, valor, data, telefone,
		// chave da NF-e, unidade: nem com "código" no e-mail viram "•".
		let t = "Seu código: 482913. Pedido 2000012345678901, rastreio AA123456789BR, CEP 01310-100, \
			CEP 01310100, valor R$ 150,00, R$ 1500, 1500,00, CPF 123.456.789-09, data 08/10/2026, \
			telefone (11) 3456-7890, 11 98765-4321, +55 11 98765 4321, Shopee 241008ABCD1234, \
			Amazon 701-1234567-1234567, chave 3524 1012 3456 7800 0123 5500 1000 0012 3410 0012 3456, \
			5000 mAh, 1500 reais, pedido #4521, 15%, ano 2026";
		assert_eq!(mascarar(t), t.replacen("482913", "••••••", 1));
		for (assunto, texto) in [
			("Pedido não chegou", "Meu pedido 2000012345678901 ainda não chegou. Código de rastreio: AA123456789BR. CEP 01310-100. Paguei R$ 1.299,00 em 05/10/2026."),
			("Troca", "Comprei o celular de 5000 mAh por 1500 reais, pedido 241008ABCD1234. CPF 123.456.789-09. Telefone (11) 3456-7890"),
			("Rastreio", "O código de rastreio NX1234567BR não atualiza desde 2026"),
			("Dúvida", "Qual o código do produto? Quero entrar em contato. Veja https://loja.example/produto/9"),
		] {
			let p = proteger(assunto, texto);
			assert_eq!(p.texto, texto);
			assert!(!p.codigo_mascarado && p.links_removidos == 0, "{texto}");
		}
	}

	#[test]
	fn lacunas_do_cetico_2() {
		// O código único sem palavra da lista: "número", "chave", "Enter", "digite".
		for (assunto, texto, esperado) in [
			(
				"Confirme que é você",
				"Para continuar, digite este número no Mercado Livre:\n\n731 604\n\nEle vence em 30 minutos.",
				"Para continuar, digite este número no Mercado Livre:\n\n••• •••\n\nEle vence em 30 minutos.",
			),
			("Itaú", "Informe a chave 804 117 no app Itaú.", "Informe a chave ••• ••• no app Itaú."),
			(
				"Liberação do novo celular",
				"Sua chave de segurança para liberar o novo celular é 7 3 1 8 2 0.",
				"Sua chave de segurança para liberar o novo celular é • • • • • •.",
			),
			("Confirme sua identidade", "Para concluir, digite o número 615 029 no app.", "Para concluir, digite o número ••• ••• no app."),
			(
				"Confirm it's you",
				"Enter 604 381 on the Wise website to approve this request.",
				"Enter ••• ••• on the Wise website to approve this request.",
			),
			("Confirmá tu identidad", "Tu número de verificación es 903 512.", "Tu número de verificación es ••• •••."),
		] {
			let p = proteger(assunto, texto);
			assert_eq!(p.texto, esperado, "{texto}");
			assert!(p.codigo_mascarado, "{texto}");
		}
		// A senha escrita em frase: o valor INTEIRO some.
		for (assunto, texto, esperado) in [
			("Senha redefinida", "sua senha foi redefinida para Xp7!kL2wQz.", "sua senha foi redefinida para ••••••••••."),
			("Account", "Your password has been reset to Zq!8mw#Lp4.", "Your password has been reset to ••••••••••."),
			("B2B", "e a senha ficou Alfa@2026uranyx (pode trocar)", "e a senha ficou ••••••••••••••• (pode trocar)"),
			("Painel", "troquei a senha do painel da Nuvemshop, agora é Ur4nyx!Painel.", "troquei a senha do painel da Nuvemshop, agora é •••••••••••••."),
			("Tu acceso", "tu contraseña quedó como Prov#2026mx.", "tu contraseña quedó como •••••••••••."),
			("senha nova do ML", "Troquei a do ML pra MlBarb0sa#26, anota aí", "Troquei a do ML pra ••••••••••••, anota aí"),
			("Acesso", "Senha (temporária): Xp7!kL2wQz", "Senha (temporária): ••••••••••"),
			("Acesso", "Senha → Xp7!kL2wQz", "Senha → ••••••••••"),
			("Acceso", "Tu nueva contraseña será Xp7!kL2wQz", "Tu nueva contraseña será ••••••••••"),
			("Senha", "Sua senha foi redefinida para abc12345.", "Sua senha foi redefinida para ••••••••."),
			("Garantia", "A senha do celular é 1234", "A senha do celular é ••••"),
		] {
			let p = proteger(assunto, texto);
			assert_eq!(p.texto, esperado, "{texto}");
			assert!(p.codigo_mascarado, "{texto}");
		}
		// O assunto sem "senha": o mesmo texto fica (o "pra X" é de qualquer coisa).
		assert_eq!(proteger("Pedido", "Troquei a do ML pra MlBarb0sa#26").texto, "Troquei a do ML pra MlBarb0sa#26");
		// O link mágico sem "entrar" e o link com prazo: todos os links saem.
		for texto in [
			"Click here to log into your dashboard: https://getsellerapp.io/m/7QpX2v (expires in 15 minutes)",
			"Accede a tu cuenta con este enlace: https://tiendita.mx/e/Qm3x9",
			"Toque para abrir o app já logado: abrir <https://lg.gy/a/Zp81Kd>",
			"Use o link abaixo, válido por 10 minutos: https://loja.example/l/Ab12Cd",
		] {
			let p = proteger("Acesso", texto);
			assert!(!p.texto.contains("http"), "{texto}");
			assert_eq!(p.links_removidos, 1, "{texto}");
		}
		// O código de letras com hífen ("QXF-7KT") no e-mail de código de acesso.
		let p = proteger("Slack confirmation code: QXF-7KT", "Enter it in your browser.\n\nQXF-7KT");
		assert_eq!((p.assunto.as_str(), p.texto.as_str()), ("Slack confirmation code: •••-•••", "Enter it in your browser.\n\n•••-•••"));
		// O endereço nunca é código ("21max@tuta.com").
		assert_eq!(
			proteger("Código de verificação", "usar 21max@tuta.com como recuperação. Código: 482913").texto,
			"usar 21max@tuta.com como recuperação. Código: ••••••"
		);
	}

	#[test]
	fn normais_do_cetico_2_ficam() {
		for texto in [
			"Confirme a entrega do pedido 2000012345678901. Pacote 43210987654.",
			"Confirme os dados de envio. Rua das Flores, 1578 - CEP 01310-100.",
			"O pedido 241008XYZ12345 vence em 2 dias. Envie até 10/10.",
			"Devolução 48291375 aprovada. Confirme o envio até 12/10.",
			"Mensagem do comprador: o número do pedido é 48291375, confirme por favor",
			"Não consigo entrar no app Uranyx Care. Número de série K81Q2X, pedido 2000012345678901.",
			"não consigo acessar o rastreio, o número 48291375 não funciona.",
			"Esqueci minha senha do app Uranyx Care, como recupero? Meu e-mail de cadastro é joao.silva@gmail.com.",
			"Fiz o PIX de R$ 89,90, ID da transação E12345678202610081234abcdef. Segue comprovante.",
		] {
			assert_eq!(proteger("Mensagem", texto).texto, texto);
		}
	}

	#[test]
	fn casos_iguais_ao_davinci() {
		// Os mesmos casos que o teste Python roda contra `codigos.proteger`.
		let bruto = include_str!("../../tests/dados/protecao-casos.json");
		let casos: serde_json::Value = serde_json::from_str(bruto).expect("json dos casos");
		let lista = casos.as_array().expect("lista de casos");
		assert!(lista.len() >= 190, "poucos casos: {}", lista.len());
		for caso in lista {
			let assunto = caso["assunto"].as_str().unwrap_or_default();
			let texto = caso["texto"].as_str().unwrap_or_default();
			let p = proteger(assunto, texto);
			let e = &caso["esperado"];
			assert_eq!(p.assunto, e["assunto"].as_str().unwrap_or_default(), "assunto de {texto:?}");
			assert_eq!(p.texto, e["texto"].as_str().unwrap_or_default(), "texto de {texto:?}");
			assert_eq!(p.codigo_mascarado, e["codigo_mascarado"].as_bool().unwrap_or_default(), "{texto:?}");
			assert_eq!(
				u64::try_from(p.links_removidos).unwrap_or(u64::MAX),
				e["links_removidos"].as_u64().unwrap_or_default(),
				"{texto:?}"
			);
		}
	}

	#[test]
	fn entradas_estranhas() {
		for t in ["", "%", "https://", "https://x?%zz=%", "www.", "•••", "1234\u{0}5678", "• • • •", "senha:", "código ••••-", "G-", "-1234-"] {
			let _ = proteger(t, t);
			let _ = link_de_acesso(t);
		}
		assert_eq!(decodificar_url("a%20b+c%zz%"), "a b c%zz%");
	}
}
