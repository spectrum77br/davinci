//! O cliente HTTP do conector: o do PRÓPRIO SDK (hyper + rustls, raízes do
//! macOS) embrulhado com três travas que valem para TODO pedido, inclusive os
//! que o SDK faz por dentro:
//!
//! 1. Destino permitido. Um cliente para o Tuta (só app.tuta.com e os
//!    servidores de blob *.tuta.com que o próprio Tuta indica), OUTRO cliente
//!    para o DaVinci (só a URL configurada) e um terceiro só para o canário
//!    (api.github.com, sem token nem dado nenhum). Um nunca fala com o destino
//!    do outro: o token do DaVinci não tem como ir ao Tuta, nem o do Tuta ao DaVinci.
//! 2. HTTPS. `http://` só para 127.0.0.1/localhost (os servidores FALSOS dos testes).
//! 3. Teto de tempo por pedido (o cliente do SDK não tem).
//!
//! Nunca loga a URL inteira (a query leva accessToken e `_body`): só o host.

use async_trait::async_trait;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tutasdk::bindings::rest_client::{
	HttpMethod, RestClient, RestClientError, RestClientOptions, RestResponse,
};

/// esquema://host:porta de uma URL.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Origem {
	pub esquema: String,
	pub host: String,
	pub porta: u16,
}

impl Origem {
	/// Lê a origem de uma URL absoluta (http/https, sem usuário:senha@).
	pub fn de(url: &str) -> Result<Self, ErroDestino> {
		let uri: http::Uri = url.parse().map_err(|_| ErroDestino::UrlInvalida)?;
		let esquema = uri.scheme_str().ok_or(ErroDestino::UrlInvalida)?.to_ascii_lowercase();
		let autoridade = uri.authority().ok_or(ErroDestino::UrlInvalida)?;
		if autoridade.as_str().contains('@') {
			return Err(ErroDestino::UrlInvalida);
		}
		let host = autoridade.host().to_ascii_lowercase();
		if host.is_empty() {
			return Err(ErroDestino::UrlInvalida);
		}
		let porta = match (autoridade.port_u16(), esquema.as_str()) {
			(Some(p), _) => p,
			(None, "https") => 443,
			(None, "http") => 80,
			_ => return Err(ErroDestino::EsquemaProibido),
		};
		if esquema != "https" && esquema != "http" {
			return Err(ErroDestino::EsquemaProibido);
		}
		Ok(Self {
			esquema,
			host,
			porta,
		})
	}

	#[must_use]
	pub fn local(&self) -> bool {
		matches!(self.host.as_str(), "127.0.0.1" | "localhost" | "[::1]" | "::1")
	}

	/// https sempre; http só para o próprio Mac (servidores falsos dos testes).
	pub fn conferir_esquema(&self) -> Result<(), ErroDestino> {
		if self.esquema == "https" || (self.esquema == "http" && self.local()) {
			Ok(())
		} else {
			Err(ErroDestino::EsquemaProibido)
		}
	}

	/// "https://host" ou "https://host:porta" (sem barra no fim).
	#[must_use]
	pub fn base(&self) -> String {
		let padrao = matches!((self.esquema.as_str(), self.porta), ("https", 443) | ("http", 80));
		if padrao {
			format!("{}://{}", self.esquema, self.host)
		} else {
			format!("{}://{}:{}", self.esquema, self.host, self.porta)
		}
	}
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErroDestino {
	UrlInvalida,
	EsquemaProibido,
	HostProibido,
}

impl std::fmt::Display for ErroDestino {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		f.write_str(match self {
			Self::UrlInvalida => "URL inválida",
			Self::EsquemaProibido => "só https (http só para 127.0.0.1 nos testes)",
			Self::HostProibido => "destino fora da lista permitida",
		})
	}
}

/// Para onde um cliente pode ir.
#[derive(Clone, Debug)]
pub enum Politica {
	/// O Tuta: a origem base e os hosts de blob com estes sufixos (mesmo esquema).
	Tuta {
		base: Origem,
		sufixos_blob: &'static [&'static str],
	},
	/// O DaVinci: exatamente esta origem.
	Davinci { base: Origem },
	/// Outra origem exata, sem segredo nenhum (o canário no GitHub).
	Exata { base: Origem },
}

impl Politica {
	pub fn tuta(url_base: &str, sufixos_blob: &'static [&'static str]) -> Result<Self, ErroDestino> {
		let base = Origem::de(url_base)?;
		base.conferir_esquema()?;
		Ok(Self::Tuta { base, sufixos_blob })
	}

	pub fn davinci(url_base: &str) -> Result<Self, ErroDestino> {
		let base = Origem::de(url_base)?;
		base.conferir_esquema()?;
		Ok(Self::Davinci { base })
	}

	/// Uma origem exata sem segredo (o canário das releases no GitHub).
	pub fn exata(url_base: &str) -> Result<Self, ErroDestino> {
		let base = Origem::de(url_base)?;
		base.conferir_esquema()?;
		Ok(Self::Exata { base })
	}

	/// Confere uma URL de pedido contra a política.
	pub fn permite(&self, url: &str) -> Result<Origem, ErroDestino> {
		let destino = Origem::de(url)?;
		destino.conferir_esquema()?;
		let ok = match self {
			Self::Davinci { base } | Self::Exata { base } => destino == *base,
			Self::Tuta { base, sufixos_blob } => {
				destino == *base
					|| (destino.esquema == base.esquema
						&& destino.porta == base.porta
						&& sufixos_blob.iter().any(|s| {
							destino.host.ends_with(s) && destino.host.len() > s.len()
						})) || (base.local() && destino.local() && destino.esquema == base.esquema)
			},
		};
		if ok {
			Ok(destino)
		} else {
			Err(ErroDestino::HostProibido)
		}
	}
}

