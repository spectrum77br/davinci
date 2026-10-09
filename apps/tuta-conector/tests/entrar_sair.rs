//! entrar / sair / estado / configurar contra um Tuta FALSO (127.0.0.1),
//! Chaveiro em memória e Terminal roteirizado. Nenhuma conta de verdade.

mod comum;

use comum::*;
use crypto_primitives::key::GenericAesKey;
use std::sync::Arc;
use std::time::Duration;
use tuta_conector::chaveiro::{self, CofreMemoria, SessaoTuta};
use tuta_conector::comandos::{self, Ambiente, ValidarSessao};
use tuta_conector::config::{self, Conta};
use tuta_conector::rede::{ClienteRest, Politica};
use tuta_conector::registro;
use tuta_conector::segredo::{Segredo, SegredoBytes};
use tuta_conector::terminal::TerminalRoteiro;
use tuta_conector::tuta::sdk::ErroRetomar;
use tuta_conector::tuta::sessao::Ritmo;
use tutasdk::bindings::rest_client::RestClient;
use tutasdk::login::Credentials;

struct ValidaSempre;
#[async_trait::async_trait(?Send)]
impl ValidarSessao for ValidaSempre {
	async fn validar(&self, _: Credentials) -> Result<(), ErroRetomar> {
		Ok(())
	}
}

struct NuncaValida;
#[async_trait::async_trait(?Send)]
impl ValidarSessao for NuncaValida {
	async fn validar(&self, _: Credentials) -> Result<(), ErroRetomar> {
		Err(ErroRetomar::SessaoCaiu)
	}
}

fn rest_para(url: &str) -> Arc<dyn RestClient> {
	Arc::new(
		ClienteRest::nativo(
			Politica::tuta(url, config::TUTA_SUFIXOS_BLOB).unwrap(),
			Duration::from_secs(10),
		)
		.unwrap(),
	)
}

fn ritmo_rapido() -> Ritmo {
	Ritmo {
		intervalo: Duration::from_millis(10),
		espera_max: Duration::from_secs(3),
	}
}

struct Cena {
	conta: Conta,
	cofre: CofreMemoria,
	terminal: TerminalRoteiro,
	pasta: std::path::PathBuf,
	url: String,
}

impl Drop for Cena {
	fn drop(&mut self) {
		let _ = std::fs::remove_dir_all(&self.pasta);
	}
}

impl Cena {
	fn nova(nome: &str, url: &str, respostas: &[&str]) -> Self {
		registro::iniciar_para_testes();
		Self {
			conta: Conta::Geral,
			cofre: CofreMemoria::default(),
			terminal: TerminalRoteiro::com(respostas),
			pasta: pasta_temporaria(nome),
			url: url.to_owned(),
		}
	}

	fn ambiente<'a>(&'a mut self, validar: &'a dyn ValidarSessao) -> Ambiente<'a> {
		Ambiente {
			conta: self.conta,
			cofre: &self.cofre,
			terminal: &mut self.terminal,
			pasta_local: self.pasta.clone(),
			tuta_url: self.url.clone(),
			rest_tuta: rest_para(&self.url),
			validar,
			ritmo_2fa: ritmo_rapido(),
		}
	}
}

/// Nenhum segredo na tela, no log capturado, nos arquivos locais, nos
/// cabeçalhos ou na URL de nenhum pedido (e nenhum e-mail em cabeçalho).
fn conferir_sem_vazamento(cena: &Cena, servidor: &ServidorFalso, segredos: &[&str]) {
	let tela = cena.terminal.tela();
	let log = registro::capturado().join("\n");
	let disco = conteudo_da_pasta(&cena.pasta);
	for s in segredos {
		assert!(!tela.contains(s), "segredo na tela");
		assert!(!log.contains(s), "segredo no log");
		assert!(!disco.contains(s), "segredo em disco");
	}
	for p in servidor.pedidos() {
		for (nome, valor) in &p.cabecalhos {
			assert!(!valor.contains('@'), "e-mail no cabeçalho {nome}");
			for s in segredos {
				assert!(!valor.contains(s), "segredo no cabeçalho {nome}");
			}
			assert_ne!(nome, "user-agent", "o conector não manda User-Agent");
		}
		// A senha nunca vai ao Tuta (só o verificador); nem na URL nem no corpo.
		let corpo = String::from_utf8_lossy(&p.corpo);
		assert!(!corpo.contains("senha certa"), "senha no corpo");
		assert!(!p.query_crua.contains("senha"), "senha na URL");
	}
}

