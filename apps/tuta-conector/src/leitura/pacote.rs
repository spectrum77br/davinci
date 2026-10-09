//! O PACOTE de um e-mail no formato da Central de e-mail: o `MessageIn` do
//! v1 mais o que só o v2 carrega (o bloco `tuta`, `delivered_to`, os anexos
//! deixados de fora e os cabeçalhos de autenticação), em `schemas/mail_v2.py`
//! do DaVinci. Puro: só monta JSON a partir do que a caixa leu.
//!
//! Tudo o que a Central recusaria é arrumado AQUI (senão o e-mail volta
//! `rejected`): assunto e cabeçalhos numa linha só de até 998, endereços que
//! o validador dela não aceita saem da lista, até 100 no Para/Cc, o texto em
//! até 2 MiB caracteres, anexos até 10 de 10 MiB.
//!
//! O corpo do Tuta (HTML) vira TEXTO (`texto::html`) e o link de acesso e o
//! código de verificação são mascarados (`texto::protecao`) ANTES de sair do
//! Mac — o mesmo que o DaVinci faria, mas aqui o original nem chega lá.

use crate::config;
use crate::leitura::caixa::{EmailLido, PastaTuta, KIND_MARCADOR, KIND_TODOS};
use crate::texto::{self, html, protecao};
use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use serde_json::{json, Value};
use std::collections::{BTreeSet, HashMap};
use tutasdk::IdTupleGenerated;

/// As pastas da caixa por id (para o nome/tipo/caminho das pastas do e-mail).
pub type MapaPastas = HashMap<String, PastaTuta>;

/// Os cabeçalhos que vão (só os de autenticação, para o aviso de golpe).
const CABECALHOS_DE_AUTENTICACAO: &[&str] = &[
	"authentication-results",
	"arc-authentication-results",
	"received-spf",
	"dkim-signature",
	"return-path",
];
/// Onde o servidor de entrada diz quem recebeu (o alias da conta).
const CABECALHOS_DE_ENTREGA: &[&str] = &["delivered-to", "x-original-to", "envelope-to", "x-delivered-to"];

/// O id do e-mail na Central: "tuta:<lista>/<elemento>" (não muda se o e-mail
/// for movido; o Message-ID se repete entre cópias e o id da entrada muda).
#[must_use]
pub fn source_id(id: &IdTupleGenerated) -> String {
	format!("tuta:{}/{}", id.list_id.as_str(), id.element_id.as_str())
}

/// O contrário de `source_id`.
#[must_use]
pub fn id_do_source(source_id: &str) -> Option<IdTupleGenerated> {
	let (lista, elemento) = source_id.strip_prefix("tuta:")?.split_once('/')?;
	let ok = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
	if !ok(lista) || !ok(elemento) {
		return None;
	}
	Some(IdTupleGenerated::new(
		tutasdk::GeneratedId(lista.to_owned()),
		tutasdk::GeneratedId(elemento.to_owned()),
	))
}

// ── Endereços ───────────────────────────────────────────────────────────

const TLD_ESPECIAIS: &[&str] = &["arpa", "invalid", "local", "localhost", "onion", "test"];

/// O endereço passa no `EmailStr` da Central? (o validador de e-mail do
/// Python: sem aspas, sem ".." nem ponto nas pontas, domínio com ponto, TLD
/// que não é só número nem de uso especial). Devolve minúsculo.
#[must_use]
pub fn endereco_valido(bruto: &str) -> Option<String> {
	let e = bruto.trim();
	if e.is_empty() || e.chars().count() > config::ENDERECO_MAX_CARACTERES || e.chars().any(|c| c.is_whitespace() || c.is_control()) {
		return None;
	}
	let (local, dominio) = e.rsplit_once('@')?;
	if local.is_empty() || local.chars().count() > 64 || local.contains('@') {
		return None;
	}
	if local.starts_with('.') || local.ends_with('.') || local.contains("..") {
		return None;
	}
	let especial = |c: char| "!#$%&'*+/=?^_`{|}~.-".contains(c);
	if !local.chars().all(|c| c.is_alphanumeric() || especial(c)) {
		return None;
	}
	let dominio = dominio.to_lowercase();
	let rotulos: Vec<&str> = dominio.split('.').collect();
	if rotulos.len() < 2 {
		return None;
	}
	for r in &rotulos {
		if r.is_empty() || r.len() > 63 || r.starts_with('-') || r.ends_with('-') {
			return None;
		}
		if !r.chars().all(|c| c.is_alphanumeric() || c == '-') {
			return None;
		}
	}
	let tld = rotulos.last()?;
	if tld.chars().all(|c| c.is_ascii_digit()) || TLD_ESPECIAIS.contains(tld) {
		return None;
	}
	Some(format!("{}@{dominio}", local.to_lowercase()))
}

