//! LEITURA (decisões 6, 7, 8 e 9): `tuta-conector ler`.
//!
//! REGRAS (o teste tests/so_leitura.rs confere por TEXTO este arquivo e
//! src/leitura/**, inclusive os comentários — por isso os nomes proibidos não
//! aparecem aqui):
//! - Este módulo NUNCA referencia nada que ESCREVE no Tuta: atualizar
//!   instância, marcar lido/não lido, mover, apagar, criar rascunho, enviar,
//!   encerrar sessão… nem o módulo de envio. Carregar Mail, ConversationEntry,
//!   MailDetails e arquivos é só GET e não mexe no "não lido".
//! - NUNCA websocket/event bus: só consulta periódica (60–120 s). Assim o
//!   conector nunca vira "líder" e não segura as regras de pasta da equipe.
//! - Em disco, só ids/cursores (estado_local.rs); conteúdo de e-mail só em
//!   memória até a Central de e-mail responder (aceito ou repetido).
//! - O corpo só sai das pastas que a Central manda ler (`corpo`, pelo
//!   /v2/sync); das outras vão só os ids, na contagem.
//! - Um e-mail ou pasta que não decifra (AEAD/erro/pânico do SDK) é CONTADO
//!   como ilegível e reportado no pulso; nunca derruba a volta inteira.
//!
//! Módulos:
//! - `entrada_id`  o id do MailSetEntry (data + e-mail), como o cliente oficial
//! - `caixa`       o que se lê do Tuta (pastas, entradas, e-mail, anexo)
//! - `pacote`      o JSON de cada e-mail no formato da Central (MessageIn v2)
//! - `volta`       o laço: sinal, pastas, cursores, lotes, contagem
//!
//! Modos: `--uma-volta` (uma volta e sai), `--contar` (só ids por pasta: a
//! fase do piloto, nada de conteúdo vai ao DaVinci), `--seco` (lê do Tuta —
//! os 3 mais novos de cada pasta, para provar que decifra — e NÃO manda nada
//! ao DaVinci nem grava cursor).

pub mod caixa;
pub mod entrada_id;
pub mod pacote;
pub mod volta;

use crate::chaveiro::{self, ChaveiroMac, Cofre};
use crate::config::{self, Conta};
use crate::davinci::ClienteDavinci;
use crate::estado::{Compartilhado, EstadoConector};
use crate::estado_local::{agora_ms, pasta_padrao};
use crate::rede::{ClienteRest, Politica};
use std::sync::Arc;
use tutasdk::bindings::rest_client::RestClient;
pub use volta::{Dependencias, Leitor, Parada};

/// Os modos do subcomando `ler`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct OpcoesLeitura {
	/// Uma volta só e sai.
	pub uma_volta: bool,
	/// Só conta ids por pasta (fase 0).
	pub contar: bool,
	/// Não manda nada ao DaVinci (mostra o resumo na tela).
	pub seco: bool,
}

/// O resumo de uma volta (vai para o log e para a tela; só números).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ResumoVolta {
	pub pastas_lidas: u32,
	pub pastas_ilegiveis: u32,
	/// E-mails novos (acima do cursor) vistos nesta volta.
	pub emails_novos: u32,
	/// Aceitos ou repetidos na Central.
	pub entregues: u32,
	/// Ilegíveis NESTA volta (o total fica no registro local).
	pub ilegiveis: u32,
	/// Recusados pela Central (`rejected`) nesta volta.
	pub recusados: u32,
	/// Anexos que foram junto / que ficaram só no rastro (perigoso, grande…).
	pub anexos: u32,
	pub anexos_pulados: u32,
	pub lotes: u32,
	pub contagens: u32,
	/// Pedidos de volta da contagem (no Tuta, não na Central).
	pub faltando: u32,
	/// Só para aliases que só se contam: o corpo não subiu.
	pub so_contados: u32,
	pub movidos: u32,
	pub apagados: u32,
	/// Com `processNeeded` na Entrada há menos de `espera_regra_min`.
	pub esperando_regra: u32,
	/// Sumiram entre a lista e a leitura (404).
	pub sumidos: u32,
	/// Corpo encurtado para caber no lote.
	pub cortados: u32,
	pub varreduras: u32,
	pub dias_fechados: u32,
	pub tentados_de_novo: u32,
	/// Só no `--seco`: quantas entradas no topo das pastas.
	pub entradas_no_topo: u32,
}

impl ResumoVolta {
	/// O estado do pulso que esta volta justifica (sem o registro de antes).
	#[must_use]
	pub fn estado(&self) -> EstadoConector {
		if self.ilegiveis > 0 || self.pastas_ilegiveis > 0 {
			EstadoConector::Ilegivel
		} else {
			EstadoConector::Ok
		}
	}
}

