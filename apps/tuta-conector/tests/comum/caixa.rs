//! Um Tuta FALSO com caixa de verdade (só nos testes, em 127.0.0.1): as
//! entidades são CIFRADAS como no servidor (chave de grupo → chave de sessão
//! → valores), serializadas pelo PRÓPRIO SDK oficial no formato do servidor
//! (ids de atributo), e o conector as lê pelo `Sdk::login` + leitura de
//! verdade. Nenhuma conta, nenhum dado real.
//!
//! O "serializador" é um LoggedInSdk de fábrica, aberto com os dados
//! GRAVADOS do teste oficial do Tuta (`tuta-sdk/rust/sdk/tests/
//! download_mail_test`, conta de teste deles) por um cliente de teste em
//! memória — ele só serve para transformar as nossas entidades em JSON
//! cifrado (`serialize_instance_to_json`), nunca fala com rede.
#![allow(dead_code)]

use super::{Pedido, Resposta, ServidorFalso};
use base64::engine::general_purpose::{STANDARD as BASE64, URL_SAFE_NO_PAD};
use base64::Engine;
use crypto_primitives::aes::{Aes256Key, InitializationVector};
use crypto_primitives::key::GenericAesKey;
use crypto_primitives::randomizer_facade::RandomizerFacade;
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use tutasdk::bindings::rest_client::HttpMethod;
use tutasdk::bindings::test_file_client::TestFileClient;
use tutasdk::bindings::test_rest_client::TestRestClient;
use tutasdk::date::DateTime;
use tutasdk::entities::entity_facade::EntityFacadeImpl;
use tutasdk::entities::generated::storage::{BlobAccessTokenPostOut, BlobServerAccessInfo, BlobServerUrl};
use tutasdk::entities::generated::sys::{Blob, Group, GroupInfo, GroupKeysRef, GroupMembership, MailAddressAlias, Session, User};
use tutasdk::entities::generated::tutanota::{
	Body, ConversationEntry, DraftCreateReturn, Header, Mail, MailAddress, MailBox, MailDetails,
	MailDetailsBlob, MailSet, MailSetEntry, MailSetRef, MailboxGroupRoot, Recipients, SendDraftReturn, SpamResults,
	TutanotaFile,
};
use tutasdk::entities::Entity;
use tutasdk::login::{CredentialType, Credentials};
use tutasdk::type_model_provider::{ApplicationTypesGetOut, CLIENT_TYPE_MODEL};
use tutasdk::util::BASE64_EXT;
use tutasdk::{CustomId, GeneratedId, IdTupleCustom, IdTupleGenerated, LoggedInSdk, Sdk};

pub const HASH_MODELOS: &str = "hash-dos-modelos-falso";

// ── A fábrica (serializador do SDK) ─────────────────────────────────────

/// O LoggedInSdk que só serializa (aberto com os dados gravados do teste
/// oficial `download_mail_test`, sem rede).
pub fn fabrica() -> Arc<LoggedInSdk> {
	static FABRICA: OnceLock<Arc<LoggedInSdk>> = OnceLock::new();
	FABRICA
		.get_or_init(|| {
			std::thread::spawn(|| {
				let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
				rt.block_on(async {
					let base = "http://localhost:9000";
					let pasta = concat!(
						env!("CARGO_MANIFEST_DIR"),
						"/vendor/tutanota/tuta-sdk/rust/sdk/tests/download_mail_test"
					);
					let sessao = std::fs::read(format!("{pasta}/session.json")).unwrap();
					// A tag 361 ganhou o User.plugins (atributo 2811) e o JSON gravado
					// não: o mesmo acréscimo que o Tuta fez no master (so-testes/00).
					let mut usuario: Value =
						serde_json::from_slice(&std::fs::read(format!("{pasta}/user.json")).unwrap()).unwrap();
					usuario["2811"] = Value::Array(vec![]);
					let usuario = serde_json::to_vec(&usuario).unwrap();
					let mut cliente = TestRestClient::new(base);
					cliente.insert_response(
						"http://localhost:9000/rest/sys/Session/O1qC702-1J-0/3u3i8Lr9_7TnDDdAVw7w3TypTD2k1L00vIUTMF0SIPY",
						HttpMethod::GET,
						200,
						HashMap::default(),
						Some(&sessao),
					);
					cliente.insert_response(
						"http://localhost:9000/rest/sys/User/O1qC700----0",
						HttpMethod::GET,
						200,
						HashMap::default(),
						Some(&usuario),
					);
					let chave = BASE64
						.decode("AZWEA/KTrHu0bW52CsctsBTTV4U3jrU51TadSxf6Nqs3xbEs3WfoOpPtxUDCNjHNppt6LHCfgTioejjGUJ2cCsXosZAysUiau5Nvyi8mtjLz")
						.unwrap();
					let sdk = Sdk::new(base.into(), Arc::new(cliente), Arc::new(TestFileClient::default()));
					sdk.login(Credentials {
						login: "bed-free@tutanota.de".into(),
						user_id: GeneratedId("O1qC700----0".into()),
						access_token: "ZC2NIBDACUABAdJhibIwclzaPU3fEu-NzQ".into(),
						encrypted_passphrase_key: chave,
						credential_type: CredentialType::Internal,
					})
					.await
					.expect("fábrica de JSON (dados gravados do teste oficial)")
				})
			})
			.join()
			.unwrap()
		})
		.clone()
}

fn aleatorio() -> RandomizerFacade {
	RandomizerFacade::from_core(rand_core::OsRng)
}

fn chave_nova() -> GenericAesKey {
	GenericAesKey::Aes256(Aes256Key::generate(&aleatorio()))
}

fn iv() -> InitializationVector {
	InitializationVector::generate(&aleatorio())
}

fn json<E: Entity + serde::Serialize>(e: E, chave: &GenericAesKey) -> String {
	fabrica().serialize_instance_to_json(e, chave.clone()).expect("serializar entidade de teste")
}

static CONTADOR_ID: AtomicU64 = AtomicU64::new(1);

/// Um GeneratedId novo, crescente (9 bytes em base64ext, como o do Tuta).
pub fn id_novo() -> GeneratedId {
	let n = CONTADOR_ID.fetch_add(1, Ordering::SeqCst);
	let mut b = [0u8; 9];
	b[1..].copy_from_slice(&n.to_be_bytes());
	b[0] = 0x10;
	GeneratedId(BASE64_EXT.encode(b))
}

