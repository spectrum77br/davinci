//! Servidores FALSOS para os testes: um Tuta falso e uma Central falsa, em
//! 127.0.0.1, HTTP/1.1 simples, sem nenhuma conta de verdade. O conector fala
//! com eles pelo MESMO cliente HTTP de produção (o do SDK + as travas de rede.rs).
#![allow(dead_code)]

use base64::engine::general_purpose::{STANDARD as BASE64, URL_SAFE_NO_PAD};
use base64::Engine;
use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

// ── Servidor HTTP mínimo ────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct Pedido {
	pub metodo: String,
	pub caminho: String,
	/// A query crua (para conferir que não vai e-mail/segredo na URL).
	pub query_crua: String,
	pub query: HashMap<String, String>,
	/// Nomes em minúsculas.
	pub cabecalhos: HashMap<String, String>,
	pub corpo: Vec<u8>,
}

impl Pedido {
	pub fn json(&self) -> Value {
		serde_json::from_slice(&self.corpo).unwrap_or(Value::Null)
	}
	pub fn body_da_query(&self) -> Value {
		self.query
			.get("_body")
			.and_then(|b| serde_json::from_str(b).ok())
			.unwrap_or(Value::Null)
	}
}

pub struct Resposta {
	pub status: u16,
	pub cabecalhos: Vec<(String, String)>,
	pub corpo: Vec<u8>,
}

impl Resposta {
	pub fn json(status: u16, v: Value) -> Self {
		Self {
			status,
			cabecalhos: vec![("Content-Type".into(), "application/json".into())],
			corpo: serde_json::to_vec(&v).unwrap(),
		}
	}
	pub fn vazia(status: u16) -> Self {
		Self {
			status,
			cabecalhos: vec![],
			corpo: vec![],
		}
	}
	pub fn com_cabecalho(mut self, nome: &str, valor: &str) -> Self {
		self.cabecalhos.push((nome.into(), valor.into()));
		self
	}
}

type Manipulador = dyn Fn(&Pedido) -> Resposta + Send + Sync;

pub struct ServidorFalso {
	pub url: String,
	pub pedidos: Arc<Mutex<Vec<Pedido>>>,
	parar: Arc<AtomicBool>,
}

impl Drop for ServidorFalso {
	fn drop(&mut self) {
		self.parar.store(true, Ordering::SeqCst);
	}
}

impl ServidorFalso {
	pub fn iniciar(manipulador: impl Fn(&Pedido) -> Resposta + Send + Sync + 'static) -> Self {
		let ouvinte = TcpListener::bind("127.0.0.1:0").unwrap();
		let url = format!("http://127.0.0.1:{}", ouvinte.local_addr().unwrap().port());
		ouvinte.set_nonblocking(true).unwrap();
		let pedidos = Arc::new(Mutex::new(Vec::new()));
		let parar = Arc::new(AtomicBool::new(false));
		let manipulador: Arc<Manipulador> = Arc::new(manipulador);
		{
			let pedidos = pedidos.clone();
			let parar = parar.clone();
			std::thread::spawn(move || {
				while !parar.load(Ordering::SeqCst) {
					match ouvinte.accept() {
						Ok((conexao, _)) => {
							let pedidos = pedidos.clone();
							let manipulador = manipulador.clone();
							std::thread::spawn(move || atender(conexao, &*manipulador, &pedidos));
						},
						Err(_) => std::thread::sleep(Duration::from_millis(3)),
					}
				}
			});
		}
		Self { url, pedidos, parar }
	}

	pub fn pedidos(&self) -> Vec<Pedido> {
		self.pedidos.lock().unwrap().clone()
	}

	pub fn pedidos_em(&self, caminho: &str) -> Vec<Pedido> {
		self.pedidos().into_iter().filter(|p| p.caminho == caminho).collect()
	}
}

