//! Tudo o que fala com o Tuta.
//!
//! - `protocolo` e `sessao`: entrar/sair (código NOSSO, fora do SDK).
//! - `sdk`: o SDK oficial montado do jeito do conector (sem websocket, sem
//!   disco, pânico isolado).
//! - `canario`: a versão ainda é aceita? (sem senha).

pub mod canario;
pub mod protocolo;
pub mod sdk;
pub mod sessao;
