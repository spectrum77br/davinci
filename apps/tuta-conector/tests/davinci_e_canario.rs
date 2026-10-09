//! Cliente da Central de e-mail (sinal v1, fila v1, v2 nosso, chave, erros
//! do contrato, recuo) contra uma Central FALSA, e o canário de versão contra
//! um Tuta FALSO.

mod comum;

use comum::*;
use serde_json::json;
use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;
use tuta_conector::davinci::{ClienteDavinci, ErroDavinci, Mudanca, Recibo, Sinal, Sincronia};
use tuta_conector::estado::EstadoConector;
use tuta_conector::rede::{ClienteRest, Politica};
use tuta_conector::segredo::Segredo;
use tuta_conector::tuta::canario::{self, Canario};
use tutasdk::bindings::rest_client::RestClient;

fn cliente(url: &str, token: &str) -> ClienteDavinci {
	let rest = ClienteRest::nativo(Politica::davinci(url).unwrap(), Duration::from_secs(10)).unwrap();
	ClienteDavinci::com_cliente(url, CAIXA, Segredo::novo(token.into()), Arc::new(rest)).unwrap()
}

fn sinal() -> Sinal {
	Sinal::de(EstadoConector::Ok, false)
}

#[tokio::test]
async fn sinal_v1_no_formato_da_central() {
	let davinci = davinci_falso(vec![Resposta::json(200, json!({"ok": true, "send_enabled": true}))]);
	let c = cliente(&davinci.url, TOKEN_DAVINCI);
	let r = c.sinal(&Sinal::de(EstadoConector::Ilegivel, true)).await.unwrap();
	assert!(r.send_enabled);
	let pedidos = davinci.pedidos();
	assert_eq!(pedidos.len(), 1);
	let p = &pedidos[0];
	assert_eq!(p.metodo, "POST");
	assert_eq!(p.caminho, format!("/api/mail/agent/{CAIXA}/heartbeat"));
	assert!(p.query_crua.is_empty());
	assert_eq!(p.cabecalhos["content-type"], "application/json");
	// EXATAMENTE o Heartbeat do v1 (a Central recusa campo a mais).
	assert_eq!(p.json(), json!({"state": "online", "can_send": true, "error_code": "ilegivel"}));
	// A chave só no cabeçalho Authorization; nada de e-mail em cabeçalho.
	assert!(!String::from_utf8_lossy(&p.corpo).contains(TOKEN_DAVINCI));
	assert_eq!(p.cabecalhos["authorization"], format!("Bearer {TOKEN_DAVINCI}"));
	for (nome, valor) in &p.cabecalhos {
		assert!(!valor.contains('@'), "e-mail no cabeçalho {nome}");
	}
	// Sessão caída: login_required e nunca "posso enviar".
	let davinci = davinci_falso(vec![]);
	let c = cliente(&davinci.url, TOKEN_DAVINCI);
	c.sinal(&Sinal::de(EstadoConector::SessaoCaiu, true)).await.unwrap();
	assert_eq!(davinci.pedidos()[0].json(), json!({"state": "login_required", "can_send": false, "error_code": "sessao_caiu"}));
}

#[tokio::test]
async fn fila_v1_lease_e_recibo() {
	let tarefa = json!({"id": "11111111-2222-4333-8444-555555555555", "lease_token": "L".repeat(43),
		"from_address": "21max@tuta.com", "to": "cliente@example.com", "subject": "Re: Pedido",
		"text": "Chega amanhã.", "in_reply_to": "<pai@x>", "references": ["<pai@x>"],
		"message_id": "<j@mail.davinci.local>"});
	let davinci = davinci_falso(vec![
		Resposta::json(200, json!({"jobs": [tarefa]})),
		Resposta::json(200, json!({"ok": true, "status": "sent"})),
		Resposta::json(409, json!({"detail": {"code": "receipt_conflict"}})),
	]);
	let c = cliente(&davinci.url, TOKEN_DAVINCI);
	let jobs = c.pegar_tarefas().await.unwrap();
	assert_eq!(jobs.len(), 1);
	assert_eq!(jobs[0].in_reply_to.as_deref(), Some("<pai@x>"));
	let recibo = Recibo {
		lease_token: jobs[0].lease_token.clone(),
		status: "sent",
		message_id: Some("<real@tuta.com>".into()),
		error_code: None,
	};
	c.recibo(&jobs[0].id, &recibo).await.unwrap();
	assert_eq!(c.recibo(&jobs[0].id, &recibo).await.unwrap_err(), ErroDavinci::Conflito("receipt_conflict".into()));
	let pedidos = davinci.pedidos();
	assert_eq!(pedidos[0].caminho, format!("/api/mail/agent/{CAIXA}/outbox/lease"));
	assert_eq!(pedidos[0].json(), json!({}));
	assert_eq!(pedidos[1].caminho, format!("/api/mail/agent/{CAIXA}/outbox/{}/receipt", jobs[0].id));
	assert_eq!(
		pedidos[1].json(),
		json!({"lease_token": "L".repeat(43), "status": "sent", "message_id": "<real@tuta.com>", "error_code": null})
	);
	// Id de tarefa estranho nem sai (vai na URL).
	assert_eq!(c.recibo("../../x", &recibo).await.unwrap_err(), ErroDavinci::Resposta);
	assert_eq!(davinci.pedidos().len(), 3);
}

