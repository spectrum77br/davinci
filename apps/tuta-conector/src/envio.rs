//! ENVIO (decisão 10) — pela FILA da Central de e-mail (o v1 do outro dev:
//! `outbox/lease` → enviar no Tuta → `outbox/{job}/receipt`). Módulo
//! SEPARADO da leitura (a leitura não pode nem importar este).
//!
//! Nada sai sem uma PESSOA ter clicado no DaVinci (a Central só enfileira o
//! que alguém mandou, uma resposta viva por e-mail, `request_id` que não
//! duplica). E aqui, por cima:
//!
//! - TODAS as chaves: a do DaVinci (o `send_enabled` que o sinal devolve —
//!   a pessoa liga na tela) E a chave LOCAL do conector (chaves.json →
//!   `envio`), ambas desligadas por padrão. Sem as duas, nem pede trabalho.
//! - Os tetos por conta: 30/h e 300/dia deste conector, e 100/h da CONTA
//!   inteira do Tuta (pela pasta Enviados, que a leitura conta). No teto,
//!   não pede trabalho; o que já veio volta `failed` "teto_local".
//! - O remetente tem de ser EXATAMENTE (minúsculo) um endereço ATIVO da conta
//!   no Tuta. Se não for, `failed` — nunca troca pelo endereço principal.
//! - Só RESPOSTA: `conversationType` REPLY com o `previousMessageId` = o
//!   Message-ID do original (o `in_reply_to` que a Central manda, que é o
//!   do ConversationEntry que a leitura entregou). Sem ele, `failed`
//!   "sem_fio" — nunca sai como e-mail novo.
//! - Destinatário do próprio Tuta: `failed` (exige a cifra de ponta a ponta,
//!   que o conector não monta; responder pelo app do Tuta).
//! - DIÁRIO local por tarefa (diario-envio.json, só ids e passos) gravado
//!   ANTES de cada passo: recebido → preparando → rascunho criado → "vai
//!   enviar" (um sinal novo: a chave da pessoa ainda está ligada?) →
//!   enviando → concluído. Falha ANTES do rascunho = `failed` (nada saiu);
//!   DEPOIS dele = `uncertain` (uma pessoa confere no Tuta e marca
//!   "Saiu"/"Não saiu" no DaVinci). NUNCA reenvia sozinho.
//! - O token do lease fica SÓ na memória: o processo caiu antes do recibo →
//!   a Central marca o envio como incerto em 15 min (é o comportamento dela).
//! - O job pego há mais de 12 min (`LEASE_LIMITE_ENVIO`) não vai para o
//!   SendDraft: `failed` "lease_vencido" — depois de 15 min uma pessoa pode ter
//!   marcado "Não saiu" e respondido de novo (crítica de 08/10).
//! - O DIÁRIO barra o job repetido: um id que já passou do "recebido" (ou já
//!   fechou, ou ficou no meio quando o processo caiu) nunca é enviado de novo
//!   — recibo `uncertain` "ja_visto_no_diario" (a Central hoje nunca devolve
//!   um job à fila; o diário é a segunda defesa).
//!
//! O texto vai como texto (escapado, quebras de linha; `plaintext`): o Tuta
//! monta o e-mail. Não marca o original como "respondido" (a chave local
//! `marcar_respondido` continua sem efeito).

use crate::chaveiro::{self, Cofre};
use crate::config::{self, Conta};
use crate::davinci::{codigo_de_erro, ClienteDavinci, ErroDavinci, Recibo, Sinal, Tarefa};
use crate::estado::Compartilhado;
use crate::estado_local::{gravar_atomico, ChavesLocais};
use crate::registro::mascarar;
use crate::tuta::sdk::{self, estado_do_erro, sem_panico, ErroRetomar};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use crypto_primitives::aes::{Aes256Key, InitializationVector};
use crypto_primitives::key::GenericAesKey;
use crypto_primitives::randomizer_facade::RandomizerFacade;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tutasdk::bindings::rest_client::RestClient;
use tutasdk::entities::generated::sys::{Group, GroupInfo};
use tutasdk::entities::generated::tutanota::{DraftCreateData, DraftData, DraftRecipient, SendDraftData, SendDraftParameters};
use tutasdk::services::generated::tutanota::{DraftService, SendDraftService};
use tutasdk::services::ExtraServiceParams;
use tutasdk::{ApiCallError, CustomId, GeneratedId, IdTupleGenerated, LoggedInSdk};

/// Tuta: `ConversationType.REPLY`.
const RESPOSTA: i64 = 1;
const HORA_MS: u64 = 60 * 60 * 1000;
const DIA_MS: u64 = 24 * HORA_MS;

// ── O diário ────────────────────────────────────────────────────────────

/// O passo de uma tarefa (gravado ANTES de cada um).
#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Passo {
	Recebido,
	Preparando,
	RascunhoCriado,
	Enviando,
	Concluido,
}

/// Uma tarefa no diário: só ids, passos e o resultado (nada do cliente).
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct EntradaDiario {
	pub passo: Passo,
	/// O rascunho no Tuta [lista, elemento].
	#[serde(default)]
	pub rascunho: Option<Vec<String>>,
	/// sent | failed | uncertain
	#[serde(default)]
	pub status: Option<String>,
	#[serde(default)]
	pub codigo: Option<String>,
	#[serde(default)]
	pub recibo_entregue: bool,
	/// O processo caiu no meio (achado na volta seguinte): o recibo não sai
	/// daqui (o token era só da memória); a Central marca incerto.
	#[serde(default)]
	pub interrompido: bool,
	pub criado_em_ms: u64,
	pub atualizado_em_ms: u64,
	#[serde(default)]
	pub enviado_em_ms: Option<u64>,
}

