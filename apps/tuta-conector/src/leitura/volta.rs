//! A VOLTA de leitura (decisão 6), falando com a Central de e-mail: sinal v1
//! → canário → caixa → pastas do Tuta → /v2/sync (o servidor diz o que ler)
//! → para cada pasta, os e-mails novos acima do cursor (só as de corpo) e a
//! /v2/count do topo (todas) → varredura funda (1 pasta) → fechamento do dia
//! (1 pasta) → ilegíveis vencidos. SÓ consulta periódica: nunca websocket.
//!
//! As garantias:
//! - NÃO PERDE: o cursor de uma pasta só anda até o último e-mail entregue
//!   sem buraco (aceito/repetido na Central, ou registrado como ilegível/
//!   recusado). O que a Central não respondeu, o que voltou com pasta
//!   desconhecida e o que espera a regra do Tuta seguram o cursor; a volta
//!   seguinte manda de novo. A /v2/count do topo e a varredura funda pegam o
//!   que escapou (movido para pasta lida, reinício no meio do lote).
//! - NÃO DUPLICA: a Central deduplica pelo `source_id` ("tuta:<lista>/<elemento>");
//!   o conector ainda guarda as entradas já entregues acima de um buraco para
//!   não carregá-las de novo.
//! - NÃO PARA POR UM ITEM: e-mail/pasta/arquivo que não decifra ou faz o SDK
//!   entrar em pânico é CONTADO (registro local só com ids, estado
//!   `ilegivel`) e a volta segue. Só para a volta o que vale para a conta
//!   inteira: sessão caída (401), versão recusada (474), 429, Tuta fora, e a
//!   Central fora/recusando.
//! - O CORPO DE PASTA "SÓ CONTAR" NUNCA SAI DO MAC: o servidor diz quais
//!   pastas se leem (`corpo`); das outras vão só os ids, na contagem. O
//!   e-mail só para os aliases internos (adm@, financeiro@…) também fica.
//! - Em disco, só ids e cursores (estado_local.rs). Conteúdo de e-mail só em
//!   memória até a Central responder.

use crate::chaveiro::{self, Cofre};
use crate::config::{self, Conta};
use crate::davinci::{
	ClienteDavinci, ErroDavinci, Mudanca, PastaParaCentral, Recuo, RespostaContagem, RespostaSincronia, ResultadoEmail,
	Sinal, Sincronia,
};
use crate::estado::{Compartilhado, EstadoConector};
use crate::estado_local::{instancia_nova, CanarioGuardado, Conciliacao, EstadoLocal, Pendencia, UltimoPulso};
use crate::leitura::caixa::{
	ArquivoLido, Caixa, EmailLido, FalhaTuta, Pastas, PastaTuta, KIND_ENTRADA, KIND_ENVIADOS, KIND_LIXEIRA, KIND_MARCADOR,
	KIND_TODOS,
};
use crate::leitura::pacote::{self, AnexoBaixado, AnexoOmitido, MapaPastas, SemPacote};
use crate::leitura::{entrada_id, OpcoesLeitura, ResumoVolta};
use crate::registro::mascarar;
use crate::segredo::Segredo;
use crate::tuta::canario::{self, Canario, Releases};
use futures::StreamExt;
use serde_json::{json, Value};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::PathBuf;
use std::sync::atomic::Ordering as Atomica;
use std::sync::Arc;
use std::time::Duration;
use tutasdk::bindings::rest_client::RestClient;
use tutasdk::{GeneratedId, IdTupleGenerated};

const DIA_MS: u64 = 24 * 60 * 60 * 1000;
const HORA_MS: u64 = 60 * 60 * 1000;

/// O relógio (ms desde 1970). Os testes passam um relógio próprio.
pub type Relogio = Arc<dyn Fn() -> u64 + Send + Sync>;

/// Tudo o que o leitor usa de fora (os testes passam um Tuta e uma Central falsos).
pub struct Dependencias {
	pub conta: Conta,
	pub cofre: Arc<dyn Cofre>,
	pub tuta_url: String,
	pub rest_tuta: Arc<dyn RestClient>,
	/// None no `--seco` (nada vai ao DaVinci).
	pub davinci: Option<Arc<ClienteDavinci>>,
	/// A URL completa da lista de releases (canário "atrasado").
	pub url_releases: String,
	pub rest_github: Arc<dyn RestClient>,
	pub pasta_local: PathBuf,
	pub relogio: Relogio,
	/// O que a leitura e o envio contam um ao outro (o mesmo processo).
	pub compartilhado: Arc<Compartilhado>,
}

/// Por que uma volta parou.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Parada {
	/// Sem sessão no Chaveiro: `tuta-conector entrar`.
	SemSessao,
	/// O Chaveiro recusou (texto do macOS, sem segredo).
	Cofre(String),
	/// Falha que vale para a conta inteira (sessão, 474, 429, fora).
	Tuta(FalhaTuta),
	/// O login retomado não abriu por outro motivo (formato, pânico).
	Login(String),
	/// O DaVinci não respondeu (rede, 5xx, 429) e não há leitura recente.
	DavinciFora(ErroDavinci),
	/// A Central recusou (chave do agente, corpo).
	DavinciRecusou(ErroDavinci),
	/// A Central ainda não tem o contrato v2.
	SemV2,
	/// Outro agente (outro Mac/processo) está lendo esta caixa.
	Duplicado,
	/// Outro processo desta conta já está rodando neste Mac.
	JaRodando,
	/// Não deu para gravar o estado local.
	Disco(String),
}

impl std::fmt::Display for Parada {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		match self {
			Self::SemSessao => f.write_str("não há sessão do Tuta no Chaveiro para esta conta: rode `tuta-conector entrar --conta …`"),
			Self::Cofre(e) => write!(f, "Chaveiro: {e}"),
			Self::Tuta(t) => write!(f, "{t}"),
			Self::Login(m) => write!(f, "a sessão guardada não abriu ({m})"),
			Self::DavinciFora(e) | Self::DavinciRecusou(e) => write!(f, "{e}"),
			Self::SemV2 => f.write_str("a Central de e-mail do DaVinci ainda não tem o contrato v2: nada foi lido (publicar o servidor antes)"),
			Self::Duplicado => f.write_str("outro agente está lendo esta caixa (a Central avisou): este parou"),
			Self::JaRodando => f.write_str("outro processo desta conta já está rodando neste Mac"),
			Self::Disco(e) => write!(f, "não consegui gravar o estado local: {e}"),
		}
	}
}

impl std::error::Error for Parada {}

/// O papel de uma pasta (a Central decide no /v2/sync).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Papel {
	/// O corpo vai (pasta de plataforma, Entrada, Enviados, caixas dos sites).
	Ler,
	/// Só os ids vão, na contagem (financeiro, Lixeira, Spam, pasta nova…).
	Contar,
}

/// O que aconteceu com um item.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Desfecho {
	/// Aceito ou repetido na Central.
	Entregue,
	/// Não vai agora mas está registrado (ilegível, recusado, sumiu, só contado): o cursor passa.
	Registrado,
	/// Fica para a próxima volta: o cursor NÃO passa.
	Pendente,
}

#[derive(Clone)]
struct Item {
	/// A chave do desfecho (o id da entrada, ou o do e-mail).
	chave: String,
	mail: IdTupleGenerated,
	pasta: PastaTuta,
}

enum Carga {
	Pacote(Value),
	/// Entrada com `processNeeded` há pouco (a regra do Tuta ainda vai mover)
	/// ou enviado que ainda está "enviando": espera a próxima volta.
	Esperar,
	/// Só para aliases que só se contam: o corpo não sobe (registrado).
	SoContado,
	/// Rascunho (não é e-mail ainda): passa sem registro.
	Pular,
	/// A Central não aceitaria (remetente inválido): registrado como recusado.
	Recusar(&'static str),
	Falhou(FalhaTuta),
}

struct Carregado {
	item: Item,
	carga: Carga,
}

/// Os contadores do /v2/sync (desde que o processo subiu, menos os de agora).
#[derive(Debug, Default, Clone)]
pub struct Contadores {
	pub lidos: i64,
	pub anexos: i64,
	pub anexos_omitidos: i64,
	pub erros_429: i64,
	pub erros_401: i64,
	pub erros_474: i64,
	pub erros_5xx: i64,
	pub movidos: i64,
	pub apagados: i64,
	pub processando_regra: i64,
	pub pastas_ilegiveis: i64,
	pub so_contados: i64,
}

fn chave_email(id: &IdTupleGenerated) -> String {
	format!("{}/{}", id.list_id.as_str(), id.element_id.as_str())
}

fn email_da_chave(chave: &str) -> Option<IdTupleGenerated> {
	let (l, e) = chave.split_once('/')?;
	Some(IdTupleGenerated::new(GeneratedId(l.to_owned()), GeneratedId(e.to_owned())))
}

fn iso(ms: u64) -> String {
	time::OffsetDateTime::from_unix_timestamp_nanos(i128::from(ms) * 1_000_000)
		.ok()
		.and_then(|d| d.format(&time::format_description::well_known::Rfc3339).ok())
		.unwrap_or_else(|| "1970-01-01T00:00:00Z".to_owned())
}

/// "AAAA-MM-DD" e o começo/fim (ms) do dia ANTERIOR em Brasília, se já
/// passou da hora do fechamento hoje.
fn dia_para_fechar(agora_ms: u64) -> Option<(String, u64, u64)> {
	let agora_s = i64::try_from(agora_ms / 1000).ok()?;
	let local = time::OffsetDateTime::from_unix_timestamp(agora_s + config::FUSO_BRT_SEGUNDOS).ok()?;
	if u64::from(local.hour()) < config::CONCILIACAO_HORA_BRT {
		return None;
	}
	let hoje = local.date();
	let ontem = hoje.previous_day()?;
	let meia_noite_hoje_s = hoje.midnight().assume_utc().unix_timestamp() - config::FUSO_BRT_SEGUNDOS;
	let fim = u64::try_from(meia_noite_hoje_s).ok()? * 1000;
	let inicio = fim.checked_sub(DIA_MS)?;
	let texto = format!("{:04}-{:02}-{:02}", ontem.year(), u8::from(ontem.month()), ontem.day());
	Some((texto, inicio, fim))
}

/// A janela de uma contagem: os ids (`tuta:<lista>/<elemento>`), se é a
/// janela inteira desde `desde`, até `ate`, o dia fechado e o total.
struct Janela<'a> {
	ids: &'a [IdTupleGenerated],
	desde: Option<u64>,
	ate: Option<u64>,
	dia: Option<&'a str>,
}