fn lista_de_enderecos<'a>(enderecos: impl Iterator<Item = &'a str>, maximo: usize) -> Vec<String> {
	let mut saida: Vec<String> = Vec::new();
	for e in enderecos.filter_map(endereco_valido) {
		if !saida.contains(&e) {
			saida.push(e);
		}
		if saida.len() >= maximo {
			break;
		}
	}
	saida
}

// ── Cabeçalhos brutos ───────────────────────────────────────────────────

/// Os cabeçalhos brutos desdobrados: (nome minúsculo, valor numa linha, linha original).
fn cabecalhos(brutos: &str) -> Vec<(String, String, String)> {
	let mut saida: Vec<(String, String, String)> = Vec::new();
	for linha in brutos.split('\n') {
		let linha = linha.trim_end_matches('\r');
		if linha.is_empty() {
			continue;
		}
		if linha.starts_with([' ', '\t']) {
			if let Some(ultimo) = saida.last_mut() {
				ultimo.1.push(' ');
				ultimo.1.push_str(linha.trim());
				ultimo.2.push_str("\r\n");
				ultimo.2.push_str(linha);
			}
			continue;
		}
		if let Some((nome, valor)) = linha.split_once(':') {
			saida.push((nome.trim().to_ascii_lowercase(), valor.trim().to_owned(), linha.to_owned()));
		}
		if saida.len() > 500 {
			break;
		}
	}
	saida
}

/// Os "<…>" de um cabeçalho (Message-ID, In-Reply-To, References).
fn ids_de_mensagem(valor: &str) -> Vec<String> {
	let mut saida = Vec::new();
	let mut resto = valor;
	while let Some(a) = resto.find('<') {
		let Some(b) = resto[a..].find('>') else {
			break;
		};
		let id = &resto[a..=a + b];
		if id.len() > 2 && id.chars().count() <= config::MESSAGE_ID_MAX_CARACTERES && !id.chars().any(char::is_control) {
			saida.push(id.to_owned());
		}
		resto = &resto[a + b + 1..];
	}
	saida
}

/// Os endereços de um cabeçalho de entrega ("Delivered-To: x@y").
fn enderecos_do_cabecalho(valor: &str) -> Vec<String> {
	valor
		.split([',', ';', ' ', '<', '>'])
		.filter_map(endereco_valido)
		.collect()
}

/// Um Header (até 998, uma linha) ou vazio.
fn cabecalho_ou_vazio(valor: &str) -> String {
	if valor.chars().count() > config::MESSAGE_ID_MAX_CARACTERES || valor.chars().any(char::is_control) {
		return String::new();
	}
	valor.to_owned()
}

// ── Anexos ──────────────────────────────────────────────────────────────

/// Anexo que a Central não deve receber (extensão ou tipo perigoso): fica só
/// no rastro (`omitted_attachments`), como o DaVinci já fazia.
#[must_use]
pub fn anexo_perigoso(nome: &str, tipo: &str) -> bool {
	let n = nome.trim().to_lowercase();
	let t = tipo.to_lowercase();
	config::ANEXO_EXTENSOES_PERIGOSAS.iter().any(|e| n.ends_with(e)) || config::ANEXO_TIPOS_PERIGOSOS.iter().any(|x| t.contains(x))
}

/// O nome do arquivo como a Central aceita (sem CR/LF/NUL, até 255).
#[must_use]
pub fn nome_de_arquivo(nome: &str, n: usize) -> String {
	let limpo = texto::uma_linha(nome, 255);
	if limpo.is_empty() {
		format!("anexo-{n}")
	} else {
		limpo
	}
}

/// O tipo como a Central aceita (`type/subtype` simples), senão octet-stream.
#[must_use]
pub fn tipo_de_arquivo(tipo: &str) -> String {
	let t = tipo.split(';').next().unwrap_or("").trim().to_ascii_lowercase();
	let ok_parte = |p: &str| !p.is_empty() && p.chars().all(|c| c.is_ascii_alphanumeric() || "!#$&^_.+-".contains(c));
	match t.split_once('/') {
		Some((a, b)) if ok_parte(a) && ok_parte(b) && t.len() <= 127 => t,
		_ => "application/octet-stream".to_owned(),
	}
}

