# Canal Zap (WhatsApp Cloud API) no Atendimento — desenho final

> **Situação:** proposta para aprovação do Eduardo. Nada foi implementado. **Revisada em 17/10/2026** com as críticas de dois revisores (o que mudou e o que foi recusado está no §15).
> **Base de leitura:** worktree `/Users/eduardofelipe/davinci-wt-atend`, branch `feat/atendimento-zap`, commit `905baa22`. Essa cópia está **34 commits atrás do `origin/main`**. Todas as linhas `arquivo:linha` abaixo foram conferidas nesse commit e precisam ser reconferidas depois do rebase.
> **Migration:** o head do `origin/main` hoje (17/10/2026) é `0414_vigia_conciliacao_chave_etapa`, então a próxima é **`0415_atendimento_zap`**. O main ganha migrations todo dia, por isso o número tem de ser conferido na hora de criar o arquivo.
> **Requisito:** RF10 do levantamento (`docs/atendimento-comunicador-levantamento.md` §3.14; decisões D16, Q16, Q19 e Q20).
> **Como este documento foi feito:** foram comparadas duas propostas, uma "incremental" e uma "robusta". A base escolhida foi a incremental, com enxertos da robusta. O porquê de cada escolha está no Apêndice A.

---

## 1. Resumo para o Eduardo

- **O que vai dar para fazer:**
  - Cada número de WhatsApp das lojas vira uma linha verde "WhatsApp" na barra do Atendimento.
  - As conversas caem na mesma caixa de Shopee e ML. Foto, áudio e documento do cliente aparecem dentro da conversa.
  - A equipe responde pela tela e vê os tiques de enviado, entregue e lido.
  - Conversa do Zap sem venda aparece como **PRÉ-VENDA**.
- **O que não dá:**
  - O histórico que está no Duoke **não vem** para o DaVinci.
  - Passadas 24 h desde a última mensagem do cliente, o WhatsApp só deixa mandar um **modelo aprovado pela Meta**, que é pago. Isso fica para uma etapa posterior.
  - A IA **não responde** no Zap na primeira versão.
  - Muito provavelmente não dá para o Duoke e o DaVinci atenderem o mesmo número ao mesmo tempo.
- **Tudo nasce desligado.** Existem três chaves: a geral, a do Zap e um botão "pausar" em cada número. Também há um botão "pausar todos".
- **Regras do próprio WhatsApp que valem para a equipe também:** no Zap ninguém pode pedir nem mandar número de cartão, de conta bancária ou de documento (RG, CPF). O DaVinci barra isso, inclusive para pessoas. Se o CPF para a nota fiscal pode ser pedido, ainda precisa ser confirmado.
- **A ordem é esta:**
  1. Você cria **dois** apps na Meta, no Gerenciador dos números: o "DaVinci Zap Teste", só para os testes no computador, e o "DaVinci Zap", para o servidor. Cada um vem com um número de teste grátis (passo a passo no §13).
  2. Primeiro nós fazemos o **receber** no computador **sem nenhuma chave de verdade**, com mensagens de mentira.
  3. Depois vem o **responder**, com o número de teste do app de teste. Só o seu celular recebe.
  4. Depois vêm foto e áudio e a tela "Números do WhatsApp".
  5. Com o seu OK, fazemos um ensaio no servidor, ainda com o número de teste.
  6. Por fim vêm os números reais, um por vez, começando pelo de menor movimento. Para cada número é preciso perguntar antes ao Duoke. No dia, só se começa depois de uma lista de "vai/não vai" toda conferida (§13.3).
- **O que você precisa fazer:**
  - Criar os dois apps e dois "usuários do sistema" do tipo **funcionário** (não administrador), um para cada app. Cada um só enxerga as contas de WhatsApp que você der a ele.
  - Digitar as chaves **só no DaVinci, nunca no chat**. A chave do app do servidor **nunca** vai para o computador.
  - Publicar uma **política de privacidade** (uma página no site) e pôr o app do servidor em modo **"Ao vivo"** na Meta. Sem isso, a Meta deixa de mandar parte das mensagens.
  - Antes dos números reais: verificar a empresa e cadastrar uma forma de pagamento **sua** na conta do WhatsApp.
  - Fazer ao Duoke as perguntas do §13, número por número. Isso inclui pedir que **tirem o acesso deles** à conta do WhatsApp depois da troca, para os clientes não continuarem caindo lá.
- **Custo:** a documentação nova da Meta diz que, desde 01/10/2026, as respostas também são cobradas, com 1.000 grátis por número por mês. A página de preços da Meta ainda diz "grátis". Isso precisa ser conferido. A tela vai mostrar a conta por número e avisar quando chegar perto de 1.000.

---

## 2. Decisões já tomadas e premissas

### 2.1 Decididas pelo Eduardo (não mudam sem ele)

| # | Decisão | Data |
|---|---|---|
| E1 | O Zap mora **só no DaVinci**, pela **WhatsApp Cloud API oficial da Meta, direto**, sem BSP (opção a) | 02/10 |
| E2 | Os números estão hoje no Duoke e serão liberados e migrados **um por vez**, começando pelo de **menor movimento** (Q19) | 16/10 |
| E3 | A conta Meta (Gerenciador) dos números é **outra**, diferente da do Instagram. Por isso as credenciais são por app, por WABA e por número, e pode haver mais de um app Meta | 16/10 |
| E4 | O código começa **já**, com o **número de teste gratuito da Meta**, testado em **localhost**. Nada vai para produção sem o OK dele | 16/10 |
| E5 | O envio do Atendimento é controlado por chaves e **nasce desligado** | 16/10 |
| E6 | Zap sem venda = **PRÉ-VENDA** até ser transferido (Q20); a aba "Zap" vem depois do E-mail e não é etiqueta (D16 / Q16) | levantamento |

### 2.2 Premissas de desenho

As marcadas **[VETÁVEL]** são escolhas de negócio. Se o Eduardo disser outra coisa, o desenho muda num ponto só.

| # | Premissa | Por quê |
|---|---|---|
| P1 | O Zap é um **canal externo por número**, com `plataforma='whatsapp'`, `canal='zap'`, `externo_ref='zap:<phone_number_id>'` e `integration_id=NULL` | É o mesmo trilho de sites e redes (`canais_externos.garantir_canal`, `apps/api/app/services/atendimento/canais_externos.py:137`). O CHECK `tem_origem` (`models/atendimento.py:166-170`) e o índice `uq_atendimento_conversas_robo` (`:1549-1555`) já aceitam esse formato sem mudar schema. O número atende várias lojas e a pré-venda não tem loja |
| P2 | A conversa "no formato antigo" (canal `zap` com plataforma de marketplace) **continua recusada para sempre** | É a trava de 05/10 (`constantes.py:112-127, 169-170`). Os testes atuais (`tests/test_atendimento_enviar.py:1620-1780`) ficam verdes, e nada sai pelo chat do marketplace |
| P3 | A chave da conversa é o **id próprio do contato** (`atendimento_zap_contatos.id`), não o telefone | O `wa_id` pode faltar (cliente com nome de usuário, BSUID), mudar (`user_changed_number`) ou vir sem o 9º dígito. Usar o telefone como chave cria conversa em dobro |
| P4 | **Dois apps Meta próprios** dentro do Gerenciador dos números: **"DaVinci Zap Teste"** (só a WABA de teste, só para o computador) e **"DaVinci Zap"** (produção). O token de cada um é de um **usuário do sistema funcionário** (*employee*), com acesso só às WABAs atribuídas | Desenvolvedor direto com dados próprios não precisa de App Review. Usar o app do Instagram numa WABA de outro portfólio exigiria Advanced Access. Separar os apps impede que o banco do Mac guarde uma chave que forja webhook de produção. A Meta diz que o usuário do sistema administrador tem, por padrão, "full access to all WABAs" do portfólio, e que um funcionário basta quando o app só precisa de algumas |
| P5 | As credenciais ficam **no banco, cifradas**, em 3 níveis (app → WABA → número). Nada no `.env` | Pode haver mais de um app. O `meta_app_secret`/`instagram_app_secret` (`config.py:412-418`) não é tocado |
| P6 | O webhook **só guarda** o evento cru e responde 200 depois do commit. Um job do worker processa | A Meta reenvia por até 7 dias. Banco fora do ar responde 500 e a Meta repete. Responder rápido evita que a Meta desligue o webhook |
| P7 | O envio passa pelo **caminho único** `enviar.enviar_resposta`, com um desvio pelo **canal + plataforma** | Reaproveita a linha em voo, o índice de envio em voo, a régua enviada/revisar/falhou, o simulador e o freio único |
| P8 | **IA fora do Zap** na v1 (nem sugere, nem envia) **[VETÁVEL]** | O prompt e os exemplos são de marketplace (§7) |
| P9 | **PIX, conta bancária e "por fora"** continuam bloqueados para todos no Zap. Além disso, uma regra **só do Zap**, para pessoa e IA, barra número de cartão, agência e conta, e pedido de cartão, RG ou CPF (§7). E-mail, link, telefone e "WhatsApp" ficam liberados só no Zap. **[VETÁVEL]** só o PIX da própria empresa; conta bancária e documento **não são vetáveis** | Golpe e fraude: afrouxar depois é fácil, voltar atrás não é. A política do WhatsApp manda: "Don't share or ask people to share full length individual payment card numbers, financial account numbers…" (whatsappbusiness.com/policy). Violar isso põe o número em risco de restrição. Se o CPF para a nota fiscal tem exceção ainda está **[a confirmar]** |
| P10 | **Mídia do cliente guardada em disco**, com retenção de **180 dias** **[VETÁVEL]** | O link da Meta vale 5 min e a mídia some em 7 dias. Depois da migração não há celular onde olhar |
| P11 | **Tiques azuis** (marcar como lida no WhatsApp) desligados **[VETÁVEL]** | Mostra ao cliente que alguém leu |
| P12 | **Modelos pagos** só na Onda 5, só por pessoa e só da categoria *utility* **[VETÁVEL]** | Custo e risco de qualidade do número |
| P13 | No dia da migração o número entra em modo **`humano`**, não `observar` | Depois de sair do Duoke, "observar" quer dizer que ninguém responde |
| P14 | O Zap só aparece para **quem vê tudo** (sem equipe) até a Onda 6 **[VETÁVEL]** | O escopo da equipe filtra por `integration_id` (`routers/atendimento.py:584-590`). Hoje quem usa o Atendimento já vê tudo |
| P15 | A tela "Números do WhatsApp" fica **dentro de "Lojas e modo"** e é só para admin **[VETÁVEL]** | É onde já se liga o modo de cada loja |
| P16 | O prazo de resposta do Zap na fila é de **24 h** **[VETÁVEL]** | É igual à janela do WhatsApp |
| P17 | "Transferir para a venda" vem **depois** do 1º número real (Onda 6) **[VETÁVEL]** | Não é necessário para atender. A pré-venda já sai de graça |

---

## 3. Arquitetura e pontos de integração

```
 Meta (app "DaVinci Zap", no Gerenciador DOS NÚMEROS)
   │  GET  handshake  /api/webhooks/meta/whatsapp/{app_id}
   │  POST assinado   X-Hub-Signature-256 = HMAC(corpo cru, app secret DESTE app)
   ▼
 Caddy  /api/* → api:8000   (infra/caddy/Caddyfile:26-28 e 62-64 — nada muda; o Caddy não limita tamanho)
   ▼
 routers/whatsapp_webhook.py
   0) só no host principal; lê o corpo em streaming com teto de 4 MB
   1) acha o app pelo {app_id}       2) confere a assinatura (segredo atual; o anterior só 1 h numa troca de rotina)
   3) quebra o corpo em itens         4) INSERT atendimento_zap_eventos ON CONFLICT (chave) DO NOTHING
   5) COMMIT  → 200                   6) enfileira o job, coalescido (melhor esforço; o cron de 1 min é a rede)
   ▼
 worker: zap_entrada.processar_eventos()   ── SKIP LOCKED, UM COMMIT POR EVENTO (nunca trava longa na conversa)
   ├─ mensagem → atendimento_zap_contatos → canais_externos.garantir_canal('zap:<pnid>')
   │             → gravar.upsert_conversa → gravar.gravar_mensagem   (fila, prazo, etiqueta: prontos)
   │             → canal.status='ok' + ultimo_ok_em               (a barra "Lojas e modo" lê daqui)
   │             └─ mídia → atendimento_zap_midias (pendente) → job baixar (Onda 3)
   ├─ status   → atendimento_mensagens (pelo biz_opaque_callback_data ou pelo wamid) → payload.zap.entrega
   ├─ user_preferences → atendimento_zap_contatos (opt-out)
   └─ conta/erros/qualidade → atendimento_zap_numeros / atendimento_zap_contas (saúde) → canal.status
   ▼
 /atendimento (barra "WhatsApp", lista, conversa)
   └─ POST /api/atendimento/conversas/{id}/responder → enviar.enviar_resposta
        → _destino → [desvio] _destino_zap (todas as travas) → validador → linha "enviando" commitada
        → _chamar_plataforma → [desvio] zap.enviar_texto → POST graph.facebook.com/{v}/{pnid}/messages
        → _gravar_resultado (igual hoje)
```

### 3.1 Módulos novos

| Arquivo | Papel | Onda |
|---|---|---|
| `apps/api/app/models/atendimento_zap.py` | Modelos das tabelas novas (exportar em `app/models/__init__.py`) | 1 |
| `apps/api/alembic/versions/0415_atendimento_zap.py` | Migration | 1 |
| `apps/api/app/routers/whatsapp_webhook.py` | Webhook público, no molde de `routers/nfse_webhook.py:38-40`: router próprio com `prefix="/api/webhooks"` e assinatura pura | 1 |
| `apps/api/app/services/atendimento/zap_contas.py` | Cifrar e decifrar credenciais, achar app/WABA/número, cache de 60 s do segredo do app, descoberta de números | 1 |
| `apps/api/app/services/atendimento/zap_entrada.py` | Parser dos eventos, contato, conversa, mensagem, status e eventos de conta | 1 |
| `apps/api/app/services/atendimento/zap_api.py` | HTTP da Cloud API. Reaproveita de `services/marketing/meta_client.py` só o que não muda frase nem versão: `GRAPH_HOST` (48), `_eh_ambiguo` (184), `_redigir` (197) e `_headers` (234). **Não** reaproveita `graph_version()`: usa a chave própria `meta_whatsapp_graph_version` (§6.1), porque subir a versão do Instagram (`config.py:327`) mudaria os payloads do WhatsApp sem aviso. **Não** usa `_erro_da_resposta`/`_resposta_json` como estão, porque eles traduzem pelos dicionários do módulo (`:152-166`), e mexer neles muda as frases do Instagram e do Facebook para os códigos 10, 200, 368 e 190. Tem um `_erro_zap(resp, token)` próprio: lê `code`/`error_subcode` igual e traduz por `zap_erros`. Tem também a função nova e estrita `inspecionar_token` (§10.1), porque a `validade_do_token` (`meta_client.py:388-420`) é "melhor esforço" | 1 (só GET) / 2 |
| `apps/api/scripts/atendimento_zap_local.py` | Só em dev: `cadastrar` (fictício na Onda 1, app de teste da Onda 2 em diante), `simular` (com `--processar`), `subir-midia` (§11) | 1 |
| `apps/api/app/services/atendimento/zap.py` | Adaptador de saída: texto (Onda 2), imagem (3), modelo e lida (5) | 2 |
| `apps/api/app/services/atendimento/zap_erros.py` | Tabela única código da Meta → classe, frase e efeito (§6.5) | 2 |
| `apps/api/app/services/atendimento/zap_midia.py` | Baixar, servir e expirar mídia | 3 |
| `apps/api/app/routers/atendimento_zap.py` | `/api/atendimento/zap/*`: cadastro, saúde, mídia, pausar. Registrado em `app/main.py` (`app.include_router(atendimento_zap_router.router)`, perto de `:298-308`) **já na Onda 3**, quando nasce a rota da mídia | 3 (mídia) / 4 |
| `apps/web/components/AtendimentoZapNumeros.vue` | Tela "Números do WhatsApp" | 4 |

### 3.2 Pontos de integração no código atual

| Onde (arquivo:linha) | O que muda | Onda |
|---|---|---|
| `app/main.py:66` e `:235` | Importar e registrar o `whatsapp_webhook_router` ao lado do `nfse_webhook_router` | 1 |
| `app/main.py` (`:298-308`, junto dos routers do atendimento) | Registrar o `atendimento_zap_router` | 3 |
| `app/historico/sql.py:104-126` (`EXCLUIDAS`) e `tests/test_historico_regras.py:119-147` | **O gatilho do Histórico entra em toda tabela nova** (`garantir_gatilhos` diário, `historico/banco.py:36-55`). Ele grava a linha inteira no INSERT e no DELETE, e o antes e o depois de cada coluna no UPDATE (`sql.py:311-341`), por 365 dias (`banco.py:21`). Por isso `zap_(contatos\|eventos\|midias)` entra no grupo `atendimento_(…)` de `EXCLUIDAS`, e as três tabelas entram na lista "not in com" do teste, **na própria Onda 1**. `apps`, `contas` e `numeros` ficam **com** o gatilho: as colunas `*_enc` são `bytea` e saem como `{_arquivo: true}` (`sql.py:229-240`), e trocar envio/etapa de um número é o que o Histórico serve para mostrar | 1 |
| `app/historico/nomes.py:259-261` (`SEM_CORPO`), `:224-237` (`REVELACOES`), `ACOES`/`FRASES` | `SEM_CORPO` ganha `atendimento/zap/(conversas\|contatos)/` (corpo com parâmetros de modelo, busca por CPF, pedido de exclusão). `REVELACOES` ganha o `GET` que mostra o verify token e o que mostra o PIN. `ACOES` ganha as frases das rotas novas ("pausou o envio do número", "pausou todos os números", "trocou o token da conta"…) | 1 (`SEM_CORPO`) / 4 |
| `app/routers/webhooks.py:686-707` | Extrair a parte pura `assinatura_meta_ok(body, header, segredos: list[bytes])`. O `_verify_meta_signature` vira um invólucro e o Instagram fica idêntico, protegido por `tests/test_webhook_dm_instagram.py` | 1 |
| `app/routers/webhooks.py:78-101` | Usar `_bump_sig_failure(..., contador="webhook:meta_wa:sig_fail_count", snapshot_key="webhook:meta_wa:sig_fail_last")`, sem mexer no do Instagram. A falha de assinatura fica **só no Redis**, nunca no banco | 1 |
| `app/config.py:513-517` (bloco do atendimento) e `:327` | Chaves novas, todas desligadas (§6.1), e `meta_whatsapp_graph_version` separada da `meta_graph_version` do Instagram | 1 |
| `app/services/atendimento/constantes.py:56-64` | `PLATAFORMA_WHATSAPP='whatsapp'` em `PLATAFORMAS_EXTERNAS`, o que a leva para `PLATAFORMAS_LISTA` e deixa **fora** de `PLATAFORMAS_CAIXA` (`:37`). Assim o sync, a IA e o manual não a veem | 1 |
| `constantes.py:187` (`CANAIS_EXTERNOS`), `:242-246` (`MODOS_EXTERNOS`), `:271` (`SLA_HORAS`), `:315` (`LIMITE_CARACTERES`), `:643` (`ROTULO_TIPO_PLATAFORMA`) | `whatsapp → ('zap',)`; modos `('observar',)` na Onda 1 e `('observar','humano')` a partir da Onda 2; SLA de 24 h; limite de 4096; rótulos de áudio, localização, contato, figurinha e botão | 1-2 |
| `constantes.py:161-182` (`motivo_canal_sem_envio`) | **Onda 1: nada muda.** O Zap continua com "Responda pelo Duoke". **Onda 2:** `zap + whatsapp` passa a devolver a frase nova `MOTIVO_ZAP_SO_PESSOA` ("a IA não sugere no Zap; responde uma pessoa"), no mesmo padrão do `MOTIVO_EMAIL_SO_PESSOA` (`:133-136`). `zap + outra plataforma` continua com `MOTIVO_ZAP_SEM_ENVIO` | 2 |
| `app/services/atendimento/canais_externos.py:46-47, 57-86, 110-129` | `PREFIXO_ZAP='zap:'`, `ref_do_zap(pnid)` e o ramo novo em `partes()` → `('zap','whatsapp',pnid)`. Sem isso, `garantir_canal` (`:153-157`) levanta `ReferenciaInvalida` e o filtro `?externo_ref` da lista dá 422 (`routers/atendimento.py:2274`). Textos do Zap em `nome_do_canal` e `descricao_da_origem` | 1 |
| `app/routers/atendimento.py:2929` | O "Sugerir" recusa toda `PLATAFORMAS_EXTERNAS` com a frase "carrinho de site nem comentário de rede". Precisa de uma frase própria para `whatsapp` | 1 |
| `app/routers/atendimento_painel.py:84-101, 119` | `_painel_externo` ganha o texto do Zap ("Zap sem venda vinculada"). Na Onda 3, o `envio_foto` do Zap passa a ser `pode=True` (hoje todo canal externo devolve `pode=False`, e a tela decide o botão de foto e a legenda por esse bloco) | 1 / 3 |
| `app/routers/atendimento.py:1500-1539` (`_motivo_do_status`) e `:1886` (flags no `/resumo`) | Frases de saúde do número; `zap_leitura_ativa` e `zap_envio_ativo` nas flags. `_motivo_do_status` só enxerga o `CanalOut` (`:1409-1440`), por isso **quem grava a saúde no `atendimento_canais`** (`status`, `ultimo_ok_em`, `ultimo_erro`) é o processador do Zap, como fazem `redes.aplicar_status` (`redes.py:1265-1279`) e `carrinhos.py:1424-1426` | 1 / 2 |
| `app/worker.py:1225-1245` (molde) e `functions` (`:4810`), `cron_jobs` (`:4941`) | Job `atendimento_zap_processar` e cron `atendimento_zap_varrer` (a cada minuto), que saem na hora com a chave desligada | 1 |
| `app/services/atendimento/enviar.py:373-378` (`_Destino`), `:384-402` (`_destino`), `:430-445`, `:774-826` (`_chamar_plataforma`), `:1321` | O desvio do Zap (§6.2). **`ADAPTADORES` (`:197-206`) não ganha `whatsapp`**, de propósito: as automações (`:1684`) e a foto genérica nunca pegam o Zap por engano | 2 |
| `app/services/atendimento/validador.py:679-701` e `:707` (molde `_DOCUMENTO_MAGALU`) | Exceção **só** para `(whatsapp, zap)` no bloco de contato, e uma regra nova **só do Zap**, para pessoa e IA: cartão, agência/conta, pedido de cartão/RG/CPF (§7). Hoje o `_DADO_PESSOAL` só roda para a IA (`:725-737`) | 2 |
| `app/services/atendimento/foto.py:66-80, 207-224, 249-283` e `enviar.py:1517-1525, 1560-1588, 883` | Foto de saída no Zap, com legenda opcional: `legenda_permitida` ≠ `legenda_obrigatoria`, ramo do Zap em `_chamar_plataforma_foto` (hoje chama `clientes.cliente_da_integracao(None)` e daria `cliente_indisponivel`) e URL relativa em `_aplicar_resultado` (hoje só grava `https://`) (§6.8) | 3 |
| `app/worker.py:4166-4175` (`webhook_signature_alert_scan`) | Também ler `webhook:meta_wa:*` (e o `webhook:meta:*` do Instagram, que hoje ninguém lê) | 4 |
| `app/services/vigia_leitura_atendimento.py:187-201` (`classe_do_canal`) e `:142` (`LIMITES`) | A porta de entrada é `classe_do_canal`, que hoje devolve None para `whatsapp`. Ganha uma `CLASSE_ZAP`, com regra própria em `LIMITES`: só número `ativo`, só em horário comercial e só se houve movimento nas últimas 24 h (webhook é "empurrado": número quieto à noite não é leitura parada) | 4 |
| `abas.py:318, 608, 630, 780, 830, 1102`; `cliente.py:796-799`; `routers/atendimento.py:578-590` | Vínculo Zap → venda, aba Zap na conversa do pedido, cartão Cliente e escopo da equipe | 6 |
| Web: `AtendimentoPlataforma.vue:13, 626, 640, 651, 799, 865, 1774`; `AtendimentoIconePlataforma.vue:19`; `AtendimentoLojas.vue:175`; `AtendimentoFiltroPlataforma.vue:50`; `AtendimentoConversa.vue:1108-1117, 1888, 1895-1898, 1907, 2434, 2591, 2721, 3313-3320`; `pages/atendimento.vue:228-240` | Vocabulário, ícone, barra, filtro, selo, janela, mídia, faixa das chaves, painel do pedido escondido no Zap (§8) | 1-4 |
| Web (testes que **já existem** e quebram): `apps/web/tests/atendimento-filtro-plataforma.cjs:103` (lista exata de `GRUPOS_CAIXA`) e `:319` (`'whatsapp'` como "plataforma fora do menu", `plataforma_topo === ''`) | Atualizar os dois **junto** com o grupo "WhatsApp" do filtro | 1 |
| `tests/test_atendimento_enviar.py:1324-1329` | O falso de `_chamar_plataforma` tem exatamente 4 parâmetros posicionais. Não muda, porque os nomeados novos só são passados quando `destino.zap` existe (§6.2) | 2 |
| `tests/test_atendimento_migration.py:82-91, 213, 220, 240-251` | `_MIGRATION_ZAP`; `assert len(TABELAS) == 18` passa a 24 (+6 tabelas `atendimento_zap_*`; reconferir o número depois do rebase); o schema de rascunho ganha o stub `marcas (id uuid PRIMARY KEY)`, porque a FK `numeros.marca_id → marcas` faria o upgrade falhar | 1 |

