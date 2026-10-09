//! O formato de rede dos serviços de LOGIN do Tuta (sys/saltservice,
//! sys/sessionservice, sys/secondfactorauthservice, sys/closesessionservice).
//!
//! Por que nosso e não o do SDK: o SDK em Rust só RETOMA sessão; criar sessão
//! com segundo fator exige o executor de serviços e o serializador, que são
//! privados no SDK (lib.rs: `HeadersProvider::new` e `json_serializer`). Em vez
//! de remendar o SDK (o 08-interactive-session da tutabridge), escrevemos aqui
//! só estes 9 tipos, com os ids de atributo do modelo `sys` da release fixada.
//! O teste `ids_batem_com_o_modelo_do_sdk` confere CADA id e o conjunto
//! completo de atributos contra o modelo embutido no SDK: se o Tuta mudar
//! algo numa release nova, o teste quebra antes de qualquer login.
//!
//! O formato (o mesmo do serializador do SDK, json_serializer.rs): objeto com
//! o id do atributo como chave; número e data como texto; bytes em base64;
//! booleano "0"/"1"; opcional vazio = null; agregado e associação = lista.

use crate::segredo::Segredo;
use base64::engine::general_purpose::{STANDARD as BASE64, URL_SAFE_NO_PAD};
use base64::Engine;
use serde_json::{json, Map, Value};

/// Ids dos atributos (modelo sys da 361: versão 156).
pub mod ids {
	pub const SALT_DATA_FORMAT: &str = "418";
	pub const SALT_DATA_MAIL_ADDRESS: &str = "419";

	pub const SALT_RETURN_FORMAT: &str = "421";
	pub const SALT_RETURN_SALT: &str = "422";
	pub const SALT_RETURN_KDF_VERSION: &str = "2133";

	pub const CREATE_SESSION_DATA_FORMAT: &str = "1212";
	pub const CREATE_SESSION_DATA_MAIL_ADDRESS: &str = "1213";
	pub const CREATE_SESSION_DATA_AUTH_VERIFIER: &str = "1214";
	pub const CREATE_SESSION_DATA_CLIENT_IDENTIFIER: &str = "1215";
	pub const CREATE_SESSION_DATA_ACCESS_KEY: &str = "1216";
	pub const CREATE_SESSION_DATA_AUTH_TOKEN: &str = "1217";
	pub const CREATE_SESSION_DATA_RECOVER_CODE_VERIFIER: &str = "1417";
	pub const CREATE_SESSION_DATA_USER: &str = "1218";

	pub const CREATE_SESSION_RETURN_FORMAT: &str = "1220";
	pub const CREATE_SESSION_RETURN_ACCESS_TOKEN: &str = "1221";
	pub const CREATE_SESSION_RETURN_CHALLENGES: &str = "1222";
	pub const CREATE_SESSION_RETURN_USER: &str = "1223";

	pub const CHALLENGE_ID: &str = "1188";
	pub const CHALLENGE_TYPE: &str = "1189";
	pub const CHALLENGE_U2F: &str = "1190";
	pub const CHALLENGE_OTP: &str = "1247";

	pub const SECOND_FACTOR_AUTH_DATA_FORMAT: &str = "542";
	pub const SECOND_FACTOR_AUTH_DATA_TYPE: &str = "1230";
	pub const SECOND_FACTOR_AUTH_DATA_OTP_CODE: &str = "1243";
	pub const SECOND_FACTOR_AUTH_DATA_U2F: &str = "1231";
	pub const SECOND_FACTOR_AUTH_DATA_SESSION: &str = "1232";
	pub const SECOND_FACTOR_AUTH_DATA_WEBAUTHN: &str = "1905";

	pub const SECOND_FACTOR_AUTH_GET_DATA_FORMAT: &str = "1234";
	pub const SECOND_FACTOR_AUTH_GET_DATA_ACCESS_TOKEN: &str = "1235";