fn id_agregado() -> Option<CustomId> {
	Some(CustomId(URL_SAFE_NO_PAD.encode(rand_core::RngCore::next_u32(&mut rand_core::OsRng).to_be_bytes())))
}

/// O id do MailSetEntry (EntityUtils.ts constructMailSetEntryId): 4 bytes
/// de (recebido >> 10) + os 9 bytes do id do e-mail, em base64url.
pub fn id_entrada(recebido_ms: u64, mail: &GeneratedId) -> CustomId {
	let mut b = [0u8; 13];
	b[..4].copy_from_slice(&((recebido_ms >> 10) as u32).to_be_bytes());
	b[4..].copy_from_slice(&BASE64_EXT.decode(mail.as_str()).unwrap());
	CustomId(URL_SAFE_NO_PAD.encode(b))
}

fn bytes_da_entrada(id: &str) -> Vec<u8> {
	URL_SAFE_NO_PAD.decode(id).unwrap_or_else(|_| vec![0xff; 255])
}

fn endereco(nome: &str, endereco: &str) -> MailAddress {
	MailAddress {
		_id: id_agregado(),
		name: nome.into(),
		address: endereco.into(),
		contact: None,
		_errors: Default::default(),
	}
}

// ── A caixa ─────────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct Pasta {
	pub id: GeneratedId,
	pub entradas: GeneratedId,
	pub nome: String,
	pub tipo: i64,
	pub pai: Option<GeneratedId>,
	sk: GenericAesKey,
}

#[derive(Clone)]
pub struct Anexo {
	pub nome: String,
	pub tipo: String,
	pub bytes: Vec<u8>,
	pub cid: Option<String>,
}

/// Um e-mail a pôr na caixa.
#[derive(Clone)]
pub struct NovoEmail {
	pub pasta: GeneratedId,
	pub de: (String, String),
	pub para: Vec<(String, String)>,
	pub cc: Vec<(String, String)>,
	pub reply_to: Vec<(String, String)>,
	pub assunto: String,
	pub corpo_html: String,
	pub cabecalhos: String,
	pub message_id: String,
	pub recebido_ms: u64,
	pub anexos: Vec<Anexo>,
	pub rotulos: Vec<GeneratedId>,
	pub nao_lido: bool,
	pub resposta: i64,
	pub phishing: i64,
	pub auth: Option<i64>,
	/// Mail.state: 2 recebido (padrão), 1 enviado, 0 rascunho, 3 enviando.
	pub estado: i64,
	/// O e-mail a que este responde (o fio do Tuta: ConversationEntry.previous).
	pub anterior: Option<IdTupleGenerated>,
	pub envelope: Option<String>,
	pub processar: bool,
}

impl NovoEmail {
	pub fn simples(pasta: &GeneratedId, assunto: &str, recebido_ms: u64) -> Self {
		let mid = format!("<{}@exemplo.test>", assunto.replace(' ', "-"));
		Self {
			pasta: pasta.clone(),
			de: ("Cliente Teste".into(), "cliente@exemplo.com.br".into()),
			para: vec![("Loja".into(), "21max@tuta.com".into())],
			cc: vec![],
			reply_to: vec![],
			assunto: assunto.into(),
			corpo_html: format!("<p>Olá, {assunto}</p>"),
			cabecalhos: format!(
				"Message-ID: {mid}\r\nFrom: cliente@exemplo.com.br\r\nTo: 21max@tuta.com\r\nSubject: {assunto}\r\n"
			),
			message_id: mid,
			recebido_ms,
			anexos: vec![],
			rotulos: vec![],
			nao_lido: true,
			resposta: 0,
			phishing: 0,
			auth: Some(0),
			estado: 2,
			anterior: None,
			envelope: None,
			processar: false,
		}
	}
}

/// Um rascunho que o conector criou (DraftService), guardado como chegou.
pub struct RascunhoGuardado {
	pub id: IdTupleGenerated,
	pub corpo: Vec<u8>,
}

/// Um envio que o conector fez (SendDraftService), já decifrado com a chave
/// que ele mandou — o que o Tuta mandaria para fora.
#[derive(Clone, Debug)]
pub struct EnvioFeito {
	pub rascunho: IdTupleGenerated,
	pub previous_message_id: Option<String>,
	pub conversation_type: i64,
	pub assunto: String,
	pub corpo: String,
	pub remetente: String,
	pub nome_remetente: String,
	pub para: Vec<String>,
	pub confidencial: bool,
	pub plaintext: bool,
	/// A chave do envio é a mesma que o rascunho guardou para o grupo de e-mail.
	pub chave_confere: bool,
	pub message_id: String,
}

struct EmailGuardado {
	mail: Mail,
	sk: GenericAesKey,
	conversa: ConversationEntry,
	detalhes: Option<MailDetailsBlob>,
	/// Valor de assunto trocado por bytes ruins (o e-mail não decifra).
	estragado: bool,
}

struct ArquivoGuardado {
	arquivo: TutanotaFile,
	sk: GenericAesKey,
}

/// Uma falha programada: caminho (contém), método opcional, status, corpo e quantas vezes.
#[derive(Clone)]
pub struct Falha {
	pub caminho_contem: String,
	pub metodo: Option<&'static str>,
	pub status: u16,
	pub corpo: Option<Vec<u8>>,
	pub cabecalhos: Vec<(String, String)>,
	/// None = sempre.
	pub vezes: Option<u32>,
}

impl Falha {
	pub fn status(caminho: &str, status: u16) -> Self {
		Self {
			caminho_contem: caminho.into(),
			metodo: None,
			status,
			corpo: None,
			cabecalhos: vec![],
			vezes: None,
		}
	}
	pub fn uma_vez(mut self) -> Self {
		self.vezes = Some(1);
		self
	}
	pub fn vezes(mut self, n: u32) -> Self {
		self.vezes = Some(n);
		self
	}
	pub fn com_corpo(mut self, corpo: &[u8]) -> Self {
		self.corpo = Some(corpo.to_vec());
		self
	}
	pub fn com_cabecalho(mut self, nome: &str, valor: &str) -> Self {
		self.cabecalhos.push((nome.into(), valor.into()));
		self
	}
}

