//! O cliente da Central de e-mail do DaVinci (a do outro dev, 0386) — por
//! caixa: `/api/mail/agent/{caixa}/…`.
//!
//! - v1 (o contrato DELE, do jeito que está): `heartbeat` (o sinal a cada
//!   60 s: estado, `can_send`, `error_code` → `send_enabled`),
//!   `outbox/lease` (as respostas que uma PESSOA mandou, até 5) e
//!   `outbox/{job}/receipt` (o recibo: sent | failed | uncertain).
//! - v2 (o NOSSO, `routers/mail_agent_v2.py`, só o que o v1 não carrega):
//!   `sync` (pastas e aliases da conta → quais pastas ler), `ingest` (o
//!   e-mail com os ids do Tuta, resultado por e-mail), `count` (a janela de
//!   uma pasta → o que falta / saiu) e `changes` (movido, apagado).
//!
//! - `Authorization: Bearer <chave do agente da caixa>` (do Chaveiro). A
//!   chave nunca vai em URL, log ou erro.
//! - O cliente HTTP só fala com a URL configurada (rede.rs, política DaVinci).
//! - Respostas da Central: `{"detail": {"code": …, "fields": [{field, type}]}}`.
//!   401 `agent_unauthorized` = chave errada (para e avisa); 404 sem código =
//!   o servidor ainda não tem o v2; 409 = conflito com código; 413 = lote
//!   grande demais (quem chama divide); 422 = campo inválido (só o NOME do
//!   campo volta); 429/5xx/sem rede = recuo crescente.

use crate::config;
use crate::estado::EstadoConector;
use crate::rede::{ClienteRest, ErroDestino, Politica};
use crate::registro::mascarar;
use crate::segredo::Segredo;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tutasdk::bindings::rest_client::{HttpMethod, RestClient, RestClientOptions, RestResponse};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ErroDavinci {
	/// 404 sem código: a rota não existe (o servidor ainda não tem o v2).
	SemV2,
	/// 401 `agent_unauthorized`: a chave do Chaveiro não é a desta caixa.
	TokenRecusado,
	/// 409 `another_agent_active`: outro Mac/processo está lendo esta caixa.
	OutroAgente,
	/// Outro 409/401 com código (`folder_unknown`, `receipt_conflict`, `invalid_lease`…).
	Conflito(String),
	/// 404 com código (ex.: `job_not_found`).
	NaoEncontrado(String),
	/// 413: o corpo passou do teto da rota.
	GrandeDemais,
	/// 422: estes campos não passaram (o valor nunca volta).
	Invalido(Vec<String>),
	/// 429: esperar.
	Limitado(Duration),
	/// 5xx.
	Fora(u32),
	/// Outro status inesperado.
	Status(u32),
	/// Sem conexão / tempo esgotado.
	Rede,
	/// O DaVinci respondeu algo que não é o formato combinado.
	Resposta,
}

impl std::fmt::Display for ErroDavinci {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		match self {
			Self::SemV2 => f.write_str("a Central de e-mail do DaVinci ainda não tem o contrato v2 (publicar o servidor antes do conector)"),
			Self::TokenRecusado => f.write_str("a Central recusou a chave do agente (rode `tuta-conector configurar` com a chave desta caixa)"),
			Self::OutroAgente => f.write_str("outro agente está lendo esta caixa agora (dois Macs ou dois processos na mesma conta)"),
			Self::Conflito(c) => write!(f, "a Central recusou por conflito ({c})"),
			Self::NaoEncontrado(c) => write!(f, "a Central não achou ({c})"),
			Self::GrandeDemais => f.write_str("o lote passou do tamanho que a Central aceita"),
			Self::Invalido(campos) => write!(f, "a Central recusou campos: {}", campos.join(", ")),
			Self::Limitado(d) => write!(f, "o DaVinci pediu para esperar {} s", d.as_secs()),
			Self::Fora(s) => write!(f, "o DaVinci está com erro ({s})"),
			Self::Status(s) => write!(f, "o DaVinci respondeu {s}"),
			Self::Rede => f.write_str("sem conexão com o DaVinci"),
			Self::Resposta => f.write_str("resposta do DaVinci fora do formato"),
		}
	}
}

