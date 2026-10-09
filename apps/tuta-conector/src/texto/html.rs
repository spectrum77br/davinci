//! O corpo do Tuta (HTML) → TEXTO (a Central nunca guarda HTML).
//!
//! O que não pode se perder:
//! - link: "texto <url>" (quando o texto já é a URL, só ela) — a diferença
//!   entre o texto e o destino é o sinal típico de golpe;
//! - imagem: "[imagem: alt]", a embutida (cid:) "[imagem embutida: alt]";
//!   o pixel de rastreio (1×1 sem alt) some;
//! - parágrafo, quebra, item de lista ("- "), linha de tabela: quebras de
//!   linha; citação (`blockquote`): "> " na frente de cada linha;
//! - entidades (&amp;, &aacute;, &#227;…) decodificadas.
//!
//! O que some: script, style, head, title, template, noscript, svg; os
//! caracteres invisíveis (&zwnj;, &shy;…); mais de uma linha em branco seguida.
//! Cores, layout e imagens de verdade: só no Tuta ("Abrir no Tuta").
//!
//! Escrito à mão (sem parser de terceiros), linear e sem `unsafe`; quem
//! chama ainda roda isolado de pânico. Entrada até CORPO_HTML_MAX_CARACTERES.

/// O texto e se foi cortado no teto.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Convertido {
	pub texto: String,
	pub cortado: bool,
}

/// A marca do corte (o original inteiro continua no Tuta).
pub const MARCA_CORTE: &str = "[…cortado; o original está no Tuta]";

const IGNORAR_CONTEUDO: &[&str] = &["script", "style", "head", "title", "template", "noscript", "svg", "object", "xml"];
const BLOCOS: &[&str] = &[
	"div", "table", "tbody", "thead", "tfoot", "tr", "ul", "ol", "dl", "dt", "dd", "section", "article", "header", "footer",
	"nav", "aside", "main", "form", "fieldset", "figure", "figcaption", "address", "center", "caption", "details",
	"summary",
];
const PARAGRAFOS: &[&str] = &["p", "h1", "h2", "h3", "h4", "h5", "h6", "pre"];

struct Link {
	href: String,
	inicio: usize,
}

struct Saida {
	buf: String,
	quebras: u8,
	espaco: bool,
	em_linha: bool,
	citacao: usize,
	pre: usize,
	links: Vec<Link>,
}

impl Saida {
	fn novo() -> Self {
		Self {
			buf: String::new(),
			quebras: 0,
			espaco: false,
			em_linha: false,
			citacao: 0,
			pre: 0,
			links: Vec::new(),
		}
	}

	/// Pede `n` quebras de linha antes do próximo texto (no máximo 2 = uma linha em branco).
	fn quebra(&mut self, n: u8) {
		if self.buf.is_empty() {
			return;
		}
		self.quebras = self.quebras.max(n.min(2));
		self.espaco = false;
	}

	/// Uma quebra a mais (o <br>): duas seguidas fazem uma linha em branco.
	fn br(&mut self) {
		if self.buf.is_empty() {
			return;
		}
		self.quebras = if self.em_linha && self.quebras == 0 { 1 } else { (self.quebras + 1).min(2) };
		self.em_linha = false;
		self.espaco = false;
	}

	fn abrir_linha(&mut self) {
		if self.quebras > 0 && !self.buf.is_empty() {
			for _ in 0..self.quebras {
				self.buf.push('\n');
			}
			self.em_linha = false;
		}
		self.quebras = 0;
		if !self.em_linha {
			for _ in 0..self.citacao {
				self.buf.push_str("> ");
			}
			self.em_linha = true;
			self.espaco = false;
		}
	}

	/// Texto já decodificado: os espaços viram um só (fora de <pre>).
	fn texto(&mut self, t: &str) {
		if self.pre > 0 {
			let mut primeira = true;
			for linha in t.split('\n') {
				if !primeira {
					self.quebras = 1;
					self.em_linha = false;
				}
				primeira = false;
				let linha = linha.trim_end_matches('\r');
				if !linha.is_empty() {
					self.abrir_linha();
					self.buf.push_str(linha);
				}
			}
			return;
		}
		let comeca_com_espaco = t.starts_with(espaco_html);
		let termina_com_espaco = t.ends_with(espaco_html);
		let mut palavras = t.split(espaco_html).filter(|p| !p.is_empty()).peekable();
		if palavras.peek().is_none() {
			if comeca_com_espaco && self.em_linha {
				self.espaco = true;
			}
			return;
		}
		if comeca_com_espaco && self.em_linha {
			self.espaco = true;
		}
		let mut primeira = true;
		for p in palavras {
			if self.quebras > 0 || !self.em_linha {
				self.abrir_linha();
			} else if (self.espaco || !primeira) && !self.buf.ends_with(' ') {
				self.buf.push(' ');
			}
			self.buf.push_str(p);
			self.espaco = false;
			primeira = false;
		}
		self.espaco = termina_com_espaco;
	}