---

## 4. Modelo de dados + migration

### 4.1 O que NÃO precisa de schema novo

| Tabela | Uso no Zap | Por que já funciona |
|---|---|---|
| `atendimento_canais` | Uma linha por número: `externo_ref='zap:<pnid>'` (cabe no String(191)), `plataforma='whatsapp'` (cabe no String(16)), `canal='zap'`. Nasce em `observar`/`novo` | UNIQUE(externo_ref, canal) (`models/atendimento.py:163`); `garantir_canal` com ON CONFLICT |
| `atendimento_conversas` | `plataforma='whatsapp'`, `canal='zap'`, `canal_id` = canal do número, **`externo_id` = `str(contato.id)`**, `comprador_id` = `wa_id` (ou o BSUID, quando não há telefone), `comprador_nome` = nome do perfil, `conta` = número de exibição, `pode_enviar_ate` = última fala do cliente + 24 h, `dados = {comprador_plataforma: bsuid, zap: {numero_id, contato_id, ctwa?}}` | Sem integração, `upsert_conversa` usa o canal como chave (`gravar.py:574-576` → `_buscar_conversa :503-530`); unicidade em `uq_atendimento_conversas_robo` |
| `atendimento_mensagens` | `externo_id` = wamid; `tipo` (String(16), sem CHECK: entram `audio`, `localizacao`, `contato`, `figurinha`, `botao` e `modelo`); `anexos` com a referência da nossa cópia. **Dois caminhos fixos no `payload`:** `payload.zap = {tipo_meta, contexto_wamid, encaminhada, voz, referral, reacoes, entrega:{status, rank, em, erro_code, categoria, tipo_preco}, conciliado}` é gravado pelo **processador** (entrada e status do webhook); `payload.envio.zap = {message_status, efeito}` é gravado pelo **envio**, porque `_aplicar_resultado` guarda o payload do adaptador em `payload.envio` (`enviar.py:855`). O `MensagemOut` lê cada um no seu lugar | Idempotência pelo UNIQUE(conversa_id, externo_id) (`:485`); `payload` JSONB (`:537-539`) |

### 4.2 Migration `0415_atendimento_zap.py`

- `revision="0415_atendimento_zap"`, `down_revision="0414_vigia_conciliacao_chave_etapa"` (reconferir).
- `SCHEMA="davinci"`, `SET LOCAL lock_timeout`, docstring em português com "Revision ID/Revises". Molde: `alembic/versions/0362_atendimento_carrinho_redes.py`.
- **Todas as tabelas com o prefixo `atendimento_zap_`.** Assim elas entram sozinhas no `TABELAS` de `tests/test_atendimento_migration.py:94-98`, que filtra pelo prefixo `atendimento_`. A migration nova entra na lista `:82-91` (`_MIGRATION_ZAP`) e no carregamento do teste (como `_MIGRATION_VISTO`, `:213`). No mesmo teste: `assert len(TABELAS) == 18` (`:220`) passa a 24 e o conjunto conferido ganha as 6; o schema de rascunho (`:240-251`) ganha `CREATE TABLE "{rascunho}".marcas (id uuid PRIMARY KEY)`, senão a FK `numeros.marca_id` derruba o upgrade.
- **Histórico (LGPD):** na mesma entrega, `zap_(contatos|eventos|midias)` entra em `EXCLUIDAS` (`app/historico/sql.py:116-118`) e as três tabelas entram na lista "not in com" de `tests/test_historico_regras.py:131-147`. Sem isso, o gatilho grava por 365 dias o telefone, o nome do perfil e o texto do cliente a cada ação de pessoa nessas linhas (inclusive a própria exclusão LGPD). Detalhe no §4.3.
- Os modelos ficam em `app/models/__init__.py`. As tabelas entram em `_CLEANUP_TABLES` (`tests/conftest.py:426`), **antes** de `atendimento_mensagens`/`atendimento_conversas`/`atendimento_canais` e de `marcas`/`users`, nesta ordem: `atendimento_zap_midias`, `atendimento_zap_eventos`, `atendimento_zap_contatos`, `atendimento_zap_numeros`, `atendimento_zap_contas` e `atendimento_zap_apps`.
- O schema da mídia já entra nesta migration, para o processador da Onda 1 registrar as mídias como `pendente` e a Onda 3 só acrescentar o baixador. Uma migration a menos numa fila que anda rápido.

#### `atendimento_zap_apps` — um app Meta (pode haver vários)

| Coluna | Tipo | Nota |
|---|---|---|
| id | UUID PK | |
| app_id | String(32) NOT NULL **UNIQUE** | Vai na URL do webhook (é público, não é segredo) |
| nome | String(120) | Ex.: "DaVinci Zap — Gerenciador X" |
| segredo_enc | LargeBinary NOT NULL | `encrypt_json({"atual": app_secret, "anterior": str\|null, "anterior_ate": iso\|null})`. A Meta troca a chave na hora, sem período em que as duas valem. Por isso: troca **de rotina** = o anterior vale só **1 h** (e cada assinatura que bate só com ele é contada no Redis); troca **por vazamento** = o anterior é apagado na hora |
| verify_token_enc | LargeBinary NOT NULL | Gerado pelo DaVinci (`secrets.token_urlsafe(32)`). Mostrado ao admin sob clique (fica no Histórico, `REVELACOES`), com botão "gerar outro" |
| teste | bool NOT NULL default false | App de teste ("DaVinci Zap Teste"). Em produção, o cadastro recusa app de teste ligado a número que não seja de teste |
| ativo | bool NOT NULL default true | |
| ultimo_evento_em | timestamptz NULL | Saúde. A falha de assinatura **não** vai para o banco (só Redis, §5.1) |
| criado_por | UUID FK users SET NULL | |
| created_at, updated_at | | TimestampMixin |

#### `atendimento_zap_contas` — uma WABA (conta WhatsApp Business)

| Coluna | Tipo | Nota |
|---|---|---|
| id | UUID PK | |
| app_ref | UUID FK → apps RESTRICT | O app que assina os eventos desta WABA |
| waba_id | String(32) **UNIQUE** | |
| business_id | String(32) NULL | Portfólio. O BSUID é por portfólio |
| nome | String(120) | |
| token_enc | LargeBinary NOT NULL | `encrypt_json({"access_token", "scopes", "system_user_id", "tipo": "system_user"})` |
| token_expira_em, token_nunca_vence | timestamptz NULL, bool NOT NULL default false | Gravados **só** pela função nova `zap_api.inspecionar_token` (§10.1). `expires_at=0` vira `token_nunca_vence=true` de forma explícita. Inspeção que falha é **erro** e não grava nada (a `validade_do_token` de `meta_client.py:388-420` devolve None tanto para "nunca vence" quanto para "não consegui ver", e com `expires_at=0` cai no `data_access_expires_at`) |
| token_conferido_em, token_trocado_em | timestamptz NULL | Última inspeção ok; última troca (rotação semestral, §10.1) |
| inscrito_em | timestamptz NULL | Quando o `POST /{waba}/subscribed_apps` deu certo **e** o `GET /{waba}/subscribed_apps` mostrou só o nosso app |
| status | String(16) | `novo` / `ok` / `token_invalido` / `sem_permissao` / `vencendo` / `erro` |
| ultimo_ok_em, ultimo_erro, ultimo_erro_code, ultimo_erro_em | | A frase é em português e nunca contém o token |

#### `atendimento_zap_numeros` — um número (o botão "pausar" mora aqui)

| Coluna | Tipo | Nota |
|---|---|---|
| id | UUID PK | |
| conta_ref | UUID FK → contas RESTRICT | |
| phone_number_id | String(32) **UNIQUE** | Chave do webhook (`metadata.phone_number_id`), como o `uq_dm_contas_ig_user_id` (`models/instagram_dm.py:181-187`) |
| display_phone_number | String(32) | |
| verified_name, nome_status | String(120), String(24) | Nome de exibição e se está aprovado |
| nome_na_barra | String(120) NULL | Ex.: "Charlots · (11) 9xxxx-xxxx" |
| marca_id | FK marcas SET NULL, NULL | Para o nome e, no futuro, o escopo e o `sac_fone` |
| canal_id | UUID FK atendimento_canais SET NULL, UNIQUE | O canal `zap:<pnid>` |
| teste | bool NOT NULL default false | Número de teste da Meta |
| etapa | String(16) NOT NULL default `'preparando'` | `teste` / `preparando` / `aguardando_duoke` / `migrando` / `ativo` / `pausado` / `desligado`. É o "um por vez" na tela |
| **envio_ligado** | bool NOT NULL default **false** | **Botão "pausar" do número**, lido do banco a cada envio |
| destinos_teste | ARRAY(String(32)) NOT NULL default `'{}'` | Lista de teste por número, que se soma à global (§6.1). Comparada pelo número **normalizado** (mesma regra do `wa_id_norm`), para o 9º dígito não deixar o celular do Eduardo de fora |
| teto_dia | int NULL | Teto de envios por dia **deste** número, definido a partir do volume medido no Duoke. NULL = usa `atendimento_zap_max_dia_numero` (§6.1) |
| pin_enc | LargeBinary NULL | PIN da verificação em 2 etapas, **guardado cifrado**. Mostrado ao admin sob clique, e cada vez fica no Histórico (`REVELACOES`). O Eduardo também anota fora do sistema. Decisão final na pergunta 14.2 nº 10 (Onda 4) |
| registro_tentativas | ARRAY(timestamptz) NOT NULL default `'{}'` | Cada `POST /{pnid}/register` feito. A Meta aceita 10 em 72 h e depois bloqueia o número por 72 h (erro 133016); o DaVinci para em **3** |
| saude | JSONB default `{}` | quality_rating, messaging_limit, name_status, throughput |
| saude_status | String(16) | `novo` / `ok` / `restrito` / `erro` / `sem_eventos` |
| ultimo_recebido_em, ultimo_enviado_em, ultimo_ok_em, ultimo_erro, ultimo_erro_code, ultimo_erro_em | | |
| duoke_liberado_em, migrado_em, observacao | | Para o roteiro da migração |

#### `atendimento_zap_contatos` — o cliente num número

| Coluna | Tipo | Nota |
|---|---|---|
| id | UUID PK | **É o `externo_id` da conversa** (P3) |
| numero_ref | UUID FK → numeros RESTRICT | |
| bsuid | String(64) NULL | `user_id` da Meta (por portfólio) [confirmar o nome do campo em `messages[]`/`statuses[]`] |
| wa_id | String(20) NULL | Como veio. É o que vai no `to` do envio |
| wa_id_norm | String(20) NULL | Só dígitos, com o 9º dígito brasileiro posto **só em número com cara de celular** (DDD + 8 dígitos começando por 6–9). **Só para casar contatos, nunca para enviar** |
| nome_perfil | String(120) | `contacts[].profile.name` |
| ultima_recebida_em | timestamptz NULL | **Relógio da janela de 24 h** = `GREATEST(atual, ts da Meta)`. Uma reentrega atrasada nunca encolhe a janela |
| janela_gratis_ate | timestamptz NULL | 72 h de anúncio CTWA (só afeta o custo) |
| opt_in_em, opt_in_origem | timestamptz NULL, String(32) NULL | Prova de que o cliente aceitou receber mensagem que **a loja** inicia (modelo). Sem isso, nenhum modelo sai (Onda 5). Os contatos que vêm do Duoke nascem **sem** opt-in |
| opt_out_em, opt_out_tipo | timestamptz NULL, String(16) NULL | `todos` ("PARAR", `user_preferences=stop`, pedido confirmado por pessoa) ou `marketing` (erro 131050). `todos` barra **qualquer** modelo, não só marketing |
| bloqueado | bool default false | |
| apagado_em | timestamptz NULL | Pedido LGPD |
| conversa_id | UUID FK atendimento_conversas SET NULL, UNIQUE | |
| created_at, updated_at | | |

Índices: `UNIQUE(numero_ref, bsuid) WHERE bsuid IS NOT NULL` e `UNIQUE(numero_ref, wa_id_norm) WHERE wa_id_norm IS NOT NULL`.

#### `atendimento_zap_eventos` — caixa de entrada do webhook (um item por linha)

| Coluna | Tipo | Nota |
|---|---|---|
| id | BigInteger identity PK | |
| chave | String(191) **UNIQUE** | `m:<wamid>` (mensagem) · `s:<wamid>:<status>` (status) · `e:<sha256 de entry.id + entry.time + item canônico>` (erro, conta, preferência, modelo, objeto inesperado; o `entry.time` entra para que um `account_update` ou um `value.errors` idêntico que volte dias depois **não** seja engolido como repetido) · `j:<sha256 do corpo>` (JSON inválido) |
| app_ref | FK → apps SET NULL | |
| waba_id, phone_number_id | String(32) NULL | |
| campo | String(48) | `messages`, `user_preferences`, `account_update`, `message_template_status_update`… |
| tipo | String(16) | `mensagem` / `status` / `erro` / `preferencia` / `conta` / `ignorado` |
| payload | JSONB | O item mais `metadata` e **só o `contacts[]` do remetente** daquele item (o `value` pode trazer outros clientes do mesmo lote) |
| recebido_em | timestamptz default now() | |
| processado_em, proxima_em | timestamptz NULL | Evento novo nasce com `proxima_em` NULL. O `ignorado` nasce já com `processado_em` preenchido |
| tentativas | int default 0 | |
| resultado | String(32) NULL | `gravado` / `repetido` (só quando `gravar_mensagem` diz que a mensagem já existia) / `numero_desconhecido` / `waba_desconhecida` / `numero_de_outra_waba` / `sem_alvo` / `objeto_inesperado` / `json_invalido` / `erro` |
| erro | String(200) NULL | Só o tipo do erro, **nunca o texto do cliente** |

Índice parcial da fila: `(proxima_em NULLS FIRST, id) WHERE processado_em IS NULL`, e o predicado de quem pega é **`processado_em IS NULL AND (proxima_em IS NULL OR proxima_em <= now())`** (com só `proxima_em <= now()`, o NULL do evento novo nunca seria verdadeiro e nada seria processado). O UNIQUE da `chave` também guarda o histórico de status, por isso não existe tabela de status separada. Retenção de 30 dias (cron `atendimento_zap_limpeza`, Onda 4). **Sem gatilho do Histórico** (§4.3).

#### `atendimento_zap_midias` (schema na Onda 1, baixador na Onda 3)

Colunas:
- `id` UUID PK; `numero_ref` FK; `mensagem_id` FK atendimento_mensagens CASCADE NULL;
- `direcao` (`entrada`/`saida`); `media_id` String(64); `mime` String(100) (o declarado); `mime_detectado` String(100) (pela assinatura do arquivo, §5.5); `sha256_meta` String(100); `tamanho` BigInteger;
- `nome_original` String(255), saneado;
- `caminho` String(255), relativo a `uploads_dir` (`config.py:32`): `atendimento/zap/<numero>/<aaaa>/<mm>/<uuid>.<ext>`;
- `sha256_local` char(64);
- `status`: `pendente` / `baixando` / `baixada` / `grande_demais` / `falhou` / `expirada` / `apagada`;
- `tentativas`, `proxima_em`, `expira_meta_em` (recebida + 7 dias), `apagar_em` (retenção), `erro`, `baixada_em`.

Restrição: UNIQUE(numero_ref, media_id).

### 4.3 Migrations das ondas seguintes (reconferir os números)

| Onda | Migration | Tabela |
|---|---|---|
| 5 | `04xx_atendimento_zap_modelos` | `atendimento_zap_modelos`: conta_ref, meta_id, nome, idioma, categoria, status, componentes JSONB, qualidade, motivo_rejeicao, sincronizado_em; UNIQUE(conta_ref, nome, idioma) |
| 6 | `04xx_atendimento_zap_vinculos` | `atendimento_zap_vinculos`: conversa_zap_id FK, integration_id, pedido_marketplace, bling_pedido_id NULL, conversa_venda_id NULL, criado_por/em, desfeito_por/em, motivo; índice único parcial (conversa_zap_id) WHERE desfeito_em IS NULL |

**Auditoria e Histórico.** Não há tabela de auditoria própria, mas o Histórico tem **duas** partes, e as duas precisam de cuidado:

1. **O middleware** (`historico/middleware.py`) grava o evento de cada POST/PATCH/DELETE de pessoa, com o corpo mascarado (`app_secret`, `token`, `secret`, `pin`: `historico/mascara.py:26-41`).
   - `SEM_CORPO` (`historico/nomes.py:259-261`) só pega `atendimento/(conversas|rascunhos|mensagens)/`. As rotas novas `/api/atendimento/zap/(conversas|contatos)/*` (modelo com parâmetros, vínculo com busca por CPF, exclusão LGPD) entram no `SEM_CORPO`.
   - `GET` que revela segredo só fica registrado se estiver em `REVELACOES` (`nomes.py:224-237`; `middleware.py:120-129`). Entram o `GET` do verify token e o `GET` do PIN.
   - As ações das rotas novas ganham frase em `ACOES`/`FRASES`.
2. **O gatilho no banco** (`historico/sql.py`), que entra em **toda** tabela, inclusive as futuras, pelo `garantir_gatilhos` diário. Ele só dispara em transação de pessoa (o worker não marca), mas grava a linha inteira e o antes/depois por 365 dias.
   - **Sem gatilho:** `atendimento_zap_contatos` (wa_id, BSUID, nome), `atendimento_zap_eventos` (texto do cliente no `payload`) e `atendimento_zap_midias` (nome do arquivo). Sem isso, "apagar dados deste contato", "reprocessar" e o vínculo copiariam para o Histórico exatamente o que a exclusão LGPD manda apagar.
   - **Com gatilho:** `apps`, `contas`, `numeros` (os `*_enc` saem como `{_arquivo:true}`; pausar, trocar etapa e trocar token aparecem para quem lê), `atendimento_zap_modelos` (Onda 5, texto da empresa) e `atendimento_zap_vinculos` (Onda 6). O vínculo é decisão de pessoa, guarda só o nº do pedido e o "desfazer fica no Histórico" é de propósito (§9).

Testes: PIN e token nunca aparecem no Histórico; as três tabelas ficam sem gatilho (`test_historico_regras.py`); as rotas `zap/(conversas|contatos)` não guardam corpo (§11.6).

---

## 5. Recebimento

### 5.1 Webhook público (`routers/whatsapp_webhook.py`)

**`GET /api/webhooks/meta/whatsapp/{app_id}`** (handshake)
- Copia `webhooks.py:790-812`:
  - confere `hub.mode == "subscribe"`;
  - compara o verify token do app **em bytes** com `hmac.compare_digest`;
  - responde o `hub.challenge` como **`text/plain`** (devolver JSON é a causa nº 1 de "URL couldn't be validated").
- App desconhecido ou inativo dá 404. Token errado dá 403.
- O verify token vai na query e aparece no access log (§10.2). Por isso a rota só responde no **host principal** (passo 0 abaixo).