impl std::error::Error for ErroDavinci {}

impl ErroDavinci {
	/// Vale tentar de novo mais tarde (com recuo)?
	#[must_use]
	pub fn passageiro(&self) -> bool {
		matches!(self, Self::Limitado(_) | Self::Fora(_) | Self::Rede)
	}
}

/// Recuo crescente: RECUO_INICIAL, ×2 a cada falha seguida, até RECUO_MAXIMO,
/// com sorteio de até +20%. Sucesso zera.
#[derive(Debug, Default)]
pub struct Recuo {
	falhas: u32,
}

impl Recuo {
	/// A espera para a próxima tentativa (e conta mais uma falha).
	pub fn falhou(&mut self) -> Duration {
		let sorteio = f64::from(rand_core::RngCore::next_u32(&mut rand_core::OsRng)) / f64::from(u32::MAX);
		let espera = Self::espera(self.falhas, sorteio);
		self.falhas = self.falhas.saturating_add(1);
		espera
	}

	pub fn deu_certo(&mut self) {
		self.falhas = 0;
	}

	#[must_use]
	pub fn falhas(&self) -> u32 {
		self.falhas
	}

	/// A conta sem sorteio (`sorteio` em 0..=1).
	#[must_use]
	pub fn espera(falhas: u32, sorteio: f64) -> Duration {
		let base = config::RECUO_INICIAL.as_secs_f64() * 2f64.powi(i32::try_from(falhas.min(20)).unwrap_or(20));
		let teto = config::RECUO_MAXIMO.as_secs_f64();
		let com_sorteio = base.min(teto) * (1.0 + 0.2 * sorteio.clamp(0.0, 1.0));
		Duration::from_secs_f64(com_sorteio.min(teto))
	}
}

// ── v1: o sinal ─────────────────────────────────────────────────────────

/// O corpo do POST /heartbeat (o `Heartbeat` do v1, nada a mais).
#[derive(Serialize, Debug, Clone, PartialEq, Eq)]
pub struct Sinal {
	/// online | login_required | error
	pub state: &'static str,
	pub can_send: bool,
	/// `^[a-z0-9_:-]+$`, até 64.
	pub error_code: Option<String>,
}

impl Sinal {
	/// O estado do conector nos 3 da Central (o detalhe vai no `error_code`).
	#[must_use]
	pub fn de(estado: EstadoConector, can_send: bool) -> Self {
		let (state, codigo) = match estado {
			EstadoConector::Ok => ("online", None),
			EstadoConector::Iniciando
			| EstadoConector::Ilegivel
			| EstadoConector::Atrasado
			| EstadoConector::Limitado
			| EstadoConector::TutaFora => ("online", Some(estado.como_texto())),
			EstadoConector::SessaoCaiu => ("login_required", Some(estado.como_texto())),
			EstadoConector::VersaoRecusada | EstadoConector::Erro | EstadoConector::SemV2 => {
				("error", Some(estado.como_texto()))
			},
		};
		// Sem sessão ou com a versão recusada, nada sai (a Central exige online).
		let pode = can_send && state == "online";
		Self {
			state,
			can_send: pode,
			error_code: codigo.map(str::to_owned),
		}
	}
}

#[derive(Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
pub struct RespostaSinal {
	pub ok: bool,
	pub send_enabled: bool,
}

// ── v1: a fila de respostas ─────────────────────────────────────────────

/// Uma resposta que uma PESSOA mandou (o job do lease v1). O `message_id`
/// "<job@mail.davinci.local>" é ignorado (o Tuta não deixa escolher).
#[derive(Deserialize, Clone, PartialEq, Eq)]
pub struct Tarefa {
	pub id: String,
	pub lease_token: String,
	pub from_address: String,
	pub to: String,
	pub subject: String,
	pub text: String,
	#[serde(default)]
	pub in_reply_to: Option<String>,
	#[serde(default)]
	pub references: Vec<String>,
	#[serde(default)]
	pub message_id: Option<String>,
}

