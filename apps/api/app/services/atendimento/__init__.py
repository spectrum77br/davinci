"""Atendimento unificado: a caixa única de Shopee, ML, TikTok e Amazon (25/09/2026).

"O Duoke nosso" — plano em `docs/atendimento-unificado.md`. O pacote é
dividido por RESPONSABILIDADE, para que cada peça seja testada sozinha:

  constantes    — vocabulário (plataformas, canais, modos, estados, SLA,
                  limites) e os dois resultados que os adaptadores devolvem.
  gravar        — a ÚNICA porta de escrita de conversa e mensagem. Idempotente
                  por id externo; decide autor × origem; adota a nossa própria
                  resposta quando o sync a traz de volta; recalcula fila e prazo.
  clientes      — o cliente de API de cada integração, com o token renovado
                  numa sessão própria (refresh token da Shopee/ML é de uso
                  único) e sob a trava de renovação por integração.
  shopee, ml, tiktok, amazon_email
                — adaptadores: `sincronizar` lê o que mudou desde o cursor do
                  canal; `enviar_texto` responde. Nunca marcam como lido.
  validador     — o que não pode sair (contato fora da loja, limite, ISO-8859-1
                  do ML...). Vale para pessoa e para IA; a IA tem regras a mais.
  contexto      — o pedido por trás da conversa (Bling, rastreio, chamados).
  enriquecer    — o que o Duoke mostra, pela API da loja (SÓ LEITURA): cartão
                  de produto (foto, título, preço; cache de 24 h) e o retrato
                  do pedido na plataforma (`dados["pedido_mkt"]`, renovado no
                  máximo a cada 30 min), com cota por rodada e sem dado pessoal.
  ia            — o rascunho: uma chamada de modelo sem ferramentas; o código
                  escolhe o pedido e preenche as lacunas, o validador decide.
  enviar        — o caminho único de saída (travas, índice de envio em voo,
                  simulador local, avaliação do rascunho).
  sync          — o cron: garante os canais e roda os adaptadores.
  instagram     — as DMs do Instagram na mesma tela, SÓ LEITURA.

Parte 2 (28/09/2026):

  lojas         — o nome da LOJA por trás da integração ("mega" → "Marquezini",
                  como no Duoke), com cache por sessão.
  indice        — o índice próprio de pedidos e avaliações por comprador (a
                  Shopee não filtra pedido por comprador); upsert, nunca levanta.
  indexar       — o job de hora em hora que alimenta o índice (últimas 2 h de
                  pedidos e as avaliações novas), só com a leitura ligada.
  cliente       — o cartão "Cliente" do painel: compras, devoluções,
                  avaliações, perguntas de pré-venda, sinais e linha do tempo.
  importar      — a importação do histórico (uma vez, à mão, retomável, sem
                  IA nem alerta; `scripts/atendimento_importar_historico.py`).
  manual        — o manual base da IA: assuntos do banco, conflito entre
                  regras e importar/exportar (`scripts/atendimento_manual.py`).

Temu e AliExpress (30/09/2026) — sem API de chat, lidos pelo robô do Mac mini
(um perfil do AdsPower por loja, só ESCUTANDO o Seller Center):

  robo          — a recepção (routers/atendimento_robo.py): o canal da loja
                  SEM integração (migration 0347), o pulso e a "leitura
                  parada", e a gravação dos eventos pela porta única.
  robo_leitura  — o vocabulário comum dos leitores (Python puro).
  robo_temu, robo_aliexpress
                — os leitores: cópias do que a página recebeu (fetch/XHR e
                  WebSocket) → conversas e mensagens; o que não reconhecem
                  vira contagem por NOME de campo, nunca o valor.

Nada aqui importa os submódulos no carregamento do pacote: o worker e o
router puxam só o que usam, e os testes trocam um adaptador por monkeypatch.
"""
