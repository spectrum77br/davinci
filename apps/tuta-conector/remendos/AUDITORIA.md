# Auditoria dos remendos no SDK oficial do Tuta

Feita em 08/10/2026 para a etapa 1 do conector. Toda linha que entra no SDK
por aqui passou a ser NOSSA: quem trocar um remendo refaz a auditoria dele
nesta página (e o `build.rs` recusa compilar se o sha256 mudar sem o
`scripts/atualizar-sdk.sh` rodar de novo).

Nada foi rodado contra o Tuta de verdade. Só leitura de código, testes
locais e servidores FALSOS em 127.0.0.1.

## 1. Base

| | |
|---|---|
| Release oficial | `tutanota-desktop-release-361.260929.0` (a 361 não tem tag `tutanota-release-*`; as tags desktop/android/ios da 361.260929.0 apontam TODAS para o mesmo commit) |
| Commit | `f7b4a552dbd0c3b95d2f937135f64a436cee8421` (conferido pelo script: a tag tem de apontar para ele) |
| Versão do SDK (`cv`) | `361.260929.0`, a do `Cargo.toml` do Tuta, nunca editada (o script recusa remendo que mexa em `Cargo.toml`) |
| O que é baixado | só `Cargo.toml`, `Cargo.lock`, `LICENSE.txt`, `rustfmt.toml`, `tuta-sdk/rust/`, `src/app-kit/mimimi/` (membro do workspace do Tuta) e o JSON de dados dos testes de cripto |
| De onde vêm os remendos | PR #52 da tutabridge (github.com/spartanz51/tutabridge, `pull/52/head` = `2da8887bf86489e90773d6e96af6736366b25f00`, buscado em 08/10/2026), pasta `sdk/patches/`. A base do PR é a 360.260922.0; os remendos aplicam na 361 sem "fuzz" (menos o 07, adaptado, ver abaixo) |

### Os arquivos (sha256)

| Remendo | sha256 aqui | Igual ao PR #52? |
|---|---|---|
| 01-optional-empty | `0d31c776…1a866` | idêntico |
| 05-blob-downloads | `80789402…077e2` | idêntico |
| 06-blob-elements | `b39ffec8…511c8` | idêntico |
| 07-parse-raw | `d1a533dd…4aae` | ADAPTADO (original `a90f8dcc…f046`) |
| 09-owner-session-key | `7f5fca2f…6454` | idêntico |
| 10-aead-session-reads | `e6313042…5f77` | idêntico |
| 11-aead-group-reads | `2e82066f…d5d` | idêntico |
| so-testes/00-teste-oficial-361-plugins | `44d14d9b…2060` | nosso; NUNCA entra no binário |

Hashes completos: `shasum -a 256 remendos/*.patch` (o `vendor/tutanota/.conector-base` guarda os mesmos).

### Por que estes (decisão 3) e não outros

- **01** (vazio opcional cifrado = nulo): sem ele o `Body` do e-mail (text OU compressedText) não decifra.
- **06 + 09** (ler o MailDetailsBlob = cabeçalhos brutos e corpo). O 06 depende do **05** (o `read.rs` e o cache de token de leitura nascem no 05) e entrega JSON cru; para virar entidade é preciso o **07** (`parse_raw`), porque o serializador do SDK é privado. Então o mínimo real para "ler o corpo" é 05 + 06 + 07 + 09.
- **Anexos**: o 05 já entra por causa do 06, e traz `download_blobs` (anexos) com testes. Fazer anexo "por fora" seria reescrever o mesmo protocolo (token por instância, formato binário, failover) com cripto nossa: o 05 é menor e mais seguro.
- **10 + 11** (ler AEAD): sem eles, UMA pasta ou e-mail gravado em AEAD derruba a lista inteira (`entity_facade.rs` só conhecia AES-CBC). Entram desde já.
- **Fora**: 02 e 03 (são do `create_session` do SDK, que não usamos: o login é nosso; e-mail normalizado no nosso código; conta Bcrypt vira erro claro), 04 (`load_multiple`, só velocidade), 08 (sessão com TOTP: feito por NÓS fora do SDK, `src/tuta/sessao.rs`).

## 2. Como foi auditado