#[derive(Serialize, Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct Diario {
	/// Por id da tarefa (o job da Central).
	pub tarefas: BTreeMap<String, EntradaDiario>,
}

impl Diario {
	#[must_use]
	pub fn carregar(pasta: &Path) -> Self {
		match std::fs::read(pasta.join(config::ARQUIVO_DIARIO)) {
			Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_else(|_| {
				log::warn!("diário de envio ilegível: começando outro (nada é reenviado)");
				Self::default()
			}),
			Err(_) => Self::default(),
		}
	}

	pub fn salvar(&self, pasta: &Path) -> std::io::Result<()> {
		let bytes = serde_json::to_vec_pretty(self).map_err(std::io::Error::other)?;
		gravar_atomico(&pasta.join(config::ARQUIVO_DIARIO), &bytes)
	}

	/// Quantas saíram (status sent) desde `desde_ms`.
	#[must_use]
	pub fn enviados_desde(&self, desde_ms: u64) -> u32 {
		let n = self
			.tarefas
			.values()
			.filter(|e| e.status.as_deref() == Some("sent") && e.enviado_em_ms.is_some_and(|t| t >= desde_ms))
			.count();
		u32::try_from(n).unwrap_or(u32::MAX)
	}
}

// ── As travas puras ─────────────────────────────────────────────────────

#[derive(Debug, PartialEq, Eq)]
pub enum Bloqueio {
	/// chaves.json → "envio": false (o padrão).
	ChaveLocalDesligada,
	/// A pessoa não ligou o envio da caixa na Central (`send_enabled`).
	DavinciNaoLiberou,
	/// Teto deste conector (hora/dia) ou da conta do Tuta (hora).
	Teto(&'static str),
	/// O remetente pedido não é um endereço ativo da conta.
	AliasNaoConfere,
}

impl std::fmt::Display for Bloqueio {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		match self {
			Self::ChaveLocalDesligada => f.write_str("envio desligado neste Mac (chaves.json: \"envio\": false)"),
			Self::DavinciNaoLiberou => f.write_str("o envio desta caixa está desligado na Central do DaVinci"),
			Self::Teto(t) => write!(f, "teto de envio atingido ({t})"),
			Self::AliasNaoConfere => f.write_str("o remetente não é um endereço ativo da conta: envio abortado"),
		}
	}
}

impl std::error::Error for Bloqueio {}

/// As duas chaves ligadas? (a local E a da pessoa na Central).
pub fn pode_tentar(chaves: &ChavesLocais, envio_ligado: bool) -> Result<(), Bloqueio> {
	if !chaves.envio {
		return Err(Bloqueio::ChaveLocalDesligada);
	}
	if !envio_ligado {
		return Err(Bloqueio::DavinciNaoLiberou);
	}
	Ok(())
}

/// Os tetos (deste conector na hora/no dia; da conta do Tuta na hora).
pub fn dentro_do_teto(conector_hora: u32, conector_dia: u32, conta_hora: u32) -> Result<(), Bloqueio> {
	if conector_hora >= config::ENVIO_TETO_HORA {
		return Err(Bloqueio::Teto("conector_hora"));
	}
	if conector_dia >= config::ENVIO_TETO_DIA {
		return Err(Bloqueio::Teto("conector_dia"));
	}
	if conta_hora >= config::TUTA_TETO_CONTA_HORA {
		return Err(Bloqueio::Teto("conta_hora"));
	}
	Ok(())
}

/// O remetente, conferido: igual (minúsculo, sem espaços nas pontas) a um
/// dos endereços ATIVOS da conta. Nunca troca por outro.
pub fn alias_confere(de_alias: &str, aliases_ativos: &[String]) -> Result<String, Bloqueio> {
	let pedido = de_alias.trim().to_lowercase();
	if pedido.is_empty() || !pedido.contains('@') {
		return Err(Bloqueio::AliasNaoConfere);
	}
	aliases_ativos
		.iter()
		.map(|a| a.trim().to_lowercase())
		.find(|a| *a == pedido)
		.ok_or(Bloqueio::AliasNaoConfere)
}

/// O destinatário é do próprio Tuta? (precisa da cifra de ponta a ponta).
#[must_use]
pub fn destinatario_do_tuta(endereco: &str) -> bool {
	let dominio = endereco.rsplit_once('@').map(|(_, d)| d.trim().to_lowercase()).unwrap_or_default();
	config::DOMINIOS_TUTA.iter().any(|d| dominio == *d)
}

/// Um endereço de destino simples (sem espaço, com um @ e domínio com ponto).
#[must_use]
pub fn destino_valido(endereco: &str) -> Option<String> {
	let e = endereco.trim();
	let (local, dominio) = e.rsplit_once('@')?;
	if local.is_empty() || !dominio.contains('.') || e.chars().any(|c| c.is_whitespace() || c.is_control()) || e.len() > 254 {
		return None;
	}
	Some(e.to_owned())
}

/// O texto da pessoa como HTML do Tuta (escapado, `\n` → `<br>`), o mesmo
/// que o Tuta faz com um e-mail de texto (o `plaintext` manda texto puro).
#[must_use]
pub fn texto_para_html(texto: &str) -> String {
	texto
		.replace('&', "&amp;")
		.replace('<', "&lt;")
		.replace('>', "&gt;")
		.replace('"', "&quot;")
		.replace("\r\n", "<br>")
		.replace(['\n', '\r'], "<br>")
}