**`POST /api/webhooks/meta/whatsapp/{app_id}`**
0. **Host.** Só responde quando o `Host` é o do `api_url` (`config.py:17`). Em outro host (`gestaoestoque.com`, cujo log no Caddy não tem filtro nenhum, `Caddyfile:52-71`) dá 404. Fica no código, sem mexer no Caddy de produção.
1. **Tamanho.** Lê o corpo por `request.stream()` e **para** ao passar de **4 MB**: 413 e conta no Redis. A Meta manda no máximo 3 MB. O `request.body()` leria tudo antes de conferir, o corpo pode vir em partes, sem `Content-Length`, e o Caddy não limita tamanho (`docs/deploy-atendimento.md:249`).
2. **App.** Acha o app pelo `app_id`, com cache em memória de 60 s, limpo na troca do segredo. `app_id` desconhecido também vai para um **cache negativo** de 60 s, para não consultar o banco a cada pedido. Se não achar: 404.
3. **Freio por IP, só para quem já errou.** Um IP com mais de 30 assinaturas inválidas em 10 min (contador no Redis) recebe 429 **antes** de calcular o HMAC. O `app_id` é público, então qualquer um com curl conseguiria forçar o HMAC de 4 MB a cada pedido. Os IPs da Meta que acertam nunca são freados.
4. **Assinatura.** Calcula o **HMAC-SHA256 do corpo cru** com o segredo atual. Se falhar e houver `anterior` dentro da janela de 1 h de uma troca de rotina (§4.2), tenta o anterior e soma um contador "bateu só com o anterior". Usa a função pura extraída de `webhooks.py:686-707`.
5. **Assinatura inválida:** responde 403 e grava `_bump_sig_failure` com a chave própria `meta_wa`, **só no Redis** (nada no banco a cada falha). O alarme (Onda 4) é **por proporção**: só dispara quando há falhas **e** nenhum evento válido daquele app no mesmo período. Um estranho com curl gera falhas, mas não para os eventos verdadeiros; a URL ou o segredo errados param todos. É o envenenamento de alarme que o docstring de `_bump_sig_failure` (`webhooks.py:84-88`) descreve.
6. **JSON inválido** (com assinatura certa): grava um evento `ignorado`, `resultado='json_invalido'`, chave `j:<sha256 do corpo>`, com o corpo cru no `payload` (para reprocessar à mão), alerta e responde 200. **Nada some em silêncio.**
7. Quebra o corpo em itens percorrendo `entry[] → changes[] → value`:
   - `field=='messages'`: um item por `messages[]`, por `statuses[]` e por `errors[]`, cada um com `metadata` e **só o contato do remetente**;
   - `field=='user_preferences'`: um item `preferencia` por usuário (vai para o contato, §5.3);
   - outro `field` (`account_update`, `phone_number_quality_update`, `phone_number_name_update`, `message_template_status_update`, `message_template_quality_update`…): um item `conta`;
   - `object != 'whatsapp_business_account'`: um item `ignorado` (com `objeto_inesperado`) com aviso no log;
   - **Não existe teto que descarte item.** As cópias de `webhooks.py:842` (teto 200×200) jogariam fora o resto de um lote de até 1.000 updates respondendo 200. O limite de 4 MB já segura o tamanho.
8. Um único `INSERT … ON CONFLICT (chave) DO NOTHING` com todos os itens. Depois, **COMMIT**. Se o banco falhar, responde **500** e a Meta reenvia. A reentrega da Meta já para aqui, no UNIQUE da `chave`.
9. **Enfileira coalescido.** `SET zap:processar:pedido NX EX 3` no Redis e, só se pegou, `enqueue_job("atendimento_zap_processar")` (`worker_pool.get_arq_pool()`). O job apaga essa chave ao começar, para que o que chegar durante a rodada peça outra. **Não** usar `_job_id` fixo: o worker guarda o resultado por 1 h e todo pedido dessa hora sumiria calado (`routers/marketing_metricas.py:271-274`). Um POST por mensagem viraria uma rajada de jobs competindo, todos saindo na hora com a chave desligada. Se enfileirar falhar, o cron de 1 min pega o evento.
10. Responde `200 {"ack": true, "novos": n}`.

O webhook **grava sempre** que a assinatura confere, mesmo com as chaves desligadas. Quem decide processar é o worker. Enquanto nenhum app estiver cadastrado no banco de produção, a rota responde 404 e não faz nada.

### 5.2 Processador (`zap_entrada.processar_eventos`)

- Wrapper no worker no molde de `atendimento_redes` (`worker.py:1225-1245`). Sai na hora se `not (atendimento_leitura_ativa and atendimento_zap_leitura_ativa)`. Os eventos ficam pendentes e são processados quando ligarem.
- Também roda o cron `atendimento_zap_varrer`, a cada minuto, com trava no Redis (SET NX EX). Ao terminar a rodada, relê a fila uma última vez antes de soltar a trava.
- **Um evento por transação, com commit a cada evento.** Laço: abre a transação, pega **1** evento com `processado_em IS NULL AND (proxima_em IS NULL OR proxima_em <= now())` e `FOR UPDATE SKIP LOCKED` (ordem `proxima_em NULLS FIRST, id`), processa e **commita**. Repete até esvaziar ou estourar o tempo da rodada. É **fila, não descarte**.
  - Por quê: `gravar_mensagem` trava a conversa (`FOR NO KEY UPDATE`, `gravar.py:395-433`) até o fim da transação. Um lote de 200 numa transação só seguraria essa trava, e o "Enviar" da tela, que espera 5 s (`enviar.travar_conversa`, `enviar.py:661-672`), receberia `conversa_ocupada`. É o mesmo motivo do `fim_do_item` do sync (`gravar.py:381-392`).
- **Erro num evento** (inclusive `IntegrityError`): rollback **só daquele evento**, depois `tentativas+1` e `proxima_em` em backoff (30 s → 2 min → 10 min → 1 h → 6 h, até 20 tentativas; depois `resultado='erro'` com alerta). O log leva só o tipo do erro.
  - O `IntegrityError` **nunca** vira `repetido`. A reentrega da Meta já é barrada no INSERT do webhook (UNIQUE da `chave`), e a corrida de conversa e de mensagem já é resolvida dentro de `upsert_conversa`/`gravar_mensagem` (savepoint e releitura, `gravar.py:612-627` e `:1019-1028`). O que sobra é defeito (FK, UNIQUE de `contatos.conversa_id`, índice de envio em voo), e marcá-lo como processado perderia a mensagem em silêncio.
- As primeiras conferências são duas:
  - o `waba_id` (`entry.id`) do evento pertence ao app do evento. Se não pertencer: `waba_desconhecida`, com alerta (protege contra rota trocada);
  - o `phone_number_id` do `metadata` pertence **àquela** WABA no nosso cadastro. Se não pertencer: `numero_de_outra_waba`, com alerta.
- O número é achado por `phone_number_id`. Desconhecido: `numero_desconhecido`, que fica reprocessável quando o número for cadastrado.
- **Saúde na barra.** A cada evento do número processado, grava `atendimento_canais.status='ok'` e `ultimo_ok_em` no canal `zap:<pnid>`. Erro de conta ou do número (`value.errors`, `account_update` de restrição, token inválido) grava `status='erro'`, `ultimo_erro` (frase em português) e `ultimo_erro_em`. É o que `redes.aplicar_status` e `carrinhos.py` já fazem. Sem isso, o canal ficaria `novo` para sempre e a barra diria "Ainda não foi lida".
- No localhost, o `simular --processar` chama `processar_eventos` no mesmo processo, sem worker (§11.4).

### 5.3 Mensagem recebida → contato → conversa → mensagem

1. **Contato.** `bsuid` e `wa_id` vêm de `contacts[]` / `messages[].from` (com `from_user_id` [confirmar]).
   - Busca por `(numero, bsuid)` e, se não achar, por `(numero, wa_id_norm)`.
   - Achou pelo BSUID e o telefone veio novo: completa o contato.
   - Achou pelo `wa_id_norm`:
     - o contato achado **não tem** BSUID: completa com o BSUID que chegou;
     - o contato achado **já tem um BSUID diferente**: é **outra pessoa** (no Brasil o número de celular é reciclado). Cria um contato novo, **solta** o `wa_id_norm` do antigo (`NULL`, com nota `sistema` "o número passou a ser de outra pessoa" na conversa antiga) e loga `zap_numero_reciclado`. Juntar os dois mostraria à equipe o histórico e os pedidos do dono anterior, e ela poderia revelá-los a um terceiro (incidente de LGPD).
   - Não achou: `INSERT … ON CONFLICT DO NOTHING` e relê.
   - Se os dois apontarem para contatos **diferentes**, fica o achado pelo BSUID, com log `zap_contato_duplo` e alerta. **A v1 não junta contatos sozinha.**
   - Grava `ultima_recebida_em = GREATEST(atual, ts)`.
2. **Canal.** `garantir_canal(externo_ref=ref_do_zap(pnid), plataforma='whatsapp', canal='zap', nome=nome_na_barra or verified_name or display)`, que grava `numero.canal_id`.
3. **Conversa.** `gravar.upsert_conversa(canal=…, integration=None, plataforma='whatsapp', canal_nome='zap', externo_id=str(contato.id), conta=display_phone_number, comprador_id=contato.wa_id or contato.bsuid, comprador_nome=nome_perfil, pode_enviar_ate=contato.ultima_recebida_em + 24h, dados={...})` (`gravar.py:533`). Grava `contato.conversa_id`.
   - Duas mensagens ao mesmo tempo de um cliente novo: `upsert_conversa` resolve a corrida por dentro (savepoint e releitura). Se ainda assim sobrar um `IntegrityError`, o evento vai para o backoff do §5.2 e é tentado de novo.
4. **Mensagem.** `gravar.gravar_mensagem(conversa, externo_id=wamid, autor='cliente', texto, enviada_em=datetime.fromtimestamp(int(ts), UTC), tipo, anexos, payload={'zap': …})` (`gravar.py:917`).
   - **O ts do WhatsApp vem em segundos e como string.** O do Instagram vem em ms (`webhooks.py:901-906`).
   - É idempotente pelo wamid e já recalcula a fila, o prazo e a etiqueta.
   - O conteúdo de `payload` vai sem dado pessoal além do que já está no texto.
5. **Fora de ordem.** A linha do tempo segue o relógio da Meta. Uma mensagem antiga que chega depois de uma resposta nossa mais nova **não** reabre "aguardando" (`gravar.recalcular`, `:1081-1205`). Esse é um teste obrigatório.
6. **Opt-out** (pedido para não receber mais). Nunca bloqueia o atendimento: o cliente continua podendo falar e ser respondido dentro das 24 h.
   - Mensagem **inteira** "PARAR", "SAIR" ou "STOP": grava `opt_out_em` com `opt_out_tipo='todos'` na hora.
   - Frase que **parece** pedido de parada ("quero parar de receber", "não me mande mais mensagem", "me tire da lista"): **não** grava sozinha. Põe uma nota `sistema` na conversa ("o cliente parece ter pedido para não receber mais: confirme") e a pessoa confirma com um clique, que grava `opt_out_em`.
   - "Cancelar" nunca conta, porque é comum em pedido.
7. **`user_preferences`** (evento próprio, tipo `preferencia`). O cliente usou o "parar" do próprio WhatsApp: `stop` grava `opt_out_em` (`todos`) no contato achado pelo BSUID ou pelo `wa_id_norm`; `resume` limpa. Não vira mensagem na conversa, só uma nota `sistema`. Hoje esse campo cairia como `conta` e nunca chegaria ao contato.
8. **Troca de BSUID** [a confirmar]: a lista oficial de campos do webhook (consultada em 17/10) não traz `user_id_update` nem `business_username_updates`. Se a Meta confirmar o campo, ele ganha tratador próprio, que atualiza `contato.bsuid`. Até lá, entra como `conta` e gera alerta.

### 5.4 Tradução dos tipos

| WhatsApp | `tipo` gravado | Texto / anexos / payload |
|---|---|---|
| text | `texto` | `text.body` |
| image | `imagem` | Legenda, ou "[Foto]". Anexo `{tipo:'imagem', midia_id:<id nosso>, mime, status:'pendente'}` mais uma linha em `atendimento_zap_midias` |
| sticker | `figurinha` | "[Figurinha]" mais a mídia |
| audio | `audio` | "[Áudio]" ou "[Áudio de voz]" (`voice:true`) mais a mídia |
| video | `video` | Legenda, ou "[Vídeo]", mais a mídia |
| document | `arquivo` | Legenda ou o nome do arquivo, mais a mídia |
| location | `localizacao` | "[Localização] nome · endereço"; `lat`/`long` no payload |
| contacts | `contato` | "[Contato] nome · telefone" |
| interactive (`button_reply`/`list_reply`) | `botao` | O `title` escolhido; `id` e `contexto_wamid` no payload |
| button (resposta de modelo) | `botao` | `button.text`; `payload` no payload |
| reaction | **não vira mensagem** | Vai para `payload.zap.reacoes` da mensagem citada, para não abrir a vez na fila. Se a citada não existe: `sem_alvo` |
| unsupported | `outro` | "[O cliente mandou algo que o WhatsApp não entrega pela API: <tipo>]". Conta como fala do cliente |
| system (`user_changed_number`) | nota `sistema` | "O cliente trocou de número"; atualiza o `wa_id` do contato (e o `wa_id_norm`, se o número novo estiver livre naquele número da loja; se não estiver, aplica a regra do número reciclado do §5.3) |
| `messages[].errors` / `value.errors` | — | Evento `erro`: log + `numero.ultimo_erro` e alerta |

`context.id` (o cliente respondeu citando uma mensagem) vai para `payload.zap.contexto_wamid`; `context.forwarded` vai para `encaminhada`; `referral` (anúncio CTWA) vai para `dados.zap.ctwa` e `contato.janela_gratis_ate`.

### 5.5 Mídia (Onda 3; o registro como `pendente` já começa na Onda 1)

Prazos da Meta: o **id da mídia vale 7 dias** e a **URL vale 5 minutos**.

**Job `zap_midia.baixar(midia_id)`**: é enfileirado depois do commit da mensagem, e o `varrer` é a rede de segurança.

1. Faz `GET /{v}/{media_id}` com o Bearer do token da WABA, que devolve `url`, `mime_type`, `sha256` e `file_size`. Prefere essa URL à que às vezes vem no próprio webhook.
2. **O Bearer só segue para um host permitido**: `https` e host em `lookaside.fbsbx.com`, `*.fbcdn.net` ou `graph.facebook.com` [confirmar os domínios]. Isso protege contra SSRF e contra vazar o token. O cliente HTTP usa **`follow_redirects=False`**: se a Meta responder com redirecionamento, o DaVinci confere o host do novo endereço contra a mesma lista **a cada salto** (no máximo 3) antes de mandar o Bearer de novo.
3. Baixa em streaming para um arquivo temporário. Acima de `atendimento_zap_midia_max_mb`: `grande_demais`.
4. Confere o `sha256` [confirmar se vem em hex ou base64]. Se divergir: `falhou`.
5. O MIME tem de estar na lista: JPEG/PNG, MP4/3GP, AAC/AMR/MP3/M4A/OGG-OPUS, PDF/DOC(X)/XLS(X)/PPT(X)/TXT, WebP. **Nunca** SVG, HTML ou executável. O tipo vale **pela assinatura do arquivo** (os primeiros bytes, gravados em `mime_detectado`), não pelo `mime_type` que o remetente declarou. Se os dois não baterem com a lista: `falhou`, com "tipo de arquivo não aceito".
   - Antivírus (ex.: ClamAV) para documentos que a equipe baixa fica **[VETÁVEL]** para depois da Onda 3. Até lá, documento sai sempre como `attachment`, nunca aberto no navegador.
6. Move para o caminho final, grava `sha256_local` e `baixada`, e atualiza o anexo com `url:'/api/atendimento/zap/midia/<id>'`, nome e tamanho.
7. 404 na URL: pede a URL de novo. Backoff 1 → 5 → 30 min → 2 h → 12 h até `expira_meta_em` menos uma margem. Depois disso: `expirada`, e o balão diz "a mídia expirou antes de ser baixada".

Isso contraria **de propósito** o "nunca o arquivo" de `models/atendimento.py:515-519`, e só para o Zap.

**Rota `GET /api/atendimento/zap/midia/{id}`:**
- autenticação por cookie (o `<img src>` funciona);
- confere `pode_ver` e o escopo da conversa;
- `Content-Type` da **nossa** lista (pelo `mime_detectado`), mais `X-Content-Type-Options: nosniff` e `Content-Security-Policy: sandbox`;
- `inline` só para imagem, áudio e vídeo; documento vai como `attachment`;
- `Cache-Control: private`;
- padrão `FileResponse` de `routers/denuncia.py:417-426`.

O arquivo fica no disco **sem cifra**. Por isso a retenção (180 dias, P10) e a entrada de `/data/uploads` no backup do servidor são decisões explícitas do Eduardo (pergunta 14.2 nº 2).

### 5.6 Status de entrega e leitura

Os status chegam como eventos `s:<wamid>:<status>`, e o UNIQUE já tira a reentrega.

1. **Acha a mensagem nossa:**
   - (a) pelo `biz_opaque_callback_data`, que nós mandamos em todo envio com o `atendimento_mensagens.id` (busca por PK; confere se é conversa Zap deste número);
   - (b) se não houver, pelo contato (`recipient_id` → `wa_id_norm`/bsuid) → conversa → `(conversa_id, externo_id=wamid)`;
   - (c) se não achar, fica pendente com `proxima_em = +30 s` por até 1 h. Depois disso: `sem_alvo`, por exemplo uma mensagem mandada pelo Duoke ou pelo WhatsApp Manager.
2. **Nunca mexe em linha `enviando`.** Ela é do envio em curso e o status espera 30 s. Isso evita que um `failed` processado antes do `_gravar_resultado` seja sobrescrito por `enviada`.
3. **Ordem que não volta atrás:** sent=1 < delivered=2 < read=3 < played=4. Pode chegar `read` sem `delivered`. Um rank menor que chega depois não muda nada. Cada horário (`entregue_em`, `lida_em`) é gravado uma vez só, em `payload.zap.entrega`.
4. **`failed`:**
   - a linha vira `status='falhou'` e `erro='meta:<code>'`, com a frase de `zap_erros`, e passa por `recalcular_conversa` (`gravar.py:1253`). É o padrão da "falha que chega depois" da Magalu (`magalu.py:38-40, 169-173`);
   - o **131047** também fecha `pode_enviar_ate = agora`, o que corrige diferença de relógio. A trava 12 do envio lê esse mesmo campo (§6.3), então a tela para de deixar enviar;
   - se depois vier `delivered`/`read`, a linha volta para `enviada`, com log;
   - **efeitos fortes vindos do webhook** (131048, 368, 131031, 190): só **alerta** na primeira vez. A pausa automática do número só acontece com a resposta da **nossa** chamada à API (§6.5), que vem pelo TLS do `graph.facebook.com`, ou com **2 ocorrências** em wamids diferentes em 24 h. Quem roubasse a chave secreta não conseguiria pausar o número com um `failed` forjado.
5. **Conciliação do ambíguo.** Uma linha em `revisar` (timeout no envio) que recebe status pelo `biz_opaque` ganha o `externo_id = wamid` (se livre, `_externo_id_livre`, `enviar.py:829`), passa a `enviada` (ou `falhou`) e ganha `payload.zap.conciliado=true`. Nada é reenviado.
6. **Custo.** O `pricing` vem em mais de um status (`sent` e `delivered`), então o contador conta **uma vez por wamid**: o primeiro status com `pricing` grava `payload.zap.entrega.categoria` e `tipo_preco`, e os seguintes não contam de novo. A conta usa `pricing.type` (`regular` = cobrada; `free_*` = grátis) e `pricing.category`. **Não** usa `billable`, que a Meta vai remover. Alimenta o "respostas do mês: N de 1.000 grátis" por número, com alerta em **800** e em **1.000**.
7. **`MensagemOut`** (`schemas/atendimento.py:296-322`) ganha `entrega` e `entrega_erro` (de `payload.zap.entrega`) e `retida_pela_meta` (de `payload.envio.zap.message_status`).

### 5.7 Resumo da idempotência

| Camada | Chave | Efeito da reentrega |
|---|---|---|
| Evento | `atendimento_zap_eventos.chave` UNIQUE (com `entry.time` nos itens de conta, §4.2) | Não grava de novo |
| Contato | UNIQUE parcial (numero, bsuid) e (numero, wa_id_norm) | Acha o mesmo contato |
| Conversa | `uq_atendimento_conversas_robo` + `contatos.conversa_id` UNIQUE | Acha a mesma conversa |
| Mensagem | UNIQUE(conversa_id, externo_id=wamid) | `gravar_mensagem` devolve a linha que já existe |
| Status | chave `s:<wamid>:<status>` + rank monotônico | Sem efeito |
| Mídia | UNIQUE(numero, media_id) | Não baixa duas vezes |

---

## 6. Envio

### 6.1 Chaves (`config.py`, perto de `atendimento_envio_ativo`, `:517`). Todas nascem desligadas e têm teste do valor padrão

```python
# Zap (WhatsApp Cloud API, RF10) — TUDO nasce desligado.
atendimento_zap_leitura_ativa: bool = False   # worker processa os eventos (o webhook guarda o cru mesmo assim)
atendimento_zap_envio_ativo: bool = False     # resposta pelo Zap (além do freio único, do número e do modo)
atendimento_zap_destinos_teste: str = ""      # wa_ids por vírgula; não vazia = SÓ eles recebem (comparados normalizados)
atendimento_zap_max_dia_numero: int = 300     # teto PADRÃO por número por dia; cada número tem o seu (numeros.teto_dia)
atendimento_zap_max_contato_10min: int = 8    # contra laço: envios ao MESMO contato em 10 min
atendimento_zap_midia_max_mb: int = 32
meta_whatsapp_graph_version: str = "v26.0"    # própria do Zap; subir a do Instagram não mexe aqui
atendimento_zap_retencao_eventos_dias: int = 30
atendimento_zap_retencao_midia_dias: int = 180  # [VETÁVEL] decisão LGPD do Eduardo
atendimento_zap_marcar_lida: bool = False     # Onda 5 — tiques azuis [VETÁVEL]
atendimento_zap_modelos_ativo: bool = False   # Onda 5 — modelos pagos
atendimento_zap_ia_ativa: bool = False        # Onda 7
```

Regras:
- **Nenhuma lista "libera" uma chave desligada.** Para sair mensagem, tudo isto precisa estar ligado ao mesmo tempo:
  - `atendimento_envio_ativo` (freio único, `config.py:517`);
  - `atendimento_zap_envio_ativo`;
  - `numero.envio_ligado`;
  - o canal num modo que envia.
  
  As listas de teste (a global mais a do número) só **restringem**.
- **Teto por número.** O padrão de 300 por dia é só o valor inicial. Antes de cada número real, o teto (`numeros.teto_dia`) é posto **a partir do volume medido no Duoke** (pergunta do §13.2), com folga. A tela avisa em **70 %** do teto. O bloqueio duro de verdade contra defeito em laço é o `atendimento_zap_max_contato_10min`: mais de 8 envios ao mesmo contato em 10 min. Assim, um dia movimentado não deixa o número "fora do ar".
- **Fail-closed fora de produção.** Com `ENV != production`, o Zap só envia se houver lista de teste preenchida. Isso existe porque no ambiente do `dca.sh` nada barra o `graph.facebook.com`, e nem o `_HOSTS_BARRADOS` de `scripts/atendimento_local.py:2173` barra.
- **Botões no banco** (tela da Onda 4; antes, o script):
  - "Pausar" por número: `envio_ligado=false`;
  - "Pausar todos": `UPDATE … SET envio_ligado=false`.
  
  Valem na hora, sem reiniciar nada. A chave do `.env` só vale depois de recriar api e worker (`lru_cache`, `config.py:933`). **Recriar é publicar**, e publicar exige o OK do Eduardo. Por isso a chave `ATENDIMENTO_ZAP_ENVIO_ATIVO` de produção é ligada **na véspera** do 1º número real, com todos os números ainda em `envio_ligado=false` (item do "vai/não vai", §13.3). A `ATENDIMENTO_ENVIO_ATIVO` é o freio geral de **todas** as lojas: só se confere que ela já está como precisa, nunca se liga por causa do Zap.