pub struct CaixaFalsa {
	pub email_conta: String,
	pub access_token: String,
	pub user_id: GeneratedId,
	passphrase: GenericAesKey,
	access_key: GenericAesKey,
	grupo_usuario: GenericAesKey,
	grupo_email: GenericAesKey,
	pub id_grupo_usuario: GeneratedId,
	pub id_grupo_email: GeneratedId,
	id_info_usuario: IdTupleGenerated,
	pub id_mailbox: GeneratedId,
	pub lista_pastas: GeneratedId,
	lista_emails: GeneratedId,
	lista_conversas: GeneratedId,
	lista_arquivos: GeneratedId,
	arquivo_detalhes: GeneratedId,
	arquivo_blobs: GeneratedId,
	pub pastas: Vec<Pasta>,
	/// entradas[lista de entradas] = (bytes do id → (id, e-mail)).
	entradas: HashMap<String, BTreeMap<Vec<u8>, (CustomId, IdTupleGenerated)>>,
	emails: HashMap<String, EmailGuardado>,
	arquivos: HashMap<String, ArquivoGuardado>,
	blobs: HashMap<String, Vec<u8>>,
	pub aliases: Vec<(String, bool)>,
	pub falhas: Vec<Falha>,
	/// Pastas (por id) cuja lista de entradas responde lixo (o SDK entra em pânico).
	pub pastas_com_lixo: Vec<GeneratedId>,
	/// MailSets estragados (nome que não decifra).
	pub pastas_estragadas: Vec<GeneratedId>,
	pub entrada: GeneratedId,
	pub enviados: GeneratedId,
	pub lixeira: GeneratedId,
	pub spam: GeneratedId,
	pub rascunhos: GeneratedId,
	pub arquivo: GeneratedId,
	/// O que o ENVIO do conector fez (os testes de envio conferem).
	pub rascunhos_criados: Vec<RascunhoGuardado>,
	pub envios: Vec<EnvioFeito>,
}

impl CaixaFalsa {
	pub fn nova() -> Self {
		let token_bytes: Vec<u8> = (1u8..=24).collect();
		let mut caixa = Self {
			email_conta: "conta.teste@tuta.com".into(),
			access_token: URL_SAFE_NO_PAD.encode(&token_bytes),
			user_id: id_novo(),
			passphrase: chave_nova(),
			access_key: chave_nova(),
			grupo_usuario: chave_nova(),
			grupo_email: chave_nova(),
			id_grupo_usuario: id_novo(),
			id_grupo_email: id_novo(),
			id_info_usuario: IdTupleGenerated::new(id_novo(), id_novo()),
			id_mailbox: id_novo(),
			lista_pastas: id_novo(),
			lista_emails: id_novo(),
			lista_conversas: id_novo(),
			lista_arquivos: id_novo(),
			arquivo_detalhes: id_novo(),
			arquivo_blobs: id_novo(),
			pastas: vec![],
			entradas: HashMap::new(),
			emails: HashMap::new(),
			arquivos: HashMap::new(),
			blobs: HashMap::new(),
			aliases: vec![("21max@tuta.com".into(), true), ("22max@tuta.com".into(), true), ("antigo@tuta.com".into(), false)],
			falhas: vec![],
			pastas_com_lixo: vec![],
			pastas_estragadas: vec![],
			entrada: GeneratedId::min_id(),
			enviados: GeneratedId::min_id(),
			lixeira: GeneratedId::min_id(),
			spam: GeneratedId::min_id(),
			rascunhos: GeneratedId::min_id(),
			arquivo: GeneratedId::min_id(),
			rascunhos_criados: vec![],
			envios: vec![],
		};
		caixa.entrada = caixa.pasta("", 1, None);
		caixa.enviados = caixa.pasta("", 2, None);
		caixa.lixeira = caixa.pasta("", 3, None);
		caixa.arquivo = caixa.pasta("", 4, None);
		caixa.spam = caixa.pasta("", 5, None);
		caixa.rascunhos = caixa.pasta("", 6, None);
		caixa
	}

	pub fn credenciais(&self) -> Credentials {
		Credentials {
			login: self.email_conta.clone(),
			user_id: self.user_id.clone(),
			access_token: self.access_token.clone(),
			encrypted_passphrase_key: self.access_key.encrypt_key(&self.passphrase, iv()),
			credential_type: CredentialType::Internal,
		}
	}

	/// Cria uma pasta (MailSet) e devolve o id.
	pub fn pasta(&mut self, nome: &str, tipo: i64, pai: Option<&GeneratedId>) -> GeneratedId {
		let p = Pasta {
			id: id_novo(),
			entradas: id_novo(),
			nome: nome.into(),
			tipo,
			pai: pai.cloned(),
			sk: chave_nova(),
		};
		let id = p.id.clone();
		self.entradas.insert(p.entradas.as_str().to_owned(), BTreeMap::new());
		self.pastas.push(p);
		id
	}

	fn pasta_por_id(&self, id: &GeneratedId) -> &Pasta {
		self.pastas.iter().find(|p| &p.id == id).expect("pasta")
	}

