//! Entrar e sair da conta do Tuta (decisão 4). Código NOSSO, fora do SDK,
//! seguindo o LoginFacade.ts oficial (createSession, authenticateWithSecondFactor,
//! waitUntilSecondFactorApproved, cancelCreateSession) e o
//! UserController.deleteSessionSync (CloseSessionService):
//!
//!   1. SaltService (GET): o sal e o KDF da conta.
//!   2. Argon2id(senha, sal) → passphrase key (a função OFICIAL do SDK);
//!      authVerifier = base64url(sha256(passphrase key)) (função oficial).
//!      A senha nunca vai ao Tuta, só o verificador.
//!   3. SessionService (POST) com accessKey aleatória → access token.
//!   4. Se o Tuta pedir segundo fator: TOTP (SecondFactorAuthService POST) e
//!      espera a confirmação (GET). Só chave de segurança (U2F/WebAuthn) =
//!      erro claro. Desistiu/errou demais = a sessão pendente é cancelada.
//!   5. encrypted_passphrase_key = accessKey cifrando a passphrase key (a mesma
//!      conta do SDK oficial). O que fica guardado (no Chaveiro, por quem chama):
//!      user_id + access token + encrypted_passphrase_key.
//!
//! Nenhum segredo vai para log, erro ou tela: os erros dizem o QUE houve
//! (senha errada, Tuta fora…), nunca o valor.

use super::protocolo::{self, Desafio, IdSessao};
use crate::config;
use crate::segredo::{Segredo, SegredoBytes};
use crypto_primitives::aes::{Aes256Key, InitializationVector};
use crypto_primitives::key::GenericAesKey;
use crypto_primitives::randomizer_facade::RandomizerFacade;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tutasdk::bindings::rest_client::{
	encode_query_params, HttpMethod, RestClient, RestClientOptions, RestResponse,
};
use tutasdk::rest_error::HttpError;
use tutasdk::services::generated::sys::{
	CloseSessionService, SaltService, SecondFactorAuthService, SessionService,
};
use tutasdk::services::Service;

/// Por que não deu para entrar/sair. A mensagem é para o dono, em português.
#[derive(Debug, PartialEq, Eq)]
pub enum ErroSessao {
	EmailInvalido,
	SenhaOuEmail,
	ContaBcrypt,
	KdfDesconhecido(i64),
	SoChaveSeguranca,
	SegundoFatorDesconhecido,
	TotpErrado,
	Desistiu,
	SegundoFatorNaoConfirmou,
	AcessoBloqueado,
	MuitasTentativas { espera_s: Option<u64> },
	VersaoRecusada,
	TutaFora(u32),
	Rede,
	Protocolo(protocolo::ErroProtocolo),
	Recusado(u32),
}

impl std::fmt::Display for ErroSessao {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		match self {
			Self::EmailInvalido => f.write_str("e-mail inválido"),
			Self::SenhaOuEmail => f.write_str("o Tuta não aceitou o e-mail ou a senha"),
			Self::ContaBcrypt => f.write_str(
				"a conta ainda usa a proteção de senha antiga (Bcrypt); entre uma vez no app oficial do Tuta (ele migra para Argon2) e tente de novo",
			),
			Self::KdfDesconhecido(n) => write!(f, "o Tuta informou um tipo de proteção de senha desconhecido ({n})"),
			Self::SoChaveSeguranca => f.write_str(
				"a conta só tem chave de segurança (U2F/WebAuthn) como segundo fator; o conector precisa de um TOTP (app autenticador). Cadastre um em Configurações › Login › Segundo fator e rode `tuta-conector entrar` de novo",
			),
			Self::SegundoFatorDesconhecido => {
				f.write_str("o Tuta pediu um segundo fator que o conector não conhece")
			},
			Self::TotpErrado => f.write_str("o código do autenticador não foi aceito"),
			Self::Desistiu => f.write_str("login cancelado (a sessão pendente foi cancelada no Tuta)"),
			Self::SegundoFatorNaoConfirmou => {
				f.write_str("o Tuta não confirmou o segundo fator a tempo; tente de novo")
			},
			Self::AcessoBloqueado => f.write_str(
				"o Tuta bloqueou o acesso por um tempo (tentativas demais); espere e tente de novo",
			),
			Self::MuitasTentativas { espera_s } => match espera_s {
				Some(s) => write!(f, "o Tuta pediu para esperar {s} s antes de tentar de novo"),
				None => f.write_str("o Tuta pediu para esperar antes de tentar de novo"),
			},
			Self::VersaoRecusada => f.write_str(
				"o Tuta recusou a versão do SDK (474): é preciso atualizar o conector (LEIA-ME, \"Atualizar o SDK\")",
			),
			Self::TutaFora(status) => write!(f, "o Tuta está fora do ar ou com erro ({status})"),
			Self::Rede => f.write_str("sem conexão com o Tuta"),
			Self::Protocolo(e) => write!(f, "{e}"),
			Self::Recusado(status) => write!(f, "o Tuta recusou o pedido ({status})"),
		}
	}
}

