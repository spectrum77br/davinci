//! Segredos em memória (senha, TOTP, access_token, token do DaVinci, chaves).
//!
//! - `Debug` nunca mostra o conteúdo e NÃO existe `Display`: um `{}`/`{:?}`
//!   por engano num log ou num erro sai como `Segredo(***)`.
//! - A memória é zerada ao descartar (zeroize).

use std::fmt;
use zeroize::{Zeroize, Zeroizing};

/// Texto secreto.
#[derive(Clone)]
pub struct Segredo(Zeroizing<String>);

impl Segredo {
	#[must_use]
	pub fn novo(valor: String) -> Self {
		Self(Zeroizing::new(valor))
	}

	/// O valor, só para quem PRECISA dele (montar o pedido ao Tuta/DaVinci).
	#[must_use]
	pub fn expor(&self) -> &str {
		self.0.as_str()
	}

	#[must_use]
	pub fn vazio(&self) -> bool {
		self.0.trim().is_empty()
	}
}

impl fmt::Debug for Segredo {
	fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
		f.write_str("Segredo(***)")
	}
}

impl PartialEq for Segredo {
	fn eq(&self, outro: &Self) -> bool {
		self.0.as_bytes() == outro.0.as_bytes()
	}
}

/// Bytes secretos (chave cifrada da senha, chave de acesso).
#[derive(Clone)]
pub struct SegredoBytes(Zeroizing<Vec<u8>>);

impl SegredoBytes {
	#[must_use]
	pub fn novo(valor: Vec<u8>) -> Self {
		Self(Zeroizing::new(valor))
	}

	#[must_use]
	pub fn expor(&self) -> &[u8] {
		self.0.as_slice()
	}
}

impl fmt::Debug for SegredoBytes {
	fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
		f.write_str("SegredoBytes(***)")
	}
}

/// Zera um `String` comum que carregou um segredo de passagem.
pub fn zerar(texto: &mut String) {
	texto.zeroize();
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn debug_nao_mostra_o_valor() {
		let s = Segredo::novo("senha-super-secreta".into());
		assert_eq!(format!("{s:?}"), "Segredo(***)");
		assert_eq!(s.expor(), "senha-super-secreta");
		let b = SegredoBytes::novo(vec![1, 2, 3]);
		assert_eq!(format!("{b:?}"), "SegredoBytes(***)");
	}
}