/// O leitor: guarda o estado entre as voltas.
pub struct Leitor {
	deps: Dependencias,
	opcoes: OpcoesLeitura,
	pub estado: EstadoLocal,
	caixa: Option<Caixa>,
	caixa_aberta_em: u64,
	/// O token da caixa aberta e o que o Tuta recusou (não insistir nele).
	token_da_caixa: Option<Segredo>,
	sessao_recusada: Option<Segredo>,
	parado_por_versao: bool,
	/// O e-mail da conta do Tuta (da sessão no Chaveiro).
	email_conta: String,
	/// A última resposta boa do /v2/sync e quando.
	leitura: Option<(RespostaSincronia, u64)>,
	/// A assinatura das pastas+aliases mandadas no último /v2/sync e quando.
	pastas_enviadas: Option<(String, u64)>,
	/// A Central não conhecia uma pasta: manda a lista de novo na próxima volta.
	forcar_sync: bool,
	aliases_conta: Arc<BTreeSet<String>>,
	pub estado_conector: EstadoConector,
	pub detalhe: Option<String>,
	falhas_tuta: u32,
	recuo_tuta: Recuo,
	ultimo_pulso_ms: u64,
	pub contadores: Contadores,
	/// Chaves que ficaram pendentes nesta volta (a contagem não repede).
	pendentes_volta: HashSet<String>,
	pub ultima: ResumoVolta,
}

impl Leitor {
	#[must_use]
	pub fn novo(deps: Dependencias, opcoes: OpcoesLeitura) -> Self {
		let mut estado = EstadoLocal::carregar(&deps.pasta_local);
		if estado.instancia.is_none() {
			estado.instancia = Some(instancia_nova());
		}
		let email_conta = chaveiro::ler_sessao(&*deps.cofre, deps.conta)
			.ok()
			.flatten()
			.map(|s| s.login.trim().to_lowercase())
			.or_else(|| deps.conta.email_padrao().map(str::to_owned))
			.unwrap_or_default();
		Self {
			deps,
			opcoes,
			estado,
			caixa: None,
			caixa_aberta_em: 0,
			token_da_caixa: None,
			sessao_recusada: None,
			parado_por_versao: false,
			email_conta,
			leitura: None,
			pastas_enviadas: None,
			forcar_sync: false,
			aliases_conta: Arc::new(BTreeSet::new()),
			estado_conector: EstadoConector::Iniciando,
			detalhe: None,
			falhas_tuta: 0,
			recuo_tuta: Recuo::default(),
			ultimo_pulso_ms: 0,
			contadores: Contadores::default(),
			pendentes_volta: HashSet::new(),
			ultima: ResumoVolta::default(),
		}
	}

	fn agora(&self) -> u64 {
		(self.deps.relogio)()
	}

	/// A conta do Tuta deste leitor (`--conta`).
	#[must_use]
	pub fn conta(&self) -> Conta {
		self.deps.conta
	}

	fn salvar(&self) -> Result<(), Parada> {
		if self.opcoes.seco {
			return Ok(());
		}
		self.estado.salvar(&self.deps.pasta_local).map_err(|e| Parada::Disco(e.to_string()))
	}

	// ── O laço ──────────────────────────────────────────────────────────

	/// O laço só de leitura (`ler`): volta, espera (pulsando), volta…
	/// (`--uma-volta` e `--seco` param depois da primeira). O `rodar` do
	/// serviço (leitura + envio) usa `uma_volta`, `espera_depois` e `pulsar`.
	pub async fn rodar(&mut self) -> Result<ResumoVolta, Parada> {
		loop {
			let r = self.uma_volta().await;
			if self.opcoes.uma_volta || self.opcoes.seco {
				return r;
			}
			let espera = self.espera_depois(&r);
			match &r {
				Ok(resumo) => log::info!("volta: {resumo}; próxima em {} s", espera.as_secs()),
				Err(p) => log::warn!("volta parou: {p}; próxima em {} s", espera.as_secs()),
			}
			let pulsar = !matches!(r, Err(Parada::Duplicado));
			let mut resta = espera;
			while !resta.is_zero() {
				let passo = resta.min(config::PULSO_INTERVALO);
				tokio::time::sleep(passo).await;
				resta = resta.saturating_sub(passo);
				if pulsar && !resta.is_zero() {
					let _ = self.pulsar().await;
				}
			}
		}
	}

	/// Uma volta e o que vem depois dela (estado do sinal, disco).
	pub async fn uma_volta(&mut self) -> Result<ResumoVolta, Parada> {
		self.pendentes_volta.clear();
		let r = self.volta().await;
		self.depois_da_volta(&r).await;
		r
	}

	/// Quanto esperar até a próxima volta (pelo resultado desta).
	pub fn espera_depois(&mut self, r: &Result<ResumoVolta, Parada>) -> Duration {
		match r {
			Ok(_) => config::LEITURA_INTERVALO,
			Err(Parada::Tuta(FalhaTuta::Limitado(pedido))) => {
				let recuo = self.recuo_tuta.falhou();
				pedido.map_or(recuo, |p| p.max(recuo)).min(config::RECUO_MAXIMO)
			},
			Err(Parada::Tuta(FalhaTuta::Fora)) => self.recuo_tuta.falhou(),
			Err(Parada::Tuta(_) | Parada::SemSessao | Parada::Cofre(_) | Parada::Login(_)) => config::PARADO_ESPERA,
			Err(Parada::DavinciFora(_)) => self
				.deps
				.davinci
				.as_ref()
				.map_or(config::RECUO_INICIAL, |d| d.proxima_espera())
				.max(config::RECUO_INICIAL),
			Err(Parada::DavinciRecusou(_) | Parada::SemV2) => config::DAVINCI_RECUSOU_ESPERA,
			Err(Parada::Duplicado) => config::DUPLICADO_ESPERA,
			Err(Parada::JaRodando | Parada::Disco(_)) => config::PARADO_ESPERA,
		}
	}

	// ── Estado do sinal ─────────────────────────────────────────────────

