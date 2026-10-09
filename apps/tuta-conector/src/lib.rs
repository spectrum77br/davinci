//! Conector do DaVinci com as contas do Tuta (decisões no config.rs; uso no LEIA-ME.md).
//!
//! Lê cada caixa SÓ por consulta periódica (nunca websocket, nunca "líder"),
//! usando o SDK OFICIAL do Tuta numa release fixa (SDK_VERSAO) + os remendos
//! auditados de remendos/ (AUDITORIA.md), e entrega à Central de e-mail do
//! DaVinci (`/api/mail/agent/{caixa}/…`: o v1 dela para o sinal e a fila de
//! respostas, o v2 nosso para o que o v1 não carrega). Um processo por conta
//! (`--conta geral|goslin`). A senha e o TOTP são digitados uma vez
//! (`entrar`); a sessão e a chave do agente moram só no Chaveiro do macOS.
//!
//! Módulos:
//! - `config`        as decisões (constantes) e as contas
//! - `segredo`       tipos que nunca aparecem em log/erro
//! - `chaveiro`      Chaveiro do macOS (sessão do Tuta, caixa da Central), por conta
//! - `terminal`      perguntas ao dono (senha sem eco)
//! - `registro`      log mascarado; pânico curto
//! - `rede`          cliente HTTP com destinos permitidos
//! - `tuta`          login/sair (nosso), SDK oficial, canário (474 e releases)
//! - `davinci`       cliente da Central de e-mail (v1 + v2, recuo)
//! - `estado`        os estados do conector e o que leitura e envio trocam
//! - `estado_local`  o que fica em disco (só ids/estado), por conta
//! - `texto`         HTML → texto; códigos e links de acesso mascarados
//! - `leitura`       volta de leitura — SÓ LEITURA: caixa, pacote, volta
//! - `envio`         envio pela fila da Central — separado da leitura
//! - `servico`       o `rodar` de uma conta: leitura + envio
//! - `comandos`      entrar, sair, estado, configurar

pub mod chaveiro;
pub mod comandos;
pub mod config;
pub mod davinci;
pub mod envio;
pub mod estado;
pub mod estado_local;
pub mod leitura;
pub mod rede;
pub mod registro;
pub mod segredo;
pub mod servico;
pub mod terminal;
pub mod texto;
pub mod tuta;