### 6.2 O desvio no caminho único (`services/atendimento/enviar.py`)

1. `_Destino` (`:373-378`) passa a ter `integration: Integration | None` e `zap: ZapDestino | None = None` (número, conta, contato e token decifrado só na memória do processo). No dataclass `ZapDestino`, o token é **`token: str = field(repr=False)`**, para um `logger.info(destino=…)` ou uma exceção não levarem o token no texto. (O Sentry já está com `include_local_variables=False`, `services/sentry.py:124`, mas o log não tem essa proteção.)
2. Em `_destino` (`:384`), logo **depois** da checagem da ponte de e-mail (`:397-402`) e **antes** de `motivo_canal_sem_envio` (`:404`):
   ```python
   if conversa.canal == CANAL_ZAP and conversa.plataforma == PLATAFORMA_WHATSAPP:
       return await _destino_zap(session, conversa, origem=origem)
   ```
   A decisão é pelo **canal E pela plataforma**. O Zap no formato antigo continua caindo na trava `:404` com "Responda pelo Duoke" (P2).
3. As travas do sistema de `:430-445` (simulador em produção e freio único) passam para um helper `_travas_do_sistema(settings, conversa)`, chamado pelos dois caminhos. É **movimento puro**, coberto pelos testes atuais.
4. `motivo_para_nao_enviar` (`:628-653`) já chama `_destino`. Assim, a faixa da tela e o botão dizem a mesma coisa, sem código extra. Como `motivo_canal_sem_envio` passa a devolver `MOTIVO_ZAP_SO_PESSOA` (não vazio), `modo_observacao` (`routers/atendimento.py:2557-2563`) dá **False**. A tela mostra o compositor com o cadeado e o porquê, e não o painel "O que a IA responderia". É o mesmo comportamento do e-mail da ponte.
5. `_chamar_plataforma` (`:774-826`) ganha os parâmetros nomeados `zap: ZapDestino | None = None` e `mensagem_id: UUID | None = None`. Os testes atuais que chamam por posição (`tests/test_atendimento_enviar.py:1383, 1532`) continuam iguais.
   - O simulador já funciona sozinho: `vai_para_o_simulador(conversa.plataforma)` (`:791`) recebe `'whatsapp'`, e `ATENDIMENTO_SIMULADOR_EXCETO=whatsapp` deixa **só o Zap** sair de verdade no localhost.
   - Com `zap`, chama `zap.enviar_texto` dentro do mesmo `wait_for`/`except` (timeout ou erro inesperado = ambíguo), sem passar por `clientes.cliente_da_integracao` (`:798-805`).
6. O único chamador (`:1321`) passa os nomeados **só quando `destino.zap is not None`**:
   ```python
   extra = {"zap": destino.zap, "mensagem_id": mensagem.id} if destino.zap is not None else {}
   resultado = await _chamar_plataforma(session, conversa, destino.integration, normalizado, **extra)
   ```
   Um teste existente troca `_chamar_plataforma` por um falso de exatamente 4 parâmetros posicionais (`tests/test_atendimento_enviar.py:1324-1329`). Passar sempre os nomeados quebraria esse teste com `TypeError`. Assim, ele fica igual.
7. **Efeitos do erro** (pausar o número, marcar token inválido, fechar a janela): o adaptador **não** grava nada no banco. Ele só os descreve em `payload['zap']['efeito']` do `ResultadoEnvio`. O `enviar_resposta` aplica esses efeitos **depois** de `_gravar_resultado`, numa transação curta e própria (`zap_erros.aplicar_efeito`). Dentro do adaptador, eles se perderiam no rollback do laço de `_gravar_resultado` (`enviar.py:1380-1449`), e `ResultadoEnvio` (`constantes.py:888-902`) não tem outro campo para isso. O contrato não muda.

### 6.3 Ordem das travas em `_destino_zap`

Cada trava tem um código e uma frase própria. As frases novas entram em `ERROS` (`AtendimentoPlataforma.vue:1774`).

| # | Trava | Código | Frase na tela |
|---|---|---|---|
| 1 | `origem != pessoa` (IA, automática) | `canal_sem_envio` | `MOTIVO_ZAP_SO_PESSOA`: "No Zap quem responde é uma pessoa (a IA não sugere nem envia)." |
| 2 | Simulador ligado em produção | `simulador_em_producao` | (a de hoje) |
| 3 | `atendimento_envio_ativo` desligado | `envio_desligado` | (a de hoje) |
| 4 | `atendimento_zap_envio_ativo` desligado | `zap_envio_desligado` | "Zap: o envio pelo DaVinci está desligado. Responda pelo Duoke." |
| 5 | Conversa bloqueada | `conversa_bloqueada` | (a de hoje) |
| 6 | Canal e número **relidos** (`populate_existing`); canal sem `zap:` ou número sumido | `sem_integracao` | "Este número de WhatsApp não está mais cadastrado no DaVinci." |
| 7 | `etapa ∉ {teste, ativo}` ou `envio_ligado=false` | `zap_numero_pausado` | "Este número está pausado no DaVinci (Lojas e modo → Números do WhatsApp)." |
| 8 | `canal.modo ∉ MODOS_QUE_ENVIAM` (`constantes.py:233`) | `canal_em_observacao` | "Este número está em observar: quem responde é o Duoke." |
| 9 | Conta sem token, `token_invalido` ou `sem_permissao` | `zap_sem_credencial` | "A conexão com a Meta deste número caiu. Peça ao administrador um token novo." |
| 10 | Contato bloqueado ou apagado (LGPD) | `zap_contato_bloqueado` | "Este contato está bloqueado no DaVinci." |
| 11 | Lista de teste (global + número), comparada pelo número **normalizado**, e fail-closed fora de produção | `zap_fora_dos_testes` | "Modo teste: só os números de teste recebem." |
| 12 | **Janela de 24 h no servidor**, com **uma fonte só**: `now() > min(contato.ultima_recebida_em + 24h, conversa.pode_enviar_ate) − 60 s`. O 131047 fecha `conversa.pode_enviar_ate` (§5.6); sem o `min`, a trava continuaria deixando enviar e a Meta devolveria 131047 a cada tentativa | `fora_da_janela` | "Passaram 24 h desde a última mensagem do cliente. O WhatsApp só deixa mandar um modelo aprovado." |
| 13 | Contra laço: mais de `atendimento_zap_max_contato_10min` envios ao mesmo contato em 10 min | `zap_laco` | "Muitas mensagens seguidas para este cliente. Espere alguns minutos." |
| 14 | Teto diário **do número** (`numeros.teto_dia` ou o padrão) | `zap_teto_dia` | "Este número já mandou o máximo de mensagens de hoje. Fale com o administrador." |

### 6.4 Adaptador `zap.enviar_texto(session, conversa, destino_zap, texto, *, mensagem_id)`

O adaptador **confere de novo** a lista de teste, a janela e `atendimento_envio_ativo` (cinto de segurança). Depois faz `POST {GRAPH_HOST}/{v}/{phone_number_id}/messages`, com o token **no header** (`meta_client._headers`, `:234`):

```json
{"messaging_product": "whatsapp", "recipient_type": "individual",
 "to": "<wa_id como veio>", "type": "text",
 "text": {"body": "...", "preview_url": false},
 "biz_opaque_callback_data": "<atendimento_mensagens.id>"}
```

- **Contato só com BSUID** (sem telefone): manda pelo campo `recipient` [confirmar o nome e a disponibilidade no Brasil]. Se não der, a recusa é clara.
- **`200` com `messages[0].id`**: `ResultadoEnvio(ok=True, externo_id=wamid, payload={'zap': {'message_status': …}})`. Esse payload vai parar em **`payload.envio.zap`** da mensagem (`enviar.py:855`), e o `held_for_quality_assessment` aparece como "segurada pela Meta".
- **Erro com código**: `ok=False`, `erro="meta:<code>"` e `payload={'zap': {'efeito': <da tabela §6.5>}}`. O efeito é aplicado pelo `enviar_resposta` depois de gravar o resultado (§6.2 item 7).
- **Timeout, 5xx sem código ou conexão cortada**: `ambiguo=True`. A mensagem vai para `revisar` e **nunca é retentada**. A conciliação é pelo status (§5.6).
- O `_redigir` (`meta_client.py:197`) tira o token de todo log. O contrato `ResultadoEnvio` é o de `constantes.py:888-902`.
- Limite: `LIMITE_CARACTERES[('whatsapp','zap')] = 4096`.

### 6.5 Erros da Meta (`zap_erros.py`)

| Código | Classe | Efeito no DaVinci | Frase (resumo) |
|---|---|---|---|
| 131047 | bloqueio | `pode_enviar_ate = agora` | "Passaram 24 h: só modelo aprovado." |
| 131026 | falhou | — | "Não foi entregue (o número pode não ter WhatsApp, ou o app está desatualizado)." |
| 131050 | falhou | `opt_out_em` com `opt_out_tipo='marketing'` | "O cliente pediu para não receber marketing." |
| 131049 | falhou | — | "A Meta segurou o marketing; tente em 24 h." |
| 131056 / 130429 | falhou (sem retentativa) | — | "Mensagens demais em pouco tempo; espere um minuto." |
| 131048 / 368 | **pausa o número** (pela resposta da nossa chamada; pelo webhook só com 2 ocorrências, §5.6) | `envio_ligado=false`, `saude_status='restrito'` + alerta | "A Meta restringiu este número. Veja no WhatsApp Manager." |
| 131031 | **pausa o número** e **trava o botão "registrar"** | `envio_ligado=false`, `saude_status='restrito'` + alerta | "A conta deste número foi travada pela Meta (pode ser PIN errado). **Não tente de novo; fale com o suporte da Meta.**" |
| 133016 | **trava o botão "registrar"** por 72 h | `registro_tentativas` cheio + alerta | "Tentativas de registro demais: a Meta bloqueou o registro deste número por 72 h. **Não tente de novo; fale com o suporte da Meta.**" |
| 131042 | **pausa o número** e alerta | `envio_ligado=false`, `saude_status='erro'` | "Problema de pagamento na conta Meta: as mensagens não estão saindo." Desde 01/10/2026 também barra a resposta comum, não só os modelos [a confirmar com a página de preços] |
| 131045 / 133010 | erro do número | `saude_status='erro'` | "Número não registrado na API." |
| 131037 | aviso | — | "Nome de exibição não aprovado." |
| 131051 / 131052 / 131053 | falhou (mídia) | — | "Tipo ou arquivo não aceito pelo WhatsApp." |
| 131057 | falhou | — | "Conta em manutenção na Meta; tente mais tarde." |
| 132000–132016 | falhou (modelo) | Sincroniza modelos | "Problema no modelo de mensagem." |
| 190 / 0 | **credencial** | `conta.status='token_invalido'` + alerta | "A conexão com a Meta caiu: é preciso um token novo." |
| 10 / 200 | **credencial** | `conta.status='sem_permissao'` | "O token não tem permissão nesta conta." |
| 131030 [não confirmado] | falhou | — | "Número de teste: este destino não está na lista de teste da Meta." |
| 5xx / timeout sem código | **ambíguo** | `revisar`, conciliado em §5.6 | "Não sabemos se saiu; confira antes de repetir." |

A tradução é feita **só** em `zap_erros`, pelo `_erro_zap` do `zap_api` (§3.1). Os dicionários `_ERRO_POR_CODE`/`_ERRO_POR_SUBCODE` do `meta_client` (`:134-166`) **não mudam**: o `_erro_da_resposta` (`:244-280`) lê esses dicionários do módulo, e mexer neles trocaria as frases do Instagram e do Facebook para 10, 200, 368 e 190, de que `tests/test_redes_sociais_conectar.py` depende.

### 6.6 Modelos aprovados — fora das 24 h (Onda 5)

- Lê `GET /{waba}/message_templates` e grava em `atendimento_zap_modelos` (cron diário e botão).
- Só sai modelo `APPROVED`, só por **pessoa**, com `atendimento_zap_modelos_ativo` ligada e só da categoria *utility* **ligada a um pedido** (ex.: "atualização da entrega do pedido {n}"). Um modelo genérico como "retomar_conversa" **não** entra: desde 09/04/2025 a Meta reclassifica como marketing o utility com cara de marketing, o que muda o custo e as regras de opt-out.
- **Opt-in obrigatório.** A política do WhatsApp só deixa a loja iniciar contato com quem deu opt-in, e isso vale para qualquer modelo, não só marketing. O modelo é **bloqueado** para contato sem `opt_in_em`. Os contatos herdados do Duoke nascem sem opt-in. Como registrar o opt-in (ex.: o cliente responde "sim" a uma pergunta da equipe dentro das 24 h, gravando `opt_in_origem='conversa'`) é decisão da Onda 5.
- **Opt-out vale para todo modelo:** com `opt_out_em` (tipo `todos`), nenhum modelo sai. Com o tipo `marketing`, só sai utility.
- Antes de enviar: confirmação com aviso de custo.
- O envio é `type=template` com `{name, language:{code}, components}`, e a mensagem é gravada com `tipo='modelo'` e o corpo já com os parâmetros.
- A **criação** dos modelos continua no WhatsApp Manager. O número de teste já vem com o `hello_world`.

### 6.7 Interativos, "digitando" e tiques azuis

- **Onda 5**, atrás de `atendimento_zap_marcar_lida`: marcar como lida com `POST /{pnid}/messages {status:'read', message_id:<último wamid de entrada>}`. Só vale até 30 dias depois de receber. Vai num ramo próprio, fora de `lida.PLATAFORMAS_QUE_MARCAM` (`lida.py:139-143`).
- **Onda 7**: o "digitando…" (`typing_indicator`), só se a resposta for sair mesmo.
- **Onda 7**: botões de resposta (até 3, título ≤ 20) e lista (≤ 10 linhas, título ≤ 24, descrição ≤ 72).
  - Só com a janela aberta, com os limites validados no servidor e o validador rodando sobre o corpo e todos os títulos.
  - Passam pelo mesmo `enviar_resposta`.
  - A **resposta** do cliente a botão ou lista já é lida desde a Onda 1.

### 6.8 Foto e documento de saída (Onda 3)

- `foto.py`: entra `('whatsapp','zap')` em `CAIXAS_COM_FOTO` (`:78-80`), com 5 MB em `MAX_BYTES_PLATAFORMA` (`:66-70`).
- `enviar_imagem` (`:249-283`) ganha o ramo: `POST /{pnid}/media` (multipart `file`, `type`, `messaging_product`) e depois `{"type":"image","image":{"id":…,"caption":…}}`.
- **`_chamar_plataforma_foto`** (`enviar.py:1560-1588`) ganha o ramo do Zap **antes** de `clientes.cliente_da_integracao(integration)` (`:1576`). Hoje ele seria chamado com `None` e a foto do Zap sairia `cliente_indisponivel`. O ramo usa o `destino.zap` e o mesmo `wait_for` (timeout = ambíguo).
- **Legenda opcional**, de até 1024 caracteres:
  - `legenda_permitida` deixa de ser igual a `legenda_obrigatoria` (`foto.py:217-224`): no Zap é permitida sem ser obrigatória;
  - é o 3º caso em `enviar.py:1517-1525`, que hoje tem legenda obrigatória no ML e proibida nos outros;
  - a legenda **passa pelo validador** (`_preparar_texto`, com limite de 1024 para `('whatsapp','zap')` quando é legenda). Sem isso, uma legenda levaria PIX ou "por fora" sem conferência.
- **Painel:** `_painel_externo` (`routers/atendimento_painel.py:84-101`) passa a devolver, para o Zap, `envio_foto={pode: True, legenda: 'opcional'}`. A tela decide o botão de foto e a legenda por esse bloco (`AtendimentoConversa.vue:2067-2124`), e hoje todo canal externo recebe `pode=False`.
- **O balão usa a nossa cópia**, porque o WhatsApp não devolve URL pública (`enviar.py:882-887`). `_aplicar_resultado` só grava URL que começa com `https://` (`:883`), então ganha a exceção da URL **relativa** `/api/atendimento/zap/midia/<id>` (a mesma regra de `gravar.py:376-377`, que já aceita `/…` e recusa `//…`).

---

## 7. IA e validador

**Validador** (`services/atendimento/validador.py:662-741`, que já recebe `plataforma` e `canal`).

Só quando `plataforma == 'whatsapp' and canal == 'zap'`:
- o bloco de contato fora da loja (`:682-695`: e-mail, WhatsApp/zap, link, telefone, @perfil, rede social) **não gera motivo**. Os `_apagar` de e-mail, WhatsApp e link continuam rodando, para limpar o `plano` antes das regras seguintes: um link com "pix" no caminho não pode virar falso PIX;
- **continuam**:
  - vazio, limite, lacuna;
  - pedido de avaliação e desestímulo de reclamação, porque a política do marketplace vale para o pedido venha a conversa por onde vier;
  - **PIX, conta bancária e "por fora"** (`:697-701`), pela P9;
  - **todas** as regras só da IA (`:725-739`, CDC).
- **entra uma regra só do Zap, para pessoa e IA** (`_DADO_SENSIVEL_ZAP`, no molde de `_DOCUMENTO_MAGALU`, `:707`), por causa da política do WhatsApp: *"Don't share or ask people to share full length individual payment card numbers, financial account numbers…"* (whatsappbusiness.com/policy). Ela barra:
  - número de cartão: 13 a 19 dígitos seguidos ou em grupos que passem no dígito verificador (Luhn);
  - agência e conta ("ag 1234 c/c 56789-0", "conta corrente…");
  - **pedido** de cartão, RG ou CPF ("me passa o número do cartão", "manda foto do RG", "qual o seu CPF").

  Hoje o `_DADO_PESSOAL` (`:725-737`) só roda para a IA, então uma pessoa poderia pedir cartão ou documento no Zap sem trava. Se o pedido de CPF para emitir a nota fiscal tem exceção na política está **[a confirmar]**. Até a resposta, a regra **barra** (falhar fechado), e a frase da tela explica: "No WhatsApp a Meta proíbe pedir número de cartão, conta ou documento."

Já existem dois precedentes de canal da própria marca com a régua de contato relaxada: `redes.preparar_texto` (`redes.py:1739-1753`) e `mail_atendimento/responder.preparar_texto` (`:593-611`). Os testes de `tests/test_atendimento_validador.py:84-100, 268, 484-485` continuam iguais para todos os outros canais.

**IA (fora até a Onda 7):**
- O cron só olha `PLATAFORMAS_CAIXA` (`ia.py:4416`), e `whatsapp` não está lá.
- `_sql_canal_sem_envio` (`ia.py:4614-4631`) corta todo `canal='zap'`.
- `ia._gerar` (`:3709`), o automático (`:4208`, `:4504`), a automática (`enviar.py:1753`) e o "Sugerir" (`routers/atendimento.py:2929/2940`) cortam por `motivo_canal_sem_envio` ou por `PLATAFORMAS_EXTERNAS`. A frase passa a ser a do Zap.
- A Caixa Humano exige `PLATAFORMAS_CAIXA` (`humano.py:334-335`), então o Zap fica fora.
- Nada disso muda até a Onda 7.

**Onda 7 (IA no Zap), se o Eduardo quiser:**
- entradas em `_PLATAFORMA_NOME`/`_CANAL_DESCRICAO` (`ia.py:2811-2820`): "WhatsApp da marca; o cliente já está no WhatsApp";
- uma variante de `_REGRAS_GERAIS` (`:2865`) **sem** a linha de contato (`:2874`), mantendo PIX, preço, prazo e dado pessoal, e **sem** o `_REGRAS_PLATAFORMA` do marketplace (`:2848`);
- link só para domínios próprios;
- `PROMPT_VERSAO='v8'` (`:322`);
- **exemplos filtrados pelo canal** (`:2042-2067`, `:2316/2391`): o Zap aprende só com o Zap e o marketplace nunca aprende com o Zap. Senão, uma resposta do Zap com link vira exemplo para a Shopee;
- modo `copiloto` em `MODOS_EXTERNOS`; `auto` nunca na v1;
- rever a política "AI Providers" da Meta.

**Direct do Instagram:** o `dm_ia` manda o cliente para o WhatsApp da marca (`dm_ia.py:83-86`, `marcas.sac_fone`). O número não muda com a migração, só passa a ser atendido pelo DaVinci. Não há nada a mexer, só conferir o `sac_fone` de cada marca.

---

## 8. Telas

### 8.1 Vocabulário e listas paralelas

São mais de 10 listas, e esquecer uma faz a tela mostrar "whatsapp" cru. Por isso há um teste `.cjs` que confere todas.

- `AtendimentoPlataforma.vue`:
  - `PLATAFORMAS_ATENDIMENTO` (`:626`): `{value:'whatsapp', nome:'WhatsApp', curto:'Zap', cor:'bg-[#25D366]', de:'do WhatsApp'}`;
  - `PLATAFORMAS_EXTERNAS` (`:640`); `MODOS_EXTERNOS` (`:651`): `['observar']` na Onda 1 e `['observar','humano']` na Onda 2;
  - `CANAL_LABEL` (`:799`): `zap → 'Zap'`; `LIMITE_CARACTERES` (`:865`): `'whatsapp:zap': 4096`;
  - `ERROS` (`:1774`) com os códigos de §6.3; `TIPO_LABEL` com áudio, localização, contato, figurinha e botão;
  - `Flags` (`:13`) com `zap_leitura_ativa` e `zap_envio_ativo`.
- **Ícone** (`AtendimentoIconePlataforma.vue`, `NOMES :19` e ramo próprio perto do `site`): SVG desenhado no arquivo, um círculo verde com balão (regra do arquivo: nada baixado). **Não** usar o `Zap` do lucide, que é um raio e já é usado em Automáticas.
- **Barra** (`AtendimentoLojas.vue:175`, `ORDEM`): entra `'whatsapp'`. Sai uma linha por número sozinha, porque o `/resumo` manda `externo_ref` e o laço (`:239-243`) só descarta linha sem ele. O `_juntar_externas` (`routers/atendimento.py:1551-1593`) já junta por `externo_ref`.
- **Filtro** (`AtendimentoFiltroPlataforma.vue:50`): grupo "WhatsApp". Dois testes que já existem mudam **junto**: `apps/web/tests/atendimento-filtro-plataforma.cjs:103` (a lista exata de `GRUPOS_CAIXA` ganha `'WhatsApp'`) e `:319` (hoje usa `'whatsapp'` como exemplo de "plataforma fora do menu", com `plataforma_topo === ''`; passa a esperar o corte do grupo WhatsApp, e o exemplo de "fora do menu" vira uma plataforma inexistente).
- **Faixa do topo** (`pages/atendimento.vue:228-240`): "Zap: envio desligado — responda pelo Duoke", só quando existe canal whatsapp.

