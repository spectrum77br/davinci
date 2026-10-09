//! Os subcomandos de gente (entrar, sair, estado, configurar), sempre de UMA
//! conta (`--conta geral|goslin`). Tudo o que é externo (Chaveiro, Terminal,
//! Tuta, pasta local) vem de fora, para os testes rodarem contra um Tuta
//! FALSO, um Chaveiro em memória e um Terminal roteirizado.

use crate::chaveiro::{self, AcessoDavinci, Cofre, SessaoTuta};
use crate::config::{self, Conta};
use crate::envio::{pendencias_do_diario, Diario};
use crate::estado_local::{agora_ms, data_legivel, ChavesLocais, EstadoLocal};
use crate::rede::Politica;
use crate::segredo::Segredo;
use crate::terminal::Terminal;
use crate::tuta::sdk::{self, ErroRetomar};
use crate::tuta::sessao::{ClienteLogin, Encerramento, NovaSessao, Ritmo};
use std::path::PathBuf;
use std::sync::Arc;
use tutasdk::bindings::rest_client::RestClient;
use tutasdk::login::Credentials;

/// Confere se a sessão recém-criada abre de verdade (o `Sdk::login` oficial).
// O async_trait marca o método com #[must_use] e o clippy reclama em dobro.
#[allow(clippy::double_must_use)]
#[async_trait::async_trait(?Send)]
pub trait ValidarSessao {
	async fn validar(&self, credenciais: Credentials) -> Result<(), ErroRetomar>;
}

/// A validação de verdade: retoma a sessão com o SDK (só leitura).
pub struct ValidarComSdk {
	pub base: String,
	pub rest: Arc<dyn RestClient>,
}

#[async_trait::async_trait(?Send)]
impl ValidarSessao for ValidarComSdk {
	async fn validar(&self, credenciais: Credentials) -> Result<(), ErroRetomar> {
		let sdk = sdk::novo_sdk(&self.base, self.rest.clone());
		sdk::retomar(&sdk, credenciais).await.map(|_| ())
	}
}

/// Tudo o que os comandos usam de fora.
pub struct Ambiente<'a> {
	/// A conta do Tuta (`--conta`): o item do Chaveiro, a pasta local e o
	/// nome da sessão são dela.
	pub conta: Conta,
	pub cofre: &'a dyn Cofre,
	pub terminal: &'a mut dyn Terminal,
	/// A pasta local DESTA conta.
	pub pasta_local: PathBuf,
	/// "https://app.tuta.com" (ou o Tuta falso).
	pub tuta_url: String,
	/// O cliente HTTP do Tuta, já com a política de destinos.
	pub rest_tuta: Arc<dyn RestClient>,
	pub validar: &'a dyn ValidarSessao,
	pub ritmo_2fa: Ritmo,
}

/// O resultado de um comando: o código de saída do processo.
pub type Saida = i32;

// ── entrar ──────────────────────────────────────────────────────────────

