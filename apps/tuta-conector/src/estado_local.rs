//! O que o conector guarda em disco: SÓ estado e ids (decisão 6 — nada de
//! conteúdo de e-mail, nada de segredo). UMA PASTA POR CONTA do Tuta:
//! ~/Library/Application Support/davinci-tuta-conector/<conta>/ (0700),
//! arquivos 0600, gravação atômica (arquivo temporário + rename).
//!
//! - estado.json: a instância (o id deste Mac para esta conta), último
//!   pulso, última leitura ok, cursores por pasta (ids).
//! - chaves.json: as chaves LOCAIS do conector, editáveis pelo dono; ausente
//!   ou ilegível = TUDO DESLIGADO.
//! - diario-envio.json: do módulo de envio (envio.rs), só ids e passos.

use crate::config::{self, Conta};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

/// A pasta local desta conta (no HOME de quem roda).
pub fn pasta_padrao(conta: Conta) -> io::Result<PathBuf> {
	let home = std::env::var_os("HOME")
		.filter(|h| !h.is_empty())
		.ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "HOME não definido"))?;
	Ok(PathBuf::from(home).join(config::PASTA_LOCAL).join(conta.apelido()))
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct UltimoPulso {
	/// ms desde 1970.
	pub em_ms: u64,
	pub estado: String,
	/// "aceito" ou o erro do DaVinci (sem segredo).
	pub resultado: String,
}

/// Cursor de uma pasta: até onde já foi entregue (só ids do Tuta).
///
/// As entradas (MailSetEntry) de uma pasta vêm ordenadas pela data de
/// recebimento. `ultimo_entregue` é a entrada mais nova tal que ela e TODAS
/// as mais velhas (desde a primeira carga) foram entregues ao DaVinci (ou
/// registradas como ilegível/recusada). As entregues acima de um buraco (um
/// e-mail que voltou `erro_passageiro`) ficam em `entregues_acima` para não
/// serem carregadas de novo; o cursor anda quando o buraco fecha.
#[derive(Serialize, Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct CursorPasta {
	/// O id do MailSetEntry (base64url) mais novo já entregue sem buraco.
	pub ultimo_entregue: Option<String>,
	/// Ids de entradas acima do cursor já entregues.
	pub entregues_acima: Vec<String>,
	/// Última varredura funda desta pasta (ms).
	pub ultima_varredura_funda_ms: Option<u64>,
	/// Desde quando a pasta é lida (a primeira carga): a contagem e a varredura
	/// nunca pedem e-mail mais velho que isto (não traz pendência de meses).
	pub inicio_cobertura_ms: Option<u64>,
}

/// Um e-mail que não entrou: ilegível (não decifra) ou recusado pelo DaVinci
/// (`erro_dado`). Só ids e um motivo curto SEM conteúdo.
#[derive(Serialize, Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct Pendencia {
	/// A pasta (id do MailSet) onde foi visto.
	pub pasta: String,
	pub tentativas: u32,
	/// Não tentar de novo antes disto (ms).
	pub proxima_ms: u64,
	/// Código curto (ex.: "aead", "panico", "formato") — nunca texto do e-mail.
	pub motivo: String,
}

/// O último canário (sem segredo).
#[derive(Serialize, Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct CanarioGuardado {
	/// "aceita", "recusada" (474) ou "indefinido".
	pub versao: String,
	pub releases_atras: Option<u32>,
	pub mais_nova: Option<String>,
}

/// O fechamento do dia (/v2/count com `day`), uma pasta por volta.
#[derive(Serialize, Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct Conciliacao {
	/// "AAAA-MM-DD" (o dia fechado, horário de Brasília).
	pub dia: String,
	/// As pastas já fechadas neste dia.
	pub feitas: Vec<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default)]
