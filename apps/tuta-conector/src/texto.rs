//! O texto do e-mail como a Central guarda: TEXTO (nunca HTML), com os
//! códigos e os links de acesso já mascarados — tudo feito AQUI no Mac,
//! antes de qualquer coisa ir ao DaVinci.
//!
//! - `html`: o corpo do Tuta (HTML) vira texto legível sem perder o que
//!   importa: o link vira "texto <url>" (mostra a diferença entre o texto e
//!   o destino, o sinal típico de golpe), a imagem vira "[imagem: alt]", a
//!   citação vira "> ", script/style/head somem. Código nosso, sem
//!   dependência nova (nada de parser de terceiros) e sem `unsafe`.
//! - `protecao`: o mesmo de `services/mail_atendimento/codigos.py` do
//!   DaVinci (D8 + a crítica de 08/10): link que dá ACESSO (login, senha,
//!   confirmação, token) vira "[link de acesso removido]" e, se o e-mail
//!   fala de código, os números de 4 a 8 dígitos (e o código alfanumérico
//!   curto) viram "•". Os aliases da conta geral são os e-mails de LOGIN das
//!   lojas nas plataformas: um código desses na mão de qualquer leitor
//!   tomaria a conta de uma loja.
//!
//! Puro: sem rede, sem disco, sem log de conteúdo.

pub mod html;
pub mod protecao;

/// Corta em caracteres (nunca no meio de um caractere).
#[must_use]
pub fn cortar(texto: &str, maximo: usize) -> String {
	match texto.char_indices().nth(maximo) {
		Some((i, _)) => texto[..i].to_owned(),
		None => texto.to_owned(),
	}
}

/// Uma linha de cabeçalho: sem CR, LF, NUL nem outro controle; espaços juntos.
#[must_use]
pub fn uma_linha(texto: &str, maximo: usize) -> String {
	let limpo: String = texto.chars().map(|c| if c.is_control() { ' ' } else { c }).collect();
	cortar(limpo.split_whitespace().collect::<Vec<_>>().join(" ").as_str(), maximo)
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn cortar_respeita_caractere() {
		assert_eq!(cortar("ação", 2), "aç");
		assert_eq!(cortar("abc", 10), "abc");
		assert_eq!(cortar("", 1), "");
	}

	#[test]
	fn uma_linha_tira_quebras_e_controles() {
		assert_eq!(uma_linha("Assunto\r\nBcc: x@y\0 fim", 998), "Assunto Bcc: x@y fim");
		assert_eq!(uma_linha("  a   b  ", 10), "a b");
		assert_eq!(uma_linha(&"é".repeat(2000), 998).chars().count(), 998);
	}
}
