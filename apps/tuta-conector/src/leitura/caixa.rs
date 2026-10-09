//! A caixa do Tuta vista pela leitura: SÓ consultas (GET) pelo SDK oficial.
//!
//! - Pastas: as MailSet da caixa, cada uma decifrada SOZINHA (uma pasta que
//!   não decifra é contada e as outras seguem — o `load_range` oficial
//!   falharia a lista inteira).
//! - Entradas (MailSetEntry): os ids da pasta, do topo para baixo ou do
//!   cursor para cima (o MailSetEntry não é cifrado).
//! - E-mail: o Mail cru → a chave de sessão (`resolve_session_key` oficial:
//!   chave do dono OU o balde/bucketKey do e-mail recebido de fora) →
//!   decifrado (`decrypt_parsed`, remendo 09) → o ConversationEntry (o
//!   Message-ID de verdade) → o MailDetailsBlob (remendos 05/06/07/09:
//!   cabeçalhos brutos e corpo) → os arquivos (File) com a chave de cada um.
//! - Anexo: os blobs do arquivo (remendo 05), decifrados com a chave dele.
//!
//! Toda chamada passa por `sem_panico` (o SDK tem `expect`/`panic!` em
//! resposta estranha do servidor): pânico vira `Ilegivel` daquele item e a
//! caixa fica "suspeita" (a volta seguinte abre o SDK de novo).
//!
//! Nada aqui grava no Tuta (o teste tests/so_leitura.rs confere por texto).

use crate::estado::EstadoConector;
use crate::leitura::entrada_id;
use crate::registro::mascarar;
use crate::tuta::sdk::{self, estado_do_erro, sem_panico, ErroRetomar};
use crypto_primitives::key::GenericAesKey;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tutasdk::bindings::rest_client::RestClient;
use tutasdk::entities::generated::sys::{Blob, Group, GroupInfo};
use tutasdk::entities::generated::tutanota::{
	ConversationEntry, Mail, MailDetails, MailDetailsBlob, MailSet, MailSetEntry, TutanotaFile,
};
use tutasdk::entities::Entity;
use tutasdk::login::Credentials;
use tutasdk::rest_error::HttpError;
use tutasdk::tutanota_constants::ArchiveDataType;
use tutasdk::{ApiCallError, CustomId, GeneratedId, IdTupleGenerated, ListLoadDirection, LoggedInSdk};

/// Uma entidade CRUA do SDK (`ParsedEntity`: id do atributo → valor). O SDK
/// não exporta esse nome; `entities::Errors` é o mesmo tipo.
type Bruto = tutasdk::entities::Errors;

/// Por que uma leitura no Tuta não deu.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FalhaTuta {
	/// 401/sessão encerrada: alguém precisa rodar `tuta-conector entrar`.
	SessaoCaiu,
	/// 474: o Tuta não aceita mais esta versão do SDK.
	VersaoRecusada,
	/// 429 (com o tempo que o Tuta pediu, se veio).
	Limitado(Option<Duration>),
	/// 5xx, sem rede, tempo esgotado.
	Fora,
	/// SÓ este item (e-mail, pasta, arquivo) não deu: não decifra, formato
	/// estranho, pânico do SDK. Motivo curto, sem conteúdo.
	Ilegivel(String),
	/// 404: sumiu entre a lista e a leitura (apagado de vez, movido).
	Sumiu,
}

impl FalhaTuta {
	/// Vale para a conta inteira (para a volta) — o resto é do item.
	#[must_use]
	pub fn geral(&self) -> bool {
		matches!(self, Self::SessaoCaiu | Self::VersaoRecusada | Self::Limitado(_) | Self::Fora)
	}

	#[must_use]
	pub fn estado(&self) -> EstadoConector {
		match self {
			Self::SessaoCaiu => EstadoConector::SessaoCaiu,
			Self::VersaoRecusada => EstadoConector::VersaoRecusada,
			Self::Limitado(_) => EstadoConector::Limitado,
			Self::Fora => EstadoConector::TutaFora,
			Self::Ilegivel(_) | Self::Sumiu => EstadoConector::Ilegivel,
		}
	}

