//! Os estados do conector (decisão 8).
//!
//! Na Central de e-mail eles viram os 3 estados do sinal v1 (`online`,
//! `login_required`, `error`) e o detalhe vai no `error_code` (davinci.rs,
//! `Sinal::de`): a Saúde e a faixa "lojas sem ler" do /atendimento mostram
//! o estado da caixa e o código.

/// O que o conector está vivendo agora.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum EstadoConector {
	/// Subindo (primeiro pulso).
	Iniciando,
	/// Tudo certo: última volta de leitura completa.
	Ok,
	/// 401 / sessão encerrada no Tuta → alguém precisa rodar `tuta-conector entrar`.
	SessaoCaiu,
	/// 474: o Tuta recusou a versão do SDK → atualizar o conector.
	VersaoRecusada,
	/// Algum e-mail ou pasta não decifra (AEAD/erro): contado e reportado; a
	/// volta continua com o resto.
	Ilegivel,
	/// 429: o Tuta mandou esperar; o conector recua.
	Limitado,
	/// 5xx ou sem rede.
	TutaFora,
	/// Canário: o SDK está N releases atrás da mais nova do Tuta.
	Atrasado,
	/// Outra falha (detalhe no campo `detalhe`, mascarado).
	Erro,
	/// A Central do DaVinci ainda não tem o contrato v2 (o servidor sobe antes).
	SemV2,
}

impl EstadoConector {
	/// O texto que vai no campo `estado` do pulso.
	#[must_use]
	pub fn como_texto(self) -> &'static str {
		match self {
			Self::Iniciando => "iniciando",
			Self::Ok => "ok",
			Self::SessaoCaiu => "sessao_caiu",
			Self::VersaoRecusada => "versao_recusada",
			Self::Ilegivel => "ilegivel",
			Self::Limitado => "limitado",
			Self::TutaFora => "tuta_fora",
			Self::Atrasado => "atrasado",
			Self::Erro => "erro",
			Self::SemV2 => "sem_v2",
		}
	}

	/// Todos.
	pub const TODOS: [EstadoConector; 10] = [
		Self::Iniciando,
		Self::Ok,
		Self::SessaoCaiu,
		Self::VersaoRecusada,
		Self::Ilegivel,
		Self::Limitado,
		Self::TutaFora,
		Self::Atrasado,
		Self::Erro,
		Self::SemV2,
	];

	/// Texto curto e claro para a equipe (o DaVinci pode usar o dele).
	#[must_use]
	pub fn para_equipe(self) -> &'static str {
		match self {
			Self::Iniciando => "Conector do Tuta iniciando",
			Self::Ok => "Conector do Tuta lendo normalmente",
			Self::SessaoCaiu => "Conector do Tuta sem sessão: o Eduardo precisa entrar de novo no Mac mini",
			Self::VersaoRecusada => "O Tuta recusou a versão do conector: precisa atualizar",
			Self::Ilegivel => "Alguns e-mails do Tuta não puderam ser lidos (contados no painel)",
			Self::Limitado => "O Tuta pediu para o conector esperar; ele volta sozinho",
			Self::TutaFora => "Tuta fora do ar ou sem internet no Mac mini; o conector tenta de novo",
			Self::Atrasado => "Conector do Tuta com o SDK atrasado: atualizar logo",
			Self::Erro => "Conector do Tuta com erro (ver detalhe)",
			Self::SemV2 => "O DaVinci ainda não tem a versão nova da Central de e-mail: publicar o servidor",
		}
	}
}

/// O que a leitura e o envio (módulos separados, o mesmo processo por conta)
/// precisam saber um do outro — só números e chaves, nada de e-mail.
#[derive(Debug, Default)]
pub struct Compartilhado {
	/// O envio está pronto neste Mac (chave local ligada e sessão aberta):
	/// vai como `can_send` no sinal. Sem isto a Central nem enfileira.
	pub pode_enviar: std::sync::atomic::AtomicBool,
	/// O `send_enabled` que a Central devolveu no último sinal (a chave da
	/// pessoa na tela). O envio só pede trabalho com ela ligada.
	pub envio_ligado: std::sync::atomic::AtomicBool,
	/// Quantos e-mails a CONTA mandou na última hora (pela pasta Enviados:
	/// soma a equipe, os robôs e o conector). O Tuta aceita 100/h.
	pub enviados_conta_hora: std::sync::atomic::AtomicU32,
	/// Quantos o envio deste conector mandou na última hora (vai no /v2/sync).
	pub enviados_conector_hora: std::sync::atomic::AtomicU32,
	/// O estado da leitura agora (o índice em `EstadoConector::TODOS`): o
	/// sinal que o envio manda antes de enviar não pode apagar o da leitura.
	pub estado_leitura: std::sync::atomic::AtomicU8,
}

impl Compartilhado {
	#[must_use]
	pub fn estado(&self) -> EstadoConector {
		let i = usize::from(self.estado_leitura.load(std::sync::atomic::Ordering::Relaxed));
		EstadoConector::TODOS.get(i).copied().unwrap_or(EstadoConector::Iniciando)
	}

	pub fn guardar_estado(&self, estado: EstadoConector) {
		let i = EstadoConector::TODOS.iter().position(|e| *e == estado).unwrap_or(0);
		self.estado_leitura.store(u8::try_from(i).unwrap_or(0), std::sync::atomic::Ordering::Relaxed);
	}
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn textos_unicos_e_curtos() {
		let mut vistos = std::collections::HashSet::new();
		for e in EstadoConector::TODOS {
			let t = e.como_texto();
			assert!(t.len() <= 32 && t.is_ascii(), "{t}");
			assert!(vistos.insert(t), "repetido: {t}");
		}
	}
}
