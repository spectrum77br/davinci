//! tuta-conector — o programa de linha de comando (uso: `tuta-conector ajuda`).
//!
//! Nenhum segredo entra por argumento ou variável de ambiente: senha, TOTP e
//! a chave do agente são perguntados no Terminal; a sessão e a chave ficam no
//! Chaveiro, uma por conta (`--conta geral|goslin`).

use std::process::ExitCode;
use std::sync::Arc;
use tuta_conector::chaveiro::ChaveiroMac;
use tuta_conector::comandos::{self, Ambiente, ValidarComSdk};
use tuta_conector::config::{self, Conta};
use tuta_conector::envio::{DependenciasEnvio, Enviador};
use tuta_conector::estado::Compartilhado;
use tuta_conector::estado_local::pasta_padrao;
use tuta_conector::rede::{ClienteRest, Politica};
use tuta_conector::terminal::TerminalReal;
use tuta_conector::tuta::canario::{self, Canario};
use tuta_conector::tuta::sessao::Ritmo;
use tuta_conector::{leitura, registro, servico};
use tutasdk::bindings::rest_client::RestClient;

const AJUDA: &str = "\
tuta-conector — conector do DaVinci com as contas do Tuta (Central de e-mail)

Toda conta é separada: `--conta geral` (061083.jf@tuta.com) ou `--conta goslin`.

  entrar --conta X        cria a sessão \"DaVinci conector – Mac mini (X)\" (pede
                          e-mail, senha e o código do autenticador) e guarda no Chaveiro
  sair --conta X [--forcar]
                          encerra a sessão NO TUTA e apaga do Chaveiro
  estado --conta X        sessão, caixa da Central, último sinal, diário de envio
                          (sem segredo, sem rede)
  configurar --conta X    grava a URL do DaVinci, o id da caixa na Central e a
                          chave do agente dessa caixa no Chaveiro
  ler --conta X [--uma-volta] [--contar] [--seco]
                          só a leitura (sem websocket) entregue à Central;
                          --uma-volta: uma volta e sai; --contar: só os ids por
                          pasta (nada de conteúdo); --seco: lê os 3 mais novos de
                          cada pasta e NÃO manda nada ao DaVinci (teste depois de `entrar`)
  enviar --conta X        UMA volta de envio (a fila da Central; precisa das duas
                          chaves: a da caixa no DaVinci e \"envio\" no chaves.json)
  rodar --conta X [--contar]
                          o serviço (o que o LaunchAgent roda): leitura + envio
  canario                 pergunta ao Tuta se ainda aceita esta versão (sem senha)
  versao                  versão do conector e do SDK
";

fn rest_do_tuta() -> Result<Arc<dyn RestClient>, String> {
	let politica =
		Politica::tuta(config::TUTA_URL, config::TUTA_SUFIXOS_BLOB).map_err(|e| format!("endereço do Tuta: {e}"))?;
	ClienteRest::nativo(politica, config::TUTA_TEMPO_MAX_PEDIDO)
		.map(|c| Arc::new(c) as Arc<dyn RestClient>)
		.map_err(|_| "não consegui montar o cliente HTTPS".to_owned())
}

/// `--conta X` (ou `--conta=X`) dos argumentos; o resto volta sem ele.
fn separar_conta(argumentos: &[String]) -> Result<(Option<Conta>, Vec<String>), String> {
	let mut conta = None;
	let mut resto = Vec::new();
	let mut i = 0;
	while i < argumentos.len() {
		let a = &argumentos[i];
		let valor = if a == "--conta" {
			i += 1;
			Some(argumentos.get(i).cloned().ok_or("--conta precisa de geral ou goslin")?)
		} else {
			a.strip_prefix("--conta=").map(str::to_owned)
		};
		match valor {
			Some(v) => {
				conta = Some(Conta::de(&v).ok_or_else(|| format!("conta desconhecida: {v} (use geral ou goslin)"))?);
			},
			None => resto.push(a.clone()),
		}
		i += 1;
	}
	Ok((conta, resto))
}

#[tokio::main]
async fn main() -> ExitCode {
	registro::iniciar(log::LevelFilter::Info);
	registro::instalar_gancho_de_panico();

	let argumentos: Vec<String> = std::env::args().skip(1).collect();
	let comando = argumentos.first().map(String::as_str).unwrap_or("ajuda").to_owned();
	let (conta, opcoes) = match separar_conta(argumentos.get(1..).unwrap_or(&[])) {
		Ok(x) => x,
		Err(e) => {
			eprintln!("{e}\n\n{AJUDA}");
			return ExitCode::from(2);
		},
	};
	let opcoes: Vec<&str> = opcoes.iter().map(String::as_str).collect();
	let permitidas: &[&str] = match comando.as_str() {
		"sair" => &["--forcar"],
		"ler" => &["--uma-volta", "--contar", "--seco"],
		"rodar" => &["--contar"],
		_ => &[],
	};
	if let Some(estranha) = opcoes.iter().find(|o| !permitidas.contains(o)) {
		eprintln!("opção desconhecida para `{comando}`: {estranha}\n\n{AJUDA}");
		return ExitCode::from(2);
	}

	match comando.as_str() {
		"ajuda" | "--help" | "-h" => {
			print!("{AJUDA}");
			return ExitCode::SUCCESS;
		},
		"versao" | "--version" => {
			println!(
				"tuta-conector {} — SDK oficial do Tuta {} ({}, {})",
				config::VERSAO_CONECTOR,
				config::versao_sdk(),
				config::SDK_TAG,
				config::SDK_COMMIT
			);
			return ExitCode::SUCCESS;
		},
		"canario" => {
			let rest = match rest_do_tuta() {
				Ok(r) => r,
				Err(e) => {
					eprintln!("{e}");
					return ExitCode::FAILURE;
				},
			};
			let resultado = canario::conferir_versao(config::TUTA_URL, &rest, config::versao_sdk()).await;
			let texto = match resultado {
				Canario::Aceita => "o Tuta aceita esta versão do SDK".to_owned(),
				Canario::Recusada => "o Tuta RECUSA esta versão (474): atualize o conector".to_owned(),
				Canario::Indefinido(s) => format!("resposta {s}: tente de novo mais tarde"),
				Canario::SemRede => "sem conexão com o Tuta".to_owned(),
			};
			println!("Canário ({}): {texto}", config::versao_sdk());
			return if resultado == Canario::Aceita {
				ExitCode::SUCCESS
			} else {
				ExitCode::FAILURE
			};
		},
		"entrar" | "sair" | "estado" | "configurar" | "ler" | "enviar" | "rodar" => {},
		outro => {
			eprintln!("comando desconhecido: {outro}\n\n{AJUDA}");
			return ExitCode::from(2);
		},
	}
	let Some(conta) = conta else {
		eprintln!("`{comando}` precisa de --conta geral ou --conta goslin\n\n{AJUDA}");
		return ExitCode::from(2);
	};

	match comando.as_str() {
		"ler" => {
			let opcoes = leitura::OpcoesLeitura {
				uma_volta: opcoes.contains(&"--uma-volta"),
				contar: opcoes.contains(&"--contar"),
				seco: opcoes.contains(&"--seco"),
			};
			return match leitura::executar(conta, opcoes).await {
				Ok(resumo) => {
					let prefixo = if opcoes.seco { "Leitura a seco (nada foi ao DaVinci)" } else { "Volta" };
					println!("[{conta}] {prefixo}: {resumo}");
					ExitCode::SUCCESS
				},
				Err(e) => {
					eprintln!("[{conta}] {e}");
					ExitCode::from(2)
				},
			};
		},
		"rodar" => {
			return match servico::rodar(conta, opcoes.contains(&"--contar")).await {
				Ok(()) => ExitCode::SUCCESS,
				Err(e) => {
					eprintln!("[{conta}] {e}");
					ExitCode::from(2)
				},
			};
		},
		"enviar" => return enviar_uma_vez(conta).await,
		_ => {},
	}

	let pasta_local = match pasta_padrao(conta) {
		Ok(p) => p,
		Err(e) => {
			eprintln!("não achei a pasta do usuário: {e}");
			return ExitCode::FAILURE;
		},
	};
	let rest_tuta = match rest_do_tuta() {
		Ok(r) => r,
		Err(e) => {
			eprintln!("{e}");
			return ExitCode::FAILURE;
		},
	};
	let validar = ValidarComSdk {
		base: config::TUTA_URL.to_owned(),
		rest: rest_tuta.clone(),
	};
	let cofre = ChaveiroMac;
	let mut terminal = TerminalReal;
	let mut amb = Ambiente {
		conta,
		cofre: &cofre,
		terminal: &mut terminal,
		pasta_local,
		tuta_url: config::TUTA_URL.to_owned(),
		rest_tuta,
		validar: &validar,
		ritmo_2fa: Ritmo::default(),
	};
	let codigo = match comando.as_str() {
		"entrar" => comandos::entrar(&mut amb).await,
		"sair" => comandos::sair(&mut amb, opcoes.contains(&"--forcar")).await,
		"estado" => comandos::estado(&mut amb),
		"configurar" => comandos::configurar(&mut amb),
		_ => 2,
	};
	ExitCode::from(u8::try_from(codigo).unwrap_or(1))
}

/// `enviar`: um sinal (para saber a chave da caixa) e UMA volta de envio.
async fn enviar_uma_vez(conta: Conta) -> ExitCode {
	let compartilhado = Arc::new(Compartilhado::default());
	let deps = match leitura::dependencias(conta, false, compartilhado.clone()) {
		Ok(d) => d,
		Err(e) => {
			eprintln!("[{conta}] {e}");
			return ExitCode::from(2);
		},
	};
	let _trava = match leitura::travar(&deps.pasta_local) {
		Ok(t) => t,
		Err(e) => {
			eprintln!("[{conta}] {e}");
			return ExitCode::from(2);
		},
	};
	let Some(davinci) = deps.davinci.clone() else {
		return ExitCode::from(2);
	};
	let mut enviador = Enviador::novo(DependenciasEnvio {
		conta,
		cofre: deps.cofre.clone(),
		tuta_url: deps.tuta_url.clone(),
		rest_tuta: deps.rest_tuta.clone(),
		davinci,
		pasta_local: deps.pasta_local.clone(),
		relogio: deps.relogio.clone(),
		compartilhado: compartilhado.clone(),
	});
	// O primeiro sinal diz à Central se este Mac pode enviar e traz a chave da caixa.
	let _ = enviador.uma_volta().await;
	let mut leitor = leitura::Leitor::novo(
		deps,
		leitura::OpcoesLeitura {
			uma_volta: true,
			contar: true,
			seco: false,
		},
	);
	let _ = leitor.pulsar().await;
	match enviador.uma_volta().await {
		Ok(resumo) => {
			println!("[{conta}] Envio: {resumo}");
			ExitCode::SUCCESS
		},
		Err(e) => {
			eprintln!("[{conta}] {e}");
			ExitCode::from(2)
		},
	}
}