	/// Um pedaço que vai como está (marca de imagem, URL do link), com espaço antes.
	fn marca(&mut self, t: &str) {
		if self.quebras > 0 || !self.em_linha {
			self.abrir_linha();
		} else if !self.buf.ends_with(' ') && !self.buf.ends_with("> ") {
			self.buf.push(' ');
		}
		self.buf.push_str(t);
		self.espaco = false;
	}
}

fn espaco_html(c: char) -> bool {
	matches!(c, ' ' | '\t' | '\n' | '\r' | '\x0c' | '\u{a0}')
}

/// Os caracteres invisíveis que as newsletters enchem de linhas vazias.
fn invisivel(c: char) -> bool {
	matches!(c, '\u{200b}' | '\u{200c}' | '\u{200d}' | '\u{2060}' | '\u{feff}' | '\u{ad}' | '\u{34f}')
}

/// HTML → texto, com o teto em caracteres.
#[must_use]
pub fn para_texto(html: &str, maximo: usize) -> Convertido {
	let mut s = Saida::novo();
	let bytes = html.as_bytes();
	let mut i = 0usize;
	let mut texto_ini = 0usize;
	while i < bytes.len() {
		if bytes[i] != b'<' {
			i += 1;
			continue;
		}
		match ler_tag(html, i) {
			Some((tag, fim)) => {
				// O texto antes da tag.
				if texto_ini < i {
					s.texto(&decodificar(&html[texto_ini..i]));
				}
				i = fim;
				if let Some(nome) = tag.nome.as_deref() {
					if !tag.fecha && !tag.auto && IGNORAR_CONTEUDO.contains(&nome) {
						i = pular_ate_fechar(html, i, nome);
					} else {
						aplicar(&mut s, &tag);
					}
				}
				texto_ini = i;
			},
			None => {
				// "<" que não abre tag ("a < b"): é texto.
				i += 1;
			},
		}
	}
	if texto_ini < bytes.len() {
		s.texto(&decodificar(&html[texto_ini..]));
	}
	arrumar(&s.buf, maximo)
}

/// Uma tag lida.
#[derive(Debug, Default)]
struct Tag {
	/// None = comentário/declaração (só some).
	nome: Option<String>,
	fecha: bool,
	auto: bool,
	attrs: Vec<(String, String)>,
}

impl Tag {
	fn attr(&self, nome: &str) -> Option<&str> {
		self.attrs.iter().find(|(n, _)| n == nome).map(|(_, v)| v.as_str())
	}
}