### 8.2 A conversa do Zap (`AtendimentoConversa.vue`)

- **Selo** (`seloCanal`, `:1907`): "Zap".
- **`CANAIS_SEM_PEDIDO`** (`:1888`): entra `'zap'` até a Onda 6. Assim a tela não procura reclamação nem avaliação de pedido que não existe.
- **Painel do pedido, "ver no pedido" e gaveta.** A conversa esconde esses três por `canalExterno` (`:1895-1898`), que só vale para `carrinho` e `comentario`. Sem mudança, o Zap continuaria abrindo o painel do pedido (`:3316`), o botão "ver no pedido" (`:2721`), a gaveta (`:3313`) e os blocos de marketplace (`:2434`, `:2591`). Cria-se um `semPainelDePedido = computed(() => !!canalExterno.value || ehZap.value)`, usado **só** nesses pontos. **Não** se amplia o `canalExterno`, porque ele também esconde o compositor (`:3008`) e mostra "nada é mandado" (`:3078`), e no Zap a equipe responde.
- **Janela de 24 h** (`janela`, `:1108-1117`), num ramo próprio para `canal==='zap'`, que aparece **sempre**:
  - neutra com a janela aberta: "Janela do WhatsApp: dá para responder até 14:32 (faltam 3 h 10 min)";
  - **âmbar só nas últimas 2 h** (régua `VENCENDO` de `instagram.py:73`; a régua genérica de 48 h deixaria a faixa sempre âmbar);
  - vermelha quando fecha: "Passaram 24 h: o WhatsApp só aceita um modelo aprovado". Na Onda 5 aparece o botão "Usar um modelo".
  - Ela fica numa cadeia `v-else-if`; conferir que nenhum ramo anterior pega o Zap.
- **Compositor**: o cadeado com o motivo já existe (`bloqueioEnvio`, `:1238`; faixa `:3109`) e recebe os códigos novos.
- **Tiques no balão** (Onda 2): ✓ enviado · ✓✓ entregue · ✓✓ azul lido · ⚠ falhou com o motivo · "não sabemos se saiu" para `revisar`.
- **Mídia** (`pecasDe`, `:847-868`; regex `:861`):
  - Onda 1: o anexo `pendente` mostra "[Foto — aguardando download]";
  - Onda 3: aceita a URL **relativa** `/api/atendimento/zap/midia/…`, mais `<audio controls preload="none">`, `<video>`, link de documento e os estados "expirou" e "grande demais";
  - reação: um emoji pequeno sob o balão citado.

### 8.3 Aba 🟢 Zap

Já existe em `AtendimentoAbas.vue:24` (rótulo, cor e posição depois do E-mail, Q16). Na conversa do próprio Zap não há abas (`abas.py:830, 1102` devolvem a conversa sozinha para plataforma externa). A aba Zap **na conversa do pedido** só aparece com o vínculo da Onda 6.

### 8.4 Tela "Números do WhatsApp" (Onda 4)

Fica dentro de "Lojas e modo" (`AtendimentoCanais`, montado em `pages/atendimento.vue:844`), visível só para `isAdmin` (P15).

- **Componente:** `AtendimentoZapNumeros.vue`, com StatCards e tabela no padrão `AtendimentoCanais.vue:248-290`.
- **Colunas:**
  - Número (`fmtFone` de `lib/redesSociais.ts`), Nome de exibição (aprovado?), Marca;
  - Etapa (Teste → Aguardando Duoke → Migrando → No DaVinci → Pausado);
  - **Conexão** (pill: Conectado · Token vencido · Sem eventos · Assinatura falhando · Restrito);
  - Último recebido, Último erro (frase simples), Qualidade (verde/amarela/vermelha);
  - Respostas no mês ("N de 1.000 grátis");
  - **Envio** (chave com confirmação) e Modo.
- **Botões:** Testar conexão, Copiar URL do webhook, Mostrar código de verificação (fica no Histórico), Trocar token, Pausar envio, **Pausar todos**, Reprocessar ignorados. "Registrar número" e "Mostrar PIN" só aparecem nas etapas e casos certos (§8.5).
- **Teto do dia** por número (editável, com o volume medido no Duoke ao lado) e aviso em 70 %.
- **Sub-modal "Conectar"** (app_id + chave secreta; waba_id + token): copia o padrão de `pages/redes-sociais.vue:1195-1300` e `:2094-2175`.
  - O campo é mascarado com `WebkitTextSecurity`, `autocomplete=off` e `data-1p-ignore`.
  - O segredo vai só no corpo do POST, é zerado ao fechar e nunca é relido do servidor.
- **Não misturar** com o "Meta Verified" de Redes Sociais (`whatsapp_verificacao_status`): aquilo é o selo do aplicativo, não a conexão da API.

### 8.5 Rotas internas (`routers/atendimento_zap.py`, prefixo `/api/atendimento/zap`)

**Atenção:** o `_so_admin` do router do Atendimento (`routers/atendimento.py:294-309`), apesar do nome, é a trava de **acesso ao Atendimento**: deixa passar `pode_mexer` e as rotas de quem só lê. As rotas de credencial e de chaves precisam de uma dependência **a mais**, exigindo `acesso.pode_mexer(user)` **e** o papel admin, como o Configurar do e-mail.

| Método | Rota | Faz | Trava |
|---|---|---|---|
| GET | `/numeros` | Lista números, saúde, chaves e contador do mês. **Nunca devolve segredo** | `pode_ver` |
| POST | `/apps` | `{nome, app_id, app_secret}` → grava cifrado e gera o verify token | admin |
| GET | `/apps/{id}/webhook` | URL `{api_url}/api/webhooks/meta/whatsapp/{app_id}` (`config.py:17`, padrão `routers/settings.py:52-59`). O verify token sai **só** por `GET /apps/{id}/verify-token`, sob clique, rota que entra em `REVELACOES` ("viu o código de verificação do webhook do WhatsApp") | admin |
| POST | `/apps/{id}/segredo` · `/apps/{id}/verify-token` | Troca o segredo: `{novo, motivo: 'rotina'\|'vazou'}`. Na rotina o anterior vale 1 h; quando vazou, zero · gera outro verify token | admin |
| POST | `/contas` | `{app, waba_id, access_token}` → `zap_api.inspecionar_token` (estrita, §10.1), `GET /{waba}` e `GET /{waba}/phone_numbers`, grava a conta e devolve os números achados. Recusa o token se: `is_valid` falso, `app_id` diferente do app, faltar um dos escopos `whatsapp_business_messaging`/`whatsapp_business_management`, ou o `waba_id` não estiver nos `granular_scopes[].target_ids`. Falha na inspeção = erro, nada é gravado | admin |
| POST | `/contas/{id}/token` | **Trocar token**: grava o novo (mesmas conferências), testa com `GET /{waba}`, só então troca, e a tela lembra de **revogar o antigo** no painel da Meta | admin |
| POST | `/contas/{id}/inscrever` | `POST /{waba}/subscribed_apps` e, logo depois, `GET /{waba}/subscribed_apps` para conferir quais apps ficaram inscritos (alerta se houver outro app além do nosso, por exemplo o do Duoke). É escrita na Meta: confirmação na tela e, em produção, só com OK | admin |
| POST | `/numeros` | Cadastra o número escolhido: `garantir_canal` (nasce `observar`) e `envio_ligado=false` | admin |
| POST | `/numeros/{id}/testar` | `GET /{pnid}?fields=display_phone_number,verified_name,name_status,quality_rating,messaging_limit_tier,code_verification_status,throughput,webhook_configuration` [confirmar os campos]. O `webhook_configuration` mostra se há um **endereço alternativo** posto no número ou na WABA, que tem prioridade sobre o do app (por exemplo, um deixado pelo Duoke). Se `phone_number` ou `whatsapp_business_account` vier preenchido com algo que não é a nossa URL: alerta "as mensagens deste número estão indo para outro lugar". O mesmo campo entra no cron de saúde | admin |
| PATCH | `/numeros/{id}` | `envio_ligado`, `etapa`, `destinos_teste`, `teto_dia`, `marca_id`, `nome_na_barra` (com confirmação) | admin |
| POST | `/numeros/pausar-todos` | Botão de emergência | admin |
| POST | `/numeros/{id}/registrar` | `POST /{pnid}/register` com o PIN. **Só habilitada** com `etapa='migrando'` e caso **A** ou **B** marcado no número (no caso **A2** ela nunca aparece: o número já está registrado pelo Duoke e registrar com outro PIN só queimaria tentativas). **Uma tentativa por clique, nunca automática nem em retentativa.** Para com 3 tentativas em 72 h (`registro_tentativas`); a Meta bloqueia o número por 72 h na 10ª (133016). 131031 e 133016 travam o botão com "não tente de novo; fale com o suporte" | admin |
| GET | `/numeros/{id}/pin` | Mostra o PIN sob clique. Entra em `REVELACOES` ("viu o PIN do número do WhatsApp") | admin |
| POST | `/eventos/{id}/reprocessar` | Evento `numero_desconhecido`/`waba_desconhecida`/`numero_de_outra_waba`/`json_invalido`/`erro` | admin |
| POST | `/contatos/{id}/apagar` | Pedido de exclusão LGPD (§10.5). Em `SEM_CORPO` | admin |
| GET | `/midia/{id}` | §5.5 (Onda 3) | `pode_ver` + escopo |
| GET/POST | `/modelos`, `/modelos/sincronizar`, `/conversas/{id}/modelo` | Onda 5 | admin / pode_mexer |
| POST/DELETE | `/conversas/{id}/vinculo` | Onda 6 | pode_mexer |

O que já existe e não muda:
- `POST /conversas/{id}/responder` (`routers/atendimento.py:2869`);
- `PATCH /canais/{id}`, que já só deixa admin pôr canal externo em modo que envia (`:3686-3696`);
- `GET /conversas?plataforma=whatsapp&externo_ref=zap:…`.

---

## 9. PRÉ-VENDA e transferir para a venda

- **PRÉ-VENDA (Q20) sai de graça na Onda 1.** `etiqueta_fatos.e_pos_venda` (`:282-293`) dá PRÉ-VENDA a canal fora de `CANAIS_SEMPRE_POS_VENDA` sem `pedido_marketplace`.
- **Transferir para a venda (Onda 6):**
  - A tabela é `atendimento_zap_vinculos` (§4.3), com desfazer e histórico.
  - A busca é por nº do pedido, nome, CPF/CNPJ ou telefone. O espelho do Bling **não guarda telefone**, então a sugestão por telefone acerta pouco (levantamento §3.14).
  - Ao vincular, grava `pedido_marketplace` na conversa do Zap. Ela vira PÓS-VENDA pelo recálculo de `gravar.upsert_conversa` (`:640-661`), e a próxima mensagem não apaga, porque None não sobrescreve.
  - **Nunca** trocar o `integration_id` ou o `canal_id` da conversa: a mensagem seguinte procuraria pelo canal e criaria uma conversa em dobro (`gravar._buscar_conversa :503-530`).
  - **Abas:** exceção pelo vínculo (e não por "mesma loja") em `abas.familia` (`:828-831`), `abas_da_conversa` (`:1099-1102`), `_candidatas` (`:630`) e `_membro` (`:780`). A aba Zap da conversa do pedido responde pela `AtendimentoAbaResposta.vue:25`, com o mesmo POST.
  - **Escopo da equipe:** vale pelo `integration_id` do vínculo (`_no_escopo`/`_clausula_escopo`, `routers/atendimento.py:578-590`) e, sem vínculo, pela marca do número.
  - **Cartão Cliente** (`cliente.py:796-799`): junta pelo mesmo `bsuid`/`wa_id_norm` e pelo pedido vinculado.
  - **Desfazer** limpa o pedido e fica no Histórico.

---

## 10. Credenciais e segurança

### 10.1 Mais de uma conta Meta (multi-WABA)

- A hierarquia é `apps` (app_id, app secret, verify token) → `contas` (WABA + token do System User) → `numeros`. Suporta **mais de um app** e mais de uma WABA por app.
- **Dois apps, dois mundos (P4).** O "DaVinci Zap Teste" só tem a WABA de teste, e só ele pode ter credencial no banco do Mac. O "DaVinci Zap" é o de produção, e a chave secreta e o token dele **nunca** são gravados no banco local: são digitados só na tela do DaVinci de produção. Com a chave secreta de produção no Mac, quem tivesse o banco local forjaria webhooks de produção, e o `.env` do worktree tem `DEV_MOCK_LOGIN=true`, que dá admin sem senha (`routers/dev.py:28-35`).
- O Instagram (`meta_app_secret`/`instagram_app_secret`, `config.py:412-418`) **não é tocado**. Ele pode depender do fallback de hoje.
- **Token só de usuário do sistema funcionário** (*employee*), com acesso **só** às WABAs atribuídas e validade "Nunca". O usuário administrador tem acesso a todas as WABAs do portfólio por padrão, então um token dele vazado valeria para todo número real que entrasse depois. O token do painel vence rápido e é recusado.
- **Conferência estrita do token** (`zap_api.inspecionar_token`, função nova):
  - `GET /debug_token` devolve `is_valid`, `app_id`, `scopes`, `granular_scopes` e `expires_at`;
  - exige `is_valid`, `app_id == app.app_id`, os dois escopos e o `waba_id` dentro de `granular_scopes[].target_ids`;
  - `expires_at = 0` grava `token_nunca_vence=true`;
  - falha na inspeção (a Meta não respondeu, o app não pode inspecionar) é **erro**: no cadastro, nada é gravado; no cron diário, vira alerta, nunca "sem validade". Na função antiga, um token do painel que falhasse na inspeção passaria por "nunca vence" e o alerta de 7 dias nunca dispararia;
  - o `debug_token` exige o token em `?input_token=` na URL [confirmar se aceita no corpo]. Por isso o `zap_api` mascara também `input_token=` (o `_RE_ACCESS_TOKEN_QS`, `meta_client.py:173`, só pega `access_token=`) e nunca loga a URL dessa chamada.
- Roda na hora de gravar e num cron diário. Faltando 7 dias para vencer, já gera alerta.
- **Troca do token** (rota `POST /contas/{id}/token`): gerar o novo no painel, gravar, testar e só então **revogar o antigo** no painel. Rotação **semestral** (lembrete pelo `token_trocado_em`) e **imediata** se houver suspeita de vazamento.
- O segredo é decifrado **só na memória do processo**, nunca no Redis. O cache de 60 s vale só para o segredo do app no webhook e é limpo na troca.
- Cifra: `encrypt_json`/`decrypt_json` (`security/cipher.py:37/49`), AES-GCM sobre a `CREDENTIALS_KEY` (`config.py:56`).

### 10.2 Webhook

- Rota e segredo **próprios por app**. Assinatura dura: HMAC do corpo cru, comparação em bytes. 403 com contador próprio, **só no Redis**. Freio por IP só para quem errou a assinatura, e alarme por proporção (§5.1).
- Corpo lido em streaming com teto de 4 MB, cache negativo para `app_id` desconhecido e resposta só no host principal (§5.1).
- 200 **só depois do commit**. Nada é descartado em silêncio (o oposto de `webhooks.py:836-837`); até o JSON inválido vira evento `ignorado`.
- O verify token vai na query do GET e aparece no access log, porque nem o Caddy nem o uvicorn mascaram. Os dois hosts do Caddy logam, e o `gestaoestoque.com` não tem filtro nenhum (`Caddyfile:52-71`); a rota só responde no host principal. O verify token é aleatório por app e pode ser trocado na tela. Mascarar no Caddy é opcional e exige OK, porque mexe em produção.
- **App em modo "Ao vivo" (Live).** A Meta avisa: *"Make sure your app is in Live mode; some webhooks will not be sent if your app is in Dev mode"* (webhooks/overview, consultado em 17/10). O app de produção é publicado antes da Onda 4b. Isso exige a URL da política de privacidade e a de instruções de exclusão de dados (§10.5).
- **Inscrição conferida por API.** Depois de cada `POST /{waba}/subscribed_apps`, um `GET /{waba}/subscribed_apps` confere quais apps estão inscritos. Mais de um app pode estar inscrito na mesma WABA, e *"Meta sends retries to all apps that have subscribed"*. Se o app do Duoke continuar inscrito, ele continua recebendo as mensagens dos clientes.
- **Campos assinados no painel** (§13.1): `messages`, `user_preferences`, `account_update`, `phone_number_quality_update`, `phone_number_name_update`, `message_template_status_update`, `message_template_quality_update` e `account_alerts`. Só com `messages` não chegariam os alarmes de qualidade e restrição, o status dos modelos e o opt-out que o §10.5 promete respeitar.
- **Cloudflare:** o Caddyfile fala em compatibilidade com o proxy da Cloudflare e `docs/deploy-atendimento.md:249` diz que não há Cloudflare na frente. Conferir antes de cadastrar a URL na Meta, porque um WAF poderia barrar o POST.

### 10.3 Logs e dados

- No log nunca vai texto, telefone ou token; só ids e o tipo do erro. O `_redigir` vale em todo erro HTTP, e o token do `ZapDestino` tem `repr=False`.
- O Histórico mascara `app_secret`, `token` e `pin` no corpo (`historico/mascara.py:26-41`), mas o **gatilho** do banco grava a linha inteira de toda tabela nova. Por isso contatos, eventos e mídias ficam sem gatilho, as rotas `zap/(conversas|contatos)` ficam em `SEM_CORPO` e os GETs que mostram segredo ficam em `REVELACOES` (§4.3). Há testes obrigatórios para as três coisas.

### 10.4 Mídia

- Lista de hosts antes do Bearer, **conferida a cada redirecionamento** (`follow_redirects=False`).
- Tipo pela **assinatura do arquivo**, não pelo que o remetente declarou; lista de MIME; nunca SVG/HTML.
- `nosniff`, `Content-Security-Policy: sandbox`, `attachment` para documento.
- Teto de tamanho e caminho gerado pelo servidor (sem path traversal).
- Antivírus **[VETÁVEL]**, para depois.
- Arquivo **sem cifra** no disco: a retenção e o backup são decisão explícita (pergunta 14.2 nº 2).

### 10.5 LGPD

- **Base legal:** o atendimento se apoia em **execução de contrato ou de procedimentos preliminares a pedido do titular** (LGPD art. 7º, V: o cliente pergunta sobre uma compra ou quer comprar) e, no que sobrar, em **legítimo interesse** (art. 7º, IX). **Não** em consentimento, que pode ser revogado a qualquer momento e exige prova. **Mensagem que a loja inicia** (modelo) exige o opt-in que a política do WhatsApp pede (§6.6). Marketing não existe na v1. "PARAR", o pedido confirmado por pessoa e `user_preferences=stop` são respeitados para **todo** modelo.
- **Política de privacidade publicada** (item do checklist §13.1). A política do WhatsApp exige *"maintaining a published privacy policy"*, e a URL é pré-requisito para pôr o app em modo Live (§10.2). Ela cita a Meta como operadora (a mensagem passa pelos servidores dela) e diz por quanto tempo o DaVinci guarda mensagens e mídias. O app também precisa da URL de **instruções de exclusão de dados**. O texto é com o Eduardo e o jurídico; o desenho só exige que exista.
- **Dados de terceiros:** o evento guarda só o `contacts[]` do remetente (§4.2). vCard (contato compartilhado) e localização são conteúdo que o cliente mandou: ficam na mensagem e entram na exclusão.
- **Retenção:** eventos por 30 dias; mídia por 180 dias (**[VETÁVEL]**). O cron `atendimento_zap_limpeza` apaga o arquivo e marca `apagada`, e o balão diz "mídia apagada pela política de retenção".
- **Backup:** conferir se `/data/uploads` entra no backup do servidor. O arquivo está sem cifra no disco.
- **Pedido de exclusão** (Onda 4), rota de admin `POST /contatos/{id}/apagar`, que fica em `SEM_CORPO`. Ela varre **todos** os lugares:
  - o contato: `wa_id`, `wa_id_norm`, `bsuid` e `nome_perfil` anonimizados, `apagado_em`;
  - a conversa: `comprador_id`, `comprador_nome` e `dados.zap` limpos;
  - as mensagens: texto trocado por "[apagado a pedido]" e `payload.zap` (referral, localização, vCard, contexto) limpo;
  - as mídias: arquivo apagado e `nome_original` limpo;
  - o `payload` dos eventos daquele contato (até 30 dias);
  - o Histórico: as três tabelas do Zap já não têm gatilho (§4.3), então a exclusão não cria cópia. A ação em si fica no Histórico, sem corpo ("apagou os dados de um contato do Zap", com o id).

  O que já está em **backup** sai sozinho quando o backup antigo expira, e o prazo do backup entra na política de privacidade.

### 10.6 Túnel no localhost (só com OK)

- Exporia a API local inteira, inclusive `/api/dev/mock-login` (`routers/dev.py:28-35`; o `DEV_MOCK_LOGIN` está no `.env` do worktree), que dá admin sem senha.
- Se um dia houver túnel:
  - um Caddy local que só deixe passar `/api/webhooks/meta/whatsapp/*`;
  - `DEV_MOCK_LOGIN=false`;
  - desligar o túnel no fim.
- A alternativa preferida é o ensaio em produção com o número de teste (Onda 4b).

---

## 11. Teste em localhost com o número de teste + testes automatizados

### 11.1 Ambiente

- Colima `davinci-nfse` + `~/nf-executor/local_atend/dca.sh` (projeto `davinci-atend`, Postgres e Redis com `--profile local`). **Hoje está parado.**
- A API não recarrega sozinha nesse colima: reiniciar a api depois de editar.
- Testes por `~/nf-executor/local_atend/atend_api.sh`, que roda pytest e ruff numa cópia em `/tmp`, dentro do container, com `DAVINCI_TEST_SCHEMA=davinci_test_atend`. Ele usa `docker exec -i` com o stdin ocupado pelo `tar` do código: **não serve** para comando que pede digitação.
- O `scripts/atendimento_local.py` **não serve neste Mac** (sem uv, sem Postgres darwin na 5433).
- **Worker:** o `docker-compose.atend-local.yml` só tem api, postgres e redis. Subir o `worker` do compose ligaria **todos** os crons do `WorkerSettings` contra o banco local. Por isso:
  - Ondas 1 e 2: **sem worker**. O `simular --processar` chama `zap_entrada.processar_eventos` no mesmo processo (§11.4);
  - Onda 3 (baixador de mídia): uma `WorkerSettingsZap` mínima, só com os jobs e o cron do Zap (o molde é a `WorkerSettingsUI`, `worker.py:5551`). Rodá-la exige pôr o worker no `docker-compose.atend-local.yml` com o **mesmo volume da api** (`atend_uploads:/data/uploads`). Hoje o worker do `docker-compose.override.yml:17-20` monta `./data/uploads`, e a mídia que ele baixasse não apareceria para a api. Esse arquivo é do ambiente local e não vai para o git.