1. Li cada linha `+` e `-` dos 7 remendos e o resultado montado em `vendor/tutanota` (`git -C vendor/tutanota diff` + os arquivos novos).
2. Procurei em todas as linhas adicionadas: rede (`request_binary`, URLs, hosts), log/print (`log::`, `println!`, `dbg!`), processo/ambiente/disco (`std::process`, `env`, `fs`), `unsafe`, `unwrap/expect/panic!` fora de teste, segredos em mensagens de erro.
3. Conferi o comportamento contra o cliente OFICIAL em TypeScript da mesma tag (caminhos abaixo) e contra o `crypto-primitives` oficial em Rust.
4. Rodei os testes do PRÓPRIO SDK com os remendos (seção 5).

## 3. Remendo por remendo

Linhas: "código" = entra no binário; "teste" = só em `#[cfg(test)]`.

### 01 — valor opcional cifrado vazio vira nulo (`json_serializer.rs:79-83`)
- **O que faz**: campo `ZeroOrOne` + cifrado + `""` → `Null`, em vez de mandar zero bytes ao AES (que dava `InvalidDataSizeError`). Igual ao TS (`CryptoMapper.decryptValue`).
- **Código**: 7 linhas. **Teste**: 21.
- **Rede / chaves / log / cripto**: nada. Não enfraquece a cripto: valor com conteúdo continua indo ao AES com MAC.
- **Veredito**: OK.

### 05 — baixar blobs referenciados por uma instância (anexos)
Arquivos: `blobs/blob_access_token_cache.rs`, `blobs/blob_access_token_facade.rs`, `blobs/blob_facade.rs` (+`mod read`), `blobs/blob_facade/read.rs` (novo).
- **O que faz**: `download_blobs` pede um token de LEITURA com o escopo da instância (archive + lista + elemento + tipo), baixa os blobs de 100 em 100 e devolve os bytes CIFRADOS por id. 403 → descarta o token e refaz a leitura inteira UMA vez (TS `doBlobRequestWithRetry`); erro de um servidor (conexão, 500, 404, rede, handshake) → tenta o próximo (TS `tryServers`). Tokens de leitura num cache separado do de escrita, com a chave = o escopo inteiro.
- **Código**: ~256 linhas em `read.rs` + ~85 nos outros. **Teste**: ~420 + ~110.
- **Rede**: GET `<server.url>/rest/storage/blobservice?...` para os servidores que o PRÓPRIO Tuta devolve em `BlobServerAccessInfo.servers` (o mesmo que o upload oficial já faz). A query leva `v`, `cv`, `accessToken`, `blobAccessToken` e `_body` com os ids — igual ao TS (`BlobAccessTokenFacade.createQueryParams`, `BlobAccessTokenFacade.ts:255-265`). No conector, o `ClienteRest` (`src/rede.rs`) só deixa sair para `app.tuta.com` e `*.tuta.com` em HTTPS; qualquer outro host é recusado antes de sair. O token de sessão vai na URL como no oficial; o conector nunca loga URL (só o host).
- **Chaves**: nenhuma. Devolve o blob cifrado; quem chama decifra com a chave de sessão do arquivo (`GenericAesKey::decrypt_data` oficial, AES-CBC + HMAC).
- **Log**: nenhum.
- **Pânico**: o parser binário (`parse_multiple_blobs_response`, `read.rs:225`) confere tamanhos ANTES de alocar (`count <= resto/19`, `split_at_checked`), recusa id repetido e sobra de bytes. Só fica o `.expect("poisoned lock")` do cache, padrão do código oficial.
- **Atenção para a etapa 2**: o cliente HTTP do SDK lê a resposta inteira em memória; o conector precisa conferir o `size` do arquivo ANTES de baixar (teto do contrato: 10 MB por anexo, 20 MB por e-mail).
- **Veredito**: OK.