	/// Põe um e-mail na caixa (Mail + ConversationEntry + MailDetailsBlob + arquivos + entrada na pasta).
	pub fn email(&mut self, novo: NovoEmail) -> IdTupleGenerated {
		let mail_id = IdTupleGenerated::new(self.lista_emails.clone(), id_novo());
		let sk = chave_nova();
		let conversa_id = IdTupleGenerated::new(self.lista_conversas.clone(), id_novo());
		let detalhes_id = IdTupleGenerated::new(self.arquivo_detalhes.clone(), id_novo());
		let mut anexos = vec![];
		for a in &novo.anexos {
			let arquivo_id = IdTupleGenerated::new(self.lista_arquivos.clone(), id_novo());
			let ask = chave_nova();
			let blob_id = id_novo();
			let cifrado = ask.encrypt_data(&a.bytes, iv()).unwrap();
			self.blobs.insert(blob_id.as_str().to_owned(), cifrado);
			let arquivo = TutanotaFile {
				_id: Some(arquivo_id.clone()),
				_permissions: id_novo(),
				_format: 0,
				_ownerEncSessionKey: Some(self.grupo_email.encrypt_key(&ask, iv())),
				name: a.nome.clone(),
				size: a.bytes.len() as i64,
				mimeType: Some(a.tipo.clone()),
				_ownerGroup: Some(self.id_grupo_email.clone()),
				cid: a.cid.clone(),
				_ownerKeyVersion: Some(0),
				_kdfNonce: None,
				parent: None,
				subFiles: None,
				blobs: vec![Blob {
					_id: id_agregado(),
					archiveId: self.arquivo_blobs.clone(),
					size: a.bytes.len() as i64,
					blobId: blob_id,
				}],
				_errors: Default::default(),
			};
			self.arquivos.insert(chave_tupla(&arquivo_id), ArquivoGuardado { arquivo, sk: ask });
			anexos.push(arquivo_id);
		}
		let mut sets = vec![IdTupleGenerated::new(self.lista_pastas.clone(), novo.pasta.clone())];
		for r in &novo.rotulos {
			sets.push(IdTupleGenerated::new(self.lista_pastas.clone(), r.clone()));
		}
		let mail = Mail {
			_id: Some(mail_id.clone()),
			_permissions: id_novo(),
			_format: 0,
			_ownerEncSessionKey: Some(self.grupo_email.encrypt_key(&sk, iv())),
			subject: novo.assunto.clone(),
			receivedDate: DateTime::from_millis(novo.recebido_ms),
			state: novo.estado,
			unread: novo.nao_lido,
			confidential: false,
			replyType: novo.resposta,
			_ownerGroup: Some(self.id_grupo_email.clone()),
			differentEnvelopeSender: novo.envelope.clone(),
			listUnsubscribe: false,
			movedTime: None,
			phishingStatus: novo.phishing,
			authStatus: novo.auth,
			method: 0,
			recipientCount: novo.para.len() as i64,
			encryptionAuthStatus: None,
			_ownerKeyVersion: Some(0),
			processingState: 0,
			processNeeded: novo.processar,
			sendAt: None,
			serverClassificationData: None,
			_kdfNonce: None,
			sender: endereco(&novo.de.0, &novo.de.1),
			attachments: anexos,
			conversationEntry: conversa_id.clone(),
			firstRecipient: novo.para.first().map(|(n, e)| endereco(n, e)),
			mailDetails: Some(detalhes_id.clone()),
			mailDetailsDraft: None,
			bucketKey: None,
			sets,
			clientSpamClassifierResult: None,
			_errors: Default::default(),
		};
		let lista = |v: &Vec<(String, String)>| v.iter().map(|(n, e)| endereco(n, e)).collect::<Vec<_>>();
		let detalhes = MailDetailsBlob {
			_id: Some(detalhes_id),
			_permissions: id_novo(),
			_format: 0,
			_ownerGroup: Some(self.id_grupo_email.clone()),
			_ownerEncSessionKey: None,
			_ownerKeyVersion: None,
			_kdfNonce: None,
			details: MailDetails {
				_id: id_agregado(),
				sentDate: DateTime::from_millis(novo.recebido_ms.saturating_sub(5_000)),
				authStatus: 0,
				replyTos: novo
					.reply_to
					.iter()
					.map(|(n, e)| tutasdk::entities::generated::tutanota::EncryptedMailAddress {
						_id: id_agregado(),
						name: n.clone(),
						address: e.clone(),
						_errors: Default::default(),
					})
					.collect(),
				recipients: Recipients {
					_id: id_agregado(),
					toRecipients: lista(&novo.para),
					ccRecipients: lista(&novo.cc),
					bccRecipients: vec![],
				},
				headers: Some(Header {
					_id: id_agregado(),
					headers: None,
					compressedHeaders: Some(novo.cabecalhos.clone()),
					_errors: Default::default(),
				}),
				body: Body {
					_id: id_agregado(),
					text: None,
					compressedText: Some(novo.corpo_html.clone()),
					_errors: Default::default(),
				},
			},
			_errors: Default::default(),
		};
		let anterior = novo
			.anterior
			.as_ref()
			.and_then(|a| self.emails.get(&chave_tupla(a)))
			.and_then(|g| g.conversa._id.clone());
		let conversa = ConversationEntry {
			_id: Some(conversa_id),
			_permissions: id_novo(),
			_format: 0,
			messageId: novo.message_id.clone(),
			conversationType: if anterior.is_some() { 1 } else { 0 },
			_ownerGroup: Some(self.id_grupo_email.clone()),
			previous: anterior,
			mail: Some(mail_id.clone()),
		};
		self.emails.insert(
			chave_tupla(&mail_id),
			EmailGuardado {
				mail,
				sk,
				conversa,
				detalhes: Some(detalhes),
				estragado: false,
			},
		);
		self.por_na_pasta(&mail_id, &novo.pasta, novo.recebido_ms);
		for r in &novo.rotulos {
			self.por_na_pasta(&mail_id, r, novo.recebido_ms);
		}
		mail_id
	}

	fn por_na_pasta(&mut self, mail: &IdTupleGenerated, pasta: &GeneratedId, recebido_ms: u64) {
		let lista = self.pasta_por_id(pasta).entradas.as_str().to_owned();
		let id = id_entrada(recebido_ms, &mail.element_id);
		self.entradas
			.get_mut(&lista)
			.unwrap()
			.insert(bytes_da_entrada(id.as_str()), (id, mail.clone()));
	}

	fn tirar_da_pasta(&mut self, mail: &IdTupleGenerated, pasta: &GeneratedId) {
		let lista = self.pasta_por_id(pasta).entradas.as_str().to_owned();
		self.entradas.get_mut(&lista).unwrap().retain(|_, (_, m)| m != mail);
	}

	/// Move o e-mail para outra pasta (como o Tuta: a entrada na pasta nova
	/// fica na posição da DATA DE RECEBIMENTO, não no topo).
	pub fn mover(&mut self, mail: &IdTupleGenerated, para: &GeneratedId) {
		let guardado = self.emails.get(&chave_tupla(mail)).expect("e-mail");
		let de = guardado.mail.sets[0].element_id.clone();
		let recebido = guardado.mail.receivedDate.as_millis();
		self.tirar_da_pasta(mail, &de);
		self.por_na_pasta(mail, para, recebido);
		let g = self.emails.get_mut(&chave_tupla(mail)).unwrap();
		g.mail.sets[0] = IdTupleGenerated::new(self.lista_pastas.clone(), para.clone());
	}

	/// Apaga de vez (some de todas as pastas e o Mail dá 404).
	pub fn apagar_de_vez(&mut self, mail: &IdTupleGenerated) {
		let sets: Vec<GeneratedId> = self.emails[&chave_tupla(mail)].mail.sets.iter().map(|s| s.element_id.clone()).collect();
		for s in sets {
			self.tirar_da_pasta(mail, &s);
		}
		self.emails.remove(&chave_tupla(mail));
	}