- **Antes do 1º cadastro com chave real (Onda 2):** conferir, sem mostrar os valores, que a `CREDENTIALS_KEY` do `.env` local **não** é a padrão `dev-credentials-key-change-me` (`config.py:56`) **nem** igual à de produção. O `DEV_MOCK_LOGIN=true` do worktree fica, porque é só localhost sem túnel (§10.6).

### 11.2 `.env` do worktree, só durante o teste

```
ATENDIMENTO_LEITURA_ATIVA=true
ATENDIMENTO_ZAP_LEITURA_ATIVA=true
# só a partir da Onda 2, no ensaio de envio:
ATENDIMENTO_ENVIO_ATIVO=true
ATENDIMENTO_ZAP_ENVIO_ATIVO=true
ATENDIMENTO_SIMULADOR=true
ATENDIMENTO_SIMULADOR_EXCETO=whatsapp
ATENDIMENTO_ZAP_DESTINOS_TESTE=<wa_id do celular do Eduardo>
```

Depois de mexer, recriar a api (`lru_cache`; o worker local só existe a partir da Onda 3). Com isso o marketplace finge e só o Zap sai, e só para o celular dele.

### 11.3 Cadastro local (`scripts/atendimento_zap_local.py cadastrar`)

- Recusa `ENV != development` e banco que não seja o local, como `_seed_local_atend.py`.
- **Onda 1: `cadastrar --ficticio`, sem nenhuma chave de verdade.** Grava um app, uma WABA e um número inventados (app_id, segredo e `phone_number_id` gerados na hora, `teste=true`, `etapa='teste'`, canal em `observar`). O `simular` assina com esse segredo falso, e a Meta nunca chama o localhost. Na Onda 1 o computador não precisa de chave real nenhuma.
- **Onda 2 em diante: `cadastrar`, só com o app "DaVinci Zap Teste".** Pede APP_ID, chave secreta, TOKEN e WABA_ID por **`getpass`**. Quem digita é o Eduardo, no terminal dele. **Nunca no chat, nunca no `.env`.**
  - O `getpass` precisa de terminal. O comando é, de fora do container:
    ```
    DOCKER_CONTEXT=colima-davinci-nfse docker exec -it -w /app davinci-atend-api-1 \
        uv run --offline python scripts/atendimento_zap_local.py cadastrar
    ```
    (o `atend_api.sh` não serve: usa `-i` com o stdin ocupado pelo código; o precedente `_seed_local_atend.py` roda com `docker exec` sem `-t`).
  - Valida o token (`inspecionar_token`), descobre os números (`GET /{waba}/phone_numbers`) e grava tudo cifrado no banco local, com `apps.teste=true`, o número `teste=true`, `etapa='teste'` e o canal em `observar`.
  - **Recusa** o cadastro se a WABA tiver algum número que não seja o de teste. É a trava para a credencial de produção nunca entrar no Mac.
- Na Onda 2, o mesmo script passa o canal para `humano` e liga `envio_ligado`, com confirmação.
- **Ao fim de cada rodada de testes no computador** (fim da Onda 2 e fim da Onda 3), o Eduardo **revoga o token de teste** no painel da Meta. Na rodada seguinte, gera outro.

### 11.4 Receber sem túnel (padrão, risco zero)

- `atendimento_zap_local.py simular --tipo texto|imagem|audio|documento|localizacao|botao|reacao|status|preferencia --de <wa_id> --texto "oi" [--processar]`.
- Ele monta o evento a partir de `tests/fixtures/whatsapp/*.json`, com o `phone_number_id` do número de teste, um wamid único e o timestamp de agora.
- Assina com o segredo do banco local (nunca impresso) e faz POST em `http://localhost:8000/api/webhooks/meta/whatsapp/<app_id>`. É o mesmo princípio de `tests/test_webhook_dm_instagram.py:48-113`.
- Com `--processar`, depois do POST chama `zap_entrada.processar_eventos` **no mesmo processo** (as chaves `ATENDIMENTO_LEITURA_ATIVA` e `ATENDIMENTO_ZAP_LEITURA_ATIVA` do `.env` local continuam valendo). Assim a mensagem aparece na tela sem worker.
- **Mídia de verdade sem túnel** (Onda 3): `subir-midia` faz `POST /{pnid}/media` com o nosso token e recebe um `media_id` real; o `simular` usa esse id. Assim o baixador faz o `GET /{media-id}` de verdade.
- **Status de verdade**: depois de um envio real, simula `sent/delivered/read/failed` com o wamid que a Meta devolveu.

### 11.5 Enviar de verdade a partir do localhost (Onda 2)

A saída para `graph.facebook.com` não precisa de túnel.

1. O Eduardo manda "oi" do celular para o número de teste. Isso abre a janela **do lado da Meta**.
2. O script simula esse mesmo "oi" no local, o que abre a janela **do nosso lado**.
3. A resposta pela tela chega ao celular dele, e os status simulados mostram os tiques.

Observações:
- Sem o passo 1, a Meta devolve 131047.
- O número de teste só entrega para os destinatários cadastrados no painel (até 5, [não confirmado], erro 131030).
- O `to` usa o `wa_id` como veio. Atenção ao 9º dígito [confirmar com um payload real]. A lista de teste compara pelo número normalizado, então o celular do Eduardo entra com ou sem o 9.

### 11.6 Testes automatizados

O pytest usa Postgres e Redis reais (`tests/conftest.py`), o client ASGI e `respx` no `graph.facebook.com`, **sem chamada real de rede**. Os payloads ficam em `tests/fixtures/whatsapp/`:
- mensagens: text, image, audio voice, video, document, sticker, location, contacts, interactive button/list, button, reaction, unsupported, system, só BSUID, CTWA;
- statuses: sent/delivered/read/played/failed com 131047, e o mesmo wamid com `pricing` em `sent` e em `delivered`;
- `value.errors`, account_update (duas vezes iguais, com `entry.time` diferente), user_preferences (stop/resume), phone_number_quality_update, message_template_status_update e objeto inesperado.

| Arquivo | O que prova | Onda |
|---|---|---|
| `tests/test_atendimento_zap_webhook.py` (molde `test_webhook_dm_instagram.py:35-116`) | Handshake ok / token errado / byte alto (bytes, não 500) / `text/plain`; app desconhecido 404 (e o cache negativo não consulta o banco de novo); host que não é o principal 404; segredo de outro app 403 + contador `meta_wa` **só no Redis** (nada gravado no banco); IP com muitas falhas leva 429 sem calcular HMAC; segredo anterior aceito só na janela de 1 h da troca de rotina e recusado na troca "vazou"; corpo acima do teto 413 **lido em streaming, sem `Content-Length`**; JSON inválido 200 **e gravado como `ignorado`/`json_invalido`**; **evento gravado antes do 200**; banco fora 500; enfileirar falhando ainda dá 200 e o `varrer` pega; 50 POSTs seguidos = 1 job enfileirado (coalescido); mesmo corpo duas vezes = mesmos eventos; `account_update` igual com `entry.time` diferente = 2 eventos; lote com mais de 200 itens **todos gravados**; `payload` só com o contato do remetente; `user_preferences` vira `preferencia`; objeto inesperado gravado como `ignorado`; **Instagram intacto** | 1 |
| `tests/test_atendimento_zap_entrada.py` | Texto vira canal `zap:<pnid>` em observar + conversa `whatsapp/zap` + mensagem; **evento novo com `proxima_em` NULL é processado**; **um commit por evento** (com o processador parado no meio de um evento, outra sessão trava a conversa já gravada em menos de 5 s); `IntegrityError` forçado vai para backoff com `tentativas+1`, **nunca** `repetido`; o canal passa a `status='ok'` com `ultimo_ok_em` (e a barra não diz "Ainda não foi lida"); PRÉ-VENDA; `pode_enviar_ate = ts(s)+24h`; reentrega idempotente; fora de ordem (a janela usa o maior ts e a mensagem atrasada não reabre "aguardando"); 2 mensagens simultâneas de cliente novo = 1 conversa; contato só BSUID; wa_id que depois ganha BSUID continua o mesmo contato; **número reciclado** (mesmo telefone, BSUID diferente = contato novo, antigo solto); 9º dígito só em celular; `user_changed_number`; `user_preferences=stop` grava `opt_out_em`; "PARAR" grava, "quero parar de receber" só põe a nota para a pessoa confirmar; reação não entra na fila; unsupported; botão e lista; CTWA; `value.errors`; número desconhecido fica reprocessável; WABA de outro app recusada; `phone_number_id` de outra WABA recusado; chave desligada deixa o evento pendente; mídia registrada `pendente` | 1 |
| `tests/test_atendimento_zap_canais.py` | `ref_do_zap`/`partes`/`garantir_canal`/`nome_do_canal`; filtro `?externo_ref=zap:` não dá 422; `/resumo` com uma linha por número; "Sugerir" com a frase do Zap | 1 |
| Padrões das chaves e worker | Todas as `atendimento_zap_*` nascem False ou vazias (molde `test_atendimento_redes.py:809-810`); `meta_whatsapp_graph_version` separada da do Instagram; job e cron registrados e saindo cedo (molde `test_atendimento_comunicador.py:84-90`) | 1 |
| `tests/test_atendimento_migration.py` | `0415` sobe e desce igual ao model (`_MIGRATION_ZAP`); contagem 18 → 24; stub `marcas` no rascunho | 1 |
| `tests/test_historico_regras.py` (casos novos) | `atendimento_zap_contatos`, `_eventos` e `_midias` **sem** gatilho; `apps`, `contas` e `numeros` **com**; `SEM_CORPO` pega `/api/atendimento/zap/conversas/…` e `/contatos/…` | 1 |
| `tests/test_atendimento_zap_envio.py` | **Cada trava de §6.3, na ordem**; fail-closed em dev sem lista de teste; lista de teste casa com e sem o 9º dígito; "pausar todos" barra na hora; sucesso grava o wamid em `externo_id` e o `message_status` em `payload.envio.zap`, e o corpo tem `biz_opaque_callback_data`; o token **só no header** (nunca na URL nem no log; a única exceção é o `input_token` do `debug_token`, sempre mascarado); 131047 fecha a janela e a trava 12 passa a recusar na hora; 190 marca o token inválido; 368 pausa o número **e o efeito sobrevive** a um rollback de `_gravar_resultado`; timeout = `revisar` sem retentativa; simulador com a exceção `whatsapp`; simulador em produção recusa; laço (9 envios ao mesmo contato em 10 min) recusa; teto do número; Zap com plataforma shopee continua `canal_sem_envio` (`test_atendimento_enviar.py:1620-1780` sem mudança); o falso de 4 parâmetros de `test_atendimento_enviar.py:1324-1329` continua passando; a extração `_travas_do_sistema` não muda nada nos testes atuais | 2 |
| `tests/test_atendimento_zap_status.py` | sent → delivered → read; read sem delivered; nada regride; failed vira `falhou` com frase; status antes do wamid fica pendente e é aplicado depois; linha `enviando` intocada; `biz_opaque` concilia `revisar`; pricing contado **uma vez por wamid**, pelo `pricing.type`; 368 vindo **só** do webhook não pausa na 1ª vez (só alerta) e pausa na 2ª | 2 |
| `tests/test_atendimento_validador.py` (casos novos) | `(whatsapp, zap)` aceita WhatsApp, link, telefone e e-mail; PIX, banco, "por fora", avaliação e lacuna continuam barrados; **cartão (Luhn), agência/conta e pedido de cartão/RG/CPF barrados para pessoa e para IA, só no Zap**; regras só da IA intactas; link com "pix" não vira falso PIX; marketplace igual | 2 |
| `tests/test_atendimento_zap_midia.py` | Download com Bearer; host fora da lista **não recebe o Bearer**, nem depois de um redirecionamento; 404 pede a URL de novo; hash divergente; grande demais; MIME proibido; arquivo que diz ser JPEG mas é HTML recusado; expira depois de 7 d; rota exige login e escopo e manda `nosniff` e `CSP: sandbox`; documento como attachment; sem path traversal; retenção apaga; foto de saída sem `cliente_indisponivel`; legenda opcional passa pelo validador; URL relativa gravada no anexo; `envio_foto.pode=True` no painel do Zap | 3 |
| `tests/test_atendimento_zap_cadastro.py` | Só admin (quem só lê e quem mexe sem ser admin levam 403); GET nunca devolve segredo; em repouso fica cifrado; Histórico sem app_secret, token nem PIN; ver o verify token e ver o PIN ficam no Histórico (`REVELACOES`); `inspecionar_token` recusa: sem escopo, de outro app, sem a WABA nos `granular_scopes`, inspeção que falha (nada gravado); `expires_at=0` = nunca vence; "registrar" escondido no A2, fora de `migrando` e depois de 3 tentativas; 131031/133016 travam o botão; `testar` alerta com `webhook_configuration` apontando para outro lugar; `inscrever` alerta com outro app em `subscribed_apps`; exclusão LGPD limpa contato, conversa, mensagens, mídias e eventos, e não deixa cópia no Histórico; descoberta de números com respx | 4 |
| Web `apps/web/tests/atendimento-zap.cjs` (molde `atendimento-direct.cjs`) | `whatsapp` em **todas** as listas paralelas; ícone; linha por número na barra; janela âmbar só nas últimas 2 h; códigos novos em `ERROS`; painel do pedido, "ver no pedido" e gaveta escondidos no Zap, com o compositor visível; URL relativa e áudio em `pecasDe`. `atendimento-abas.cjs:223` continua passando, porque `MOTIVO_ZAP_SEM_ENVIO` não muda | 1-3 |
| Web `apps/web/tests/atendimento-filtro-plataforma.cjs` (**atualizado**) | `:103` com `'WhatsApp'` em `GRUPOS_CAIXA`; `:319` com outra plataforma como exemplo de "fora do menu" | 1 |

O ruff roda **só nos arquivos tocados**, sem formatar arquivo inteiro (a base já tem erros antigos).

---

## 12. Ondas de entrega

Todas podem ser publicadas sozinhas, com as chaves desligadas, e **só com o OK do Eduardo**. Publicar é sempre do jeito de sempre: `git pull` mais o compose com os **dois `-f`** (`docker-compose.yml` + `docker-compose.prod.yml`).

### Onda 0 — Preparar (P)

- [ ] Rebase de `feat/atendimento-zap` no `origin/main`; conferir o head das migrations (hoje `0414`) e as linhas citadas.
- [ ] Ligar o colima `davinci-nfse` e o `dca.sh`; rodar `atend_api.sh` com a suíte do atendimento para ter a linha de base verde.
- [ ] **A Onda 1 não precisa de nada da Meta** (cadastro fictício, §11.3). Os passos da Meta (§13.1) só são necessários antes da Onda 2.

### Onda 1 — Receber (só leitura) — **implementável já** (M)

**Criar:**
- [ ] `apps/api/app/models/atendimento_zap.py`: `AtendimentoZapApp`, `AtendimentoZapConta`, `AtendimentoZapNumero`, `AtendimentoZapContato`, `AtendimentoZapEvento` e `AtendimentoZapMidia` (§4.2), exportados em `app/models/__init__.py`.
- [ ] `apps/api/alembic/versions/0415_atendimento_zap.py` (as 6 tabelas, índices parciais, `lock_timeout`).
- [ ] `apps/api/app/routers/whatsapp_webhook.py`: GET e POST (§5.1), com host principal, corpo em streaming com teto, cache negativo, freio por IP nos 403, falha de assinatura só no Redis, JSON inválido gravado e enfileiramento coalescido.
- [ ] `apps/api/app/services/atendimento/zap_contas.py`: `cifrar_segredo_app`, `segredos_do_app(app_id)` com cache de 60 s (e janela de 1 h do anterior), `numero_por_pnid`, `token_da_conta` e `descobrir_numeros` (só GET).
- [ ] `apps/api/app/services/atendimento/zap_api.py`: só os GETs que o cadastro usa: `inspecionar_token` (estrita, §10.1), `GET /{waba}`, `GET /{waba}/phone_numbers`; `_erro_zap` próprio; `meta_whatsapp_graph_version`.
- [ ] `apps/api/app/services/atendimento/zap_entrada.py`: `processar_eventos(session, tempo_max_s=…)` **com um commit por evento** e o predicado `proxima_em IS NULL OR proxima_em <= now()` (§5.2), `_contato` (com a regra do número reciclado), `_mensagem` (tabela §5.4), `_status` (só aplica quando acha; o resto fica pendente), `_preferencia` (`user_preferences`), `_conta_ou_erro` e a gravação de `canal.status`/`ultimo_ok_em`.
- [ ] `apps/api/scripts/atendimento_zap_local.py`: `cadastrar --ficticio` e `simular` (com `--processar`) (§11.3-11.4).
- [ ] `apps/api/tests/fixtures/whatsapp/*.json`.
- [ ] Testes: `test_atendimento_zap_webhook.py`, `test_atendimento_zap_entrada.py` e `test_atendimento_zap_canais.py`.
- [ ] Web: `apps/web/tests/atendimento-zap.cjs`.

**Alterar:**
- [ ] `app/historico/sql.py` (`EXCLUIDAS`): `zap_(contatos|eventos|midias)` no grupo `atendimento_(…)`; `app/historico/nomes.py` (`SEM_CORPO`): `atendimento/zap/(conversas|contatos)/`; `tests/test_historico_regras.py`: as três tabelas na lista "not in com" (§4.3).
- [ ] `app/routers/webhooks.py:686-707`: extrair `assinatura_meta_ok(body, header, segredos)`; `_verify_meta_signature` passa a chamá-la (o comportamento do Instagram não muda).
- [ ] `app/main.py:66, 235`: registrar o router novo.
- [ ] `app/config.py` (~`:517`): as chaves de §6.1, inclusive `meta_whatsapp_graph_version`.
- [ ] `app/services/atendimento/constantes.py`:
  - `PLATAFORMA_WHATSAPP` em `PLATAFORMAS_EXTERNAS` (`:61`);
  - `CANAIS_EXTERNOS` (`:187`); `MODOS_EXTERNOS['whatsapp']=('observar',)` (`:242`);
  - `SLA_HORAS` e `LIMITE_CARACTERES` (`:271`, `:315`); `ROTULO_TIPO_PLATAFORMA` (`:643`);
  - `motivo_canal_sem_envio` **não muda**.
- [ ] `app/services/atendimento/canais_externos.py`: `PREFIXO_ZAP`, `ref_do_zap`, `partes`, `nome_do_canal` e `descricao_da_origem`.
- [ ] `app/routers/atendimento.py:2929`: frase do Zap no "Sugerir"; `:1500-1539`: texto do Zap em `_motivo_do_status`; `:1886`: flags `zap_*` no `/resumo` (com `FlagsOut`, `schemas/atendimento.py:901`).
- [ ] `app/routers/atendimento_painel.py:84-96`: `_painel_externo` com o texto do Zap.
- [ ] `app/worker.py`: `atendimento_zap_processar` (job, `functions` ~`:4925`) e `atendimento_zap_varrer` (cron de 1 min num segundo livre, `cron_jobs` ~`:5310`), os dois saindo cedo com as chaves desligadas.
- [ ] `tests/test_atendimento_migration.py:82-91` (+ carregamento ~`:213`, contagem `:220` de 18 para 24, stub `marcas` em `:240-251`) e `tests/conftest.py:426` (`_CLEANUP_TABLES`, ordem de §4.2).
- [ ] Web:
  - `AtendimentoPlataforma.vue` (vocabulário, `Flags`, `TIPO_LABEL`);
  - `AtendimentoIconePlataforma.vue` (ícone);
  - `AtendimentoLojas.vue:175`; `AtendimentoFiltroPlataforma.vue:50`, **com** `tests/atendimento-filtro-plataforma.cjs:103` e `:319` atualizados;
  - `AtendimentoConversa.vue` (selo `:1907`, `CANAIS_SEM_PEDIDO :1888`, `semPainelDePedido` em `:2434, 2591, 2721, 3313-3320`, janela `:1108`, anexo pendente em `pecasDe`);
  - `pages/atendimento.vue:228-240` (aviso).

**Pronto quando:**
- os testes da Onda 1 e a suíte inteira do atendimento (pytest **e** os `.cjs` da web) estão verdes;
- no localhost, **sem nenhuma chave real**, um "oi" simulado com `--processar` (e foto, áudio, botão) aparece na barra "WhatsApp", na linha do número fictício, como PRÉ-VENDA, com a faixa da janela e com a loja "ok" (não "Ainda não foi lida");
- o painel do pedido não abre na conversa do Zap;
- o compositor diz "Responda pelo Duoke".

### Onda 2 — Responder texto (M)

**Criar:**
- [ ] `services/atendimento/zap.py` (`enviar_texto`).
- [ ] `services/atendimento/zap_erros.py`.
- [ ] `test_atendimento_zap_envio.py` e `test_atendimento_zap_status.py`.

**Alterar:**
- [ ] `constantes.py`: `MOTIVO_ZAP_SO_PESSOA` e o ramo `zap + whatsapp` em `motivo_canal_sem_envio`; `MODOS_EXTERNOS['whatsapp']=('observar','humano')`.
- [ ] `enviar.py`: `_Destino` (token com `repr=False`), desvio em `_destino`, `_destino_zap` (§6.3, janela com o `min`, laço e teto do número), `_travas_do_sistema`, parâmetros nomeados em `_chamar_plataforma` **passados só quando `destino.zap` existe**, e os efeitos do erro aplicados depois de `_gravar_resultado` (§6.2).
- [ ] `zap_entrada._status`: conciliação por `biz_opaque` e rank, custo contado uma vez por wamid, efeito forte do webhook só com 2 ocorrências (§5.6).
- [ ] `zap_api.py`: `POST /{pnid}/messages`.
- [ ] `validador.py:679-701`: exceção `(whatsapp, zap)` e a regra `_DADO_SENSIVEL_ZAP` (cartão, conta, documento; pessoa e IA), com casos novos em `test_atendimento_validador.py`.
- [ ] `schemas/atendimento.py:296-322`: `entrega`/`entrega_erro` (de `payload.zap`) e `retida_pela_meta` (de `payload.envio.zap`).
- [ ] Web: `ERROS`, tiques no balão, `MODOS_EXTERNOS`.
- [ ] Script: `cadastrar` com o app **"DaVinci Zap Teste"** (`docker exec -it`, §11.3) e liga o envio do número de teste (canal `humano`, `envio_ligado`), com confirmação.

