//! O ENVIO pela fila da Central (lease v1 → rascunho REPLY no Tuta → "vai
//! enviar" → SendDraft → recibo v1) contra um Tuta FALSO (o rascunho é
//! decifrado de verdade pelo SDK oficial com a chave que o conector mandou) e
//! uma Central FALSA. Nenhuma conta, nenhum e-mail de verdade.

mod comum;

use comum::caixa::{CaixaFalsa, Falha, NovoEmail};
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
use tuta_conector::envio::{pendencias_do_diario, Diario, DependenciasEnvio, Enviador, EntradaDiario, Passo};
use tuta_conector::estado::Compartilhado;
use tuta_conector::leitura::{Dependencias, Leitor, OpcoesLeitura};
use tuta_conector::rede::{ClienteRest, Politica};
use tuta_conector::registro;
use tuta_conector::segredo::{Segredo, SegredoBytes};
use tutasdk::bindings::rest_client::RestClient;

const AGORA: u64 = 1_791_440_000_000;
const MIN: u64 = 60_000;
const JOB: &str = "11111111-2222-4333-8444-555555555555";
const JOB2: &str = "11111111-2222-4333-8444-666666666666";
const FIO: &str = "<pedido-chegou?@exemplo.test>";

fn rest(politica: Politica) -> Arc<dyn RestClient> {
	Arc::new(ClienteRest::nativo(politica, Duration::from_secs(20)).unwrap())
}

struct Cena {
	tuta: ServidorFalso,
	caixa: Arc<Mutex<CaixaFalsa>>,
	central_srv: ServidorFalso,
	central: Arc<Mutex<Central>>,
	cofre: Arc<CofreMemoria>,
	pasta: PathBuf,
	relogio: Arc<AtomicU64>,
	compartilhado: Arc<Compartilhado>,
}

impl Drop for Cena {
	fn drop(&mut self) {
		let _ = std::fs::remove_dir_all(&self.pasta);
	}
}