### 06 — ler um elemento de blob (o MailDetailsBlob)
Arquivos: `blobs/blob_access_token_facade.rs` (`ReadTokenKey::Archive`), `blobs/blob_facade/read.rs` (`load_blob_element`).
- **O que faz**: token de leitura do ARQUIVO (archiveDataType nulo, `instanceListId` nulo, `instanceIds` vazio — igual a `requestReadTokenArchive`, `BlobAccessTokenFacade.ts:200-215`) e GET `/rest/<app>/<tipo>/<archiveId>?ids=<id>` nos servidores de blob — igual a `EntityRestClient.loadMultipleBlobElements` (`EntityRestClient.ts:321-337`). Recusa tipo que não seja BlobElement. Devolve o JSON CRU (cifrado).
- **Código**: ~45 linhas. **Teste**: ~70.
- **Rede / log / chaves**: como no 05; nada novo.
- **Veredito**: OK.

### 07 — `EntityClient::parse_raw` (ADAPTADO)
- **O que faz**: expõe o `JsonSerializer::parse` oficial com o modelo do servidor (o mesmo que `load` usa por dentro), para o JSON que vem do 06 entrar no mesmo caminho de decifração.
- **Adaptação**: o original não aplica sem o 04 (o teste usava o helper `test_entity_client` que o 04 cria). O CÓDIGO é idêntico ao original (10 linhas + 1 no mock); só o teste monta o `EntityClient` na mão, como os testes oficiais vizinhos.
- **Pânico**: o `JsonSerializer::parse` OFICIAL tem `panic!` para resposta fora do esperado (`json_serializer.rs`, associação desconhecida, id que não é texto). O remendo não cria pânico, mas expõe esse caminho: no conector toda chamada ao SDK que processa resposta do servidor passa por `sem_panico` (`src/tuta/sdk.rs`, `catch_unwind`) e vira "ilegível", sem derrubar o processo.
- **Veredito**: OK.

### 09 — decifrar entidade já lida com a chave de sessão do dono (`crypto_entity_client.rs:297-349`)
- **O que faz**: `decrypt_parsed(tipo, entidade, chave herdada)`. O MailDetailsBlob não tem `_ownerEncSessionKey` próprio; o TS decifra com a chave do Mail (`keyProviderFromInstance`, `MailFacade.loadMailDetailsBlob`). Sem chave herdada, resolve pela própria entidade (caminho oficial). Entidade sem cifra volta igual. O trecho `decrypt_with_session_key` foi só EXTRAÍDO do `process_encrypted_entity` oficial (mesmas linhas, inclusive a conferência `encryptionAuthStatus`).
- **Código**: ~55 linhas (a maior parte é mover código). **Teste**: ~155.
- **Cripto**: chave errada → falha de MAC → erro (nunca texto errado, nos formatos com MAC). A chave herdada vem do `_ownerEncSessionKey` do Mail decifrado com a chave do grupo dono pelo `KeyLoaderFacade` oficial.
- **Veredito**: OK.

### 10 — ler valores AEAD v3 (chave de sessão)
Arquivos: `entities/entity_facade.rs`, `entities/entity_facade/decryption.rs` (novo), `test_data/aead_attributes_ts.json` (novo).
- **O que faz**: ao decifrar cada valor, olha a versão da cifra (`cipher_version`, `decryption.rs:18`): tamanho par → antigo (AES-CBC, caminho oficial de sempre); ímpar → 1º byte: 0/1 antigo, 2 AEAD com chave de grupo, 3 AEAD com chave de sessão, outro → erro. Igual a `getSymmetricCipherVersion`/`parseVersionedCiphertext` do TS (`SymmetricCipherVersion.ts:17-36`, `ParsedCiphertext.ts:104-145`).
- **AEAD v3**: subchaves = `AeadSubKeys::derive_from_session_key(chave, "<app>/<typeId>")` do `crypto-primitives` OFICIAL (contexto `"SK instanceSessionKey\x1f"` + app/id, igual a `SymmetricKeyDeriver.deriveSubKeysAeadFromSessionKey`); dado associado `"attributeEncSK\x1f" + caminho do campo` (igual a `InstanceDecryptor.ts:56`); o caminho é `<idDoValor>` na raiz e `<idDaAssociação>/<idDoAgregado>/<idDoValor>` nos agregados — igual a `CryptoMapper.ts:150, 177, 236`. A decifração é a OFICIAL (`aead_facade.rs`): confere o MAC BLAKE3 ANTES de decifrar (AES-CTR). Chave de 128 bits com AEAD → erro; agregado sem `_id` → erro (não inventa caminho).
- **Código**: ~70 linhas de `decryption.rs` (parte de sessão) + ~45 em `entity_facade.rs`. **Teste**: ~150 + vetores.
- **Rede / log**: nada.
- **Vetores**: `aead_attributes_ts.json` diz vir do cliente TS; não reproduzi no TS, mas o teste também prova que contexto/tipo/byte errado FALHAM — e a derivação e o MAC são do `crypto-primitives` oficial, que tem os próprios testes de compatibilidade com o TS (`CompatibilityTestData.json`, rodados aqui).
- **Achado (não é do remendo)**: no TS OFICIAL, `encryptAggregateAssociation` (`CryptoMapper.ts:338-346`, igual no master) reatribui o prefixo dentro do laço — a partir do 2º agregado o caminho ACUMULA os ids, enquanto a decifração oficial não acumula. Se o Tuta gravar AEAD em 2º agregado em diante, nem o app oficial lê de volta; o conector também não (MAC falha → "ilegível"). Nunca dado errado.
- **Veredito**: OK.