	async fn depois_da_volta(&mut self, r: &Result<ResumoVolta, Parada>) {
		let antes = self.estado_conector;
		match r {
			Ok(resumo) => {
				self.falhas_tuta = 0;
				self.recuo_tuta.deu_certo();
				self.ultima = resumo.clone();
				let atras = self.estado.canario.as_ref().and_then(|c| c.releases_atras).unwrap_or(0);
				let mut partes = Vec::new();
				if !self.estado.ilegiveis.is_empty() {
					partes.push(format!("{} e-mail(s) que não decifram", self.estado.ilegiveis.len()));
				}
				if resumo.pastas_ilegiveis > 0 {
					partes.push(format!("{} pasta(s) que não decifram", resumo.pastas_ilegiveis));
				}
				if !self.estado.recusados.is_empty() {
					partes.push(format!("{} e-mail(s) recusados pela Central", self.estado.recusados.len()));
				}
				if resumo.esperando_regra > 0 {
					partes.push(format!("{} esperando a regra do Tuta", resumo.esperando_regra));
				}
				if atras >= config::CANARIO_ATRASO_RELEASES {
					let nova = self.estado.canario.as_ref().and_then(|c| c.mais_nova.clone()).unwrap_or_default();
					partes.push(format!("SDK {atras} releases atrás (mais nova {nova})"));
				}
				self.estado_conector = if !self.estado.ilegiveis.is_empty() || resumo.pastas_ilegiveis > 0 {
					EstadoConector::Ilegivel
				} else if atras >= config::CANARIO_ATRASO_RELEASES {
					EstadoConector::Atrasado
				} else {
					EstadoConector::Ok
				};
				self.detalhe = (!partes.is_empty()).then(|| partes.join("; "));
			},
			Err(Parada::Tuta(f)) => {
				match f {
					FalhaTuta::SessaoCaiu => self.contadores.erros_401 += 1,
					FalhaTuta::VersaoRecusada => self.contadores.erros_474 += 1,
					FalhaTuta::Limitado(_) => self.contadores.erros_429 += 1,
					FalhaTuta::Fora => self.contadores.erros_5xx += 1,
					_ => {},
				}
				let novo = match f {
					FalhaTuta::Fora => {
						self.falhas_tuta += 1;
						(self.falhas_tuta >= config::TUTA_FORA_DEPOIS_DE_FALHAS).then_some(EstadoConector::TutaFora)
					},
					FalhaTuta::Ilegivel(_) | FalhaTuta::Sumiu => Some(EstadoConector::Erro),
					outra => Some(outra.estado()),
				};
				if let Some(e) = novo {
					self.estado_conector = e;
					self.detalhe = Some(f.to_string());
				}
			},
			Err(Parada::SemSessao) => {
				self.estado_conector = EstadoConector::SessaoCaiu;
				self.detalhe = Some("sem sessão no Chaveiro: rodar `tuta-conector entrar` no Mac mini".into());
			},
			Err(Parada::SemV2) => {
				self.estado_conector = EstadoConector::SemV2;
				self.detalhe = Some(Parada::SemV2.to_string());
			},
			Err(p @ (Parada::Cofre(_) | Parada::Login(_) | Parada::Disco(_))) => {
				self.estado_conector = EstadoConector::Erro;
				self.detalhe = Some(p.to_string());
			},
			Err(Parada::DavinciFora(_) | Parada::DavinciRecusou(_) | Parada::Duplicado | Parada::JaRodando) => {},
		}
		let _ = self.salvar();
		let davinci_recusou = matches!(r, Err(Parada::DavinciRecusou(_) | Parada::Duplicado));
		if self.estado_conector != antes && !davinci_recusou {
			let _ = self.pulsar().await;
		}
	}

	fn contadores_do_sync(&self) -> BTreeMap<String, i64> {
		let c = &self.contadores;
		let tamanho = |n: usize| i64::try_from(n).unwrap_or(i64::MAX);
		let comp = &self.deps.compartilhado;
		BTreeMap::from([
			("lidos".to_owned(), c.lidos),
			("ilegiveis".to_owned(), tamanho(self.estado.ilegiveis.len())),
			("pastas_ilegiveis".to_owned(), c.pastas_ilegiveis),
			("recusados".to_owned(), tamanho(self.estado.recusados.len())),
			("anexos".to_owned(), c.anexos),
			("anexos_omitidos".to_owned(), c.anexos_omitidos),
			("processando_regra".to_owned(), c.processando_regra),
			("so_contados".to_owned(), c.so_contados),
			("erros_429".to_owned(), c.erros_429),
			("erros_401".to_owned(), c.erros_401),
			("erros_474".to_owned(), c.erros_474),
			("erros_5xx".to_owned(), c.erros_5xx),
			("movidos".to_owned(), c.movidos),
			("apagados".to_owned(), c.apagados),
			(
				"releases_atras".to_owned(),
				i64::from(self.estado.canario.as_ref().and_then(|x| x.releases_atras).unwrap_or(0)),
			),
			("enviados_conta_hora".to_owned(), i64::from(comp.enviados_conta_hora.load(Atomica::Relaxed))),
			("enviados_conector_hora".to_owned(), i64::from(comp.enviados_conector_hora.load(Atomica::Relaxed))),
		])
		.into_iter()
		.map(|(k, v)| (k, v.clamp(0, 1_000_000_000)))
		.collect()
	}

	/// O sinal v1 ("estou vivo, estou assim, posso enviar?") → a chave de envio da caixa.
	pub async fn pulsar(&mut self) -> Result<bool, ErroDavinci> {
		let Some(davinci) = self.deps.davinci.clone() else {
			return Ok(false);
		};
		let agora = self.agora();
		let pode = self.deps.compartilhado.pode_enviar.load(Atomica::Relaxed);
		self.deps.compartilhado.guardar_estado(self.estado_conector);
		let sinal = Sinal::de(self.estado_conector, pode);
		let r = davinci.sinal(&sinal).await;
		self.ultimo_pulso_ms = agora;
		self.estado.ultimo_pulso = Some(UltimoPulso {
			em_ms: agora,
			estado: self.estado_conector.como_texto().to_owned(),
			resultado: match &r {
				Ok(_) => "aceito".to_owned(),
				Err(e) => mascarar(&e.to_string()),
			},
		});
		match r {
			Ok(resposta) => {
				self.deps.compartilhado.envio_ligado.store(resposta.send_enabled, Atomica::Relaxed);
				Ok(resposta.send_enabled)
			},
			Err(e) => {
				if !e.passageiro() {
					self.deps.compartilhado.envio_ligado.store(false, Atomica::Relaxed);
				}
				Err(e)
			},
		}
	}

	/// Numa volta longa (a primeira carga), o sinal continua a cada minuto.
	async fn pulsar_se_vencido(&mut self) {
		if self.opcoes.seco {
			return;
		}
		let intervalo_ms = u64::try_from(config::PULSO_INTERVALO.as_millis()).unwrap_or(u64::MAX);
		if self.agora().saturating_sub(self.ultimo_pulso_ms) >= intervalo_ms {
			let _ = self.pulsar().await;
		}
	}

	// ── A volta ─────────────────────────────────────────────────────────

	async fn volta(&mut self) -> Result<ResumoVolta, Parada> {
		if !self.opcoes.seco {
			match self.pulsar().await {
				Ok(_) => {},
				Err(e) if e.passageiro() => return Err(Parada::DavinciFora(e)),
				Err(e) => return Err(Parada::DavinciRecusou(e)),
			}
		}
		// O canário não precisa de sessão: vem antes (um 474 para tudo sem login).
		self.canario_se_vencido().await?;
		let caixa = self.garantir_caixa().await?;
		let mut r = ResumoVolta::default();
		let pastas = match caixa.pastas().await {
			Ok(p) => p,
			Err(f) if f.geral() => return Err(self.parar(f)),
			Err(_) => {
				// A LISTA de pastas veio estranha (não uma pasta só): nada a ler agora.
				r.pastas_ilegiveis = 1;
				self.contadores.pastas_ilegiveis = 1;
				return Ok(r);
			},
		};
		r.pastas_ilegiveis = pastas.ilegiveis;
		let papeis = self.papeis(&caixa, &pastas).await?;
		let mapa: Arc<MapaPastas> = Arc::new(pastas.pastas.iter().map(|p| (p.id.clone(), p.clone())).collect());
		let mut ordem: Vec<&PastaTuta> = pastas.pastas.iter().filter(|p| !p.e_marcador()).collect();
		// Entrada e Enviados primeiro, as pastas de corpo, e por último as contadas.
		ordem.sort_by_key(|p| (papeis.get(&p.id) != Some(&Papel::Ler), p.tipo != KIND_ENTRADA));
		for pasta in ordem {
			let Some(papel) = papeis.get(&pasta.id).copied() else {
				continue;
			};
			self.ler_pasta(&caixa, pasta, papel, &mapa, &mut r).await?;
			self.pulsar_se_vencido().await;
		}
		if !self.opcoes.seco {
			self.varredura_funda(&caixa, &pastas, &papeis, &mapa, &mut r).await?;
			self.fechar_dia(&caixa, &pastas, &papeis, &mapa, &mut r).await?;
			if self.emails_ligados() {
				self.ilegiveis_vencidos(&caixa, &papeis, &mapa, &mut r).await?;
			}
		}
		self.contadores.pastas_ilegiveis = i64::from(r.pastas_ilegiveis);
		self.contadores.processando_regra = i64::from(r.esperando_regra);
		if !self.opcoes.seco {
			self.estado.ultima_leitura_ok_em_ms = Some(self.agora());
		}
		self.salvar()?;
		Ok(r)
	}

	fn emails_ligados(&self) -> bool {
		!self.opcoes.contar && !self.opcoes.seco
	}

	/// Uma falha da conta inteira: o que muda no leitor.
	fn parar(&mut self, f: FalhaTuta) -> Parada {
		match &f {
			FalhaTuta::SessaoCaiu => {
				self.caixa = None;
				self.sessao_recusada = self.token_da_caixa.take();
			},
			FalhaTuta::VersaoRecusada => {
				self.caixa = None;
				self.parado_por_versao = true;
			},
			_ => {},
		}
		Parada::Tuta(f)
	}