	pub const SECOND_FACTOR_AUTH_GET_RETURN_FORMAT: &str = "1237";
	pub const SECOND_FACTOR_AUTH_GET_RETURN_PENDING: &str = "1238";

	pub const SECOND_FACTOR_AUTH_DELETE_DATA_FORMAT: &str = "1756";
	pub const SECOND_FACTOR_AUTH_DELETE_DATA_SESSION: &str = "1757";

	pub const CLOSE_SESSION_POST_FORMAT: &str = "1596";
	pub const CLOSE_SESSION_POST_ACCESS_TOKEN: &str = "1597";
	pub const CLOSE_SESSION_POST_SESSION_ID: &str = "1598";
}

/// Tipos de segundo fator (TutanotaConstants.ts `SecondFactorType`).
pub const SEGUNDO_FATOR_U2F: i64 = 0;
pub const SEGUNDO_FATOR_TOTP: i64 = 1;
pub const SEGUNDO_FATOR_WEBAUTHN: i64 = 2;

/// KDF da senha (SaltReturn.kdfVersion): 0 Bcrypt (antigo), 1 Argon2id.
pub const KDF_BCRYPT: i64 = 0;
pub const KDF_ARGON2ID: i64 = 1;

/// Tamanho do id gerado do Tuta em bytes (o começo do access token).
const ID_GERADO_BYTES: usize = 9;

#[derive(Debug, PartialEq, Eq)]
pub struct ErroProtocolo(pub &'static str);

impl std::fmt::Display for ErroProtocolo {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		write!(f, "resposta do Tuta fora do formato esperado ({})", self.0)
	}
}

fn objeto(pares: Vec<(&str, Value)>) -> Value {
	let mut mapa = Map::new();
	for (chave, valor) in pares {
		mapa.insert(chave.to_owned(), valor);
	}
	Value::Object(mapa)
}

/// SaltData — vai no `_body` do GET (como faz o cliente oficial).
#[must_use]
pub fn salt_data(email: &str) -> Value {
	objeto(vec![
		(ids::SALT_DATA_FORMAT, json!("0")),
		(ids::SALT_DATA_MAIL_ADDRESS, json!(email)),
	])
}

/// CreateSessionData de uma sessão PERSISTENTE (com accessKey), como o
/// LoginFacade.createSession oficial com SessionType.Persistent.
#[must_use]
pub fn create_session_data(
	email: &str,
	auth_verifier: &str,
	nome_sessao: &str,
	access_key: &[u8],
) -> Value {
	objeto(vec![
		(ids::CREATE_SESSION_DATA_FORMAT, json!("0")),
		(ids::CREATE_SESSION_DATA_MAIL_ADDRESS, json!(email)),
		(ids::CREATE_SESSION_DATA_AUTH_VERIFIER, json!(auth_verifier)),
		(ids::CREATE_SESSION_DATA_CLIENT_IDENTIFIER, json!(nome_sessao)),
		(ids::CREATE_SESSION_DATA_ACCESS_KEY, json!(BASE64.encode(access_key))),
		(ids::CREATE_SESSION_DATA_AUTH_TOKEN, Value::Null),
		(ids::CREATE_SESSION_DATA_RECOVER_CODE_VERIFIER, Value::Null),
		(ids::CREATE_SESSION_DATA_USER, json!([])),
	])
}

/// SecondFactorAuthData com o código TOTP (SecondFactorAuthDialog.onConfirmOtp).
#[must_use]
pub fn second_factor_totp(sessao: &IdSessao, codigo: u32) -> Value {
	objeto(vec![
		(ids::SECOND_FACTOR_AUTH_DATA_FORMAT, json!("0")),
		(ids::SECOND_FACTOR_AUTH_DATA_TYPE, json!(SEGUNDO_FATOR_TOTP.to_string())),
		(ids::SECOND_FACTOR_AUTH_DATA_OTP_CODE, json!(codigo.to_string())),
		(ids::SECOND_FACTOR_AUTH_DATA_U2F, json!([])),
		(ids::SECOND_FACTOR_AUTH_DATA_SESSION, json!([[sessao.lista, sessao.elemento]])),
		(ids::SECOND_FACTOR_AUTH_DATA_WEBAUTHN, json!([])),
	])
}