/// Um anexo baixado e decifrado (vai em base64 no pacote).
pub struct AnexoBaixado {
	pub nome: String,
	pub tipo: String,
	pub bytes: Vec<u8>,
}

/// Um anexo que NÃO foi (e por quê): perigoso, grande_demais, ilegivel, teto_do_email.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AnexoOmitido {
	pub nome: String,
	pub tipo: String,
	pub tamanho: u64,
	pub motivo: &'static str,
}

fn omitido_json(a: &AnexoOmitido, n: usize) -> Value {
	json!({
		"filename": nome_de_arquivo(&a.nome, n),
		"content_type": texto::uma_linha(&a.tipo, 127),
		"size": a.tamanho,
		"reason": a.motivo,
	})
}

// ── O pacote ────────────────────────────────────────────────────────────

/// Por que um e-mail não vira pacote (fica registrado, não vai).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SemPacote {
	/// O remetente não é um endereço que a Central aceita.
	RemetenteInvalido,
	/// Rascunho ou ainda enviando (estado 0/3): não é e-mail de verdade ainda.
	NaoEnviado,
}

/// A pasta "de verdade" do e-mail (a primeira do `sets` que não é marcador),
/// ou a pasta em que ele foi achado.
fn pasta_do_email<'a>(email: &EmailLido, achado_em: &'a PastaTuta, mapa: &'a MapaPastas) -> &'a PastaTuta {
	email
		.mail
		.sets
		.iter()
		.filter_map(|s| mapa.get(s.element_id.as_str()))
		.find(|p| p.tipo != KIND_MARCADOR && p.tipo != KIND_TODOS)
		.unwrap_or(achado_em)
}

/// Os aliases DA CONTA que receberam: os cabeçalhos de entrega, senão o Para e o Cc.
#[must_use]
pub fn quem_recebeu(email: &EmailLido, aliases_conta: &BTreeSet<String>) -> Vec<String> {
	let d = email.detalhes.as_ref();
	let brutos = d
		.and_then(|d| d.headers.as_ref())
		.and_then(|h| h.compressedHeaders.clone().or_else(|| h.headers.clone()))
		.unwrap_or_default();
	let mut saida: Vec<String> = Vec::new();
	for (nome, valor, _) in cabecalhos(&brutos) {
		if CABECALHOS_DE_ENTREGA.contains(&nome.as_str()) {
			for e in enderecos_do_cabecalho(&valor) {
				if aliases_conta.contains(&e) && !saida.contains(&e) {
					saida.push(e);
				}
			}
		}
	}
	if saida.is_empty() {
		let para = d.map(|d| d.recipients.toRecipients.iter().chain(d.recipients.ccRecipients.iter()).map(|r| r.address.as_str()).collect::<Vec<_>>());
		let enderecos: Vec<&str> =
			para.unwrap_or_else(|| email.mail.firstRecipient.iter().map(|r| r.address.as_str()).collect());
		for e in enderecos.into_iter().filter_map(endereco_valido) {
			if aliases_conta.contains(&e) && !saida.contains(&e) {
				saida.push(e);
			}
		}
	}
	saida.truncate(config::ENTREGUE_A_MAX);
	saida
}

/// A pasta como a Central aceita (`folder`: 1 a 128, uma linha).
fn nome_da_pasta(caminho: &str) -> String {
	let n = texto::uma_linha(caminho, config::PASTA_MAX_CARACTERES);
	if n.is_empty() {
		"?".to_owned()
	} else {
		n
	}
}

fn iso(ms: u64) -> String {
	let nanos = i128::from(ms) * 1_000_000;
	time::OffsetDateTime::from_unix_timestamp_nanos(nanos)
		.ok()
		.and_then(|d| d.format(&time::format_description::well_known::Rfc3339).ok())
		.unwrap_or_else(|| "1970-01-01T00:00:00Z".to_owned())
}

