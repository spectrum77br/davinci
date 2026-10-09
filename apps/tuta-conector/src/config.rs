//! As decisões do conector, cada uma numa constante documentada (o dono
//! confirma depois; mudar aqui é mudar a decisão). Nada aqui é segredo: o
//! token do DaVinci e a sessão do Tuta ficam SÓ no Chaveiro (chaveiro.rs).

use std::time::Duration;

// ── Tuta ────────────────────────────────────────────────────────────────

/// O mesmo endereço do app oficial. Não é configurável de fora: o conector só
/// fala com o Tuta de verdade aqui (os testes passam um Tuta falso pela API).
pub const TUTA_URL: &str = "https://app.tuta.com";

/// Além do TUTA_URL, só os servidores de arquivos (blob) que o PRÓPRIO Tuta
/// indica na resposta do BlobAccessTokenService (ex.: w1.api.tuta.com). Pedido
/// para qualquer outro host é recusado pelo cliente HTTP (rede.rs).
pub const TUTA_SUFIXOS_BLOB: &[&str] = &[".tuta.com"];

// ── As contas do Tuta (08/10/2026: um processo por conta) ───────────────

/// Uma conta do Tuta lida por este Mac. Cada conta tem a SUA sessão no Tuta,
/// a SUA caixa na Central (id + chave do agente), a sua pasta local e o seu
/// LaunchAgent: uma sessão caída, um 429 ou um pânico do SDK numa conta não
/// para a outra.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Conta {
	/// 061083.jf@tuta.com: o principal + 54 aliases, ~40 pastas (as lojas).
	Geral,
	/// goslin@tuta.com: a conta privada que também recebe algumas lojas.
	Goslin,
}

impl Conta {
	pub const TODAS: [Conta; 2] = [Conta::Geral, Conta::Goslin];

	/// `--conta geral|goslin`.
	#[must_use]
	pub fn de(texto: &str) -> Option<Self> {
		match texto.trim().to_ascii_lowercase().as_str() {
			"geral" => Some(Self::Geral),
			"goslin" => Some(Self::Goslin),
			_ => None,
		}
	}

	#[must_use]
	pub fn apelido(self) -> &'static str {
		match self {
			Self::Geral => "geral",
			Self::Goslin => "goslin",
		}
	}

	/// O e-mail que o `entrar` sugere (Enter aceita). Na Goslin o dono digita.
	#[must_use]
	pub fn email_padrao(self) -> Option<&'static str> {
		match self {
			Self::Geral => Some("061083.jf@tuta.com"),
			Self::Goslin => None,
		}
	}

	/// O nome que aparece no Tuta em Configurações › Login › Sessões ativas.
	/// É por ele que o dono acha e encerra a sessão do conector desta conta.
	#[must_use]
	pub fn nome_sessao(self) -> String {
		format!("DaVinci conector – Mac mini ({})", self.apelido())
	}

	/// Item do Chaveiro com a sessão do Tuta desta conta.
	#[must_use]
	pub fn chaveiro_sessao(self) -> String {
		format!("sessao:{}", self.apelido())
	}

	/// Item do Chaveiro com a caixa da Central desta conta (URL, id, chave).
	#[must_use]
	pub fn chaveiro_caixa(self) -> String {
		format!("caixa:{}", self.apelido())
	}

	/// O LaunchAgent desta conta (launchd/, não instalado).
	#[must_use]
	pub fn rotulo_launchd(self) -> String {
		format!("com.davinci.tuta-conector.{}", self.apelido())
	}
}

impl std::fmt::Display for Conta {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		f.write_str(self.apelido())
	}
}

/// Tentativas de código TOTP antes de desistir (e cancelar a sessão pendente).
pub const TOTP_TENTATIVAS: u32 = 3;

/// Quanto esperar o Tuta confirmar o segundo fator depois do código certo.
pub const SEGUNDO_FATOR_ESPERA_MAX: Duration = Duration::from_secs(120);

