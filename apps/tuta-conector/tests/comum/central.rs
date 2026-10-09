//! Uma Central de e-mail FALSA que guarda o que recebe (só nos testes): o
//! contrato v1 (heartbeat, outbox/lease, outbox/{job}/receipt) e o v2 nosso
//! (sync, ingest, count, changes) com o comportamento que importa para o
//! conector — o dedupe pelo `source_id`, só pasta de corpo no ingest, o
//! "falta/mudou/saiu" da contagem, o movido/apagado, a fila de respostas e
//! falhas programadas. O formato EXATO é conferido também pela Central de
//! verdade (`apps/api/tests/test_mail_v2_conector_contrato.py` valida as
//! amostras deste conector com os schemas dela) e no teste de ponta a ponta.
#![allow(dead_code)]

use super::{Resposta, ServidorFalso, CAIXA, TOKEN_DAVINCI};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};

pub const PLATAFORMAS: &[&str] = &["ml", "shopee", "amazon", "tiktok", "temu", "magalu", "ali", "shein"];
const SO_CONTAR: &[&str] = &["financeiro", "contabilidade", "dnp", "devolucoes", "devoluções", "envio", "retido", "avisos"];

/// A regra de pastas da Central, resumida (a de verdade é `regras.py`):
/// Entrada e Enviados, e as de nome com palavra de plataforma no fim (ou de
/// marca) se leem com corpo; financeiro & cia., Lixeira, Spam… só contam.
pub fn leitura(nome: &str, tipo: i64) -> &'static str {
	if tipo == 1 || tipo == 2 {
		return "corpo";
	}
	if tipo != 0 {
		return "so_contar";
	}
	let n = nome.to_lowercase();
	let palavras: Vec<&str> = n.split_whitespace().collect();
	if palavras.first().is_some_and(|p| SO_CONTAR.contains(&p.trim_start_matches('*'))) {
		return "so_contar";
	}
	let ultima = palavras.last().copied().unwrap_or("");
	if PLATAFORMAS.contains(&ultima) || n.contains("uranyx") {
		"corpo"
	} else {
		"so_contar"
	}
}

/// Os campos que o `MessageInV2` aceita (a Central recusa campo a mais).
pub const CAMPOS_EMAIL: &[&str] = &[
	"source_id", "folder", "direction", "received_at", "subject", "from_address", "from_name", "to", "cc", "reply_to",
	"message_id", "in_reply_to", "references", "text", "attachments", "tuta", "delivered_to", "text_from_html",
	"omitted_attachments", "raw_headers",
];
pub const CAMPOS_TUTA: &[&str] = &[
	"mail_id", "folder_key", "folder_kind", "folder_path", "conversation_id", "state", "unread", "replied",
	"phishing_status", "auth_status", "envelope_sender", "labels", "sent_at", "codes_masked", "links_removed",
];

/// "2026-10-08T05:43:20Z" / "…20.123Z" (o formato que o conector manda) → ms.
fn ms(iso: &str) -> u64 {
	let ler = || -> Option<u64> {
		let (data, hora) = iso.trim_end_matches('Z').split_once('T')?;
		let mut d = data.split('-').map(|x| x.parse::<i32>().ok());
		let (ano, mes, dia) = (d.next()??, d.next()??, d.next()??);
		let (hms, frac) = hora.split_once('.').unwrap_or((hora, "0"));
		let mut h = hms.split(':').map(|x| x.parse::<u8>().ok());
		let (hh, mm, ss) = (h.next()??, h.next()??, h.next()??);
		let mes = time::Month::try_from(u8::try_from(mes).ok()?).ok()?;
		let data = time::Date::from_calendar_date(ano, mes, u8::try_from(dia).ok()?).ok()?;
		let hora = time::Time::from_hms(hh, mm, ss).ok()?;
		let s = time::PrimitiveDateTime::new(data, hora).assume_utc().unix_timestamp();
		let milis: u64 = format!("{frac:0<3}")[..3].parse().ok()?;
		Some(u64::try_from(s).ok()? * 1000 + milis)
	};
	ler().unwrap_or(0)
}

