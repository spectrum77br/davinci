//! Conversa com quem está no Terminal (entrar, configurar).
//!
//! A senha e o token são lidos SEM ECO direto do /dev/tty (rpassword): não
//! aparecem na tela, não passam pela entrada padrão (um `| tee` não pega) e
//! nunca vêm de argumento ou variável de ambiente.

use crate::segredo::Segredo;
use std::collections::VecDeque;
use std::io::{self, BufRead, Write};

pub trait Terminal {
	/// Pergunta com eco (e-mail, URL, código TOTP).
	fn perguntar(&mut self, texto: &str) -> io::Result<String>;
	/// Pergunta SEM eco (senha, token).
	fn perguntar_oculto(&mut self, texto: &str) -> io::Result<Segredo>;
	/// Uma linha de aviso para a pessoa.
	fn dizer(&mut self, texto: &str);
}

/// O Terminal de verdade.
pub struct TerminalReal;

impl Terminal for TerminalReal {
	fn perguntar(&mut self, texto: &str) -> io::Result<String> {
		let mut saida = io::stdout().lock();
		write!(saida, "{texto}")?;
		saida.flush()?;
		let mut linha = String::new();
		let lidos = io::stdin().lock().read_line(&mut linha)?;
		if lidos == 0 {
			return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "entrada fechada"));
		}
		Ok(linha.trim().to_owned())
	}

	fn perguntar_oculto(&mut self, texto: &str) -> io::Result<Segredo> {
		rpassword::prompt_password(texto).map(Segredo::novo)
	}

	fn dizer(&mut self, texto: &str) {
		println!("{texto}");
	}
}

/// Terminal "roteirizado" dos testes: respostas prontas, saída guardada.
#[derive(Default)]
pub struct TerminalRoteiro {
	pub respostas: VecDeque<String>,
	pub perguntas: Vec<String>,
	pub falado: Vec<String>,
}

impl TerminalRoteiro {
	#[must_use]
	pub fn com(respostas: &[&str]) -> Self {
		Self {
			respostas: respostas.iter().map(|r| (*r).to_owned()).collect(),
			..Self::default()
		}
	}

	/// Tudo o que apareceu na tela (perguntas e avisos), para conferir que
	/// nenhum segredo foi mostrado.
	#[must_use]
	pub fn tela(&self) -> String {
		let mut tudo = self.perguntas.join("\n");
		tudo.push('\n');
		tudo.push_str(&self.falado.join("\n"));
		tudo
	}

	fn proxima(&mut self) -> io::Result<String> {
		self.respostas
			.pop_front()
			.ok_or_else(|| io::Error::new(io::ErrorKind::UnexpectedEof, "roteiro acabou"))
	}
}

impl Terminal for TerminalRoteiro {
	fn perguntar(&mut self, texto: &str) -> io::Result<String> {
		self.perguntas.push(texto.to_owned());
		self.proxima().map(|r| r.trim().to_owned())
	}

	fn perguntar_oculto(&mut self, texto: &str) -> io::Result<Segredo> {
		self.perguntas.push(texto.to_owned());
		self.proxima().map(Segredo::novo)
	}

	fn dizer(&mut self, texto: &str) {
		self.falado.push(texto.to_owned());
	}
}