#[tokio::test]
async fn entrar_sem_segundo_fator_guarda_no_chaveiro() {
	let (tuta, estado) = tuta_falso(ConfigTuta::default());
	let mut cena = Cena::nova("sem2fa", &tuta.url, &["  Conta.Teste@Tuta.com ", "senha certa do teste"]);
	let codigo = comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await;
	assert_eq!(codigo, 0, "{}", cena.terminal.tela());

	// O que foi ao Tuta: e-mail normalizado, verificador certo, nome da sessão,
	// cabeçalhos cv e v, nenhum pedido fora do esperado.
	let sessoes = estado.lock().unwrap().sessoes.clone();
	assert_eq!(sessoes.len(), 1);
	let pedido = &sessoes[0];
	assert_eq!(pedido["1213"], "conta.teste@tuta.com");
	assert_eq!(pedido["1214"], verificador("senha certa do teste").as_str());
	assert_eq!(pedido["1215"], "DaVinci conector – Mac mini (geral)");
	assert_eq!(pedido["1217"], serde_json::Value::Null);
	let salt = tuta.pedidos_em("/rest/sys/saltservice");
	assert_eq!(salt.len(), 1);
	assert_eq!(salt[0].cabecalhos["cv"], tutasdk::CLIENT_VERSION);
	assert_eq!(salt[0].cabecalhos["v"], "156");

	// O que ficou no Chaveiro: o token do Tuta, o usuário e a chave cifrada
	// com a accessKey que foi ao Tuta (a mesma conta do resume_session oficial).
	let guardada = chaveiro::ler_sessao(&cena.cofre, Conta::Geral).unwrap().expect("sessão no cofre");
	assert_eq!(guardada.access_token.expor(), token_falso());
	assert_eq!(guardada.user_id, USUARIO);
	assert_eq!(guardada.login, "conta.teste@tuta.com");
	let chave_acesso = base64::Engine::decode(
		&base64::engine::general_purpose::STANDARD,
		pedido["1216"].as_str().unwrap(),
	)
	.unwrap();
	let chave_senha = GenericAesKey::from_bytes(&chave_acesso)
		.unwrap()
		.decrypt_aes_key(guardada.encrypted_passphrase_key.expor())
		.unwrap();
	let esperada = tutasdk::crypto::generate_key_from_passphrase("senha certa do teste", SALT);
	assert_eq!(chave_senha.as_bytes(), esperada.as_bytes());

	assert!(cena.terminal.tela().contains("Pronto"));
	conferir_sem_vazamento(&cena, &tuta, &["senha certa do teste", &token_falso()]);
	assert!(tuta.pedidos_em("/rest/sys/secondfactorauthservice").is_empty());
}