	async fn garantir_caixa(&mut self) -> Result<Caixa, Parada> {
		let reabrir = u64::try_from(config::CAIXA_REABRIR_A_CADA.as_millis()).unwrap_or(u64::MAX);
		if let Some(c) = &self.caixa {
			let velha = self.agora().saturating_sub(self.caixa_aberta_em) >= reabrir;
			if !c.suspeita() && !velha {
				return Ok(c.clone());
			}
			if c.suspeita() {
				log::info!("o SDK teve pânico na volta anterior: abrindo a sessão de novo");
			}
		}
		self.caixa = None;
		if self.parado_por_versao {
			return Err(Parada::Tuta(FalhaTuta::VersaoRecusada));
		}
		let sessao = match chaveiro::ler_sessao(&*self.deps.cofre, self.deps.conta) {
			Ok(Some(s)) => s,
			Ok(None) => return Err(Parada::SemSessao),
			Err(e) => return Err(Parada::Cofre(e.to_string())),
		};
		if self.sessao_recusada.as_ref() == Some(&sessao.access_token) {
			// O Tuta já disse que esta sessão caiu: não insistir. Uma sessão nova
			// (`tuta-conector entrar`) tem outro token e é tentada.
			return Err(Parada::Tuta(FalhaTuta::SessaoCaiu));
		}
		self.email_conta = sessao.login.trim().to_lowercase();
		if self.estado.conta.as_deref().is_some_and(|c| c != self.email_conta) {
			// O estado local é de OUTRA conta (alguém entrou com outra): os
			// cursores e registros não valem aqui. Começa do zero.
			log::warn!("o estado local era de outra conta do Tuta: cursores zerados");
			let sessao_criada = self.estado.sessao_criada_em_ms;
			let instancia = self.estado.instancia.clone();
			self.estado = EstadoLocal {
				sessao_criada_em_ms: sessao_criada,
				instancia,
				..EstadoLocal::default()
			};
		}
		self.estado.conta = Some(self.email_conta.clone());
		match Caixa::abrir(&self.deps.tuta_url, self.deps.rest_tuta.clone(), sessao.credenciais()).await {
			Ok(c) => {
				self.sessao_recusada = None;
				self.token_da_caixa = Some(sessao.access_token.clone());
				self.caixa = Some(c.clone());
				self.caixa_aberta_em = self.agora();
				Ok(c)
			},
			Err(FalhaTuta::SessaoCaiu) => {
				self.sessao_recusada = Some(sessao.access_token.clone());
				Err(Parada::Tuta(FalhaTuta::SessaoCaiu))
			},
			Err(FalhaTuta::Ilegivel(m)) => Err(Parada::Login(m)),
			Err(f) => Err(self.parar(f)),
		}
	}

	async fn canario_se_vencido(&mut self) -> Result<(), Parada> {
		if self.opcoes.seco {
			return Ok(());
		}
		let agora = self.agora();
		// Parado por 474, confere de novo a cada 6 h (um 474 de passagem não
		// deixa o conector parado para sempre); senão, 1x por dia.
		let a_cada = if self.parado_por_versao {
			config::CANARIO_PARADO_A_CADA
		} else {
			config::CANARIO_A_CADA
		};
		let a_cada = u64::try_from(a_cada.as_millis()).unwrap_or(u64::MAX);
		if self.estado.ultimo_canario_ms.is_some_and(|u| agora.saturating_sub(u) < a_cada) {
			return Ok(());
		}
		let versao = canario::conferir_versao(&self.deps.tuta_url, &self.deps.rest_tuta, config::versao_sdk()).await;
		let releases = canario::releases_atras(&self.deps.url_releases, &self.deps.rest_github, config::versao_sdk()).await;
		let anterior = self.estado.canario.clone().unwrap_or_default();
		let (releases_atras, mais_nova) = match releases {
			Releases::Atras { quantas, mais_nova } => (Some(quantas), mais_nova),
			// O GitHub não respondeu: fica o que se sabia.
			Releases::Indefinido => (anterior.releases_atras, anterior.mais_nova),
		};
		// Sem resposta do Tuta: tenta de novo em 1 h (não espera o dia).
		let respondeu = matches!(versao, Canario::Aceita | Canario::Recusada);
		let tentar_em = if respondeu {
			agora
		} else {
			agora.saturating_sub(a_cada).saturating_add(HORA_MS)
		};
		self.estado.ultimo_canario_ms = Some(tentar_em);
		self.estado.canario = Some(CanarioGuardado {
			versao: match versao {
				Canario::Aceita => "aceita",
				Canario::Recusada => "recusada",
				_ => "indefinido",
			}
			.to_owned(),
			releases_atras,
			mais_nova,
		});
		match versao {
			Canario::Recusada => return Err(self.parar(FalhaTuta::VersaoRecusada)),
			Canario::Aceita if self.parado_por_versao => {
				log::info!("o canário voltou a aceitar a versão: a leitura recomeça");
				self.parado_por_versao = false;
			},
			_ => {},
		}
		Ok(())
	}

	/// /v2/sync (a lista vai quando muda, de hora em hora ou quando a Central
	/// não conhecia uma pasta) → o papel de cada pasta.
	async fn papeis(&mut self, caixa: &Caixa, pastas: &Pastas) -> Result<HashMap<String, Papel>, Parada> {
		if self.opcoes.seco {
			return Ok(pastas.pastas.iter().map(|p| (p.id.clone(), Papel::Ler)).collect());
		}
		let mut assinatura: Vec<String> = pastas
			.pastas
			.iter()
			.map(|p| format!("{}|{}|{}|{}", p.id, p.nome, p.tipo, p.pai.clone().unwrap_or_default()))
			.collect();
		assinatura.sort();
		let assinatura = assinatura.join("\n");
		let agora = self.agora();
		let reenvio = u64::try_from(config::PASTAS_REENVIO_A_CADA.as_millis()).unwrap_or(u64::MAX);
		let mandar_lista = self.forcar_sync
			|| match &self.pastas_enviadas {
				Some((a, em)) => *a != assinatura || agora.saturating_sub(*em) >= reenvio,
				None => true,
			};
		let (folders, aliases) = if mandar_lista {
			let aliases = match caixa.aliases().await {
				Ok(a) => Some(a),
				Err(f) if f.geral() => return Err(self.parar(f)),
				Err(_) => None,
			};
			let lista = pastas
				.pastas
				.iter()
				.filter(|p| !p.id.is_empty())
				.map(|p| PastaParaCentral {
					key: p.id.clone(),
					name: texto_de_pasta(&p.nome),
					path: texto_de_pasta(&p.caminho),
					kind: p.tipo,
					parent: p.pai.clone(),
				})
				.collect();
			(Some(lista), aliases)
		} else {
			(None, None)
		};
		if let Some(a) = &aliases {
			self.aliases_conta = Arc::new(a.iter().cloned().collect());
		}
		let corpo = Sincronia {
			instance: self.estado.instancia.clone().unwrap_or_else(instancia_nova),
			agent_version: config::versao_completa(),
			tuta_version: config::versao_sdk().to_owned(),
			counters: self.contadores_do_sync(),
			folders,
			folders_complete: pastas.ilegiveis == 0,
			aliases,
		};
		let davinci = self.deps.davinci.clone().ok_or(Parada::DavinciFora(ErroDavinci::Rede))?;
		let resposta = match davinci.sincronizar(&corpo).await {
			Ok(r) => {
				if mandar_lista {
					self.pastas_enviadas = Some((assinatura, agora));
					self.forcar_sync = false;
				}
				self.leitura = Some((r.clone(), agora));
				r
			},
			Err(ErroDavinci::OutroAgente) => return Err(Parada::Duplicado),
			Err(ErroDavinci::SemV2) => return Err(Parada::SemV2),
			Err(e) if e.passageiro() => {
				let valido = u64::try_from(config::CONFIG_VALIDA_POR.as_millis()).unwrap_or(u64::MAX);
				match &self.leitura {
					Some((r, em)) if agora.saturating_sub(*em) < valido => r.clone(),
					_ => return Err(Parada::DavinciFora(e)),
				}
			},
			Err(e) => return Err(Parada::DavinciRecusou(e)),
		};
		let mut papeis = HashMap::new();
		for f in &resposta.folders {
			match f.read.as_str() {
				"corpo" => {
					papeis.insert(f.key.clone(), Papel::Ler);
				},
				"so_contar" => {
					papeis.insert(f.key.clone(), Papel::Contar);
				},
				// "nao" (uma pessoa mandou ignorar) e o que vier de novo: nada.
				_ => {},
			}
		}
		Ok(papeis)
	}

	fn so_contar_aliases(&self) -> Arc<BTreeSet<String>> {
		Arc::new(
			self.leitura
				.as_ref()
				.map(|(r, _)| r.count_only_aliases.iter().map(|a| a.trim().to_lowercase()).collect())
				.unwrap_or_default(),
		)
	}

	// ── Uma pasta ───────────────────────────────────────────────────────