**Pronto quando:** o ensaio de §11.5 funciona e a resposta pela tela chega **só** ao celular do Eduardo. No fim, o Eduardo revoga o token de teste.

### Onda 3 — Mídia (M)

- [ ] Criar `services/atendimento/zap_midia.py` (baixador, §5.5: `follow_redirects=False`, tipo pela assinatura) e o job `atendimento_zap_baixar_midia`; o `varrer` passa a cuidar das mídias pendentes.
- [ ] Criar `routers/atendimento_zap.py` com só a rota `GET /midia/{id}` (com `CSP: sandbox`) e **registrá-lo em `app/main.py`** (`app.include_router(atendimento_zap_router.router)`).
- [ ] Mídia de saída (§6.8): `foto.py` (`CAIXAS_COM_FOTO`, `legenda_permitida` ≠ `legenda_obrigatoria`), `enviar.py:1517-1525` (legenda opcional pelo validador), ramo do Zap em `_chamar_plataforma_foto` (`:1560-1588`), URL relativa em `_aplicar_resultado` (`:883`), `envio_foto` do Zap em `routers/atendimento_painel.py:84-101`; `zap_api.py` com upload.
- [ ] Local: `WorkerSettingsZap` mínima e o worker no `docker-compose.atend-local.yml` com o volume `atend_uploads` (§11.1).
- [ ] Web: `pecasDe` com URL relativa, `<audio>`, `<video>`, documento e estados.
- [ ] Script: subcomando `subir-midia`.
- [ ] Testes: `test_atendimento_zap_midia.py`.
- [ ] No fim, o Eduardo revoga o token de teste.

### Onda 4 — Tela de números, saúde e alarmes (M)

- [ ] Completar `routers/atendimento_zap.py` (tabela §8.5) com a dependência de admin.
- [ ] Criar `AtendimentoZapNumeros.vue` dentro de `AtendimentoCanais`.
- [ ] `app/historico/nomes.py`: `REVELACOES` (verify token, PIN) e `ACOES`/`FRASES` das rotas novas.
- [ ] Rotas de credencial: `inspecionar_token` no `POST /contas`, "trocar token", troca de segredo `rotina`/`vazou`, `inscrever` com conferência de `subscribed_apps`, `testar` com `webhook_configuration`, "registrar" com as travas de etapa, caso e contador.
- [ ] Cron `atendimento_zap_saude` (a cada 15 min). Gera alerta quando:
  - há falha de assinatura de um app **e** nenhum evento válido dele no mesmo período (proporção, §5.1);
  - o backlog de eventos passa de 10 min;
  - há mídia a menos de 24 h de expirar sem ter sido baixada;
  - o token é inválido ou vence em 7 dias, ou a inspeção falha;
  - a qualidade do número cai, ou chega 368/131048;
  - o `webhook_configuration` do número ou da WABA aponta para outro lugar, ou há outro app em `subscribed_apps`;
  - o contador do mês chega a 800 e a 1.000 respostas cobradas, ou o dia chega a 70 % do teto;
  - um número `ativo` com movimento nas últimas 24 h fica mais de 3 h **sem webhook** em horário comercial ("a Meta parou de mandar?").
- [ ] `worker.py:4166-4175`: ler `webhook:meta_wa:*` (e `webhook:meta:*`).
- [ ] Cron `atendimento_zap_limpeza` (retenção).
- [ ] `vigia_leitura_atendimento.py`: `CLASSE_ZAP` em `classe_do_canal` (`:187-201`) e em `LIMITES` (`:142`).
- [ ] Rota LGPD `POST /contatos/{id}/apagar`, varrendo tudo o que o §10.5 lista.
- [ ] Testes: `test_atendimento_zap_cadastro.py`.

### Onda 4b — Ensaio em produção com o número de teste (só com OK)

- [ ] Publicar as Ondas 1 a 4 com tudo desligado.
- [ ] Eduardo publica a **política de privacidade** e a página de **exclusão de dados**, preenche as duas URLs no app **"DaVinci Zap"** (o de produção) e põe o app em modo **"Ao vivo"** (§13.1 passo 7).
- [ ] Eduardo cadastra, na tela de produção, o app "DaVinci Zap" e a WABA de teste **dele** (nunca o "DaVinci Zap Teste" do computador).
- [ ] Na Meta, colar a URL e o verify token e marcar **todos os campos** da lista do §10.2.
- [ ] "Inscrever" pela tela (`POST /{waba}/subscribed_apps`) e conferir em `GET /{waba}/subscribed_apps` que só o nosso app está lá.
- [ ] Ligar só `ATENDIMENTO_ZAP_LEITURA_ATIVA` e conferir que as mensagens reais chegam.
- [ ] Depois, envio com a lista de teste.
- [ ] Deixar 48 h sem falha de assinatura.

### Marco — 1º número real (o de menor movimento)

Roteiro de §13.3, a começar pelo "vai/não vai", com o OK do Eduardo para **cada** número.

### Onda 5 — Fora das 24 h (M)

Modelos (migration, sincronização, envio por pessoa, aviso de custo), **com** opt-in obrigatório, opt-out valendo para todo modelo e só utility ligado a pedido (§6.6), e marcar como lida (decisão P11).

### Onda 6 — Transferir para a venda (M)

Vínculos (migration), abas, cartão Cliente, escopo da equipe (§9).

### Onda 7 — IA e extras (M–G)

IA no Zap (v8), interativos, "digitando".

---

## 13. Checklist do Eduardo

### 13.1 Na Meta — no Gerenciador de Negócios **onde estão os números** (não no do Instagram)

**Para a Onda 1 você não precisa fazer nada na Meta.** O receber é testado no computador com mensagens de mentira e sem nenhuma chave de verdade. Os passos abaixo valem a partir da Onda 2.

1. **Dois apps.** Entre em **developers.facebook.com** → "Criar app" → tipo **"Empresa/Negócios"** → escolha **esse** Gerenciador → adicione o produto **WhatsApp**. Faça isso **duas vezes**:
   - um com o nome **"DaVinci Zap Teste"**, que só vai ser usado no computador;
   - outro com o nome **"DaVinci Zap"**, que só vai ser usado no servidor.

   Em cada um, a Meta cria sozinha uma conta de teste e um **número de teste grátis** (em "Configuração da API").
2. Nos dois apps, em "Configuração da API": cadastre o **seu celular** como destinatário de teste e confirme com o código que chega por WhatsApp.
3. Em "Configurações do app → Básico", localize o **ID do app** e a **Chave secreta do app**. Não copie para lugar nenhum por enquanto.
4. Em **Configurações do negócio → Usuários do sistema**, crie **dois** usuários do tipo **funcionário** (não administrador). Um administrador enxerga **todas** as contas de WhatsApp do Gerenciador, e um funcionário só as que você der a ele.
   - **"DaVinci Zap Teste"**: em "Atribuir ativos", dê a ele **só** o app de teste e **só** a conta de teste desse app;
   - **"DaVinci Zap"**: dê a ele o app de produção e, por enquanto, só a conta de teste desse app. Cada número real entra aqui no dia dele;
   - em cada um, clique em "Gerar token": escolha o app certo, validade **"Nunca"** e as permissões `whatsapp_business_messaging` e `whatsapp_business_management`.
5. **Onda 2 (no computador):** quando nós avisarmos, rode no seu terminal o comando que vamos passar (`docker exec -it … atendimento_zap_local.py cadastrar`, §11.3). Ele vai pedir o ID do app, a chave secreta, o token e o ID da conta do WhatsApp **do app "DaVinci Zap Teste"**. **Digite lá, nunca mande no chat nem por mensagem.** A chave e o token do "DaVinci Zap" (produção) **nunca** são digitados no computador, só na tela do DaVinci de produção (Onda 4b).
   - Ao fim de cada rodada de testes no computador, **revogue o token de teste** (em Usuários do sistema, no usuário "DaVinci Zap Teste"). Na rodada seguinte, gere outro.
6. **Política de privacidade.** Publique uma página de **política de privacidade** e uma de **como pedir a exclusão dos dados**, por exemplo no site da empresa. Elas citam que as conversas do WhatsApp passam pela Meta e por quanto tempo o DaVinci guarda mensagens e fotos. A política do WhatsApp exige isso, e sem a URL a Meta não deixa pôr o app em modo "Ao vivo".
7. **Só quando autorizar a produção** (Onda 4b), no app **"DaVinci Zap"**:
   - em "Configurações do app → Básico", preencha as duas URLs do passo 6 e mude o app para **"Ao vivo"** (*Live*). A Meta avisa que, em modo de desenvolvimento, parte das mensagens não é enviada;
   - em WhatsApp → Configuração → Webhook, cole a **URL** e o **código de verificação** que o DaVinci mostrar e marque **todos** estes campos: `messages`, `user_preferences`, `account_update`, `account_alerts`, `phone_number_quality_update`, `phone_number_name_update`, `message_template_status_update` e `message_template_quality_update`.
8. **Antes do 1º número real:**
   - peça a **verificação da empresa** desse Gerenciador. Sem ela só cabem 2 números, e a migração exige isso dos dois lados;
   - cadastre uma **forma de pagamento sua** na conta do WhatsApp (não a do Duoke). Desde 01/10/2026 as respostas passaram a ser cobradas depois das 1.000 grátis por número por mês (a conferir);
   - confira se o **nome de exibição** de cada número está aprovado;
   - se o Gerenciador tem endereço no Brasil, a conta do WhatsApp se cria pelo **painel do app**, não pelo Business Suite.
9. **PIN de 6 dígitos** de cada número: o DaVinci guarda cifrado e mostra só para administrador, e cada vez que alguém vê fica registrado. Anote também num lugar seguro fora do sistema (decisão final na pergunta 14.2 nº 10).

### 13.2 No Duoke — para **cada** número, começando pelo de menor movimento

1. Esse número está na **API oficial do WhatsApp** ou no **aplicativo WhatsApp Business ligado por QR code**?
2. Se for API: em qual Gerenciador e em qual conta do WhatsApp? Ela é **sua** ou **do Duoke**?
3. Quem paga as mensagens hoje: você ou o Duoke?
4. No dia combinado, eles **desligam a verificação em duas etapas (o PIN)** desse número?
5. Quais **modelos de mensagem aprovados** existem nesse número?
6. Dá para **exportar o histórico** das conversas? O WhatsApp não passa o histórico para o DaVinci.
7. Até quando o contrato cobra por esse número?
8. Qual é o horário de menor movimento para a troca, e quanto tempo o número pode ficar fora?
9. **Quantas mensagens por dia** a equipe manda por esse número (média e pico)? É o que define o teto do número no DaVinci.
10. No dia, eles **tiram o app deles** da conta do WhatsApp (`subscribed_apps`), o **acesso de parceiro** e o **usuário do sistema** deles, e apagam qualquer **endereço alternativo de webhook** que tenham posto no número ou na conta? Sem isso, as mensagens dos clientes continuam chegando a eles.

E com você: **quem guarda o chip** de cada número? O código de confirmação chega nele por SMS ou por ligação.

### 13.3 Roteiro do dia da migração (um número por vez)

1. **Uma semana antes, descobrir o caso** (pelas respostas do Duoke) e marcá-lo no número no DaVinci:
   - **A**: o número está na API oficial numa conta do Duoke. Exige migração entre contas (passo 5).
   - **A2**: a conta já é sua e o Duoke só tem acesso. **Não** se registra o número de novo (ele já está registrado pelo Duoke; registrar com outro PIN só queima tentativas). Inscreve-se o nosso app e tira-se o Duoke, na ordem do passo 5.
   - **B**: o número está no aplicativo por QR code. Sem "coexistência" (que parece exclusiva de parceiros da Meta [não confirmado]), passar para a API **apaga o histórico do aplicativo**, e o número fica cerca de 3 min fora.
2. **Uma semana antes:** nome de exibição aprovado, lista dos modelos, chip à mão, exportação do histórico pedida ao Duoke, volume diário do número (para o teto) e o compromisso do Duoke com a pergunta 10 do §13.2.
3. **Um dia antes:**
   - avisar a equipe;
   - cadastrar o número no DaVinci com `etapa='migrando'`, `envio_ligado=false` e o `teto_dia` do volume medido;
   - **com o OK do Eduardo**, ligar `ATENDIMENTO_ZAP_ENVIO_ATIVO` em produção (recriar api e worker é publicar, §6.1). Com todos os números em `envio_ligado=false`, nada sai por causa disso.
4. **"Vai/não vai"** (no dia, antes de qualquer passo no número). **Tudo conferido por API ou pela tela**; se um item falhar, **não vai**:
   - [ ] WABA de destino criada e com a **empresa verificada**;
   - [ ] **forma de pagamento sua** cadastrada na WABA de destino;
   - [ ] app "DaVinci Zap" em modo **"Ao vivo"**, com a política de privacidade publicada;
   - [ ] URL do webhook validada e **todos os campos** marcados (§13.1 passo 7);
   - [ ] `GET /{waba}/subscribed_apps` da WABA de destino com o **nosso app inscrito**. Mensagem que chega sem app inscrito não é reenviada e não dá para buscar depois: perde-se de vez;
   - [ ] `ATENDIMENTO_ZAP_LEITURA_ATIVA` e `ATENDIMENTO_ZAP_ENVIO_ATIVO` ligadas em produção (a tela de números mostra as duas);
   - [ ] um envio e um recebimento ok **hoje**, com o número de teste, em produção;
   - [ ] usuário do sistema "DaVinci Zap" com acesso à WABA de destino;
   - [ ] o Duoke disponível agora, para fazer a parte dele **na mesma sessão**.
5. **No dia:**

   **Caso A** (tudo numa sessão só, com o Duoke na linha):
   1. O Duoke desliga o PIN. A partir daqui o número fica **sem** a verificação em duas etapas, exposto a quem tiver o chip ou conseguir o código. Por isso os passos 2 a 4 seguem **sem intervalo**.
   2. Nós pedimos a migração (`POST /{waba}/phone_numbers` com `migrate_phone_number=true`).
   3. O código chega no chip e nós confirmamos.
   4. Registramos **uma vez** com o PIN novo (botão "registrar", que só aparece agora; §8.5). Se der 131031 ou 133016: **parar** e falar com o suporte da Meta.
   5. "Testar conexão": o `webhook_configuration` do número e da WABA vem vazio ou com a nossa URL.

   **Caso A2** (nesta ordem):
   1. Cadastrar a **nossa forma de pagamento** na WABA. Se a linha de crédito for do Duoke, os envios passam a falhar com 131042 quando ele sair.
   2. Inscrever o nosso app e conferir em `GET /{waba}/subscribed_apps`.
   3. Enquanto o Duoke ainda responde, o DaVinci fica em `observar`, porque **não vemos** as respostas do Duoke e a fila mostraria "aguardando".
   4. O Duoke **remove o app dele** (`DELETE /{waba}/subscribed_apps`, feito por eles), e você revoga o acesso de parceiro e o usuário do sistema do Duoke em Configurações do negócio. Mais de um app pode estar inscrito na mesma WABA, e enquanto o do Duoke estiver lá ele continua recebendo as mensagens dos clientes (vazamento sob a LGPD) e pode responder em dobro.
   5. Conferir de novo: `GET /{waba}/subscribed_apps` com **só** o nosso app, e `webhook_configuration` vazio ou com a nossa URL. **Guardar um print** das duas telas.
6. **Teste de 5 minutos:**
   - o seu celular manda texto, foto e áudio, e tudo aparece no DaVinci em menos de 1 min;
   - nós respondemos pela tela e aparece ✓✓;
   - o Duoke não recebe mais (confirmado por eles e pelo `subscribed_apps`).
   
   Só então: canal em **`humano`**, `envio_ligado=true` e `etapa='ativo'`.
7. **Vigiar 48 h** na tela de números: conexão, último recebido, erros, qualidade, teto do dia.
8. **Não existe volta instantânea.** Voltar para o Duoke é outra migração. Se o DaVinci cair, nada se perde (a Meta reenvia por 7 dias e nós guardamos tudo), mas os clientes esperam. O botão "pausar" só para o envio.

---

## 14. Riscos e perguntas em aberto

### 14.1 Riscos

| # | Risco | Mitigação |
|---|---|---|
| R1 | **Perder mensagem em silêncio** (URL ou segredo errado = 403 em tudo, e a Meta pode desligar o webhook; objeto inesperado com 200) | Rota e segredo próprios por app; 200 só depois de gravar; nada descartado; contador e alarme de assinatura; alerta "número sem webhook" |
| R2 | **Resposta pelo canal errado** (Zap pelo chat do marketplace) | Desvio por canal **e** plataforma; trava antiga mantida; teste "zap com plataforma shopee" |
| R3 | **Ordem das travas** no `_destino` | `_travas_do_sistema` como movimento puro; teste de cada trava na ordem |
| R4 | **"Enviada" não é "entregue"** | Status por webhook com tiques; conciliação do ambíguo |
| R5 | **Mídia expira** (URL em 5 min, id em 7 dias) | Baixar na hora, com backoff até o prazo; alerta 24 h antes |
| R6 | **LGPD e disco** com arquivo de cliente | Retenção, rota autenticada, exclusão a pedido; conferir o backup de `/data/uploads` |
| R7 | **BSUID e 9º dígito** (conversa em dobro ou contato que não casa) | Contato próprio como chave; normalização só para casar; confirmar com payload real antes da Onda 2 |
| R8 | **Token de System User revogado** (o canal emudece) ou **vazado** | `inspecionar_token` diário e estrito, `status` da conta, alerta, recusa clara; usuário funcionário só com as WABAs necessárias; rota "trocar token" e rotação semestral |
| R9 | **Custo** (atendimento cobrado desde 01/10/2026; modelos pagos) | Contador por número, uma vez por wamid e pelo `pricing.type`; alertas em 800 e 1.000; teto por número; modelos só por pessoa |
| R10 | **O dia da migração** ("observar" = ninguém responde; sem histórico; sem volta rápida) | Ondas 1 a 4 sólidas + ensaio 4b; "vai/não vai" conferido por API; canal em `humano`; exportação pedida ao Duoke |
| R11 | **Caso A2 em paralelo** (as respostas do Duoke não chegam até nós; status com wamid desconhecido) | `observar` por pouco tempo; `sem_alvo` sem erro |
| R19 | **Duoke continua recebendo** depois da troca (app inscrito, acesso de parceiro, endereço alternativo de webhook) | Ordem do caso A2; `subscribed_apps` e `webhook_configuration` conferidos na troca e no cron de saúde; print guardado |
| R20 | **Número travado pelo "registrar"** (10 tentativas em 72 h; PIN errado = conta travada) | Botão só em `migrando` e nos casos A/B; uma tentativa por clique; para em 3; 131031/133016 com "fale com o suporte" |
| R21 | **Histórico copiando dado de cliente** por 365 dias (inclusive na exclusão LGPD) | Contatos, eventos e mídias fora do gatilho; `SEM_CORPO`; teste |
| R22 | **Credencial de produção no Mac** | Dois apps; Onda 1 sem chave real; só o app de teste no banco local; token de teste revogado no fim de cada rodada |
| R23 | **Violação da política do WhatsApp** (pedir cartão, conta, documento; modelo sem opt-in) | Regra só do Zap no validador, para pessoa e IA; opt-in obrigatório para modelo |
| R24 | **Número reciclado** junta duas pessoas no mesmo contato | BSUID diferente = contato novo; 9º dígito só em celular; teste |
| R25 | **App em modo de desenvolvimento** (parte dos webhooks não chega) | Modo "Ao vivo" e política de privacidade antes da Onda 4b, item do "vai/não vai" |
| R12 | **Túnel no localhost** expõe o mock-login | Não usar; ensaio 4b no lugar |
| R13 | **Listas paralelas no front** | Teste `.cjs` que confere todas |
| R14 | **Colisão de migration** (o main anda rápido) | Reconferir o head na hora |
| R15 | **Limite de 2 números** sem verificar a empresa | Verificação antes do 1º número real |
| R16 | **Volume herdado do Duoke** | Webhook só grava; se a fila padrão ficar lenta, uma fila própria `davinci_zap` em `worker_pool.py` |
| R17 | **Escopo da equipe** (Zap só para quem vê tudo) | P14; Onda 6 abre pela marca e pelo vínculo |
| R18 | **Cloudflare/WAF** na frente do servidor (documentação conflitante) | Conferir antes de cadastrar a URL |

### 14.2 Perguntas para o Eduardo (as premissas vetáveis)

1. **PIX da empresa** no Zap: liberar para a **pessoa** (o atacado fecha venda por ali)? A sugestão é manter bloqueado na v1, e para a IA sempre. **Conta bancária, cartão e documento não entram nesta pergunta:** a política do WhatsApp proíbe pedir ou mandar esses números, e insistir põe o número em risco de restrição.
2. **Por quanto tempo guardar** fotos, áudios e documentos dos clientes? A sugestão é 180 dias. Os arquivos ficam no disco do servidor sem cifra: eles devem entrar no backup?
3. O cliente pode ver os **tiques azuis** ("lido") quando alguém abre a conversa?
4. **Prazo de resposta** do Zap na fila: 24 h (igual à janela) ou menor?
5. A tela de números fica **dentro de "Lojas e modo"** (sugerido) ou em Cadastros?
6. A **IA** entra no Zap algum dia? Só sugerindo ou também enviando?
7. Quem da equipe, além dos administradores, deve ver o Zap?
8. Qual é o **número de menor movimento**, para começar por ele?
9. A equipe precisa **pedir o CPF** do cliente pelo Zap para emitir a nota fiscal? A política do WhatsApp proíbe pedir número de documento, e ainda não está confirmado se há exceção. Até confirmar, o DaVinci barra.
10. **O PIN de cada número:** fica guardado (cifrado) no DaVinci, para o administrador ver sob clique, com registro de quem viu? Ou fica **só com você**, fora do sistema? A sugestão é guardar no DaVinci **e** você anotar.
11. **Política de privacidade:** onde ela vai ficar publicada (site de qual empresa ou marca)? Quem escreve o texto (você, o jurídico)?
12. **Modelos (Onda 5):** como o cliente vai dar o "aceito receber mensagens"? Sugestão: a equipe pergunta dentro da conversa e o "sim" do cliente fica registrado. Os contatos que vieram do Duoke começam sem esse aceite.

### 14.3 Pontos técnicos a confirmar (antes de codar a onda indicada)