	/// O assunto passa a ser um valor AEAD (versão 3) que não confere: o
	/// e-mail inteiro não decifra (igual a um valor gravado em AEAD com chave
	/// que não bate).
	pub fn estragar_email(&mut self, mail: &IdTupleGenerated) {
		self.emails.get_mut(&chave_tupla(mail)).unwrap().estragado = true;
	}

	/// O e-mail volta a decifrar (ex.: o SDK passou a ler o formato).
	pub fn consertar_email(&mut self, mail: &IdTupleGenerated) {
		self.emails.get_mut(&chave_tupla(mail)).unwrap().estragado = false;
	}

	/// O e-mail ainda espera as regras da caixa (`processNeeded`).
	pub fn marcar_processar(&mut self, mail: &IdTupleGenerated) {
		self.emails.get_mut(&chave_tupla(mail)).unwrap().mail.processNeeded = true;
	}

	pub fn entradas_da_pasta(&self, pasta: &GeneratedId) -> usize {
		self.entradas[self.pasta_por_id(pasta).entradas.as_str()].len()
	}

	// ── O servidor ──────────────────────────────────────────────────────

	fn json_do_email(&self, g: &EmailGuardado) -> String {
		let texto = json(g.mail.clone(), &g.sk);
		if !g.estragado {
			return texto;
		}
		let mut v: Value = serde_json::from_str(&texto).unwrap();
		// 105 = Mail.subject. Versão 3 (AEAD com chave de sessão), tamanho ímpar, MAC errado.
		let mut ruim = vec![3u8];
		ruim.extend(std::iter::repeat_n(7u8, 64));
		v["105"] = Value::String(BASE64.encode(ruim));
		v.to_string()
	}

	fn json_da_pasta(&self, p: &Pasta) -> String {
		let ms = MailSet {
			_id: Some(IdTupleGenerated::new(self.lista_pastas.clone(), p.id.clone())),
			_permissions: id_novo(),
			_format: 0,
			_ownerEncSessionKey: Some(self.grupo_email.encrypt_key(&p.sk, iv())),
			name: p.nome.clone(),
			folderType: p.tipo,
			_ownerGroup: Some(self.id_grupo_email.clone()),
			_ownerKeyVersion: Some(0),
			color: None,
			_kdfNonce: None,
			parentFolder: p.pai.clone().map(|pai| IdTupleGenerated::new(self.lista_pastas.clone(), pai)),
			entries: p.entradas.clone(),
			_errors: Default::default(),
		};
		let texto = json(ms, &p.sk);
		if !self.pastas_estragadas.contains(&p.id) {
			return texto;
		}
		let mut v: Value = serde_json::from_str(&texto).unwrap();
		let mut ruim = vec![3u8];
		ruim.extend(std::iter::repeat_n(9u8, 64));
		v["435"] = Value::String(BASE64.encode(ruim));
		v.to_string()
	}