/// O diário já viu este job além do "recebido" (ou fechado, ou interrompido)?
/// Então ele nunca é enviado de novo.
#[must_use]
pub fn ja_visto(entrada: Option<&EntradaDiario>) -> bool {
	entrada.is_some_and(|e| e.passo != Passo::Recebido || e.interrompido || e.status.is_some() || e.rascunho.is_some())
}

/// O lease ainda dá tempo para o SendDraft? (`agora` e `pego_em` em ms)
#[must_use]
pub fn lease_no_prazo(pego_em_ms: u64, agora_ms: u64) -> bool {
	let limite = u64::try_from(config::LEASE_LIMITE_ENVIO.as_millis()).unwrap_or(u64::MAX);
	agora_ms.saturating_sub(pego_em_ms) <= limite
}

/// Por que uma tarefa não pode sair (o `error_code` do recibo `failed`).
pub fn conferir_tarefa(t: &Tarefa, aliases_ativos: &[String]) -> Result<(String, String, String), &'static str> {
	let remetente = alias_confere(&t.from_address, aliases_ativos).map_err(|_| "sender_not_active_alias")?;
	let fio = t.in_reply_to.as_deref().map(str::trim).unwrap_or("");
	if fio.is_empty() {
		return Err("sem_fio");
	}
	let destino = destino_valido(&t.to).ok_or("destinatario_invalido")?;
	if destinatario_do_tuta(&destino) {
		return Err("destinatario_tuta");
	}
	if t.text.trim().is_empty() {
		return Err("texto_vazio");
	}
	Ok((remetente, destino, fio.to_owned()))
}

// ── O enviador ──────────────────────────────────────────────────────────

/// O que o enviador usa de fora (os testes passam um Tuta e uma Central falsos).
pub struct DependenciasEnvio {
	pub conta: Conta,
	pub cofre: Arc<dyn Cofre>,
	pub tuta_url: String,
	pub rest_tuta: Arc<dyn RestClient>,
	pub davinci: Arc<ClienteDavinci>,
	pub pasta_local: PathBuf,
	pub relogio: Arc<dyn Fn() -> u64 + Send + Sync>,
	pub compartilhado: Arc<Compartilhado>,
}

/// O resumo de uma volta de envio (só números).
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct ResumoEnvio {
	pub tarefas: u32,
	pub enviadas: u32,
	pub falharam: u32,
	pub incertas: u32,
	pub recibos: u32,
	/// Por que não pediu trabalho (chave, teto), se for o caso.
	pub parado: Option<String>,
}

impl std::fmt::Display for ResumoEnvio {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		write!(
			f,
			"{} tarefa(s): {} enviada(s), {} falha(s), {} incerta(s); {} recibo(s)",
			self.tarefas, self.enviadas, self.falharam, self.incertas, self.recibos
		)?;
		if let Some(p) = &self.parado {
			write!(f, " ({p})")?;
		}
		Ok(())
	}
}

/// Uma falha do Tuta que vale para a conta inteira (para as tarefas que restam).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParadaEnvio {
	SemSessao,
	Cofre(String),
	Tuta(String),
	Davinci(ErroDavinci),
	Disco(String),
}

impl std::fmt::Display for ParadaEnvio {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		match self {
			Self::SemSessao => f.write_str("não há sessão do Tuta no Chaveiro para esta conta"),
			Self::Cofre(e) => write!(f, "Chaveiro: {e}"),
			Self::Tuta(e) => write!(f, "Tuta: {e}"),
			Self::Davinci(e) => write!(f, "{e}"),
			Self::Disco(e) => write!(f, "não consegui gravar o diário de envio: {e}"),
		}
	}
}

impl std::error::Error for ParadaEnvio {}

pub struct Enviador {
	deps: DependenciasEnvio,
	sdk: Option<Arc<LoggedInSdk>>,
	sdk_aberto_em: u64,
	pub diario: Diario,
	/// Recibos que não chegaram à Central (rede): o token SÓ em memória.
	recibos_pendentes: HashMap<String, Recibo>,
	diario_conferido: bool,
}

/// O endereço ativo da conta e o grupo de e-mail dele (o mesmo caminho do
/// `get_group_id_for_mail_address` oficial, sem diferenciar maiúsculas).
struct Remetente {
	endereco: String,
	grupo: GeneratedId,
}

fn erro_api(e: &ApiCallError) -> String {
	codigo_de_erro(&format!("{:?}", estado_do_erro(e)))
}

impl Enviador {
	#[must_use]
	pub fn novo(deps: DependenciasEnvio) -> Self {
		let diario = Diario::carregar(&deps.pasta_local);
		Self {
			deps,
			sdk: None,
			sdk_aberto_em: 0,
			diario,
			recibos_pendentes: HashMap::new(),
			diario_conferido: false,
		}
	}

	fn agora(&self) -> u64 {
		(self.deps.relogio)()
	}

	fn salvar(&self) -> Result<(), ParadaEnvio> {
		self.diario.salvar(&self.deps.pasta_local).map_err(|e| ParadaEnvio::Disco(e.to_string()))
	}

