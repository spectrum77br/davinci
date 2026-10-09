//! O SDK oficial do Tuta, montado do jeito do conector:
//!
//! - o RestClient é o nosso `ClienteRest` (destinos permitidos + teto de tempo);
//! - SEM o "suspendable" do SDK: um 429/503 volta para nós na hora e vira o
//!   estado `limitado`/`tuta_fora` com recuo nosso (não fica preso lá dentro);
//! - o FileClient guarda em MEMÓRIA (o SDK só usa para o modelo de tipos do
//!   servidor; nada de e-mail vai para disco por aqui);
//! - todo uso que pode entrar em pânico (o SDK tem `.expect()`/`unwrap()`/
//!   `panic!` em respostas do servidor) passa por `sem_panico`.
//!
//! Não existe websocket/event bus no SDK oficial e o conector não cria um:
//! assim ele nunca vira "líder" e nunca segura as regras de pasta da equipe.

use crate::estado::EstadoConector;
use crate::registro::mascarar;
use futures::FutureExt;
use std::collections::HashMap;
use std::future::Future;
use std::panic::AssertUnwindSafe;
use std::sync::{Arc, Mutex};
use tutasdk::bindings::file_client::{FileClient, FileClientError};
use tutasdk::bindings::rest_client::{RestClient, RestClientError};
use tutasdk::login::{Credentials, LoginError};
use tutasdk::rest_error::HttpError;
use tutasdk::{ApiCallError, LoggedInSdk, Sdk};

/// FileClient só em memória (some quando o processo termina).
#[derive(Default)]
pub struct ArquivosEmMemoria {
	itens: Mutex<HashMap<String, Vec<u8>>>,
}

#[async_trait::async_trait]
impl FileClient for ArquivosEmMemoria {
	async fn persist_content(&self, name: String, content: Vec<u8>) -> Result<(), FileClientError> {
		self.itens
			.lock()
			.map_err(|_| FileClientError::Unknown)?
			.insert(name, content);
		Ok(())
	}

	async fn read_content(&self, name: String) -> Result<Vec<u8>, FileClientError> {
		self.itens
			.lock()
			.map_err(|_| FileClientError::Unknown)?
			.get(&name)
			.cloned()
			.ok_or(FileClientError::NotFound)
	}
}

/// Um `Sdk` (ainda sem login) falando com `base` pelo nosso cliente.
#[must_use]
pub fn novo_sdk(base: &str, rest: Arc<dyn RestClient>) -> Sdk {
	Sdk::new_without_suspension(
		base.trim_end_matches('/').to_owned(),
		rest,
		Arc::new(ArquivosEmMemoria::default()),
	)
}

/// Um pânico dentro do SDK, já isolado (só o lugar/mensagem mascarada).
#[derive(Debug, PartialEq, Eq)]
pub struct Panico(pub String);

/// Roda `f` e transforma um pânico em `Err(Panico)` em vez de derrubar o
/// processo. Usar em TODA chamada ao SDK que processa resposta do servidor.
pub async fn sem_panico<T, F>(f: F) -> Result<T, Panico>
where
	F: Future<Output = T>,
{
	AssertUnwindSafe(f).catch_unwind().await.map_err(|carga| {
		let texto = carga
			.downcast_ref::<&str>()
			.map(|s| (*s).to_owned())
			.or_else(|| carga.downcast_ref::<String>().cloned())
			.unwrap_or_default();
		Panico(mascarar(&texto).chars().take(200).collect())
	})
}

/// Como uma falha do SDK se traduz no estado do pulso.
#[must_use]
pub fn estado_do_erro(erro: &ApiCallError) -> EstadoConector {
	match erro {
		ApiCallError::ServerResponseError { source } => match source {
			HttpError::NotAuthenticatedError
			| HttpError::SessionExpiredError
			| HttpError::AccessDeactivatedError
			| HttpError::AccessExpiredError
			| HttpError::AccessBlockedError => EstadoConector::SessaoCaiu,
			HttpError::InvalidSoftwareVersionError => EstadoConector::VersaoRecusada,
			HttpError::TooManyRequestsError { .. } => EstadoConector::Limitado,
			HttpError::InternalServerError
			| HttpError::BadGatewayError
			| HttpError::ServiceUnavailableError { .. }
			| HttpError::ConnectionError
			| HttpError::RequestTimeoutError => EstadoConector::TutaFora,
			_ => EstadoConector::Erro,
		},
		ApiCallError::RestClient { source } => match source {
			RestClientError::NetworkError
			| RestClientError::FailedHandshake
			| RestClientError::Suspended => EstadoConector::TutaFora,
			_ => EstadoConector::Erro,
		},
		ApiCallError::InternalSdkError { .. } => EstadoConector::Erro,
	}
}