	async fn ler_pasta(
		&mut self,
		caixa: &Caixa,
		pasta: &PastaTuta,
		papel: Papel,
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		let topo = match caixa
			.pagina(&pasta.lista_entradas, &entrada_id::id_maximo(), config::PAGINA_TOPO, true)
			.await
		{
			Ok(t) => t,
			Err(f) if f.geral() => return Err(self.parar(f)),
			Err(_) => {
				r.pastas_ilegiveis += 1;
				return Ok(());
			},
		};
		r.pastas_lidas += 1;
		if pasta.tipo == KIND_ENVIADOS {
			// A CONTA inteira na última hora (equipe, robôs e o conector): o teto de 100/h do Tuta.
			let desde = self.agora().saturating_sub(HORA_MS);
			let n = topo.iter().filter(|e| e.recebido_ms >= desde).count();
			self.deps
				.compartilhado
				.enviados_conta_hora
				.store(u32::try_from(n).unwrap_or(u32::MAX), Atomica::Relaxed);
		}
		if self.opcoes.seco {
			return self.ler_pasta_seco(caixa, pasta, &topo, r).await;
		}
		if papel == Papel::Ler && self.emails_ligados() {
			self.novos_da_pasta(caixa, pasta, &topo, mapa, r).await?;
		}
		// Só a janela COBERTA (desde a primeira carga): e-mail mais velho que
		// isso nunca é pedido de volta (não vira pendência de meses atrás).
		let cobertura = self.cobertura(pasta);
		let completa_desde = if topo.len() < config::PAGINA_TOPO {
			Some(cobertura)
		} else {
			topo.last().map(|e| (e.recebido_ms + 1024).max(cobertura))
		};
		let ids: Vec<IdTupleGenerated> = topo.iter().filter(|e| e.recebido_ms >= cobertura).map(|e| e.mail.clone()).collect();
		let contagem = self
			.contar(
				pasta,
				Janela {
					ids: &ids,
					desde: completa_desde,
					ate: None,
					dia: None,
				},
				r,
			)
			.await?;
		self.depois_da_contagem(caixa, pasta, papel, &contagem, mapa, r).await
	}

	/// O que a contagem disse: o que falta vai de novo (pasta de corpo) e o que
	/// saiu da pasta é conferido no Tuta (movido para onde, ou apagado).
	async fn depois_da_contagem(
		&mut self,
		caixa: &Caixa,
		pasta: &PastaTuta,
		papel: Papel,
		contagem: &RespostaContagem,
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		if papel == Papel::Ler && self.emails_ligados() && !contagem.missing.is_empty() {
			self.faltando(caixa, pasta, &contagem.missing, mapa, r).await?;
		}
		if !contagem.left.is_empty() {
			self.resolver_saidos(caixa, &contagem.left, mapa, r).await?;
		}
		Ok(())
	}

	/// `--seco`: lê só os 3 mais novos de cada pasta (para provar que decifra) e
	/// não manda nada.
	async fn ler_pasta_seco(
		&mut self,
		caixa: &Caixa,
		pasta: &PastaTuta,
		topo: &[crate::leitura::caixa::Entrada],
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		r.entradas_no_topo += u32::try_from(topo.len()).unwrap_or(u32::MAX);
		for e in topo.iter().take(3) {
			match caixa.email(&e.mail, pasta.tipo != crate::leitura::caixa::KIND_SPAM).await {
				Ok(_) => r.emails_novos += 1,
				Err(f) if f.geral() => return Err(self.parar(f)),
				Err(FalhaTuta::Sumiu) => {},
				Err(_) => r.ilegiveis += 1,
			}
		}
		Ok(())
	}

	/// Desde quando a pasta é coberta (a primeira carga; sem ela, a janela
	/// da primeira carga contada de agora).
	fn cobertura(&self, pasta: &PastaTuta) -> u64 {
		self.estado
			.cursores
			.get(&pasta.id)
			.and_then(|c| c.inicio_cobertura_ms)
			.unwrap_or_else(|| self.agora().saturating_sub(config::PRIMEIRA_CARGA_DIAS * DIA_MS))
	}

	/// Os e-mails acima do cursor da pasta (do mais velho para o mais novo).
	async fn novos_da_pasta(
		&mut self,
		caixa: &Caixa,
		pasta: &PastaTuta,
		topo: &[crate::leitura::caixa::Entrada],
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		let agora = self.agora();
		let mut cursor = self.estado.cursores.get(&pasta.id).cloned().unwrap_or_default();
		if cursor.inicio_cobertura_ms.is_none() {
			cursor.inicio_cobertura_ms = Some(agora.saturating_sub(config::PRIMEIRA_CARGA_DIAS * DIA_MS));
		}
		let inicio = cursor
			.ultimo_entregue
			.clone()
			.unwrap_or_else(|| entrada_id::id_do_instante(cursor.inicio_cobertura_ms.unwrap_or(0)));
		// Nada acima do cursor no topo da pasta: nem pede a página de novos.
		let nada_novo = topo
			.first()
			.is_none_or(|mais_nova| entrada_id::comparar(&mais_nova.id, &inicio) != Ordering::Greater);
		if nada_novo {
			if cursor.ultimo_entregue.is_none() {
				cursor.ultimo_entregue = Some(inicio);
			}
			self.estado.cursores.insert(pasta.id.clone(), cursor);
			return Ok(());
		}
		let mut entradas = Vec::new();
		let mut desde = inicio.clone();
		for _ in 0..config::PAGINAS_MAX_POR_PASTA {
			let pagina = match caixa.pagina(&pasta.lista_entradas, &desde, config::PAGINA_TOPO, false).await {
				Ok(p) => p,
				Err(f) if f.geral() => return Err(self.parar(f)),
				Err(_) => {
					r.pastas_ilegiveis += 1;
					return Ok(());
				},
			};
			let n = pagina.len();
			if let Some(u) = pagina.last() {
				desde = u.id.clone();
			}
			entradas.extend(pagina);
			if n < config::PAGINA_TOPO || entradas.len() >= config::EMAILS_MAX_POR_PASTA_VOLTA {
				break;
			}
		}
		entradas.truncate(config::EMAILS_MAX_POR_PASTA_VOLTA);
		if entradas.is_empty() {
			if cursor.ultimo_entregue.is_none() {
				// Primeira carga sem nada na janela: o cursor fica no começo dela.
				cursor.ultimo_entregue = Some(inicio);
			}
			self.estado.cursores.insert(pasta.id.clone(), cursor);
			return Ok(());
		}
		let ja: HashSet<&String> = cursor.entregues_acima.iter().collect();
		let itens: Vec<Item> = entradas
			.iter()
			.filter(|e| !ja.contains(&e.id))
			.map(|e| Item {
				chave: e.id.clone(),
				mail: e.mail.clone(),
				pasta: pasta.clone(),
			})
			.collect();
		r.emails_novos += u32::try_from(itens.len()).unwrap_or(u32::MAX);
		let (desfechos, parada) = self.entregar(caixa, itens, mapa, r).await;
		// O cursor anda até o primeiro buraco; o que foi entregue acima dele fica guardado.
		let mut c = cursor.clone();
		let mut buraco = false;
		for e in &entradas {
			let d = if ja.contains(&e.id) {
				Desfecho::Entregue
			} else {
				desfechos.get(&e.id).copied().unwrap_or(Desfecho::Pendente)
			};
			match d {
				Desfecho::Entregue | Desfecho::Registrado if !buraco => c.ultimo_entregue = Some(e.id.clone()),
				Desfecho::Entregue | Desfecho::Registrado => c.entregues_acima.push(e.id.clone()),
				Desfecho::Pendente => buraco = true,
			}
		}
		if let Some(base) = c.ultimo_entregue.clone() {
			c.entregues_acima.retain(|id| entrada_id::comparar(id, &base) == Ordering::Greater);
		}
		c.entregues_acima.sort_by(|a, b| entrada_id::comparar(a, b));
		c.entregues_acima.dedup();
		if c.entregues_acima.len() > config::REGISTRO_MAX_ITENS {
			let sobra = c.entregues_acima.len() - config::REGISTRO_MAX_ITENS;
			c.entregues_acima.drain(..sobra);
		}
		self.estado.cursores.insert(pasta.id.clone(), c);
		self.salvar()?;
		match parada {
			Some(p) => Err(p),
			None => Ok(()),
		}
	}

	/// O que a contagem disse que falta na Central (pasta de corpo).
	async fn faltando(
		&mut self,
		caixa: &Caixa,
		pasta: &PastaTuta,
		faltando: &[String],
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		let agora = self.agora();
		let itens: Vec<Item> = faltando
			.iter()
			.filter_map(|s| pacote::id_do_source(s))
			.map(|mail| (chave_email(&mail), mail))
			.filter(|(chave, _)| {
				!self.pendentes_volta.contains(chave) && !self.estado.so_contados.contains(chave) && self.pode_tentar(chave, agora)
			})
			.map(|(chave, mail)| Item {
				chave,
				mail,
				pasta: pasta.clone(),
			})
			.collect();
		if itens.is_empty() {
			return Ok(());
		}
		r.faltando += u32::try_from(itens.len()).unwrap_or(u32::MAX);
		let (_, parada) = self.entregar(caixa, itens, mapa, r).await;
		self.salvar()?;
		match parada {
			Some(p) => Err(p),
			None => Ok(()),
		}
	}

