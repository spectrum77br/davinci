//! Travas por TEXTO (decisão 7), como o teste do robô no DaVinci:
//!
//! 1. O código de LEITURA (src/leitura.rs e src/leitura/**) não pode citar
//!    NADA que escreva no Tuta — nem em comentário. Se alguém precisar, o
//!    lugar é o módulo de envio, separado.
//! 2. Nenhum websocket/event bus em lugar nenhum: nem no nosso código, nem no
//!    SDK montado em vendor/, nem no Cargo.lock (assim o conector nunca vira
//!    "líder" e não segura as regras de pasta da equipe).

use std::fs;
use std::path::{Path, PathBuf};

const RAIZ: &str = env!("CARGO_MANIFEST_DIR");

/// O que escreve no Tuta (nomes do SDK, dos serviços e do nosso código).
const PROIBIDOS_NA_LEITURA: &[&str] = &[
	// SDK: entidades e mails
	"update_instance",
	"create_instance",
	"erase_element",
	"erase_list_element",
	"update(",
	"set_unread_status",
	"set_unread",
	"UnreadMailState",
	"move_mails",
	"MoveMail",
	"SimpleMoveMail",
	"delete",
	"erase",
	"DraftService",
	"SendDraftService",
	"DraftCreateData",
	"SendDraftData",
	"ProcessInbox",
	"ReportMail",
	// Ler o `replyType` do Mail (vai no pacote como "respondido") é só
	// leitura; ESCREVER nele (atribuição ou Mail montado com ele) não.
	"replyType =",
	"replyType:",
	"replyType=",
	"trash_mails",
	"archive_mails",
	"simple_move",
	"UpdateSessionKeys",
	"OwnerEncSessionKeysUpdate",
	"encrypt_and_map",
	"encrypt_and_upload",
	"request_blob_facade_write_token",
	"request_write_token",
	"post::<",
	"put::<",
	"delete::<",
	"HttpMethod::POST",
	"HttpMethod::PUT",
	"HttpMethod::DELETE",
	"get_service_executor",
	// Sessão
	"CloseSession",
	"encerrar_sessao",
	"criar_sessao",
	"SessionService",
	"SecondFactorAuthService",
	// Nosso envio
	"crate::envio",
	"envio::",
	"marcar_respondido",
];

/// Sinais de websocket/event bus no CÓDIGO (os comentários dizem "websocket"
/// em minúsculas para explicar que NÃO usamos; por isso estes são os nomes
/// de código, bibliotecas e caminhos).
const SINAIS_DE_WEBSOCKET: &[&str] = &[
	"tungstenite",
	"WebSocket",
	"Websocket(",
	"wss://",
	"ws://",
	"/event?",
	"EventBus",
	"event_bus",
	"leaderStatus",
	"websocket_client",
];

fn arquivos_rs(pasta: &Path, saida: &mut Vec<PathBuf>) {
	if let Ok(itens) = fs::read_dir(pasta) {
		for item in itens.flatten() {
			let caminho = item.path();
			if caminho.is_dir() {
				arquivos_rs(&caminho, saida);
			} else if caminho.extension().is_some_and(|e| e == "rs") {
				saida.push(caminho);
			}
		}
	}
}

fn codigo_da_leitura() -> Vec<(PathBuf, String)> {
	let raiz = Path::new(RAIZ).join("src");
	let mut arquivos = vec![raiz.join("leitura.rs")];
	arquivos_rs(&raiz.join("leitura"), &mut arquivos);
	arquivos
		.into_iter()
		.filter(|a| a.exists())
		.map(|a| {
			let texto = fs::read_to_string(&a).unwrap();
			(a, texto)
		})
		.collect()
}

/// Acha os proibidos num texto (sem diferenciar maiúsculas).
fn achados(texto: &str, proibidos: &[&str]) -> Vec<String> {
	let minusculo = texto.to_lowercase();
	proibidos
		.iter()
		.filter(|p| minusculo.contains(&p.to_lowercase()))
		.map(|p| (*p).to_owned())
		.collect()
}