pub async fn entrar(amb: &mut Ambiente<'_>) -> Saida {
	let conta = amb.conta;
	match chaveiro::ler_sessao(amb.cofre, conta) {
		Ok(Some(_)) => {
			amb.terminal.dizer(&format!(
				"Já existe uma sessão do conector no Chaveiro para a conta {conta}. Rode `tuta-conector sair --conta {conta}` antes de entrar de novo."
			));
			return 1;
		},
		Ok(None) => {},
		Err(e) => {
			amb.terminal.dizer(&format!("Não consegui ler o Chaveiro: {e}"));
			return 1;
		},
	}

	let pergunta = match conta.email_padrao() {
		Some(padrao) => format!("E-mail da conta do Tuta ({conta}) [{padrao}]: "),
		None => format!("E-mail da conta do Tuta ({conta}): "),
	};
	let email = match amb.terminal.perguntar(&pergunta) {
		Ok(e) if e.trim().is_empty() => match conta.email_padrao() {
			Some(padrao) => padrao.to_owned(),
			None => {
				amb.terminal.dizer("E-mail vazio: nada feito.");
				return 1;
			},
		},
		Ok(e) => e,
		Err(_) => return 1,
	};
	let senha = match amb.terminal.perguntar_oculto("Senha do Tuta (não aparece na tela): ") {
		Ok(s) if !s.vazio() => s,
		_ => {
			amb.terminal.dizer("Senha vazia: nada feito.");
			return 1;
		},
	};

	let cliente = ClienteLogin::novo(&amb.tuta_url, amb.rest_tuta.clone());
	let ritmo = amb.ritmo_2fa;
	let resultado = {
		// O Terminal é usado por dois "fechos" (código e aviso): um de cada vez.
		let terminal = std::cell::RefCell::new(&mut *amb.terminal);
		let mut pedir_totp = |tentativa: u32| -> Option<String> {
			let texto = if tentativa == 1 {
				"Código do app autenticador (6 números; Enter vazio desiste): ".to_owned()
			} else {
				format!("Código do autenticador, tentativa {tentativa} de {}: ", config::TOTP_TENTATIVAS)
			};
			terminal.borrow_mut().perguntar(&texto).ok()
		};
		let mut avisar = |texto: &str| terminal.borrow_mut().dizer(texto);
		cliente
			.criar_sessao(&email, &senha, &conta.nome_sessao(), &mut pedir_totp, &mut avisar, ritmo)
			.await
	};
	drop(senha);

	let nova = match resultado {
		Ok(n) => n,
		Err(e) => {
			amb.terminal.dizer(&format!("Não entrou: {e}."));
			return 1;
		},
	};

	let sessao = sessao_guardavel(nova);
	if let Err(e) = amb.validar.validar(sessao.credenciais()).await {
		amb.terminal
			.dizer(&format!("A sessão foi criada, mas não abriu ({e}). Vou encerrá-la no Tuta."));
		encerrar_no_tuta(&cliente, &sessao.access_token, amb.terminal).await;
		return 1;
	}

	if let Err(e) = chaveiro::gravar_sessao(amb.cofre, conta, &sessao) {
		amb.terminal
			.dizer(&format!("Não consegui guardar no Chaveiro ({e}). Vou encerrar a sessão no Tuta."));
		encerrar_no_tuta(&cliente, &sessao.access_token, amb.terminal).await;
		return 1;
	}

	let mut estado = EstadoLocal::carregar(&amb.pasta_local);
	estado.sessao_criada_em_ms = Some(sessao.criada_em_ms);
	if let Err(e) = estado.salvar(&amb.pasta_local) {
		log::warn!("não consegui gravar o estado local: {e}");
	}

	amb.terminal.dizer(&format!(
		"Pronto: sessão \"{}\" criada no Tuta e guardada no Chaveiro (serviço \"{}\", item \"{}\").",
		conta.nome_sessao(),
		config::CHAVEIRO_SERVICO_TUTA,
		conta.chaveiro_sessao()
	));
	amb.terminal.dizer(&format!(
		"Para encerrar: `tuta-conector sair --conta {conta}` (ou no Tuta: Configurações › Login › Sessões ativas)."
	));
	0
}

fn sessao_guardavel(nova: NovaSessao) -> SessaoTuta {
	SessaoTuta {
		login: nova.login,
		user_id: nova.user_id,
		access_token: nova.access_token,
		encrypted_passphrase_key: nova.encrypted_passphrase_key,
		criada_em_ms: agora_ms(),
		versao_sdk: config::versao_sdk().to_owned(),
	}
}

async fn encerrar_no_tuta(cliente: &ClienteLogin, token: &Segredo, terminal: &mut dyn Terminal) {
	match cliente.encerrar_sessao(token).await {
		Ok(_) => terminal.dizer("Sessão encerrada no Tuta."),
		Err(e) => terminal.dizer(&format!(
			"Não consegui encerrar ({e}). Encerre à mão no Tuta: Configurações › Login › Sessões ativas › a sessão \"DaVinci conector – Mac mini (…)\"."
		)),
	}
}

// ── sair ────────────────────────────────────────────────────────────────