	/// O código curto que fica no registro local (nunca texto do e-mail).
	#[must_use]
	pub fn codigo(&self) -> String {
		match self {
			Self::SessaoCaiu => "sessao".into(),
			Self::VersaoRecusada => "versao".into(),
			Self::Limitado(_) => "limitado".into(),
			Self::Fora => "fora".into(),
			Self::Ilegivel(m) => m.chars().take(60).collect(),
			Self::Sumiu => "sumiu".into(),
		}
	}
}

impl std::fmt::Display for FalhaTuta {
	fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
		match self {
			Self::SessaoCaiu => f.write_str("a sessão do Tuta caiu (rode `tuta-conector entrar`)"),
			Self::VersaoRecusada => f.write_str("o Tuta recusou a versão do SDK (474)"),
			Self::Limitado(Some(d)) => write!(f, "o Tuta pediu para esperar {} s (429)", d.as_secs()),
			Self::Limitado(None) => f.write_str("o Tuta pediu para esperar (429)"),
			Self::Fora => f.write_str("o Tuta está fora do ar ou sem conexão"),
			Self::Ilegivel(m) => write!(f, "ilegível ({m})"),
			Self::Sumiu => f.write_str("sumiu do Tuta"),
		}
	}
}

/// A falha de uma chamada do SDK. `o_que` é um rótulo curto nosso (ex.: "mail").
fn falha(o_que: &str, erro: &ApiCallError) -> FalhaTuta {
	if let ApiCallError::ServerResponseError {
		source: HttpError::NotFoundError,
	} = erro
	{
		return FalhaTuta::Sumiu;
	}
	if let ApiCallError::ServerResponseError {
		source: HttpError::TooManyRequestsError { suspension_time_sec },
	} = erro
	{
		return FalhaTuta::Limitado(suspension_time_sec.map(Duration::from_secs));
	}
	match estado_do_erro(erro) {
		EstadoConector::SessaoCaiu => FalhaTuta::SessaoCaiu,
		EstadoConector::VersaoRecusada => FalhaTuta::VersaoRecusada,
		EstadoConector::TutaFora => FalhaTuta::Fora,
		_ => {
			let texto = mascarar(&erro.to_string());
			let curto = if texto.contains("decrypt") || texto.contains("Decrypt") || texto.contains("MAC") {
				"decifrar"
			} else if texto.contains("AEAD") || texto.contains("kdf") || texto.contains("nonce") {
				"aead"
			} else if texto.contains("session key") || texto.contains("Session key") {
				"chave"
			} else {
				"formato"
			};
			FalhaTuta::Ilegivel(format!("{o_que}:{curto}"))
		},
	}
}

/// Uma pasta (MailSet) da caixa.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PastaTuta {
	/// O id do MailSet (elemento), o que o DaVinci chama de `pasta_id`.
	pub id: String,
	/// A lista das entradas (MailSetEntry) da pasta.
	pub lista_entradas: GeneratedId,
	pub nome: String,
	/// MailSetKind: 0 pessoal, 1 Entrada, 2 Enviados, 3 Lixeira, 4 Arquivo,
	/// 5 Spam, 6 Rascunhos, 7 Todos, 8 marcador, 9 importados, 10 agendados.
	pub tipo: i64,
	pub pai: Option<String>,
	/// "Pai/Filho" (só registro: quem decide é o NOME).
	pub caminho: String,
}

/// Os MailSetKind que importam aqui.
pub const KIND_ENTRADA: i64 = 1;
pub const KIND_ENVIADOS: i64 = 2;
pub const KIND_LIXEIRA: i64 = 3;
pub const KIND_SPAM: i64 = 5;
pub const KIND_TODOS: i64 = 7;
pub const KIND_MARCADOR: i64 = 8;

impl PastaTuta {
	/// Marcador (rótulo) ou "Todos": não é a pasta de um e-mail. Não entra
	/// na contagem (a Central entenderia como "movido para o marcador").
	#[must_use]
	pub fn e_marcador(&self) -> bool {
		self.tipo == KIND_MARCADOR || self.tipo == KIND_TODOS
	}
}

/// O nome que a pasta do sistema mostra (o MailSet dela vem sem nome).
fn nome_do_sistema(tipo: i64) -> &'static str {
	match tipo {
		1 => "Entrada",
		2 => "Enviados",
		3 => "Lixeira",
		4 => "Arquivo",
		5 => "Spam",
		6 => "Rascunhos",
		7 => "Todos",
		9 => "Importados",
		10 => "Agendados",
		_ => "",
	}
}