impl std::error::Error for ErroSessao {}

impl From<protocolo::ErroProtocolo> for ErroSessao {
	fn from(e: protocolo::ErroProtocolo) -> Self {
		Self::Protocolo(e)
	}
}

/// Ritmo da espera pelo segundo fator (os testes encurtam).
#[derive(Clone, Copy, Debug)]
pub struct Ritmo {
	pub intervalo: Duration,
	pub espera_max: Duration,
}

impl Default for Ritmo {
	fn default() -> Self {
		Self {
			intervalo: config::SEGUNDO_FATOR_INTERVALO,
			espera_max: config::SEGUNDO_FATOR_ESPERA_MAX,
		}
	}
}

/// A sessão recém-criada (ainda não guardada).
pub struct NovaSessao {
	pub login: String,
	pub user_id: String,
	pub access_token: Segredo,
	pub encrypted_passphrase_key: SegredoBytes,
}

impl std::fmt::Debug for NovaSessao {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		f.write_str("NovaSessao(***)")
	}
}

/// Resultado de encerrar a sessão no Tuta.
#[derive(Debug, PartialEq, Eq)]
pub enum Encerramento {
	Encerrada,
	/// O Tuta disse que ela já não existia (401/404/440): já estava encerrada.
	JaEncerrada,
}

/// Fala com os serviços de login do Tuta.
pub struct ClienteLogin {
	base: String,
	rest: Arc<dyn RestClient>,
}

/// Erro de uma resposta HTTP do Tuta → ErroSessao.
fn classificar(r: &RestResponse) -> ErroSessao {
	match HttpError::from_http_response(r.status, &r.headers) {
		Ok(HttpError::NotAuthenticatedError) => ErroSessao::SenhaOuEmail,
		Ok(HttpError::AccessBlockedError | HttpError::AccessDeactivatedError) => {
			ErroSessao::AcessoBloqueado
		},
		Ok(HttpError::TooManyRequestsError { suspension_time_sec }) => ErroSessao::MuitasTentativas {
			espera_s: suspension_time_sec,
		},
		Ok(HttpError::InvalidSoftwareVersionError) => ErroSessao::VersaoRecusada,
		Ok(
			HttpError::InternalServerError
			| HttpError::BadGatewayError
			| HttpError::ServiceUnavailableError { .. }
			| HttpError::ConnectionError,
		) => ErroSessao::TutaFora(r.status),
		_ => ErroSessao::Recusado(r.status),
	}
}

impl ClienteLogin {
	/// `base` = "https://app.tuta.com" (ou o Tuta falso dos testes); `rest` já
	/// vem com a política de destinos (rede.rs).
	pub fn novo(base: &str, rest: Arc<dyn RestClient>) -> Self {
		Self {
			base: base.trim_end_matches('/').to_owned(),
			rest,
		}
	}

	async fn chamar<S: Service>(
		&self,
		metodo: HttpMethod,
		corpo: &Value,
	) -> Result<RestResponse, ErroSessao> {
		let mut url = format!("{}/rest/{}", self.base, S::PATH);
		let mut cabecalhos = HashMap::from([
			("cv".to_owned(), tutasdk::CLIENT_VERSION.to_owned()),
			("v".to_owned(), S::VERSION.to_string()),
		]);
		let corpo_http = if metodo == HttpMethod::GET {
			// Como o ServiceExecutor oficial: GET leva os dados em `_body`.
			url.push_str(&encode_query_params([("_body", corpo.to_string())]));
			None
		} else {
			cabecalhos.insert("Content-Type".to_owned(), "application/json".to_owned());
			Some(corpo.to_string().into_bytes())
		};
		self.rest
			.request_binary(
				url,
				metodo,
				RestClientOptions {
					headers: cabecalhos,
					body: corpo_http,
					suspension_behavior: None,
				},
			)
			.await
			.map_err(|_| ErroSessao::Rede)
	}