#[tokio::test]
async fn rotas_v2_e_o_corpo_das_mudancas() {
	let davinci = davinci_falso(vec![
		Resposta::json(200, json!({"contract": 2, "folders": [{"key": "P1", "read": "corpo"}], "count_only_aliases": ["adm@x.com"]})),
		Resposta::json(200, json!({"results": [{"source_id": "tuta:a/b", "status": "accepted"}], "accepted": 1, "duplicates": 0, "rejected": 0})),
		Resposta::json(200, json!({"missing": ["tuta:c/d"], "moved": 1, "left": []})),
		Resposta::json(200, json!({"updated": 1, "unknown": 0})),
		// Servidor velho (sem o v2): 404 do FastAPI, sem código.
		Resposta::json(404, json!({"detail": "Not Found"})),
	]);
	let c = cliente(&davinci.url, TOKEN_DAVINCI);
	let s = c
		.sincronizar(&Sincronia {
			instance: "instancia-1234567".into(),
			agent_version: "x".into(),
			tuta_version: "y".into(),
			counters: BTreeMap::from([("lidos".into(), 1)]),
			folders: None,
			folders_complete: true,
			aliases: None,
		})
		.await
		.unwrap();
	assert_eq!(s.count_only_aliases, vec!["adm@x.com"]);
	let r = c.ingerir(br#"{"messages":[]}"#.to_vec()).await.unwrap();
	assert_eq!(r[0].status, "accepted");
	let n = c.contar(&json!({"folder_key": "P1", "ids": []})).await.unwrap();
	assert_eq!(n.missing, vec!["tuta:c/d"]);
	c.mudancas(&[Mudanca {
		source_id: "tuta:a/b".into(),
		folder_key: None,
		apagado: true,
	}])
	.await
	.unwrap();
	let e = c.contar(&json!({})).await.unwrap_err();
	assert_eq!(e, ErroDavinci::SemV2);
	let pedidos = davinci.pedidos();
	let caminhos: Vec<&str> = pedidos.iter().map(|p| p.caminho.as_str()).collect();
	let base = format!("/api/mail/agent/{CAIXA}/v2");
	assert_eq!(
		caminhos,
		vec![
			format!("{base}/sync"),
			format!("{base}/ingest"),
			format!("{base}/count"),
			format!("{base}/changes"),
			format!("{base}/count")
		]
	);
	assert_eq!(pedidos[0].json()["folders"], json!(null));
	assert_eq!(pedidos[3].json(), json!({"changes": [{"source_id": "tuta:a/b", "deleted": true}]}));
}

#[tokio::test]
async fn erros_do_contrato_viram_erros_claros() {
	let casos = vec![
		(Resposta::json(404, json!({"detail": "Not Found"})), ErroDavinci::SemV2),
		(Resposta::json(404, json!({"detail": {"code": "job_not_found"}})), ErroDavinci::NaoEncontrado("job_not_found".into())),
		(Resposta::json(409, json!({"detail": {"code": "another_agent_active"}})), ErroDavinci::OutroAgente),
		(Resposta::json(409, json!({"detail": {"code": "folder_unknown"}})), ErroDavinci::Conflito("folder_unknown".into())),
		(Resposta::json(401, json!({"detail": {"code": "invalid_lease"}})), ErroDavinci::Conflito("invalid_lease".into())),
		(Resposta::json(413, json!({"detail": {"code": "body_too_large"}})), ErroDavinci::GrandeDemais),
		(
			Resposta::json(422, json!({"detail": {"code": "invalid_body", "fields": [{"field": "state", "type": "literal_error"}]}})),
			ErroDavinci::Invalido(vec!["state".into()]),
		),
		(Resposta::vazia(429).com_cabecalho("Retry-After", "42"), ErroDavinci::Limitado(Duration::from_secs(42))),
		(Resposta::vazia(502), ErroDavinci::Fora(502)),
		(Resposta::json(200, json!({"ok": false, "send_enabled": true})), ErroDavinci::Resposta),
		(Resposta::json(200, json!({"x": 1})), ErroDavinci::Resposta),
	];
	for (resposta, esperado) in casos {
		let davinci = davinci_falso(vec![resposta]);
		let c = cliente(&davinci.url, TOKEN_DAVINCI);
		let erro = c.sinal(&sinal()).await.unwrap_err();
		assert_eq!(erro, esperado);
	}
}

#[tokio::test]
async fn chave_errada_401() {
	let davinci = davinci_falso(vec![]);
	let c = cliente(&davinci.url, "outra-chave-qualquer-123456");
	let erro = c.sinal(&sinal()).await.unwrap_err();
	assert_eq!(erro, ErroDavinci::TokenRecusado);
	assert!(!erro.passageiro());
}

#[tokio::test]
async fn recuo_cresce_com_falhas_passageiras_e_zera_no_sucesso() {
	let davinci = davinci_falso(vec![Resposta::vazia(503), Resposta::vazia(503), Resposta::vazia(500)]);
	let c = cliente(&davinci.url, TOKEN_DAVINCI);
	let mut esperas = Vec::new();
	for _ in 0..3 {
		let erro = c.sinal(&sinal()).await.unwrap_err();
		assert!(erro.passageiro());
		esperas.push(c.proxima_espera());
	}
	assert!(esperas[0] < esperas[1] && esperas[1] < esperas[2], "{esperas:?}");
	c.sinal(&sinal()).await.unwrap();
	assert_eq!(c.recuo.lock().unwrap().falhas(), 0);
}

#[test]
fn caixa_invalida_nem_monta_o_cliente() {
	let rest = ClienteRest::nativo(Politica::davinci("https://davinci.exemplo").unwrap(), Duration::from_secs(5)).unwrap();
	assert!(ClienteDavinci::com_cliente("https://davinci.exemplo", "../x", Segredo::novo("t".into()), Arc::new(rest)).is_err());
}

#[tokio::test]
async fn sem_rede_e_passageiro() {
	let ouvinte = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
	let url = format!("http://127.0.0.1:{}", ouvinte.local_addr().unwrap().port());
	drop(ouvinte);
	let c = cliente(&url, TOKEN_DAVINCI);
	let erro = c.sinal(&sinal()).await.unwrap_err();
	assert_eq!(erro, ErroDavinci::Rede);
	assert!(erro.passageiro());
}

#[tokio::test]
async fn cliente_do_davinci_nao_fala_com_outro_host() {
	// O cliente do DaVinci tem a política do DaVinci: mesmo que alguém monte
	// uma URL do Tuta, o pedido é recusado antes de sair (e o token não vai).
	let tuta = ServidorFalso::iniciar(|_| Resposta::vazia(200));
	let davinci = davinci_falso(vec![]);
	let rest = ClienteRest::nativo(Politica::davinci(&davinci.url).unwrap(), Duration::from_secs(5)).unwrap();
	let r = rest
		.request_binary(
			format!("{}/rest/sys/saltservice", tuta.url),
			tutasdk::bindings::rest_client::HttpMethod::GET,
			tutasdk::bindings::rest_client::RestClientOptions {
				headers: Default::default(),
				body: None,
				suspension_behavior: None,
			},
		)
		.await;
	assert!(r.is_err());
	assert!(tuta.pedidos().is_empty());
	assert_eq!(rest.contadores.recusados_destino.load(std::sync::atomic::Ordering::Relaxed), 1);
}

#[tokio::test]
async fn tempo_esgotado_nao_pendura() {
	// Servidor que aceita e nunca responde.
	let ouvinte = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
	let url = format!("http://127.0.0.1:{}", ouvinte.local_addr().unwrap().port());
	let _segura = std::thread::spawn(move || {
		let _conexoes: Vec<_> = ouvinte.incoming().take(1).collect();
		std::thread::sleep(Duration::from_secs(5));
	});
	let rest: Arc<dyn RestClient> = Arc::new(
		ClienteRest::nativo(Politica::davinci(&url).unwrap(), Duration::from_millis(300)).unwrap(),
	);
	let c = ClienteDavinci::com_cliente(&url, CAIXA, Segredo::novo(TOKEN_DAVINCI.into()), rest).unwrap();
	let inicio = std::time::Instant::now();
	let erro = c.sinal(&sinal()).await.unwrap_err();
	assert_eq!(erro, ErroDavinci::Rede);
	assert!(inicio.elapsed() < Duration::from_secs(3));
}

#[tokio::test]
async fn canario_aceita_recusa_e_indefinido() {
	for (status, esperado) in [
		(200u16, Canario::Aceita),
		(474, Canario::Recusada),
		(503, Canario::Indefinido(503)),
	] {
		let (tuta, _) = tuta_falso(ConfigTuta {
			status_canario: status,
			..ConfigTuta::default()
		});
		let rest: Arc<dyn RestClient> = Arc::new(
			ClienteRest::nativo(Politica::tuta(&tuta.url, &[]).unwrap(), Duration::from_secs(5)).unwrap(),
		);
		let r = canario::conferir_versao(&tuta.url, &rest, tutasdk::CLIENT_VERSION).await;
		assert_eq!(r, esperado);
		let p = &tuta.pedidos_em("/rest/base/applicationtypesservice")[0];
		assert_eq!(p.metodo, "GET");
		assert_eq!(p.cabecalhos["cv"], tutasdk::CLIENT_VERSION);
		assert!(!p.cabecalhos.contains_key("accesstoken"), "canário sem sessão");
		assert!(p.corpo.is_empty());
	}
}


#[tokio::test]
async fn canario_das_releases_no_github() {
	use tuta_conector::tuta::canario::Releases;
	let lista = json!([
		{"tag_name": "tutanota-desktop-release-361.261006.0", "draft": false, "prerelease": false},
		{"tag_name": "tutanota-android-release-361.261006.0", "draft": false, "prerelease": false},
		{"tag_name": "tutanota-desktop-release-362.261013.0", "draft": false, "prerelease": true},
		{"tag_name": "tutanota-desktop-release-363.261020.0", "draft": true, "prerelease": false},
		{"tag_name": "calendar-release-9.9.9", "draft": false, "prerelease": false},
		{"tag_name": "tutanota-desktop-release-361.260929.0", "draft": false, "prerelease": false},
		{"tag_name": "tutanota-release-360.260921.0", "draft": false, "prerelease": false}
	]);
	let github = ServidorFalso::iniciar(move |p| {
		assert_eq!(p.cabecalhos.get("user-agent").map(String::as_str), Some("davinci-tuta-conector"));
		assert!(!p.cabecalhos.contains_key("authorization"), "sem token no GitHub");
		Resposta::json(200, lista.clone())
	});
	let rest: Arc<dyn RestClient> =
		Arc::new(ClienteRest::nativo(Politica::exata(&github.url).unwrap(), Duration::from_secs(5)).unwrap());
	let url = format!("{}/repos/tutao/tutanota/releases?per_page=50", github.url);
	// Só as publicadas e mais novas contam (rascunho e pré-release não; a mesma versão em 2 apps conta 1).
	assert_eq!(
		canario::releases_atras(&url, &rest, "361.260929.0").await,
		Releases::Atras { quantas: 1, mais_nova: Some("361.261006.0".into()) }
	);
	assert_eq!(canario::releases_atras(&url, &rest, "361.261006.0").await, Releases::Atras { quantas: 0, mais_nova: None });
	// GitHub com limite (403) ou resposta estranha: não conclui nada.
	let fora = ServidorFalso::iniciar(|_| Resposta::json(403, json!({"message": "rate limit"})));
	let rest_fora: Arc<dyn RestClient> =
		Arc::new(ClienteRest::nativo(Politica::exata(&fora.url).unwrap(), Duration::from_secs(5)).unwrap());
	assert_eq!(canario::releases_atras(&fora.url, &rest_fora, "361.260929.0").await, Releases::Indefinido);
	// O cliente do canário não fala com outro host.
	assert_eq!(canario::releases_atras(&url, &rest_fora, "361.260929.0").await, Releases::Indefinido);
}