impl Cena {
	fn nova(nome: &str, chave_local: bool) -> Self {
		registro::iniciar_para_testes();
		let mut c = CaixaFalsa::nova();
		let problema = c.pasta("problema ml", 0, None);
		c.email(NovoEmail::simples(&problema, "pedido chegou?", AGORA - 30 * MIN));
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
		let (tuta, caixa) = c.servir();
		let mut cv = Central::nova();
		cv.envio_ligado = true;
		let (central_srv, central) = central(cv);
		let pasta = pasta_temporaria(nome);
		std::fs::create_dir_all(&pasta).unwrap();
		if chave_local {
			std::fs::write(pasta.join("chaves.json"), br#"{"envio": true}"#).unwrap();
		}
		let compartilhado = Arc::new(Compartilhado::default());
		// O sinal da leitura já disse: a pessoa ligou o envio desta caixa.
		compartilhado.envio_ligado.store(true, Ordering::Relaxed);
		Self {
			tuta,
			caixa,
			central_srv,
			central,
			cofre,
			pasta,
			relogio: Arc::new(AtomicU64::new(AGORA)),
			compartilhado,
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

	fn enviador(&self) -> Enviador {
		let relogio = self.relogio.clone();
		Enviador::novo(DependenciasEnvio {
			conta: Conta::Geral,
			cofre: self.cofre.clone(),
			tuta_url: self.tuta.url.clone(),
			rest_tuta: rest(Politica::tuta(&self.tuta.url, &[".tuta.com"]).unwrap()),
			davinci: self.cliente(),
			pasta_local: self.pasta.clone(),
			relogio: Arc::new(move || relogio.load(Ordering::SeqCst)),
			compartilhado: self.compartilhado.clone(),
		})
	}

	fn cv(&self) -> std::sync::MutexGuard<'_, Central> {
		self.central.lock().unwrap()
	}

	fn cx(&self) -> std::sync::MutexGuard<'_, CaixaFalsa> {
		self.caixa.lock().unwrap()
	}

	fn recibo(&self, job: &str) -> Option<Value> {
		self.cv().recibos_finais.get(job).cloned()
	}

	fn pedidos_tuta(&self, contem: &str) -> usize {
		self.tuta.pedidos().iter().filter(|p| p.caminho.contains(contem)).count()
	}
}

#[tokio::test]
async fn resposta_sai_como_reply_pelo_alias_que_recebeu() {
	let cena = Cena::nova("envio-ok", true);
	cena.cv().enfileirar(JOB, "21MAX@tuta.com", "cliente@exemplo.com.br", Some(FIO), "Chega amanhã.\nObrigado <3 & até");
	let sinais_antes = cena.cv().sinais.len();
	let mut e = cena.enviador();
	let r = e.uma_volta().await.unwrap();
	assert_eq!((r.tarefas, r.enviadas, r.falharam, r.incertas), (1, 1, 0, 0), "{r}");
	assert!(cena.compartilhado.pode_enviar.load(Ordering::Relaxed), "o sinal pode dizer can_send");
	let envio = cena.cx().envios[0].clone();
	assert_eq!(envio.conversation_type, 1, "REPLY");
	assert_eq!(envio.previous_message_id.as_deref(), Some(FIO), "o fio do original");
	assert_eq!(envio.remetente, "21max@tuta.com", "o alias que recebeu, exato");
	assert_eq!(envio.para, vec!["cliente@exemplo.com.br"]);
	assert_eq!(envio.assunto, "Re: Pedido");
	assert_eq!(envio.corpo, "Chega amanhã.<br>Obrigado &lt;3 &amp; até");
	assert!(envio.chave_confere, "a chave do envio é a do rascunho (grupo de e-mail)");
	assert!(envio.plaintext && !envio.confidencial);
	// Dois sinais: "fiquei pronto" (can_send) antes do lease e o "vai enviar"
	// entre o rascunho e o envio.
	assert_eq!(cena.cv().sinais.len(), sinais_antes + 2);
	assert!(cena.cv().sinais.iter().skip(sinais_antes).all(|s| s["can_send"] == true));
	// O recibo leva o Message-ID que o Tuta deu (a ponte casa a resposta que volta pelo Enviados).
	assert_eq!(
		cena.recibo(JOB).unwrap(),
		json!({"status": "sent", "message_id": "<enviado-1@tuta.com>", "error_code": null})
	);
	// O diário: só ids e passos; o token do lease nunca vai para o disco.
	let diario = Diario::carregar(&cena.pasta);
	let entrada = &diario.tarefas[JOB];
	assert_eq!(entrada.passo, Passo::Concluido);
	assert_eq!(entrada.status.as_deref(), Some("sent"));
	assert!(entrada.recibo_entregue);
	assert!(entrada.rascunho.is_some());
	let disco = conteudo_da_pasta(&cena.pasta);
	assert!(!disco.contains("token-do-lease") && !disco.contains("cliente@") && !disco.contains("Chega"), "{disco}");
	// Outra volta: nada sai de novo.
	e.uma_volta().await.unwrap();
	assert_eq!(cena.cx().envios.len(), 1);
	assert_eq!(cena.pedidos_tuta("senddraftservice"), 1);
	for linha in registro::capturado() {
		assert!(!linha.contains("cliente@exemplo") && !linha.contains("Chega amanhã"), "{linha}");
	}
}

#[tokio::test]
async fn sem_as_duas_chaves_nem_pede_trabalho() {
	let cena = Cena::nova("envio-chave-local", false);
	cena.cv().enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
	let r = cena.enviador().uma_volta().await.unwrap();
	assert!(r.parado.as_deref().is_some_and(|p| p.contains("chaves.json")), "{r}");
	assert!(!cena.compartilhado.pode_enviar.load(Ordering::Relaxed));
	assert!(cena.central_srv.pedidos_em(&format!("/api/mail/agent/{CAIXA}/outbox/lease")).is_empty());

	let cena = Cena::nova("envio-chave-central", true);
	cena.compartilhado.envio_ligado.store(false, Ordering::Relaxed);
	cena.cv().envio_ligado = false;
	cena.cv().enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
	let r = cena.enviador().uma_volta().await.unwrap();
	assert!(r.parado.as_deref().is_some_and(|p| p.contains("desligado na Central")), "{r}");
	assert!(cena.central_srv.pedidos_em(&format!("/api/mail/agent/{CAIXA}/outbox/lease")).is_empty());
	assert!(cena.cx().rascunhos_criados.is_empty());
}

#[tokio::test]
async fn o_que_nao_pode_sair_volta_failed_sem_rascunho() {
	let cena = Cena::nova("envio-recusas", true);
	{
		let mut cv = cena.cv();
		// Alias desligado na conta (nunca cai no principal).
		cv.enfileirar("11111111-2222-4333-8444-000000000001", "antigo@tuta.com", "c@exemplo.com.br", Some(FIO), "a");
		// Sem o fio: nunca sai como e-mail novo.
		cv.enfileirar("11111111-2222-4333-8444-000000000002", "21max@tuta.com", "c@exemplo.com.br", None, "b");
		// Destinatário do próprio Tuta (cifra de ponta a ponta).
		cv.enfileirar("11111111-2222-4333-8444-000000000003", "21max@tuta.com", "alguem@tutanota.de", Some(FIO), "c");
		// Endereço que nem é da conta.
		cv.enfileirar("11111111-2222-4333-8444-000000000004", "loja@outra.com", "c@exemplo.com.br", Some(FIO), "d");
	}
	let r = cena.enviador().uma_volta().await.unwrap();
	assert_eq!((r.tarefas, r.falharam, r.enviadas), (4, 4, 0), "{r}");
	let codigos: Vec<Value> = (1..=4)
		.map(|i| cena.recibo(&format!("11111111-2222-4333-8444-00000000000{i}")).unwrap()["error_code"].clone())
		.collect();
	assert_eq!(
		codigos,
		vec![json!("sender_not_active_alias"), json!("sem_fio"), json!("destinatario_tuta"), json!("sender_not_active_alias")]
	);
	assert!(cena.cx().rascunhos_criados.is_empty(), "nenhum rascunho");
	assert_eq!(cena.pedidos_tuta("draftservice"), 0);
}

#[tokio::test]
async fn chave_da_caixa_desligada_antes_de_enviar() {
	let cena = Cena::nova("envio-desligou", true);
	{
		let mut cv = cena.cv();
		cv.enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
		cv.desligar_depois_do_lease = true;
	}
	let r = cena.enviador().uma_volta().await.unwrap();
	assert_eq!(r.falharam, 1, "{r}");
	assert_eq!(cena.recibo(JOB).unwrap()["error_code"], "envio_desligado");
	assert_eq!(cena.cx().rascunhos_criados.len(), 1, "o rascunho ficou no Tuta");
	assert!(cena.cx().envios.is_empty(), "e nada saiu");
	assert!(!cena.compartilhado.envio_ligado.load(Ordering::Relaxed));
}

#[tokio::test]
async fn falha_depois_do_rascunho_fica_incerta_e_nunca_reenvia() {
	let cena = Cena::nova("envio-incerto", true);
	cena.cv().enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
	cena.cx().falhas.push(Falha::status("/rest/tutanota/senddraftservice", 500));
	let mut e = cena.enviador();
	let r = e.uma_volta().await.unwrap();
	assert_eq!(r.incertas, 1, "{r}");
	assert_eq!(cena.recibo(JOB).unwrap(), json!({"status": "uncertain", "message_id": null, "error_code": "envio_sem_confirmacao"}));
	let tentativas = cena.pedidos_tuta("senddraftservice");
	assert_eq!(tentativas, 1);
	cena.cx().falhas.clear();
	e.uma_volta().await.unwrap();
	assert_eq!(cena.pedidos_tuta("senddraftservice"), tentativas, "nunca reenvia sozinho");
	assert_eq!(Diario::carregar(&cena.pasta).tarefas[JOB].status.as_deref(), Some("uncertain"));
}

#[tokio::test]
async fn falha_no_rascunho_e_failed_e_a_sessao_caida_para_a_leva() {
	let cena = Cena::nova("envio-rascunho", true);
	cena.cv().enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
	cena.cx().falhas.push(Falha::status("/rest/tutanota/draftservice", 500).uma_vez());
	let r = cena.enviador().uma_volta().await.unwrap();
	assert_eq!(r.falharam, 1, "{r}");
	assert_eq!(cena.recibo(JOB).unwrap()["error_code"], "rascunho_falhou");
	assert!(cena.cx().envios.is_empty());

	let cena = Cena::nova("envio-sessao", true);
	{
		let mut cv = cena.cv();
		cv.enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
		cv.enfileirar(JOB2, "21max@tuta.com", "outro@exemplo.com.br", Some(FIO), "oi");
	}
	cena.cx().falhas.push(Falha::status("/rest/tutanota/draftservice", 401));
	let r = cena.enviador().uma_volta().await;
	assert!(r.is_err(), "a conta caiu: {r:?}");
	assert_eq!(cena.recibo(JOB).unwrap()["error_code"], "rascunho_falhou");
	assert_eq!(cena.recibo(JOB2).unwrap()["error_code"], "sessao_caiu", "o resto da leva volta failed (nada saiu)");
	assert!(cena.cx().envios.is_empty());
}

#[tokio::test]
async fn recibo_que_nao_chegou_vai_de_novo_e_o_email_nao() {
	let cena = Cena::nova("envio-recibo", true);
	{
		let mut cv = cena.cv();
		cv.enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
		cv.falhas.push_back(("__recibo__", 503));
	}
	let mut e = cena.enviador();
	let r = e.uma_volta().await.unwrap();
	assert_eq!((r.enviadas, r.recibos), (1, 0), "{r}");
	assert!(cena.recibo(JOB).is_none());
	assert!(!Diario::carregar(&cena.pasta).tarefas[JOB].recibo_entregue);
	let r = e.uma_volta().await.unwrap();
	assert_eq!(r.recibos, 1, "{r}");
	assert_eq!(cena.recibo(JOB).unwrap()["status"], "sent");
	assert_eq!(cena.cx().envios.len(), 1, "o e-mail saiu UMA vez");
	assert!(Diario::carregar(&cena.pasta).tarefas[JOB].recibo_entregue);
}

#[tokio::test]
async fn tetos_param_antes_do_lease() {
	let cena = Cena::nova("envio-teto", true);
	cena.cv().enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
	// 30 saíram na última hora por este conector.
	let mut diario = Diario::default();
	for i in 0..30u64 {
		diario.tarefas.insert(
			format!("t{i}"),
			EntradaDiario {
				passo: Passo::Concluido,
				rascunho: None,
				status: Some("sent".into()),
				codigo: None,
				recibo_entregue: true,
				interrompido: false,
				criado_em_ms: AGORA - 10 * MIN,
				atualizado_em_ms: AGORA - 10 * MIN,
				enviado_em_ms: Some(AGORA - 10 * MIN),
			},
		);
	}
	diario.salvar(&cena.pasta).unwrap();
	let r = cena.enviador().uma_volta().await.unwrap();
	assert!(r.parado.as_deref().is_some_and(|p| p.contains("conector_hora")), "{r}");
	assert!(cena.central_srv.pedidos_em(&format!("/api/mail/agent/{CAIXA}/outbox/lease")).is_empty());

	// A CONTA inteira (Enviados) chegou a 100 na hora.
	let cena = Cena::nova("envio-teto-conta", true);
	cena.compartilhado.enviados_conta_hora.store(100, Ordering::Relaxed);
	let r = cena.enviador().uma_volta().await.unwrap();
	assert!(r.parado.as_deref().is_some_and(|p| p.contains("conta_hora")), "{r}");
}

#[tokio::test]
async fn lease_que_passou_do_prazo_nao_vai_para_o_send_draft() {
	// O Mac ficou parado 13 min entre pegar o trabalho e enviar: depois de 15
	// a Central deixa uma pessoa marcar "Não saiu" e responder de novo.
	let cena = Cena::nova("envio-lease-vencido", true);
	{
		let mut cv = cena.cv();
		cv.enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
		cv.relogio = Some(cena.relogio.clone());
		cv.demora_no_sinal_depois_do_lease_ms = 13 * MIN;
	}
	let r = cena.enviador().uma_volta().await.unwrap();
	assert_eq!(r.falharam, 1, "{r}");
	assert_eq!(cena.recibo(JOB).unwrap()["error_code"], "lease_vencido");
	assert!(cena.cx().envios.is_empty(), "nada saiu");
	assert_eq!(cena.pedidos_tuta("senddraftservice"), 0);
}

#[tokio::test]
async fn job_repetido_que_o_diario_ja_viu_nao_sai_de_novo() {
	let cena = Cena::nova("envio-repetido", true);
	let mut diario = Diario::default();
	diario.tarefas.insert(
		JOB.into(),
		EntradaDiario {
			passo: Passo::Concluido,
			rascunho: Some(vec!["L".into(), "E".into()]),
			status: Some("sent".into()),
			codigo: None,
			recibo_entregue: true,
			interrompido: false,
			criado_em_ms: AGORA - MIN,
			atualizado_em_ms: AGORA - MIN,
			enviado_em_ms: Some(AGORA - MIN),
		},
	);
	diario.salvar(&cena.pasta).unwrap();
	cena.cv().enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
	let r = cena.enviador().uma_volta().await.unwrap();
	assert_eq!(r.incertas, 1, "{r}");
	assert_eq!(cena.recibo(JOB).unwrap(), json!({"status": "uncertain", "message_id": null, "error_code": "ja_visto_no_diario"}));
	assert_eq!(cena.pedidos_tuta("draftservice"), 0, "nem rascunho");
	assert_eq!(Diario::carregar(&cena.pasta).tarefas[JOB].status.as_deref(), Some("sent"), "o diário fica como estava");
}

#[tokio::test]
async fn processo_que_caiu_no_meio_nao_reenvia() {
	let cena = Cena::nova("envio-caiu", true);
	let mut diario = Diario::default();
	diario.tarefas.insert(
		JOB.into(),
		EntradaDiario {
			passo: Passo::Enviando,
			rascunho: Some(vec!["L".into(), "E".into()]),
			status: None,
			codigo: None,
			recibo_entregue: false,
			interrompido: false,
			criado_em_ms: AGORA - MIN,
			atualizado_em_ms: AGORA - MIN,
			enviado_em_ms: None,
		},
	);
	diario.salvar(&cena.pasta).unwrap();
	let mut e = cena.enviador();
	e.uma_volta().await.unwrap();
	let diario = Diario::carregar(&cena.pasta);
	assert!(diario.tarefas[JOB].interrompido);
	assert_eq!(pendencias_do_diario(&diario), (1, 0, 0));
	assert_eq!(cena.pedidos_tuta("senddraftservice"), 0, "nada sai de novo");
	assert!(cena.recibo(JOB).is_none(), "sem o token (só da memória) o recibo não sai: a Central marca incerto");
	assert!(registro::capturado().iter().any(|l| l.contains("conferir no Tuta")));
}

#[tokio::test]
async fn o_servico_junta_leitura_e_envio() {
	let cena = Cena::nova("servico", true);
	cena.compartilhado.envio_ligado.store(false, Ordering::Relaxed);
	cena.cv().enfileirar(JOB, "21max@tuta.com", "cliente@exemplo.com.br", Some(FIO), "oi");
	let relogio = cena.relogio.clone();
	let github = ServidorFalso::iniciar(|_| Resposta::json(200, json!([])));
	let leitor = Leitor::novo(
		Dependencias {
			conta: Conta::Geral,
			cofre: cena.cofre.clone(),
			tuta_url: cena.tuta.url.clone(),
			rest_tuta: rest(Politica::tuta(&cena.tuta.url, &[".tuta.com"]).unwrap()),
			davinci: Some(cena.cliente()),
			url_releases: format!("{}/r", github.url),
			rest_github: rest(Politica::exata(&github.url).unwrap()),
			pasta_local: cena.pasta.clone(),
			relogio: Arc::new(move || relogio.load(Ordering::SeqCst)),
			compartilhado: cena.compartilhado.clone(),
		},
		OpcoesLeitura::default(),
	);
	tuta_conector::servico::laco(leitor, cena.enviador(), cena.compartilhado.clone(), Some(1)).await.unwrap();
	// A leitura pulsou (send_enabled = true veio da Central) e o envio saiu na mesma volta.
	assert_eq!(cena.cv().emails.len(), 1, "o original foi lido");
	assert_eq!(cena.cx().envios.len(), 1);
	assert_eq!(cena.recibo(JOB).unwrap()["status"], "sent");
}