/// O pacote COMPLETO de um e-mail de pasta lida com corpo.
pub fn completo(
	email: &EmailLido,
	achado_em: &PastaTuta,
	mapa: &MapaPastas,
	aliases_conta: &BTreeSet<String>,
	anexos: &[AnexoBaixado],
	omitidos: &[AnexoOmitido],
) -> Result<Value, SemPacote> {
	let m = &email.mail;
	let direcao = match m.state {
		2 => "inbound",
		1 => "sent",
		_ => return Err(SemPacote::NaoEnviado),
	};
	let de = endereco_valido(&m.sender.address).ok_or(SemPacote::RemetenteInvalido)?;
	let pasta = pasta_do_email(email, achado_em, mapa);
	let d = email.detalhes.as_ref();

	// O corpo (HTML do Tuta) → texto; depois o que a equipe nunca vê.
	let html_bruto = d.and_then(|d| d.body.compressedText.clone().or_else(|| d.body.text.clone())).unwrap_or_default();
	let html_bruto = texto::cortar(&html_bruto, config::CORPO_HTML_MAX_CARACTERES);
	let convertido = html::para_texto(&html_bruto, config::TEXTO_MAX_CARACTERES);
	let assunto = texto::uma_linha(&m.subject, config::ASSUNTO_MAX_CARACTERES);
	let protegido = protecao::proteger(&assunto, &convertido.texto);
	let texto_final = texto::cortar(&protegido.texto, config::TEXTO_MAX_CARACTERES);

	// Cabeçalhos: fio (In-Reply-To/References) e os de autenticação.
	let brutos = d
		.and_then(|d| d.headers.as_ref())
		.and_then(|h| h.compressedHeaders.clone().or_else(|| h.headers.clone()))
		.unwrap_or_default();
	let lidos = cabecalhos(&texto::cortar(&brutos, 2 * config::CABECALHOS_MAX_CARACTERES));
	let mut em_resposta_a = lidos
		.iter()
		.find(|(n, _, _)| n == "in-reply-to")
		.and_then(|(_, v, _)| ids_de_mensagem(v).into_iter().next());
	if em_resposta_a.is_none() {
		// O enviado não tem cabeçalho bruto: o fio do Tuta diz a quem respondeu.
		em_resposta_a = email.resposta_a.as_deref().map(cabecalho_ou_vazio).filter(|v| !v.is_empty());
	}
	let mut referencias: Vec<String> = lidos
		.iter()
		.filter(|(n, _, _)| n == "references")
		.flat_map(|(_, v, _)| ids_de_mensagem(v))
		.collect();
	if referencias.len() > config::REFERENCIAS_MAX {
		referencias.drain(..referencias.len() - config::REFERENCIAS_MAX);
	}
	let autenticacao: Vec<&str> = lidos
		.iter()
		.filter(|(n, _, _)| CABECALHOS_DE_AUTENTICACAO.contains(&n.as_str()))
		.map(|(_, _, linha)| linha.as_str())
		.collect();
	let cabecalhos_auth = (!autenticacao.is_empty()).then(|| texto::cortar(&autenticacao.join("\r\n"), config::CABECALHOS_MAX_CARACTERES));

	let para = match d {
		Some(d) => lista_de_enderecos(d.recipients.toRecipients.iter().map(|r| r.address.as_str()), config::ENDERECOS_MAX),
		None => lista_de_enderecos(m.firstRecipient.iter().map(|r| r.address.as_str()), config::ENDERECOS_MAX),
	};
	let cc = d
		.map(|d| lista_de_enderecos(d.recipients.ccRecipients.iter().map(|r| r.address.as_str()), config::ENDERECOS_MAX))
		.unwrap_or_default();
	let reply_to = d.and_then(|d| d.replyTos.iter().find_map(|r| endereco_valido(&r.address)));
	let rotulos: Vec<&str> = m
		.sets
		.iter()
		.filter_map(|s| mapa.get(s.element_id.as_str()))
		.filter(|p| p.tipo == KIND_MARCADOR)
		.map(|p| p.id.as_str())
		.take(20)
		.collect();
	let envelope = m
		.differentEnvelopeSender
		.as_deref()
		.map(str::trim)
		.filter(|e| !e.is_empty() && e.chars().count() <= 254 && !e.chars().any(|c| c.is_whitespace() || c.is_control()));
	let anexos_json: Vec<Value> = anexos
		.iter()
		.enumerate()
		.map(|(n, a)| {
			json!({
				"filename": nome_de_arquivo(&a.nome, n + 1),
				"content_type": tipo_de_arquivo(&a.tipo),
				"data_base64": BASE64.encode(&a.bytes),
			})
		})
		.collect();
	let omitidos_json: Vec<Value> = omitidos.iter().enumerate().map(|(n, a)| omitido_json(a, n + 1)).collect();
	let auth = m.authStatus.or(d.map(|d| d.authStatus));
	Ok(json!({
		"source_id": source_id(&email.id),
		"folder": nome_da_pasta(&pasta.caminho),
		"direction": direcao,
		"received_at": iso(m.receivedDate.as_millis()),
		"subject": texto::cortar(&protegido.assunto, config::ASSUNTO_MAX_CARACTERES),
		"from_address": de,
		"from_name": texto::uma_linha(&m.sender.name, config::NOME_MAX_CARACTERES),
		"to": para,
		"cc": cc,
		"reply_to": reply_to,
		// Exatamente como o Tuta guarda: volta no lease como o fio da resposta.
		"message_id": email.message_id.as_deref().map(cabecalho_ou_vazio).unwrap_or_default(),
		"in_reply_to": em_resposta_a,
		"references": referencias,
		"text": texto_final,
		"attachments": anexos_json,
		"tuta": {
			"mail_id": format!("{}/{}", email.id.list_id.as_str(), email.id.element_id.as_str()),
			"folder_key": pasta.id,
			"folder_kind": pasta.tipo.to_string(),
			"folder_path": texto::uma_linha(&pasta.caminho, 1000),
			"conversation_id": m.conversationEntry.list_id.as_str(),
			"state": m.state,
			"unread": m.unread,
			"replied": m.replyType,
			"phishing_status": m.phishingStatus.to_string(),
			"auth_status": auth.map(|a| a.to_string()),
			"envelope_sender": envelope,
			"labels": rotulos,
			"sent_at": d.map(|d| iso(d.sentDate.as_millis())),
			"codes_masked": protegido.codigo_mascarado,
			"links_removed": protegido.links_removidos,
		},
		"delivered_to": quem_recebeu(email, aliases_conta),
		"text_from_html": true,
		"omitted_attachments": omitidos_json,
		"raw_headers": cabecalhos_auth,
	}))
}