/// SecondFactorAuthGetData — "o segundo fator ainda está pendente?".
#[must_use]
pub fn second_factor_get(access_token: &Segredo) -> Value {
	objeto(vec![
		(ids::SECOND_FACTOR_AUTH_GET_DATA_FORMAT, json!("0")),
		(ids::SECOND_FACTOR_AUTH_GET_DATA_ACCESS_TOKEN, json!(access_token.expor())),
	])
}

/// SecondFactorAuthDeleteData — desiste da sessão pendente (cancelCreateSession).
#[must_use]
pub fn second_factor_delete(sessao: &IdSessao) -> Value {
	objeto(vec![
		(ids::SECOND_FACTOR_AUTH_DELETE_DATA_FORMAT, json!("0")),
		(ids::SECOND_FACTOR_AUTH_DELETE_DATA_SESSION, json!([[sessao.lista, sessao.elemento]])),
	])
}

/// CloseSessionServicePost — encerra a sessão no Tuta (o mesmo corpo que o
/// UserController.deleteSessionSync oficial manda).
#[must_use]
pub fn close_session(access_token: &Segredo, sessao: &IdSessao) -> Value {
	objeto(vec![
		(ids::CLOSE_SESSION_POST_FORMAT, json!("0")),
		(ids::CLOSE_SESSION_POST_ACCESS_TOKEN, json!(access_token.expor())),
		(ids::CLOSE_SESSION_POST_SESSION_ID, json!([[sessao.lista, sessao.elemento]])),
	])
}

fn ler_objeto(bytes: &[u8]) -> Result<Map<String, Value>, ErroProtocolo> {
	match serde_json::from_slice::<Value>(bytes) {
		Ok(Value::Object(m)) => Ok(m),
		_ => Err(ErroProtocolo("não é um objeto JSON")),
	}
}

fn texto<'a>(m: &'a Map<String, Value>, id: &'static str) -> Result<&'a str, ErroProtocolo> {
	m.get(id).and_then(Value::as_str).ok_or(ErroProtocolo(id))
}

fn numero(m: &Map<String, Value>, id: &'static str) -> Result<i64, ErroProtocolo> {
	texto(m, id)?.parse::<i64>().map_err(|_| ErroProtocolo(id))
}

/// SaltReturn.
#[derive(Debug, PartialEq, Eq)]
pub struct Salt {
	pub salt: [u8; 16],
	pub kdf: i64,
}

pub fn ler_salt_return(bytes: &[u8]) -> Result<Salt, ErroProtocolo> {
	let m = ler_objeto(bytes)?;
	let salt = BASE64
		.decode(texto(&m, ids::SALT_RETURN_SALT)?)
		.map_err(|_| ErroProtocolo(ids::SALT_RETURN_SALT))?;
	let salt: [u8; 16] = salt
		.try_into()
		.map_err(|_| ErroProtocolo(ids::SALT_RETURN_SALT))?;
	Ok(Salt {
		salt,
		kdf: numero(&m, ids::SALT_RETURN_KDF_VERSION)?,
	})
}

/// Um desafio de segundo fator (Challenge.type).
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Desafio {
	Totp,
	U2f,
	Webauthn,
	Outro(i64),
}

/// CreateSessionReturn.
pub struct SessaoCriada {
	pub access_token: Segredo,
	pub user_id: String,
	pub desafios: Vec<Desafio>,
}