#[tokio::test]
async fn entrar_com_totp_errado_depois_certo() {
	let (tuta, estado) = tuta_falso(ConfigTuta {
		desafios: vec!["1"],
		pendente_vezes: 2,
		..ConfigTuta::default()
	});
	let mut cena = Cena::nova(
		"totp",
		&tuta.url,
		&["conta.teste@tuta.com", "senha certa do teste", "000000", "12345", "123 456"],
	);
	let codigo = comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await;
	assert_eq!(codigo, 0, "{}", cena.terminal.tela());
	let posts: Vec<_> = tuta
		.pedidos_em("/rest/sys/secondfactorauthservice")
		.into_iter()
		.filter(|p| p.metodo == "POST")
		.collect();
	// "000000" (errado) e "123456" (certo); "12345" nem sai (não tem 6 números).
	assert_eq!(posts.len(), 2);
	let ultimo = posts[1].json();
	assert_eq!(ultimo["1230"], "1");
	assert_eq!(ultimo["1243"], "123456");
	let id = tuta_conector::tuta::protocolo::id_da_sessao(&Segredo::novo(token_falso())).unwrap();
	assert_eq!(ultimo["1232"], serde_json::json!([[id.lista, id.elemento]]));
	assert!(estado.lock().unwrap().totp_aceito);
	assert!(!estado.lock().unwrap().cancelada);
	let gets = tuta
		.pedidos_em("/rest/sys/secondfactorauthservice")
		.into_iter()
		.filter(|p| p.metodo == "GET")
		.count();
	assert_eq!(gets, 3, "esperou o Tuta liberar (2 pendentes + 1 liberado)");
	assert!(chaveiro::ler_sessao(&cena.cofre, Conta::Geral).unwrap().is_some());
	assert!(cena.terminal.tela().contains("Código não aceito"));
	conferir_sem_vazamento(&cena, &tuta, &["senha certa do teste", &token_falso()]);
}

#[tokio::test]
async fn desistir_do_totp_cancela_a_sessao_pendente() {
	let (tuta, estado) = tuta_falso(ConfigTuta {
		desafios: vec!["1"],
		..ConfigTuta::default()
	});
	let mut cena = Cena::nova("desiste", &tuta.url, &["", "senha certa do teste", ""]);
	// e-mail vazio = o padrão (061083.jf@tuta.com) → o falso não conhece → 401.
	let codigo = comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await;
	assert_eq!(codigo, 1);
	assert!(cena.terminal.tela().contains("não aceitou o e-mail ou a senha"));

	let mut cena = Cena::nova("desiste2", &tuta.url, &["conta.teste@tuta.com", "senha certa do teste", ""]);
	let codigo = comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await;
	assert_eq!(codigo, 1);
	assert!(estado.lock().unwrap().cancelada, "a sessão pendente foi cancelada no Tuta");
	assert_eq!(cena.cofre.quantos(), 0);
	assert!(cena.terminal.tela().contains("login cancelado"));
}

#[tokio::test]
async fn tres_codigos_errados_cancelam() {
	let (tuta, estado) = tuta_falso(ConfigTuta {
		desafios: vec!["1"],
		..ConfigTuta::default()
	});
	let mut cena = Cena::nova(
		"errado3",
		&tuta.url,
		&["conta.teste@tuta.com", "senha certa do teste", "111111", "222222", "333333"],
	);
	assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 1);
	assert!(estado.lock().unwrap().cancelada);
	assert_eq!(cena.cofre.quantos(), 0);
	assert!(cena.terminal.tela().contains("não foi aceito"));
}

#[tokio::test]
async fn conta_so_com_chave_de_seguranca_da_erro_claro() {
	let (tuta, estado) = tuta_falso(ConfigTuta {
		desafios: vec!["2", "0"],
		..ConfigTuta::default()
	});
	let mut cena = Cena::nova("u2f", &tuta.url, &["conta.teste@tuta.com", "senha certa do teste"]);
	assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 1);
	let tela = cena.terminal.tela();
	assert!(tela.contains("chave de segurança"), "{tela}");
	assert!(tela.contains("TOTP"), "{tela}");
	assert!(estado.lock().unwrap().cancelada);
	assert_eq!(cena.cofre.quantos(), 0);
}

#[tokio::test]
async fn senha_errada_nao_guarda_nada() {
	let (tuta, _estado) = tuta_falso(ConfigTuta::default());
	let mut cena = Cena::nova("errada", &tuta.url, &["conta.teste@tuta.com", "senha ERRADA"]);
	assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 1);
	assert!(cena.terminal.tela().contains("não aceitou o e-mail ou a senha"));
	assert_eq!(cena.cofre.quantos(), 0);
	conferir_sem_vazamento(&cena, &tuta, &["senha ERRADA"]);
}

