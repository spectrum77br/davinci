//! A LEITURA de ponta a ponta contra um Tuta FALSO com caixa cifrada de
//! verdade (tests/comum/caixa.rs: o SDK oficial + remendos decifra tudo) e
//! uma Central de e-mail FALSA que guarda o que recebe
//! (tests/comum/central.rs). Nenhuma conta, nenhum dado real, só 127.0.0.1.

mod comum;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use comum::caixa::{Anexo, CaixaFalsa, Falha, NovoEmail};
use comum::central::{central, Central};
use comum::*;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tuta_conector::chaveiro::{self, CofreMemoria, SessaoTuta};
use tuta_conector::config::Conta;
use tuta_conector::davinci::ClienteDavinci;
use tuta_conector::estado::{Compartilhado, EstadoConector};
use tuta_conector::estado_local::EstadoLocal;
use tuta_conector::leitura::caixa::FalhaTuta;
use tuta_conector::leitura::{Dependencias, Leitor, OpcoesLeitura, Parada};
use tuta_conector::rede::{ClienteRest, Politica};
use tuta_conector::registro;
use tuta_conector::segredo::{Segredo, SegredoBytes};
use tutasdk::bindings::rest_client::RestClient;
use tutasdk::{GeneratedId, IdTupleGenerated};

/// 2026-10-08 06:13 UTC (03:13 em Brasília: antes do fechamento do dia).
const AGORA: u64 = 1_791_440_000_000;
const MIN: u64 = 60_000;

fn sid(id: &IdTupleGenerated) -> String {
	format!("tuta:{}/{}", id.list_id.as_str(), id.element_id.as_str())
}

fn chave(id: &IdTupleGenerated) -> String {
	format!("{}/{}", id.list_id.as_str(), id.element_id.as_str())
}

fn rest(politica: Politica) -> Arc<dyn RestClient> {
	Arc::new(ClienteRest::nativo(politica, Duration::from_secs(20)).unwrap())
}

fn releases(versoes: &[&str]) -> ServidorFalso {
	let lista: Vec<Value> = versoes
		.iter()
		.map(|v| json!({"tag_name": format!("tutanota-desktop-release-{v}"), "draft": false, "prerelease": false}))
		.collect();
	ServidorFalso::iniciar(move |p| {
		assert_eq!(p.cabecalhos.get("user-agent").map(String::as_str), Some("davinci-tuta-conector"));
		Resposta::json(200, Value::Array(lista.clone()))
	})
}

struct Cena {
	tuta: ServidorFalso,
	caixa: Arc<Mutex<CaixaFalsa>>,
	central_srv: ServidorFalso,
	central: Arc<Mutex<Central>>,
	github: ServidorFalso,
	cofre: Arc<CofreMemoria>,
	pasta: PathBuf,
	relogio: Arc<AtomicU64>,
	compartilhado: Arc<Compartilhado>,
}

impl Cena {
	fn nova(nome: &str, caixa: CaixaFalsa, mut cv: Central) -> Self {
		registro::iniciar_para_testes();
		let cofre = Arc::new(CofreMemoria::default());
		guardar_sessao(&cofre, &caixa);
		let (tuta, caixa) = caixa.servir();
		let relogio = Arc::new(AtomicU64::new(AGORA));
		cv.relogio = Some(relogio.clone());
		let (central_srv, central) = central(cv);
		Self {
			tuta,
			caixa,
			central_srv,
			central,
			github: releases(&[tutasdk::CLIENT_VERSION]),
			cofre,
			pasta: pasta_temporaria(nome),
			relogio,
			compartilhado: Arc::new(Compartilhado::default()),
		}
	}

	fn cliente(&self) -> Arc<ClienteDavinci> {
		Arc::new(
			ClienteDavinci::com_cliente(
				&self.central_srv.url,
				CAIXA,
				Segredo::novo(TOKEN_DAVINCI.into()),
				rest(Politica::davinci(&self.central_srv.url).unwrap()),
			)
			.unwrap(),
		)
	}

	fn leitor(&self, opcoes: OpcoesLeitura) -> Leitor {
		let relogio = self.relogio.clone();
		let davinci = if opcoes.seco { None } else { Some(self.cliente()) };
		Leitor::novo(
			Dependencias {
				conta: Conta::Geral,
				cofre: self.cofre.clone(),
				tuta_url: self.tuta.url.clone(),
				rest_tuta: rest(Politica::tuta(&self.tuta.url, &[".tuta.com"]).unwrap()),
				davinci,
				url_releases: format!("{}/repos/tutao/tutanota/releases?per_page=50", self.github.url),
				rest_github: rest(Politica::exata(&self.github.url).unwrap()),
				pasta_local: self.pasta.clone(),
				relogio: Arc::new(move || relogio.load(Ordering::SeqCst)),
				compartilhado: self.compartilhado.clone(),
			},
			opcoes,
		)
	}

	fn avancar(&self, ms: u64) {
		self.relogio.fetch_add(ms, Ordering::SeqCst);
	}

	/// A leitura NUNCA muda nada no Tuta: todo pedido é GET, menos o POST do
	/// token de LEITURA de arquivo (BlobAccessTokenService, `read`).
	fn conferir_so_leitura(&self) {
		for p in self.tuta.pedidos() {
			let token_de_leitura = p.metodo == "POST" && p.caminho == "/rest/storage/blobaccesstokenservice";
			assert!(p.metodo == "GET" || token_de_leitura, "{} {} na leitura", p.metodo, p.caminho);
			if token_de_leitura {
				let corpo = p.json();
				// 80 = write, 181 = read (BlobAccessTokenPostIn): só leitura.
				assert!(corpo["80"].as_array().is_none_or(Vec::is_empty), "token de ESCRITA pedido: {corpo}");
				assert!(corpo["181"].as_array().is_some_and(|r| r.len() == 1), "{corpo}");
			}
			assert!(!p.cabecalhos.values().any(|v| v.contains('@')), "e-mail em cabeçalho: {}", p.caminho);
		}
		for p in self.central_srv.pedidos() {
			assert!(p.query_crua.is_empty(), "nada na URL da Central");
			assert!(!p.caminho.contains('@'));
		}
	}

	fn pedidos_tuta(&self, contem: &str) -> usize {
		self.tuta.pedidos().iter().filter(|p| p.caminho.contains(contem)).count()
	}

	fn cv(&self) -> std::sync::MutexGuard<'_, Central> {
		self.central.lock().unwrap()
	}

	fn cx(&self) -> std::sync::MutexGuard<'_, CaixaFalsa> {
		self.caixa.lock().unwrap()
	}

	fn estado(&self) -> EstadoLocal {
		EstadoLocal::carregar(&self.pasta)
	}

	fn ultimo_sinal(&self) -> Value {
		self.cv().sinais.last().cloned().unwrap_or(Value::Null)
	}

	fn ultimo_sync(&self) -> Value {
		self.cv().syncs.last().cloned().unwrap_or(Value::Null)
	}
}

impl Drop for Cena {
	fn drop(&mut self) {
		let _ = std::fs::remove_dir_all(&self.pasta);
	}
}

fn guardar_sessao(cofre: &CofreMemoria, caixa: &CaixaFalsa) {
	let c = caixa.credenciais();
	chaveiro::gravar_sessao(
		cofre,
		Conta::Geral,
		&SessaoTuta {
			login: c.login,
			user_id: c.user_id.as_str().to_owned(),
			access_token: Segredo::novo(c.access_token),
			encrypted_passphrase_key: SegredoBytes::novo(c.encrypted_passphrase_key),
			criada_em_ms: AGORA,
			versao_sdk: tutasdk::CLIENT_VERSION.into(),
		},
	)
	.unwrap();
}

fn uma() -> OpcoesLeitura {
	OpcoesLeitura {
		uma_volta: true,
		..OpcoesLeitura::default()
	}
}

/// Uma caixa com as pastas do dia a dia da equipe.
struct Pastas {
	problema_ml: GeneratedId,
	reclamacao_ml: GeneratedId,
	vendas_shopee: GeneratedId,
	financeiro: GeneratedId,
}