	fn marcar(&mut self, tarefa: &str, passo: Passo) -> Result<(), ParadaEnvio> {
		let agora = self.agora();
		let e = self.diario.tarefas.entry(tarefa.to_owned()).or_insert_with(|| EntradaDiario {
			passo,
			rascunho: None,
			status: None,
			codigo: None,
			recibo_entregue: false,
			interrompido: false,
			criado_em_ms: agora,
			atualizado_em_ms: agora,
			enviado_em_ms: None,
		});
		e.passo = passo;
		e.atualizado_em_ms = agora;
		self.salvar()
	}

	/// A sessão do Tuta desta conta (a mesma do Chaveiro que a leitura usa).
	async fn sdk(&mut self) -> Result<Arc<LoggedInSdk>, ParadaEnvio> {
		let reabrir = u64::try_from(config::CAIXA_REABRIR_A_CADA.as_millis()).unwrap_or(u64::MAX);
		if let Some(s) = &self.sdk {
			if self.agora().saturating_sub(self.sdk_aberto_em) < reabrir {
				return Ok(s.clone());
			}
		}
		self.sdk = None;
		let sessao = match chaveiro::ler_sessao(&*self.deps.cofre, self.deps.conta) {
			Ok(Some(s)) => s,
			Ok(None) => return Err(ParadaEnvio::SemSessao),
			Err(e) => return Err(ParadaEnvio::Cofre(e.to_string())),
		};
		let s = sdk::novo_sdk(&self.deps.tuta_url, self.deps.rest_tuta.clone());
		match sdk::retomar(&s, sessao.credenciais()).await {
			Ok(logado) => {
				self.sdk = Some(logado.clone());
				self.sdk_aberto_em = self.agora();
				Ok(logado)
			},
			Err(ErroRetomar::SessaoCaiu) => Err(ParadaEnvio::SemSessao),
			Err(e) => Err(ParadaEnvio::Tuta(codigo_de_erro(&format!("{e:?}")))),
		}
	}

	/// Os endereços ATIVOS da conta (principal + aliases ligados) e o grupo de cada um.
	async fn remetentes(&mut self) -> Result<Vec<Remetente>, ParadaEnvio> {
		let sdk = self.sdk().await?;
		let r = sem_panico(async {
			let usuario = sdk.get_user();
			let cripto = sdk.mail_facade().get_crypto_entity_client();
			let mut saida = Vec::new();
			for m in usuario.memberships.iter().filter(|m| m.groupType == Some(5)) {
				let grupo: Group = cripto.load(&m.group).await?;
				let info: GroupInfo = match (&grupo.user, &usuario._id) {
					(None, _) => cripto.load(&m.groupInfo).await?,
					(Some(dono), Some(eu)) if dono == eu => cripto.load(&usuario.userGroup.groupInfo).await?,
					_ => continue,
				};
				for e in info.mailAddress.iter().cloned().chain(info.mailAddressAliases.iter().filter(|a| a.enabled).map(|a| a.mailAddress.clone())) {
					saida.push(Remetente {
						endereco: e,
						grupo: m.group.clone(),
					});
				}
			}
			Ok::<_, ApiCallError>(saida)
		})
		.await;
		match r {
			Ok(Ok(l)) => Ok(l),
			Ok(Err(e)) => {
				self.sdk = None;
				Err(ParadaEnvio::Tuta(erro_api(&e)))
			},
			Err(_) => {
				self.sdk = None;
				Err(ParadaEnvio::Tuta("panico".into()))
			},
		}
	}

	/// Uma volta: os recibos que ficaram, o diário de antes e — com as duas
	/// chaves ligadas e dentro do teto — as tarefas da fila.
	pub async fn uma_volta(&mut self) -> Result<ResumoEnvio, ParadaEnvio> {
		let mut r = ResumoEnvio::default();
		self.conferir_diario()?;
		self.entregar_recibos(&mut r).await;
		let chaves = ChavesLocais::carregar(&self.deps.pasta_local);
		let comp = self.deps.compartilhado.clone();
		let agora = self.agora();
		let na_hora = self.diario.enviados_desde(agora.saturating_sub(HORA_MS));
		comp.enviados_conector_hora.store(na_hora, Ordering::Relaxed);
		// O sinal diz "posso enviar" só com a chave local e a sessão abrindo.
		let pronto = chaves.envio && self.sdk().await.is_ok();
		let antes = comp.pode_enviar.swap(pronto, Ordering::Relaxed);
		if pronto && !antes {
			// Acabou de ficar pronto: a Central só entrega trabalho a quem disse
			// `can_send` no sinal — avisa já, sem esperar o próximo minuto.
			if let Ok(s) = self.deps.davinci.sinal(&Sinal::de(comp.estado(), true)).await {
				comp.envio_ligado.store(s.send_enabled, Ordering::Relaxed);
			}
		}
		if let Err(b) = pode_tentar(&chaves, comp.envio_ligado.load(Ordering::Relaxed)) {
			r.parado = Some(b.to_string());
			return Ok(r);
		}
		if !pronto {
			r.parado = Some("sem sessão do Tuta".into());
			return Ok(r);
		}
		let no_dia = self.diario.enviados_desde(agora.saturating_sub(DIA_MS));
		if let Err(b) = dentro_do_teto(na_hora, no_dia, comp.enviados_conta_hora.load(Ordering::Relaxed)) {
			r.parado = Some(b.to_string());
			return Ok(r);
		}
		let tarefas = match self.deps.davinci.pegar_tarefas().await {
			Ok(t) => t,
			Err(e) => return Err(ParadaEnvio::Davinci(e)),
		};
		// A hora em que a Central entregou a leva (o lease dela conta daqui).
		let pego_em = self.agora();
		let mut parada: Option<ParadaEnvio> = None;
		for t in tarefas {
			r.tarefas += 1;
			if let Some(p) = &parada {
				// A conta caiu no meio: o que já veio volta `failed` (nada saiu).
				let codigo = match p {
					ParadaEnvio::SemSessao => "sessao_caiu",
					_ => "tuta_indisponivel",
				};
				self.concluir(&t, "failed", None, Some(codigo), &mut r).await;
				continue;
			}
			// O teto pode chegar no meio da leva.
			let agora = self.agora();
			let na_hora = self.diario.enviados_desde(agora.saturating_sub(HORA_MS));
			let no_dia = self.diario.enviados_desde(agora.saturating_sub(DIA_MS));
			let conta = comp.enviados_conta_hora.load(Ordering::Relaxed);
			if dentro_do_teto(na_hora, no_dia, conta).is_err() {
				self.concluir(&t, "failed", None, Some("teto_local"), &mut r).await;
				continue;
			}
			if let Err(p) = self.tratar(&t, pego_em, &mut r).await {
				parada = Some(p);
			}
		}
		self.limpar();
		self.salvar()?;
		match parada {
			Some(p) => Err(p),
			None => Ok(r),
		}
	}