/// As pastas lidas e quantas não decifraram.
#[derive(Clone, Debug, Default)]
pub struct Pastas {
	pub pastas: Vec<PastaTuta>,
	pub ilegiveis: u32,
}

/// Uma entrada de pasta: o id dela e o e-mail.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entrada {
	pub id: String,
	pub mail: IdTupleGenerated,
	pub recebido_ms: u64,
}

/// Um arquivo (anexo) do e-mail.
#[derive(Clone)]
pub struct ArquivoLido {
	pub id: IdTupleGenerated,
	pub nome: String,
	pub tipo: String,
	pub tamanho: u64,
	pub cid: Option<String>,
	blobs: Vec<Blob>,
	/// None = o arquivo não decifrou (vai só o id na lista).
	chave: Option<GenericAesKey>,
}

impl ArquivoLido {
	#[must_use]
	pub fn legivel(&self) -> bool {
		self.chave.is_some()
	}
}

/// Um e-mail lido (só em memória até o DaVinci aceitar).
#[derive(Clone)]
pub struct EmailLido {
	pub id: IdTupleGenerated,
	pub mail: Mail,
	/// O Message-ID real (ConversationEntry); None se não deu para ler.
	pub message_id: Option<String>,
	/// No e-mail ENVIADO (que não tem cabeçalho bruto): o Message-ID do e-mail
	/// a que ele respondeu, pelo fio do Tuta (ConversationEntry.previous).
	pub resposta_a: Option<String>,
	/// Cabeçalhos brutos e corpo (None no "só metadados" ou rascunho).
	pub detalhes: Option<MailDetails>,
	pub arquivos: Vec<ArquivoLido>,
	/// Campos que o SDK não conseguiu ler (vão vazios).
	pub campos_ilegiveis: u32,
}

/// A caixa (o SDK oficial já com a sessão retomada).
#[derive(Clone)]
pub struct Caixa {
	sdk: Arc<LoggedInSdk>,
	suspeita: Arc<AtomicBool>,
}

impl Caixa {
	/// Retoma a sessão guardada (Session + User; destrava a chave do grupo).
	pub async fn abrir(base: &str, rest: Arc<dyn RestClient>, credenciais: Credentials) -> Result<Self, FalhaTuta> {
		let s = sdk::novo_sdk(base, rest);
		match sdk::retomar(&s, credenciais).await {
			Ok(logado) => Ok(Self {
				sdk: logado,
				suspeita: Arc::new(AtomicBool::new(false)),
			}),
			Err(ErroRetomar::SessaoCaiu) => Err(FalhaTuta::SessaoCaiu),
			Err(ErroRetomar::VersaoRecusada) => Err(FalhaTuta::VersaoRecusada),
			Err(ErroRetomar::Limitado) => Err(FalhaTuta::Limitado(None)),
			Err(ErroRetomar::TutaFora) => Err(FalhaTuta::Fora),
			Err(ErroRetomar::Panico(_)) => Err(FalhaTuta::Ilegivel("login:panico".into())),
			Err(ErroRetomar::Outro(_)) => Err(FalhaTuta::Ilegivel("login:formato".into())),
		}
	}

	/// Houve pânico do SDK nesta caixa (a volta seguinte abre outra).
	#[must_use]
	pub fn suspeita(&self) -> bool {
		self.suspeita.load(Ordering::Relaxed)
	}

	fn panico(&self, o_que: &str) -> FalhaTuta {
		self.suspeita.store(true, Ordering::Relaxed);
		log::warn!("o SDK entrou em pânico lendo {o_que}: item isolado como ilegível");
		FalhaTuta::Ilegivel(format!("{o_que}:panico"))
	}

