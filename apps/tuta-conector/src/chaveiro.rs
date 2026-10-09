//! Chaveiro do macOS: o ÚNICO lugar onde ficam a sessão do Tuta e o token do
//! DaVinci (decisões 4 e 5).
//!
//! UM ITEM POR CONTA DO TUTA (`--conta geral|goslin`):
//! - Item "davinci-tuta-conector" / conta "sessao:<conta>": e-mail, user_id,
//!   access_token e encrypted_passphrase_key. Com esses três o Tuta abre a
//!   caixa inteira até a sessão ser encerrada; a passphrase key também prova a
//!   senha, então se o item vazar é preciso TROCAR A SENHA, não só sair.
//! - Item "davinci-tuta-conector-davinci" / conta "caixa:<conta>": URL do
//!   DaVinci, o id da caixa na Central de e-mail e a chave do agente dessa
//!   caixa (mostrada UMA vez na tela da Central).
//!
//! Nunca em arquivo, log, argumento de linha de comando ou variável de
//! ambiente. Gravado e lido pela API do Security.framework (o `security` de
//! linha de comando poria o segredo no argv). O FFI fica no crate
//! security-framework (o mesmo que o rustls do SDK já usa para as raízes TLS).

use crate::config::{self, Conta};
use crate::segredo::{Segredo, SegredoBytes};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fmt;
use std::sync::Mutex;
use zeroize::{Zeroize, Zeroizing};

#[derive(Debug)]
pub enum ErroCofre {
	/// O Chaveiro recusou (bloqueado, permissão negada…). Só o código do macOS.
	Chaveiro(i32),
	/// O item existe mas não é do formato esperado.
	Formato,
	/// Sistema sem Chaveiro do macOS.
	SemChaveiro,
}

impl fmt::Display for ErroCofre {
	fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
		match self {
			Self::Chaveiro(codigo) => write!(
				f,
				"o Chaveiro do macOS recusou (código {codigo}); se apareceu um pedido de permissão, clique em \"Permitir sempre\""
			),
			Self::Formato => f.write_str("o item do Chaveiro não está no formato esperado"),
			Self::SemChaveiro => f.write_str("este sistema não tem o Chaveiro do macOS"),
		}
	}
}

impl std::error::Error for ErroCofre {}

/// Onde os segredos moram. Produção = Chaveiro do macOS; testes = memória.
pub trait Cofre: Send + Sync {
	fn ler(&self, servico: &str, conta: &str) -> Result<Option<Zeroizing<Vec<u8>>>, ErroCofre>;
	fn gravar(&self, servico: &str, conta: &str, valor: &[u8]) -> Result<(), ErroCofre>;
	/// `true` se havia item para apagar.
	fn apagar(&self, servico: &str, conta: &str) -> Result<bool, ErroCofre>;
}

/// O Chaveiro de verdade (login keychain do usuário).
pub struct ChaveiroMac;

#[cfg(target_os = "macos")]
mod mac {
	use super::{ChaveiroMac, Cofre, ErroCofre};
	use security_framework::passwords;
	use zeroize::Zeroizing;

	/// errSecItemNotFound
	const NAO_ACHOU: i32 = -25300;

	impl Cofre for ChaveiroMac {
		fn ler(&self, servico: &str, conta: &str) -> Result<Option<Zeroizing<Vec<u8>>>, ErroCofre> {
			match passwords::get_generic_password(servico, conta) {
				Ok(valor) => Ok(Some(Zeroizing::new(valor))),
				Err(e) if e.code() == NAO_ACHOU => Ok(None),
				Err(e) => Err(ErroCofre::Chaveiro(e.code())),
			}
		}

		fn gravar(&self, servico: &str, conta: &str, valor: &[u8]) -> Result<(), ErroCofre> {
			passwords::set_generic_password(servico, conta, valor)
				.map_err(|e| ErroCofre::Chaveiro(e.code()))
		}

		fn apagar(&self, servico: &str, conta: &str) -> Result<bool, ErroCofre> {
			match passwords::delete_generic_password(servico, conta) {
				Ok(()) => Ok(true),
				Err(e) if e.code() == NAO_ACHOU => Ok(false),
				Err(e) => Err(ErroCofre::Chaveiro(e.code())),
			}
		}
	}
}

#[cfg(not(target_os = "macos"))]
impl Cofre for ChaveiroMac {
	fn ler(&self, _: &str, _: &str) -> Result<Option<Zeroizing<Vec<u8>>>, ErroCofre> {
		Err(ErroCofre::SemChaveiro)
	}
	fn gravar(&self, _: &str, _: &str, _: &[u8]) -> Result<(), ErroCofre> {
		Err(ErroCofre::SemChaveiro)
	}
	fn apagar(&self, _: &str, _: &str) -> Result<bool, ErroCofre> {
		Err(ErroCofre::SemChaveiro)
	}
}