	/// Os e-mails que saíram de uma pasta (a contagem disse): onde estão agora
	/// no Tuta → /v2/changes (movido para pasta que não se conta, ou apagado de vez).
	async fn resolver_saidos(
		&mut self,
		caixa: &Caixa,
		saidos: &[String],
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		let mut mudancas = Vec::new();
		for source in saidos.iter().take(config::SAIDOS_POR_VOLTA) {
			let Some(id) = pacote::id_do_source(source) else {
				continue;
			};
			match caixa.email(&id, false).await {
				Ok(e) => {
					let Some(onde) = e
						.mail
						.sets
						.iter()
						.filter_map(|s| mapa.get(s.element_id.as_str()))
						.find(|p| p.tipo != KIND_MARCADOR && p.tipo != KIND_TODOS)
					else {
						continue;
					};
					mudancas.push(Mudanca {
						source_id: source.clone(),
						folder_key: Some(onde.id.clone()),
						apagado: onde.tipo == KIND_LIXEIRA,
					});
					r.movidos += 1;
				},
				Err(FalhaTuta::Sumiu) => {
					mudancas.push(Mudanca {
						source_id: source.clone(),
						folder_key: None,
						apagado: true,
					});
					r.apagados += 1;
				},
				Err(f) if f.geral() => return Err(self.parar(f)),
				Err(_) => {},
			}
		}
		if mudancas.is_empty() {
			return Ok(());
		}
		self.contadores.movidos += mudancas.iter().filter(|m| m.folder_key.is_some()).count() as i64;
		self.contadores.apagados += mudancas.iter().filter(|m| m.apagado).count() as i64;
		let davinci = self.deps.davinci.clone().ok_or(Parada::DavinciFora(ErroDavinci::Rede))?;
		match davinci.mudancas(&mudancas).await {
			Ok(_) => Ok(()),
			Err(e) if e.passageiro() => Err(Parada::DavinciFora(e)),
			Err(e) => Err(Parada::DavinciRecusou(e)),
		}
	}

	/// Pode tentar este e-mail agora? (ilegível/recusado só quando vence a espera).
	fn pode_tentar(&self, chave: &str, agora: u64) -> bool {
		let vencido = |p: &Pendencia| p.proxima_ms <= agora;
		self.estado.ilegiveis.get(chave).is_none_or(vencido) && self.estado.recusados.get(chave).is_none_or(vencido)
	}

	// ── Entregar ────────────────────────────────────────────────────────