/// Lê a tag que começa em `ini` (o "<"). Devolve a tag e onde ela termina.
fn ler_tag(html: &str, ini: usize) -> Option<(Tag, usize)> {
	let resto = &html[ini..];
	if resto.starts_with("<!--") {
		let fim = resto.find("-->").map_or(html.len(), |f| ini + f + 3);
		return Some((Tag::default(), fim));
	}
	let b = resto.as_bytes();
	if b.len() < 2 {
		return None;
	}
	if b[1] == b'!' || b[1] == b'?' {
		let fim = resto.find('>').map_or(html.len(), |f| ini + f + 1);
		return Some((Tag::default(), fim));
	}
	let mut j = 1;
	let fecha = b[1] == b'/';
	if fecha {
		j = 2;
	}
	let nome_ini = j;
	while j < b.len() && (b[j].is_ascii_alphanumeric() || b[j] == b'-' || b[j] == b':') {
		j += 1;
	}
	if j == nome_ini || !b[nome_ini].is_ascii_alphabetic() {
		return None;
	}
	let nome = resto[nome_ini..j].to_ascii_lowercase();
	let mut tag = Tag {
		nome: Some(nome),
		fecha,
		auto: false,
		attrs: Vec::new(),
	};
	// Atributos até o ">" (respeitando aspas).
	loop {
		while j < b.len() && (b[j] as char).is_ascii_whitespace() {
			j += 1;
		}
		if j >= b.len() {
			return Some((tag, html.len()));
		}
		match b[j] {
			b'>' => return Some((tag, ini + j + 1)),
			b'/' => {
				tag.auto = true;
				j += 1;
				continue;
			},
			_ => {},
		}
		let a_ini = j;
		while j < b.len() && !(b[j] as char).is_ascii_whitespace() && b[j] != b'=' && b[j] != b'>' && b[j] != b'/' {
			j += 1;
		}
		let nome_attr = resto[a_ini..j].to_ascii_lowercase();
		while j < b.len() && (b[j] as char).is_ascii_whitespace() {
			j += 1;
		}
		let mut valor = String::new();
		if j < b.len() && b[j] == b'=' {
			j += 1;
			while j < b.len() && (b[j] as char).is_ascii_whitespace() {
				j += 1;
			}
			if j < b.len() && (b[j] == b'"' || b[j] == b'\'') {
				let aspa = b[j];
				let v_ini = j + 1;
				let v_fim = resto[v_ini..].find(aspa as char).map_or(b.len(), |f| v_ini + f);
				valor = decodificar(&resto[v_ini..v_fim]);
				j = (v_fim + 1).min(b.len());
			} else {
				let v_ini = j;
				while j < b.len() && !(b[j] as char).is_ascii_whitespace() && b[j] != b'>' {
					j += 1;
				}
				valor = decodificar(&resto[v_ini..j]);
			}
		} else if a_ini == j {
			// Byte estranho: anda um para não ficar parado.
			j += 1;
			continue;
		}
		if !nome_attr.is_empty() && tag.attrs.len() < 64 {
			tag.attrs.push((nome_attr, valor));
		}
	}
}

/// Depois de <script>, <style>…: tudo até o "</nome" correspondente some.
fn pular_ate_fechar(html: &str, de: usize, nome: &str) -> usize {
	let alvo = format!("</{nome}");
	let resto = &html[de..];
	// Busca sem diferenciar maiúsculas, sem alocar o texto inteiro em minúsculas.
	let b = resto.as_bytes();
	let a = alvo.as_bytes();
	let mut k = 0;
	while k + a.len() <= b.len() {
		if b[k] == b'<' && b[k..k + a.len()].eq_ignore_ascii_case(a) {
			let depois = de + k + a.len();
			return html[depois..].find('>').map_or(html.len(), |f| depois + f + 1);
		}
		k += 1;
	}
	html.len()
}

fn aplicar(s: &mut Saida, tag: &Tag) {
	let Some(nome) = tag.nome.as_deref() else {
		return;
	};
	match (nome, tag.fecha) {
		("br", _) => s.br(),
		("hr", false) => {
			s.quebra(1);
			s.marca("---");
			s.quebra(1);
		},
		("li", false) => {
			s.quebra(1);
			s.marca("-");
			s.espaco = true;
		},
		("li", true) => s.quebra(1),
		("td" | "th", false) => {
			if s.em_linha {
				s.espaco = true;
			}
		},
		("blockquote", false) => {
			s.quebra(1);
			s.citacao += 1;
		},
		("blockquote", true) => {
			s.citacao = s.citacao.saturating_sub(1);
			s.quebra(1);
			s.em_linha = false;
		},
		("pre", false) => {
			s.quebra(2);
			s.pre += 1;
		},
		("pre", true) => {
			s.pre = s.pre.saturating_sub(1);
			s.quebra(2);
		},
		("img", _) => imagem(s, tag),
		("a", false) => {
			let href = tag.attr("href").unwrap_or("").trim().to_owned();
			s.links.push(Link {
				href,
				inicio: s.buf.len(),
			});
			if tag.auto {
				fechar_link(s);
			}
		},
		("a", true) => fechar_link(s),
		(n, _) if PARAGRAFOS.contains(&n) => s.quebra(2),
		(n, _) if BLOCOS.contains(&n) => s.quebra(1),
		_ => {},
	}
}