/// Cofre em memória, para os testes (nunca toca o Chaveiro de verdade).
#[derive(Default)]
pub struct CofreMemoria {
	itens: Mutex<HashMap<(String, String), Vec<u8>>>,
}

impl CofreMemoria {
	/// Quantos itens há (os testes conferem que `sair` apaga).
	#[must_use]
	pub fn quantos(&self) -> usize {
		self.itens.lock().expect("cofre").len()
	}

	/// Os bytes crus guardados (os testes conferem o formato e a ausência de lixo).
	#[must_use]
	pub fn cru(&self, servico: &str, conta: &str) -> Option<Vec<u8>> {
		self.itens
			.lock()
			.expect("cofre")
			.get(&(servico.to_owned(), conta.to_owned()))
			.cloned()
	}
}

impl Cofre for CofreMemoria {
	fn ler(&self, servico: &str, conta: &str) -> Result<Option<Zeroizing<Vec<u8>>>, ErroCofre> {
		Ok(self.cru(servico, conta).map(Zeroizing::new))
	}
	fn gravar(&self, servico: &str, conta: &str, valor: &[u8]) -> Result<(), ErroCofre> {
		self.itens
			.lock()
			.expect("cofre")
			.insert((servico.to_owned(), conta.to_owned()), valor.to_vec());
		Ok(())
	}
	fn apagar(&self, servico: &str, conta: &str) -> Result<bool, ErroCofre> {
		Ok(self
			.itens
			.lock()
			.expect("cofre")
			.remove(&(servico.to_owned(), conta.to_owned()))
			.is_some())
	}
}

// ── A sessão do Tuta ────────────────────────────────────────────────────

/// O que o conector precisa para retomar a sessão (as Credentials do SDK).
pub struct SessaoTuta {
	/// O e-mail da conta (não é segredo, mas vai junto para conferir a conta).
	pub login: String,
	pub user_id: String,
	pub access_token: Segredo,
	pub encrypted_passphrase_key: SegredoBytes,
	/// Quando foi criada (ms desde 1970) e com que versão do SDK.
	pub criada_em_ms: u64,
	pub versao_sdk: String,
}

impl fmt::Debug for SessaoTuta {
	fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
		f.debug_struct("SessaoTuta")
			.field("login", &"***")
			.field("user_id", &"***")
			.field("access_token", &self.access_token)
			.field("encrypted_passphrase_key", &self.encrypted_passphrase_key)
			.field("criada_em_ms", &self.criada_em_ms)
			.field("versao_sdk", &self.versao_sdk)
			.finish()
	}
}

/// O formato dentro do item do Chaveiro (JSON; `v` = versão do formato).
#[derive(Serialize, Deserialize)]
struct SessaoNoChaveiro {
	v: u8,
	login: String,
	user_id: String,
	access_token: String,
	encrypted_passphrase_key: String,
	criada_em_ms: u64,
	versao_sdk: String,
}

impl Drop for SessaoNoChaveiro {
	fn drop(&mut self) {
		self.access_token.zeroize();
		self.encrypted_passphrase_key.zeroize();
		self.user_id.zeroize();
	}
}

impl SessaoTuta {
	/// As Credentials que o `Sdk::login` oficial recebe.
	#[must_use]
	pub fn credenciais(&self) -> tutasdk::login::Credentials {
		tutasdk::login::Credentials {
			login: self.login.clone(),
			user_id: tutasdk::GeneratedId(self.user_id.clone()),
			access_token: self.access_token.expor().to_owned(),
			encrypted_passphrase_key: self.encrypted_passphrase_key.expor().to_vec(),
			credential_type: tutasdk::login::CredentialType::Internal,
		}
	}
}

pub fn gravar_sessao(cofre: &dyn Cofre, conta: Conta, sessao: &SessaoTuta) -> Result<(), ErroCofre> {
	let guardada = SessaoNoChaveiro {
		v: 1,
		login: sessao.login.clone(),
		user_id: sessao.user_id.clone(),
		access_token: sessao.access_token.expor().to_owned(),
		encrypted_passphrase_key: BASE64.encode(sessao.encrypted_passphrase_key.expor()),
		criada_em_ms: sessao.criada_em_ms,
		versao_sdk: sessao.versao_sdk.clone(),
	};
	let bytes = Zeroizing::new(serde_json::to_vec(&guardada).map_err(|_| ErroCofre::Formato)?);
	cofre.gravar(config::CHAVEIRO_SERVICO_TUTA, &conta.chaveiro_sessao(), &bytes)
}