pub fn ler_create_session_return(bytes: &[u8]) -> Result<SessaoCriada, ErroProtocolo> {
	let m = ler_objeto(bytes)?;
	let access_token = texto(&m, ids::CREATE_SESSION_RETURN_ACCESS_TOKEN)?;
	if access_token.is_empty() {
		return Err(ErroProtocolo(ids::CREATE_SESSION_RETURN_ACCESS_TOKEN));
	}
	// ELEMENT_ASSOCIATION One: o id do usuário (o serializador do SDK aceita a lista).
	let user_id = match m.get(ids::CREATE_SESSION_RETURN_USER) {
		Some(Value::String(s)) => s.clone(),
		Some(Value::Array(v)) if v.len() == 1 => v[0]
			.as_str()
			.ok_or(ErroProtocolo(ids::CREATE_SESSION_RETURN_USER))?
			.to_owned(),
		_ => return Err(ErroProtocolo(ids::CREATE_SESSION_RETURN_USER)),
	};
	let desafios = match m.get(ids::CREATE_SESSION_RETURN_CHALLENGES) {
		Some(Value::Array(lista)) => lista
			.iter()
			.map(|d| {
				let tipo = d
					.as_object()
					.ok_or(ErroProtocolo(ids::CREATE_SESSION_RETURN_CHALLENGES))
					.and_then(|o| numero(o, ids::CHALLENGE_TYPE))?;
				Ok(match tipo {
					SEGUNDO_FATOR_TOTP => Desafio::Totp,
					SEGUNDO_FATOR_U2F => Desafio::U2f,
					SEGUNDO_FATOR_WEBAUTHN => Desafio::Webauthn,
					outro => Desafio::Outro(outro),
				})
			})
			.collect::<Result<Vec<_>, _>>()?,
		None | Some(Value::Null) => Vec::new(),
		_ => return Err(ErroProtocolo(ids::CREATE_SESSION_RETURN_CHALLENGES)),
	};
	Ok(SessaoCriada {
		access_token: Segredo::novo(access_token.to_owned()),
		user_id,
		desafios,
	})
}

/// SecondFactorAuthGetReturn.secondFactorPending.
pub fn ler_segundo_fator_pendente(bytes: &[u8]) -> Result<bool, ErroProtocolo> {
	let m = ler_objeto(bytes)?;
	match texto(&m, ids::SECOND_FACTOR_AUTH_GET_RETURN_PENDING)? {
		"0" => Ok(false),
		"1" => Ok(true),
		_ => Err(ErroProtocolo(ids::SECOND_FACTOR_AUTH_GET_RETURN_PENDING)),
	}
}

/// O id da sessão a partir do access token (LoginFacade.getSessionListId e
/// getSessionElementId): lista = base64ext dos 9 primeiros bytes; elemento =
/// base64url(sha256(resto)).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IdSessao {
	pub lista: String,
	pub elemento: String,
}

pub fn id_da_sessao(access_token: &Segredo) -> Result<IdSessao, ErroProtocolo> {
	let bytes = zeroize::Zeroizing::new(
		URL_SAFE_NO_PAD
			.decode(access_token.expor())
			.map_err(|_| ErroProtocolo("accessToken"))?,
	);
	if bytes.len() <= ID_GERADO_BYTES {
		return Err(ErroProtocolo("accessToken"));
	}
	let (lista, resto) = bytes.split_at(ID_GERADO_BYTES);
	Ok(IdSessao {
		lista: tutasdk::util::BASE64_EXT.encode(lista),
		elemento: URL_SAFE_NO_PAD.encode(tutasdk::crypto::sha256(resto)),
	})
}

#[cfg(test)]
mod testes {
	use super::*;
	use std::collections::BTreeSet;
	use std::sync::Arc;
	use tutasdk::entities::generated::sys::{
		Challenge, CloseSessionServicePost, CreateSessionData, CreateSessionReturn, SaltData,
		SaltReturn, SecondFactorAuthData, SecondFactorAuthDeleteData, SecondFactorAuthGetData,
		SecondFactorAuthGetReturn,
	};
	use tutasdk::entities::Entity;
	use tutasdk::type_model_provider::TypeModelProvider;

