//! O id de um MailSetEntry (a "entrada" de um e-mail numa pasta), igual ao
//! cliente oficial (`src/platform-kit/meta/EntityUtils.ts`,
//! `constructMailSetEntryId` / `deconstructMailSetEntryId`, tag 361):
//!
//! ```text
//! 13 bytes em base64url sem "=":
//! [ 4 bytes: recebido_em_ms >> 10 (big endian) ][ 9 bytes: o id do Mail ]
//! ```
//!
//! As entradas de uma pasta ficam em ordem desses BYTES (a data de
//! recebimento, ~1 s de resolução, depois o id do e-mail). Um e-mail movido
//! entra na pasta nova na posição da DATA DE RECEBIMENTO, não no topo — por
//! isso movidos e apagados são vistos pela contagem (/v2/count), não pelo cursor.
//!
//! Código NOSSO (a ponte tem um igual; este foi escrito de novo a partir do
//! TS oficial e do vetor de teste dele).

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use std::cmp::Ordering;

/// O maior id possível (`CUSTOM_MAX_ID` do TS: 340 "_" = 255 bytes 0xFF):
/// início de uma leitura do topo para baixo.
#[must_use]
pub fn id_maximo() -> String {
	"_".repeat(340)
}

/// Os bytes de um id (None se não for base64url).
#[must_use]
pub fn bytes(id: &str) -> Option<Vec<u8>> {
	URL_SAFE_NO_PAD.decode(id).ok()
}

/// A data de recebimento (ms, arredondada para baixo em 1024 ms) de um id.
#[must_use]
pub fn recebido_ms(id: &str) -> Option<u64> {
	let b = bytes(id)?;
	if b.len() != 13 {
		return None;
	}
	let n = u32::from_be_bytes([b[0], b[1], b[2], b[3]]);
	Some(u64::from(n) << 10)
}

/// O id "mais baixo" do instante `ms`: começa uma leitura em ordem
/// crescente a partir desta data (o início é exclusivo; nenhum e-mail tem
/// id só de zeros). É o que o cliente oficial faz para ler por data.
#[must_use]
pub fn id_do_instante(ms: u64) -> String {
	let mut b = [0u8; 13];
	let quantum = u32::try_from(ms >> 10).unwrap_or(u32::MAX);
	b[..4].copy_from_slice(&quantum.to_be_bytes());
	URL_SAFE_NO_PAD.encode(b)
}

/// Compara dois ids como o servidor ordena (pelos bytes).
#[must_use]
pub fn comparar(a: &str, b: &str) -> Ordering {
	match (bytes(a), bytes(b)) {
		(Some(x), Some(y)) => x.cmp(&y),
		_ => a.cmp(b),
	}
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn vetor_do_ts_oficial() {
		// EntityUtilsTest.ts: mail "-----------0" (9 bytes, o último = 1) em
		// 2017-10-03T13:46:13Z → "V7ifKQAAAAAAAAAAAQ".
		let id = "V7ifKQAAAAAAAAAAAQ";
		let ms = recebido_ms(id).unwrap();
		assert_eq!(ms, 1_507_038_373_000 & !1023);
		assert_eq!(bytes(id).unwrap()[4..], [0, 0, 0, 0, 0, 0, 0, 0, 1]);
	}

	#[test]
	fn ordem_pelos_bytes_e_inicio_por_data() {
		let cedo = id_do_instante(1_000_000_000_000);
		let tarde = id_do_instante(1_700_000_000_000);
		assert_eq!(comparar(&cedo, &tarde), Ordering::Less);
		assert_eq!(comparar(&tarde, &id_maximo()), Ordering::Less);
		assert_eq!(recebido_ms(&tarde), Some(1_700_000_000_000 & !1023));
		// base64url NÃO é ordem ASCII ("-" < "A" em ASCII, mas vale 62):
		// por isso a comparação é pelos bytes.
		let a = URL_SAFE_NO_PAD.encode([0u8; 13]);
		let mut b = [0u8; 13];
		b[0] = 0xF8;
		let b = URL_SAFE_NO_PAD.encode(b);
		assert!(b.starts_with('-'));
		assert_eq!(comparar(&a, &b), Ordering::Less);
		assert_eq!(recebido_ms("lixo"), None);
	}
}