/// Pausa entre as consultas "o segundo fator já foi aceito?".
pub const SEGUNDO_FATOR_INTERVALO: Duration = Duration::from_secs(1);

/// Teto de tempo de UM pedido HTTP ao Tuta (o cliente do SDK não tem teto).
pub const TUTA_TEMPO_MAX_PEDIDO: Duration = Duration::from_secs(60);

// ── Chaveiro do macOS (decisões 4 e 5) ──────────────────────────────────

/// Sessão do Tuta: user_id + access_token + encrypted_passphrase_key (+ e-mail),
/// um item por conta ("sessao:geral", "sessao:goslin").
/// Nunca em arquivo, log, argumento de linha de comando ou variável de ambiente.
pub const CHAVEIRO_SERVICO_TUTA: &str = "davinci-tuta-conector";

/// A caixa da Central de cada conta (URL do DaVinci, id da caixa e a chave do
/// agente mostrada UMA vez na tela), gravada por `tuta-conector configurar`.
pub const CHAVEIRO_SERVICO_DAVINCI: &str = "davinci-tuta-conector-davinci";

// ── DaVinci: a Central de e-mail (contrato v1 dele + o v2 nosso) ─────────

/// As rotas do agente da caixa: v1 (`/heartbeat`, `/outbox/lease`,
/// `/outbox/{job}/receipt`) e v2 (`/v2/sync`, `/v2/ingest`, `/v2/count`,
/// `/v2/changes`). `{caixa}` é o id da caixa na Central.
#[must_use]
pub fn prefixo_davinci(caixa: &str) -> String {
	format!("/api/mail/agent/{caixa}")
}

/// Teto de tempo de UM pedido HTTP ao DaVinci.
pub const DAVINCI_TEMPO_MAX_PEDIDO: Duration = Duration::from_secs(30);

/// Recuo (backoff) quando o DaVinci ou o Tuta mandam esperar ou estão fora:
/// dobra a cada falha seguida, de RECUO_INICIAL até RECUO_MAXIMO, com sorteio
/// de até 20% para dois processos não baterem juntos.
pub const RECUO_INICIAL: Duration = Duration::from_secs(5);
pub const RECUO_MAXIMO: Duration = Duration::from_secs(15 * 60);

// ── Leitura (decisão 6) ─────────────────────────────────────────────────

/// O intervalo entre duas voltas de leitura (sempre na faixa de 60–120 s).
pub const LEITURA_INTERVALO: Duration = Duration::from_secs(90);
pub const LEITURA_INTERVALO_MIN: Duration = Duration::from_secs(60);
pub const LEITURA_INTERVALO_MAX: Duration = Duration::from_secs(120);

/// A primeira carga de uma pasta lê os últimos N dias (nada de pendência de
/// meses atrás); a varredura funda confere os últimos 30.
pub const PRIMEIRA_CARGA_DIAS: u64 = 7;
pub const VARREDURA_FUNDA_DIAS: u64 = 30;

/// A Entrada com `processNeeded` (as regras de pasta da equipe ainda vão
/// mover o e-mail) espera até isto antes de ser lida.
pub const ESPERA_REGRA: Duration = Duration::from_secs(15 * 60);

/// Varredura funda (confere pastas além dos ids novos) de hora em hora.
/// É feita UMA pasta por volta, em rodízio, para não juntar pedidos.
pub const VARREDURA_FUNDA_A_CADA: Duration = Duration::from_secs(60 * 60);

/// O sinal (heartbeat v1) a cada 60 s: a Central bloqueia o envio e mostra
/// "desconectado" depois de 3 min sem sinal.
pub const PULSO_INTERVALO: Duration = Duration::from_secs(60);

/// Com o envio ligado, entre as voltas a fila de respostas é conferida neste
/// ritmo (o lease v1 da Central; nada sai sem clique de pessoa lá).
pub const ENVIO_CONFERIR_A_CADA: Duration = Duration::from_secs(20);