	/// (tipo, [(nome do atributo, id que usamos)]) — o conjunto tem de ser COMPLETO.
	fn conferir<E: Entity>(provedor: &TypeModelProvider, esperado: &[(&str, &str)]) {
		let modelo = provedor
			.resolve_client_type_ref(&E::type_ref())
			.expect("tipo no modelo do SDK");
		for (nome, id) in esperado {
			assert_eq!(
				modelo.get_attribute_id_by_attribute_name(nome).unwrap(),
				*id,
				"{}.{nome}",
				modelo.name
			);
		}
		let do_modelo: BTreeSet<String> = modelo
			.values
			.values()
			.map(|v| v.name.clone())
			.chain(modelo.associations.values().map(|a| a.name.clone()))
			.collect();
		let nossos: BTreeSet<String> = esperado.iter().map(|(n, _)| (*n).to_owned()).collect();
		// _id dos agregados não vai nos nossos pedidos de serviço.
		let do_modelo: BTreeSet<String> = do_modelo.into_iter().filter(|n| n != "_id").collect();
		assert_eq!(do_modelo, nossos, "atributos de {}", modelo.name);
	}

	struct SemArquivo;
	#[async_trait::async_trait]
	impl tutasdk::bindings::file_client::FileClient for SemArquivo {
		async fn persist_content(
			&self,
			_: String,
			_: Vec<u8>,
		) -> Result<(), tutasdk::bindings::file_client::FileClientError> {
			Ok(())
		}
		async fn read_content(
			&self,
			_: String,
		) -> Result<Vec<u8>, tutasdk::bindings::file_client::FileClientError> {
			Err(tutasdk::bindings::file_client::FileClientError::NotFound)
		}
	}

	struct SemRede;
	#[async_trait::async_trait]
	impl tutasdk::bindings::rest_client::RestClient for SemRede {
		async fn request_binary(
			&self,
			_: String,
			_: tutasdk::bindings::rest_client::HttpMethod,
			_: tutasdk::bindings::rest_client::RestClientOptions,
		) -> Result<tutasdk::bindings::rest_client::RestResponse, tutasdk::bindings::rest_client::RestClientError>
		{
			Err(tutasdk::bindings::rest_client::RestClientError::NetworkError)
		}
	}