	/// As pastas (MailSet) da caixa, cada uma decifrada sozinha.
	pub async fn pastas(&self) -> Result<Pastas, FalhaTuta> {
		let sdk = self.sdk.clone();
		let caixa_de_email = match sem_panico(async { sdk.mail_facade().load_user_mailbox().await }).await {
			Ok(Ok(m)) => m,
			Ok(Err(e)) => return Err(falha("caixa", &e)),
			Err(_) => return Err(self.panico("caixa")),
		};
		let lista = caixa_de_email.mailSets.mailSets.clone();
		let cliente = self.sdk.get_entity_client();
		let tipo = MailSet::type_ref();
		let brutos = match sem_panico(cliente.load_range(&tipo, &lista, &GeneratedId::min_id(), 1000, ListLoadDirection::ASC)).await {
			Ok(Ok(b)) => b,
			Ok(Err(e)) => return Err(falha("pastas", &e)),
			Err(_) => return Err(self.panico("pastas")),
		};
		let cripto = self.sdk.mail_facade().get_crypto_entity_client();
		let mut lidas: Vec<MailSet> = Vec::new();
		let mut ilegiveis = 0u32;
		for bruto in brutos {
			let r = sem_panico(async {
				let decifrado = cripto.decrypt_parsed(&tipo, bruto, None).await?;
				self.sdk
					.instance_mapper
					.parse_entity::<MailSet>(decifrado)
					.map_err(|e| ApiCallError::internal_with_err(e, "MailSet"))
			})
			.await;
			match r {
				Ok(Ok(p)) => lidas.push(p),
				Ok(Err(e)) => {
					let f = falha("pasta", &e);
					if f.geral() {
						return Err(f);
					}
					ilegiveis += 1;
				},
				Err(_) => {
					let _ = self.panico("pasta");
					ilegiveis += 1;
				},
			}
		}
		Ok(Pastas {
			pastas: montar_pastas(&lidas),
			ilegiveis,
		})
	}

	/// Os aliases ATIVOS da conta (o mesmo caminho do `get_group_id_for_mail_address`
	/// oficial: grupos de e-mail do usuário → GroupInfo → endereço + aliases ligados).
	pub async fn aliases(&self) -> Result<Vec<String>, FalhaTuta> {
		let r = sem_panico(async {
			let usuario = self.sdk.get_user();
			let cripto = self.sdk.mail_facade().get_crypto_entity_client();
			let mut saida: Vec<String> = Vec::new();
			for m in usuario.memberships.iter().filter(|m| m.groupType == Some(5)) {
				let grupo: Group = cripto.load(&m.group).await?;
				let info: GroupInfo = match (&grupo.user, &usuario._id) {
					(None, _) => cripto.load(&m.groupInfo).await?,
					(Some(dono), Some(eu)) if dono == eu => cripto.load(&usuario.userGroup.groupInfo).await?,
					_ => continue,
				};
				saida.extend(info.mailAddress.iter().cloned());
				saida.extend(info.mailAddressAliases.iter().filter(|a| a.enabled).map(|a| a.mailAddress.clone()));
			}
			Ok::<_, ApiCallError>(saida)
		})
		.await;
		let mut lista = match r {
			Ok(Ok(l)) => l,
			Ok(Err(e)) => return Err(falha("aliases", &e)),
			Err(_) => return Err(self.panico("aliases")),
		};
		for a in &mut lista {
			*a = a.trim().to_lowercase();
		}
		lista.retain(|a| a.contains('@'));
		lista.sort();
		lista.dedup();
		Ok(lista)
	}

	/// Uma página de entradas: `quantos` a partir de `inicio` (exclusivo), do
	/// topo para baixo (`decrescente`) ou do cursor para cima.
	pub async fn pagina(
		&self,
		lista: &GeneratedId,
		inicio: &str,
		quantos: usize,
		decrescente: bool,
	) -> Result<Vec<Entrada>, FalhaTuta> {
		let cripto = self.sdk.mail_facade().get_crypto_entity_client();
		let direcao = if decrescente {
			ListLoadDirection::DESC
		} else {
			ListLoadDirection::ASC
		};
		let inicio = CustomId(inicio.to_owned());
		let r = sem_panico(cripto.load_range::<MailSetEntry, _>(lista, &inicio, quantos, direcao)).await;
		let entradas = match r {
			Ok(Ok(e)) => e,
			Ok(Err(e)) => return Err(falha("entradas", &e)),
			Err(_) => return Err(self.panico("entradas")),
		};
		Ok(entradas
			.into_iter()
			.filter_map(|e| {
				let id = e._id?.element_id.0;
				let recebido_ms = entrada_id::recebido_ms(&id)?;
				Some(Entrada {
					id,
					mail: e.mail,
					recebido_ms,
				})
			})
			.collect())
	}

	/// Um e-mail. `completo` = também cabeçalhos, corpo e arquivos.
	pub async fn email(&self, id: &IdTupleGenerated, completo: bool) -> Result<EmailLido, FalhaTuta> {
		match sem_panico(self.email_sem_isolar(id, completo)).await {
			Ok(r) => r,
			Err(_) => Err(self.panico("mail")),
		}
	}