impl std::fmt::Debug for Tarefa {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		// Nada do cliente nem o token do lease.
		f.debug_struct("Tarefa").field("id", &self.id).finish_non_exhaustive()
	}
}

#[derive(Deserialize)]
struct LeaseOut {
	jobs: Vec<Tarefa>,
}

/// O recibo (o `Receipt` do v1).
#[derive(Serialize, Debug, Clone, PartialEq, Eq)]
pub struct Recibo {
	pub lease_token: String,
	/// sent | failed | uncertain
	pub status: &'static str,
	pub message_id: Option<String>,
	pub error_code: Option<String>,
}

// ── v2 ──────────────────────────────────────────────────────────────────

/// Uma pasta da conta (FolderIn do v2).
#[derive(Serialize, Debug, Clone, PartialEq, Eq)]
pub struct PastaParaCentral {
	pub key: String,
	pub name: String,
	pub path: String,
	pub kind: i64,
	pub parent: Option<String>,
}

/// O corpo do POST /v2/sync.
#[derive(Serialize, Debug, Clone)]
pub struct Sincronia {
	pub instance: String,
	pub agent_version: String,
	pub tuta_version: String,
	pub counters: BTreeMap<String, i64>,
	/// None = não mudou (a lista inteira quando mudar).
	pub folders: Option<Vec<PastaParaCentral>>,
	/// false = alguma pasta não decifrou agora (a Central não dá as outras por sumidas).
	pub folders_complete: bool,
	pub aliases: Option<Vec<String>>,
}

#[derive(Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct LeituraDaPasta {
	pub key: String,
	/// corpo | so_contar | nao
	pub read: String,
}

/// A resposta do /v2/sync: o que ler (o servidor decide; o conector obedece).
#[derive(Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct RespostaSincronia {
	pub contract: u32,
	pub folders: Vec<LeituraDaPasta>,
	pub count_only_aliases: Vec<String>,
}

/// O resultado de UM e-mail do /v2/ingest.
#[derive(Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct ResultadoEmail {
	#[serde(default)]
	pub source_id: Option<String>,
	/// accepted | duplicate | rejected
	pub status: String,
	#[serde(default)]
	pub code: Option<String>,
	#[serde(default)]
	pub fields: Vec<Value>,
}

#[derive(Deserialize)]
struct IngestOut {
	results: Vec<ResultadoEmail>,
}

/// A resposta do /v2/count.
#[derive(Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct RespostaContagem {
	/// No Tuta e não na Central (pasta de corpo): mandar de novo.
	pub missing: Vec<String>,
	/// Mudaram para esta pasta (a Central já aplicou).
	pub moved: u64,
	/// Estavam nesta pasta e não estão mais (o conector confere).
	pub left: Vec<String>,
}

/// Uma mudança vista no Tuta (ChangeIn do v2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mudanca {
	pub source_id: String,
	pub folder_key: Option<String>,
	/// Sumiu de vez do Tuta (ou foi para a Lixeira).
	pub apagado: bool,
}

#[derive(Serialize)]
struct MudancaOut<'a> {
	source_id: &'a str,
	#[serde(skip_serializing_if = "Option::is_none")]
	folder_key: Option<&'a str>,
	deleted: bool,
}

#[derive(Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct RespostaMudancas {
	pub updated: u64,
	pub unknown: u64,
}

// ── O cliente ───────────────────────────────────────────────────────────

pub struct ClienteDavinci {
	base: String,
	caixa: String,
	token: Segredo,
	rest: Arc<dyn RestClient>,
	pub recuo: Mutex<Recuo>,
}

impl std::fmt::Debug for ClienteDavinci {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		f.debug_struct("ClienteDavinci")
			.field("base", &self.base)
			.field("caixa", &self.caixa)
			.field("token", &self.token)
			.finish_non_exhaustive()
	}
}