	/// O diário de ANTES de o processo subir: o que ficou no meio é marcado
	/// (o recibo não sai daqui; a Central marca incerto e uma pessoa confere).
	fn conferir_diario(&mut self) -> Result<(), ParadaEnvio> {
		if self.diario_conferido {
			return Ok(());
		}
		self.diario_conferido = true;
		let mut mudou = false;
		for (id, e) in &mut self.diario.tarefas {
			if e.passo != Passo::Concluido && !e.interrompido {
				log::warn!(
					"envio {id} ficou no passo {:?} quando o conector parou: o recibo não sai daqui (a Central marca incerto; conferir no Tuta)",
					e.passo
				);
				e.interrompido = true;
				mudou = true;
			}
		}
		if mudou {
			self.salvar()?;
		}
		Ok(())
	}

	async fn entregar_recibos(&mut self, r: &mut ResumoEnvio) {
		let pendentes: Vec<(String, Recibo)> = self.recibos_pendentes.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
		for (tarefa, recibo) in pendentes {
			if self.mandar_recibo(&tarefa, &recibo).await {
				r.recibos += 1;
			}
		}
	}

	/// Manda o recibo; `true` se a Central ficou sabendo (ou já sabia).
	async fn mandar_recibo(&mut self, tarefa: &str, recibo: &Recibo) -> bool {
		let entregue = match self.deps.davinci.recibo(tarefa, recibo).await {
			Ok(()) => true,
			// Uma pessoa já resolveu (receipt_conflict) ou o lease não vale mais:
			// nada a fazer daqui (fica no log).
			Err(ErroDavinci::Conflito(c)) => {
				log::warn!("recibo do envio {tarefa} recusado pela Central ({c})");
				true
			},
			Err(ErroDavinci::NaoEncontrado(_)) => true,
			Err(e) => {
				log::warn!("recibo do envio {tarefa} não chegou ({}); tento de novo", mascarar(&e.to_string()));
				false
			},
		};
		if entregue {
			self.recibos_pendentes.remove(tarefa);
			if let Some(e) = self.diario.tarefas.get_mut(tarefa) {
				e.recibo_entregue = true;
			}
		} else {
			self.recibos_pendentes.insert(tarefa.to_owned(), recibo.clone());
		}
		entregue
	}

	async fn concluir(&mut self, t: &Tarefa, status: &'static str, message_id: Option<String>, codigo: Option<&str>, r: &mut ResumoEnvio) {
		let agora = self.agora();
		let e = self.diario.tarefas.entry(t.id.clone()).or_insert_with(|| EntradaDiario {
			passo: Passo::Concluido,
			rascunho: None,
			status: None,
			codigo: None,
			recibo_entregue: false,
			interrompido: false,
			criado_em_ms: agora,
			atualizado_em_ms: agora,
			enviado_em_ms: None,
		});
		e.passo = Passo::Concluido;
		e.status = Some(status.to_owned());
		e.codigo = codigo.map(str::to_owned);
		e.atualizado_em_ms = agora;
		if status == "sent" {
			e.enviado_em_ms = Some(agora);
		}
		match status {
			"sent" => r.enviadas += 1,
			"uncertain" => r.incertas += 1,
			_ => r.falharam += 1,
		}
		if let Err(e) = self.salvar() {
			log::warn!("diário de envio: {e}");
		}
		let recibo = Recibo {
			lease_token: t.lease_token.clone(),
			status,
			message_id: message_id.filter(|m| !m.is_empty() && m.chars().count() <= 998 && !m.chars().any(char::is_control)),
			error_code: codigo.map(codigo_de_erro),
		};
		if self.mandar_recibo(&t.id, &recibo).await {
			r.recibos += 1;
		}
	}