/// Por que não deu para retomar a sessão guardada.
#[derive(Debug)]
pub enum ErroRetomar {
	/// Sessão encerrada/expirada/inválida: precisa `tuta-conector entrar`.
	SessaoCaiu,
	VersaoRecusada,
	Limitado,
	TutaFora,
	/// O SDK entrou em pânico com a resposta do servidor.
	Panico(Panico),
	/// Outra falha do SDK (mensagem mascarada).
	Outro(String),
}

impl ErroRetomar {
	#[must_use]
	pub fn estado(&self) -> EstadoConector {
		match self {
			Self::SessaoCaiu => EstadoConector::SessaoCaiu,
			Self::VersaoRecusada => EstadoConector::VersaoRecusada,
			Self::Limitado => EstadoConector::Limitado,
			Self::TutaFora => EstadoConector::TutaFora,
			Self::Panico(_) | Self::Outro(_) => EstadoConector::Erro,
		}
	}
}

impl std::fmt::Display for ErroRetomar {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		match self {
			Self::SessaoCaiu => f.write_str("a sessão do Tuta foi encerrada ou expirou: rode `tuta-conector entrar`"),
			Self::VersaoRecusada => f.write_str("o Tuta recusou a versão do SDK (474): atualize o conector"),
			Self::Limitado => f.write_str("o Tuta pediu para esperar (429)"),
			Self::TutaFora => f.write_str("o Tuta está fora do ar ou sem conexão"),
			Self::Panico(p) => write!(f, "o SDK falhou com a resposta do Tuta ({})", p.0),
			Self::Outro(m) => write!(f, "falha do SDK: {m}"),
		}
	}
}

fn erro_de_login(erro: &LoginError) -> ErroRetomar {
	match erro {
		LoginError::InvalidAccessToken { .. }
		| LoginError::InvalidPassphrase { .. }
		| LoginError::InvalidKey { .. } => ErroRetomar::SessaoCaiu,
		LoginError::ApiCall { source } => match estado_do_erro(source) {
			EstadoConector::SessaoCaiu => ErroRetomar::SessaoCaiu,
			EstadoConector::VersaoRecusada => ErroRetomar::VersaoRecusada,
			EstadoConector::Limitado => ErroRetomar::Limitado,
			EstadoConector::TutaFora => ErroRetomar::TutaFora,
			_ => ErroRetomar::Outro(mascarar(&source.to_string())),
		},
	}
}

/// Retoma a sessão guardada com o `Sdk::login` OFICIAL (só leitura: carrega a
/// Session e o User e destrava a chave do grupo; não grava nada no Tuta).
pub async fn retomar(sdk: &Sdk, credenciais: Credentials) -> Result<Arc<LoggedInSdk>, ErroRetomar> {
	match sem_panico(sdk.login(credenciais)).await {
		Ok(Ok(logado)) => Ok(logado),
		Ok(Err(e)) => Err(erro_de_login(&e)),
		Err(p) => Err(ErroRetomar::Panico(p)),
	}
}

#[cfg(test)]
mod testes {
	use super::*;

	#[tokio::test]
	async fn panico_vira_erro_mascarado() {
		let r: Result<(), Panico> = sem_panico(async {
			panic!("valor estranho do servidor: fulano@uranyx.com.br");
		})
		.await;
		let Err(Panico(texto)) = r else {
			panic!("deveria ter isolado o pânico")
		};
		assert!(texto.contains("valor estranho"));
		assert!(!texto.contains("uranyx"));
	}

	#[test]
	fn erros_do_sdk_viram_estados() {
		let e = |s| ApiCallError::ServerResponseError { source: s };
		assert_eq!(estado_do_erro(&e(HttpError::NotAuthenticatedError)), EstadoConector::SessaoCaiu);
		assert_eq!(
			estado_do_erro(&e(HttpError::InvalidSoftwareVersionError)),
			EstadoConector::VersaoRecusada
		);
		assert_eq!(
			estado_do_erro(&e(HttpError::TooManyRequestsError {
				suspension_time_sec: Some(5)
			})),
			EstadoConector::Limitado
		);
		assert_eq!(estado_do_erro(&e(HttpError::BadGatewayError)), EstadoConector::TutaFora);
		assert_eq!(
			estado_do_erro(&ApiCallError::RestClient {
				source: RestClientError::NetworkError
			}),
			EstadoConector::TutaFora
		);
	}

	#[tokio::test]
	async fn arquivos_ficam_so_em_memoria() {
		let a = ArquivosEmMemoria::default();
		assert_eq!(a.read_content("x".into()).await, Err(FileClientError::NotFound));
		a.persist_content("x".into(), vec![1]).await.unwrap();
		assert_eq!(a.read_content("x".into()).await.unwrap(), vec![1]);
	}
}