#[test]
fn leitura_nao_cita_nada_que_escreve_no_tuta() {
	let codigo = codigo_da_leitura();
	assert!(!codigo.is_empty(), "src/leitura.rs sumiu?");
	for (arquivo, texto) in codigo {
		let ruins = achados(&texto, PROIBIDOS_NA_LEITURA);
		assert!(
			ruins.is_empty(),
			"{} cita o que escreve no Tuta: {ruins:?} (isso vai para o módulo de envio)",
			arquivo.display()
		);
	}
}

#[test]
fn a_trava_pega_de_verdade() {
	// Mutação de teste: se a leitura chamasse estas coisas, o teste acima falharia.
	for linha in [
		"client.update_instance(mail).await?;",
		"mail_facade.set_unread_status_for_mails(ids, false)",
		"executor.post::<SendDraftService>(dados, params)",
		"use crate::envio::executar;",
		"sdk.get_service_executor().delete::<X>(y)",
		"tuta_conector::tuta::sessao::ClienteLogin::encerrar_sessao",
		"mail.replyType = 1;",
		"let m = Mail { replyType: 1, ..mail };",
		"sdk.mail_facade().trash_mails(vec![id]).await",
		"executor.post::<UpdateSessionKeysService>(chaves, p)",
	] {
		assert!(!achados(linha, PROIBIDOS_NA_LEITURA).is_empty(), "{linha}");
	}
	for linha in [
		"load_range::<MailSetEntry>(&lista, &inicio, 100, DESC)",
		"\"respondido\": m.replyType.to_string(),",
		"self.estado.anexos_pendentes.retain(|p| !mesma(p));",
	] {
		assert!(achados(linha, PROIBIDOS_NA_LEITURA).is_empty(), "{linha}");
	}
}

#[test]
fn nenhum_websocket_no_nosso_codigo() {
	let mut arquivos = Vec::new();
	arquivos_rs(&Path::new(RAIZ).join("src"), &mut arquivos);
	assert!(arquivos.len() > 5);
	for arquivo in arquivos {
		let texto = fs::read_to_string(&arquivo).unwrap();
		let ruins: Vec<_> = SINAIS_DE_WEBSOCKET
			.iter()
			.filter(|s| texto.contains(**s))
			.collect();
		assert!(ruins.is_empty(), "{}: {ruins:?}", arquivo.display());
	}
}

#[test]
fn nenhum_websocket_no_sdk_montado_nem_nas_dependencias() {
	let sdk = Path::new(RAIZ).join("vendor/tutanota/tuta-sdk/rust/sdk/src");
	assert!(sdk.exists(), "rode scripts/atualizar-sdk.sh");
	let mut arquivos = Vec::new();
	arquivos_rs(&sdk, &mut arquivos);
	for arquivo in arquivos {
		let texto = fs::read_to_string(&arquivo).unwrap();
		// (O SDK tem o TIPO de dado WebsocketLeaderStatus nos modelos gerados, mas
		// nenhum cliente de websocket: é isso que se procura aqui.)
		for sinal in ["tungstenite", "wss://", "/event?", "EventBus", "event_bus"] {
			assert!(!texto.contains(sinal), "{}: {sinal}", arquivo.display());
		}
	}
	let lock = fs::read_to_string(Path::new(RAIZ).join("Cargo.lock")).unwrap();
	for crate_ws in ["tungstenite", "websocket", "tokio-tungstenite", "async-tungstenite"] {
		assert!(
			!lock.contains(&format!("name = \"{crate_ws}\"")),
			"dependência de websocket no Cargo.lock: {crate_ws}"
		);
	}
}

#[test]
fn envio_e_leitura_sao_modulos_separados() {
	let envio = fs::read_to_string(Path::new(RAIZ).join("src/envio.rs")).unwrap();
	assert!(!envio.contains("crate::leitura"), "o envio não usa a leitura por dentro");
	let lib = fs::read_to_string(Path::new(RAIZ).join("src/lib.rs")).unwrap();
	assert!(lib.contains("pub mod leitura;") && lib.contains("pub mod envio;"));
}