/// Encerra a sessão NO TUTA e apaga do Chaveiro. Se o Tuta não responder, o
/// item fica (para tentar de novo), a não ser com `forcar`.
pub async fn sair(amb: &mut Ambiente<'_>, forcar: bool) -> Saida {
	let conta = amb.conta;
	let sessao = match chaveiro::ler_sessao(amb.cofre, conta) {
		Ok(Some(s)) => s,
		Ok(None) => {
			amb.terminal.dizer("Não há sessão do conector no Chaveiro.");
			return 0;
		},
		Err(e) => {
			amb.terminal.dizer(&format!("Não consegui ler o Chaveiro: {e}"));
			if forcar {
				return apagar_do_chaveiro(amb);
			}
			return 1;
		},
	};

	let cliente = ClienteLogin::novo(&amb.tuta_url, amb.rest_tuta.clone());
	let encerrou = match cliente.encerrar_sessao(&sessao.access_token).await {
		Ok(Encerramento::Encerrada) => {
			amb.terminal.dizer("Sessão encerrada no Tuta.");
			true
		},
		Ok(Encerramento::JaEncerrada) => {
			amb.terminal.dizer("A sessão já estava encerrada no Tuta.");
			true
		},
		Err(e) => {
			amb.terminal.dizer(&format!("Não consegui encerrar a sessão no Tuta: {e}."));
			false
		},
	};
	drop(sessao);

	if !encerrou && !forcar {
		amb.terminal.dizer(&format!(
			"Mantive o item no Chaveiro para tentar de novo. Ou encerre no Tuta (Configurações › Login › Sessões ativas › \"{}\") e rode `tuta-conector sair --conta {conta} --forcar`.",
			conta.nome_sessao()
		));
		return 1;
	}
	apagar_do_chaveiro(amb)
}

fn apagar_do_chaveiro(amb: &mut Ambiente<'_>) -> Saida {
	match chaveiro::apagar_sessao(amb.cofre, amb.conta) {
		Ok(_) => {
			amb.terminal.dizer("Item da sessão apagado do Chaveiro.");
			let mut estado = EstadoLocal::carregar(&amb.pasta_local);
			estado.sessao_criada_em_ms = None;
			let _ = estado.salvar(&amb.pasta_local);
			0
		},
		Err(e) => {
			amb.terminal.dizer(&format!("Não consegui apagar do Chaveiro: {e}"));
			1
		},
	}
}

// ── estado ──────────────────────────────────────────────────────────────

/// Mostra o estado SEM segredo e sem tocar a rede.
pub fn estado(amb: &mut Ambiente<'_>) -> Saida {
	let conta = amb.conta;
	let t = &mut *amb.terminal;
	t.dizer(&format!("Conector do Tuta (DaVinci) {} — conta {conta}", config::VERSAO_CONECTOR));
	t.dizer(&format!(
		"SDK oficial do Tuta: {} (tag {}, commit {})",
		config::versao_sdk(),
		config::SDK_TAG,
		&config::SDK_COMMIT[..config::SDK_COMMIT.len().min(12)]
	));
	match chaveiro::ler_sessao(amb.cofre, conta) {
		Ok(Some(s)) => t.dizer(&format!(
			"Sessão no Chaveiro: SIM — conta {}, criada em {} (SDK {}), nome no Tuta \"{}\"",
			s.login,
			data_legivel(s.criada_em_ms),
			s.versao_sdk,
			conta.nome_sessao()
		)),
		Ok(None) => t.dizer(&format!("Sessão no Chaveiro: NÃO (rode `tuta-conector entrar --conta {conta}`)")),
		Err(e) => t.dizer(&format!("Sessão no Chaveiro: não consegui ler ({e})")),
	}
	match chaveiro::ler_davinci(amb.cofre, conta) {
		Ok(Some(d)) => t.dizer(&format!("Central de e-mail: {} — caixa {} (chave do agente no Chaveiro)", d.url, d.caixa)),
		Ok(None) => t.dizer(&format!("Central de e-mail: NÃO configurada (rode `tuta-conector configurar --conta {conta}`)")),
		Err(e) => t.dizer(&format!("Central de e-mail: não consegui ler ({e}); rode `tuta-conector configurar --conta {conta}`")),
	}
	let estado = EstadoLocal::carregar(&amb.pasta_local);
	match &estado.ultimo_pulso {
		Some(p) => t.dizer(&format!(
			"Último pulso: {} — estado {}, {}",
			data_legivel(p.em_ms),
			p.estado,
			p.resultado
		)),
		None => t.dizer("Último pulso: nenhum ainda"),
	}
	match estado.ultima_leitura_ok_em_ms {
		Some(ms) => t.dizer(&format!("Última leitura completa: {}", data_legivel(ms))),
		None => t.dizer("Última leitura completa: nenhuma ainda"),
	}
	t.dizer(&format!(
		"Leitura: {} pasta(s) com cursor; {} e-mail(s) que não decifram; {} recusado(s) pela Central; {} só contado(s) (aliases internos)",
		estado.cursores.len(),
		estado.ilegiveis.len(),
		estado.recusados.len(),
		estado.so_contados.len()
	));
	let diario = Diario::carregar(&amb.pasta_local);
	let (interrompidas, incertas, sem_recibo) = pendencias_do_diario(&diario);
	t.dizer(&format!(
		"Envio: {} no diário; {interrompidas} interrompido(s) (conferir no Tuta), {incertas} incerto(s), {sem_recibo} sem recibo na Central",
		diario.tarefas.len()
	));
	match (&estado.canario, estado.ultimo_canario_ms) {
		(Some(c), Some(ms)) => t.dizer(&format!(
			"Canário ({}): o Tuta {} esta versão; releases mais novas: {}",
			data_legivel(ms),
			match c.versao.as_str() {
				"aceita" => "ACEITA",
				"recusada" => "RECUSA (474)",
				_ => "não respondeu sobre",
			},
			c.releases_atras.map_or_else(|| "?".to_owned(), |n| n.to_string())
		)),
		_ => t.dizer("Canário: ainda não rodou (roda dentro do `ler`, 1x por dia)"),
	}
	let chaves = ChavesLocais::carregar(&amb.pasta_local);
	let ligado = |b: bool| if b { "LIGADO" } else { "desligado" };
	t.dizer(&format!(
		"Chaves deste Mac: envio {}, marcar respondido {}",
		ligado(chaves.envio),
		ligado(chaves.marcar_respondido)
	));
	t.dizer("Websocket: nunca (o conector só consulta de tempos em tempos e não vira \"líder\")");
	0
}