- O nome exato dos campos de BSUID em `messages[]`/`statuses[]` e do campo `recipient` no envio para BSUID (Onda 1/2). Se existem os campos de webhook `user_id_update` e `business_username_updates`: a lista oficial consultada em 17/10 não os traz.
- Se o `debug_token` aceita o `input_token` fora da URL (Onda 1).
- Se o endereço alternativo de webhook (`webhook_configuration`) vale por app ou para todos os apps da WABA (Onda 4).
- Se o app de teste em modo de desenvolvimento recebe todos os webhooks do número de teste (Onda 2), já que a Meta avisa que parte não chega em modo de desenvolvimento.
- Se a política do WhatsApp tem exceção para pedir CPF para a nota fiscal (Onda 2).
- O formato do `sha256` da mídia e os domínios da URL de download (Onda 3).
- O 131030 e o limite de 5 destinatários do número de teste (Onda 2).
- Se, na janela grátis de 72 h do CTWA, texto livre passa da regra das 24 h. Por ora, as 72 h contam só como custo.
- Os campos de `GET /{phone_number_id}` para a saúde (Onda 4).
- O preço exato no Brasil e as 1.000 respostas grátis por número (a página de preços diverge do FAQ).
- Se existe caixa de entrada da Meta para número na Cloud API, para o plano de volta atrás.
- Como o Duoke conecta cada número (§13.2). Não há documentação pública da integração de WhatsApp do Duoke.
- Revisão da Onda 2 (26/10/2026), a confirmar no ensaio do §11.5 ou depois:
  - Qual `pricing.type` a Meta manda nas respostas **dentro** das 1.000 grátis (`free_customer_service` ou `regular` com `billable=false`). O contador "N de 1.000" já conta toda resposta de atendimento (`category=service`, qualquer `type`), e o alerta sai em 800 e 1.000.
  - Que códigos a Meta devolve no "marcar como lida" para um `message_id` que ela nunca vai aceitar. Hoje 100 e 131009 gravam o wamid como tratado, e a abertura seguinte não repete a chamada.
  - O tique azul só é validado de verdade na **Onda 4b**: sem túnel, o wamid real do "oi" que sai do celular não chega ao localhost, e o "oi" simulado (`wamid.LOCAL…`) não vai à Meta.
  - O 133016 (registro travado por 72 h) não segura o aviso na barra na Onda 2: ele só vem do "registrar" (Onda 4), que lê `saude.registro_travado`.
- Decidido em 26/10/2026 (não é mais pergunta): **não se pede CPF pelo Zap**. O validador falha fechado: qualquer menção a CPF, RG, CNH ou identidade barra (pessoa e IA), menos a negação explícita ("não precisamos do seu CPF").

---

## Apêndice A — Como as duas propostas foram comparadas

**Base escolhida: a proposta incremental.** Ela segue mais de perto os precedentes que já existem no código e tem uma Onda 1 menor. **Enxertos da robusta:** identidade do contato, as três chaves (com o botão por número), conciliação do ambíguo, alarmes e roteiro da migração.

| Ponto | Incremental | Robusta | Escolha e porquê (conferido no código) |
|---|---|---|---|
| Régua `motivo_canal_sem_envio` | Mantém o papel; `zap+whatsapp` ganha a frase `MOTIVO_ZAP_SO_PESSOA` | Passa a devolver None para `whatsapp` | **Incremental.** É o padrão do e-mail da ponte (`MOTIVO_EMAIL_SO_PESSOA`, `constantes.py:133`, com `_destino` desviando antes, `enviar.py:397-402`). Com None, `modo_observacao` (`routers/atendimento.py:2557-2563`) daria True e a tela mostraria "O que a IA responderia", e a IA teria de ser cortada à mão em 9 lugares (`ia.py:3709, 4208, 4504`, `humano.py:334`, `enviar.py:1753`, `routers/atendimento.py:2527, 2940`) |
| Onde o desvio entra no `_destino` | Antes da `:404`, por canal + plataforma | Depois da `:404`, só pela plataforma | **Incremental**, por canal **e** plataforma |
| Chave da conversa | BSUID ou wa_id, com "adoção" | UUID do contato (`zap_contatos`) | **Robusta.** Evita conversa em dobro quando o BSUID ou o telefone aparece depois |
| Nome das tabelas | `atendimento_zap_*` | `zap_*` | **Incremental.** `tests/test_atendimento_migration.py:94-98` só compara as tabelas com o prefixo `atendimento_`; com `zap_*` a migration ficaria fora do teste |
| Dedupe do webhook | Um evento por item (`m:`/`s:`/`e:`) | Um evento por corpo (sha256) | **Incremental.** Retentativa e dedupe por item; a mesma tabela serve de histórico de status, sem `zap_status_eventos` |
| Teto de 200×200 | No webhook | No job | **Nenhum dos dois.** As duas cópias de `webhooks.py:842` descartariam o resto de um lote de até 1.000 updates; aqui só há o limite de 4 MB e o processamento em lotes |
| Tabela `zap_mensagens` à parte | Não (fica em `payload.zap`) | Sim | **Sem tabela.** O `biz_opaque_callback_data` = id da nossa mensagem acha a linha por PK; o resto vai pelo contato e pelo `(conversa_id, wamid)` |
| `zap_auditoria` | Não | Sim | **Não.** O Histórico já grava e mascara (`historico/mascara.py`). Revisão de 17/10: as tabelas com dado de cliente saem do gatilho, e as rotas com corpo sensível entram em `SEM_CORPO` (§4.3) |
| Chaves de envio | Lista de teste **libera** mesmo com `zap_envio` desligado | Três chaves + botão por número; lista só restringe; fail-closed em dev | **Robusta.** Nenhuma lista religa uma chave; o `dca.sh` não barra a Meta |
| Conciliação do envio ambíguo | Não | `biz_opaque_callback_data` | **Robusta** (o campo volta nos status, segundo a pesquisa) |
| Status x linha `enviando` | Não trata | Não trata | **Regra nova:** o status nunca mexe em linha `enviando` (evita `failed` sobrescrito por `enviada`) |
| URL do webhook | `{app_id}` | `{rota}` aleatória | **`{app_id}`.** É público e mais simples para o Eduardo; a assinatura é a autenticação |
| Verify token | Cifrado, pode ser mostrado de novo | Só o hash, mostrado uma vez | **Cifrado**, com "gerar outro": o dono não é técnico |
| Troca do app secret | — | 48 h aceitando os dois | **Robusta, encurtada na revisão:** 1 h na troca de rotina e zero quando vazou, porque a Meta troca na hora e a janela longa só ajudaria quem roubou a chave |
| Credenciais em dev | Script + `getpass` | Só pela tela | **Revisto:** Onda 1 sem chave real (`cadastrar --ficticio`); da Onda 2 em diante, script com `getpass` (`docker exec -it`) **só com o app de teste**; tela na Onda 4. Credencial de produção nunca no Mac |
| Mídia | Onda 3 | Onda 1 | **Schema na Onda 1** (registro `pendente`) e **baixador na Onda 3**; anti-SSRF e lista de MIME da robusta |
| Erros da Meta | Tabela de tradução | Tabela + efeito (pausa automática em 368/131048, token inválido) | **Robusta** |
| `_so_admin` | Tratado como "só admin" | Idem | **Correção:** é a trava de acesso ao Atendimento (`routers/atendimento.py:294-305`); credencial exige admin à parte |
| "Sugerir" no Zap | — | — | **Correção:** `routers/atendimento.py:2929` recusa toda `PLATAFORMAS_EXTERNAS` com a frase de carrinho/comentário; precisa de frase própria |
| Janela na tela | Âmbar nas últimas 2 h | Âmbar nas últimas 2 h | Iguais; a régua genérica de 48 h (`AtendimentoConversa.vue:1115`) deixaria sempre âmbar |
| Transferir para a venda | Onda 6 | Onda 3 | **Onda 6:** não bloqueia o atendimento; a pré-venda já sai de graça |
| Ensaio em produção com o número de teste | Alternativa | Onda 5a | **Onda 4b** antes do 1º número real: dá recebimento real sem túnel |
| Modo no dia da migração | `humano` | `humano` (D7) | Iguais (P13) |

---

## 15. Revisão (17/10/2026)

Duas críticas independentes foram conferidas contra o código do commit `905baa22` (`codigo-real`, 21 itens) e contra a documentação da Meta (`meta-seguranca`, 22 itens). A documentação da Meta foi consultada em 17/10: webhooks/overview, webhooks/override, access-tokens e o registro de número. As linhas `arquivo:linha` citadas pelos revisores batem, com desvio de 1 a 3 linhas.

### 15.1 Corrigido

| # | Crítica | O que mudou no documento |
|---|---|---|
| 1 | codigo-real 1 · meta-seguranca 8 — **o gatilho do Histórico copia dado de cliente por 365 dias** (procede: `historico/sql.py:30-35, 104-126, 311-341`; `banco.py:21, 36-55`) | `zap_(contatos\|eventos\|midias)` em `EXCLUIDAS` e no teste `test_historico_regras.py`, **já na Onda 1**; `apps`/`contas`/`numeros` ficam com o gatilho (`*_enc` sai como `{_arquivo:true}`); `SEM_CORPO` ganha `atendimento/zap/(conversas\|contatos)/` (§3.2, §4.2, §4.3, §10.3, §11.6, Onda 1) |
| 2 | codigo-real 2 — o falso de 4 parâmetros de `test_atendimento_enviar.py:1324-1329` quebraria | Os nomeados só são passados quando `destino.zap` existe (§6.2 item 6) |
| 3 | codigo-real 3 — `IntegrityError` marcado como `repetido` perderia mensagem (procede: `gravar.py:612-627, 1019-1028` já tratam a corrida) | Todo erro, inclusive `IntegrityError`, vai para backoff; `repetido` só quando `gravar_mensagem` diz que a mensagem já existia (§4.2, §5.2, §5.3) |
| 4 | codigo-real 4 — `proxima_em <= now()` nunca pega evento novo (NULL) | Predicado `processado_em IS NULL AND (proxima_em IS NULL OR proxima_em <= now())`; `ignorado` nasce processado (§4.2, §5.2) |
| 5 | codigo-real 5 — lote numa transação seguraria a trava da conversa e o "Enviar" daria `conversa_ocupada` (procede: `gravar.py:381-433`, `enviar.py:661-672`) | **Um commit por evento** (§3, §5.2) |
| 6 | codigo-real 6 — worker, `getpass` sem TTY e volume de mídia no localhost (procede: `atend_api.sh` usa `-i` com o `tar`; `docker-compose.atend-local.yml` só tem api/postgres/redis; `override.yml:17-20`) | `simular --processar` no mesmo processo (Ondas 1-2); `WorkerSettingsZap` e worker com `atend_uploads` na Onda 3; comando `docker exec -it` documentado (§11.1, §11.3, §11.4) |
| 7 | codigo-real 7 — `atendimento-filtro-plataforma.cjs:103` e `:319` quebram | Os dois entram na Onda 1, atualizados junto (§3.2, §8.1, §11.6) |
| 8 | codigo-real 8 — `len(TABELAS) == 18` e FK para `marcas` no rascunho | Contagem 18 → 24 e stub `marcas` (§3.2, §4.2, Onda 1) |
| 9 | codigo-real 9 — o canal ficaria `novo` para sempre (procede: `_motivo_do_status` só vê o `CanalOut`) | O processador grava `canal.status`, `ultimo_ok_em` e `ultimo_erro`, como `redes.aplicar_status` (§3.2, §5.2) |
| 10 | codigo-real 10 · meta-seguranca 12 — a `validade_do_token` é melhor esforço (procede: `meta_client.py:388-420` confunde "nunca vence" com "não consegui ver" e não traz escopo nem `app_id`) | Função nova e estrita `inspecionar_token`; `token_nunca_vence` explícito; conferência de `app_id`, escopos e `granular_scopes`; `input_token=` mascarado; `phone_number_id` conferido contra o `entry.id` (§3.1, §4.2, §5.2, §8.5, §10.1) |
| 11 | codigo-real 11 — foto do Zap daria `cliente_indisponivel`, `envio_foto.pode=False`, legenda sem validador e URL relativa recusada (procede: `enviar.py:1576, 883`; `atendimento_painel.py:84-101`; `foto.py:217-224`) | Os quatro pontos entram na Onda 3 (§3.2, §6.8) |
| 12 | codigo-real 12 — duas fontes da janela, efeitos perdidos no rollback, lista de teste pelo `wa_id` cru | Trava 12 com `min(contato+24h, conversa.pode_enviar_ate)`; efeitos em `payload['zap']['efeito']` aplicados depois de `_gravar_resultado`; lista comparada normalizada (§4.2, §6.1, §6.2, §6.3) |
| 13 | codigo-real 13 — o retorno do envio fica em `payload.envio.zap` (procede: `enviar.py:855`) | Os dois caminhos fixados e o `MensagemOut` lendo cada um (§4.1, §5.6, §6.4) |
| 14 | codigo-real 14 — mexer em `_ERRO_POR_CODE` muda as frases do Instagram | Tradução só em `zap_erros`, por um `_erro_zap` próprio (§3.1, §6.5) |
| 15 | codigo-real 15 — o Zap abriria o painel do pedido e a gaveta (procede: `canalExterno` só cobre carrinho e comentário) | `semPainelDePedido` só nesses pontos, sem ampliar `canalExterno` (que esconderia o compositor) (§3.2, §8.2) |
| 16 | codigo-real 16 — GET que mostra segredo não fica no Histórico | Verify token e PIN em `REVELACOES`; frases em `ACOES` (§3.2, §4.3, §8.5) |
| 17 | codigo-real 17 · meta-seguranca 19 — corpo lido inteiro, falha de assinatura no banco, alarme envenenável, sem cache negativo, JSON inválido descartado, rota nos dois hosts | Streaming com teto; falha só no Redis; freio por IP nos 403; alarme por proporção; cache negativo; JSON inválido gravado como `ignorado`; rota só no host principal, no código (§5.1, §10.2) |
| 18 | codigo-real 18 — `classe_do_canal` devolve None para `whatsapp` | `CLASSE_ZAP` em `classe_do_canal` e `LIMITES` (§3.2, Onda 4) |
| 19 | codigo-real 19 — `routers/atendimento_zap.py` sem `include_router` | Registro em `main.py` na Onda 3 (§3.1, §3.2, Onda 3) |
| 20 | codigo-real 20 · meta-seguranca 21 — token na `repr` do dataclass | `token: str = field(repr=False)` (§6.2) |
| 21 | codigo-real 21 — um `enqueue_job` por POST | Enfileiramento coalescido por `SET NX EX 3`, sem `_job_id` fixo (§5.1) |
| 22 | meta-seguranca 1 — credencial de produção no Mac e usuário do sistema administrador | Dois apps (Teste e produção); usuário **funcionário**; Onda 1 sem chave real; só o app de teste no banco local; token de teste revogado no fim de cada rodada; conferir a `CREDENTIALS_KEY` local (§1, P4, §10.1, §11.1, §11.3, §13.1) |
| 23 | meta-seguranca 2 — app em modo de desenvolvimento e sem política de privacidade (procede: "some webhooks will not be sent if your app is in Dev mode") | Modo "Ao vivo", URLs de privacidade e de exclusão, `subscribed_apps` conferido na 4b e em cada WABA (§10.2, §10.5, Onda 4b, §13.1) |
| 24 | meta-seguranca 3 — webhook conferido depois do registro; chaves de produção, Live, campos e pagamento fora do roteiro; número sem PIN entre um passo e outro | "Vai/não vai" conferido por API antes do 1º passo; chave do Zap ligada na véspera com OK; PIN e registro na mesma sessão (§6.1, §13.3) |
| 25 | meta-seguranca 4 — "registrar" pode travar o número (procede: 10 tentativas em 72 h, 133016; 131031) | Botão só em `migrando` e nos casos A/B, nunca no A2; uma tentativa por clique; para em 3; 131031/133016 na tabela (§4.2, §6.5, §8.5) |
| 26 | meta-seguranca 5 — no caso A2 o Duoke continuaria recebendo e pagando (procede: "Meta sends retries to all apps that have subscribed") | Ordem nova do A2: pagamento nosso, `subscribed_apps`, remoção do app e dos acessos do Duoke, print (§13.2 pergunta 10, §13.3) |
| 27 | meta-seguranca 6 — só o campo `messages` assinado; `user_preferences` caindo como `conta` | Lista completa dos campos; tratador `preferencia` que grava no contato; fixtures (§5.1, §5.3, §10.2, §11.6, §13.1) |
| 28 | meta-seguranca 7 — endereço alternativo de webhook deixado pelo Duoke (procede: o do número tem prioridade sobre o da WABA e o do app) | `webhook_configuration` no `testar`, no cron de saúde e no roteiro (§8.5, §13.3, Onda 4) |
| 29 | meta-seguranca 9 — base legal, exclusão incompleta, contatos de terceiros, política publicada | Base legal trocada (art. 7º, V e IX); exclusão varre contato, conversa, mensagens, mídias, eventos e Histórico; evento guarda só o contato do remetente; política no checklist (§4.2, §10.5, §13.1) |
| 30 | meta-seguranca 10 — opt-in, opt-out só de marketing, "retomar_conversa", "PARAR" estreito | `opt_in_em`/`opt_in_origem` e `opt_out_em`/`opt_out_tipo` já na 0415; modelo bloqueado sem opt-in; só utility ligado a pedido; frase parecida vai para a pessoa confirmar (§4.2, §5.3, §6.6) |
| 31 | meta-seguranca 11 — a política proíbe pedir ou mandar cartão, conta e documento; o validador não cobre pessoa (procede: `validador.py:725-737` só para IA) | Regra `_DADO_SENSIVEL_ZAP` para pessoa e IA; pergunta 1 reescrita; CPF para a nota como pergunta nova (P9, §7, §14.2) |
| 32 | meta-seguranca 13 — token sem roteiro de troca | Rota "trocar token", rotação semestral e imediata em suspeita (§4.2, §8.5, §10.1) |
| 33 | meta-seguranca 14 — o segredo anterior valendo 48 h ajuda quem roubou a chave | 1 h na rotina, com contador; zero quando vazou; pausa por status do webhook só com 2 ocorrências (§4.2, §5.1, §5.6, §8.5) |
| 34 | meta-seguranca 15 — número reciclado junta duas pessoas | BSUID diferente = contato novo e o antigo é solto; 9º dígito só em celular; teste (§4.2, §5.3, §5.4, §11.6) |
| 35 | meta-seguranca 16 — `e:<sha256>` engoliria evento de conta repetido | `entry.id` e `entry.time` entram no hash (§4.2, §5.7) |
| 36 | meta-seguranca 18 — custo contado em dobro, `billable` sendo removido, 131042 subestimado | Uma vez por wamid, pelo `pricing.type`; alertas em 800 e 1.000; 131042 pausa e alerta (§5.6, §6.5) |
| 37 | meta-seguranca 20 — mídia: redirecionamento com Bearer, tipo declarado, CSP, disco sem cifra | `follow_redirects=False` com host conferido a cada salto; tipo pela assinatura; `CSP: sandbox`; backup e retenção como decisão explícita (§4.2, §5.5, §10.4, §14.2) |
| 38 | meta-seguranca 21 — PIN "guardado" e "mostrado uma vez" ao mesmo tempo | Guardado cifrado, mostrado sob clique e registrado; decisão final como pergunta 10 (§4.2, §8.5, §13.1, §14.2) |
| 39 | meta-seguranca 22 — versão da Graph presa à do Instagram | Chave própria `meta_whatsapp_graph_version` (§3.1, §3.2, §6.1) |

### 15.2 Aceito em parte

| Crítica | O que foi aceito | O que não foi, e por quê |
|---|---|---|
| meta-seguranca 17 — teto de 300/dia como bloqueio duro | Teto **por número**, a partir do volume medido no Duoke; aviso em 70 %; trava contra laço por contato | Não virou "bloqueio só contra laço". O teto por número continua, porque é a única defesa contra um defeito que mande para **muitos** contatos diferentes, e agora acompanha o volume real |
| meta-seguranca 8 — pôr também `atendimento_zap_vinculos` em `EXCLUIDAS` | Contatos, eventos e mídias saem do gatilho | O **vínculo** (Onda 6) fica **com** o gatilho: é decisão de pessoa, guarda só o nº do pedido (nenhum texto de cliente) e "desfazer fica no Histórico" é de propósito (§9) |
| meta-seguranca 19 — restringir `/api/webhooks/meta/whatsapp/*` ao host principal no Caddy | A restrição entra no **código** (404 fora do host do `api_url`) | Não no Caddy: mexer no Caddy de produção exige OK, e o código resolve o mesmo problema sem isso |
| meta-seguranca 6 — tratador próprio para `user_id_update` e `business_username_updates` | Tratador próprio para `user_preferences` | Os nomes dos campos de BSUID **não aparecem** na lista oficial de campos consultada em 17/10. Ficaram como `[a confirmar]`; até lá entram como `conta`, com alerta (§5.3 item 8, §14.3) |
| meta-seguranca 20 — antivírus | Documento sempre como `attachment` e tipo pela assinatura | ClamAV ficou **[VETÁVEL]** para depois da Onda 3: é mais um serviço no servidor, e a Onda 3 não depende dele |
| meta-seguranca 1 — revogar o token de teste ao fim de **cada** ensaio | Revogação ao fim de cada **rodada** (Onda 2 e Onda 3) | Revogar a cada envio de teste seria trabalho demais para o Eduardo e não muda o risco: o token é de funcionário, só com a WABA de teste |
| meta-seguranca 1 — "conferir a `CREDENTIALS_KEY` local" | Virou item obrigatório antes do 1º cadastro com chave real (§11.1) | Não foi conferido agora: exige ler o valor do `.env` local e o de produção, e isso não se faz sem pedir |
| meta-seguranca 9 — exclusão também no backup | Exclusão varre todas as tabelas e campos | O backup antigo não é reescrito: os dados saem quando o backup expira, e esse prazo entra na política de privacidade |

### 15.3 Rejeitado

| Crítica | Motivo |
|---|---|
| codigo-real 20 — "o Sentry captura variáveis locais" | **Não procede:** `services/sentry.py:124` tem `include_local_variables=False`. O `repr=False` entrou assim mesmo, pelo motivo do log (meta-seguranca 21) |

### 15.4 Efeito no plano

- **A Onda 1 ficou maior e mais segura.** Ganhou o Histórico, o webhook endurecido, o processador com commit por evento, a saúde do canal, o número reciclado, o `user_preferences` e os testes de front que já existiam. Em troca, **não precisa mais de nada da Meta**: roda com cadastro fictício.
- **A E4 continua valendo.** O código começa já e é testado no localhost. O número de teste gratuito da Meta entra na Onda 2, que é quando algo sai de verdade. O receber da Onda 1 é simulado, e a Meta nunca chama o localhost de qualquer jeito.
- **O dia de cada número ficou mais longo, mas sem surpresa:** "vai/não vai" conferido por API, pagamento próprio, Duoke fora da WABA e conferência do `webhook_configuration`.