fn imagem(s: &mut Saida, tag: &Tag) {
	let alt = tag.attr("alt").map(|a| a.split_whitespace().collect::<Vec<_>>().join(" ")).unwrap_or_default();
	let pixel = |v: Option<&str>| v.is_some_and(|x| matches!(x.trim().trim_end_matches("px"), "0" | "1"));
	if alt.is_empty() && (pixel(tag.attr("width")) || pixel(tag.attr("height"))) {
		return;
	}
	let embutida = tag.attr("src").is_some_and(|x| x.trim().to_ascii_lowercase().starts_with("cid:"));
	let alt = crate::texto::cortar(&alt, 200);
	let marca = match (embutida, alt.is_empty()) {
		(true, true) => "[imagem embutida]".to_owned(),
		(true, false) => format!("[imagem embutida: {alt}]"),
		(false, true) => "[imagem]".to_owned(),
		(false, false) => format!("[imagem: {alt}]"),
	};
	s.marca(&marca);
}

/// O destino que aparece: http(s), www. e mailto:. O resto (javascript:,
/// âncora, relativo) não é mostrado.
fn destino_mostravel(href: &str) -> Option<String> {
	let h = href.trim();
	let minusculo = h.to_ascii_lowercase();
	if minusculo.starts_with("http://") || minusculo.starts_with("https://") || minusculo.starts_with("www.") {
		return Some(h.chars().filter(|c| !c.is_whitespace() && !c.is_control()).take(2000).collect());
	}
	if let Some(resto) = minusculo.strip_prefix("mailto:") {
		let endereco: String = resto.split('?').next().unwrap_or("").trim().chars().take(254).collect();
		return (!endereco.is_empty()).then(|| format!("mailto:{endereco}"));
	}
	None
}

fn sem_esquema(u: &str) -> String {
	let u = u.trim().trim_end_matches('/').to_ascii_lowercase();
	let u = u.strip_prefix("https://").or_else(|| u.strip_prefix("http://")).unwrap_or(&u).to_owned();
	let u = u.strip_prefix("mailto:").unwrap_or(&u).to_owned();
	u.strip_prefix("www.").unwrap_or(&u).to_owned()
}

fn fechar_link(s: &mut Saida) {
	let Some(link) = s.links.pop() else {
		return;
	};
	let Some(destino) = destino_mostravel(&link.href) else {
		return;
	};
	let inicio = link.inicio.min(s.buf.len());
	let texto_do_link = s.buf.get(inicio..).unwrap_or("").trim().to_owned();
	let alvo = sem_esquema(&destino);
	if !texto_do_link.is_empty() && sem_esquema(&texto_do_link) == alvo {
		// O texto já é o destino: não repete.
		return;
	}
	let mostrado = destino.strip_prefix("mailto:").unwrap_or(&destino).to_owned();
	s.marca(&format!("<{mostrado}>"));
}

/// Limpa o resultado: sem espaço no fim das linhas, no máximo uma linha em
/// branco seguida, sem os invisíveis; e o teto.
fn arrumar(bruto: &str, maximo: usize) -> Convertido {
	let mut saida = String::with_capacity(bruto.len().min(maximo + 64));
	let mut brancas = 0usize;
	for linha in bruto.split('\n') {
		let linha: String = linha.chars().filter(|c| !invisivel(*c)).collect();
		let linha = linha.trim_end();
		let so_citacao = linha.chars().all(|c| c == '>' || c == ' ');
		if linha.is_empty() || so_citacao {
			brancas += 1;
			continue;
		}
		if !saida.is_empty() {
			saida.push('\n');
			if brancas > 0 {
				saida.push('\n');
			}
		}
		brancas = 0;
		saida.push_str(linha);
	}
	if saida.chars().count() <= maximo {
		return Convertido {
			texto: saida,
			cortado: false,
		};
	}
	let limite = maximo.saturating_sub(MARCA_CORTE.chars().count() + 1);
	let mut cortado = crate::texto::cortar(&saida, limite).trim_end().to_owned();
	cortado.push('\n');
	cortado.push_str(MARCA_CORTE);
	Convertido {
		texto: cortado,
		cortado: true,
	}
}