#[derive(Default)]
pub struct Central {
	/// A chave da caixa que a pessoa liga na tela (`send_enabled`).
	pub envio_ligado: bool,
	/// 409 another_agent_active no /v2/sync.
	pub outro_agente: bool,
	/// 404 sem código (servidor sem o v2).
	pub sem_v2: bool,
	pub teto_lote: Option<usize>,
	/// e-mail guardado por source_id.
	pub emails: BTreeMap<String, Value>,
	/// source_id → (chave da pasta, apagado).
	pub local: HashMap<String, (String, bool)>,
	/// Tamanho de cada POST /v2/ingest.
	pub lotes: Vec<usize>,
	/// Todo e-mail que chegou (inclusive repetidos e recusados).
	pub recebidos: Vec<Value>,
	pub corpos_ingest: Vec<Value>,
	pub sinais: Vec<Value>,
	pub syncs: Vec<Value>,
	pub contagens: Vec<Value>,
	pub mudancas: Vec<Value>,
	/// As pastas do último /v2/sync que trouxe a lista: chave → (nome, tipo).
	pub pastas: BTreeMap<String, (String, i64)>,
	/// O que a Central manda ler (sobrepõe a regra), por chave.
	pub leitura_forcada: HashMap<String, &'static str>,
	/// Os próximos N e-mails do ingest voltam `folder_unknown`.
	pub pasta_desconhecida: u32,
	pub aliases_so_contar: Vec<String>,
	/// Resultado programado por source_id ("sumir" = sem resultado; "rejeitar" = invalid_message).
	pub roteiro: HashMap<String, VecDeque<&'static str>>,
	/// Falhas programadas por rota ("/v2/ingest", "/heartbeat"…): status.
	pub falhas: VecDeque<(&'static str, u16)>,
	/// O próximo /v2/ingest é PROCESSADO mas a resposta é 502 (queda depois de gravar).
	pub processa_e_cai: u32,
	/// Cada /v2/ingest "demora" isto no relógio do teste (volta longa).
	pub relogio: Option<Arc<std::sync::atomic::AtomicU64>>,
	pub demora_por_lote_ms: u64,
	// A fila de respostas (v1).
	pub fila: VecDeque<Value>,
	/// A pessoa desliga o envio logo depois do lease (o "vai enviar" vê).
	pub desligar_depois_do_lease: bool,
	/// O "vai enviar" (o sinal depois de um lease) "demora" isto no relógio do
	/// teste: o Mac que ficou parado entre o lease e o SendDraft.
	pub demora_no_sinal_depois_do_lease_ms: u64,
	/// job → token do lease.
	pub leases: HashMap<String, String>,
	pub recibos: Vec<(String, Value)>,
	pub recibos_finais: HashMap<String, Value>,
}

impl Central {
	pub fn nova() -> Self {
		Self::default()
	}

	fn leitura_da(&self, chave: &str) -> Option<&'static str> {
		if let Some(f) = self.leitura_forcada.get(chave) {
			return Some(f);
		}
		self.pastas.get(chave).map(|(nome, tipo)| leitura(nome, *tipo))
	}

	fn falha(&mut self, rota: &str) -> Option<Resposta> {
		let i = self.falhas.iter().position(|(r, _)| *r == rota)?;
		let (_, status) = self.falhas.remove(i)?;
		Some(Resposta::json(status, json!({"detail": {"code": "falha_programada"}})))
	}

	/// Um e-mail do lote, como o `mail_v2.ingest` de verdade.
	fn receber(&mut self, e: &Value) -> Option<Value> {
		let sid = e["source_id"].as_str().unwrap_or_default().to_owned();
		if let Some(r) = self.roteiro.get_mut(&sid).and_then(VecDeque::pop_front) {
			return match r {
				"sumir" => None,
				_ => Some(json!({"source_id": sid, "status": "rejected", "code": "invalid_message", "fields": [{"field": "to.0", "type": "value_error"}]})),
			};
		}
		let objeto = e.as_object().cloned().unwrap_or_default();
		let tuta = e["tuta"].as_object().cloned().unwrap_or_default();
		let extra: Vec<String> = objeto
			.keys()
			.filter(|k| !CAMPOS_EMAIL.contains(&k.as_str()))
			.chain(tuta.keys().filter(|k| !CAMPOS_TUTA.contains(&k.as_str())))
			.cloned()
			.collect();
		let mesmo_id = e["tuta"]["mail_id"].as_str().map(|m| format!("tuta:{m}")) == Some(sid.clone());
		if !extra.is_empty() || !mesmo_id || e["from_address"].as_str().is_none_or(|f| !f.contains('@')) {
			return Some(json!({"source_id": sid, "status": "rejected", "code": "invalid_message", "fields": extra.iter().map(|c| json!({"field": c, "type": "extra_forbidden"})).collect::<Vec<_>>()}));
		}
		let pasta = e["tuta"]["folder_key"].as_str().unwrap_or_default().to_owned();
		if self.pasta_desconhecida > 0 {
			self.pasta_desconhecida -= 1;
			return Some(json!({"source_id": sid, "status": "rejected", "code": "folder_unknown"}));
		}
		match self.leitura_da(&pasta) {
			None => return Some(json!({"source_id": sid, "status": "rejected", "code": "folder_unknown"})),
			Some(l) if l != "corpo" => return Some(json!({"source_id": sid, "status": "rejected", "code": "folder_not_read"})),
			_ => {},
		}
		if self.emails.contains_key(&sid) {
			return Some(json!({"source_id": sid, "status": "duplicate"}));
		}
		self.emails.insert(sid.clone(), e.clone());
		self.local.entry(sid.clone()).or_insert((pasta, false));
		Some(json!({"source_id": sid, "status": "accepted"}))
	}