fn atender(conexao: TcpStream, manipulador: &Manipulador, pedidos: &Mutex<Vec<Pedido>>) {
	conexao.set_nonblocking(false).ok();
	conexao.set_read_timeout(Some(Duration::from_secs(10))).ok();
	let mut leitor = BufReader::new(conexao.try_clone().unwrap());
	let mut primeira = String::new();
	if leitor.read_line(&mut primeira).unwrap_or(0) == 0 {
		return;
	}
	let mut partes = primeira.split_whitespace();
	let metodo = partes.next().unwrap_or_default().to_owned();
	let alvo = partes.next().unwrap_or_default().to_owned();
	let mut cabecalhos = HashMap::new();
	loop {
		let mut linha = String::new();
		if leitor.read_line(&mut linha).unwrap_or(0) == 0 {
			break;
		}
		let linha = linha.trim_end();
		if linha.is_empty() {
			break;
		}
		if let Some((nome, valor)) = linha.split_once(':') {
			cabecalhos.insert(nome.trim().to_ascii_lowercase(), valor.trim().to_owned());
		}
	}
	let tamanho: usize = cabecalhos
		.get("content-length")
		.and_then(|v| v.parse().ok())
		.unwrap_or(0);
	let mut corpo = vec![0; tamanho];
	if tamanho > 0 {
		leitor.read_exact(&mut corpo).unwrap();
	}
	let (caminho, query_crua) = match alvo.split_once('?') {
		Some((c, q)) => (c.to_owned(), q.to_owned()),
		None => (alvo.clone(), String::new()),
	};
	let query = form_urlencoded::parse(query_crua.as_bytes())
		.into_owned()
		.collect();
	let pedido = Pedido {
		metodo,
		caminho,
		query_crua,
		query,
		cabecalhos,
		corpo,
	};
	let resposta = manipulador(&pedido);
	pedidos.lock().unwrap().push(pedido);
	let mut saida = conexao;
	let mut cabeca = format!(
		"HTTP/1.1 {} X\r\nContent-Length: {}\r\nConnection: close\r\n",
		resposta.status,
		resposta.corpo.len()
	);
	for (nome, valor) in &resposta.cabecalhos {
		cabeca.push_str(&format!("{nome}: {valor}\r\n"));
	}
	cabeca.push_str("\r\n");
	let _ = saida.write_all(cabeca.as_bytes());
	let _ = saida.write_all(&resposta.corpo);
	let _ = saida.flush();
}

// ── Tuta falso ──────────────────────────────────────────────────────────

pub const SALT: [u8; 16] = [7; 16];
/// Access token de mentira, mas no formato real (9 bytes do id + o resto).
pub fn token_falso() -> String {
	URL_SAFE_NO_PAD.encode((1u8..=24).collect::<Vec<u8>>())
}
pub const USUARIO: &str = "UsuarioFalso1";

#[derive(Clone)]
pub struct ConfigTuta {
	pub email: String,
	pub senha: String,
	pub kdf: &'static str,
	/// Tipos de desafio (Challenge.type): "1" TOTP, "0" U2F, "2" WebAuthn.
	pub desafios: Vec<&'static str>,
	pub totp: &'static str,
	/// Quantas vezes o GET de segundo fator responde "pendente" depois do código certo.
	pub pendente_vezes: u32,
	pub status_salt: Option<u16>,
	pub status_sessao: Option<u16>,
	pub status_encerrar: u16,
	pub status_canario: u16,
}

impl Default for ConfigTuta {
	fn default() -> Self {
		Self {
			email: "conta.teste@tuta.com".into(),
			senha: "senha certa do teste".into(),
			kdf: "1",
			desafios: vec![],
			totp: "123456",
			pendente_vezes: 1,
			status_salt: None,
			status_sessao: None,
			status_encerrar: 200,
			status_canario: 200,
		}
	}
}

#[derive(Default, Debug)]
pub struct EstadoTuta {
	pub sessoes: Vec<Value>,
	pub totp_aceito: bool,
	pub pendentes_restantes: u32,
	pub cancelada: bool,
	pub encerradas: Vec<Value>,
}

pub fn verificador(senha: &str) -> String {
	let chave = tutasdk::crypto::generate_key_from_passphrase(senha, SALT);
	tutasdk::crypto::crypto_facade::create_auth_verifier(chave)
}