#[tokio::test]
async fn conta_bcrypt_para_antes_de_criar_sessao() {
	let (tuta, estado) = tuta_falso(ConfigTuta {
		kdf: "0",
		..ConfigTuta::default()
	});
	let mut cena = Cena::nova("bcrypt", &tuta.url, &["conta.teste@tuta.com", "senha certa do teste"]);
	assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 1);
	assert!(cena.terminal.tela().contains("Bcrypt"));
	assert!(estado.lock().unwrap().sessoes.is_empty(), "nem tentou criar sessão");
}

#[tokio::test]
async fn versao_recusada_474_e_tuta_fora_e_429() {
	for (status, texto) in [
		(474u16, "recusou a versão do SDK"),
		(503, "fora do ar"),
		(429, "esperar"),
		(472, "bloqueou o acesso"),
	] {
		let (tuta, _) = tuta_falso(ConfigTuta {
			status_salt: Some(status),
			..ConfigTuta::default()
		});
		let mut cena = Cena::nova(&format!("st{status}"), &tuta.url, &["conta.teste@tuta.com", "senha certa do teste"]);
		assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 1);
		let tela = cena.terminal.tela();
		assert!(tela.contains(texto), "{status}: {tela}");
	}
}

#[tokio::test]
async fn sem_rede_da_erro_sem_travar() {
	// Porta fechada: o cliente HTTP falha na hora (sem pendurar).
	let ouvinte = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
	let url = format!("http://127.0.0.1:{}", ouvinte.local_addr().unwrap().port());
	drop(ouvinte);
	let mut cena = Cena::nova("semrede", &url, &["conta.teste@tuta.com", "senha certa do teste"]);
	assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 1);
	assert!(cena.terminal.tela().contains("sem conexão"));
}

#[tokio::test]
async fn sessao_que_nao_abre_e_encerrada_no_tuta() {
	let (tuta, estado) = tuta_falso(ConfigTuta::default());
	let mut cena = Cena::nova("naoabre", &tuta.url, &["conta.teste@tuta.com", "senha certa do teste"]);
	assert_eq!(comandos::entrar(&mut cena.ambiente(&NuncaValida)).await, 1);
	assert_eq!(cena.cofre.quantos(), 0, "não guarda sessão que não abre");
	let encerradas = estado.lock().unwrap().encerradas.clone();
	assert_eq!(encerradas.len(), 1);
	assert_eq!(encerradas[0]["1597"], token_falso().as_str());
}

#[tokio::test]
async fn entrar_com_sessao_existente_recusa_sem_rede() {
	let (tuta, _) = tuta_falso(ConfigTuta::default());
	let mut cena = Cena::nova("existe", &tuta.url, &[]);
	chaveiro::gravar_sessao(&cena.cofre, Conta::Geral, &sessao_de_teste()).unwrap();
	assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 1);
	assert!(cena.terminal.tela().contains("tuta-conector sair"));
	assert!(tuta.pedidos().is_empty());
}

fn sessao_de_teste() -> SessaoTuta {
	SessaoTuta {
		login: "conta.teste@tuta.com".into(),
		user_id: USUARIO.into(),
		access_token: Segredo::novo(token_falso()),
		encrypted_passphrase_key: SegredoBytes::novo(vec![1; 49]),
		criada_em_ms: 1_760_000_000_000,
		versao_sdk: tutasdk::CLIENT_VERSION.into(),
	}
}