	/// Carrega (até `LEITURA_CONCORRENCIA` ao mesmo tempo, na ordem), monta os
	/// pacotes, manda em lotes e devolve o desfecho de cada item. Uma parada
	/// no meio devolve os desfechos até ali (o resto fica Pendente).
	async fn entregar(
		&mut self,
		caixa: &Caixa,
		itens: Vec<Item>,
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> (HashMap<String, Desfecho>, Option<Parada>) {
		let mut desfechos = HashMap::new();
		let agora = self.agora();
		let aliases = self.aliases_conta.clone();
		let so_contar = self.so_contar_aliases();
		let espera_regra_ms = u64::try_from(config::ESPERA_REGRA.as_millis()).unwrap_or(u64::MAX);
		let mut pendentes: Vec<String> = Vec::new();
		let mut fluxo = futures::stream::iter(itens.into_iter().map(|item| {
			let caixa = caixa.clone();
			let mapa = mapa.clone();
			let aliases = aliases.clone();
			let so_contar = so_contar.clone();
			async move { carregar(caixa, item, mapa, aliases, so_contar, agora, espera_regra_ms).await }
		}))
		.buffered(config::LEITURA_CONCORRENCIA);
		let mut lote: Vec<(Item, Value)> = Vec::new();
		let mut bytes_lote = 0usize;
		let mut parada: Option<Parada> = None;
		while let Some(Carregado { item, carga }) = fluxo.next().await {
			if parada.is_some() {
				pendentes.push(chave_email(&item.mail));
				desfechos.insert(item.chave.clone(), Desfecho::Pendente);
				continue;
			}
			match carga {
				Carga::Pacote(mut pacote) => {
					if pacote::caber(&mut pacote, config::LOTE_MAX_BYTES.saturating_sub(64 * 1024)) {
						r.cortados += 1;
					}
					let tamanho = pacote::tamanho(&pacote);
					let n_anexos = pacote["attachments"].as_array().map_or(0, Vec::len);
					let n_omitidos = pacote["omitted_attachments"].as_array().map_or(0, Vec::len);
					r.anexos += u32::try_from(n_anexos).unwrap_or(0);
					r.anexos_pulados += u32::try_from(n_omitidos).unwrap_or(0);
					if !lote.is_empty() && (lote.len() >= config::LOTE_MAX_EMAILS || bytes_lote + tamanho > config::LOTE_MAX_BYTES) {
						let enviados = std::mem::take(&mut lote);
						bytes_lote = 0;
						if let Err(p) = self.postar(enviados, &mut desfechos, r).await {
							parada = Some(p);
							pendentes.push(chave_email(&item.mail));
							desfechos.insert(item.chave.clone(), Desfecho::Pendente);
							continue;
						}
						self.pulsar_se_vencido().await;
					}
					bytes_lote += tamanho;
					lote.push((item, pacote));
				},
				Carga::Esperar => {
					r.esperando_regra += 1;
					pendentes.push(chave_email(&item.mail));
					desfechos.insert(item.chave.clone(), Desfecho::Pendente);
				},
				Carga::SoContado => {
					r.so_contados += 1;
					self.contadores.so_contados += 1;
					self.estado.so_contados.insert(chave_email(&item.mail));
					while self.estado.so_contados.len() > config::REGISTRO_MAX_ITENS {
						self.estado.so_contados.pop_first();
					}
					desfechos.insert(item.chave.clone(), Desfecho::Registrado);
				},
				Carga::Pular => {
					desfechos.insert(item.chave.clone(), Desfecho::Registrado);
				},
				Carga::Recusar(motivo) => {
					r.recusados += 1;
					self.registrar(false, &chave_email(&item.mail), &item.pasta.id, motivo);
					desfechos.insert(item.chave.clone(), Desfecho::Registrado);
				},
				Carga::Falhou(FalhaTuta::Sumiu) => {
					r.sumidos += 1;
					desfechos.insert(item.chave.clone(), Desfecho::Registrado);
				},
				Carga::Falhou(f) if f.geral() => {
					parada = Some(self.parar(f));
					pendentes.push(chave_email(&item.mail));
					desfechos.insert(item.chave.clone(), Desfecho::Pendente);
				},
				Carga::Falhou(f) => {
					r.ilegiveis += 1;
					log::warn!("e-mail ilegível na pasta {} ({}): contado, a volta segue", item.pasta.id, f.codigo());
					self.registrar(true, &chave_email(&item.mail), &item.pasta.id, &f.codigo());
					desfechos.insert(item.chave.clone(), Desfecho::Registrado);
				},
			}
		}
		if !lote.is_empty() {
			if parada.is_none() {
				if let Err(p) = self.postar(lote, &mut desfechos, r).await {
					parada = Some(p);
				}
			} else {
				for (item, _) in lote {
					pendentes.push(chave_email(&item.mail));
					desfechos.insert(item.chave, Desfecho::Pendente);
				}
			}
		}
		self.pendentes_volta.extend(pendentes);
		(desfechos, parada)
	}

	/// Registra um ilegível (ou recusado) com a próxima tentativa.
	fn registrar(&mut self, ilegivel: bool, chave_mail: &str, pasta: &str, motivo: &str) {
		let agora = self.agora();
		let registro = if ilegivel {
			&mut self.estado.ilegiveis
		} else {
			&mut self.estado.recusados
		};
		let p = registro.entry(chave_mail.to_owned()).or_default();
		p.pasta = pasta.to_owned();
		p.motivo = motivo.chars().take(60).collect();
		let espera = if ilegivel {
			let i = usize::try_from(p.tentativas).unwrap_or(usize::MAX).min(config::ILEGIVEL_ESPERAS.len() - 1);
			config::ILEGIVEL_ESPERAS[i]
		} else {
			config::RECUSADO_ESPERA
		};
		p.tentativas = p.tentativas.saturating_add(1);
		p.proxima_ms = agora + u64::try_from(espera.as_millis()).unwrap_or(u64::MAX / 2);
		if registro.len() > config::REGISTRO_MAX_ITENS {
			// Tira o mais antigo (o de menor próxima tentativa).
			if let Some(velho) = registro.iter().min_by_key(|(_, p)| p.proxima_ms).map(|(k, _)| k.clone()) {
				registro.remove(&velho);
			}
		}
	}

	fn passageiro(&mut self, chave_mail: &str, pasta: &str, r: &mut ResumoVolta) -> Desfecho {
		let seguidas = self.estado.passageiros.entry(chave_mail.to_owned()).or_insert(0);
		*seguidas += 1;
		if *seguidas >= config::PASSAGEIRO_MAX_SEGUIDAS {
			self.estado.passageiros.remove(chave_mail);
			r.recusados += 1;
			log::warn!(
				"a Central devolveu erro passageiro {} vezes seguidas para um e-mail da pasta {pasta}: registrado, tenta 1x por dia",
				config::PASSAGEIRO_MAX_SEGUIDAS
			);
			self.registrar(false, chave_mail, pasta, "passageiro_repetido");
			Desfecho::Registrado
		} else {
			self.pendentes_volta.insert(chave_mail.to_owned());
			Desfecho::Pendente
		}
	}

	/// POST /v2/ingest de um lote; parte ao meio no 413/422 do corpo inteiro.
	async fn postar(
		&mut self,
		lote: Vec<(Item, Value)>,
		desfechos: &mut HashMap<String, Desfecho>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		let Some(davinci) = self.deps.davinci.clone() else {
			for (item, _) in lote {
				self.pendentes_volta.insert(chave_email(&item.mail));
				desfechos.insert(item.chave, Desfecho::Pendente);
			}
			return Ok(());
		};
		let pacotes: Vec<&Value> = lote.iter().map(|(_, p)| p).collect();
		let corpo = pacote::lote(&pacotes);
		r.lotes += 1;
		match davinci.ingerir(corpo).await {
			Ok(resultados) => {
				let por_id: HashMap<String, ResultadoEmail> =
					resultados.into_iter().filter_map(|x| Some((x.source_id.clone()?, x))).collect();
				for (item, _) in lote {
					let chave_mail = chave_email(&item.mail);
					let d = match por_id.get(&pacote::source_id(&item.mail)) {
						Some(x) if x.status == "accepted" || x.status == "duplicate" => {
							r.entregues += 1;
							self.contadores.lidos += 1;
							self.estado.ilegiveis.remove(&chave_mail);
							self.estado.recusados.remove(&chave_mail);
							self.estado.passageiros.remove(&chave_mail);
							Desfecho::Entregue
						},
						Some(x) if x.status == "rejected" && matches!(x.code.as_deref(), Some("folder_unknown" | "folder_not_read")) => {
							// A Central não lê (mais) esta pasta com corpo: a lista vai de novo
							// e o e-mail espera (nada se perde; o cursor não anda).
							self.forcar_sync = true;
							self.pendentes_volta.insert(chave_mail.clone());
							Desfecho::Pendente
						},
						Some(x) if x.status == "rejected" => {
							r.recusados += 1;
							let codigo = x.code.clone().unwrap_or_else(|| "rejected".into());
							let campos: Vec<String> = x
								.fields
								.iter()
								.filter_map(|f| f.get("field").and_then(Value::as_str).map(str::to_owned))
								.collect();
							log::warn!(
								"a Central recusou um e-mail da pasta {} ({codigo}; campos: {}): não reenvia",
								item.pasta.id,
								campos.join(",")
							);
							self.registrar(false, &chave_mail, &item.pasta.id, &codigo);
							Desfecho::Registrado
						},
						_ => self.passageiro(&chave_mail, &item.pasta.id, r),
					};
					desfechos.insert(item.chave, d);
				}
				Ok(())
			},
			Err(ErroDavinci::GrandeDemais | ErroDavinci::Invalido(_)) if lote.len() > 1 => {
				let mut lote = lote;
				let segunda = lote.split_off(lote.len() / 2);
				Box::pin(self.postar(lote, desfechos, r)).await?;
				Box::pin(self.postar(segunda, desfechos, r)).await
			},
			Err(e @ (ErroDavinci::GrandeDemais | ErroDavinci::Invalido(_))) => {
				for (item, _) in lote {
					r.recusados += 1;
					let motivo = if e == ErroDavinci::GrandeDemais { "grande_demais" } else { "corpo_invalido" };
					self.registrar(false, &chave_email(&item.mail), &item.pasta.id, motivo);
					desfechos.insert(item.chave, Desfecho::Registrado);
				}
				Ok(())
			},
			Err(e) => {
				for (item, _) in lote {
					self.pendentes_volta.insert(chave_email(&item.mail));
					desfechos.insert(item.chave, Desfecho::Pendente);
				}
				Err(match e {
					ErroDavinci::SemV2 => Parada::SemV2,
					e if e.passageiro() => Parada::DavinciFora(e),
					e => Parada::DavinciRecusou(e),
				})
			},
		}
	}

	// ── Contagem, varredura funda, fechamento do dia ────────────────────

	async fn contar(&mut self, pasta: &PastaTuta, janela: Janela<'_>, r: &mut ResumoVolta) -> Result<RespostaContagem, Parada> {
		if pasta.e_marcador() {
			return Ok(RespostaContagem::default());
		}
		let Some(davinci) = self.deps.davinci.clone() else {
			return Ok(RespostaContagem::default());
		};
		// O que de propósito NÃO subiu (só aliases internos) não entra na conta.
		let lista: Vec<String> = janela
			.ids
			.iter()
			.filter(|i| !self.estado.so_contados.contains(&chave_email(i)))
			.take(config::CONTAGEM_MAX_IDS)
			.map(pacote::source_id)
			.collect();
		let completa = janela.desde.is_some() && janela.ids.len() <= config::CONTAGEM_MAX_IDS;
		let corpo = json!({
			"folder_key": pasta.id,
			"ids": lista,
			"complete": completa,
			"since": janela.desde.map(iso),
			"until": janela.ate.map(iso),
			"day": janela.dia,
			"total": janela.ids.len(),
		});
		r.contagens += 1;
		let resposta = match davinci.contar(&corpo).await {
			Ok(x) => x,
			Err(ErroDavinci::Conflito(c)) if c == "folder_unknown" => {
				// Pasta criada depois do último /v2/sync: a lista vai de novo.
				self.forcar_sync = true;
				return Ok(RespostaContagem::default());
			},
			Err(ErroDavinci::SemV2) => return Err(Parada::SemV2),
			Err(e) if e.passageiro() => return Err(Parada::DavinciFora(e)),
			Err(e) => return Err(Parada::DavinciRecusou(e)),
		};
		r.movidos += u32::try_from(resposta.moved).unwrap_or(u32::MAX);
		self.contadores.movidos += i64::try_from(resposta.moved).unwrap_or(0);
		Ok(resposta)
	}

	/// Os ids de uma pasta do topo até `desde_ms` (e abaixo de `abaixo_de`).
	/// Devolve (ids, chegou_ao_fim_da_janela).
	async fn ids_da_janela(
		&mut self,
		caixa: &Caixa,
		pasta: &PastaTuta,
		abaixo_de: &str,
		desde_ms: u64,
	) -> Result<Option<(Vec<IdTupleGenerated>, bool)>, Parada> {
		let mut ids = Vec::new();
		let mut inicio = abaixo_de.to_owned();
		let paginas = config::CONTAGEM_MAX_IDS / config::PAGINA_IDS;
		for _ in 0..paginas {
			let pagina = match caixa.pagina(&pasta.lista_entradas, &inicio, config::PAGINA_IDS, true).await {
				Ok(p) => p,
				Err(f) if f.geral() => return Err(self.parar(f)),
				Err(_) => return Ok(None),
			};
			let n = pagina.len();
			for e in &pagina {
				if e.recebido_ms < desde_ms {
					return Ok(Some((ids, true)));
				}
				ids.push(e.mail.clone());
			}
			match pagina.last() {
				Some(u) if n == config::PAGINA_IDS => inicio = u.id.clone(),
				_ => return Ok(Some((ids, true))),
			}
		}
		Ok(Some((ids, false)))
	}

	/// Uma pasta por volta, a mais tempo sem varredura (no máximo 1x por hora):
	/// todos os ids dos últimos 30 dias → contagem completa → o que faltar vai
	/// de novo e o que saiu é conferido.
	async fn varredura_funda(
		&mut self,
		caixa: &Caixa,
		pastas: &Pastas,
		papeis: &HashMap<String, Papel>,
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		let agora = self.agora();
		let a_cada = u64::try_from(config::VARREDURA_FUNDA_A_CADA.as_millis()).unwrap_or(u64::MAX);
		let escolhida = pastas
			.pastas
			.iter()
			.filter(|p| !p.e_marcador() && papeis.contains_key(&p.id))
			.map(|p| {
				let ultima = self.estado.cursores.get(&p.id).and_then(|c| c.ultima_varredura_funda_ms);
				(ultima, p)
			})
			.filter(|(u, _)| u.is_none_or(|u| agora.saturating_sub(u) >= a_cada))
			.min_by_key(|(u, _)| u.unwrap_or(0))
			.map(|(_, p)| p.clone());
		let Some(pasta) = escolhida else {
			return Ok(());
		};
		let papel = papeis.get(&pasta.id).copied().unwrap_or(Papel::Contar);
		let desde = agora.saturating_sub(config::VARREDURA_FUNDA_DIAS * DIA_MS).max(self.cobertura(&pasta));
		let Some((ids, inteira)) = self.ids_da_janela(caixa, &pasta, &entrada_id::id_maximo(), desde).await? else {
			// A pasta não deu agora: fica para a próxima rodada (sem travar o rodízio).
			r.pastas_ilegiveis += 1;
			self.estado.cursores.entry(pasta.id.clone()).or_default().ultima_varredura_funda_ms = Some(agora);
			return Ok(());
		};
		r.varreduras += 1;
		let contagem = self
			.contar(
				&pasta,
				Janela {
					ids: &ids,
					desde: inteira.then_some(desde),
					ate: None,
					dia: None,
				},
				r,
			)
			.await?;
		self.estado.cursores.entry(pasta.id.clone()).or_default().ultima_varredura_funda_ms = Some(agora);
		self.salvar()?;
		self.depois_da_contagem(caixa, &pasta, papel, &contagem, mapa, r).await
	}

	/// Depois das 06:00 (Brasília), fecha o dia anterior: uma pasta por volta,
	/// contagem com `day` (a linha da conciliação diária na Central).
	async fn fechar_dia(
		&mut self,
		caixa: &Caixa,
		pastas: &Pastas,
		papeis: &HashMap<String, Papel>,
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		let Some((dia, inicio, fim)) = dia_para_fechar(self.agora()) else {
			return Ok(());
		};
		if self.estado.conciliacao.as_ref().is_none_or(|c| c.dia != dia) {
			self.estado.conciliacao = Some(Conciliacao {
				dia: dia.clone(),
				feitas: Vec::new(),
			});
		}
		let feitas = self.estado.conciliacao.as_ref().map(|c| c.feitas.clone()).unwrap_or_default();
		let Some(pasta) = pastas
			.pastas
			.iter()
			.find(|p| !p.e_marcador() && papeis.contains_key(&p.id) && !feitas.contains(&p.id))
			.cloned()
		else {
			return Ok(());
		};
		let papel = papeis.get(&pasta.id).copied().unwrap_or(Papel::Contar);
		let inicio = inicio.max(self.cobertura(&pasta).min(fim));
		let Some((ids, inteira)) = self.ids_da_janela(caixa, &pasta, &entrada_id::id_do_instante(fim), inicio).await? else {
			// A pasta não deu: o dia dela fica sem linha (as outras seguem).
			r.pastas_ilegiveis += 1;
			if let Some(c) = self.estado.conciliacao.as_mut() {
				c.feitas.push(pasta.id.clone());
			}
			return Ok(());
		};
		let contagem = self
			.contar(
				&pasta,
				Janela {
					ids: &ids,
					desde: inteira.then_some(inicio),
					ate: Some(fim),
					dia: Some(&dia),
				},
				r,
			)
			.await?;
		r.dias_fechados += 1;
		if let Some(c) = self.estado.conciliacao.as_mut() {
			c.feitas.push(pasta.id.clone());
		}
		self.salvar()?;
		self.depois_da_contagem(caixa, &pasta, papel, &contagem, mapa, r).await
	}

	/// Ilegíveis e recusados cuja espera venceu: tenta de novo (até 20 por volta).
	async fn ilegiveis_vencidos(
		&mut self,
		caixa: &Caixa,
		papeis: &HashMap<String, Papel>,
		mapa: &Arc<MapaPastas>,
		r: &mut ResumoVolta,
	) -> Result<(), Parada> {
		let agora = self.agora();
		let vencidos: Vec<(String, String)> = self
			.estado
			.ilegiveis
			.iter()
			.chain(self.estado.recusados.iter())
			.filter(|(chave, p)| p.proxima_ms <= agora && !self.pendentes_volta.contains(*chave))
			.map(|(chave, p)| (chave.clone(), p.pasta.clone()))
			.take(20)
			.collect();
		let mut itens = Vec::new();
		for (chave, pasta_id) in vencidos {
			let (Some(mail), Some(pasta), Some(Papel::Ler)) = (email_da_chave(&chave), mapa.get(&pasta_id), papeis.get(&pasta_id).copied())
			else {
				// A pasta sumiu (ou não se lê mais com corpo): sai do registro. Se o
				// e-mail estiver noutra pasta lida, a contagem de lá o pede.
				self.estado.ilegiveis.remove(&chave);
				self.estado.recusados.remove(&chave);
				continue;
			};
			itens.push(Item {
				chave,
				mail,
				pasta: pasta.clone(),
			});
		}
		if itens.is_empty() {
			return Ok(());
		}
		r.tentados_de_novo += u32::try_from(itens.len()).unwrap_or(u32::MAX);
		let (_, parada) = self.entregar(caixa, itens, mapa, r).await;
		self.salvar()?;
		match parada {
			Some(p) => Err(p),
			None => Ok(()),
		}
	}
}

/// Nome/caminho de pasta numa linha (a Central recusa caractere de controle).
fn texto_de_pasta(t: &str) -> String {
	crate::texto::uma_linha(t, 1000)
}

/// Baixa os anexos que vão (tetos do contrato; perigoso, grande e ilegível
/// ficam no rastro). Uma falha da CONTA (sessão, 429, fora) volta como erro.
async fn baixar_anexos(caixa: &Caixa, email: &EmailLido) -> Result<(Vec<AnexoBaixado>, Vec<AnexoOmitido>), FalhaTuta> {
	let mut baixados = Vec::new();
	let mut omitidos = Vec::new();
	let mut total = 0u64;
	for a in &email.arquivos {
		let omitir = |motivo: &'static str, a: &ArquivoLido| AnexoOmitido {
			nome: a.nome.clone(),
			tipo: a.tipo.clone(),
			tamanho: a.tamanho,
			motivo,
		};
		if !a.legivel() {
			omitidos.push(omitir("ilegivel", a));
			continue;
		}
		if pacote::anexo_perigoso(&a.nome, &a.tipo) {
			omitidos.push(omitir("perigoso", a));
			continue;
		}
		// O tamanho é conferido ANTES de baixar (o cliente HTTP lê tudo na memória).
		if a.tamanho > config::ANEXO_MAX_BYTES {
			omitidos.push(omitir("grande_demais", a));
			continue;
		}
		if baixados.len() >= config::ANEXOS_POR_EMAIL || total + a.tamanho > config::ANEXOS_POR_EMAIL_MAX_BYTES {
			omitidos.push(omitir("teto_do_email", a));
			continue;
		}
		match caixa.baixar(a).await {
			Ok(bytes) if (bytes.len() as u64) <= config::ANEXO_MAX_BYTES => {
				total += bytes.len() as u64;
				baixados.push(AnexoBaixado {
					nome: a.nome.clone(),
					tipo: a.tipo.clone(),
					bytes,
				});
			},
			Ok(_) => omitidos.push(omitir("grande_demais", a)),
			Err(f) if f.geral() => return Err(f),
			Err(_) => omitidos.push(omitir("ilegivel", a)),
		}
	}
	Ok((baixados, omitidos))
}