	/// Uma tarefa, do começo ao recibo. `Err` = a conta caiu (as próximas não saem).
	async fn tratar(&mut self, t: &Tarefa, pego_em: u64, r: &mut ResumoEnvio) -> Result<(), ParadaEnvio> {
		if ja_visto(self.diario.tarefas.get(&t.id)) {
			// O mesmo job de novo: nunca reenvia (uma pessoa confere no Tuta). O
			// diário fica como estava (o que ele sabe do envio de antes vale).
			log::warn!("envio {}: o diário já viu este job; não envio de novo (incerto)", t.id);
			r.incertas += 1;
			let recibo = Recibo {
				lease_token: t.lease_token.clone(),
				status: "uncertain",
				message_id: None,
				error_code: Some(codigo_de_erro("ja_visto_no_diario")),
			};
			if self.mandar_recibo(&t.id, &recibo).await {
				r.recibos += 1;
			}
			return Ok(());
		}
		self.marcar(&t.id, Passo::Recebido)?;
		let remetentes = match self.remetentes().await {
			Ok(l) => l,
			Err(p) => {
				self.concluir(t, "failed", None, Some("tuta_indisponivel"), r).await;
				return Err(p);
			},
		};
		let ativos: Vec<String> = remetentes.iter().map(|x| x.endereco.trim().to_lowercase()).collect();
		let (remetente, destino, fio) = match conferir_tarefa(t, &ativos) {
			Ok(x) => x,
			Err(codigo) => {
				log::warn!("envio {} recusado aqui ({codigo}): nada saiu", t.id);
				self.concluir(t, "failed", None, Some(codigo), r).await;
				return Ok(());
			},
		};
		let Some(grupo) = remetentes.iter().find(|x| x.endereco.trim().to_lowercase() == remetente) else {
			self.concluir(t, "failed", None, Some("sender_not_active_alias"), r).await;
			return Ok(());
		};
		let endereco = grupo.endereco.clone();
		let grupo = grupo.grupo.clone();

		// 1. O rascunho (REPLY ao original). Falha aqui = nada saiu.
		self.marcar(&t.id, Passo::Preparando)?;
		let sdk = match self.sdk().await {
			Ok(s) => s,
			Err(p) => {
				self.concluir(t, "failed", None, Some("tuta_indisponivel"), r).await;
				return Err(p);
			},
		};
		let aleatorio = RandomizerFacade::from_core(rand_core::OsRng);
		let chave: GenericAesKey = Aes256Key::generate(&aleatorio).into();
		let rascunho = match criar_rascunho(&sdk, &aleatorio, &chave, &grupo, &endereco, &destino, &fio, t).await {
			Ok(id) => id,
			Err(e) => {
				log::warn!("envio {}: o rascunho não foi criado ({}): nada saiu", t.id, erro_api(&e));
				let geral = matches!(estado_do_erro(&e), crate::estado::EstadoConector::SessaoCaiu);
				self.concluir(t, "failed", None, Some("rascunho_falhou"), r).await;
				if geral {
					self.sdk = None;
					return Err(ParadaEnvio::SemSessao);
				}
				return Ok(());
			},
		};
		if let Some(e) = self.diario.tarefas.get_mut(&t.id) {
			e.rascunho = Some(vec![rascunho.list_id.as_str().to_owned(), rascunho.element_id.as_str().to_owned()]);
		}
		self.marcar(&t.id, Passo::RascunhoCriado)?;

		// 2. "Vai enviar": a chave da pessoa ainda está ligada?
		let comp = self.deps.compartilhado.clone();
		let sinal = Sinal::de(comp.estado(), true);
		match self.deps.davinci.sinal(&sinal).await {
			Ok(s) if s.send_enabled => comp.envio_ligado.store(true, Ordering::Relaxed),
			Ok(_) => {
				comp.envio_ligado.store(false, Ordering::Relaxed);
				log::warn!("envio {}: a chave da caixa foi desligada antes de enviar; o rascunho ficou no Tuta", t.id);
				self.concluir(t, "failed", None, Some("envio_desligado"), r).await;
				return Ok(());
			},
			Err(e) => {
				log::warn!("envio {}: sem confirmação da Central antes de enviar ({}); o rascunho ficou no Tuta", t.id, mascarar(&e.to_string()));
				self.concluir(t, "failed", None, Some("sem_confirmacao"), r).await;
				return Ok(());
			},
		}

		// 3. O lease ainda vale? Depois de 15 min a Central deixa uma pessoa
		// marcar "Não saiu" e responder de novo: aqui não se arrisca.
		if !lease_no_prazo(pego_em, self.agora()) {
			log::warn!("envio {}: o lease passou do prazo antes de enviar; o rascunho ficou no Tuta", t.id);
			self.concluir(t, "failed", None, Some("lease_vencido"), r).await;
			return Ok(());
		}

		// 4. Enviar. Falha DEPOIS do rascunho = incerto (nunca reenvia).
		self.marcar(&t.id, Passo::Enviando)?;
		match enviar_rascunho(&sdk, &aleatorio, &chave, &rascunho).await {
			Ok(message_id) => {
				log::info!("envio {} saiu pelo Tuta", t.id);
				self.concluir(t, "sent", Some(message_id), None, r).await;
				Ok(())
			},
			Err(e) => {
				log::warn!("envio {}: o Tuta não confirmou ({}): INCERTO, conferir no Tuta", t.id, erro_api(&e));
				self.concluir(t, "uncertain", None, Some("envio_sem_confirmacao"), r).await;
				Ok(())
			},
		}
	}

	/// Tira do diário o que fechou há mais de uma semana.
	fn limpar(&mut self) {
		let limite = self.agora().saturating_sub(u64::try_from(config::DIARIO_GUARDA.as_millis()).unwrap_or(u64::MAX));
		self.diario
			.tarefas
			.retain(|_, e| e.atualizado_em_ms >= limite || (e.passo != Passo::Concluido && !e.interrompido));
	}
}