pub fn tuta_falso(cfg: ConfigTuta) -> (ServidorFalso, Arc<Mutex<EstadoTuta>>) {
	let estado = Arc::new(Mutex::new(EstadoTuta {
		pendentes_restantes: cfg.pendente_vezes,
		..EstadoTuta::default()
	}));
	let esperado = verificador(&cfg.senha);
	let token = token_falso();
	let st = estado.clone();
	let servidor = ServidorFalso::iniciar(move |p| {
		let mut estado = st.lock().unwrap();
		match (p.metodo.as_str(), p.caminho.as_str()) {
			("GET", "/rest/sys/saltservice") => {
				if let Some(s) = cfg.status_salt {
					return Resposta::vazia(s);
				}
				if p.body_da_query()["419"] != json!(cfg.email) {
					return Resposta::vazia(401);
				}
				Resposta::json(200, json!({"421": "0", "422": BASE64.encode(SALT), "2133": cfg.kdf}))
			},
			("POST", "/rest/sys/sessionservice") => {
				if let Some(s) = cfg.status_sessao {
					return Resposta::vazia(s);
				}
				let corpo = p.json();
				estado.sessoes.push(corpo.clone());
				if corpo["1213"] != json!(cfg.email) || corpo["1214"] != json!(esperado) {
					return Resposta::vazia(401);
				}
				let desafios: Vec<Value> = cfg
					.desafios
					.iter()
					.enumerate()
					.map(|(i, t)| json!({"1188": format!("d{i}"), "1189": t, "1190": [], "1247": []}))
					.collect();
				Resposta::json(
					200,
					json!({"1220": "0", "1221": token, "1222": desafios, "1223": [USUARIO]}),
				)
			},
			("POST", "/rest/sys/secondfactorauthservice") => {
				let corpo = p.json();
				if corpo["1243"] == json!(cfg.totp) && corpo["1230"] == json!("1") {
					estado.totp_aceito = true;
					Resposta::vazia(200)
				} else {
					Resposta::vazia(401)
				}
			},
			("GET", "/rest/sys/secondfactorauthservice") => {
				if p.body_da_query()["1235"] != json!(token) {
					return Resposta::vazia(401);
				}
				let pendente = if estado.totp_aceito && estado.pendentes_restantes == 0 {
					"0"
				} else {
					if estado.totp_aceito {
						estado.pendentes_restantes -= 1;
					}
					"1"
				};
				Resposta::json(200, json!({"1237": "0", "1238": pendente}))
			},
			("DELETE", "/rest/sys/secondfactorauthservice") => {
				estado.cancelada = true;
				Resposta::vazia(200)
			},
			("POST", "/rest/sys/closesessionservice") => {
				estado.encerradas.push(p.json());
				Resposta::vazia(cfg.status_encerrar)
			},
			("GET", "/rest/base/applicationtypesservice") => Resposta::vazia(cfg.status_canario),
			_ => Resposta::vazia(404),
		}
	});
	(servidor, estado)
}

// ── A Central de e-mail falsa ───────────────────────────────────────────

/// A chave do agente da caixa de teste (a Central confere o Bearer).
pub const TOKEN_DAVINCI: &str = "chave-do-agente-de-teste-1234567890";
/// O id da caixa de teste na Central.
pub const CAIXA: &str = "0b4e6a52-3f1d-4c55-9e0a-2a9c1d7e8f10";

/// Central falsa simples: confere a chave; responde a fila de respostas e,
/// quando ela acaba, o sinal padrão.
pub fn davinci_falso(fila: Vec<Resposta>) -> ServidorFalso {
	let fila = Mutex::new(VecDeque::from(fila));
	ServidorFalso::iniciar(move |p| {
		if p.cabecalhos.get("authorization").map(String::as_str) != Some(&format!("Bearer {TOKEN_DAVINCI}")) {
			return Resposta::json(401, json!({"detail": {"code": "agent_unauthorized"}}));
		}
		if let Some(r) = fila.lock().unwrap().pop_front() {
			return r;
		}
		Resposta::json(200, json!({"ok": true, "send_enabled": false}))
	})
}

// ── Utilidades ──────────────────────────────────────────────────────────

pub fn pasta_temporaria(nome: &str) -> PathBuf {
	let p = std::env::temp_dir().join(format!(
		"tuta-conector-it-{nome}-{}-{:?}",
		std::process::id(),
		std::thread::current().id()
	));
	let _ = std::fs::remove_dir_all(&p);
	p
}

/// Todos os arquivos de uma pasta, juntos (para procurar segredo vazado).
pub fn conteudo_da_pasta(pasta: &PathBuf) -> String {
	let mut tudo = String::new();
	if let Ok(itens) = std::fs::read_dir(pasta) {
		for item in itens.flatten() {
			if let Ok(texto) = std::fs::read_to_string(item.path()) {
				tudo.push_str(&texto);
			}
		}
	}
	tudo
}
pub mod caixa;
pub mod central;