/// Carrega um item e monta o pacote (fora do `&mut self`: roda em paralelo).
async fn carregar(
	caixa: Caixa,
	item: Item,
	mapa: Arc<MapaPastas>,
	aliases_conta: Arc<BTreeSet<String>>,
	so_contar: Arc<BTreeSet<String>>,
	agora: u64,
	espera_regra_ms: u64,
) -> Carregado {
	let carga = match caixa.email(&item.mail, true).await {
		Ok(e) => {
			let recebido = e.mail.receivedDate.as_millis();
			let recebeu = pacote::quem_recebeu(&e, &aliases_conta);
			// A regra do Tuta ainda vai mover (Entrada há pouco) ou ainda está enviando.
			let regra = e.mail.processNeeded && item.pasta.tipo == KIND_ENTRADA && agora.saturating_sub(recebido) < espera_regra_ms;
			if regra || e.mail.state == 3 {
				Carga::Esperar
			} else if e.mail.state == 0 {
				Carga::Pular
			} else if e.mail.state == 2 && !recebeu.is_empty() && recebeu.iter().all(|a| so_contar.contains(a)) {
				Carga::SoContado
			} else {
				match baixar_anexos(&caixa, &e).await {
					Err(f) => Carga::Falhou(f),
					Ok((anexos, omitidos)) => match pacote::completo(&e, &item.pasta, &mapa, &aliases_conta, &anexos, &omitidos) {
						Ok(p) => Carga::Pacote(p),
						Err(SemPacote::RemetenteInvalido) => Carga::Recusar("remetente_invalido"),
						Err(SemPacote::NaoEnviado) => Carga::Pular,
					},
				}
			}
		},
		Err(f) => Carga::Falhou(f),
	};
	Carregado { item, carga }
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn fechamento_do_dia_em_brasilia() {
		// 2026-10-08 05:59 BRT (08:59 UTC): ainda não fecha.
		let antes = 1_791_449_940_000u64; // 2026-10-08T08:59:00Z
		assert!(dia_para_fechar(antes).is_none());
		// 2026-10-08 06:01 BRT (09:01 UTC): fecha 2026-10-07.
		let depois = 1_791_450_060_000u64; // 2026-10-08T09:01:00Z
		let (dia, inicio, fim) = dia_para_fechar(depois).unwrap();
		assert_eq!(dia, "2026-10-07");
		assert_eq!(fim - inicio, DIA_MS);
		// 2026-10-08T03:00:00Z = meia-noite de Brasília.
		assert_eq!(fim, 1_791_428_400_000);
	}

	#[test]
	fn chaves_dos_emails() {
		let id = IdTupleGenerated::new(GeneratedId("L".into()), GeneratedId("E".into()));
		assert_eq!(chave_email(&id), "L/E");
		assert_eq!(email_da_chave("L/E"), Some(id));
		assert_eq!(email_da_chave("sem-barra"), None);
		assert_eq!(iso(1_791_428_400_000), "2026-10-08T03:00:00Z");
	}
}