	fn mover(&mut self, sid: &str, pasta: &str, apagado: Option<bool>) {
		let lixeira = self.pastas.get(pasta).is_some_and(|(_, t)| *t == 3);
		let apagado = apagado.unwrap_or(lixeira);
		self.local.insert(sid.to_owned(), (pasta.to_owned(), apagado));
		if let Some(e) = self.emails.get_mut(sid) {
			e["tuta"]["folder_key"] = json!(pasta);
			e["tuta"]["deleted"] = json!(apagado);
		}
	}

	pub fn atender(&mut self, caminho: &str, corpo: &Value) -> Resposta {
		let base = format!("/api/mail/agent/{CAIXA}");
		let Some(rota) = caminho.strip_prefix(&base) else {
			return Resposta::json(401, json!({"detail": {"code": "agent_unauthorized"}}));
		};
		let chave_da_falha = if rota.ends_with("/receipt") { "__recibo__" } else { rota };
		if let Some(r) = self.falha(chave_da_falha) {
			return r;
		}
		if rota.starts_with("/v2/") && self.sem_v2 {
			return Resposta::json(404, json!({"detail": "Not Found"}));
		}
		match rota {
			"/heartbeat" => {
				self.sinais.push(corpo.clone());
				if !self.leases.is_empty() && self.demora_no_sinal_depois_do_lease_ms > 0 {
					if let Some(r) = &self.relogio {
						r.fetch_add(self.demora_no_sinal_depois_do_lease_ms, std::sync::atomic::Ordering::SeqCst);
					}
				}
				Resposta::json(200, json!({"ok": true, "send_enabled": self.envio_ligado}))
			},
			"/v2/sync" => {
				self.syncs.push(corpo.clone());
				if self.outro_agente {
					return Resposta::json(409, json!({"detail": {"code": "another_agent_active"}}));
				}
				if let Some(lista) = corpo["folders"].as_array() {
					self.pastas = lista
						.iter()
						.map(|p| {
							(
								p["key"].as_str().unwrap_or_default().to_owned(),
								(p["name"].as_str().unwrap_or_default().to_owned(), p["kind"].as_i64().unwrap_or(0)),
							)
						})
						.collect();
				}
				let pastas: Vec<Value> = self
					.pastas
					.keys()
					.map(|k| json!({"key": k, "read": self.leitura_da(k).unwrap_or("so_contar")}))
					.collect();
				Resposta::json(200, json!({"contract": 2, "folders": pastas, "count_only_aliases": self.aliases_so_contar}))
			},
			"/v2/ingest" => {
				let emails = corpo["messages"].as_array().cloned().unwrap_or_default();
				self.corpos_ingest.push(corpo.clone());
				self.lotes.push(emails.len());
				if let Some(r) = &self.relogio {
					r.fetch_add(self.demora_por_lote_ms, std::sync::atomic::Ordering::SeqCst);
				}
				if emails.len() > 20 {
					return Resposta::json(422, json!({"detail": {"code": "invalid_body", "fields": [{"field": "messages", "type": "too_long"}]}}));
				}
				if self.teto_lote.is_some_and(|t| emails.len() > t) {
					return Resposta::json(413, json!({"detail": {"code": "body_too_large"}}));
				}
				let resultados: Vec<Value> = emails
					.iter()
					.filter_map(|e| {
						self.recebidos.push(e.clone());
						self.receber(e)
					})
					.collect();
				if self.processa_e_cai > 0 {
					self.processa_e_cai -= 1;
					return Resposta::vazia(502);
				}
				Resposta::json(200, json!({"results": resultados}))
			},
			"/v2/count" => {
				self.contagens.push(corpo.clone());
				let pasta = corpo["folder_key"].as_str().unwrap_or_default().to_owned();
				let Some(leitura) = self.leitura_da(&pasta) else {
					return Resposta::json(409, json!({"detail": {"code": "folder_unknown"}}));
				};
				let ids: Vec<String> = corpo["ids"].as_array().cloned().unwrap_or_default().iter().filter_map(|v| v.as_str().map(str::to_owned)).collect();
				let lixeira = self.pastas.get(&pasta).is_some_and(|(_, t)| *t == 3);
				let mut faltando = vec![];
				let mut movidos = 0;
				for sid in &ids {
					match self.local.get(sid).cloned() {
						None if leitura == "corpo" => faltando.push(sid.clone()),
						Some((chave, apagado)) if chave != pasta || (apagado && !lixeira) => {
							self.mover(sid, &pasta, None);
							movidos += 1;
						},
						_ => {},
					}
				}
				let mut saiu = vec![];
				if corpo["complete"] == true {
					if let Some(desde) = corpo["since"].as_str().map(ms) {
						let ate = corpo["until"].as_str().map(ms);
						let conjunto: HashSet<&String> = ids.iter().collect();
						for (sid, (chave, apagado)) in &self.local {
							let recebido = self.emails.get(sid).and_then(|e| e["received_at"].as_str()).map_or(0, ms);
							if *chave == pasta && !*apagado && recebido >= desde && ate.is_none_or(|a| recebido < a) && !conjunto.contains(sid) {
								saiu.push(sid.clone());
							}
						}
					}
				}
				Resposta::json(200, json!({"missing": faltando, "moved": movidos, "left": saiu}))
			},
			"/v2/changes" => {
				self.mudancas.push(corpo.clone());
				let mut atualizados = 0;
				let mut desconhecidos = 0;
				for m in corpo["changes"].as_array().cloned().unwrap_or_default() {
					let sid = m["source_id"].as_str().unwrap_or_default().to_owned();
					if !self.local.contains_key(&sid) {
						desconhecidos += 1;
						continue;
					}
					let apagado = m["deleted"] == true;
					match m["folder_key"].as_str() {
						Some(p) => self.mover(&sid, p, apagado.then_some(true)),
						None if apagado => {
							if let Some(l) = self.local.get_mut(&sid) {
								l.1 = true;
							}
						},
						None => {},
					}
					atualizados += 1;
				}
				Resposta::json(200, json!({"updated": atualizados, "unknown": desconhecidos}))
			},
			"/outbox/lease" => {
				let mut jobs = vec![];
				if self.envio_ligado {
					while let Some(mut j) = self.fila.pop_front() {
						let token = format!("token-do-lease-{}-{}", jobs.len(), "x".repeat(24));
						self.leases.insert(j["id"].as_str().unwrap_or_default().to_owned(), token.clone());
						j["lease_token"] = json!(token);
						jobs.push(j);
						if jobs.len() == 5 {
							break;
						}
					}
				}
				if self.desligar_depois_do_lease && !jobs.is_empty() {
					self.envio_ligado = false;
				}
				Resposta::json(200, json!({"jobs": jobs}))
			},
			outra if outra.starts_with("/outbox/") && outra.ends_with("/receipt") => {
				let job = outra.trim_start_matches("/outbox/").trim_end_matches("/receipt").to_owned();
				self.recibos.push((job.clone(), corpo.clone()));
				if self.leases.get(&job).map(String::as_str) != corpo["lease_token"].as_str() {
					return Resposta::json(401, json!({"detail": {"code": "invalid_lease"}}));
				}
				let mut gravado = corpo.clone();
				if let Some(o) = gravado.as_object_mut() {
					o.remove("lease_token");
				}
				match self.recibos_finais.get(&job) {
					Some(antes) if *antes != gravado => Resposta::json(409, json!({"detail": {"code": "receipt_conflict"}})),
					_ => {
						self.recibos_finais.insert(job, gravado);
						Resposta::json(200, json!({"ok": true, "status": corpo["status"]}))
					},
				}
			},
			_ => Resposta::json(404, json!({"detail": "Not Found"})),
		}
	}

	/// Quantos POST /v2/ingest chegaram.
	pub fn quantos_lotes(&self) -> usize {
		self.lotes.len()
	}

	/// Uma resposta de PESSOA na fila (o job do lease v1).
	pub fn enfileirar(&mut self, id: &str, de: &str, para: &str, fio: Option<&str>, texto: &str) {
		self.fila.push_back(json!({
			"id": id, "from_address": de, "to": para, "subject": "Re: Pedido", "text": texto,
			"in_reply_to": fio, "references": fio.map(|f| vec![f]).unwrap_or_default(),
			"message_id": format!("<{id}@mail.davinci.local>"),
		}));
	}
}

pub fn central(estado: Central) -> (ServidorFalso, Arc<Mutex<Central>>) {
	let estado = Arc::new(Mutex::new(estado));
	let e = estado.clone();
	let servidor = ServidorFalso::iniciar(move |p| {
		if p.cabecalhos.get("authorization").map(String::as_str) != Some(&format!("Bearer {TOKEN_DAVINCI}")) {
			return Resposta::json(401, json!({"detail": {"code": "agent_unauthorized"}}));
		}
		e.lock().unwrap().atender(&p.caminho, &p.json())
	});
	(servidor, estado)
}