impl ClienteDavinci {
	/// Cliente com o HTTPS do SDK, preso à origem de `url`.
	pub fn novo(url: &str, caixa: &str, token: Segredo) -> Result<Self, ErroDestino> {
		let politica = Politica::davinci(url)?;
		let rest = ClienteRest::nativo(politica, config::DAVINCI_TEMPO_MAX_PEDIDO).map_err(|_| ErroDestino::UrlInvalida)?;
		Self::com_cliente(url, caixa, token, Arc::new(rest))
	}

	/// Com um RestClient dado (os testes; ele já deve ter a política certa).
	pub fn com_cliente(url: &str, caixa: &str, token: Segredo, rest: Arc<dyn RestClient>) -> Result<Self, ErroDestino> {
		let politica = Politica::davinci(url)?;
		let Politica::Davinci { base } = politica else {
			return Err(ErroDestino::UrlInvalida);
		};
		if !crate::chaveiro::caixa_valida(caixa) {
			return Err(ErroDestino::UrlInvalida);
		}
		Ok(Self {
			base: base.base(),
			caixa: caixa.trim().to_lowercase(),
			token,
			rest,
			recuo: Mutex::new(Recuo::default()),
		})
	}

	fn url(&self, rota: &str) -> String {
		format!("{}{}{}", self.base, config::prefixo_davinci(&self.caixa), rota)
	}

	async fn pedir(&self, rota: &str, corpo: Vec<u8>) -> Result<RestResponse, ErroDavinci> {
		let cabecalhos = HashMap::from([
			("Authorization".to_owned(), format!("Bearer {}", self.token.expor())),
			("Accept".to_owned(), "application/json".to_owned()),
			("Content-Type".to_owned(), "application/json".to_owned()),
		]);
		self.rest
			.request_binary(
				self.url(rota),
				HttpMethod::POST,
				RestClientOptions {
					headers: cabecalhos,
					body: Some(corpo),
					suspension_behavior: None,
				},
			)
			.await
			.map_err(|_| ErroDavinci::Rede)
	}

	/// POST com JSON e resposta JSON, classificando os erros do contrato.
	pub async fn post_json<T: Serialize, R: DeserializeOwned>(&self, rota: &str, corpo: &T) -> Result<R, ErroDavinci> {
		let bytes = serde_json::to_vec(corpo).map_err(|_| ErroDavinci::Resposta)?;
		let resposta = self.pedir(rota, bytes).await;
		self.tratar(resposta)
	}

	fn tratar<R: DeserializeOwned>(&self, resposta: Result<RestResponse, ErroDavinci>) -> Result<R, ErroDavinci> {
		let resultado = resposta.and_then(|r| {
			if (200..300).contains(&r.status) {
				serde_json::from_slice::<R>(r.body.as_deref().unwrap_or(b"null")).map_err(|_| ErroDavinci::Resposta)
			} else {
				Err(classificar(&r))
			}
		});
		if let Ok(mut recuo) = self.recuo.lock() {
			match &resultado {
				Ok(_) => recuo.deu_certo(),
				Err(e) if e.passageiro() => {
					let _ = recuo.falhou();
				},
				Err(_) => {},
			}
		}
		resultado
	}

	/// Quanto esperar antes da próxima tentativa (pelo recuo atual).
	#[must_use]
	pub fn proxima_espera(&self) -> Duration {
		self.recuo
			.lock()
			.map(|r| Recuo::espera(r.falhas().saturating_sub(1), 0.0))
			.unwrap_or(config::RECUO_INICIAL)
	}

	// v1 ─────────────────────────────────────────────────────────────────

	/// O sinal v1 → o `send_enabled` da caixa (a chave que a pessoa liga).
	pub async fn sinal(&self, sinal: &Sinal) -> Result<RespostaSinal, ErroDavinci> {
		let r: RespostaSinal = self.post_json("/heartbeat", sinal).await?;
		if !r.ok {
			return Err(ErroDavinci::Resposta);
		}
		Ok(r)
	}