/// As entidades do HTML (as comuns em e-mail em português; o resto numérico).
#[must_use]
pub fn decodificar(t: &str) -> String {
	if !t.contains('&') {
		return t.to_owned();
	}
	let mut saida = String::with_capacity(t.len());
	let mut resto = t;
	while let Some(p) = resto.find('&') {
		saida.push_str(&resto[..p]);
		let depois = &resto[p + 1..];
		match entidade(depois) {
			Some((texto, consumido)) => {
				saida.push_str(&texto);
				resto = &depois[consumido..];
			},
			None => {
				saida.push('&');
				resto = depois;
			},
		}
	}
	saida.push_str(resto);
	saida
}

/// A entidade no começo de `t` (sem o "&") → (texto, bytes consumidos).
fn entidade(t: &str) -> Option<(String, usize)> {
	let fim = t.char_indices().take(40).find(|(_, c)| *c == ';').map(|(i, _)| i);
	if let Some(num) = t.strip_prefix('#') {
		let f = fim?;
		let corpo = &num[..f - 1];
		let codigo = if let Some(hex) = corpo.strip_prefix(['x', 'X']) {
			u32::from_str_radix(hex, 16).ok()?
		} else {
			corpo.parse::<u32>().ok()?
		};
		let c = char::from_u32(codigo).filter(|c| *c != '\0').unwrap_or('\u{fffd}');
		return Some((c.to_string(), f + 1));
	}
	// Sem ";": só as que o navegador aceita assim.
	let sem_ponto = [("amp", "&"), ("lt", "<"), ("gt", ">"), ("quot", "\""), ("nbsp", " ")];
	let Some(f) = fim else {
		return sem_ponto
			.iter()
			.find(|(n, _)| t.starts_with(n) && !t[n.len()..].starts_with(|c: char| c.is_ascii_alphanumeric()))
			.map(|(n, v)| ((*v).to_owned(), n.len()));
	};
	let nome = &t[..f];
	let valor = match nome {
		"amp" => "&",
		"lt" => "<",
		"gt" => ">",
		"quot" => "\"",
		"apos" => "'",
		"nbsp" | "ensp" | "emsp" | "thinsp" => " ",
		"zwnj" | "zwj" | "shy" | "lrm" | "rlm" => "",
		"copy" => "©",
		"reg" => "®",
		"trade" => "™",
		"hellip" => "…",
		"mdash" => "—",
		"ndash" => "–",
		"laquo" => "«",
		"raquo" => "»",
		"ldquo" => "“",
		"rdquo" => "”",
		"lsquo" => "‘",
		"rsquo" => "’",
		"sbquo" => "‚",
		"bdquo" => "„",
		"bull" | "middot" => "•",
		"euro" => "€",
		"deg" => "°",
		"ordf" => "ª",
		"ordm" => "º",
		"sup2" => "²",
		"times" => "×",
		"aacute" => "á",
		"Aacute" => "Á",
		"agrave" => "à",
		"Agrave" => "À",
		"acirc" => "â",
		"Acirc" => "Â",
		"atilde" => "ã",
		"Atilde" => "Ã",
		"auml" => "ä",
		"eacute" => "é",
		"Eacute" => "É",
		"egrave" => "è",
		"ecirc" => "ê",
		"Ecirc" => "Ê",
		"iacute" => "í",
		"Iacute" => "Í",
		"oacute" => "ó",
		"Oacute" => "Ó",
		"ocirc" => "ô",
		"Ocirc" => "Ô",
		"otilde" => "õ",
		"Otilde" => "Õ",
		"ouml" => "ö",
		"uacute" => "ú",
		"Uacute" => "Ú",
		"uuml" => "ü",
		"Uuml" => "Ü",
		"ccedil" => "ç",
		"Ccedil" => "Ç",
		"ntilde" => "ñ",
		"Ntilde" => "Ñ",
		_ => return None,
	};
	Some((valor.to_owned(), f + 1))
}

#[cfg(test)]
mod testes {
	use super::*;

	fn t(html: &str) -> String {
		para_texto(html, 1_000_000).texto
	}

	#[test]
	fn paragrafos_quebras_e_espacos() {
		assert_eq!(t("<p>Olá,</p><p>Recebi   o\n produto.</p>"), "Olá,\n\nRecebi o produto.");
		assert_eq!(t("linha 1<br>linha 2<br><br>linha 4"), "linha 1\nlinha 2\n\nlinha 4");
		assert_eq!(t("<div>a</div><div>b</div>"), "a\nb");
		assert_eq!(t("<b>Pedido</b> <i>2000012345</i>"), "Pedido 2000012345");
		assert_eq!(t("texto puro\nsem html"), "texto puro sem html");
	}