/// O corpo do POST /v2/ingest.
#[must_use]
pub fn lote(pacotes: &[&Value]) -> Vec<u8> {
	serde_json::to_vec(&json!({ "messages": pacotes })).unwrap_or_default()
}

/// O tamanho de um pacote no lote (bytes do JSON).
#[must_use]
pub fn tamanho(pacote: &Value) -> usize {
	serde_json::to_vec(pacote).map(|v| v.len()).unwrap_or(usize::MAX)
}

/// Se um pacote sozinho passar do teto do pedido: primeiro o texto encolhe;
/// se não bastar, os anexos (do maior para o menor) saem para a lista dos
/// deixados de fora ("teto_do_email"). O original continua no Tuta.
pub fn caber(pacote: &mut Value, teto_bytes: usize) -> bool {
	let mut mexeu = false;
	for _ in 0..6 {
		let atual = tamanho(pacote);
		if atual <= teto_bytes {
			return mexeu;
		}
		let corpo = pacote.get("text").and_then(Value::as_str).unwrap_or("").to_owned();
		let caracteres = corpo.chars().count();
		if caracteres < 2048 {
			break;
		}
		let sobra = atual - teto_bytes;
		let novo = caracteres.saturating_sub(sobra + 1024).min(caracteres / 2);
		pacote["text"] = Value::String(format!("{}\n{}", texto::cortar(&corpo, novo), html::MARCA_CORTE));
		mexeu = true;
	}
	while tamanho(pacote) > teto_bytes {
		let Some(anexos) = pacote.get_mut("attachments").and_then(Value::as_array_mut) else {
			break;
		};
		let Some((i, _)) = anexos
			.iter()
			.enumerate()
			.max_by_key(|(_, a)| a.get("data_base64").and_then(Value::as_str).map_or(0, str::len))
		else {
			break;
		};
		let tirado = anexos.remove(i);
		let tamanho_bytes = tirado.get("data_base64").and_then(Value::as_str).map_or(0, |b| b.len() / 4 * 3);
		let omitido = json!({
			"filename": tirado.get("filename").cloned().unwrap_or(Value::String("anexo".into())),
			"content_type": tirado.get("content_type").cloned().unwrap_or(Value::String(String::new())),
			"size": tamanho_bytes,
			"reason": "teto_do_email",
		});
		if let Some(lista) = pacote.get_mut("omitted_attachments").and_then(Value::as_array_mut) {
			lista.push(omitido);
		}
		mexeu = true;
	}
	mexeu
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn enderecos_como_a_central() {
		assert_eq!(endereco_valido(" Cliente@Exemplo.COM.br "), Some("cliente@exemplo.com.br".into()));
		assert_eq!(endereco_valido("a.b+c@x-y.com"), Some("a.b+c@x-y.com".into()));
		assert_eq!(endereco_valido("joão@x.com"), Some("joão@x.com".into()));
		for ruim in ["", "sem-arroba", "a..b@x.com", ".a@x.com", "a@x", "a@-x.com", "a@x_y.com", "\"a b\"@x.com", "a@x.test", "a@x.local", "a@x.123", "a b@x.com"] {
			assert_eq!(endereco_valido(ruim), None, "{ruim}");
		}
	}

	#[test]
	fn ids_dos_cabecalhos() {
		let brutos = "Message-ID: <a@b>\r\nIn-Reply-To: <pai@x.com>\r\nReferences: <1@x>\r\n <2@x>\r\n\t<3@x>\r\nDelivered-To: 21max@tuta.com\r\nAuthentication-Results: mx; dkim=pass\r\n";
		let lidos = cabecalhos(brutos);
		let refs: Vec<String> = lidos.iter().filter(|(n, _, _)| n == "references").flat_map(|(_, v, _)| ids_de_mensagem(v)).collect();
		assert_eq!(refs, vec!["<1@x>", "<2@x>", "<3@x>"]);
		assert_eq!(ids_de_mensagem("lixo <a@b> mais <c@d"), vec!["<a@b>"]);
		assert_eq!(enderecos_do_cabecalho("21MAX@tuta.com"), vec!["21max@tuta.com"]);
		assert!(lidos.iter().any(|(n, _, l)| n == "authentication-results" && l.starts_with("Authentication-Results")));
	}

	#[test]
	fn arquivos_como_a_central() {
		assert_eq!(tipo_de_arquivo("Image/PNG; name=x"), "image/png");
		assert_eq!(tipo_de_arquivo("lixo"), "application/octet-stream");
		assert_eq!(tipo_de_arquivo("a/b c"), "application/octet-stream");
		assert_eq!(nome_de_arquivo("nota\r\n.pdf", 1), "nota .pdf");
		assert_eq!(nome_de_arquivo("  ", 3), "anexo-3");
		assert!(anexo_perigoso("nota.EXE", ""));
		assert!(anexo_perigoso("x.html", "text/html"));
		assert!(!anexo_perigoso("nota.pdf", "application/pdf"));
	}

	#[test]
	fn ids_do_tuta_vao_e_voltam() {
		let id = IdTupleGenerated::new(tutasdk::GeneratedId("Lista_1-".into()), tutasdk::GeneratedId("Elem2".into()));
		assert_eq!(source_id(&id), "tuta:Lista_1-/Elem2");
		assert_eq!(id_do_source("tuta:Lista_1-/Elem2"), Some(id));
		assert_eq!(id_do_source("imap:39"), None);
		assert_eq!(id_do_source("tuta:a/b/c"), None);
	}

	#[test]
	fn pacote_grande_encolhe_ate_caber() {
		let mut p = json!({"text": "x".repeat(100_000), "attachments": [], "omitted_attachments": []});
		assert!(caber(&mut p, 20_000));
		assert!(tamanho(&p) <= 20_000);
		let mut com_anexo = json!({"text": "oi", "attachments": [
			{"filename": "a.pdf", "content_type": "application/pdf", "data_base64": "A".repeat(30_000)},
			{"filename": "b.pdf", "content_type": "application/pdf", "data_base64": "B".repeat(100)},
		], "omitted_attachments": []});
		assert!(caber(&mut com_anexo, 20_000));
		assert_eq!(com_anexo["attachments"].as_array().unwrap().len(), 1);
		assert_eq!(com_anexo["omitted_attachments"][0]["reason"], "teto_do_email");
		let mut pequeno = json!({"text": "oi"});
		assert!(!caber(&mut pequeno, 1000));
	}

	#[test]
	fn data_em_iso_com_fuso() {
		assert_eq!(iso(0), "1970-01-01T00:00:00Z");
		assert_eq!(iso(1_791_440_000_123), "2026-10-08T06:13:20.123Z");
	}
}