fn caixa_com_pastas() -> (CaixaFalsa, Pastas) {
	let mut c = CaixaFalsa::nova();
	let problema_ml = c.pasta("problema ml", 0, None);
	let reclamacao_ml = c.pasta("reclamação ml", 0, None);
	let vendas_shopee = c.pasta("vendas shopee", 0, None);
	let financeiro = c.pasta("financeiro", 0, None);
	(
		c,
		Pastas {
			problema_ml,
			reclamacao_ml,
			vendas_shopee,
			financeiro,
		},
	)
}

fn email_de<'a>(cv: &'a Central, id: &IdTupleGenerated) -> &'a Value {
	cv.emails.get(&sid(id)).unwrap_or_else(|| panic!("a Central não tem {}", sid(id)))
}

// ── Os casos ────────────────────────────────────────────────────────────

#[tokio::test]
async fn primeira_volta_entrega_no_formato_da_central() {
	let (mut c, p) = caixa_com_pastas();
	let mut completo = NovoEmail::simples(&p.problema_ml, "produto chegou quebrado", AGORA - 30 * MIN);
	completo.de = ("Maria Compradora".into(), "Maria@Exemplo.com.br".into());
	completo.para = vec![("Loja 21".into(), "21max@tuta.com".into())];
	completo.cc = vec![("Outro".into(), "copia@exemplo.com.br".into()), ("Ruim".into(), "sem-arroba".into())];
	completo.reply_to = vec![("Maria".into(), "resposta@exemplo.com.br".into())];
	completo.corpo_html = concat!(
		"<html><head><style>p{color:red}</style></head><body>",
		"<p>Olá, o <b>produto</b> chegou quebrado.</p>",
		"<p>Veja <a href=\"https://loja.example/pedido/2000012345\">o pedido</a>.</p>",
		"<blockquote><p>mensagem antiga</p></blockquote></body></html>"
	)
	.into();
	completo.cabecalhos = format!(
		"Message-ID: {}\r\nIn-Reply-To: <anterior@exemplo.com.br>\r\nReferences: <primeiro@exemplo.com.br>\r\n <anterior@exemplo.com.br>\r\nDelivered-To: 21max@tuta.com\r\nAuthentication-Results: mx.tuta.com; dkim=pass header.d=exemplo.com.br\r\nSubject: produto chegou quebrado\r\n",
		completo.message_id
	);
	completo.anexos = vec![
		Anexo { nome: "nota.pdf".into(), tipo: "application/pdf".into(), bytes: vec![7; 5000], cid: None },
		Anexo { nome: "foto.jpg".into(), tipo: "image/jpeg".into(), bytes: vec![9; 3000], cid: Some("img1".into()) },
		Anexo { nome: "virus.exe".into(), tipo: "application/x-msdownload".into(), bytes: vec![1; 100], cid: None },
	];
	let id_completo = c.email(completo.clone());
	let entrada = c.entrada.clone();
	let spam = c.spam.clone();
	let id_entrada = c.email(NovoEmail::simples(&entrada, "pergunta sobre pedido", AGORA - 20 * MIN));
	let id_spam = c.email(NovoEmail::simples(&spam, "promoção imperdível", AGORA - 10 * MIN));
	let id_fin = c.email(NovoEmail::simples(&p.financeiro, "boleto confidencial", AGORA - 5 * MIN));
	let id_vendas = c.email(NovoEmail::simples(&p.vendas_shopee, "venda 123", AGORA - 3 * MIN));
	let cena = Cena::nova("formato", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	let r = leitor.rodar().await.expect("volta");
	assert_eq!(r.ilegiveis, 0, "{r}");
	assert_eq!(r.anexos, 2, "pdf e jpg junto; o .exe só no rastro: {r}");
	assert_eq!(r.anexos_pulados, 1, "{r}");
	assert_eq!(r.entregues, 3, "completo, Entrada e vendas: {r}");

	let cv = cena.cv();
	// O sinal v1: exatamente o Heartbeat dele.
	assert_eq!(cv.sinais[0], json!({"state": "online", "can_send": false, "error_code": "iniciando"}));
	assert_eq!(cv.sinais.last().unwrap(), &json!({"state": "online", "can_send": false, "error_code": null}));
	// /v2/sync: as 10 pastas (chave, nome, caminho, tipo) e os aliases ATIVOS.
	let sync = &cv.syncs[0];
	assert_eq!(sync["folders"].as_array().unwrap().len(), 10);
	assert_eq!(sync["aliases"], json!(["21max@tuta.com", "22max@tuta.com", "conta.teste@tuta.com"]));
	let entrada_pasta = sync["folders"].as_array().unwrap().iter().find(|x| x["kind"] == 1).unwrap();
	assert_eq!(entrada_pasta["name"], "Entrada");
	assert_eq!(entrada_pasta["key"], entrada.as_str());
	assert!(sync["instance"].as_str().is_some_and(|i| i.len() == 32));
	assert!(sync["agent_version"].as_str().unwrap().starts_with("tuta-conector/"));
	assert_eq!(sync["tuta_version"], tutasdk::CLIENT_VERSION);
	assert_eq!(sync["folders_complete"], true);
	assert!(sync["counters"].as_object().unwrap().values().all(|v| v.as_i64().unwrap() >= 0));

	// O e-mail COMPLETO como a Central guarda (MessageIn + o bloco do Tuta).
	let e = email_de(&cv, &id_completo);
	assert_eq!(e["source_id"], sid(&id_completo));
	assert_eq!(e["folder"], "problema ml");
	assert_eq!(e["direction"], "inbound");
	assert_eq!(e["received_at"], "2026-10-08T05:43:20Z");
	assert_eq!(e["subject"], "produto chegou quebrado");
	assert_eq!(e["from_address"], "maria@exemplo.com.br");
	assert_eq!(e["from_name"], "Maria Compradora");
	assert_eq!(e["to"], json!(["21max@tuta.com"]));
	assert_eq!(e["cc"], json!(["copia@exemplo.com.br"]), "o endereço que a Central recusaria sai");
	assert_eq!(e["reply_to"], "resposta@exemplo.com.br");
	assert_eq!(e["message_id"], completo.message_id);
	assert_eq!(e["in_reply_to"], "<anterior@exemplo.com.br>");
	assert_eq!(e["references"], json!(["<primeiro@exemplo.com.br>", "<anterior@exemplo.com.br>"]));
	// O HTML virou TEXTO (o link mostra o destino; a citação ficou com "> ").
	assert_eq!(
		e["text"],
		"Olá, o produto chegou quebrado.\n\nVeja o pedido <https://loja.example/pedido/2000012345>.\n\n> mensagem antiga"
	);
	assert_eq!(e["text_from_html"], true);
	let anexos = e["attachments"].as_array().unwrap();
	assert_eq!(anexos.len(), 2);
	assert_eq!(anexos[0]["filename"], "nota.pdf");
	assert_eq!(anexos[0]["content_type"], "application/pdf");
	assert_eq!(BASE64.decode(anexos[0]["data_base64"].as_str().unwrap()).unwrap(), vec![7u8; 5000]);
	assert_eq!(BASE64.decode(anexos[1]["data_base64"].as_str().unwrap()).unwrap(), vec![9u8; 3000]);
	assert_eq!(
		e["omitted_attachments"],
		json!([{"filename": "virus.exe", "content_type": "application/x-msdownload", "size": 100, "reason": "perigoso"}])
	);
	let t = &e["tuta"];
	assert_eq!(t["mail_id"], chave(&id_completo));
	assert_eq!(t["folder_key"], p.problema_ml.as_str());
	assert_eq!(t["folder_kind"], "0");
	assert_eq!(t["folder_path"], "problema ml");
	assert!(t["conversation_id"].as_str().is_some_and(|f| !f.is_empty()));
	assert_eq!((t["state"].clone(), t["unread"].clone(), t["replied"].clone()), (json!(2), json!(true), json!(0)));
	assert_eq!((t["phishing_status"].clone(), t["auth_status"].clone()), (json!("0"), json!("0")));
	assert_eq!(t["codes_masked"], false);
	assert_eq!(e["delivered_to"], json!(["21max@tuta.com"]));
	let cab = e["raw_headers"].as_str().unwrap();
	assert!(cab.starts_with("Authentication-Results:"), "{cab}");
	assert!(!cab.contains("Message-ID") && !cab.contains("Subject"), "só os de autenticação: {cab}");

	// Entrada e vendas: com corpo. Spam e financeiro: SÓ contados (o corpo nunca sai).
	assert_eq!(email_de(&cv, &id_entrada)["folder"], "Entrada");
	assert_eq!(email_de(&cv, &id_vendas)["tuta"]["folder_key"], p.vendas_shopee.as_str());
	assert!(!cv.emails.contains_key(&sid(&id_spam)));
	assert!(!cv.emails.contains_key(&sid(&id_fin)));
	let contagem_fin = cv.contagens.iter().find(|x| x["folder_key"] == p.financeiro.as_str()).expect("contagem do financeiro");
	assert_eq!(contagem_fin["ids"], json!([sid(&id_fin)]));
	assert!(cv.contagens.len() >= 8, "o topo de cada pasta (não dos marcadores)");
	drop(cv);
	// O e-mail das pastas só contadas nem foi aberto no Tuta.
	for id in [&id_fin, &id_spam] {
		let caminho = format!("/rest/tutanota/Mail/{}/{}", id.list_id.as_str(), id.element_id.as_str());
		assert_eq!(cena.pedidos_tuta(&caminho), 0);
	}
	// Em disco: só ids e cursores — nada de assunto, endereço, corpo.
	let disco = conteudo_da_pasta(&cena.pasta);
	for proibido in ["quebrado", "maria@", "Olá", "boleto", "promoção", "21max"] {
		assert!(!disco.contains(proibido), "conteúdo de e-mail em disco: {proibido}");
	}
	let estado = cena.estado();
	assert!(estado.cursores.contains_key(p.problema_ml.as_str()));
	assert_eq!(estado.instancia.as_deref(), cena.ultimo_sync()["instance"].as_str(), "a instância fica no disco");
	cena.conferir_so_leitura();
	for linha in registro::capturado() {
		assert!(!linha.contains("quebrado") && !linha.contains("maria@exemplo"), "{linha}");
	}
}

#[tokio::test]
async fn codigos_e_links_de_acesso_saem_mascarados_do_mac() {
	let (mut c, p) = caixa_com_pastas();
	let mut e = NovoEmail::simples(&p.problema_ml, "Seu código de verificação 482913", AGORA - 10 * MIN);
	e.message_id = "<codigo@exemplo.test>".into();
	e.cabecalhos = "Message-ID: <codigo@exemplo.test>\r\nSubject: Seu código de verificação 482913\r\n".into();
	e.corpo_html = concat!(
		"<p>Use o código 482913 para entrar.</p>",
		"<p><a href=\"https://contas.example/redefinir-senha?token=abcdef\">Redefinir senha</a></p>",
		"<p>Seu pedido 2000012345678901 segue.</p>"
	)
	.into();
	let id = c.email(e);
	let cena = Cena::nova("codigos", c, Central::nova());
	cena.leitor(uma()).uma_volta().await.unwrap();
	let cv = cena.cv();
	let e = email_de(&cv, &id);
	assert_eq!(e["subject"], "Seu código de verificação ••••••");
	assert_eq!(
		e["text"],
		"Use o código •••••• para entrar.\n\nRedefinir senha <[link de acesso removido]>\n\nSeu pedido 2000012345678901 segue."
	);
	assert_eq!(e["tuta"]["codes_masked"], true);
	assert_eq!(e["tuta"]["links_removed"], 1);
	let tudo = serde_json::to_string(&cv.recebidos).unwrap();
	assert!(!tudo.contains("482913") && !tudo.contains("token=abcdef"), "o código e o link nunca saem do Mac");
}

#[tokio::test]
async fn aliases_que_so_se_contam_nao_sobem() {
	let (mut c, _) = caixa_com_pastas();
	c.aliases.push(("adm@poofy.com.br".into(), true));
	let entrada = c.entrada.clone();
	let mut interno = NovoEmail::simples(&entrada, "extrato do banco", AGORA - 10 * MIN);
	interno.para = vec![("Adm".into(), "adm@poofy.com.br".into())];
	interno.cabecalhos = "Delivered-To: adm@poofy.com.br\r\n".into();
	let id_interno = c.email(interno);
	let mut com_loja = NovoEmail::simples(&entrada, "para os dois", AGORA - 9 * MIN);
	com_loja.para = vec![("Adm".into(), "adm@poofy.com.br".into()), ("Loja".into(), "21max@tuta.com".into())];
	let id_loja = c.email(com_loja);
	let mut cv = Central::nova();
	cv.aliases_so_contar = vec!["adm@poofy.com.br".into()];
	let cena = Cena::nova("so-contar-aliases", c, cv);
	let mut leitor = cena.leitor(uma());
	// O /v2/sync vem antes da leitura: a lista já vale na 1ª volta.
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.so_contados, 1, "{r}");
	assert!(!cena.cv().emails.contains_key(&sid(&id_interno)));
	assert!(cena.cv().emails.contains_key(&sid(&id_loja)), "com uma loja junto, sobe");
	assert!(cena.estado().so_contados.contains(&chave(&id_interno)));
	// A contagem não pede de novo o que de propósito não subiu.
	cena.avancar(2 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!((r.faltando, r.entregues), (0, 0), "{r}");
	assert!(cena.cv().contagens.iter().all(|x| !x["ids"].as_array().unwrap().contains(&json!(sid(&id_interno)))));
	assert!(cena.cv().recebidos.iter().all(|e| e["subject"] != "extrato do banco"));
}

#[tokio::test]
async fn paginacao_e_cursor_sem_repetir() {
	let (mut c, p) = caixa_com_pastas();
	for i in 0..250u64 {
		c.email(NovoEmail::simples(&p.problema_ml, &format!("pedido {i:03}"), AGORA - 300 * MIN + i * MIN));
	}
	let cena = Cena::nova("paginacao", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 250, "{r}");
	assert_eq!(cena.cv().emails.len(), 250);
	assert!(cena.cv().lotes.iter().all(|n| *n <= 20), "lotes de até 20: {:?}", cena.cv().lotes);
	// Segunda volta: nada novo, nenhum e-mail carregado de novo.
	let mails_antes = cena.pedidos_tuta("/rest/tutanota/Mail/");
	let lotes_antes = cena.cv().quantos_lotes();
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.emails_novos, 0, "{r}");
	assert_eq!(cena.pedidos_tuta("/rest/tutanota/Mail/"), mails_antes);
	assert_eq!(cena.cv().quantos_lotes(), lotes_antes);
	// Chegam 3: só eles.
	{
		let mut cx = cena.cx();
		for i in 0..3 {
			cx.email(NovoEmail::simples(&p.problema_ml, &format!("novo {i}"), AGORA + i * MIN));
		}
	}
	cena.avancar(5 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!((r.emails_novos, r.entregues), (3, 3), "{r}");
	assert_eq!(cena.cv().emails.len(), 253);
	assert_eq!(cena.cv().recebidos.len(), 253, "nenhum e-mail mandado duas vezes");
}

#[tokio::test]
async fn mesmo_email_para_dois_aliases_sao_dois_na_central() {
	let (mut c, p) = caixa_com_pastas();
	let para_21 = NovoEmail::simples(&p.problema_ml, "dois aliases", AGORA - 10 * MIN);
	let mut para_22 = para_21.clone();
	para_22.pasta = p.reclamacao_ml.clone();
	para_22.para = vec![("Loja 22".into(), "22max@tuta.com".into())];
	let a = c.email(para_21);
	let b = c.email(para_22);
	let cena = Cena::nova("aliases", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 2, "{r}");
	cena.avancar(2 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!((r.entregues, r.faltando), (0, 0), "{r}");
	let cv = cena.cv();
	// Cada Mail do Tuta é um e-mail na Central (a ponte junta pelo Message-ID).
	assert_eq!(email_de(&cv, &a)["delivered_to"], json!(["21max@tuta.com"]));
	assert_eq!(email_de(&cv, &b)["delivered_to"], json!(["22max@tuta.com"]));
	assert_eq!(email_de(&cv, &a)["message_id"], email_de(&cv, &b)["message_id"]);
}

#[tokio::test]
async fn movido_e_apagado_vistos_pela_contagem() {
	let (mut c, p) = caixa_com_pastas();
	let movido = c.email(NovoEmail::simples(&p.problema_ml, "vai mudar de pasta", AGORA - 60 * MIN));
	let apagado = c.email(NovoEmail::simples(&p.problema_ml, "vai para a lixeira", AGORA - 50 * MIN));
	let some = c.email(NovoEmail::simples(&p.problema_ml, "vai sumir de vez", AGORA - 40 * MIN));
	let financeiro = c.email(NovoEmail::simples(&p.problema_ml, "vai para o financeiro", AGORA - 35 * MIN));
	// A pasta de destino já tem um e-mail MAIS NOVO (o cursor dela passa da data do movido).
	c.email(NovoEmail::simples(&p.reclamacao_ml, "já estava lá", AGORA - 5 * MIN));
	let cena = Cena::nova("movido", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	leitor.uma_volta().await.unwrap();
	assert_eq!(cena.cv().emails.len(), 5);
	{
		let mut cx = cena.cx();
		let destino = p.reclamacao_ml.clone();
		let lixeira = cx.lixeira.clone();
		cx.mover(&movido, &destino);
		cx.mover(&apagado, &lixeira);
		cx.apagar_de_vez(&some);
		cx.mover(&financeiro, &p.financeiro);
	}
	cena.avancar(3 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert!(r.movidos >= 2, "{r}");
	assert_eq!(r.apagados, 1, "o que sumiu de vez: {r}");
	let lixeira = cena.cx().lixeira.clone();
	let cv = cena.cv();
	assert_eq!(cv.local[&sid(&movido)], (p.reclamacao_ml.as_str().to_owned(), false));
	assert_eq!(cv.local[&sid(&apagado)], (lixeira.as_str().to_owned(), true), "Lixeira = apagado");
	assert!(cv.local[&sid(&some)].1, "sumiu de vez: apagado");
	assert_eq!(cv.local[&sid(&financeiro)].0, p.financeiro.as_str(), "movido para pasta só contada");
	assert_eq!(cv.recebidos.iter().filter(|e| e["subject"] == "vai mudar de pasta").count(), 1, "movido NÃO é mandado de novo");
	assert!(!cv.mudancas.is_empty());
	assert!(cv.mudancas.iter().flat_map(|m| m["changes"].as_array().cloned().unwrap_or_default()).any(|m| m["deleted"] == true));
}

#[tokio::test]
async fn enviado_pelo_tuta_vai_com_o_fio() {
	let (mut c, p) = caixa_com_pastas();
	let original = c.email(NovoEmail::simples(&p.problema_ml, "quando chega?", AGORA - 30 * MIN));
	let mid_original = format!("<{}@exemplo.test>", "quando-chega?");
	let enviados = c.enviados.clone();
	let mut resposta = NovoEmail::simples(&enviados, "Re: quando chega?", AGORA - 10 * MIN);
	resposta.de = ("Loja".into(), "21max@tuta.com".into());
	resposta.para = vec![("Cliente".into(), "cliente@exemplo.com.br".into())];
	resposta.estado = 1;
	resposta.cabecalhos = String::new();
	resposta.anterior = Some(original.clone());
	let id_resposta = c.email(resposta);
	let cena = Cena::nova("enviado", c, Central::nova());
	cena.leitor(uma()).uma_volta().await.unwrap();
	let cv = cena.cv();
	let e = email_de(&cv, &id_resposta);
	assert_eq!(e["direction"], "sent");
	assert_eq!(e["in_reply_to"], mid_original, "o fio do Tuta (ConversationEntry.previous)");
	assert_eq!(e["from_address"], "21max@tuta.com");
	assert_eq!(e["tuta"]["state"], 1);
}

#[tokio::test]
async fn email_aead_ilegivel_no_meio_nao_para_a_volta() {
	let (mut c, p) = caixa_com_pastas();
	let mut ids = vec![];
	for i in 0..5u64 {
		ids.push(c.email(NovoEmail::simples(&p.problema_ml, &format!("e-mail {i}"), AGORA - 50 * MIN + i * MIN)));
	}
	c.estragar_email(&ids[2]);
	let cena = Cena::nova("aead", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!((r.entregues, r.ilegiveis), (4, 1), "{r}");
	assert!(!cena.cv().emails.contains_key(&sid(&ids[2])));
	let estado = cena.estado();
	assert_eq!(estado.ilegiveis.len(), 1);
	assert!(estado.ilegiveis[&chave(&ids[2])].motivo.starts_with("mail:"));
	// O cursor PASSOU do ilegível (o resto não espera por ele).
	let cursor = estado.cursores[p.problema_ml.as_str()].ultimo_entregue.clone().unwrap();
	assert!(tuta_conector::leitura::entrada_id::recebido_ms(&cursor).unwrap() >= AGORA - 46 * MIN - 1024);
	// O sinal diz "ilegivel"; o contador vai no /v2/sync seguinte.
	assert_eq!(leitor.estado_conector, EstadoConector::Ilegivel);
	assert_eq!(cena.ultimo_sinal()["error_code"], "ilegivel");
	assert_eq!(cena.ultimo_sinal()["state"], "online");
	cena.avancar(10 * MIN);
	let mails = cena.pedidos_tuta(&format!("/Mail/{}/{}", ids[2].list_id.as_str(), ids[2].element_id.as_str()));
	leitor.uma_volta().await.unwrap();
	assert_eq!(cena.ultimo_sync()["counters"]["ilegiveis"], 1);
	assert_eq!(cena.pedidos_tuta(&format!("/Mail/{}/{}", ids[2].list_id.as_str(), ids[2].element_id.as_str())), mails, "antes de 1 h não tenta de novo");
	// O Tuta passa a decifrar e venceu a espera: entra.
	cena.cx().consertar_email(&ids[2]);
	cena.avancar(61 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert!(r.faltando + r.tentados_de_novo >= 1, "{r}");
	assert!(cena.cv().emails.contains_key(&sid(&ids[2])));
	assert!(cena.estado().ilegiveis.is_empty());
	assert_eq!(leitor.estado_conector, EstadoConector::Ok);
}

#[tokio::test]
async fn pasta_ilegivel_e_panico_do_sdk_isolados() {
	let (mut c, p) = caixa_com_pastas();
	let bom = c.email(NovoEmail::simples(&p.problema_ml, "chega normal", AGORA - 10 * MIN));
	let na_quebrada = c.email(NovoEmail::simples(&p.reclamacao_ml, "pasta com lixo", AGORA - 9 * MIN));
	let na_estragada = c.email(NovoEmail::simples(&p.vendas_shopee, "pasta que não decifra", AGORA - 8 * MIN));
	let mail_com_lixo = c.email(NovoEmail::simples(&p.problema_ml, "mail com lixo", AGORA - 7 * MIN));
	let depois = c.email(NovoEmail::simples(&p.problema_ml, "depois do lixo", AGORA - 6 * MIN));
	c.pastas_com_lixo.push(p.reclamacao_ml.clone());
	c.pastas_estragadas.push(p.vendas_shopee.clone());
	let caminho = format!("/rest/tutanota/Mail/{}/{}", mail_com_lixo.list_id.as_str(), mail_com_lixo.element_id.as_str());
	c.falhas.push(Falha::status(&caminho, 200).com_corpo(b"<<isto nao e json>>"));
	let cena = Cena::nova("panico", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await.expect("o processo NÃO cai");
	assert_eq!(r.pastas_ilegiveis, 2, "uma não decifra e outra entra em pânico: {r}");
	assert_eq!(r.ilegiveis, 1, "{r}");
	{
		let cv = cena.cv();
		assert!(cv.emails.contains_key(&sid(&bom)));
		assert!(cv.emails.contains_key(&sid(&depois)), "o e-mail depois do pânico entra");
		assert!(!cv.emails.contains_key(&sid(&na_quebrada)));
		assert!(!cv.emails.contains_key(&sid(&na_estragada)));
		assert_eq!(cv.syncs[0]["folders"].as_array().unwrap().len(), 9, "a pasta que não decifra não vai no /v2/sync");
		assert_eq!(cv.syncs[0]["folders_complete"], false, "e a Central não a dá por sumida");
	}
	assert_eq!(leitor.estado_conector, EstadoConector::Ilegivel);
	let sessoes = cena.pedidos_tuta("/rest/sys/Session/");
	cena.cx().pastas_com_lixo.clear();
	cena.avancar(2 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(cena.ultimo_sync()["counters"]["pastas_ilegiveis"], 2);
	assert_eq!(cena.pedidos_tuta("/rest/sys/Session/"), sessoes + 1, "depois do pânico a sessão é aberta de novo");
	assert!(cena.cv().emails.contains_key(&sid(&na_quebrada)), "{r}");
	for linha in registro::capturado() {
		assert!(!linha.contains("isto nao e json"), "o log não ecoa a resposta: {linha}");
	}
	cena.conferir_so_leitura();
}

#[tokio::test]
async fn sessao_caiu_401_para_e_nao_insiste() {
	let (mut c, p) = caixa_com_pastas();
	c.email(NovoEmail::simples(&p.problema_ml, "antes", AGORA - 10 * MIN));
	let cena = Cena::nova("401", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	leitor.uma_volta().await.unwrap();
	cena.cx().falhas.push(Falha::status("/rest/", 401));
	cena.avancar(2 * MIN);
	assert_eq!(leitor.uma_volta().await.unwrap_err(), Parada::Tuta(FalhaTuta::SessaoCaiu));
	assert_eq!(leitor.estado_conector, EstadoConector::SessaoCaiu);
	assert_eq!(cena.ultimo_sinal(), json!({"state": "login_required", "can_send": false, "error_code": "sessao_caiu"}));
	let antes = cena.tuta.pedidos().len();
	cena.avancar(5 * MIN);
	assert_eq!(leitor.uma_volta().await.unwrap_err(), Parada::Tuta(FalhaTuta::SessaoCaiu));
	assert_eq!(cena.tuta.pedidos().len(), antes, "nenhum pedido ao Tuta com a sessão morta");
	{
		let mut cx = cena.cx();
		cx.falhas.clear();
		cx.access_token = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB".into();
		cx.email(NovoEmail::simples(&p.problema_ml, "depois", AGORA + 6 * MIN));
		guardar_sessao(&cena.cofre, &cx);
	}
	cena.avancar(5 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 1, "{r}");
	assert_eq!(leitor.estado_conector, EstadoConector::Ok);
}

#[tokio::test]
async fn limitado_429_recua_e_volta() {
	let (mut c, p) = caixa_com_pastas();
	c.email(NovoEmail::simples(&p.problema_ml, "um", AGORA - 10 * MIN));
	c.falhas.push(Falha::status("/rest/tutanota/MailSetEntry/", 429).com_cabecalho("Retry-After", "30").uma_vez());
	let cena = Cena::nova("429", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await;
	assert!(matches!(r, Err(Parada::Tuta(FalhaTuta::Limitado(_)))), "{r:?}");
	assert_eq!(leitor.estado_conector, EstadoConector::Limitado);
	assert_eq!(cena.ultimo_sinal()["error_code"], "limitado");
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 1);
	assert_eq!(cena.cv().syncs.last().unwrap()["counters"]["erros_429"], 1);
	assert_eq!(leitor.estado_conector, EstadoConector::Ok);
}

#[tokio::test]
async fn versao_recusada_474_para_de_vez() {
	let (mut c, p) = caixa_com_pastas();
	c.email(NovoEmail::simples(&p.problema_ml, "um", AGORA - 10 * MIN));
	c.falhas.push(Falha::status("/rest/tutanota/MailSetEntry/", 474));
	let cena = Cena::nova("474", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	assert_eq!(leitor.uma_volta().await.unwrap_err(), Parada::Tuta(FalhaTuta::VersaoRecusada));
	assert_eq!(cena.ultimo_sinal(), json!({"state": "error", "can_send": false, "error_code": "versao_recusada"}));
	let antes = cena.tuta.pedidos().len();
	cena.avancar(10 * MIN);
	assert_eq!(leitor.uma_volta().await.unwrap_err(), Parada::Tuta(FalhaTuta::VersaoRecusada));
	assert_eq!(cena.tuta.pedidos().len(), antes, "com 474 o conector não insiste no Tuta");
	cena.cx().falhas.clear();
	cena.avancar(6 * 60 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 1, "{r}");
	assert_eq!(leitor.estado_conector, EstadoConector::Ok);
}

#[tokio::test]
async fn canario_474_e_atrasado() {
	let (c, _) = caixa_com_pastas();
	let cena = Cena::nova("canario474", c, Central::nova());
	cena.cx().falhas.push(Falha::status("/rest/base/applicationtypesservice", 474).uma_vez());
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await;
	assert!(matches!(r, Err(Parada::Tuta(FalhaTuta::VersaoRecusada))), "{r:?}");
	assert_eq!(cena.estado().canario.unwrap().versao, "recusada");

	let (c, _) = caixa_com_pastas();
	let mut cena = Cena::nova("atrasado", c, Central::nova());
	cena.github = releases(&["361.261006.0", "362.261013.0", "362.261013.1", tutasdk::CLIENT_VERSION, "360.260921.0"]);
	let mut leitor = cena.leitor(uma());
	leitor.uma_volta().await.unwrap();
	assert_eq!(leitor.estado_conector, EstadoConector::Atrasado);
	assert_eq!(cena.ultimo_sinal()["error_code"], "atrasado");
	let antes = cena.github.pedidos().len();
	cena.avancar(60 * MIN);
	leitor.uma_volta().await.unwrap();
	assert_eq!(cena.ultimo_sync()["counters"]["releases_atras"], 3);
	assert_eq!(cena.github.pedidos().len(), antes, "o canário roda 1x por dia");
	cena.avancar(24 * 60 * MIN);
	leitor.uma_volta().await.unwrap();
	assert_eq!(cena.github.pedidos().len(), antes + 1);
}

#[tokio::test]
async fn tuta_fora_5xx_so_acende_depois_de_3_e_nada_se_perde() {
	let (mut c, p) = caixa_com_pastas();
	let id = c.email(NovoEmail::simples(&p.problema_ml, "esperando o Tuta", AGORA - 10 * MIN));
	c.falhas.push(Falha::status("/rest/tutanota/MailSet/", 503).vezes(3));
	let cena = Cena::nova("5xx", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	for i in 1..=3 {
		assert_eq!(leitor.uma_volta().await.unwrap_err(), Parada::Tuta(FalhaTuta::Fora));
		let esperado = if i < 3 { EstadoConector::Iniciando } else { EstadoConector::TutaFora };
		assert_eq!(leitor.estado_conector, esperado, "volta {i}");
	}
	assert_eq!(cena.ultimo_sinal()["error_code"], "tuta_fora");
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 1);
	assert!(cena.cv().emails.contains_key(&sid(&id)));
	assert_eq!(leitor.estado_conector, EstadoConector::Ok);
}

#[tokio::test]
async fn central_413_parte_o_lote() {
	let (mut c, p) = caixa_com_pastas();
	for i in 0..10u64 {
		c.email(NovoEmail::simples(&p.problema_ml, &format!("lote {i}"), AGORA - 20 * MIN + i * MIN));
	}
	let mut cv = Central::nova();
	cv.teto_lote = Some(3);
	let cena = Cena::nova("413", c, cv);
	let r = cena.leitor(uma()).uma_volta().await.unwrap();
	assert_eq!(r.entregues, 10, "{r}");
	let cv = cena.cv();
	assert_eq!(cv.emails.len(), 10);
	assert!(cv.lotes.contains(&10) && cv.lotes.iter().any(|n| *n <= 3), "{:?}", cv.lotes);
}

#[tokio::test]
async fn central_fora_nada_se_perde() {
	let (mut c, p) = caixa_com_pastas();
	for i in 0..4u64 {
		c.email(NovoEmail::simples(&p.problema_ml, &format!("x {i}"), AGORA - 20 * MIN + i * MIN));
	}
	let mut cv = Central::nova();
	cv.falhas.push_back(("/v2/ingest", 502));
	let cena = Cena::nova("centralfora", c, cv);
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await;
	assert!(matches!(r, Err(Parada::DavinciFora(_))), "{r:?}");
	assert!(
		cena.estado().cursores.get(p.problema_ml.as_str()).is_none_or(|c| c.ultimo_entregue.is_none()),
		"nada entregue: o cursor não anda"
	);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 4, "{r}");
	assert_eq!(cena.cv().emails.len(), 4);
}

#[tokio::test]
async fn lote_aceito_em_parte() {
	let (mut c, p) = caixa_com_pastas();
	let mut ids = vec![];
	for i in 0..6u64 {
		ids.push(c.email(NovoEmail::simples(&p.problema_ml, &format!("parte {i}"), AGORA - 30 * MIN + i * MIN)));
	}
	let mut cv = Central::nova();
	cv.roteiro.insert(sid(&ids[1]), ["sumir"].into());
	cv.roteiro.insert(sid(&ids[3]), ["rejeitar"].into());
	let cena = Cena::nova("parte", c, cv);
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!((r.entregues, r.recusados), (4, 1), "{r}");
	let estado = cena.estado();
	let cursor = &estado.cursores[p.problema_ml.as_str()];
	assert_eq!(cursor.entregues_acima.len(), 4, "2 (recusado registrado), 3, 4, 5");
	assert_eq!(estado.recusados[&chave(&ids[3])].motivo, "invalid_message");
	let antes = cena.pedidos_tuta("/rest/tutanota/Mail/");
	cena.avancar(2 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 1, "{r}");
	assert_eq!(cena.pedidos_tuta("/rest/tutanota/Mail/"), antes + 1);
	assert!(cena.estado().cursores[p.problema_ml.as_str()].entregues_acima.is_empty());
	assert!(cena.cv().emails.contains_key(&sid(&ids[1])));
	// O recusado NÃO volta antes de 24 h (nem pela contagem).
	assert!(!cena.cv().emails.contains_key(&sid(&ids[3])));
	assert_eq!(cena.cv().recebidos.iter().filter(|e| e["subject"] == "parte 3").count(), 1);
}

#[tokio::test]
async fn reinicio_no_meio_do_lote_nao_perde_nem_duplica() {
	let (mut c, p) = caixa_com_pastas();
	let mut ids = vec![];
	for i in 0..8u64 {
		let mut e = NovoEmail::simples(&p.problema_ml, &format!("reinicio {i}"), AGORA - 30 * MIN + i * MIN);
		if i == 2 {
			e.anexos = vec![Anexo { nome: "r.pdf".into(), tipo: "application/pdf".into(), bytes: vec![4; 20], cid: None }];
		}
		ids.push(c.email(e));
	}
	let mut cv = Central::nova();
	// A Central GRAVA o lote e cai antes de responder (o conector não sabe que entrou).
	cv.processa_e_cai = 1;
	let cena = Cena::nova("reinicio", c, cv);
	{
		let mut leitor = cena.leitor(uma());
		let r = leitor.uma_volta().await;
		assert!(matches!(r, Err(Parada::DavinciFora(_))), "{r:?}");
	}
	assert_eq!(cena.cv().emails.len(), 8, "a Central gravou");
	// Processo novo, do zero, só com o estado do disco.
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 8, "mandados de novo: a Central responde repetido: {r}");
	let cv = cena.cv();
	assert_eq!(cv.emails.len(), 8, "nenhum duplicado na Central");
	for id in &ids {
		assert!(cv.emails.contains_key(&sid(id)), "nenhum perdido");
	}
	// O anexo foi junto com o e-mail (nunca depois).
	assert_eq!(email_de(&cv, &ids[2])["attachments"].as_array().unwrap().len(), 1);
	drop(cv);
	let estado = cena.estado();
	let cursor = estado.cursores[p.problema_ml.as_str()].ultimo_entregue.clone().unwrap();
	assert!(tuta_conector::leitura::entrada_id::recebido_ms(&cursor).unwrap() >= AGORA - 23 * MIN - 1024);
	cena.conferir_so_leitura();
}

#[tokio::test]
async fn faltando_da_contagem_vai_de_novo() {
	let (mut c, p) = caixa_com_pastas();
	let a = c.email(NovoEmail::simples(&p.problema_ml, "some da central", AGORA - 10 * MIN));
	let cena = Cena::nova("faltando", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	leitor.uma_volta().await.unwrap();
	cena.cv().emails.remove(&sid(&a));
	cena.cv().local.remove(&sid(&a));
	cena.avancar(2 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.faltando, 1, "{r}");
	assert!(cena.cv().emails.contains_key(&sid(&a)));
}

#[tokio::test]
async fn pasta_que_a_central_nao_conhece_espera_e_sincroniza_de_novo() {
	let (mut c, p) = caixa_com_pastas();
	c.email(NovoEmail::simples(&p.problema_ml, "primeiro", AGORA - 10 * MIN));
	let cena = Cena::nova("pasta-desconhecida", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	leitor.uma_volta().await.unwrap();
	// A Central não conhece a pasta (ex.: o servidor perdeu a lista): o e-mail
	// volta `folder_unknown`, espera, e a lista de pastas vai de novo.
	let novo = cena.cx().email(NovoEmail::simples(&p.problema_ml, "depois", AGORA + MIN));
	cena.cv().pasta_desconhecida = 1;
	cena.avancar(2 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 0, "{r}");
	assert!(!cena.cv().emails.contains_key(&sid(&novo)));
	let syncs_antes = cena.cv().syncs.len();
	cena.avancar(2 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 1, "{r}");
	let cv = cena.cv();
	assert!(cv.syncs[syncs_antes]["folders"].is_array(), "a lista de pastas foi de novo");
	assert!(cv.emails.contains_key(&sid(&novo)));
}

#[tokio::test]
async fn regra_do_tuta_espera_e_depois_entra() {
	let (mut c, _) = caixa_com_pastas();
	let entrada = c.entrada.clone();
	let mut e = NovoEmail::simples(&entrada, "regra ainda não rodou", AGORA - 2 * MIN);
	e.processar = true;
	let id = c.email(e);
	let depois = c.email(NovoEmail::simples(&entrada, "outro na entrada", AGORA - MIN));
	let cena = Cena::nova("regra", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.esperando_regra, 1, "{r}");
	assert!(!cena.cv().emails.contains_key(&sid(&id)));
	assert!(cena.cv().emails.contains_key(&sid(&depois)), "o de depois não espera");
	cena.avancar(16 * MIN);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.esperando_regra, 0, "{r}");
	assert!(cena.cv().emails.contains_key(&sid(&id)));
	assert!(cena.estado().cursores[entrada.as_str()].entregues_acima.is_empty());
}

#[tokio::test]
async fn anexos_grande_e_perigoso_ficam_no_rastro_e_queda_segura_o_email() {
	let (mut c, p) = caixa_com_pastas();
	let mut e = NovoEmail::simples(&p.problema_ml, "com anexos", AGORA - 10 * MIN);
	e.anexos = vec![
		Anexo { nome: "grande.pdf".into(), tipo: "application/pdf".into(), bytes: vec![1; 10 * 1024 * 1024 + 1], cid: None },
		Anexo { nome: "pagina.html".into(), tipo: "text/html".into(), bytes: vec![3; 10], cid: None },
		Anexo { nome: "pequeno.pdf".into(), tipo: "application/pdf".into(), bytes: vec![2; 500], cid: None },
	];
	let id = c.email(e);
	// O servidor de arquivos do Tuta cai na primeira vez: o e-mail espera (nunca vai sem o anexo).
	c.falhas.push(Falha::status("/rest/storage/blobservice", 503).uma_vez());
	let cena = Cena::nova("anexos", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await;
	assert!(matches!(r, Err(Parada::Tuta(FalhaTuta::Fora))), "{r:?}");
	assert!(!cena.cv().emails.contains_key(&sid(&id)));
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 1, "{r}");
	let cv = cena.cv();
	let e = email_de(&cv, &id);
	let anexos = e["attachments"].as_array().unwrap();
	assert_eq!(anexos.len(), 1);
	assert_eq!(BASE64.decode(anexos[0]["data_base64"].as_str().unwrap()).unwrap(), vec![2u8; 500]);
	let motivos: Vec<&str> = e["omitted_attachments"].as_array().unwrap().iter().map(|a| a["reason"].as_str().unwrap()).collect();
	assert_eq!(motivos, vec!["grande_demais", "perigoso"]);
	drop(cv);
	// O grande nem foi baixado (o tamanho é conferido antes).
	assert!(cena.tuta.pedidos().iter().filter(|p| p.caminho.contains("blobservice")).count() <= 2);
	cena.conferir_so_leitura();
}

#[tokio::test]
async fn modo_contar_so_manda_ids() {
	let (mut c, p) = caixa_com_pastas();
	c.email(NovoEmail::simples(&p.problema_ml, "não vai", AGORA - 10 * MIN));
	let cena = Cena::nova("contar", c, Central::nova());
	let opcoes = OpcoesLeitura {
		uma_volta: true,
		contar: true,
		seco: false,
	};
	let r = cena.leitor(opcoes).uma_volta().await.unwrap();
	assert_eq!(r.entregues, 0);
	let cv = cena.cv();
	assert_eq!(cv.quantos_lotes(), 0, "nenhum /v2/ingest");
	let contagem = cv.contagens.iter().find(|x| x["folder_key"] == p.problema_ml.as_str()).expect("contagem da pasta");
	assert_eq!(contagem["ids"].as_array().unwrap().len(), 1);
	assert_eq!(contagem["complete"], true);
	assert!(contagem["since"].as_str().is_some());
	drop(cv);
	assert_eq!(cena.pedidos_tuta("/rest/tutanota/Mail/"), 0, "nenhum e-mail aberto na contagem");
}

#[tokio::test]
async fn seco_le_e_nao_manda_nada() {
	let (mut c, p) = caixa_com_pastas();
	for i in 0..5u64 {
		c.email(NovoEmail::simples(&p.problema_ml, &format!("seco {i}"), AGORA - 10 * MIN + i * MIN));
	}
	let cena = Cena::nova("seco", c, Central::nova());
	let opcoes = OpcoesLeitura {
		uma_volta: true,
		contar: false,
		seco: true,
	};
	let r = cena.leitor(opcoes).rodar().await.unwrap();
	assert_eq!(r.emails_novos, 3, "os 3 mais novos da pasta: {r}");
	assert_eq!(r.entradas_no_topo, 5);
	assert!(cena.central_srv.pedidos().is_empty(), "nada ao DaVinci");
	assert!(!cena.pasta.join("estado.json").exists(), "nada em disco");
}

#[tokio::test]
async fn outro_agente_servidor_sem_v2_e_chave_recusada_param() {
	let (mut c, p) = caixa_com_pastas();
	c.email(NovoEmail::simples(&p.problema_ml, "não entra", AGORA - 10 * MIN));
	let mut cv = Central::nova();
	cv.outro_agente = true;
	let cena = Cena::nova("duplicado", c, cv);
	assert_eq!(cena.leitor(uma()).uma_volta().await.unwrap_err(), Parada::Duplicado);
	assert_eq!(cena.cv().quantos_lotes(), 0, "outro agente: nada é entregue");

	let (mut c, p) = caixa_com_pastas();
	c.email(NovoEmail::simples(&p.problema_ml, "não entra", AGORA - 10 * MIN));
	let mut cv = Central::nova();
	cv.sem_v2 = true;
	let cena = Cena::nova("semv2", c, cv);
	let mut leitor = cena.leitor(uma());
	assert_eq!(leitor.uma_volta().await.unwrap_err(), Parada::SemV2);
	assert_eq!(cena.ultimo_sinal(), json!({"state": "error", "can_send": false, "error_code": "sem_v2"}));
	assert_eq!(cena.cv().quantos_lotes(), 0, "sem o v2 nada vai pelo v1 (perderia os ids do Tuta)");

	let (c, _) = caixa_com_pastas();
	let mut cv = Central::nova();
	cv.falhas.push_back(("/heartbeat", 401));
	let cena = Cena::nova("chave", c, cv);
	let r = cena.leitor(uma()).uma_volta().await;
	assert!(matches!(r, Err(Parada::DavinciRecusou(_))), "{r:?}");
	assert_eq!(cena.pedidos_tuta("/rest/"), 0, "chave recusada: nem abre o Tuta");
}

#[tokio::test]
async fn sem_sessao_no_chaveiro() {
	let (c, _) = caixa_com_pastas();
	let cena = Cena::nova("semsessao", c, Central::nova());
	chaveiro::apagar_sessao(&*cena.cofre, Conta::Geral).unwrap();
	let mut leitor = cena.leitor(uma());
	assert_eq!(leitor.uma_volta().await.unwrap_err(), Parada::SemSessao);
	assert_eq!(cena.ultimo_sinal(), json!({"state": "login_required", "can_send": false, "error_code": "sessao_caiu"}));
}

#[tokio::test]
async fn varredura_funda_e_fechamento_do_dia() {
	let (mut c, p) = caixa_com_pastas();
	let antigo = c.email(NovoEmail::simples(&p.problema_ml, "antigo", AGORA - 20 * 24 * 60 * MIN));
	let ontem = c.email(NovoEmail::simples(&p.problema_ml, "de ontem", AGORA - 12 * 60 * MIN));
	for i in 0..120u64 {
		c.email(NovoEmail::simples(&p.problema_ml, &format!("recente {i}"), AGORA - 200 * MIN + i * MIN));
	}
	// De 3 dias atrás, numa pasta só contada; depois alguém move para a lida.
	let mudou = c.email(NovoEmail::simples(&p.financeiro, "movido de pasta antiga", AGORA - 3 * 24 * 60 * MIN));
	let cena = Cena::nova("varredura", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	leitor.uma_volta().await.unwrap();
	assert!(!cena.cv().emails.contains_key(&sid(&mudou)), "da pasta só contada: o corpo não sobe");
	assert!(!cena.cv().emails.contains_key(&sid(&antigo)), "fora da cobertura");
	let destino = p.problema_ml.clone();
	cena.cx().mover(&mudou, &destino);
	for _ in 0..12 {
		cena.avancar(2 * MIN);
		leitor.uma_volta().await.unwrap();
	}
	{
		let cv = cena.cv();
		let e = email_de(&cv, &mudou);
		assert_eq!(e["subject"], "movido de pasta antiga", "a varredura funda achou e mandou");
		assert!(cv.emails.contains_key(&sid(&ontem)));
		assert!(!cv.emails.contains_key(&sid(&antigo)), "fora da cobertura, nem na varredura");
		let varridas: Vec<&Value> = cv
			.contagens
			.iter()
			.filter(|c| c["complete"] == true && c["ids"].as_array().is_some_and(|i| i.len() > 100))
			.collect();
		assert_eq!(varridas.len(), 1, "a varredura de problema ml (122 ids na cobertura)");
		assert!(cv.contagens.iter().all(|c| c["day"].is_null()));
	}
	// Fechamento do dia: só depois das 06:00 de Brasília (09:00 UTC).
	cena.avancar(3 * 60 * MIN);
	leitor.uma_volta().await.unwrap();
	let dias: Vec<Value> = cena.cv().contagens.iter().filter(|c| !c["day"].is_null()).cloned().collect();
	assert_eq!(dias.len(), 1, "uma pasta por volta");
	assert_eq!(dias[0]["day"], "2026-10-07");
	assert_eq!(dias[0]["complete"], true);
	for _ in 0..12 {
		cena.avancar(2 * MIN);
		leitor.uma_volta().await.unwrap();
	}
	let dias: Vec<Value> = cena.cv().contagens.iter().filter(|c| !c["day"].is_null()).cloned().collect();
	assert_eq!(dias.len(), 10, "as 10 pastas, uma vez cada");
	let problema = dias.iter().find(|c| c["folder_key"] == p.problema_ml.as_str()).unwrap();
	assert!(problema["ids"].as_array().unwrap().contains(&json!(sid(&ontem))));
	assert_eq!(problema["since"], "2026-10-07T03:00:00Z");
	assert_eq!(problema["until"], "2026-10-08T03:00:00Z");
	assert_eq!(problema["total"], problema["ids"].as_array().unwrap().len());
}

#[tokio::test]
async fn erro_passageiro_repetido_nao_segura_a_pasta() {
	let (mut c, p) = caixa_com_pastas();
	let preso = c.email(NovoEmail::simples(&p.problema_ml, "sempre passageiro", AGORA - 10 * MIN));
	let depois = c.email(NovoEmail::simples(&p.problema_ml, "atrás dele", AGORA - 9 * MIN));
	let mut cv = Central::nova();
	cv.roteiro.insert(sid(&preso), std::iter::repeat_n("sumir", 20).collect());
	let cena = Cena::nova("passageiro", c, cv);
	let mut leitor = cena.leitor(uma());
	for volta in 1..=10 {
		leitor.uma_volta().await.unwrap();
		let estado = cena.estado();
		let cursor = &estado.cursores[p.problema_ml.as_str()];
		if volta < 10 {
			assert!(
				cursor
					.ultimo_entregue
					.as_deref()
					.is_none_or(|c| tuta_conector::leitura::entrada_id::recebido_ms(c).unwrap() < AGORA - 10 * MIN - 1024),
				"volta {volta}: o cursor espera"
			);
			assert_eq!(cursor.entregues_acima.len(), 1);
		}
		cena.avancar(2 * MIN);
	}
	let estado = cena.estado();
	assert!(estado.recusados.get(&chave(&preso)).is_some_and(|r| r.motivo == "passageiro_repetido"));
	assert!(estado.cursores[p.problema_ml.as_str()].entregues_acima.is_empty(), "o cursor passou");
	assert!(cena.cv().emails.contains_key(&sid(&depois)));
}

#[tokio::test]
async fn volta_longa_continua_pulsando() {
	let (mut c, p) = caixa_com_pastas();
	for i in 0..120u64 {
		c.email(NovoEmail::simples(&p.problema_ml, &format!("carga {i}"), AGORA - 200 * MIN + i * MIN));
	}
	let mut cv = Central::nova();
	cv.demora_por_lote_ms = 70_000;
	let cena = Cena::nova("pulsando", c, cv);
	let mut leitor = cena.leitor(uma());
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!(r.entregues, 120);
	assert_eq!(r.lotes, 6);
	// 6 lotes x 70 s = 7 min de volta: o sinal foi a cada ~60 s no meio dela.
	let sinais = cena.cv().sinais.len();
	assert!(sinais >= 6, "{sinais} sinais");
}

/// As chaves (caminhos) de um JSON, para comparar a FORMA do contrato.
fn forma(v: &Value, prefixo: &str, saida: &mut std::collections::BTreeSet<String>) {
	match v {
		Value::Object(o) => {
			for (k, x) in o {
				let caminho = format!("{prefixo}.{k}");
				saida.insert(caminho.clone());
				forma(x, &caminho, saida);
			}
		},
		Value::Array(a) => a.iter().for_each(|x| forma(x, &format!("{prefixo}[]"), saida)),
		_ => {},
	}
}

/// As AMOSTRAS do contrato (o que o conector manda de verdade à Central) em
/// tests/dados/contrato-amostras.json. O lado Python valida o MESMO arquivo
/// com os schemas de verdade (apps/api/tests/test_mail_v2_conector_contrato.py:
/// o Heartbeat e o Receipt do v1 e os corpos do v2). Aqui: a forma tem de
/// bater com o arquivo (mudou = regravar com ATUALIZAR_AMOSTRAS=1 e rodar o
/// teste do lado Python).
#[tokio::test]
async fn contrato_amostras_para_a_central() {
	let (mut c, p) = caixa_com_pastas();
	let rotulo = c.pasta("urgente", 8, None);
	let mut completo = NovoEmail::simples(&p.problema_ml, "amostra completa", AGORA - 30 * MIN);
	completo.de = ("Comprador Exemplo".into(), "comprador@exemplo.com.br".into());
	completo.cc = vec![("Cópia".into(), "copia@exemplo.com.br".into())];
	completo.reply_to = vec![("Responder".into(), "responder@exemplo.com.br".into())];
	completo.rotulos = vec![rotulo];
	completo.envelope = Some("bounce@envio.exemplo.com.br".into());
	completo.phishing = 1;
	completo.cabecalhos = "In-Reply-To: <pai@exemplo.com.br>\r\nReferences: <pai@exemplo.com.br>\r\nAuthentication-Results: mx; dkim=pass\r\n".into();
	completo.anexos = vec![
		Anexo { nome: "nota.pdf".into(), tipo: "application/pdf".into(), bytes: b"%PDF-teste".to_vec(), cid: Some("cid1".into()) },
		Anexo { nome: "x.exe".into(), tipo: "application/x-msdownload".into(), bytes: vec![0; 4], cid: None },
	];
	let movido = c.email(completo);
	let lixeira = c.lixeira.clone();
	let cena = Cena::nova("contrato", c, Central::nova());
	let mut leitor = cena.leitor(uma());
	leitor.uma_volta().await.unwrap();
	cena.cx().mover(&movido, &lixeira);
	cena.cx().apagar_de_vez(&movido);
	cena.avancar(2 * MIN);
	leitor.uma_volta().await.unwrap();
	let cv = cena.cv();
	let mut ingest = cv.corpos_ingest[0].clone();
	ingest["messages"] = json!([cv.corpos_ingest[0]["messages"][0].clone()]);
	let amostras = json!({
		"heartbeat": cv.sinais.last().cloned().unwrap(),
		"sync": cv.syncs[0].clone(),
		"ingest": ingest,
		"count": cv.contagens.iter().find(|c| c["folder_key"] == p.problema_ml.as_str()).cloned().unwrap(),
		"changes": cv.mudancas.last().cloned().unwrap(),
		"receipt": {"lease_token": "x".repeat(43), "status": "uncertain", "message_id": null, "error_code": "envio_sem_confirmacao"},
		"estados": EstadoConector::TODOS.iter().map(|e| tuta_conector::davinci::Sinal::de(*e, true)).map(|s| json!({"state": s.state, "can_send": s.can_send, "error_code": s.error_code})).collect::<Vec<_>>(),
	});
	drop(cv);
	let arquivo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/dados/contrato-amostras.json");
	if std::env::var_os("ATUALIZAR_AMOSTRAS").is_some() {
		std::fs::create_dir_all(arquivo.parent().unwrap()).unwrap();
		std::fs::write(&arquivo, serde_json::to_string_pretty(&amostras).unwrap() + "\n").unwrap();
	}
	let guardadas: Value = serde_json::from_slice(&std::fs::read(&arquivo).expect("rode com ATUALIZAR_AMOSTRAS=1")).unwrap();
	for chave in ["heartbeat", "sync", "ingest", "count", "changes", "receipt"] {
		let (mut agora, mut antes) = (Default::default(), Default::default());
		forma(&amostras[chave], chave, &mut agora);
		forma(&guardadas[chave], chave, &mut antes);
		assert_eq!(agora, antes, "a forma de `{chave}` mudou: regrave (ATUALIZAR_AMOSTRAS=1) e rode o teste do lado Python");
	}
	assert_eq!(amostras["estados"], guardadas["estados"]);
	// Nada de dado real nas amostras (só os endereços falsos do teste).
	let texto = serde_json::to_string(&guardadas).unwrap();
	for e in texto.split(|c: char| c == '"' || c.is_whitespace() || c == '<' || c == '>' || c == ',' || c == '\\').filter(|p| p.contains('@') && p.contains('.')) {
		assert!(e.ends_with("exemplo.com.br") || e.ends_with("@tuta.com") || e.ends_with("exemplo.test"), "{e}");
	}
}