### 11 — ler valores AEAD v2 (chave de grupo) e RECUSAR gravar AEAD
Arquivos: `crypto_entity_client.rs`, `crypto_entity_client/aead.rs` (novo), `entities/entity_facade.rs`, `entities/entity_facade/decryption.rs`, `entities/entity_facade/decryption_keys.rs` (novo), vetores.
- **O que faz**: entidade com `_kdfNonce` vai por `process_entity_with_group_keys` (`aead.rs:11`): descobre quais versões de chave de grupo os valores pedem (`EntityDecryptionRequirements`, `decryption_keys.rs`), carrega cada uma pelo `KeyLoaderFacade.load_sym_group_key` OFICIAL (grupo dono + versão), e decifra com subchaves `derive_from_group_key(chave versionada, nonce de 32 bytes, "<app>/<typeId>")` (oficial; igual a `deriveSubKeysAeadFromGroupKey`: chave‖nonce, contexto `"GK and nonce instanceMessageKey\x1f"`) e dado associado `"attributeEncGK\x1f" + caminho` (`InstanceDecryptor.ts:42`). Se houver bucket key, a chave de sessão é resolvida pela própria entidade (mantém a autenticação do remetente). Nonce ausente/≠32 bytes, grupo dono ausente, versão sem chave → erro.
- **GRAVAÇÃO**: `update_instance` e `encrypt_and_map` RECUSAM entidade com `_kdfNonce` ("AEAD instance writes are not supported", `crypto_entity_client.rs:182-186`, `entity_facade.rs:637-641`). Isso protege a etapa 3 (marcar "respondido" regrava o Mail): nunca vamos sobrescrever AEAD com CBC.
- **Código**: ~87 (`aead.rs`) + ~130 (`decryption_keys.rs`) + ~50 (`decryption.rs`) + ~40 nos outros. **Teste**: ~275 + ~110 + ~120 + ~180.
- **Rede**: só o que o `KeyLoaderFacade` oficial já faz para carregar chave de grupo (GET de entidades do próprio Tuta).
- **Pânico**: `RefCell::borrow_mut` no cache de subchaves é por decifração (sem reentrância). Nenhum `unwrap/expect` fora de teste.
- **Veredito**: OK.

### Resultado das buscas nas linhas adicionadas (todos os remendos)
- Host/URL fixo novo: nenhum (só `https://first`/`http://test` dentro de testes).
- Log/print novo: nenhum.
- `unsafe`, processo, ambiente, disco: nenhum.
- `unwrap`/`expect`/`panic!` fora de `#[cfg(test)]`: nenhum novo (só o `expect("poisoned lock")` no padrão oficial do cache).
- Mensagens de erro: só nomes de campo/tipo e versão de chave; nunca chave, token ou conteúdo.

## 4. O que entra no binário

Cerca de 880 linhas de código de remendo (o resto, ~1.500, é teste), em 7
arquivos modificados + 4 novos do SDK. Diferença completa:
`git -C vendor/tutanota diff` e `git -C vendor/tutanota status` (os novos).

