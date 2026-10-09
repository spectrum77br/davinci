//! PONTA A PONTA contra a Central de e-mail DE VERDADE (a API do DaVinci
//! rodando NESTE Mac, num schema local descartável), com o Tuta FALSO
//! cifrado de verdade: Tuta falso → conector → Central (v1 + v2) → ponte →
//! conversa do /atendimento → resposta de pessoa → fila da Central → conector
//! → rascunho REPLY + envio no Tuta falso → recibo → a mensagem `enviada`.
//!
//! Não roda no `cargo test` normal (precisa da API local no ar): quem
//! orquestra é o roteiro Python que sobe a API, semeia a caixa, roda a ponte
//! e confere o banco entre as fases. As fases:
//!
//!   E2E_URL=http://127.0.0.1:8014 E2E_CAIXA=<uuid> E2E_TOKEN=<chave> E2E_PASTA=<pasta> \
//!     cargo test --test ponta_a_ponta -- --ignored --exact ler
//!   (… a ponte e a pessoa respondem do lado Python …)
//!   E2E_URL=… E2E_CAIXA=… E2E_TOKEN=… E2E_PASTA=… cargo test --test ponta_a_ponta -- --ignored --exact enviar
//!
//! Nenhuma conta, nenhum e-mail de verdade; só 127.0.0.1.

mod comum;

use comum::caixa::{CaixaFalsa, NovoEmail};
use comum::*;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tuta_conector::chaveiro::{self, CofreMemoria, SessaoTuta};
use tuta_conector::config::Conta;
use tuta_conector::davinci::ClienteDavinci;
use tuta_conector::envio::{DependenciasEnvio, Enviador};
use tuta_conector::estado::Compartilhado;
use tuta_conector::leitura::{Dependencias, Leitor, OpcoesLeitura};
use tuta_conector::rede::{ClienteRest, Politica};
use tuta_conector::registro;
use tuta_conector::segredo::{Segredo, SegredoBytes};
use tutasdk::bindings::rest_client::RestClient;

/// 2026-10-08 06:13 UTC: o "agora" do Tuta falso (a ponte corta antes disto).
const AGORA: u64 = 1_791_440_000_000;
const MIN: u64 = 60_000;
/// O Message-ID do e-mail do cliente (o fio da resposta).
pub const FIO: &str = "<pedido-e2e@exemplo.com.br>";

struct Ambiente {
	url: String,
	caixa: String,
	token: String,
	/// A pasta local da conta (a MESMA nas duas fases: a mesma instância).
	pasta: std::path::PathBuf,
}

fn ambiente() -> Option<Ambiente> {
	Some(Ambiente {
		url: std::env::var("E2E_URL").ok()?,
		caixa: std::env::var("E2E_CAIXA").ok()?,
		token: std::env::var("E2E_TOKEN").ok()?,
		pasta: std::env::var_os("E2E_PASTA")?.into(),
	})
}

fn rest(politica: Politica) -> Arc<dyn RestClient> {
	Arc::new(ClienteRest::nativo(politica, Duration::from_secs(30)).unwrap())
}