	#[test]
	fn ids_batem_com_o_modelo_do_sdk() {
		use ids::*;
		let provedor =
			TypeModelProvider::new(Arc::new(SemRede), Arc::new(SemArquivo), "http://x".into());
		conferir::<SaltData>(
			&provedor,
			&[("_format", SALT_DATA_FORMAT), ("mailAddress", SALT_DATA_MAIL_ADDRESS)],
		);
		conferir::<SaltReturn>(
			&provedor,
			&[
				("_format", SALT_RETURN_FORMAT),
				("salt", SALT_RETURN_SALT),
				("kdfVersion", SALT_RETURN_KDF_VERSION),
			],
		);
		conferir::<CreateSessionData>(
			&provedor,
			&[
				("_format", CREATE_SESSION_DATA_FORMAT),
				("mailAddress", CREATE_SESSION_DATA_MAIL_ADDRESS),
				("authVerifier", CREATE_SESSION_DATA_AUTH_VERIFIER),
				("clientIdentifier", CREATE_SESSION_DATA_CLIENT_IDENTIFIER),
				("accessKey", CREATE_SESSION_DATA_ACCESS_KEY),
				("authToken", CREATE_SESSION_DATA_AUTH_TOKEN),
				("recoverCodeVerifier", CREATE_SESSION_DATA_RECOVER_CODE_VERIFIER),
				("user", CREATE_SESSION_DATA_USER),
			],
		);
		conferir::<CreateSessionReturn>(
			&provedor,
			&[
				("_format", CREATE_SESSION_RETURN_FORMAT),
				("accessToken", CREATE_SESSION_RETURN_ACCESS_TOKEN),
				("challenges", CREATE_SESSION_RETURN_CHALLENGES),
				("user", CREATE_SESSION_RETURN_USER),
			],
		);
		conferir::<Challenge>(
			&provedor,
			&[("type", CHALLENGE_TYPE), ("u2f", CHALLENGE_U2F), ("otp", CHALLENGE_OTP)],
		);
		assert_eq!(
			provedor
				.resolve_client_type_ref(&Challenge::type_ref())
				.unwrap()
				.get_attribute_id_by_attribute_name("_id")
				.unwrap(),
			CHALLENGE_ID
		);
		conferir::<SecondFactorAuthData>(
			&provedor,
			&[
				("_format", SECOND_FACTOR_AUTH_DATA_FORMAT),
				("type", SECOND_FACTOR_AUTH_DATA_TYPE),
				("otpCode", SECOND_FACTOR_AUTH_DATA_OTP_CODE),
				("u2f", SECOND_FACTOR_AUTH_DATA_U2F),
				("session", SECOND_FACTOR_AUTH_DATA_SESSION),
				("webauthn", SECOND_FACTOR_AUTH_DATA_WEBAUTHN),
			],
		);
		conferir::<SecondFactorAuthGetData>(
			&provedor,
			&[
				("_format", SECOND_FACTOR_AUTH_GET_DATA_FORMAT),
				("accessToken", SECOND_FACTOR_AUTH_GET_DATA_ACCESS_TOKEN),
			],
		);
		conferir::<SecondFactorAuthGetReturn>(
			&provedor,
			&[
				("_format", SECOND_FACTOR_AUTH_GET_RETURN_FORMAT),
				("secondFactorPending", SECOND_FACTOR_AUTH_GET_RETURN_PENDING),
			],
		);
		conferir::<SecondFactorAuthDeleteData>(
			&provedor,
			&[
				("_format", SECOND_FACTOR_AUTH_DELETE_DATA_FORMAT),
				("session", SECOND_FACTOR_AUTH_DELETE_DATA_SESSION),
			],
		);
		conferir::<CloseSessionServicePost>(
			&provedor,
			&[
				("_format", CLOSE_SESSION_POST_FORMAT),
				("accessToken", CLOSE_SESSION_POST_ACCESS_TOKEN),
				("sessionId", CLOSE_SESSION_POST_SESSION_ID),
			],
		);
	}

	#[test]
	fn id_da_sessao_igual_ao_do_sdk() {
		// Vetor do teste oficial login_facade.rs (test_resume_session).
		let id = id_da_sessao(&Segredo::novo("ZB-VPZfACMABhx-jUBZ91wyBWLlaJ6AIzg".into())).unwrap();
		assert_eq!(id.lista, "O0yKEOU-1B-0");
		assert_eq!(id.elemento, "jlv3AEmnv8rvtZe38u2dk-U1kzxpkMXWNusNz-NhnMI");
		assert!(id_da_sessao(&Segredo::novo("curto".into())).is_err());
		assert!(id_da_sessao(&Segredo::novo("não é base64!".into())).is_err());
	}

	#[test]
	fn respostas_estranhas_viram_erro() {
		assert!(ler_salt_return(b"[]").is_err());
		assert!(ler_salt_return(br#"{"422":"AAEC","2133":"1"}"#).is_err()); // salt curto
		assert!(ler_create_session_return(br#"{"1221":"","1222":[],"1223":"u"}"#).is_err());
		assert!(ler_segundo_fator_pendente(br#"{"1238":"talvez"}"#).is_err());
		let s = ler_create_session_return(
			br#"{"1220":"0","1221":"tok","1222":[{"1188":"a","1189":"1","1190":[],"1247":[]},{"1188":"b","1189":"2","1190":[],"1247":[]}],"1223":"usuario"}"#,
		)
		.unwrap();
		assert_eq!(s.desafios, vec![Desafio::Totp, Desafio::Webauthn]);
		assert_eq!(s.user_id, "usuario");
	}
}