	/// O lease v1: as respostas enfileiradas por pessoa (até 5). O corpo vai vazio.
	pub async fn pegar_tarefas(&self) -> Result<Vec<Tarefa>, ErroDavinci> {
		let saida: LeaseOut = self.post_json("/outbox/lease", &serde_json::json!({})).await?;
		Ok(saida.jobs)
	}

	/// O recibo v1. Repetir o MESMO recibo é aceito (200); outro dá 409.
	pub async fn recibo(&self, tarefa: &str, recibo: &Recibo) -> Result<(), ErroDavinci> {
		if !tarefa.chars().all(|c| c.is_ascii_hexdigit() || c == '-') || tarefa.len() != 36 {
			return Err(ErroDavinci::Resposta);
		}
		let _: Value = self.post_json(&format!("/outbox/{tarefa}/receipt"), recibo).await?;
		Ok(())
	}

	// v2 ─────────────────────────────────────────────────────────────────

	pub async fn sincronizar(&self, corpo: &Sincronia) -> Result<RespostaSincronia, ErroDavinci> {
		let r: RespostaSincronia = self.post_json("/v2/sync", corpo).await?;
		if r.contract < 2 {
			return Err(ErroDavinci::Resposta);
		}
		Ok(r)
	}

	/// O lote (corpo já montado, em bytes) → um resultado por e-mail.
	pub async fn ingerir(&self, corpo_json: Vec<u8>) -> Result<Vec<ResultadoEmail>, ErroDavinci> {
		let resposta = self.pedir("/v2/ingest", corpo_json).await;
		let saida: IngestOut = self.tratar(resposta)?;
		Ok(saida.results)
	}

	pub async fn contar<T: Serialize>(&self, corpo: &T) -> Result<RespostaContagem, ErroDavinci> {
		self.post_json("/v2/count", corpo).await
	}

	pub async fn mudancas(&self, mudancas: &[Mudanca]) -> Result<RespostaMudancas, ErroDavinci> {
		let lista: Vec<MudancaOut<'_>> = mudancas
			.iter()
			.map(|m| MudancaOut {
				source_id: &m.source_id,
				folder_key: m.folder_key.as_deref(),
				deleted: m.apagado,
			})
			.collect();
		self.post_json("/v2/changes", &serde_json::json!({ "changes": lista })).await
	}
}

fn detalhe(corpo: &Value) -> Option<&Value> {
	corpo.get("detail")
}

fn codigo(corpo: &Value) -> Option<String> {
	detalhe(corpo)?.get("code").and_then(Value::as_str).map(|c| c.chars().take(64).collect())
}

fn classificar(r: &RestResponse) -> ErroDavinci {
	let corpo: Value = r
		.body
		.as_deref()
		.and_then(|b| serde_json::from_slice(b).ok())
		.unwrap_or(Value::Null);
	match r.status {
		// A rota que não existe (o FastAPI responde {"detail": "Not Found"}).
		404 if codigo(&corpo).is_none() => ErroDavinci::SemV2,
		404 => ErroDavinci::NaoEncontrado(codigo(&corpo).unwrap_or_default()),
		401 => match codigo(&corpo).as_deref() {
			Some("agent_unauthorized") | None => ErroDavinci::TokenRecusado,
			Some(outro) => ErroDavinci::Conflito(outro.to_owned()),
		},
		409 => match codigo(&corpo).as_deref() {
			Some("another_agent_active") => ErroDavinci::OutroAgente,
			Some(outro) => ErroDavinci::Conflito(outro.to_owned()),
			None => ErroDavinci::Conflito(String::new()),
		},
		413 => ErroDavinci::GrandeDemais,
		422 => {
			let campos = detalhe(&corpo)
				.and_then(|d| d.get("fields"))
				.and_then(Value::as_array)
				.map(|campos| {
					campos
						.iter()
						.filter_map(|e| e.get("field").and_then(Value::as_str))
						.map(|c| c.chars().take(80).collect())
						.collect()
				})
				.unwrap_or_default();
			ErroDavinci::Invalido(campos)
		},
		429 => {
			let espera = r
				.headers
				.get("retry-after")
				.and_then(|v| v.trim().parse::<u64>().ok())
				.map_or(config::RECUO_INICIAL, Duration::from_secs)
				.min(config::RECUO_MAXIMO);
			ErroDavinci::Limitado(espera)
		},
		500..=599 => ErroDavinci::Fora(r.status),
		outro => ErroDavinci::Status(outro),
	}
}