/// Contagem de pedidos para o pulso (sem URL, sem conteúdo).
#[derive(Default, Debug)]
pub struct ContadoresRede {
	pub pedidos: AtomicU64,
	pub recusados_destino: AtomicU64,
	pub tempo_esgotado: AtomicU64,
	pub status_429: AtomicU64,
	pub status_401: AtomicU64,
	pub status_474: AtomicU64,
	pub status_5xx: AtomicU64,
}

/// O RestClient que o SDK (e o nosso código) usam.
pub struct ClienteRest {
	interno: Arc<dyn RestClient>,
	politica: Politica,
	tempo_max: Duration,
	pub contadores: Arc<ContadoresRede>,
}

impl ClienteRest {
	pub fn novo(interno: Arc<dyn RestClient>, politica: Politica, tempo_max: Duration) -> Self {
		Self {
			interno,
			politica,
			tempo_max,
			contadores: Arc::new(ContadoresRede::default()),
		}
	}

	/// O cliente HTTPS do SDK (raízes de certificado do macOS).
	pub fn nativo(politica: Politica, tempo_max: Duration) -> std::io::Result<Self> {
		let interno = tutasdk::net::native_rest_client::NativeRestClient::try_new()?;
		Ok(Self::novo(Arc::new(interno), politica, tempo_max))
	}

	#[must_use]
	pub fn politica(&self) -> &Politica {
		&self.politica
	}
}

#[async_trait]
impl RestClient for ClienteRest {
	async fn request_binary(
		&self,
		url: String,
		method: HttpMethod,
		options: RestClientOptions,
	) -> Result<RestResponse, RestClientError> {
		let destino = match self.politica.permite(&url) {
			Ok(d) => d,
			Err(e) => {
				self.contadores.recusados_destino.fetch_add(1, Ordering::Relaxed);
				let host = Origem::de(&url).map(|o| o.host).unwrap_or_else(|_| "?".into());
				log::warn!("pedido recusado ({e}) para o host {host}");
				return Err(RestClientError::InvalidURL);
			},
		};
		self.contadores.pedidos.fetch_add(1, Ordering::Relaxed);
		let resposta = tokio::time::timeout(
			self.tempo_max,
			self.interno.request_binary(url, method, options),
		)
		.await;
		match resposta {
			Err(_) => {
				self.contadores.tempo_esgotado.fetch_add(1, Ordering::Relaxed);
				log::warn!("tempo esgotado falando com {}", destino.host);
				Err(RestClientError::NetworkError)
			},
			Ok(Ok(r)) => {
				let contador = match r.status {
					429 => Some(&self.contadores.status_429),
					401 => Some(&self.contadores.status_401),
					474 => Some(&self.contadores.status_474),
					500..=599 => Some(&self.contadores.status_5xx),
					_ => None,
				};
				if let Some(c) = contador {
					c.fetch_add(1, Ordering::Relaxed);
				}
				Ok(r)
			},
			Ok(Err(e)) => Err(e),
		}
	}
}

#[cfg(test)]
mod testes {
	use super::*;

	#[test]
	fn origem_e_esquema() {
		let o = Origem::de("https://App.Tuta.com/rest/x?y=1").unwrap();
		assert_eq!(o.host, "app.tuta.com");
		assert_eq!(o.porta, 443);
		assert_eq!(o.base(), "https://app.tuta.com");
		assert!(Origem::de("http://exemplo.com").unwrap().conferir_esquema().is_err());
		assert!(Origem::de("http://127.0.0.1:9").unwrap().conferir_esquema().is_ok());
		assert!(Origem::de("ftp://exemplo.com").is_err());
		assert!(Origem::de("https://usuario:senha@exemplo.com").is_err());
		assert!(Origem::de("/rest/sem-host").is_err());
	}

	#[test]
	fn politica_do_tuta() {
		let p = Politica::tuta("https://app.tuta.com", &[".tuta.com"]).unwrap();
		assert!(p.permite("https://app.tuta.com/rest/sys/saltservice").is_ok());
		assert!(p.permite("https://w1.api.tuta.com/rest/storage/blobservice").is_ok());
		assert!(p.permite("https://tuta.com.atacante.net/x").is_err());
		assert!(p.permite("https://atacantetuta.com/x").is_err());
		assert!(p.permite("http://w1.api.tuta.com/x").is_err());
		assert!(p.permite("https://davinci.exemplo/api").is_err());
		assert!(Politica::tuta("http://app.tuta.com", &[]).is_err());
	}

	#[test]
	fn politica_do_davinci_e_so_a_origem_exata() {
		let p = Politica::davinci("https://davinci.exemplo").unwrap();
		assert!(p.permite("https://davinci.exemplo/api/mail/agent/x/heartbeat").is_ok());
		assert!(p.permite("https://davinci.exemplo:8443/api").is_err());
		assert!(p.permite("https://app.tuta.com/rest").is_err());
		let g = Politica::exata("https://api.github.com").unwrap();
		assert!(g.permite("https://api.github.com/repos/tutao/tutanota/releases").is_ok());
		assert!(g.permite("https://github.com/x").is_err());
		let local = Politica::davinci("http://127.0.0.1:8000").unwrap();
		assert!(local.permite("http://127.0.0.1:8000/x").is_ok());
		assert!(local.permite("http://127.0.0.1:9000/x").is_err());
	}
}