	fn atender(&mut self, p: &Pedido, base: &str) -> Resposta {
		for f in self.falhas.iter_mut() {
			let metodo_ok = f.metodo.is_none_or(|m| m == p.metodo);
			let restam = f.vezes.is_none_or(|v| v > 0);
			if metodo_ok && restam && p.caminho.contains(&f.caminho_contem) {
				if let Some(v) = f.vezes.as_mut() {
					*v -= 1;
				}
				let mut r = Resposta {
					status: f.status,
					cabecalhos: vec![("app-types-hash".into(), HASH_MODELOS.into())],
					corpo: f.corpo.clone().unwrap_or_default(),
				};
				r.cabecalhos.extend(f.cabecalhos.iter().cloned());
				return r;
			}
		}
		let partes: Vec<&str> = p.caminho.trim_start_matches('/').split('/').collect();
		let ok = |corpo: String| Resposta {
			status: 200,
			cabecalhos: vec![
				("Content-Type".into(), "application/json".into()),
				("app-types-hash".into(), HASH_MODELOS.into()),
			],
			corpo: corpo.into_bytes(),
		};
		let nao_achei = || Resposta {
			status: 404,
			cabecalhos: vec![("app-types-hash".into(), HASH_MODELOS.into())],
			corpo: vec![],
		};
		match (p.metodo.as_str(), partes.as_slice()) {
			("GET", ["rest", "base", "applicationtypesservice"]) => {
				let saida = ApplicationTypesGetOut {
					application_types_json: serde_json::to_string(&CLIENT_TYPE_MODEL.apps).unwrap(),
					application_types_hash: HASH_MODELOS.into(),
				};
				let texto = serde_json::to_string(&saida).unwrap();
				Resposta {
					status: 200,
					cabecalhos: vec![],
					corpo: EntityFacadeImpl::lz4_compress_plain_bytes(texto.as_bytes()).unwrap(),
				}
			},
			("GET", ["rest", "sys", "Session", ..]) => {
				if p.cabecalhos.get("accesstoken") != Some(&self.access_token) {
					return Resposta::vazia(401);
				}
				let s = Session {
					_id: Some(IdTupleCustom::new(id_novo(), CustomId("sessao".into()))),
					_permissions: id_novo(),
					_format: 0,
					_ownerGroup: Some(self.id_grupo_usuario.clone()),
					_ownerEncSessionKey: None,
					clientIdentifier: "DaVinci conector – Mac mini".into(),
					loginTime: DateTime::from_millis(1),
					loginIpAddress: None,
					lastAccessTime: DateTime::from_millis(1),
					accessKey: Some(self.access_key.as_bytes().to_vec()),
					state: 0,
					_ownerKeyVersion: None,
					_kdfNonce: None,
					challenges: vec![],
					user: self.user_id.clone(),
					_errors: Default::default(),
				};
				ok(json(s, &chave_nova()))
			},
			("GET", ["rest", "sys", "User", id]) if *id == self.user_id.as_str() => {
				let membro = |grupo: &GeneratedId, tipo: i64, chave: Vec<u8>, info: IdTupleGenerated| GroupMembership {
					_id: id_agregado(),
					symEncGKey: chave,
					admin: false,
					groupType: Some(tipo),
					capability: None,
					groupKeyVersion: 0,
					symKeyVersion: 0,
					group: grupo.clone(),
					groupInfo: info,
					groupMember: IdTupleGenerated::new(id_novo(), id_novo()),
				};
				let u = User {
					_id: Some(self.user_id.clone()),
					_permissions: id_novo(),
					_format: 0,
					salt: Some(vec![7; 16]),
					verifier: vec![1; 32],
					accountType: 2,
					enabled: true,
					_ownerGroup: Some(self.id_grupo_usuario.clone()),
					requirePasswordUpdate: false,
					kdfVersion: 1,
					userGroup: membro(
						&self.id_grupo_usuario,
						0,
						self.passphrase.encrypt_key(&self.grupo_usuario, iv()),
						self.id_info_usuario.clone(),
					),
					memberships: vec![membro(
						&self.id_grupo_email,
						5,
						self.grupo_usuario.encrypt_key(&self.grupo_email, iv()),
						IdTupleGenerated::new(id_novo(), id_novo()),
					)],
					authenticatedDevices: vec![],
					externalAuthInfo: None,
					customer: Some(id_novo()),
					successfulLogins: id_novo(),
					failedLogins: id_novo(),
					secondFactorAuthentications: id_novo(),
					pushIdentifierList: None,
					auth: None,
					alarmInfoList: None,
					plugins: None,
				};
				ok(json(u, &chave_nova()))
			},
			("GET", ["rest", "sys", "Group", id]) if *id == self.id_grupo_email.as_str() => {
				let g = Group {
					_id: Some(self.id_grupo_email.clone()),
					_permissions: id_novo(),
					_format: 0,
					r#type: 5,
					adminGroupEncGKey: None,
					enabled: true,
					_ownerGroup: None,
					external: false,
					adminGroupKeyVersion: None,
					groupKeyVersion: 0,
					currentKeys: None,
					admin: None,
					user: Some(self.user_id.clone()),
					customer: None,
					groupInfo: IdTupleGenerated::new(id_novo(), id_novo()),
					invitations: id_novo(),
					members: id_novo(),
					archives: vec![],
					storageCounter: None,
					formerGroupKeys: GroupKeysRef {
						_id: id_agregado(),
						list: id_novo(),
					},
					pubAdminGroupEncGKey: None,
					identityKeyPair: None,
				};
				ok(json(g, &chave_nova()))
			},
			("GET", ["rest", "sys", "GroupInfo", lista, elemento])
				if *lista == self.id_info_usuario.list_id.as_str()
					&& *elemento == self.id_info_usuario.element_id.as_str() =>
			{
				let sk = chave_nova();
				let gi = GroupInfo {
					_id: Some(self.id_info_usuario.clone()),
					_permissions: id_novo(),
					_format: 0,
					_listEncSessionKey: None,
					name: "Conta".into(),
					mailAddress: Some(self.email_conta.clone()),
					created: DateTime::from_millis(1),
					deleted: None,
					_ownerGroup: Some(self.id_grupo_email.clone()),
					_ownerEncSessionKey: Some(self.grupo_email.encrypt_key(&sk, iv())),
					groupType: Some(0),
					_ownerKeyVersion: Some(0),
					_kdfNonce: None,
					group: self.id_grupo_usuario.clone(),
					mailAddressAliases: self
						.aliases
						.iter()
						.map(|(a, ligado)| MailAddressAlias {
							_id: id_agregado(),
							mailAddress: a.clone(),
							enabled: *ligado,
						})
						.collect(),
					_errors: Default::default(),
				};
				ok(json(gi, &sk))
			},
			("GET", ["rest", "tutanota", "MailboxGroupRoot", id]) if *id == self.id_grupo_email.as_str() => {
				let r = MailboxGroupRoot {
					_id: Some(self.id_grupo_email.clone()),
					_permissions: id_novo(),
					_format: 0,
					_ownerGroup: Some(self.id_grupo_email.clone()),
					mailbox: self.id_mailbox.clone(),
					serverProperties: id_novo(),
					calendarEventUpdates: None,
					outOfOfficeNotification: None,
					outOfOfficeNotificationRecipientList: None,
					mailboxProperties: None,
				};
				ok(json(r, &chave_nova()))
			},
			("GET", ["rest", "tutanota", "MailBox", id]) if *id == self.id_mailbox.as_str() => {
				let sk = chave_nova();
				let m = MailBox {
					_id: Some(self.id_mailbox.clone()),
					_permissions: id_novo(),
					_format: 0,
					lastInfoDate: DateTime::from_millis(1),
					_ownerGroup: Some(self.id_grupo_email.clone()),
					_ownerEncSessionKey: Some(self.grupo_email.encrypt_key(&sk, iv())),
					_ownerKeyVersion: Some(0),
					_kdfNonce: None,
					sentAttachments: id_novo(),
					receivedAttachments: id_novo(),
					mailSets: MailSetRef {
						_id: id_agregado(),
						mailSets: self.lista_pastas.clone(),
					},
					spamResults: SpamResults {
						_id: id_agregado(),
						list: id_novo(),
					},
					mailDetailsDrafts: None,
					archivedMailBags: vec![],
					currentMailBag: None,
					importedAttachments: id_novo(),
					importFileMailStates: id_novo(),
					extractedFeatures: id_novo(),
					clientSpamTrainingData: id_novo(),
					modifiedClientSpamTrainingDataIndex: id_novo(),
					imapAccountSyncStates: None,
					deduplicatedImportedAttachments: None,
					_errors: Default::default(),
				};
				ok(json(m, &sk))
			},
			("GET", ["rest", "tutanota", "MailSet", lista]) if *lista == self.lista_pastas.as_str() => {
				let itens: Vec<String> = self.pastas.iter().map(|p| self.json_da_pasta(p)).collect();
				ok(format!("[{}]", itens.join(",")))
			},
			("GET", ["rest", "tutanota", "MailSetEntry", lista]) => {
				if let Some(p) = self.pastas.iter().find(|p| p.entradas.as_str() == *lista) {
					if self.pastas_com_lixo.contains(&p.id) {
						return ok("isto não é JSON".into());
					}
				}
				let Some(mapa) = self.entradas.get(*lista) else {
					return nao_achei();
				};
				let inicio = bytes_da_entrada(p.query.get("start").map(String::as_str).unwrap_or(""));
				let quantos: usize = p.query.get("count").and_then(|c| c.parse().ok()).unwrap_or(100);
				let reverso = p.query.get("reverse").map(String::as_str) == Some("true");
				let escolhidos: Vec<&(CustomId, IdTupleGenerated)> = if reverso {
					mapa.range(..inicio).rev().take(quantos).map(|(_, v)| v).collect()
				} else {
					mapa.range((std::ops::Bound::Excluded(inicio), std::ops::Bound::Unbounded))
						.take(quantos)
						.map(|(_, v)| v)
						.collect()
				};
				let itens: Vec<String> = escolhidos
					.into_iter()
					.map(|(id, mail)| {
						json(
							MailSetEntry {
								_id: Some(IdTupleCustom::new(GeneratedId((*lista).to_owned()), id.clone())),
								_permissions: id_novo(),
								_format: 0,
								_ownerGroup: Some(self.id_grupo_email.clone()),
								mail: mail.clone(),
							},
							&chave_nova(),
						)
					})
					.collect();
				ok(format!("[{}]", itens.join(",")))
			},
			("GET", ["rest", "tutanota", "Mail", lista, elemento]) => {
				match self.emails.get(&format!("{lista}/{elemento}")) {
					Some(g) => ok(self.json_do_email(g)),
					None => nao_achei(),
				}
			},
			("GET", ["rest", "tutanota", "ConversationEntry", lista, elemento]) => {
				let achado = self.emails.values().find(|g| {
					g.conversa._id.as_ref().is_some_and(|c| c.list_id.as_str() == *lista && c.element_id.as_str() == *elemento)
				});
				match achado {
					Some(g) => ok(json(g.conversa.clone(), &chave_nova())),
					None => nao_achei(),
				}
			},
			("GET", ["rest", "tutanota", "File", lista, elemento]) => {
				match self.arquivos.get(&format!("{lista}/{elemento}")) {
					Some(a) => ok(json(a.arquivo.clone(), &a.sk)),
					None => nao_achei(),
				}
			},
			("POST", ["rest", "storage", "blobaccesstokenservice"]) => {
				let saida = BlobAccessTokenPostOut {
					_format: 0,
					blobAccessInfo: BlobServerAccessInfo {
						_id: id_agregado(),
						blobAccessToken: "token-de-blob-falso".into(),
						expires: DateTime::from_millis(u64::from(u32::MAX) * 1000),
						tokenKind: 0,
						servers: vec![BlobServerUrl {
							_id: id_agregado(),
							url: base.to_owned(),
						}],
					},
				};
				ok(json(saida, &chave_nova()))
			},
			("GET", ["rest", "tutanota", "maildetailsblob", arquivo]) => {
				let ids = p.query.get("ids").cloned().unwrap_or_default();
				let achado = self.emails.values().find(|g| {
					g.mail.mailDetails.as_ref().is_some_and(|d| {
						d.list_id.as_str() == *arquivo && d.element_id.as_str() == ids
					})
				});
				match achado.and_then(|g| g.detalhes.clone().map(|d| (d, g.sk.clone()))) {
					Some((d, sk)) => ok(format!("[{}]", json_do_blob_de_detalhes(d, &sk))),
					None => nao_achei(),
				}
			},
			("POST", ["rest", "tutanota", "draftservice"]) => {
				let id = IdTupleGenerated::new(self.lista_emails.clone(), id_novo());
				self.rascunhos_criados.push(RascunhoGuardado {
					id: id.clone(),
					corpo: p.corpo.clone(),
				});
				ok(json(
					DraftCreateReturn {
						_format: 0,
						draft: id,
					},
					&chave_nova(),
				))
			},
			("POST", ["rest", "tutanota", "senddraftservice"]) => {
				let corpo: Value = serde_json::from_slice(&p.corpo).unwrap_or(Value::Null);
				// A associação vai como lista de um ([["lista", "elemento"]]).
				let par = corpo["556"].as_array().and_then(|a| a.first()).and_then(Value::as_array);
				let Some(rascunho) = par.and_then(|a| match a.as_slice() {
					[l, e] => Some(IdTupleGenerated::new(GeneratedId(l.as_str()?.into()), GeneratedId(e.as_str()?.into()))),
					_ => None,
				}) else {
					return Resposta::vazia(400);
				};
				let Some(guardado) = self.rascunhos_criados.iter().find(|r| r.id == rascunho) else {
					return nao_achei();
				};
				let chave = corpo["550"].as_str().and_then(|b| BASE64.decode(b).ok()).and_then(|b| GenericAesKey::from_bytes(&b).ok());
				let Some(chave) = chave else {
					return Resposta::vazia(400);
				};
				let Ok(dados) = decifrar_rascunho(&guardado.corpo, &chave) else {
					return Resposta::vazia(400);
				};
								let chave_confere = self
					.grupo_email
					.decrypt_aes_key(&dados.owner_enc_session_key)
					.is_ok_and(|k| k.as_bytes() == chave.as_bytes());
				let message_id = format!("<enviado-{}@tuta.com>", self.envios.len() + 1);
				let para = dados.para.clone();
				self.envios.push(EnvioFeito {
					rascunho: rascunho.clone(),
					previous_message_id: dados.previous_message_id.clone(),
					conversation_type: dados.conversation_type,
					assunto: dados.assunto.clone(),
					corpo: dados.corpo.clone(),
					remetente: dados.remetente.clone(),
					nome_remetente: dados.nome_remetente.clone(),
					para: para.clone(),
					confidencial: dados.confidencial,
					plaintext: corpo["675"] == "1",
					chave_confere,
					message_id: message_id.clone(),
				});
				// O enviado aparece em Enviados (estado 1), respondendo ao original.
				let original = self
					.emails
					.iter()
					.find(|(_, g)| Some(&g.conversa.messageId) == dados.previous_message_id.as_ref())
					.and_then(|(_, g)| g.mail._id.clone());
				let enviados = self.enviados.clone();
				let mut e = NovoEmail::simples(&enviados, &dados.assunto, base_agora());
				e.de = (dados.nome_remetente.clone(), dados.remetente.clone());
				e.para = para.iter().map(|x| (x.clone(), x.clone())).collect();
				e.message_id = message_id.clone();
				e.cabecalhos = String::new();
				e.corpo_html = dados.corpo.clone();
				e.estado = 1;
				e.nao_lido = false;
				e.anterior = original;
								let enviado = self.email(e);
								ok(json(
					SendDraftReturn {
						_format: 0,
						messageId: message_id,
						sentDate: DateTime::from_millis(base_agora()),
						notifications: vec![],
						sentMail: enviado,
						sendJob: None,
					},
					&chave_nova(),
				))
			},
			("GET", ["rest", "storage", "blobservice"]) => {
				let corpo: Value = p
					.query
					.get("_body")
					.and_then(|b| serde_json::from_str(b).ok())
					.unwrap_or(Value::Null);
				let mut textos = vec![];
				coletar_textos(&corpo, &mut textos);
				let pedidos: Vec<&String> = textos.iter().filter(|t| self.blobs.contains_key(*t)).collect();
				let mut saida = (pedidos.len() as i32).to_be_bytes().to_vec();
				for id in pedidos {
					let dados = &self.blobs[id];
					saida.extend(BASE64_EXT.decode(id).unwrap());
					saida.extend([0u8; 6]);
					saida.extend((dados.len() as i32).to_be_bytes());
					saida.extend(dados);
				}
				Resposta {
					status: 200,
					cabecalhos: vec![("app-types-hash".into(), HASH_MODELOS.into())],
					corpo: saida,
				}
			},
			_ => nao_achei(),
		}
	}