	/// Os passos 1 a 5 do topo. `pedir_totp(tentativa)` devolve o código
	/// digitado (None/vazio = desistir); `avisar` mostra uma linha ao dono.
	pub async fn criar_sessao(
		&self,
		email_digitado: &str,
		senha: &Segredo,
		nome_sessao: &str,
		pedir_totp: &mut dyn FnMut(u32) -> Option<String>,
		avisar: &mut dyn FnMut(&str),
		ritmo: Ritmo,
	) -> Result<NovaSessao, ErroSessao> {
		// O cliente oficial normaliza o e-mail (LoginFacade.ts: toLowerCase().trim()).
		let email = email_digitado.trim().to_lowercase();
		if !email.contains('@') || email.contains(char::is_whitespace) {
			return Err(ErroSessao::EmailInvalido);
		}

		let resposta = self
			.chamar::<SaltService>(HttpMethod::GET, &protocolo::salt_data(&email))
			.await?;
		if resposta.status != 200 {
			return Err(classificar(&resposta));
		}
		let salt = protocolo::ler_salt_return(resposta.body.as_deref().unwrap_or_default())?;
		match salt.kdf {
			protocolo::KDF_ARGON2ID => {},
			protocolo::KDF_BCRYPT => return Err(ErroSessao::ContaBcrypt),
			outro => return Err(ErroSessao::KdfDesconhecido(outro)),
		}

		let chave_senha = tutasdk::crypto::generate_key_from_passphrase(senha.expor(), salt.salt);
		let verificador =
			Segredo::novo(tutasdk::crypto::crypto_facade::create_auth_verifier(chave_senha.clone()));
		let aleatorio = RandomizerFacade::from_core(rand_core::OsRng);
		let chave_acesso = Aes256Key::generate(&aleatorio);

		let resposta = self
			.chamar::<SessionService>(
				HttpMethod::POST,
				&protocolo::create_session_data(
					&email,
					verificador.expor(),
					nome_sessao,
					chave_acesso.as_bytes(),
				),
			)
			.await?;
		if resposta.status != 200 {
			return Err(classificar(&resposta));
		}
		let criada =
			protocolo::ler_create_session_return(resposta.body.as_deref().unwrap_or_default())?;

		if !criada.desafios.is_empty() {
			self.segundo_fator(&criada.access_token, &criada.desafios, pedir_totp, avisar, ritmo)
				.await?;
		}

		let cifrada = GenericAesKey::Aes256(chave_acesso.clone()).encrypt_key(
			&GenericAesKey::Aes256(chave_senha.clone()),
			InitializationVector::generate(&aleatorio),
		);
		// Confere aqui mesmo que dá para voltar (o resume_session faz esta conta).
		// Se não der, a sessão já existe no Tuta: encerra antes de desistir.
		let volta_certa = GenericAesKey::Aes256(chave_acesso)
			.decrypt_aes_key(&cifrada)
			.is_ok_and(|volta| volta.as_bytes() == chave_senha.as_bytes());
		if !volta_certa {
			let _ = self.encerrar_sessao(&criada.access_token).await;
			return Err(ErroSessao::Protocolo(protocolo::ErroProtocolo("chave cifrada")));
		}

		Ok(NovaSessao {
			login: email,
			user_id: criada.user_id,
			access_token: criada.access_token,
			encrypted_passphrase_key: SegredoBytes::novo(cifrada),
		})
	}