/// A caixa do Tuta falso: o cliente na "problema ml", um código de acesso na
/// Entrada (vira "segurança" na ponte, mascarado já aqui) e um boleto no
/// financeiro (só contado: o corpo nunca sai do Mac).
fn caixa() -> CaixaFalsa {
	let mut c = CaixaFalsa::nova();
	let problema = c.pasta("problema ml", 0, None);
	let financeiro = c.pasta("financeiro", 0, None);
	let mut cliente = NovoEmail::simples(&problema, "Meu celular chegou quebrado", AGORA - 30 * MIN);
	cliente.de = ("Cliente Exemplo".into(), "cliente@exemplo.com.br".into());
	cliente.para = vec![("JLAS2".into(), "21max@tuta.com".into())];
	cliente.message_id = FIO.into();
	cliente.cabecalhos = format!("Message-ID: {FIO}\r\nDelivered-To: 21max@tuta.com\r\n");
	cliente.corpo_html = "<p>Olá, o celular chegou com a tela trincada.</p><p>O que faço?</p>".into();
	c.email(cliente);
	let entrada = c.entrada.clone();
	let mut codigo = NovoEmail::simples(&entrada, "Seu código de verificação", AGORA - 20 * MIN);
	codigo.de = ("Mercado Livre".into(), "no-reply@mercadolivre.com".into());
	codigo.message_id = "<codigo-e2e@mercadolivre.com>".into();
	codigo.cabecalhos = "Message-ID: <codigo-e2e@mercadolivre.com>\r\n".into();
	codigo.corpo_html = "<p>Seu código de verificação é 482913. Não compartilhe.</p>".into();
	c.email(codigo);
	let mut boleto = NovoEmail::simples(&financeiro, "boleto do fornecedor", AGORA - 10 * MIN);
	boleto.corpo_html = "<p>Segue o boleto confidencial.</p>".into();
	c.email(boleto);
	c
}

fn cofre_com_sessao(c: &CaixaFalsa) -> Arc<CofreMemoria> {
	let cofre = Arc::new(CofreMemoria::default());
	let cred = c.credenciais();
	chaveiro::gravar_sessao(
		&*cofre,
		Conta::Geral,
		&SessaoTuta {
			login: cred.login,
			user_id: cred.user_id.as_str().to_owned(),
			access_token: Segredo::novo(cred.access_token),
			encrypted_passphrase_key: SegredoBytes::novo(cred.encrypted_passphrase_key),
			criada_em_ms: AGORA,
			versao_sdk: tutasdk::CLIENT_VERSION.into(),
		},
	)
	.unwrap();
	cofre
}

struct Montado {
	tuta: ServidorFalso,
	caixa: Arc<std::sync::Mutex<CaixaFalsa>>,
	cofre: Arc<CofreMemoria>,
	davinci: Arc<ClienteDavinci>,
	pasta: std::path::PathBuf,
	compartilhado: Arc<Compartilhado>,
	relogio: Arc<AtomicU64>,
	github: ServidorFalso,
}

fn montar(a: &Ambiente) -> Montado {
	registro::iniciar_para_testes();
	let c = caixa();
	let cofre = cofre_com_sessao(&c);
	let (tuta, caixa) = c.servir();
	let davinci = Arc::new(
		ClienteDavinci::com_cliente(&a.url, &a.caixa, Segredo::novo(a.token.clone()), rest(Politica::davinci(&a.url).unwrap()))
			.unwrap(),
	);
	Montado {
		tuta,
		caixa,
		cofre,
		davinci,
		pasta: a.pasta.clone(),
		compartilhado: Arc::new(Compartilhado::default()),
		relogio: Arc::new(AtomicU64::new(AGORA)),
		github: ServidorFalso::iniciar(|_| Resposta::json(200, serde_json::json!([]))),
	}
}

impl Montado {
	fn leitor(&self) -> Leitor {
		let relogio = self.relogio.clone();
		Leitor::novo(
			Dependencias {
				conta: Conta::Geral,
				cofre: self.cofre.clone(),
				tuta_url: self.tuta.url.clone(),
				rest_tuta: rest(Politica::tuta(&self.tuta.url, &[".tuta.com"]).unwrap()),
				davinci: Some(self.davinci.clone()),
				url_releases: format!("{}/r", self.github.url),
				rest_github: rest(Politica::exata(&self.github.url).unwrap()),
				pasta_local: self.pasta.clone(),
				relogio: Arc::new(move || relogio.load(Ordering::SeqCst)),
				compartilhado: self.compartilhado.clone(),
			},
			OpcoesLeitura {
				uma_volta: true,
				..OpcoesLeitura::default()
			},
		)
	}

