//! Canário de versão (decisão 9), SEM senha e SEM sessão: o mesmo pedido que
//! o próprio SDK faz para baixar o modelo de tipos (GET
//! /rest/base/applicationtypesservice) com o cabeçalho `cv` = a versão do
//! nosso SDK. 474 = o Tuta já não aceita esta versão (`versao_recusada`).
//!
//! A segunda metade (`releases_atras`, estado `atrasado`): a lista PÚBLICA de
//! releases do Tuta no GitHub (api.github.com, sem token, sem dado nenhum;
//! cliente HTTP próprio, política `Exata`). Conta quantas versões publicadas
//! são mais novas que a nossa; a partir de `config::CANARIO_ATRASO_RELEASES`
//! o pulso diz `atrasado` (o Tuta corta versões de 5 a 6 semanas, ~1 por
//! semana: o aviso vem com folga para `scripts/atualizar-sdk.sh <tag nova>`).

use serde::Deserialize;
use std::collections::{BTreeSet, HashMap};
use std::sync::Arc;
use tutasdk::bindings::rest_client::{HttpMethod, RestClient, RestClientOptions};
use tutasdk::services::generated::base::ApplicationTypesService;
use tutasdk::services::Service;

#[derive(Debug, PartialEq, Eq)]
pub enum Canario {
	/// O Tuta aceita a nossa versão.
	Aceita,
	/// 474: precisa atualizar o SDK.
	Recusada,
	/// Outro status (5xx, 429…) — não dá para concluir agora.
	Indefinido(u32),
	/// Sem conexão.
	SemRede,
}

/// Pergunta ao Tuta se ainda aceita `versao` (normalmente `tutasdk::CLIENT_VERSION`).
pub async fn conferir_versao(base: &str, rest: &Arc<dyn RestClient>, versao: &str) -> Canario {
	let url = format!("{}/rest/{}", base.trim_end_matches('/'), ApplicationTypesService::PATH);
	let cabecalhos = HashMap::from([
		("cv".to_owned(), versao.to_owned()),
		("v".to_owned(), ApplicationTypesService::VERSION.to_string()),
	]);
	let resposta = rest
		.request_binary(
			url,
			HttpMethod::GET,
			RestClientOptions {
				headers: cabecalhos,
				body: None,
				suspension_behavior: None,
			},
		)
		.await;
	match resposta {
		Ok(r) if (200..300).contains(&r.status) => Canario::Aceita,
		Ok(r) if r.status == 474 => Canario::Recusada,
		Ok(r) => Canario::Indefinido(r.status),
		Err(_) => Canario::SemRede,
	}
}

// ── Releases no GitHub ("atrasado") ─────────────────────────────────────

/// Uma versão do Tuta: (major, data, correção), ex.: 361.260929.0.
pub type Versao = (u32, u32, u32);

/// "361.260929.0" → (361, 260929, 0).
#[must_use]
pub fn ler_versao(texto: &str) -> Option<Versao> {
	let mut partes = texto.trim().split('.');
	let a = partes.next()?.parse().ok()?;
	let b = partes.next()?.parse().ok()?;
	let c = partes.next()?.parse().ok()?;
	if partes.next().is_some() {
		return None;
	}
	Some((a, b, c))
}

/// A versão de uma tag oficial (`tutanota-desktop-release-361.260929.0`,
/// `tutanota-release-359.260904.0`, android/ios…); outras tags = None.
#[must_use]
pub fn versao_da_tag(tag: &str) -> Option<Versao> {
	let resto = tag.strip_prefix("tutanota-")?;
	let versao = resto.rsplit_once("release-").map(|(_, v)| v)?;
	ler_versao(versao)
}

#[derive(Deserialize)]
struct ReleaseDoGithub {
	#[serde(default)]
	tag_name: String,
	#[serde(default)]
	draft: bool,
	#[serde(default)]
	prerelease: bool,
}

/// O que o GitHub disse.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Releases {
	/// Quantas versões publicadas (distintas) são mais novas que a nossa, e a mais nova.
	Atras { quantas: u32, mais_nova: Option<String> },
	/// Sem resposta útil (rede, limite do GitHub, formato): não conclui nada.
	Indefinido,
}

/// Lê as releases (só JSON público) e compara com `nossa`.
pub async fn releases_atras(url_releases: &str, rest: &Arc<dyn RestClient>, nossa: &str) -> Releases {
	let Some(nossa) = ler_versao(nossa) else {
		return Releases::Indefinido;
	};
	let cabecalhos = HashMap::from([
		("Accept".to_owned(), "application/vnd.github+json".to_owned()),
		// O GitHub exige User-Agent. NUNCA e-mail nem nome de pessoa aqui.
		("User-Agent".to_owned(), crate::config::GITHUB_USER_AGENT.to_owned()),
	]);
	let resposta = rest
		.request_binary(
			url_releases.to_owned(),
			HttpMethod::GET,
			RestClientOptions {
				headers: cabecalhos,
				body: None,
				suspension_behavior: None,
			},
		)
		.await;
	let Ok(r) = resposta else {
		return Releases::Indefinido;
	};
	if !(200..300).contains(&r.status) {
		return Releases::Indefinido;
	}
	let Ok(lista) = serde_json::from_slice::<Vec<ReleaseDoGithub>>(r.body.as_deref().unwrap_or(b"[]")) else {
		return Releases::Indefinido;
	};
	let mais_novas: BTreeSet<Versao> = lista
		.iter()
		.filter(|r| !r.draft && !r.prerelease)
		.filter_map(|r| versao_da_tag(&r.tag_name))
		.filter(|v| *v > nossa)
		.collect();
	Releases::Atras {
		quantas: u32::try_from(mais_novas.len()).unwrap_or(u32::MAX),
		mais_nova: mais_novas.last().map(|(a, b, c)| format!("{a}.{b}.{c}")),
	}
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn versoes_das_tags() {
		assert_eq!(versao_da_tag("tutanota-desktop-release-361.260929.0"), Some((361, 260929, 0)));
		assert_eq!(versao_da_tag("tutanota-release-359.260904.0"), Some((359, 260904, 0)));
		assert_eq!(versao_da_tag("tutanota-android-release-361.261006.1"), Some((361, 261006, 1)));
		assert_eq!(versao_da_tag("calendar-release-1.2.3"), None);
		assert_eq!(versao_da_tag("tutanota-desktop-release-361.x.0"), None);
		assert!(ler_versao("361.260929.0").unwrap() < ler_versao("361.261006.0").unwrap());
		assert!(ler_versao("361.261006.0").unwrap() < ler_versao("362.261010.0").unwrap());
	}
}