	async fn segundo_fator(
		&self,
		access_token: &Segredo,
		desafios: &[Desafio],
		pedir_totp: &mut dyn FnMut(u32) -> Option<String>,
		avisar: &mut dyn FnMut(&str),
		ritmo: Ritmo,
	) -> Result<(), ErroSessao> {
		let sessao = protocolo::id_da_sessao(access_token)?;
		if !desafios.contains(&Desafio::Totp) {
			self.cancelar_pendente(&sessao).await;
			return Err(
				if desafios.iter().any(|d| matches!(d, Desafio::U2f | Desafio::Webauthn)) {
					ErroSessao::SoChaveSeguranca
				} else {
					ErroSessao::SegundoFatorDesconhecido
				},
			);
		}

		let mut aceito = false;
		for tentativa in 1..=config::TOTP_TENTATIVAS {
			let Some(digitado) = pedir_totp(tentativa) else {
				self.cancelar_pendente(&sessao).await;
				return Err(ErroSessao::Desistiu);
			};
			let limpo: String = digitado.chars().filter(|c| !c.is_whitespace()).collect();
			if limpo.is_empty() {
				self.cancelar_pendente(&sessao).await;
				return Err(ErroSessao::Desistiu);
			}
			let codigo = match limpo.parse::<u32>() {
				Ok(n) if limpo.len() == 6 => n,
				_ => {
					avisar("O código tem 6 números. Tente de novo.");
					continue;
				},
			};
			let resposta = self
				.chamar::<SecondFactorAuthService>(
					HttpMethod::POST,
					&protocolo::second_factor_totp(&sessao, codigo),
				)
				.await;
			match resposta {
				Ok(r) if r.status == 200 => {
					aceito = true;
					break;
				},
				// Como o SecondFactorAuthDialog oficial: 401 ou 400 = código errado.
				Ok(r) if matches!(r.status, 400 | 401) => {
					avisar("Código não aceito. Tente de novo.");
				},
				Ok(r) => {
					let erro = classificar(&r);
					self.cancelar_pendente(&sessao).await;
					return Err(erro);
				},
				Err(e) => {
					self.cancelar_pendente(&sessao).await;
					return Err(e);
				},
			}
		}
		if !aceito {
			self.cancelar_pendente(&sessao).await;
			return Err(ErroSessao::TotpErrado);
		}

		// Espera o Tuta liberar a sessão (waitUntilSecondFactorApproved).
		let inicio = tokio::time::Instant::now();
		let mut falhas_de_rede = 0;
		loop {
			let resposta = self
				.chamar::<SecondFactorAuthService>(
					HttpMethod::GET,
					&protocolo::second_factor_get(access_token),
				)
				.await;
			match resposta {
				Ok(r) if r.status == 200 => {
					if !protocolo::ler_segundo_fator_pendente(r.body.as_deref().unwrap_or_default())? {
						return Ok(());
					}
				},
				Ok(r) => {
					let erro = classificar(&r);
					self.cancelar_pendente(&sessao).await;
					return Err(erro);
				},
				// Como o oficial: até 10 falhas de conexão seguidas.
				Err(ErroSessao::Rede) if falhas_de_rede < 10 => falhas_de_rede += 1,
				Err(e) => {
					self.cancelar_pendente(&sessao).await;
					return Err(e);
				},
			}
			if inicio.elapsed() >= ritmo.espera_max {
				self.cancelar_pendente(&sessao).await;
				return Err(ErroSessao::SegundoFatorNaoConfirmou);
			}
			tokio::time::sleep(ritmo.intervalo).await;
		}
	}

	/// Desiste de uma sessão que esperava segundo fator (cancelCreateSession).
	/// Melhor esforço: se falhar, a sessão pendente expira sozinha no Tuta.
	async fn cancelar_pendente(&self, sessao: &IdSessao) {
		match self
			.chamar::<SecondFactorAuthService>(
				HttpMethod::DELETE,
				&protocolo::second_factor_delete(sessao),
			)
			.await
		{
			Ok(r) if matches!(r.status, 200 | 404 | 423) => {},
			Ok(r) => log::warn!("não consegui cancelar a sessão pendente no Tuta ({})", r.status),
			Err(_) => log::warn!("não consegui cancelar a sessão pendente no Tuta (sem conexão)"),
		}
	}

	/// Encerra a sessão NO TUTA (CloseSessionService), como o app oficial ao sair.
	pub async fn encerrar_sessao(&self, access_token: &Segredo) -> Result<Encerramento, ErroSessao> {
		let sessao = protocolo::id_da_sessao(access_token)?;
		let resposta = self
			.chamar::<CloseSessionService>(
				HttpMethod::POST,
				&protocolo::close_session(access_token, &sessao),
			)
			.await?;
		match resposta.status {
			200 | 204 => Ok(Encerramento::Encerrada),
			401 | 404 | 440 => Ok(Encerramento::JaEncerrada),
			_ => Err(classificar(&resposta)),
		}
	}
}