pub fn ler_sessao(cofre: &dyn Cofre, conta: Conta) -> Result<Option<SessaoTuta>, ErroCofre> {
	let Some(bytes) = cofre.ler(config::CHAVEIRO_SERVICO_TUTA, &conta.chaveiro_sessao())? else {
		return Ok(None);
	};
	let guardada: SessaoNoChaveiro =
		serde_json::from_slice(&bytes).map_err(|_| ErroCofre::Formato)?;
	if guardada.v != 1 {
		return Err(ErroCofre::Formato);
	}
	let chave = Zeroizing::new(
		BASE64
			.decode(guardada.encrypted_passphrase_key.as_bytes())
			.map_err(|_| ErroCofre::Formato)?,
	);
	Ok(Some(SessaoTuta {
		login: guardada.login.clone(),
		user_id: guardada.user_id.clone(),
		access_token: Segredo::novo(guardada.access_token.clone()),
		encrypted_passphrase_key: SegredoBytes::novo(chave.to_vec()),
		criada_em_ms: guardada.criada_em_ms,
		versao_sdk: guardada.versao_sdk.clone(),
	}))
}

pub fn apagar_sessao(cofre: &dyn Cofre, conta: Conta) -> Result<bool, ErroCofre> {
	cofre.apagar(config::CHAVEIRO_SERVICO_TUTA, &conta.chaveiro_sessao())
}

// ── O DaVinci (a caixa da Central) ──────────────────────────────────────

/// URL base do DaVinci (ex.: https://davinci.exemplo), o id da caixa na
/// Central de e-mail e a chave do agente dessa caixa.
pub struct AcessoDavinci {
	pub url: String,
	pub caixa: String,
	pub token: Segredo,
}

impl fmt::Debug for AcessoDavinci {
	fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
		f.debug_struct("AcessoDavinci")
			.field("url", &self.url)
			.field("caixa", &self.caixa)
			.field("token", &self.token)
			.finish()
	}
}

/// O formato dentro do item (v2 = com o id da caixa da Central).
#[derive(Serialize, Deserialize)]
struct DavinciNoChaveiro {
	v: u8,
	url: String,
	mailbox_id: String,
	token: String,
}

impl Drop for DavinciNoChaveiro {
	fn drop(&mut self) {
		self.token.zeroize();
	}
}

/// O id da caixa da Central: um UUID (só hexadecimal e hífen; vai na URL).
#[must_use]
pub fn caixa_valida(caixa: &str) -> bool {
	let c = caixa.trim();
	c.len() == 36
		&& c.chars().enumerate().all(|(i, ch)| {
			if matches!(i, 8 | 13 | 18 | 23) {
				ch == '-'
			} else {
				ch.is_ascii_hexdigit()
			}
		})
}

pub fn gravar_davinci(cofre: &dyn Cofre, conta: Conta, acesso: &AcessoDavinci) -> Result<(), ErroCofre> {
	if !caixa_valida(&acesso.caixa) {
		return Err(ErroCofre::Formato);
	}
	let guardado = DavinciNoChaveiro {
		v: 2,
		url: acesso.url.clone(),
		mailbox_id: acesso.caixa.trim().to_lowercase(),
		token: acesso.token.expor().to_owned(),
	};
	let bytes = Zeroizing::new(serde_json::to_vec(&guardado).map_err(|_| ErroCofre::Formato)?);
	cofre.gravar(config::CHAVEIRO_SERVICO_DAVINCI, &conta.chaveiro_caixa(), &bytes)
}

pub fn ler_davinci(cofre: &dyn Cofre, conta: Conta) -> Result<Option<AcessoDavinci>, ErroCofre> {
	let Some(bytes) = cofre.ler(config::CHAVEIRO_SERVICO_DAVINCI, &conta.chaveiro_caixa())? else {
		return Ok(None);
	};
	let guardado: DavinciNoChaveiro = serde_json::from_slice(&bytes).map_err(|_| ErroCofre::Formato)?;
	if guardado.v != 2 || !caixa_valida(&guardado.mailbox_id) {
		return Err(ErroCofre::Formato);
	}
	Ok(Some(AcessoDavinci {
		url: guardado.url.clone(),
		caixa: guardado.mailbox_id.clone(),
		token: Segredo::novo(guardado.token.clone()),
	}))
}

#[cfg(test)]
mod testes {
	use super::*;

	fn sessao() -> SessaoTuta {
		SessaoTuta {
			login: "conta@tuta.com".into(),
			user_id: "usuario123".into(),
			access_token: Segredo::novo("token-de-acesso".into()),
			encrypted_passphrase_key: SegredoBytes::novo(vec![9; 49]),
			criada_em_ms: 1_700_000_000_000,
			versao_sdk: "361.260929.0".into(),
		}
	}