	/// Sobe o servidor. A caixa continua mexível pelo Arc (mover, apagar, falhas…).
	pub fn servir(self) -> (ServidorFalso, Arc<Mutex<CaixaFalsa>>) {
		let _ = fabrica();
		let caixa = Arc::new(Mutex::new(self));
		let base_compartilhada: Arc<Mutex<String>> = Arc::new(Mutex::new(String::new()));
		let c = caixa.clone();
		let b = base_compartilhada.clone();
		let servidor = ServidorFalso::iniciar(move |p| {
			let base = b.lock().unwrap().clone();
			c.lock().unwrap().atender(p, &base)
		});
		*base_compartilhada.lock().unwrap() = servidor.url.clone();
		(servidor, caixa)
	}
}

/// O mapeador do SDK não serializa o `_id` [arquivo, elemento] de um
/// BlobElement (o modelo diz GeneratedId). O MailDetailsDraft tem os MESMOS
/// detalhes (o mesmo agregado MailDetails, cifrado com a mesma chave): ele é
/// serializado pelo SDK e o JSON é remontado com os ids de atributo do
/// MailDetailsBlob, como o servidor manda.
fn json_do_blob_de_detalhes(d: MailDetailsBlob, sk: &GenericAesKey) -> String {
	let id = d._id.clone().unwrap();
	let rascunho = tutasdk::entities::generated::tutanota::MailDetailsDraft {
		_id: Some(id.clone()),
		_permissions: d._permissions.clone(),
		_format: 0,
		_ownerGroup: d._ownerGroup.clone(),
		_ownerEncSessionKey: None,
		_ownerKeyVersion: None,
		_kdfNonce: None,
		details: d.details,
		_errors: Default::default(),
	};
	let r: Value = serde_json::from_str(&json(rascunho, sk)).unwrap();
	serde_json::json!({
		"1300": [id.list_id.as_str(), id.element_id.as_str()],
		"1301": r["1293"],
		"1302": r["1294"],
		"1303": r["1295"],
		"1304": r["1296"],
		"1408": r["1407"],
		"1833": r["1830"],
		"1305": r["1297"],
	})
	.to_string()
}

