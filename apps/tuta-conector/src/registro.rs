//! O log do conector (stderr; o LaunchAgent manda para um arquivo).
//!
//! Regras (auditoria da ponte: RUST_LOG=debug virava cópia dos e-mails e do token):
//! - NÃO obedece RUST_LOG. Nosso código loga no nível pedido (padrão info); o
//!   SDK e as bibliotecas (hyper, rustls…) só de "warn" para cima.
//! - Toda linha passa por `mascarar`: e-mail, token, `accessToken=`, `_body=`
//!   e textos longos com cara de chave viram `***`/`<e-mail>`/`<…>`.
//! - Pânico: só o lugar (arquivo:linha) e um trecho mascarado da mensagem
//!   (o SDK tem `panic!` que imprime o valor recebido do servidor).

use log::{LevelFilter, Log, Metadata, Record};
use regex::Regex;
use std::io::Write;
use std::sync::{Mutex, OnceLock};

const ALVO_NOSSO: &str = "tuta_conector";
const LINHA_MAX: usize = 2000;
const PANICO_MAX: usize = 200;

fn padroes() -> &'static [(Regex, &'static str)] {
	static PADROES: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
	PADROES.get_or_init(|| {
		[
			(
				r"(?i)\b(accessToken|blobAccessToken|_body|token|authVerifier|senha|password|otpCode)=[^&\s]*",
				"$1=***",
			),
			(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***"),
			(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", "<e-mail>"),
			(r"[A-Za-z0-9_+/-]{28,}={0,2}", "<…>"),
		]
		.into_iter()
		.map(|(padrao, troca)| (Regex::new(padrao).expect("padrão do registro"), troca))
		.collect()
	})
}

/// Tira do texto o que pode ser segredo ou dado pessoal.
#[must_use]
pub fn mascarar(texto: &str) -> String {
	let mut saida = texto.to_owned();
	for (padrao, troca) in padroes() {
		saida = padrao.replace_all(&saida, *troca).into_owned();
	}
	if saida.chars().count() > LINHA_MAX {
		saida = saida.chars().take(LINHA_MAX).collect::<String>() + "…";
	}
	saida
}

struct Registro {
	nivel_nosso: LevelFilter,
}

fn captura() -> &'static Mutex<Option<Vec<String>>> {
	static CAPTURA: OnceLock<Mutex<Option<Vec<String>>>> = OnceLock::new();
	CAPTURA.get_or_init(|| Mutex::new(None))
}

impl Log for Registro {
	fn enabled(&self, metadata: &Metadata<'_>) -> bool {
		if metadata.target().starts_with(ALVO_NOSSO) {
			metadata.level() <= self.nivel_nosso
		} else {
			metadata.level() <= LevelFilter::Warn
		}
	}

	fn log(&self, record: &Record<'_>) {
		if !self.enabled(record.metadata()) {
			return;
		}
		let quando = time::OffsetDateTime::now_utc()
			.format(&time::format_description::well_known::Rfc3339)
			.unwrap_or_default();
		let alvo = if record.target().starts_with(ALVO_NOSSO) {
			"conector"
		} else {
			record.target()
		};
		let linha = format!(
			"{quando} {} {alvo}: {}",
			record.level(),
			mascarar(&record.args().to_string())
		);
		if let Ok(mut guarda) = captura().lock() {
			if let Some(linhas) = guarda.as_mut() {
				linhas.push(linha.clone());
			}
		}
		let _ = writeln!(std::io::stderr().lock(), "{linha}");
	}

	fn flush(&self) {}
}

/// Liga o log (uma vez por processo; depois disso não faz nada).
pub fn iniciar(nivel_nosso: LevelFilter) {
	if log::set_boxed_logger(Box::new(Registro { nivel_nosso })).is_ok() {
		log::set_max_level(nivel_nosso.max(LevelFilter::Warn));
	}
}

/// Para os testes: liga o log em "debug" e guarda as linhas para conferir.
pub fn iniciar_para_testes() {
	if let Ok(mut guarda) = captura().lock() {
		if guarda.is_none() {
			*guarda = Some(Vec::new());
		}
	}
	iniciar(LevelFilter::Debug);
}

/// As linhas guardadas desde `iniciar_para_testes` (todas as threads).
#[must_use]
pub fn capturado() -> Vec<String> {
	captura()
		.lock()
		.ok()
		.and_then(|g| g.clone())
		.unwrap_or_default()
}

/// Pânico vira uma linha curta e mascarada (nunca o valor inteiro).
pub fn instalar_gancho_de_panico() {
	std::panic::set_hook(Box::new(|info| {
		let lugar = info
			.location()
			.map(|l| format!("{}:{}", l.file(), l.line()))
			.unwrap_or_else(|| "?".to_owned());
		let mensagem = info
			.payload()
			.downcast_ref::<&str>()
			.map(|s| (*s).to_owned())
			.or_else(|| info.payload().downcast_ref::<String>().cloned())
			.unwrap_or_default();
		let curta: String = mascarar(&mensagem).chars().take(PANICO_MAX).collect();
		let _ = writeln!(std::io::stderr().lock(), "PÂNICO em {lugar}: {curta}");
	}));
}

#[cfg(test)]
mod testes {
	use super::mascarar;

	#[test]
	fn mascara_email_token_e_chaves() {
		let texto = "GET https://app.tuta.com/rest/sys/saltservice?_body=%7B%22419%22%3A%22a%40b.com%22%7D&accessToken=ZC2NIBDACUABAdJhibIwclzaPU3fEu-NzQ v=1";
		let m = mascarar(texto);
		assert!(!m.contains("ZC2NIB"), "{m}");
		assert!(!m.contains("%40b.com"), "{m}");
		assert!(m.contains("_body=***"), "{m}");
		assert!(m.contains("accessToken=***"), "{m}");
		assert_eq!(mascarar("de fulano.tal@uranyx.com.br"), "de <e-mail>");
		assert_eq!(mascarar("Authorization: Bearer abc.def"), "Authorization: Bearer ***");
		assert_eq!(
			mascarar("chave AZWEA/KTrHu0bW52CsctsBTTV4U3jrU51TadSxf6Nqs3"),
			"chave <…>"
		);
		assert_eq!(mascarar("pasta problema ml: 3 novos"), "pasta problema ml: 3 novos");
	}
}