	#[test]
	fn sessao_vai_e_volta_e_debug_nao_mostra_segredo() {
		let cofre = CofreMemoria::default();
		assert!(ler_sessao(&cofre, Conta::Geral).unwrap().is_none());
		gravar_sessao(&cofre, Conta::Geral, &sessao()).unwrap();
		// Cada conta tem o seu item: a da Goslin continua vazia.
		assert!(ler_sessao(&cofre, Conta::Goslin).unwrap().is_none());
		let lida = ler_sessao(&cofre, Conta::Geral).unwrap().unwrap();
		assert_eq!(lida.access_token.expor(), "token-de-acesso");
		assert_eq!(lida.encrypted_passphrase_key.expor(), &[9; 49]);
		let credenciais = lida.credenciais();
		assert_eq!(credenciais.user_id.0, "usuario123");
		let debug = format!("{lida:?}");
		assert!(!debug.contains("token-de-acesso"));
		assert!(!debug.contains("usuario123"));
		assert!(!debug.contains("conta@tuta.com"));
		assert!(apagar_sessao(&cofre, Conta::Geral).unwrap());
		assert!(!apagar_sessao(&cofre, Conta::Geral).unwrap());
		assert_eq!(cofre.quantos(), 0);
	}

	#[test]
	fn formato_estranho_vira_erro_sem_ecoar() {
		let cofre = CofreMemoria::default();
		cofre
			.gravar(config::CHAVEIRO_SERVICO_TUTA, "sessao:geral", b"{\"v\":2}")
			.unwrap();
		assert!(matches!(ler_sessao(&cofre, Conta::Geral), Err(ErroCofre::Formato)));
	}

	#[test]
	fn davinci_vai_e_volta_por_conta() {
		let cofre = CofreMemoria::default();
		let caixa = "0b4e6a52-3f1d-4c55-9e0a-2a9c1d7e8f10";
		gravar_davinci(
			&cofre,
			Conta::Geral,
			&AcessoDavinci {
				url: "https://davinci.exemplo".into(),
				caixa: caixa.to_uppercase(),
				token: Segredo::novo("abc".into()),
			},
		)
		.unwrap();
		let lido = ler_davinci(&cofre, Conta::Geral).unwrap().unwrap();
		assert_eq!(lido.url, "https://davinci.exemplo");
		assert_eq!(lido.caixa, caixa);
		assert_eq!(lido.token.expor(), "abc");
		assert!(!format!("{lido:?}").contains("abc"));
		assert!(ler_davinci(&cofre, Conta::Goslin).unwrap().is_none());
		assert!(cofre.cru(config::CHAVEIRO_SERVICO_DAVINCI, "caixa:geral").is_some());
		// O id da caixa vai na URL: só UUID.
		for ruim in ["../x", "0b4e6a52-3f1d-4c55-9e0a-2a9c1d7e8f1", "0b4e6a52/3f1d-4c55-9e0a-2a9c1d7e8f10"] {
			let r = gravar_davinci(
				&cofre,
				Conta::Goslin,
				&AcessoDavinci {
					url: "https://davinci.exemplo".into(),
					caixa: ruim.into(),
					token: Segredo::novo("abc".into()),
				},
			);
			assert!(matches!(r, Err(ErroCofre::Formato)), "{ruim}");
		}
		// O formato antigo (v1, sem caixa) não serve: configurar de novo.
		cofre
			.gravar(config::CHAVEIRO_SERVICO_DAVINCI, "caixa:goslin", br#"{"v":1,"url":"https://x","token":"t"}"#)
			.unwrap();
		assert!(matches!(ler_davinci(&cofre, Conta::Goslin), Err(ErroCofre::Formato)));
	}

	/// Toca o Chaveiro DE VERDADE (cria e apaga um item de teste). Não roda no
	/// `cargo test` normal; para rodar à mão: `cargo test -- --ignored chaveiro_real`.
	#[test]
	#[ignore = "mexe no Chaveiro do usuário; só à mão"]
	fn chaveiro_real_cria_le_e_apaga() {
		let servico = "davinci-tuta-conector-TESTE";
		let cofre = ChaveiroMac;
		cofre.gravar(servico, "teste", b"valor").unwrap();
		assert_eq!(cofre.ler(servico, "teste").unwrap().unwrap().as_slice(), b"valor");
		assert!(cofre.apagar(servico, "teste").unwrap());
		assert!(cofre.ler(servico, "teste").unwrap().is_none());
	}
}