## 5. Testes do próprio SDK

`scripts/atualizar-sdk.sh --testar-sdk` (numa CÓPIA: `target/sdk-testes-src`):

- `tutasdk` (unitários): **334 passaram, 0 falharam, 1 ignorado** (o ignorado é oficial).
- `download_mail_test` (login + ler e-mail com respostas gravadas): **1 passou**.
- Os demais testes de integração do Tuta são `#[ignore]` (pedem servidor local do Tuta).
- `crypto-primitives` (com `test_utils`): **49 passaram**, inclusive `compatibility_test_aead`, `compatibility_test_aead_key_derivation` e os de BLAKE3 contra os dados de compatibilidade do cliente TS (`CompatibilityTestData.json` da tag).
- Os 33 testes dos remendos passaram (blob read/token, parse_raw, decrypt_parsed, AEAD sessão/grupo, requisitos de chave, recusa de gravação AEAD, vazio opcional).

**so-testes/00**: a tag 361.260929.0 do Tuta tem testes quebrados (o `User`
ganhou o campo `plugins` e 4 arquivos de teste não). O arquivo é EXATAMENTE a
diferença 361 → master (bbc38dbe) desses 4 arquivos; é aplicado só na cópia
dos testes, nunca no `vendor/` nem no binário. Sem ele: 1 erro de compilação
e 3 testes falhando, todos fora dos remendos.

## 6. Nosso código fora do SDK que mexe com segredo

- `src/tuta/protocolo.rs` + `src/tuta/sessao.rs` — login (Salt → Session → TOTP), cancelar sessão pendente, encerrar sessão. Formato de rede escrito à mão (9 tipos), e o teste `ids_batem_com_o_modelo_do_sdk` confere CADA id de atributo e o conjunto completo contra o modelo embutido na release fixada. KDF e verificador: funções OFICIAIS (`generate_key_from_passphrase`, `create_auth_verifier`); a senha nunca sai do Mac, só o verificador. Chave cifrada: `encrypt_key` oficial, conferida de volta (`decrypt_aes_key`) antes de guardar.
  - Única exceção consciente à regra "e-mail nunca em URL": o `SaltService` é GET e o e-mail da conta vai em `_body` na URL, PARA O TUTA, exatamente como o cliente oficial faz (`ServiceExecutor`: GET leva os dados na query). Não vai para mais ninguém; nunca em cabeçalho, User-Agent ou log.
- `src/chaveiro.rs` — Chaveiro do macOS pelo Security.framework (crate `security-framework` 3.2.0, a MESMA versão que o `rustls-native-certs` do SDK já usa). Sem `security` de linha de comando (poria segredo no argv).
- `src/rede.rs` — política de destinos por cliente (Tuta: só `app.tuta.com` e `*.tuta.com`; DaVinci: só a origem configurada), HTTPS obrigatório (HTTP só para 127.0.0.1 nos testes), teto de tempo por pedido, URL nunca no log.
- `src/registro.rs` — log que NÃO obedece RUST_LOG, SDK/bibliotecas só a partir de "warn", máscara de e-mail/token/`_body`/`accessToken`; pânico vira uma linha curta mascarada.
- `src/segredo.rs` — tipos de segredo sem `Display`, `Debug` = `***`, memória zerada ao descartar.
- O crate proíbe `unsafe` (`[lints.rust] unsafe_code = "forbid"`).

## 7. Dependências (Cargo.lock)

- O `Cargo.lock` nasceu do `Cargo.lock` OFICIAL da tag (as mesmas versões que o Tuta testou); o cargo só tirou o que o conector não usa.
- **Novas**: `rpassword 7.5.2` + `rtoolbox 0.0.6` (ler senha sem eco do `/dev/tty`); `windows-sys 0.61.2`/`windows-link` só entram no Windows (não compilam no Mac).
- **Atualizadas por aviso de segurança (RustSec, base de 08/10/2026)** — todas compatíveis (mesma série):
  - `rustls 0.23.37 → 0.23.45` (RUSTSEC-2026-0285; levou `rustls-webpki 0.103.15`, `aws-lc-rs 1.18.1`, `aws-lc-sys 0.45.0`, `pkg-config`; o provedor de cripto do TLS em uso continua o `ring`, instalado pelo cliente do SDK);
  - `h2 0.4.10 → 0.4.16` (RUSTSEC-2026-0258);
  - `bytes 1.10.1 → 1.11.1` (RUSTSEC-2026-0007);
  - `keccak 0.1.5 → 0.1.6` (RUSTSEC-2026-0012; a 0.1.5 foi recolhida);
  - `rand 0.8.5 → 0.8.6` (RUSTSEC-2026-0097);
  - `anyhow 1.0.98 → 1.0.103` (RUSTSEC-2026-0190).