	fn enviador(&self) -> Enviador {
		let relogio = self.relogio.clone();
		Enviador::novo(DependenciasEnvio {
			conta: Conta::Geral,
			cofre: self.cofre.clone(),
			tuta_url: self.tuta.url.clone(),
			rest_tuta: rest(Politica::tuta(&self.tuta.url, &[".tuta.com"]).unwrap()),
			davinci: self.davinci.clone(),
			pasta_local: self.pasta.clone(),
			relogio: Arc::new(move || relogio.load(Ordering::SeqCst)),
			compartilhado: self.compartilhado.clone(),
		})
	}
}

/// Fase 1: a leitura entrega à Central de verdade.
#[tokio::test]
#[ignore = "precisa da API local no ar (roteiro ponta a ponta)"]
async fn ler() {
	let Some(a) = ambiente() else {
		eprintln!("sem E2E_URL/E2E_CAIXA/E2E_TOKEN: nada a fazer");
		return;
	};
	let m = montar(&a);
	// O Mac já pode enviar (a chave local ligada; o envio de verdade é a fase 2).
	m.compartilhado.pode_enviar.store(true, Ordering::Relaxed);
	let mut leitor = m.leitor();
	let r = leitor.uma_volta().await.expect("volta contra a Central de verdade");
	println!("E2E ler: {r}");
	assert_eq!(r.entregues, 2, "o do cliente e o do código (o boleto só contado): {r}");
	assert_eq!(r.recusados, 0, "{r}");
	// Segunda volta: nada de novo, nada repetido.
	m.relogio.fetch_add(2 * MIN, Ordering::SeqCst);
	let r = leitor.uma_volta().await.unwrap();
	assert_eq!((r.entregues, r.faltando, r.recusados), (0, 0, 0), "{r}");
	assert!(m.compartilhado.envio_ligado.load(Ordering::Relaxed), "a pessoa ligou o envio da caixa");
}

/// Fase 2: a resposta que a pessoa mandou pela conversa sai pelo Tuta.
#[tokio::test]
#[ignore = "precisa da API local no ar (roteiro ponta a ponta)"]
async fn enviar() {
	let Some(a) = ambiente() else {
		eprintln!("sem E2E_URL/E2E_CAIXA/E2E_TOKEN: nada a fazer");
		return;
	};
	let m = montar(&a);
	std::fs::create_dir_all(&m.pasta).unwrap();
	std::fs::write(m.pasta.join("chaves.json"), br#"{"envio": true}"#).unwrap();
	let mut leitor = m.leitor();
	// O sinal traz a chave da caixa (a pessoa ligou na tela); o envio diz
	// "posso enviar" no sinal dele assim que fica pronto.
	leitor.pulsar().await.unwrap();
	let mut enviador = m.enviador();
	let r = enviador.uma_volta().await.expect("envio");
	println!("E2E enviar: {r}");
	assert_eq!((r.tarefas, r.enviadas, r.falharam, r.incertas), (1, 1, 0, 0), "{r}");
	let envio = m.caixa.lock().unwrap().envios[0].clone();
	assert_eq!(envio.conversation_type, 1, "REPLY");
	assert_eq!(envio.previous_message_id.as_deref(), Some(FIO));
	assert_eq!(envio.remetente, "21max@tuta.com", "pelo alias que recebeu (remetente estrito)");
	assert_eq!(envio.para, vec!["cliente@exemplo.com.br"]);
	assert!(envio.chave_confere);
	println!("E2E corpo enviado: {}", envio.corpo);
	println!("E2E assunto enviado: {}", envio.assunto);
	// A resposta que saiu aparece em Enviados: a leitura a entrega (a ponte casa pelo recibo).
	m.relogio.fetch_add(2 * MIN, Ordering::SeqCst);
	let r = leitor.uma_volta().await.unwrap();
	println!("E2E ler depois do envio: {r}");
	assert!(r.entregues >= 1, "o enviado entrou: {r}");
	// Nada sai de novo.
	let r = enviador.uma_volta().await.unwrap();
	assert_eq!(r.tarefas, 0, "{r}");
	assert_eq!(m.caixa.lock().unwrap().envios.len(), 1);
}