/// O "agora" do Tuta falso para o que ele mesmo cria (o enviado).
static AGORA_DO_TUTA: AtomicU64 = AtomicU64::new(1_791_440_000_000);

pub fn base_agora() -> u64 {
	AGORA_DO_TUTA.load(Ordering::SeqCst)
}

pub fn acertar_agora(ms: u64) {
	AGORA_DO_TUTA.store(ms, Ordering::SeqCst);
}

/// O que o rascunho que o conector mandou diz (decifrado com a chave do envio).
pub struct RascunhoLido {
	pub previous_message_id: Option<String>,
	pub conversation_type: i64,
	pub owner_enc_session_key: Vec<u8>,
	pub assunto: String,
	pub corpo: String,
	pub remetente: String,
	pub nome_remetente: String,
	pub para: Vec<String>,
	pub confidencial: bool,
}

/// O DraftCreateData (ids de atributo do modelo do Tuta: 510 previousMessageId,
/// 511 conversationType, 512 ownerEncSessionKey, 515 draftData → 498 subject,
/// 499 bodyText, 500 senderMailAddress, 501 senderName, 502 confidential, 503
/// toRecipients → 484 name, 485 mailAddress). Os cifrados são AES com a chave
/// do e-mail — a mesma que o SendDraftService recebe em `mailSessionKey`.
fn decifrar_rascunho(corpo: &[u8], chave: &GenericAesKey) -> Result<RascunhoLido, String> {
	let v: Value = serde_json::from_slice(corpo).map_err(|e| format!("json {e}"))?;
	let texto = |x: &Value| -> Result<String, String> {
		let b = x.as_str().unwrap_or("");
		if b.is_empty() {
			return Ok(String::new());
		}
		let cifrado = BASE64.decode(b).map_err(|e| format!("base64 {e}"))?;
		let claro = chave.decrypt_data(&cifrado).map_err(|e| format!("aes {e:?}"))?;
		String::from_utf8(claro).map_err(|e| format!("utf8 {e}"))
	};
	let d = &v["515"][0];
	Ok(RascunhoLido {
		previous_message_id: v["510"].as_str().map(str::to_owned),
		conversation_type: v["511"].as_str().and_then(|x| x.parse().ok()).unwrap_or(-1),
		owner_enc_session_key: BASE64.decode(v["512"].as_str().unwrap_or("")).map_err(|e| format!("chave {e}"))?,
		assunto: texto(&d["498"])?,
		corpo: texto(&d["499"])?,
		remetente: d["500"].as_str().unwrap_or("").to_owned(),
		nome_remetente: texto(&d["501"])?,
		para: d["503"].as_array().map(|l| l.iter().filter_map(|r| r["485"].as_str().map(str::to_owned)).collect()).unwrap_or_default(),
		confidencial: texto(&d["502"])? == "1",
	})
}

fn chave_tupla(id: &IdTupleGenerated) -> String {
	format!("{}/{}", id.list_id.as_str(), id.element_id.as_str())
}

fn coletar_textos(v: &Value, saida: &mut Vec<String>) {
	match v {
		Value::String(s) => saida.push(s.clone()),
		Value::Array(a) => a.iter().for_each(|x| coletar_textos(x, saida)),
		Value::Object(o) => o.values().for_each(|x| coletar_textos(x, saida)),
		_ => {},
	}
}