- **Avisos que ficam (não se aplicam ou sem correção)**:
  - `time 0.3.41` (RUSTSEC-2026-0009): só afeta o parse de RFC 2822, que nem o SDK nem o conector usam; a correção arrastaria serde/syn novos;
  - `rsa 0.9.8` (Marvin, sem correção): ataque por medição de tempo pela rede; aqui o RSA só decifra chaves localmente; mesmo do SDK oficial;
  - sem manutenção (informativo): `bincode`, `paste` (do uniffi do Tuta), `pqcrypto-*` (do SDK oficial).
- Nenhuma dependência de websocket (o teste `so_leitura.rs` confere o Cargo.lock e o SDK montado).

## 8. Como refazer esta auditoria numa release nova

1. `scripts/atualizar-sdk.sh <tag> <commit>` (ver LEIA-ME). Se um remendo não aplicar, refaça-o e audite de novo AQUI.
2. Para cada remendo: `git -C vendor/tutanota diff` + arquivos novos; repita as buscas da seção 3 e confira o TS da mesma tag nos caminhos citados.
3. `scripts/atualizar-sdk.sh --testar-sdk` (testes do SDK) e `cargo test` (conector; inclui `ids_batem_com_o_modelo_do_sdk`).
4. Compare `Cargo.lock` com o oficial da tag e rode a base RustSec.
5. Atualize os hashes e as contagens desta página.

## 9. A leitura (etapa 2) — código NOSSO que usa o SDK

Feita em 08/10/2026. Nenhum remendo novo: a leitura usa só funções públicas
do SDK oficial e as dos remendos 05/06/07/09/10/11 já auditados acima.
Testada só contra um Tuta FALSO em 127.0.0.1 (`tests/leitura.rs`), com a
caixa cifrada pelo próprio SDK (`tests/comum/caixa.rs`).

### O que a leitura chama no SDK (todas são leitura)

| Chamada | Pedido ao Tuta | Onde |
|---|---|---|
| `Sdk::login` (retomar a sessão guardada) | GET Session, GET User, GET applicationtypesservice | `caixa.rs::abrir` (via `tuta/sdk.rs::retomar`) |
| `mail_facade().load_user_mailbox()` | GET MailboxGroupRoot, GET MailBox | `pastas` |
| `get_entity_client().load_range(MailSet)` + `decrypt_parsed` por pasta | GET MailSet (lista) | `pastas` |
| `crypto_entity_client.load_range::<MailSetEntry>` | GET MailSetEntry (start/count/reverse) | `pagina` |
| `get_entity_client().load(Mail)` + `resolve_session_key` + `decrypt_parsed` | GET Mail | `email` |
| `crypto_entity_client.load::<ConversationEntry>` | GET ConversationEntry | `email` (Message-ID real) |
| `blob_facade().load_blob_element(MailDetailsBlob)` + `parse_raw` + `decrypt_parsed` | POST blobaccesstokenservice (token de LEITURA) + GET maildetailsblob | `email` |
| `get_entity_client().load(File)` + `resolve_session_key` + `decrypt_parsed` | GET File | `arquivo_do_email` |
| `blob_facade().download_blobs` + `GenericAesKey::decrypt_data` | POST blobaccesstokenservice (LEITURA) + GET blobservice | `baixar` |
| `crypto_entity_client.load::<Group/GroupInfo>` | GET Group, GET GroupInfo | `aliases` (o mesmo caminho do `get_group_id_for_mail_address` oficial) |