pub struct EstadoLocal {
	pub versao_formato: u8,
	/// O id deste Mac para esta conta na Central (o /v2/sync manda sempre o
	/// mesmo: um reinício não vira "dois agentes").
	pub instancia: Option<String>,
	/// A conta do Tuta destes cursores (outra conta = começa do zero).
	pub conta: Option<String>,
	pub sessao_criada_em_ms: Option<u64>,
	pub ultimo_pulso: Option<UltimoPulso>,
	pub ultima_leitura_ok_em_ms: Option<u64>,
	/// Por pasta (id do MailSet).
	pub cursores: BTreeMap<String, CursorPasta>,
	/// Por e-mail ("lista/elemento").
	pub ilegiveis: BTreeMap<String, Pendencia>,
	pub recusados: BTreeMap<String, Pendencia>,
	/// E-mails que de propósito NÃO subiram (só para aliases que só se contam:
	/// adm@, financeiro@…): a contagem não os pede de novo a cada volta.
	pub so_contados: std::collections::BTreeSet<String>,
	/// `erro_passageiro` seguidos por e-mail (some quando entra).
	pub passageiros: BTreeMap<String, u32>,
	pub ultimo_canario_ms: Option<u64>,
	pub canario: Option<CanarioGuardado>,
	pub conciliacao: Option<Conciliacao>,
}

pub(crate) fn garantir_pasta(pasta: &Path) -> io::Result<()> {
	fs::create_dir_all(pasta)?;
	#[cfg(unix)]
	{
		use std::os::unix::fs::PermissionsExt;
		fs::set_permissions(pasta, fs::Permissions::from_mode(0o700))?;
	}
	Ok(())
}

pub(crate) fn gravar_atomico(caminho: &Path, bytes: &[u8]) -> io::Result<()> {
	let pasta = caminho
		.parent()
		.ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "caminho sem pasta"))?;
	garantir_pasta(pasta)?;
	let temporario = caminho.with_extension("tmp");
	{
		let mut opcoes = fs::OpenOptions::new();
		opcoes.write(true).create(true).truncate(true);
		#[cfg(unix)]
		{
			use std::os::unix::fs::OpenOptionsExt;
			opcoes.mode(0o600);
		}
		let mut arquivo = opcoes.open(&temporario)?;
		arquivo.write_all(bytes)?;
		arquivo.sync_all()?;
	}
	fs::rename(&temporario, caminho)
}

impl EstadoLocal {
	/// Lê o estado; ausente = novo; ilegível = novo (com aviso) — o pior que
	/// acontece é reler ids (o DaVinci deduplica).
	#[must_use]
	pub fn carregar(pasta: &Path) -> Self {
		let caminho = pasta.join(config::ARQUIVO_ESTADO);
		match fs::read(&caminho) {
			Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_else(|_| {
				log::warn!("estado local ilegível; começando do zero (o DaVinci deduplica)");
				Self::default()
			}),
			Err(_) => Self::default(),
		}
	}

	pub fn salvar(&self, pasta: &Path) -> io::Result<()> {
		let mut copia = self.clone();
		copia.versao_formato = 2;
		let bytes = serde_json::to_vec_pretty(&copia).map_err(io::Error::other)?;
		gravar_atomico(&pasta.join(config::ARQUIVO_ESTADO), &bytes)
	}
}

/// As chaves LOCAIS do conector (decisão 10): mesmo com o DaVinci liberando,
/// nada sai se a chave daqui estiver desligada. Padrão: tudo desligado.
#[derive(Serialize, Deserialize, Debug, Clone, Default, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct ChavesLocais {
	/// Libera o subcomando `enviar` (com TODAS as chaves do DaVinci ligadas).
	pub envio: bool,
	/// Marcar o e-mail original como "respondido" (regrava o Mail cifrado inteiro).
	pub marcar_respondido: bool,
}