	#[test]
	fn links_mostram_o_destino() {
		assert_eq!(
			t(r#"Clique <a href="https://golpe.example/x">aqui no Mercado Livre</a>."#),
			"Clique aqui no Mercado Livre <https://golpe.example/x>."
		);
		// Texto = destino: não repete.
		assert_eq!(t(r#"<a href="https://uranyx.com.br/">https://uranyx.com.br</a>"#), "https://uranyx.com.br");
		assert_eq!(t(r#"<a href="mailto:sac@uranyx.com.br">fale conosco</a>"#), "fale conosco <sac@uranyx.com.br>");
		assert_eq!(t(r#"<a href="mailto:sac@uranyx.com.br">sac@uranyx.com.br</a>"#), "sac@uranyx.com.br");
		assert_eq!(t(r#"<a href="javascript:alert(1)">x</a>"#), "x");
		assert_eq!(t(r##"<a href="#topo">topo</a>"##), "topo");
		assert_eq!(t(r#"<a href="https://a.example"><img src="https://a.example/l.png" alt="Logo"></a>"#), "[imagem: Logo] <https://a.example>");
	}

	#[test]
	fn imagens_citacao_listas_tabelas() {
		assert_eq!(t(r#"<img src="cid:abc" alt="foto do produto">"#), "[imagem embutida: foto do produto]");
		assert_eq!(t(r#"<img src="https://t.example/p.gif" width="1" height="1">ok"#), "ok");
		assert_eq!(t(r#"<img src="https://x/y.png">"#), "[imagem]");
		assert_eq!(
			t("<p>Minha resposta</p><blockquote><p>Pergunta antiga</p><p>linha 2</p></blockquote>"),
			"Minha resposta\n\n> Pergunta antiga\n\n> linha 2"
		);
		assert_eq!(t("<ul><li>um</li><li>dois</li></ul>"), "- um\n- dois");
		assert_eq!(t("<table><tr><td>Qtd</td><td>1</td></tr><tr><td>Total</td><td>R$ 10</td></tr></table>"), "Qtd 1\nTotal R$ 10");
		assert_eq!(t("<pre>a\n  b</pre>"), "a\n  b");
	}

	#[test]
	fn some_o_que_nao_e_texto() {
		assert_eq!(t("<html><head><title>T</title><style>p{color:red}</style></head><body>oi</body></html>"), "oi");
		assert_eq!(t("<script>var x = '<p>não</p>';</script>sim"), "sim");
		assert_eq!(t("<SCRIPT>x</SCRIPT>sim"), "sim");
		assert_eq!(t("<!-- comentário <p> -->a<!DOCTYPE html>b"), "ab");
		assert_eq!(t("preheader&zwnj;&nbsp;&zwnj;&nbsp;texto"), "preheader texto");
		assert_eq!(t("a < b e c > d"), "a < b e c > d");
		// Script sem fim: o resto some (não vira texto).
		assert_eq!(t("ok<script>sem fim"), "ok");
	}

	#[test]
	fn entidades() {
		assert_eq!(decodificar("Jo&atilde;o &amp; Cia &#8212; &#x2713; &lt;b&gt;"), "João & Cia — ✓ <b>");
		assert_eq!(decodificar("R&amp D &copy x"), "R& D &copy x");
		assert_eq!(decodificar("&#0; &#xZZ; &naoexiste;"), "\u{fffd} &#xZZ; &naoexiste;");
		assert_eq!(t("<p>Pre&ccedil;o: R$&nbsp;10</p>"), "Preço: R$ 10");
	}

	#[test]
	fn teto_corta_com_a_marca() {
		let grande = format!("<p>{}</p>", "palavra ".repeat(1000));
		let c = para_texto(&grande, 200);
		assert!(c.cortado);
		assert!(c.texto.chars().count() <= 200);
		assert!(c.texto.ends_with(MARCA_CORTE));
	}

	#[test]
	fn entrada_estranha_nao_quebra() {
		for lixo in ["<", "<<>>", "</", "<a href=", "<a href='x", "&#", "&#x", "&", "<p", "<img alt=\"", "<\u{1F600}>", "é<é>é"] {
			let _ = para_texto(lixo, 100);
		}
		assert_eq!(t("é<é>é"), "é<é>é");
	}
}