	async fn email_sem_isolar(&self, id: &IdTupleGenerated, completo: bool) -> Result<EmailLido, FalhaTuta> {
		let cliente = self.sdk.get_entity_client();
		let cripto = self.sdk.mail_facade().get_crypto_entity_client();
		let tipo = Mail::type_ref();
		let modelo = cliente.resolve_server_type_ref(&tipo).map_err(|e| falha("mail", &e))?;
		let bruto = cliente.load(&tipo, id).await.map_err(|e| falha("mail", &e))?;
		let chave = cripto
			.get_crypto_facade()
			.resolve_session_key(&bruto, &modelo)
			.await
			.map_err(|_| FalhaTuta::Ilegivel("mail:chave".into()))?
			.ok_or_else(|| FalhaTuta::Ilegivel("mail:chave".into()))?;
		let decifrado = cripto
			.decrypt_parsed(&tipo, bruto.clone(), Some(chave.clone()))
			.await
			.map_err(|e| falha("mail", &e))?;
		let mail: Mail = self
			.sdk
			.instance_mapper
			.parse_entity(decifrado)
			.map_err(|_| FalhaTuta::Ilegivel("mail:formato".into()))?;
		let mut campos_ilegiveis = u32::try_from(mail._errors.len()).unwrap_or(u32::MAX);

		let (message_id, anterior) = match cripto.load::<ConversationEntry, _>(&mail.conversationEntry).await {
			Ok(c) => (Some(c.messageId), c.previous),
			Err(e) => {
				let f = falha("conversa", &e);
				if f.geral() {
					return Err(f);
				}
				campos_ilegiveis += 1;
				(None, None)
			},
		};
		// Só no enviado (estado 1): o recebido traz o In-Reply-To nos cabeçalhos.
		let mut resposta_a = None;
		if completo && mail.state == 1 {
			if let Some(id_anterior) = anterior {
				match cripto.load::<ConversationEntry, _>(&id_anterior).await {
					Ok(c) => resposta_a = Some(c.messageId),
					Err(e) if falha("conversa", &e).geral() => return Err(falha("conversa", &e)),
					Err(_) => {},
				}
			}
		}

		let mut detalhes = None;
		let mut arquivos = Vec::new();
		if completo {
			if let Some(id_detalhes) = &mail.mailDetails {
				let tipo_blob = MailDetailsBlob::type_ref();
				let corpo = self
					.sdk
					.blob_facade()
					.load_blob_element(&tipo_blob, id_detalhes)
					.await
					.map_err(|e| falha("detalhes", &e))?;
				let brutos: Vec<_> =
					serde_json::from_slice(&corpo).map_err(|_| FalhaTuta::Ilegivel("detalhes:formato".into()))?;
				let primeiro = brutos
					.into_iter()
					.next()
					.ok_or_else(|| FalhaTuta::Ilegivel("detalhes:vazio".into()))?;
				let lido = cliente.parse_raw(&tipo_blob, primeiro).map_err(|e| falha("detalhes", &e))?;
				let decifrado = cripto
					.decrypt_parsed(&tipo_blob, lido, Some(chave.clone()))
					.await
					.map_err(|e| falha("detalhes", &e))?;
				let blob: MailDetailsBlob = self
					.sdk
					.instance_mapper
					.parse_entity(decifrado)
					.map_err(|_| FalhaTuta::Ilegivel("detalhes:formato".into()))?;
				campos_ilegiveis += u32::try_from(blob._errors.len()).unwrap_or(0);
				campos_ilegiveis += u32::try_from(blob.details.body._errors.len()).unwrap_or(0);
				detalhes = Some(blob.details);
			}
			for id_arquivo in &mail.attachments {
				match self.arquivo_do_email(&bruto, mail.bucketKey.is_some(), id_arquivo).await {
					Ok(a) => arquivos.push(a),
					Err(f) if f.geral() => return Err(f),
					Err(_) => arquivos.push(ArquivoLido {
						id: id_arquivo.clone(),
						nome: String::new(),
						tipo: String::new(),
						tamanho: 0,
						cid: None,
						blobs: Vec::new(),
						chave: None,
					}),
				}
			}
		}
		Ok(EmailLido {
			id: id.clone(),
			mail,
			message_id,
			resposta_a,
			detalhes,
			arquivos,
			campos_ilegiveis,
		})
	}