O único POST é o do BlobAccessTokenService com `read` preenchido e `write`
vazio (o mesmo que o cliente oficial faz para abrir um e-mail): não muda
nada na caixa. `tests/leitura.rs::conferir_so_leitura` confere isso pedido a
pedido, e que nenhum cabeçalho leva e-mail.

O `resolve_session_key` do SDK em Rust NÃO grava a chave do dono de volta
(o cliente TS grava, `UpdateSessionKeysService`; o Rust tem um TODO e nenhum
serviço ali) — conferido em `crypto/crypto_facade.rs` (a partir da linha 87) da tag.

### A chave do arquivo pelo "balde" do e-mail

E-mail que chega de fora traz o `bucketKey` (o "balde": a chave de sessão de
CADA instância — o e-mail e os arquivos — cifrada com a chave do balde, que
vem cifrada para a chave pública da conta). Enquanto ninguém abre esse
e-mail no app oficial, o File não tem `_ownerEncSessionKey` e o
`resolve_session_key(File)` falha. O app oficial resolve o balde inteiro
(`CryptoFacade.resolveWithBucketKey`). O conector faz o mesmo SEM código de
cripto novo: pega o Mail CRU, troca só o `_id` pelo id do File e pede ao
`resolve_session_key` OFICIAL — que decifra o balde e devolve a chave da
instância cujo id foi pedido (`crypto_facade.rs:319-336` do vendor montado). Só é tentado
quando o e-mail tem balde. Chave errada não vira dado errado: o
`decrypt_parsed`/`decrypt_data` confere o MAC e falha (o anexo fica
ilegível, contado). Nada é gravado.

### Pânico e itens estranhos

O SDK tem `expect`/`unwrap`/`assert`/`todo!` em resposta de servidor
(no vendor montado: `entity_client.rs` 79/81/190/203/205, `crypto_facade.rs:307`
`todo!` do "secure external"). Cada chamada da leitura passa por `sem_panico`
(`catch_unwind`), por ITEM: pânico numa pasta ou num e-mail vira "ilegível"
daquele item, a volta segue, e a sessão do SDK é reaberta na volta seguinte
(`Caixa::suspeita`). Testado: lista de entradas com lixo, Mail com lixo,
nome de pasta em AEAD inválido, assunto em AEAD com MAC errado.

### A trava por texto

`tests/so_leitura.rs` lê `src/leitura.rs` e `src/leitura/**` e falha se
aparecer qualquer nome que GRAVA (update_instance, set_unread, move/trash/
archive, DraftService, SendDraftService, CloseSession, UpdateSessionKeys,
post/put/delete do executor…). Mudança desta etapa: `replyType` sozinho saiu
da lista porque o pacote LÊ o campo (vai como "respondido"); entraram as
formas de ESCRITA (`replyType =`, `replyType:`), que pegam atribuição e Mail
montado. O teste de "mutação" prova que a trava pega cada forma.

### Contrato e dados

- Em disco: cursores (ids de entrada), ids de e-mail/arquivo pendentes,
  códigos curtos ("mail:decifrar", "passageiro_repetido"). Nada de assunto,
  endereço ou corpo (o teste procura no estado.json e no log).
- Pastas só contadas: o e-mail nem é aberto (vai id, pasta e data da
  entrada). Spam: abre o Mail sem detalhes (remetente e assunto).
- Cobertura: nada mais velho que a primeira carga (7 dias) é pedido pela
  /contagem ou pela varredura funda (não traz pendência de meses).
- Achados do lado DaVinci, corrigidos junto (services/atendimento/tuta/
  conciliar.py): a CÓPIA (mesmo Message-ID noutro alias) voltava em
  `faltando` a cada volta; a /contagem de um MARCADOR "movia" o e-mail para o
  marcador. O conector também guarda as cópias e não manda /contagem de
  marcador nem da pasta "Todos".

### Rede nova

Só o canário "atrasado": GET `https://api.github.com/repos/tutao/tutanota/releases?per_page=50`
(público, sem token, User-Agent `davinci-tuta-conector`), cliente próprio
com política `Exata` (não fala com mais nada), 1x por dia.

## 10. A Central de e-mail e o envio (etapa D, 08/10/2026) — código NOSSO