/// Quantas entradas (MailSetEntry) a volta lê do TOPO de cada pasta: são os
/// "novos" acima do cursor e a janela que vai para a /v2/count (movidos,
/// apagados e o que faltar no DaVinci). Um pedido por pasta por volta.
pub const PAGINA_TOPO: usize = 100;

/// Página da listagem só de ids (varredura funda, conciliação do dia). O
/// Tuta aceita até 1000 por pedido.
pub const PAGINA_IDS: usize = 1000;

/// Quantos "saíram da pasta" (a contagem disse) o conector confere por volta.
pub const SAIDOS_POR_VOLTA: usize = 50;

/// Teto de páginas por pasta numa volta (para achar o cursor ou o começo da
/// janela). Passou disso, a volta segue com o que leu e continua na próxima.
pub const PAGINAS_MAX_POR_PASTA: usize = 50;

/// E-mails carregados ao mesmo tempo (cada um são 2 a 5 pedidos ao Tuta).
pub const LEITURA_CONCORRENCIA: usize = 4;

/// Teto de e-mails novos entregues por pasta numa volta (a primeira carga
/// de 7 dias vai em várias voltas, sem segurar o pulso).
pub const EMAILS_MAX_POR_PASTA_VOLTA: usize = 300;

/// Ilegível (não decifra, pânico do SDK): tenta de novo depois de 1 h, 6 h e
/// daí uma vez por dia. Fica CONTADO no pulso até sair.
pub const ILEGIVEL_ESPERAS: [Duration; 3] = [
	Duration::from_secs(60 * 60),
	Duration::from_secs(6 * 60 * 60),
	Duration::from_secs(24 * 60 * 60),
];

/// Recusado pelo DaVinci (`erro_dado`): não reenvia; tenta de novo 1x por dia
/// (pode ter sido corrigido lá).
pub const RECUSADO_ESPERA: Duration = Duration::from_secs(24 * 60 * 60);

/// Teto dos registros locais de ilegíveis e recusados (só ids).
pub const REGISTRO_MAX_ITENS: usize = 5000;

/// Um e-mail que volta `erro_passageiro` tantas vezes seguidas vira
/// "recusado" (tenta 1x por dia): não segura o cursor da pasta para sempre.
pub const PASSAGEIRO_MAX_SEGUIDAS: u32 = 10;

/// A sessão do SDK (Session + User + chaves) é reaberta de tempos em tempos
/// (uma troca de chave do grupo na conta passa a valer sem reiniciar).
pub const CAIXA_REABRIR_A_CADA: Duration = Duration::from_secs(6 * 60 * 60);

/// Anexo perigoso (executável, script, página, compactado): não é baixado
/// nem enviado à Central; fica só no rastro (`omitted_attachments`, motivo
/// "perigoso"). A mesma lista que o lado DaVinci do Tuta usava.
pub const ANEXO_EXTENSOES_PERIGOSAS: &[&str] = &[
	".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".msi", ".js", ".jse", ".vbs", ".vbe", ".wsf",
	".ps1", ".jar", ".html", ".htm", ".hta", ".svg", ".zip", ".rar", ".7z", ".iso", ".img", ".docm",
	".xlsm", ".pptm", ".dotm", ".xlam", ".lnk", ".reg", ".apk", ".dmg",
];
pub const ANEXO_TIPOS_PERIGOSOS: &[&str] =
	&["x-msdownload", "javascript", "x-sh", "html", "zip", "x-rar", "x-7z"];