/// Um código de erro do recibo/sinal no formato da Central (`^[a-z0-9_:-]+$`, até 64).
#[must_use]
pub fn codigo_de_erro(texto: &str) -> String {
	let limpo: String = mascarar(texto)
		.to_lowercase()
		.chars()
		.map(|c| if c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '_' | ':' | '-') { c } else { '_' })
		.take(64)
		.collect();
	if limpo.is_empty() {
		"erro".to_owned()
	} else {
		limpo
	}
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn recuo_dobra_e_tem_teto() {
		assert_eq!(Recuo::espera(0, 0.0), Duration::from_secs(5));
		assert_eq!(Recuo::espera(1, 0.0), Duration::from_secs(10));
		assert_eq!(Recuo::espera(3, 0.0), Duration::from_secs(40));
		assert_eq!(Recuo::espera(50, 1.0), config::RECUO_MAXIMO);
		assert_eq!(Recuo::espera(0, 1.0), Duration::from_secs(6));
		let mut r = Recuo::default();
		let a = r.falhou();
		let b = r.falhou();
		assert!(b > a);
		r.deu_certo();
		assert_eq!(r.falhas(), 0);
	}

	#[test]
	fn estados_do_conector_viram_os_tres_da_central() {
		assert_eq!(
			Sinal::de(EstadoConector::Ok, true),
			Sinal {
				state: "online",
				can_send: true,
				error_code: None
			}
		);
		let s = Sinal::de(EstadoConector::Ilegivel, true);
		assert_eq!((s.state, s.can_send, s.error_code.as_deref()), ("online", true, Some("ilegivel")));
		let s = Sinal::de(EstadoConector::SessaoCaiu, true);
		assert_eq!((s.state, s.can_send), ("login_required", false));
		let s = Sinal::de(EstadoConector::VersaoRecusada, true);
		assert_eq!((s.state, s.can_send), ("error", false));
		for e in EstadoConector::TODOS {
			let s = Sinal::de(e, false);
			assert!(!s.can_send);
			if let Some(c) = s.error_code {
				assert!(c.len() <= 64 && c.chars().all(|x| x.is_ascii_lowercase() || x.is_ascii_digit() || x == '_'), "{c}");
			}
		}
	}

	#[test]
	fn codigo_de_erro_no_formato_da_central() {
		assert_eq!(codigo_de_erro("rascunho_falhou"), "rascunho_falhou");
		let c = codigo_de_erro("Falhou para fulano@uranyx.com.br!");
		assert!(c.starts_with("falhou_para_") && !c.contains('@') && !c.contains("fulano"), "{c}");
		assert!(c.chars().all(|x| x.is_ascii_lowercase() || x.is_ascii_digit() || matches!(x, '_' | ':' | '-')));
		assert!(codigo_de_erro(&"erro grande ".repeat(30)).len() <= 64);
		assert_eq!(codigo_de_erro(""), "erro");
	}

	#[test]
	fn tarefa_debug_sem_dado_do_cliente() {
		let t: Tarefa = serde_json::from_value(serde_json::json!({
			"id": "0b4e6a52-3f1d-4c55-9e0a-2a9c1d7e8f10", "lease_token": "segredo-do-lease",
			"from_address": "21max@tuta.com", "to": "cliente@exemplo.test", "subject": "Re: oi",
			"text": "texto", "in_reply_to": "<a@b>", "references": [], "message_id": "<x@mail.davinci.local>"
		}))
		.unwrap();
		let d = format!("{t:?}");
		assert!(!d.contains("segredo") && !d.contains("cliente") && !d.contains("texto"), "{d}");
	}
}