#[allow(clippy::too_many_arguments)]
async fn criar_rascunho(
	sdk: &Arc<LoggedInSdk>,
	aleatorio: &RandomizerFacade,
	chave: &GenericAesKey,
	grupo: &GeneratedId,
	remetente: &str,
	destino: &str,
	fio: &str,
	t: &Tarefa,
) -> Result<IdTupleGenerated, ApiCallError> {
	let r = sem_panico(async {
		let chave_do_grupo = sdk.get_current_sym_group_key(grupo).await?;
		let dono = chave_do_grupo.object.encrypt_key(chave, InitializationVector::generate(aleatorio));
		let corpo = texto_para_html(&t.text);
		let dados = DraftCreateData {
			_format: 0,
			previousMessageId: Some(fio.to_owned()),
			conversationType: RESPOSTA,
			ownerEncSessionKey: dono,
			ownerKeyVersion: i64::try_from(chave_do_grupo.version).unwrap_or(0),
			draftData: DraftData {
				_id: None,
				subject: crate::texto::uma_linha(&t.subject, 998),
				bodyText: corpo.clone(),
				senderMailAddress: remetente.to_owned(),
				// Nome vazio faz o SendDraftService falhar: vai o próprio endereço.
				senderName: remetente.to_owned(),
				confidential: false,
				method: 0,
				compressedBodyText: Some(corpo),
				toRecipients: vec![DraftRecipient {
					_id: None,
					name: destino.to_owned(),
					mailAddress: destino.to_owned(),
					_errors: Default::default(),
				}],
				ccRecipients: vec![],
				bccRecipients: vec![],
				addedAttachments: vec![],
				removedAttachments: vec![],
				replyTos: vec![],
				_errors: Default::default(),
			},
			_errors: Default::default(),
		};
		let saida = sdk
			.get_service_executor()
			.post::<DraftService>(
				dados,
				ExtraServiceParams {
					session_key: Some(chave.clone()),
					..Default::default()
				},
			)
			.await?;
		Ok::<_, ApiCallError>(saida.draft)
	})
	.await;
	match r {
		Ok(x) => x,
		Err(_) => Err(ApiCallError::internal("panico no rascunho".to_owned())),
	}
}

async fn enviar_rascunho(
	sdk: &Arc<LoggedInSdk>,
	aleatorio: &RandomizerFacade,
	chave: &GenericAesKey,
	rascunho: &IdTupleGenerated,
) -> Result<String, ApiCallError> {
	let r = sem_panico(async {
		let id_parametros = CustomId(URL_SAFE_NO_PAD.encode(aleatorio.generate_random_array::<4>()));
		let bytes = chave.as_bytes().to_vec();
		let dados = SendDraftData {
			_format: 0,
			language: "pt".to_owned(),
			mailSessionKey: Some(bytes.clone()),
			bucketEncMailSessionKey: None,
			senderNameUnencrypted: None,
			plaintext: true,
			calendarMethod: false,
			sessionEncEncryptionAuthStatus: None,
			sendAt: None,
			allowUndo: false,
			internalRecipientKeyData: vec![],
			secureExternalRecipientKeyData: vec![],
			attachmentKeyData: vec![],
			mail: rascunho.clone(),
			symEncInternalRecipientKeyData: vec![],
			parameters: Some(SendDraftParameters {
				_id: Some(id_parametros),
				language: "pt".to_owned(),
				mailSessionKey: Some(bytes),
				bucketEncMailSessionKey: None,
				senderNameUnencrypted: None,
				plaintext: true,
				calendarMethod: false,
				sessionEncEncryptionAuthStatus: None,
				mail: rascunho.clone(),
				internalRecipientKeyData: vec![],
				secureExternalRecipientKeyData: vec![],
				symEncInternalRecipientKeyData: vec![],
				attachmentKeyData: vec![],
			}),
		};
		let saida = sdk
			.get_service_executor()
			.post::<SendDraftService>(dados, ExtraServiceParams::default())
			.await?;
		Ok::<_, ApiCallError>(saida.messageId)
	})
	.await;
	match r {
		Ok(x) => x,
		Err(_) => Err(ApiCallError::internal("panico no envio".to_owned())),
	}
}

/// Para o `estado`: as tarefas do diário que pedem atenção (interrompidas,
/// incertas, recibo que não chegou).
#[must_use]
pub fn pendencias_do_diario(diario: &Diario) -> (usize, usize, usize) {
	let interrompidas = diario.tarefas.values().filter(|e| e.interrompido).count();
	let incertas = diario.tarefas.values().filter(|e| e.status.as_deref() == Some("uncertain")).count();
	let sem_recibo = diario
		.tarefas
		.values()
		.filter(|e| e.passo == Passo::Concluido && !e.recibo_entregue && !e.interrompido)
		.count();
	(interrompidas, incertas, sem_recibo)
}

#[cfg(test)]
mod testes {
	use super::*;

	fn tarefa(from: &str, to: &str, fio: Option<&str>) -> Tarefa {
		serde_json::from_value(serde_json::json!({
			"id": "0b4e6a52-3f1d-4c55-9e0a-2a9c1d7e8f10", "lease_token": "t".repeat(40),
			"from_address": from, "to": to, "subject": "Re: Pedido", "text": "Oi\nTudo certo",
			"in_reply_to": fio, "references": [], "message_id": "<x@mail.davinci.local>"
		}))
		.unwrap()
	}