/// Os tetos do contrato da Central (schemas/mail.py e mail_v2.py): o que
/// passar é cortado AQUI (a Central recusaria o e-mail).
/// O corpo do Tuta (HTML) lido até isto antes de virar texto.
pub const CORPO_HTML_MAX_CARACTERES: usize = 3 * 1024 * 1024;
/// O texto que vai (`text`, 2 MiB caracteres na Central).
pub const TEXTO_MAX_CARACTERES: usize = 2 * 1024 * 1024 - 64;
/// Os cabeçalhos de autenticação que vão (`raw_headers`, 256 KiB).
pub const CABECALHOS_MAX_CARACTERES: usize = 200_000;
/// Assunto, Message-ID e cada Reference: um cabeçalho de no máximo 998.
pub const ASSUNTO_MAX_CARACTERES: usize = 998;
pub const MESSAGE_ID_MAX_CARACTERES: usize = 998;
pub const REFERENCIAS_MAX: usize = 50;
pub const NOME_MAX_CARACTERES: usize = 256;
pub const ENDERECO_MAX_CARACTERES: usize = 254;
/// Para e Cc: até 100 cada.
pub const ENDERECOS_MAX: usize = 100;
/// delivered_to: até 10.
pub const ENTREGUE_A_MAX: usize = 10;
/// A pasta (`folder`) até 128.
pub const PASTA_MAX_CARACTERES: usize = 128;
/// Anexos: até 10 por e-mail, 10 MiB cada (a Central confere depois do
/// base64), e o e-mail inteiro precisa caber no pedido de 25 MiB.
pub const ANEXOS_POR_EMAIL: usize = 10;
pub const ANEXO_MAX_BYTES: u64 = 10 * 1024 * 1024;
pub const ANEXOS_POR_EMAIL_MAX_BYTES: u64 = 16 * 1024 * 1024;
/// O lote do /v2/ingest: até 20 e-mails e ~24 MB (a Central aceita 25 MiB).
pub const LOTE_MAX_EMAILS: usize = 20;
pub const LOTE_MAX_BYTES: usize = 24 * 1024 * 1024;
/// /v2/count aceita até 5000 ids por pedido.
pub const CONTAGEM_MAX_IDS: usize = 5000;

/// Hora (de Brasília, UTC-3 o ano todo desde 2019) em que a conciliação do
/// dia anterior é fechada (/v2/count com `day`), uma pasta por volta.
pub const CONCILIACAO_HORA_BRT: u64 = 6;
pub const FUSO_BRT_SEGUNDOS: i64 = -3 * 60 * 60;

/// "tuta_fora" só depois de tantas voltas seguidas sem o Tuta (um soluço de
/// rede não acende a faixa da equipe).
pub const TUTA_FORA_DEPOIS_DE_FALHAS: u32 = 3;

/// Outra instância pulsou (DaVinci `duplicado`): esta para de ler e só
/// pulsa de novo depois disto (mais que a janela de 3 min do DaVinci).
pub const DUPLICADO_ESPERA: Duration = Duration::from_secs(4 * 60);

/// DaVinci com as rotas desligadas (404) ou recusando o token (401): tenta
/// de novo depois disto, sem insistir.
pub const DAVINCI_RECUSOU_ESPERA: Duration = Duration::from_secs(15 * 60);

/// Sem resposta da Central, a última lista de pastas a ler vale por este tempo.
pub const CONFIG_VALIDA_POR: Duration = Duration::from_secs(10 * 60);

/// As pastas e os aliases vão no /v2/sync quando a lista muda e, mesmo igual,
/// a cada tanto (uma pessoa pode ter mudado a regra de alguma pasta lá).
pub const PASTAS_REENVIO_A_CADA: Duration = Duration::from_secs(60 * 60);

/// 474 (versão recusada) ou sem sessão: o conector NÃO insiste no Tuta;
/// só pulsa para a faixa mostrar e confere o Chaveiro de novo neste ritmo.
pub const PARADO_ESPERA: Duration = Duration::from_secs(5 * 60);

// ── Canário (decisão 9) ─────────────────────────────────────────────────

/// O canário (474 no applicationtypesservice + releases do GitHub) roda na
/// subida e 1x por dia.
pub const CANARIO_A_CADA: Duration = Duration::from_secs(24 * 60 * 60);

/// Parado por 474: o canário confere de novo neste ritmo (volta sozinho se
/// o Tuta voltar a aceitar a versão).
pub const CANARIO_PARADO_A_CADA: Duration = Duration::from_secs(6 * 60 * 60);

/// A partir de quantas releases publicadas mais novas o pulso diz `atrasado`.
pub const CANARIO_ATRASO_RELEASES: u32 = 3;