impl std::fmt::Display for ResumoVolta {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		write!(
			f,
			"{} pasta(s) lida(s), {} ilegível(is); {} e-mail(s) novo(s), {} entregue(s) em {} lote(s), {} ilegível(is), {} recusado(s), {} esperando a regra, {} só contado(s); {} anexo(s) junto(s), {} deixado(s) de fora; {} contagem(ns), {} faltando, {} movido(s), {} apagado(s)",
			self.pastas_lidas,
			self.pastas_ilegiveis,
			self.emails_novos,
			self.entregues,
			self.lotes,
			self.ilegiveis,
			self.recusados,
			self.esperando_regra,
			self.so_contados,
			self.anexos,
			self.anexos_pulados,
			self.contagens,
			self.faltando,
			self.movidos,
			self.apagados
		)
	}
}

/// Trava de "um processo por conta" neste Mac (o estado local é um só por
/// conta; a leitura e o envio rodam no mesmo processo). O sistema solta a
/// trava quando o processo termina, mesmo se cair.
pub fn travar(pasta: &std::path::Path) -> Result<std::fs::File, Parada> {
	std::fs::create_dir_all(pasta).map_err(|e| Parada::Disco(e.to_string()))?;
	let arquivo = std::fs::OpenOptions::new()
		.create(true)
		.truncate(false)
		.write(true)
		.open(pasta.join("conector.trava"))
		.map_err(|e| Parada::Disco(e.to_string()))?;
	match arquivo.try_lock() {
		Ok(()) => Ok(arquivo),
		Err(std::fs::TryLockError::WouldBlock) => Err(Parada::JaRodando),
		Err(std::fs::TryLockError::Error(e)) => Err(Parada::Disco(e.to_string())),
	}
}

/// O que o `ler` (e o `rodar`) de verdade usam: Chaveiro do macOS,
/// app.tuta.com, a Central configurada para a conta e api.github.com (canário).
pub fn dependencias(conta: Conta, seco: bool, compartilhado: Arc<Compartilhado>) -> Result<Dependencias, Parada> {
	let cofre: Arc<dyn Cofre> = Arc::new(ChaveiroMac);
	let pasta_local = pasta_padrao(conta).map_err(|e| Parada::Disco(e.to_string()))?;
	let politica = Politica::tuta(config::TUTA_URL, config::TUTA_SUFIXOS_BLOB).map_err(|e| Parada::Login(e.to_string()))?;
	let rest_tuta: Arc<dyn RestClient> = Arc::new(
		ClienteRest::nativo(politica, config::TUTA_TEMPO_MAX_PEDIDO).map_err(|e| Parada::Login(e.to_string()))?,
	);
	let davinci = if seco {
		None
	} else {
		Some(Arc::new(cliente_davinci(&*cofre, conta)?))
	};
	let politica_github = Politica::exata(config::GITHUB_API_URL).map_err(|e| Parada::Login(e.to_string()))?;
	let rest_github: Arc<dyn RestClient> = Arc::new(
		ClienteRest::nativo(politica_github, config::GITHUB_TEMPO_MAX_PEDIDO).map_err(|e| Parada::Login(e.to_string()))?,
	);
	Ok(Dependencias {
		conta,
		cofre,
		tuta_url: config::TUTA_URL.to_owned(),
		rest_tuta,
		davinci,
		url_releases: format!("{}{}", config::GITHUB_API_URL, config::GITHUB_RELEASES_ROTA),
		rest_github,
		pasta_local,
		relogio: Arc::new(agora_ms),
		compartilhado,
	})
}

/// O cliente da caixa desta conta na Central (do Chaveiro).
pub fn cliente_davinci(cofre: &dyn Cofre, conta: Conta) -> Result<ClienteDavinci, Parada> {
	let acesso = match chaveiro::ler_davinci(cofre, conta) {
		Ok(Some(a)) => a,
		Ok(None) => {
			return Err(Parada::Cofre(format!(
				"a caixa da Central não está configurada para a conta {conta}: rode `tuta-conector configurar --conta {conta}`"
			)))
		},
		Err(e) => return Err(Parada::Cofre(e.to_string())),
	};
	ClienteDavinci::novo(&acesso.url, &acesso.caixa, acesso.token.clone()).map_err(|e| Parada::Cofre(e.to_string()))
}

/// O subcomando `ler` (só leitura; o `rodar` junta o envio).
pub async fn executar(conta: Conta, opcoes: OpcoesLeitura) -> Result<ResumoVolta, Parada> {
	let deps = dependencias(conta, opcoes.seco, Arc::new(Compartilhado::default()))?;
	let _trava = if opcoes.seco {
		None
	} else {
		Some(travar(&deps.pasta_local)?)
	};
	let mut leitor = Leitor::novo(deps, opcoes);
	leitor.rodar().await
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn ilegivel_vira_estado_do_pulso() {
		assert_eq!(ResumoVolta::default().estado(), EstadoConector::Ok);
		let r = ResumoVolta {
			ilegiveis: 1,
			..ResumoVolta::default()
		};
		assert_eq!(r.estado(), EstadoConector::Ilegivel);
	}

	#[test]
	fn um_processo_por_conta() {
		let pasta = std::env::temp_dir().join(format!("tuta-conector-trava-{}", std::process::id()));
		let primeira = travar(&pasta).unwrap();
		assert_eq!(travar(&pasta).unwrap_err(), Parada::JaRodando);
		drop(primeira);
		assert!(travar(&pasta).is_ok());
		let _ = std::fs::remove_dir_all(&pasta);
	}
}