Nenhum remendo novo no SDK e nenhuma dependência nova (o Cargo.lock não
mudou). O que mudou no nosso código:

### Para onde fala

- O DaVinci passou a ser a **Central de e-mail** do outro dev, por caixa:
  `POST /api/mail/agent/{caixa}/heartbeat`, `…/outbox/lease`,
  `…/outbox/{job}/receipt` (o contrato v1 dele, sem mudar nada) e o v2 nosso
  `…/v2/sync`, `…/v2/ingest`, `…/v2/count`, `…/v2/changes`. Mesmo cliente
  (política `Davinci`: só a origem configurada), chave do agente da caixa no
  `Authorization`, nunca em URL/log. O id da caixa vai na URL e é conferido
  (só UUID) antes de montar o cliente e antes de gravar no Chaveiro.
- Rede para o Tuta: a mesma (`app.tuta.com` e `*.tuta.com`).

### O que vai ao DaVinci (o que mudou)

- O corpo vira TEXTO no Mac (`src/texto/html.rs`, código nosso, linear, sem
  `unsafe`, sem parser de terceiros; chamado dentro do carregamento do e-mail,
  já isolado de pânico). Links como "texto <url>"; script/style/head somem.
- Os códigos de verificação e os links de acesso são mascarados ANTES de
  sair do Mac (`src/texto/protecao.rs`, as mesmas listas de
  `apps/api/app/services/mail_atendimento/codigos.py`).
- O corpo só sai das pastas que a Central manda ler (`/v2/sync`); das outras,
  só os ids na contagem. E-mail só para endereços internos (a lista vem do
  servidor) não sobe.
- Anexos vão JUNTO do e-mail (a Central ignora o e-mail repetido, então
  anexo depois não existe). O tamanho é conferido ANTES de baixar.
- Dos cabeçalhos brutos, só os de autenticação (Authentication-Results,
  ARC, Received-SPF, DKIM-Signature, Return-Path), para o aviso de golpe.

### O ENVIO (src/envio.rs) — o único lugar que ESCREVE no Tuta

Chamadas do SDK oficial (sem remendo):

- `LoggedInSdk::get_user()` + `Group`/`GroupInfo` (`load`): os endereços
  ATIVOS da conta e o grupo de e-mail de cada um — o mesmo caminho do
  `get_group_id_for_mail_address` oficial, comparando sem maiúsculas e
  devolvendo o endereço como o Tuta guarda. Remetente que não está ali =
  `failed`; nunca troca pelo principal.
- `LoggedInSdk::get_current_sym_group_key(grupo)` + `GenericAesKey::encrypt_key`:
  a chave de sessão nova (`Aes256Key::generate`, aleatório do sistema) cifrada
  com a chave do grupo de e-mail (o `ownerEncSessionKey` do rascunho), como o
  `MailFacade.createDraft` oficial.
- `get_service_executor().post::<DraftService>(DraftCreateData, session_key)`:
  o rascunho REPLY (`conversationType` 1, `previousMessageId` = o Message-ID
  do original), `confidential: false`, um destinatário externo.
- `get_service_executor().post::<SendDraftService>(SendDraftData)`: com a
  `mailSessionKey` em claro (o servidor do Tuta precisa dela para mandar o
  e-mail NÃO confidencial para fora — é o que o cliente oficial faz) e
  `plaintext`. O mesmo formato que a tutabridge usa e que foi provado contra
  contas de verdade lá.
- Destinatário do próprio Tuta é recusado (exigiria a cifra de ponta a ponta
  com a chave pública dele, que o conector não monta).
- Nada marca o original como "respondido" (a chave local
  `marcar_respondido` segue sem efeito).

Garantias: diário local (`diario-envio.json`, só ids e passos) gravado ANTES
de cada passo; falha antes do rascunho = `failed`; depois do rascunho =
`uncertain`; nunca reenvia; o token do lease só na memória. O teste
`tests/envio.rs` decifra o rascunho no Tuta falso com a chave que o conector
manda no SendDraft e confere que é a mesma que ele cifrou para o grupo.

**Ainda não rodou contra a conta de verdade**: o primeiro envio real deve ser
no modo teste da caixa (só para endereço nosso), conferido no Tuta.
