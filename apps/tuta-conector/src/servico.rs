//! O SERVIÇO de uma conta (`tuta-conector rodar --conta geral|goslin`, o que
//! o LaunchAgent roda): a leitura e o envio no mesmo processo, cada um no seu
//! módulo (a leitura não conhece o envio; os dois só trocam números e chaves
//! pelo `Compartilhado`).
//!
//! O laço: uma volta de leitura (sinal + /v2/sync + pastas) → uma volta de
//! envio (recibos pendentes; com as duas chaves ligadas, a fila) → espera a
//! próxima volta (90 s), mandando o sinal a cada 60 s e, com o envio ligado,
//! conferindo a fila a cada 20 s (a pessoa clicou: sai em menos de meio minuto).
//!
//! Um processo por conta (trava de arquivo na pasta da conta): uma conta
//! caída não para a outra.

use crate::config::{self, Conta};
use crate::envio::{DependenciasEnvio, Enviador};
use crate::estado::Compartilhado;
use crate::leitura::{self, Leitor, OpcoesLeitura, Parada};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

/// O laço para sempre (ou até um erro de configuração).
pub async fn rodar(conta: Conta, contar: bool) -> Result<(), Parada> {
	let compartilhado = Arc::new(Compartilhado::default());
	let deps = leitura::dependencias(conta, false, compartilhado.clone())?;
	let _trava = leitura::travar(&deps.pasta_local)?;
	let davinci = deps.davinci.clone().ok_or_else(|| Parada::Cofre("caixa da Central não configurada".into()))?;
	let enviador = Enviador::novo(DependenciasEnvio {
		conta,
		cofre: deps.cofre.clone(),
		tuta_url: deps.tuta_url.clone(),
		rest_tuta: deps.rest_tuta.clone(),
		davinci,
		pasta_local: deps.pasta_local.clone(),
		relogio: deps.relogio.clone(),
		compartilhado: compartilhado.clone(),
	});
	let leitor = Leitor::novo(
		deps,
		OpcoesLeitura {
			uma_volta: false,
			contar,
			seco: false,
		},
	);
	laco(leitor, enviador, compartilhado, None).await
}

/// O laço (os testes passam um número de voltas).
pub async fn laco(
	mut leitor: Leitor,
	mut enviador: Enviador,
	compartilhado: Arc<Compartilhado>,
	voltas: Option<u32>,
) -> Result<(), Parada> {
	let mut feitas = 0u32;
	loop {
		let r = leitor.uma_volta().await;
		let duplicado = matches!(r, Err(Parada::Duplicado));
		match &r {
			Ok(resumo) => log::info!("[{}] leitura: {resumo}", leitor.conta()),
			Err(p) => log::warn!("[{}] leitura parou: {p}", leitor.conta()),
		}
		// Outro agente na mesma caixa: este não envia (nem pulsa) até a próxima volta.
		if !duplicado {
			enviar(&mut enviador, &leitor).await;
		}
		feitas += 1;
		if voltas.is_some_and(|v| feitas >= v) {
			return Ok(());
		}
		let espera = leitor.espera_depois(&r);
		let passo = config::ENVIO_CONFERIR_A_CADA.min(config::PULSO_INTERVALO);
		let mut resta = espera;
		let mut desde_sinal = Duration::ZERO;
		while !resta.is_zero() {
			let agora = passo.min(resta);
			tokio::time::sleep(agora).await;
			resta = resta.saturating_sub(agora);
			desde_sinal += agora;
			if duplicado {
				continue;
			}
			if desde_sinal >= config::PULSO_INTERVALO {
				desde_sinal = Duration::ZERO;
				let _ = leitor.pulsar().await;
			}
			if compartilhado.envio_ligado.load(Ordering::Relaxed) && compartilhado.pode_enviar.load(Ordering::Relaxed) {
				enviar(&mut enviador, &leitor).await;
			}
		}
	}
}

async fn enviar(enviador: &mut Enviador, leitor: &Leitor) {
	match enviador.uma_volta().await {
		Ok(resumo) if resumo.tarefas > 0 || resumo.recibos > 0 => log::info!("[{}] envio: {resumo}", leitor.conta()),
		Ok(_) => {},
		Err(p) => log::warn!("[{}] envio parou: {p}", leitor.conta()),
	}
}