	/// Um arquivo do e-mail, para copiar um anexo pendente (depois de reiniciar).
	pub async fn arquivo(&self, email: &IdTupleGenerated, arquivo: &IdTupleGenerated) -> Result<ArquivoLido, FalhaTuta> {
		let r = sem_panico(async {
			let cliente = self.sdk.get_entity_client();
			let cripto = self.sdk.mail_facade().get_crypto_entity_client();
			let tipo = Mail::type_ref();
			let modelo = cliente.resolve_server_type_ref(&tipo).map_err(|e| falha("mail", &e))?;
			let bruto = cliente.load(&tipo, email).await.map_err(|e| falha("mail", &e))?;
			let chave = cripto
				.get_crypto_facade()
				.resolve_session_key(&bruto, &modelo)
				.await
				.map_err(|_| FalhaTuta::Ilegivel("mail:chave".into()))?
				.ok_or_else(|| FalhaTuta::Ilegivel("mail:chave".into()))?;
			let decifrado = cripto
				.decrypt_parsed(&tipo, bruto.clone(), Some(chave))
				.await
				.map_err(|e| falha("mail", &e))?;
			let mail: Mail = self
				.sdk
				.instance_mapper
				.parse_entity(decifrado)
				.map_err(|_| FalhaTuta::Ilegivel("mail:formato".into()))?;
			self.arquivo_do_email(&bruto, mail.bucketKey.is_some(), arquivo).await
		})
		.await;
		match r {
			Ok(r) => r,
			Err(_) => Err(self.panico("arquivo")),
		}
	}

	/// O File decifrado com a chave DELE: a do dono (`_ownerEncSessionKey`)
	/// ou — arquivo de e-mail recebido de fora que ninguém abriu ainda — a do
	/// balde (bucketKey) do e-mail: o balde traz a chave de cada instância
	/// (o e-mail e os arquivos), e o `resolve_session_key` oficial devolve a
	/// da instância cujo id é pedido. Nada é gravado (o cliente oficial
	/// gravaria a chave do dono depois; o conector nunca grava).
	///
	/// `bruto_mail` é o Mail CRU (antes de decifrar). O SDK não dá nome público
	/// a esse tipo; ele é o mesmo `HashMap<String, valor>` de `entities::Errors`.
	async fn arquivo_do_email(
		&self,
		bruto_mail: &Bruto,
		tem_balde: bool,
		id: &IdTupleGenerated,
	) -> Result<ArquivoLido, FalhaTuta> {
		let cliente = self.sdk.get_entity_client();
		let modelo_mail = cliente.resolve_server_type_ref(&Mail::type_ref()).map_err(|e| falha("mail", &e))?;
		let cripto = self.sdk.mail_facade().get_crypto_entity_client();
		let tipo = TutanotaFile::type_ref();
		let modelo = cliente.resolve_server_type_ref(&tipo).map_err(|e| falha("arquivo", &e))?;
		let bruto = cliente.load(&tipo, id).await.map_err(|e| falha("arquivo", &e))?;
		let propria = cripto.get_crypto_facade().resolve_session_key(&bruto, &modelo).await;
		let chave = match propria {
			Ok(Some(c)) => c,
			_ if tem_balde => {
				let id_mail = modelo_mail
					.get_attribute_id_by_attribute_name("_id")
					.map_err(|_| FalhaTuta::Ilegivel("arquivo:modelo".into()))?;
				let id_arquivo = modelo
					.get_attribute_id_by_attribute_name("_id")
					.map_err(|_| FalhaTuta::Ilegivel("arquivo:modelo".into()))?;
				let mut pelo_balde = bruto_mail.clone();
				let valor = bruto
					.get(&id_arquivo)
					.cloned()
					.ok_or_else(|| FalhaTuta::Ilegivel("arquivo:id".into()))?;
				pelo_balde.insert(id_mail, valor);
				cripto
					.get_crypto_facade()
					.resolve_session_key(&pelo_balde, &modelo_mail)
					.await
					.map_err(|_| FalhaTuta::Ilegivel("arquivo:chave".into()))?
					.ok_or_else(|| FalhaTuta::Ilegivel("arquivo:chave".into()))?
			},
			_ => return Err(FalhaTuta::Ilegivel("arquivo:chave".into())),
		};
		let decifrado = cripto
			.decrypt_parsed(&tipo, bruto, Some(chave.clone()))
			.await
			.map_err(|e| falha("arquivo", &e))?;
		let f: TutanotaFile = self
			.sdk
			.instance_mapper
			.parse_entity(decifrado)
			.map_err(|_| FalhaTuta::Ilegivel("arquivo:formato".into()))?;
		Ok(ArquivoLido {
			id: id.clone(),
			nome: f.name,
			tipo: f.mimeType.unwrap_or_default(),
			tamanho: u64::try_from(f.size).unwrap_or(0),
			cid: f.cid,
			blobs: f.blobs,
			chave: Some(chave.session_key),
		})
	}