// ── configurar ──────────────────────────────────────────────────────────

/// Grava a URL do DaVinci, o id da caixa na Central e a chave do agente
/// dessa caixa (a tela da Central mostra UMA vez) no Chaveiro desta conta.
pub fn configurar(amb: &mut Ambiente<'_>) -> Saida {
	let conta = amb.conta;
	let url = match amb.terminal.perguntar("URL do DaVinci (ex.: https://davinci.suaempresa.com.br): ") {
		Ok(u) => u,
		Err(_) => return 1,
	};
	let base = match Politica::davinci(url.trim()) {
		Ok(Politica::Davinci { base }) => base.base(),
		_ => {
			amb.terminal.dizer("URL inválida: use https://… (http só para 127.0.0.1 em teste).");
			return 1;
		},
	};
	let caixa = match amb.terminal.perguntar(&format!("Id da caixa na Central de e-mail (conta {conta}): ")) {
		Ok(c) => c.trim().to_lowercase(),
		Err(_) => return 1,
	};
	if !chaveiro::caixa_valida(&caixa) {
		amb.terminal.dizer("Id da caixa inválido (é um UUID, como 0b4e6a52-3f1d-4c55-9e0a-2a9c1d7e8f10): nada gravado.");
		return 1;
	}
	let token = match amb.terminal.perguntar_oculto("Chave do agente desta caixa (não aparece na tela): ") {
		Ok(t) if !t.vazio() => Segredo::novo(t.expor().trim().to_owned()),
		_ => {
			amb.terminal.dizer("Chave vazia: nada gravado.");
			return 1;
		},
	};
	if token.expor().len() < 16 || token.expor().len() > 256 {
		amb.terminal.dizer("Chave com tamanho estranho (16 a 256 caracteres): nada gravado.");
		return 1;
	}
	match chaveiro::gravar_davinci(
		amb.cofre,
		conta,
		&AcessoDavinci {
			url: base.clone(),
			caixa: caixa.clone(),
			token,
		},
	) {
		Ok(()) => {
			amb.terminal.dizer(&format!(
				"Gravado no Chaveiro (serviço \"{}\", item \"{}\"): DaVinci {base}, caixa {caixa}. A chave não é mostrada.",
				config::CHAVEIRO_SERVICO_DAVINCI,
				conta.chaveiro_caixa()
			));
			0
		},
		Err(e) => {
			amb.terminal.dizer(&format!("Não consegui gravar no Chaveiro: {e}"));
			1
		},
	}
}