	#[test]
	fn tudo_desligado_por_padrao() {
		assert_eq!(pode_tentar(&ChavesLocais::default(), true), Err(Bloqueio::ChaveLocalDesligada));
		let chaves = ChavesLocais {
			envio: true,
			..ChavesLocais::default()
		};
		assert_eq!(pode_tentar(&chaves, false), Err(Bloqueio::DavinciNaoLiberou));
		assert_eq!(pode_tentar(&chaves, true), Ok(()));
	}

	#[test]
	fn tetos() {
		assert_eq!(dentro_do_teto(0, 0, 0), Ok(()));
		assert_eq!(dentro_do_teto(30, 0, 0), Err(Bloqueio::Teto("conector_hora")));
		assert_eq!(dentro_do_teto(0, 300, 0), Err(Bloqueio::Teto("conector_dia")));
		assert_eq!(dentro_do_teto(0, 0, 100), Err(Bloqueio::Teto("conta_hora")));
	}

	#[test]
	fn alias_exato_ou_aborta() {
		let ativos = vec!["061083.jf@tuta.com".to_owned(), "21max@tuta.com".to_owned()];
		assert_eq!(alias_confere(" 21MAX@tuta.com ", &ativos), Ok("21max@tuta.com".into()));
		assert_eq!(alias_confere("22max@tuta.com", &ativos), Err(Bloqueio::AliasNaoConfere));
		assert_eq!(alias_confere("", &ativos), Err(Bloqueio::AliasNaoConfere));
		assert_eq!(alias_confere("21max", &ativos), Err(Bloqueio::AliasNaoConfere));
	}

	#[test]
	fn tarefa_conferida() {
		let ativos = vec!["21max@tuta.com".to_owned(), "principal@tuta.com".to_owned()];
		assert_eq!(
			conferir_tarefa(&tarefa("21max@tuta.com", "cliente@example.com", Some("<pai@x>")), &ativos),
			Ok(("21max@tuta.com".into(), "cliente@example.com".into(), "<pai@x>".into()))
		);
		// Nunca cai no principal: alias que a conta não tem = failed.
		assert_eq!(conferir_tarefa(&tarefa("22max@tuta.com", "c@example.com", Some("<p@x>")), &ativos), Err("sender_not_active_alias"));
		// Sem fio, nunca sai como e-mail novo.
		assert_eq!(conferir_tarefa(&tarefa("21max@tuta.com", "c@example.com", None), &ativos), Err("sem_fio"));
		assert_eq!(conferir_tarefa(&tarefa("21max@tuta.com", "c@example.com", Some("  ")), &ativos), Err("sem_fio"));
		assert_eq!(conferir_tarefa(&tarefa("21max@tuta.com", "alguem@tutanota.de", Some("<p@x>")), &ativos), Err("destinatario_tuta"));
		assert_eq!(conferir_tarefa(&tarefa("21max@tuta.com", "sem arroba", Some("<p@x>")), &ativos), Err("destinatario_invalido"));
	}

	#[test]
	fn texto_vira_html_escapado() {
		assert_eq!(texto_para_html("a <b> & \"c\"\nlinha 2\r\nlinha 3"), "a &lt;b&gt; &amp; &quot;c&quot;<br>linha 2<br>linha 3");
	}

	#[test]
	fn diario_barra_o_job_repetido() {
		let base = EntradaDiario {
			passo: Passo::Recebido,
			rascunho: None,
			status: None,
			codigo: None,
			recibo_entregue: false,
			interrompido: false,
			criado_em_ms: 0,
			atualizado_em_ms: 0,
			enviado_em_ms: None,
		};
		assert!(!ja_visto(None));
		assert!(!ja_visto(Some(&base)), "só recebido (nada feito): pode seguir");
		assert!(ja_visto(Some(&EntradaDiario { passo: Passo::RascunhoCriado, ..base.clone() })));
		assert!(ja_visto(Some(&EntradaDiario { passo: Passo::Concluido, status: Some("sent".into()), ..base.clone() })));
		assert!(ja_visto(Some(&EntradaDiario { interrompido: true, ..base.clone() })));
		assert!(ja_visto(Some(&EntradaDiario { rascunho: Some(vec!["l".into(), "e".into()]), ..base })));
	}

	#[test]
	fn lease_tem_prazo_para_o_send_draft() {
		let doze = 12 * 60 * 1000;
		assert!(lease_no_prazo(1_000, 1_000));
		assert!(lease_no_prazo(1_000, 1_000 + doze));
		assert!(!lease_no_prazo(1_000, 1_000 + doze + 1));
	}

	#[test]
	fn diario_conta_os_enviados() {
		let mut d = Diario::default();
		let base = EntradaDiario {
			passo: Passo::Concluido,
			rascunho: None,
			status: Some("sent".into()),
			codigo: None,
			recibo_entregue: true,
			interrompido: false,
			criado_em_ms: 0,
			atualizado_em_ms: 0,
			enviado_em_ms: Some(10_000),
		};
		d.tarefas.insert("a".into(), base.clone());
		d.tarefas.insert("b".into(), EntradaDiario { enviado_em_ms: Some(1), ..base.clone() });
		d.tarefas.insert("c".into(), EntradaDiario { status: Some("failed".into()), ..base });
		assert_eq!(d.enviados_desde(5_000), 1);
		assert_eq!(d.enviados_desde(0), 2);
		let texto = serde_json::to_string(&d).unwrap();
		assert!(!texto.contains("token"), "o token do lease nunca vai para o disco: {texto}");
	}
}