/// A lista pública de releases do Tuta (sem token).
pub const GITHUB_API_URL: &str = "https://api.github.com";
pub const GITHUB_RELEASES_ROTA: &str = "/repos/tutao/tutanota/releases?per_page=50";
/// O GitHub exige User-Agent: só o nome do programa (NUNCA e-mail/pessoa).
pub const GITHUB_USER_AGENT: &str = "davinci-tuta-conector";
/// Teto de tempo do pedido ao GitHub.
pub const GITHUB_TEMPO_MAX_PEDIDO: Duration = Duration::from_secs(20);

// ── Envio (decisão 10; TUDO desligado por padrão) ───────────────────────

/// Tetos do próprio conector, por conta (a Central tem os dela na hora de
/// enfileirar, e a conta inteira tem 100/h pelos termos do Tuta, cláusula
/// 7.3 — contados pela pasta Enviados, que soma a equipe e os robôs).
pub const ENVIO_TETO_HORA: u32 = 30;
pub const ENVIO_TETO_DIA: u32 = 300;
pub const TUTA_TETO_CONTA_HORA: u32 = 100;

/// Domínios do próprio Tuta: o destinatário de lá exige a chave pública dele
/// (cifra de ponta a ponta), que o conector não monta. A resposta para um
/// endereço desses volta `failed` (responder pelo app do Tuta).
pub const DOMINIOS_TUTA: &[&str] = &[
	"tuta.com",
	"tuta.io",
	"tutanota.com",
	"tutanota.de",
	"tutamail.com",
	"keemail.me",
];

/// O lease da Central vence em 15 min (depois disso o job vira "incerto" e uma
/// pessoa pode marcar "Não saiu" e responder de novo). O conector NÃO manda o
/// SendDraft de um job pego há mais que isto: falha `lease_vencido` (nada sai;
/// o rascunho pode ficar no Tuta) — com folga para a chamada ao Tuta (60 s).
pub const LEASE_LIMITE_ENVIO: Duration = Duration::from_secs(12 * 60);

/// Quanto tempo a entrada do diário de envio fica depois de fechada.
pub const DIARIO_GUARDA: Duration = Duration::from_secs(7 * 24 * 60 * 60);

// ── Arquivos locais (só ids/cursor/estado; NADA de conteúdo de e-mail) ──

/// Pasta do estado local, relativa ao HOME do usuário (uma subpasta por
/// conta: …/davinci-tuta-conector/geral/). Criada com 0700.
pub const PASTA_LOCAL: &str = "Library/Application Support/davinci-tuta-conector";
pub const ARQUIVO_ESTADO: &str = "estado.json";
/// O diário do envio (só do módulo de envio): ids, passos e o lease em curso.
pub const ARQUIVO_DIARIO: &str = "diario-envio.json";
/// Chaves LOCAIS do conector (envio, marcar respondido). Arquivo de texto
/// editável pelo dono; ausente = tudo desligado.
pub const ARQUIVO_CHAVES: &str = "chaves.json";

// ── Versão ──────────────────────────────────────────────────────────────

/// Versão do conector (a do Cargo.toml).
pub const VERSAO_CONECTOR: &str = env!("CARGO_PKG_VERSION");
/// Tag oficial do Tuta usada (SDK_VERSAO, conferida pelo build.rs).
pub const SDK_TAG: &str = env!("CONECTOR_SDK_TAG");
pub const SDK_COMMIT: &str = env!("CONECTOR_SDK_COMMIT");

/// A versão que vai no cabeçalho `cv` (a do Cargo.toml do Tuta, nunca editada).
#[must_use]
pub fn versao_sdk() -> &'static str {
	tutasdk::CLIENT_VERSION
}

/// "tuta-conector/0.1.0 sdk/361.260929.0" — vai no pulso (campo `versao`).
#[must_use]
pub fn versao_completa() -> String {
	format!("tuta-conector/{VERSAO_CONECTOR} sdk/{}", versao_sdk())
}