	/// Os bytes de um anexo (blobs baixados e decifrados com a chave do arquivo).
	/// Quem chama confere o TAMANHO antes (o cliente HTTP lê a resposta inteira).
	pub async fn baixar(&self, arquivo: &ArquivoLido) -> Result<Vec<u8>, FalhaTuta> {
		let Some(chave) = arquivo.chave.clone() else {
			return Err(FalhaTuta::Ilegivel("anexo:chave".into()));
		};
		let r = sem_panico(async {
			let mut por_arquivo: Vec<(GeneratedId, Vec<GeneratedId>)> = Vec::new();
			for b in &arquivo.blobs {
				match por_arquivo.iter_mut().find(|(a, _)| *a == b.archiveId) {
					Some((_, ids)) => ids.push(b.blobId.clone()),
					None => por_arquivo.push((b.archiveId.clone(), vec![b.blobId.clone()])),
				}
			}
			let mut pedacos = std::collections::HashMap::new();
			for (arquivo_blob, ids) in por_arquivo {
				let baixados = self
					.sdk
					.blob_facade()
					.download_blobs(&arquivo_blob, &arquivo.id, ArchiveDataType::Attachments, &ids)
					.await
					.map_err(|e| falha("anexo", &e))?;
				for (id, cifrado) in baixados {
					let claro = chave
						.decrypt_data(&cifrado)
						.map_err(|_| FalhaTuta::Ilegivel("anexo:decifrar".into()))?;
					pedacos.insert((arquivo_blob.clone(), id), claro);
				}
			}
			let mut saida = Vec::new();
			for b in &arquivo.blobs {
				let pedaco = pedacos
					.remove(&(b.archiveId.clone(), b.blobId.clone()))
					.ok_or_else(|| FalhaTuta::Ilegivel("anexo:faltou".into()))?;
				saida.extend_from_slice(&pedaco);
			}
			Ok::<_, FalhaTuta>(saida)
		})
		.await;
		match r {
			Ok(r) => r,
			Err(_) => Err(self.panico("anexo")),
		}
	}
}

/// As pastas com caminho ("Pai/Filho") e o nome das do sistema.
fn montar_pastas(lidas: &[MailSet]) -> Vec<PastaTuta> {
	let por_id: std::collections::HashMap<String, &MailSet> = lidas
		.iter()
		.filter_map(|m| m._id.as_ref().map(|id| (id.element_id.as_str().to_owned(), m)))
		.collect();
	let nome = |m: &MailSet| {
		if m.name.trim().is_empty() {
			nome_do_sistema(m.folderType).to_owned()
		} else {
			m.name.clone()
		}
	};
	let mut saida = Vec::new();
	for m in lidas {
		let Some(id) = m._id.as_ref().map(|i| i.element_id.as_str().to_owned()) else {
			continue;
		};
		let mut partes = vec![nome(m)];
		let mut atual = m.parentFolder.as_ref().map(|p| p.element_id.as_str().to_owned());
		let mut voltas = 0;
		while let Some(pai) = atual {
			voltas += 1;
			if voltas > 20 {
				break;
			}
			match por_id.get(&pai) {
				Some(p) => {
					partes.push(nome(p));
					atual = p.parentFolder.as_ref().map(|x| x.element_id.as_str().to_owned());
				},
				None => break,
			}
		}
		partes.reverse();
		saida.push(PastaTuta {
			id,
			lista_entradas: m.entries.clone(),
			nome: nome(m),
			tipo: m.folderType,
			pai: m.parentFolder.as_ref().map(|p| p.element_id.as_str().to_owned()),
			caminho: partes.join("/"),
		});
	}
	saida
}