impl ChavesLocais {
	/// Ausente, ilegível ou com campo desconhecido = tudo desligado.
	#[must_use]
	pub fn carregar(pasta: &Path) -> Self {
		let caminho = pasta.join(config::ARQUIVO_CHAVES);
		match fs::read(&caminho) {
			Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_else(|_| {
				log::warn!("chaves.json ilegível: tudo DESLIGADO");
				Self::default()
			}),
			Err(_) => Self::default(),
		}
	}
}

/// Um id de instância novo (16 bytes do sistema, em hexadecimal).
#[must_use]
pub fn instancia_nova() -> String {
	let mut b = [0u8; 16];
	rand_core::RngCore::fill_bytes(&mut rand_core::OsRng, &mut b);
	b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Agora em ms desde 1970.
#[must_use]
pub fn agora_ms() -> u64 {
	std::time::SystemTime::now()
		.duration_since(std::time::UNIX_EPOCH)
		.map(|d| u64::try_from(d.as_millis()).unwrap_or(u64::MAX))
		.unwrap_or_default()
}

/// ms desde 1970 → "2026-10-08 18:00:00 UTC".
#[must_use]
pub fn data_legivel(ms: u64) -> String {
	let formato = time::macros::format_description!("[year]-[month]-[day] [hour]:[minute]:[second] UTC");
	i64::try_from(ms / 1000)
		.ok()
		.and_then(|s| time::OffsetDateTime::from_unix_timestamp(s).ok())
		.and_then(|d| d.format(formato).ok())
		.unwrap_or_else(|| "?".to_owned())
}

#[cfg(test)]
mod testes {
	use super::*;

	fn pasta_temporaria(nome: &str) -> PathBuf {
		let p = std::env::temp_dir().join(format!("tuta-conector-teste-{nome}-{}", std::process::id()));
		let _ = fs::remove_dir_all(&p);
		p
	}

	#[test]
	fn estado_vai_e_volta_com_permissoes() {
		let pasta = pasta_temporaria("estado");
		let mut e = EstadoLocal::carregar(&pasta);
		assert_eq!(e, EstadoLocal::default());
		e.ultima_leitura_ok_em_ms = Some(123);
		e.cursores.insert(
			"pasta1".into(),
			CursorPasta {
				ultimo_entregue: Some("x".into()),
				..CursorPasta::default()
			},
		);
		e.salvar(&pasta).unwrap();
		let lido = EstadoLocal::carregar(&pasta);
		assert_eq!(lido.ultima_leitura_ok_em_ms, Some(123));
		#[cfg(unix)]
		{
			use std::os::unix::fs::PermissionsExt;
			let modo = fs::metadata(pasta.join(config::ARQUIVO_ESTADO)).unwrap().permissions().mode();
			assert_eq!(modo & 0o777, 0o600);
			let modo = fs::metadata(&pasta).unwrap().permissions().mode();
			assert_eq!(modo & 0o777, 0o700);
		}
		fs::write(pasta.join(config::ARQUIVO_ESTADO), b"{lixo").unwrap();
		assert_eq!(EstadoLocal::carregar(&pasta), EstadoLocal::default());
		let _ = fs::remove_dir_all(&pasta);
	}

	#[test]
	fn chaves_locais_comecam_desligadas() {
		let pasta = pasta_temporaria("chaves");
		assert_eq!(ChavesLocais::carregar(&pasta), ChavesLocais::default());
		fs::create_dir_all(&pasta).unwrap();
		fs::write(pasta.join(config::ARQUIVO_CHAVES), br#"{"envio":true,"outra":true}"#).unwrap();
		assert!(!ChavesLocais::carregar(&pasta).envio, "campo desconhecido = desligado");
		fs::write(pasta.join(config::ARQUIVO_CHAVES), br#"{"envio":true}"#).unwrap();
		let c = ChavesLocais::carregar(&pasta);
		assert!(c.envio && !c.marcar_respondido);
		let _ = fs::remove_dir_all(&pasta);
	}

	#[test]
	fn data_legivel_em_utc() {
		assert_eq!(data_legivel(0), "1970-01-01 00:00:00 UTC");
	}
}