#[tokio::test]
async fn sair_encerra_no_tuta_e_apaga_do_chaveiro() {
	let (tuta, estado) = tuta_falso(ConfigTuta::default());
	let mut cena = Cena::nova("sair", &tuta.url, &[]);
	chaveiro::gravar_sessao(&cena.cofre, Conta::Geral, &sessao_de_teste()).unwrap();
	assert_eq!(comandos::sair(&mut cena.ambiente(&ValidaSempre), false).await, 0);
	assert_eq!(cena.cofre.quantos(), 0);
	let encerradas = estado.lock().unwrap().encerradas.clone();
	assert_eq!(encerradas.len(), 1);
	let id = tuta_conector::tuta::protocolo::id_da_sessao(&Segredo::novo(token_falso())).unwrap();
	assert_eq!(encerradas[0]["1596"], "0");
	assert_eq!(encerradas[0]["1597"], token_falso().as_str());
	assert_eq!(encerradas[0]["1598"], serde_json::json!([[id.lista, id.elemento]]));
	let pedido = &tuta.pedidos_em("/rest/sys/closesessionservice")[0];
	assert_eq!(pedido.cabecalhos["v"], "156");
	assert!(pedido.query_crua.is_empty(), "o token vai no corpo, não na URL");
	conferir_sem_vazamento(&cena, &tuta, &[]);
	assert!(!cena.terminal.tela().contains(&token_falso()));
}

#[tokio::test]
async fn sair_com_sessao_ja_encerrada_apaga() {
	let (tuta, _) = tuta_falso(ConfigTuta {
		status_encerrar: 401,
		..ConfigTuta::default()
	});
	let mut cena = Cena::nova("jaencerrada", &tuta.url, &[]);
	chaveiro::gravar_sessao(&cena.cofre, Conta::Geral, &sessao_de_teste()).unwrap();
	assert_eq!(comandos::sair(&mut cena.ambiente(&ValidaSempre), false).await, 0);
	assert_eq!(cena.cofre.quantos(), 0);
	assert!(cena.terminal.tela().contains("já estava encerrada"));
}

#[tokio::test]
async fn sair_com_tuta_fora_mantem_o_item_a_nao_ser_forcado() {
	let (tuta, _) = tuta_falso(ConfigTuta {
		status_encerrar: 500,
		..ConfigTuta::default()
	});
	let mut cena = Cena::nova("forcar", &tuta.url, &[]);
	chaveiro::gravar_sessao(&cena.cofre, Conta::Geral, &sessao_de_teste()).unwrap();
	assert_eq!(comandos::sair(&mut cena.ambiente(&ValidaSempre), false).await, 1);
	assert_eq!(cena.cofre.quantos(), 1, "fica para tentar de novo");
	assert!(cena.terminal.tela().contains("--forcar"));
	assert_eq!(comandos::sair(&mut cena.ambiente(&ValidaSempre), true).await, 0);
	assert_eq!(cena.cofre.quantos(), 0);
}

#[tokio::test]
async fn sair_sem_sessao_nao_faz_nada() {
	let (tuta, _) = tuta_falso(ConfigTuta::default());
	let mut cena = Cena::nova("semsessao", &tuta.url, &[]);
	assert_eq!(comandos::sair(&mut cena.ambiente(&ValidaSempre), false).await, 0);
	assert!(tuta.pedidos().is_empty());
}

#[tokio::test]
async fn estado_mostra_sem_segredo_e_sem_rede() {
	let (tuta, _) = tuta_falso(ConfigTuta::default());
	let mut cena = Cena::nova("estado", &tuta.url, &[]);
	chaveiro::gravar_sessao(&cena.cofre, Conta::Geral, &sessao_de_teste()).unwrap();
	chaveiro::gravar_davinci(
		&cena.cofre,
		Conta::Geral,
		&chaveiro::AcessoDavinci {
			url: "https://davinci.exemplo".into(),
			caixa: CAIXA.into(),
			token: Segredo::novo(TOKEN_DAVINCI.into()),
		},
	)
	.unwrap();
	assert_eq!(comandos::estado(&mut cena.ambiente(&ValidaSempre)), 0);
	let tela = cena.terminal.tela();
	assert!(tela.contains(tutasdk::CLIENT_VERSION), "{tela}");
	assert!(tela.contains("Sessão no Chaveiro: SIM"), "{tela}");
	assert!(tela.contains("https://davinci.exemplo"), "{tela}");
	assert!(tela.contains(CAIXA), "{tela}");
	assert!(tela.contains("conta geral"), "{tela}");
	assert!(tela.contains("envio desligado"), "{tela}");
	assert!(tela.contains("Websocket: nunca"), "{tela}");
	assert!(!tela.contains(&token_falso()));
	assert!(!tela.contains(TOKEN_DAVINCI));
	assert!(tuta.pedidos().is_empty(), "estado não toca a rede");
}

#[tokio::test]
async fn configurar_grava_url_caixa_e_chave_sem_mostrar() {
	let (tuta, _) = tuta_falso(ConfigTuta::default());
	let mut cena = Cena::nova(
		"configurar",
		&tuta.url,
		&["https://DaVinci.Exemplo/qualquer/caminho", &CAIXA.to_uppercase(), TOKEN_DAVINCI],
	);
	assert_eq!(comandos::configurar(&mut cena.ambiente(&ValidaSempre)), 0);
	let acesso = chaveiro::ler_davinci(&cena.cofre, Conta::Geral).unwrap().unwrap();
	assert_eq!(acesso.url, "https://davinci.exemplo");
	assert_eq!(acesso.caixa, CAIXA);
	assert_eq!(acesso.token.expor(), TOKEN_DAVINCI);
	assert!(!cena.terminal.tela().contains(TOKEN_DAVINCI));
	// A outra conta continua sem caixa.
	assert!(chaveiro::ler_davinci(&cena.cofre, Conta::Goslin).unwrap().is_none());

	for (url, caixa, token) in [
		("http://davinci.exemplo", CAIXA, TOKEN_DAVINCI),
		("davinci.exemplo", CAIXA, TOKEN_DAVINCI),
		("https://davinci.exemplo", "../../outra", TOKEN_DAVINCI),
		("https://davinci.exemplo", "", TOKEN_DAVINCI),
		("https://davinci.exemplo", CAIXA, "curto"),
		("https://davinci.exemplo", CAIXA, "   "),
	] {
		let mut cena = Cena::nova("configurar-ruim", &tuta.url, &[url, caixa, token]);
		assert_eq!(comandos::configurar(&mut cena.ambiente(&ValidaSempre)), 1, "{url} {caixa}");
		assert!(chaveiro::ler_davinci(&cena.cofre, Conta::Geral).unwrap().is_none());
	}
}

#[tokio::test]
async fn cada_conta_tem_a_sua_sessao_e_a_goslin_pergunta_o_email() {
	let (tuta, estado) = tuta_falso(ConfigTuta::default());
	// Goslin: sem e-mail padrão; Enter vazio desiste sem ir ao Tuta.
	let mut cena = Cena::nova("goslin-vazio", &tuta.url, &["", "senha certa do teste"]);
	cena.conta = Conta::Goslin;
	assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 1);
	assert!(tuta.pedidos().is_empty());
	// Com o e-mail, a sessão vai para o item da Goslin com o nome dela.
	let mut cena = Cena::nova("goslin", &tuta.url, &["conta.teste@tuta.com", "senha certa do teste"]);
	cena.conta = Conta::Goslin;
	assert_eq!(comandos::entrar(&mut cena.ambiente(&ValidaSempre)).await, 0, "{}", cena.terminal.tela());
	let sessoes = estado.lock().unwrap().sessoes.clone();
	assert_eq!(sessoes.last().unwrap()["1215"], "DaVinci conector – Mac mini (goslin)");
	assert!(chaveiro::ler_sessao(&cena.cofre, Conta::Goslin).unwrap().is_some());
	assert!(chaveiro::ler_sessao(&cena.cofre, Conta::Geral).unwrap().is_none());
	assert!(cena.cofre.cru(config::CHAVEIRO_SERVICO_TUTA, "sessao:goslin").is_some());
	// Entrar de novo na MESMA conta é recusado; a outra conta é livre.
	let mut cena2 = Cena::nova("goslin-2", &tuta.url, &[]);
	cena2.conta = Conta::Goslin;
	chaveiro::gravar_sessao(&cena2.cofre, Conta::Goslin, &sessao_de_teste()).unwrap();
	assert_eq!(comandos::entrar(&mut cena2.ambiente(&ValidaSempre)).await, 1);
	assert!(cena2.terminal.tela().contains("--conta goslin"));
}
