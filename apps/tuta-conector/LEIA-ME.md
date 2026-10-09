# Conector do Tuta (DaVinci)

Programa pequeno, nosso, que lê as contas do Tuta e entrega os e-mails à
**Central de e-mail do DaVinci** (a do outro dev, `/api/mail/*`): pelo
contrato v1 DELA (o sinal, a fila de respostas e o recibo, do jeito que
estão) e por um caminho v2 NOSSO, só para o que o v1 não carrega (os ids e as
pastas do Tuta, o alias que recebeu, os avisos de golpe do Tuta, os aliases da
conta, a contagem e o movido/apagado). Usa o SDK OFICIAL do Tuta numa release
fixa (`SDK_VERSAO`) mais 7 remendos auditados (`remendos/AUDITORIA.md`).

- **Uma conta por processo**: `--conta geral` (061083.jf@tuta.com, as lojas)
  ou `--conta goslin`. Cada conta tem a sua sessão no Tuta, a sua caixa na
  Central (id + chave do agente), a sua pasta local e o seu LaunchAgent: uma
  conta caída não para a outra.
- Lê SÓ por consulta de tempos em tempos. Nunca abre websocket: assim nunca
  vira o "líder" da conta e nunca segura as regras de pasta da equipe.
- A senha e o código do autenticador são digitados UMA vez (`entrar`). Depois
  disso fica só a sessão, no Chaveiro do macOS, com o nome
  **"DaVinci conector – Mac mini (geral)"** (ou "(goslin)"), que o dono vê e
  encerra no Tuta.
- A chave do agente da caixa (a tela da Central mostra UMA vez) também fica
  só no Chaveiro.
- O corpo do e-mail sai do Mac como TEXTO (a Central nunca guarda HTML) e com
  os **códigos de verificação e os links de login/senha/confirmação
  mascarados** ("••••••", "G-••••••", "[link de acesso removido]"), a
  **senha escrita** no texto ("nova senha: ••••••••") e, no e-mail que fala de
  senha ou acesso, **todos** os links (o link curto de reset não tem cara de
  acesso) — em português, inglês e espanhol: os aliases da conta geral são os
  e-mails de LOGIN das lojas nas plataformas.
- O corpo só sai das pastas que a Central manda ler (as de plataforma, a
  Entrada, os Enviados, as caixas dos sites). Das outras (financeiro,
  contabilidade, devoluções, envio, retido, avisos, Lixeira, Spam, pasta nova
  que ninguém revisou) vão SÓ os ids, na contagem. O e-mail só para
  endereços internos (adm@, financeiro@…) também fica no Mac.
- Em disco, só estado e ids (nada de e-mail):
  `~/Library/Application Support/davinci-tuta-conector/<conta>/`.

## Estado desta versão

| Comando | Situação |
|---|---|
| `entrar`, `sair`, `estado`, `configurar`, `canario`, `versao` | prontos, por conta |
| `ler` (`--uma-volta`, `--contar`, `--seco`) | pronto: fala com a Central (v1 + v2) |
| `enviar` (uma volta) e `rodar` (leitura + envio, o do LaunchAgent) | prontos; o envio nasce DESLIGADO pelas duas chaves |
| LaunchAgent por conta | em `launchd/`, **não instalado** |

Testado contra um Tuta FALSO (cifrado de verdade pelo SDK oficial) e contra a
Central DE VERDADE rodando neste Mac (o roteiro de ponta a ponta). **Nunca
rodou contra a conta de verdade.** O primeiro passo no Mac mini é o
`ler --seco` (abaixo): lê sem mandar nada ao DaVinci.

**O servidor sobe ANTES do conector**: sem o v2 na Central (o 0387 e
`routers/mail_agent_v2.py` publicados), o conector só pulsa com o estado
`sem_v2` e não manda nenhum e-mail (pelo v1 perderia os ids do Tuta).

---

## Para o Eduardo (dono)

O programa fica em `apps/tuta-conector/target/release/tuta-conector` depois
de compilado (ver "Para quem desenvolve"). Para o LaunchAgent, copie-o para
`~/Library/Application Support/davinci-tuta-conector/bin/tuta-conector`
(um caminho que não muda a cada compilação: o Chaveiro lembra o "Permitir
sempre" por programa). Os comandos abaixo são no Terminal do Mac mini.

Toda vez: `--conta geral` (a das lojas) ou `--conta goslin`.

### 1. A caixa na Central (uma vez por conta)

Na aba **E-mail** do /atendimento, um admin cadastra a caixa:

- **Geral — Tuta**: endereço `061083.jf@tuta.com` e os 54 aliases; depois,
  em **Configurar**, visibilidade `empresa`, remetente estrito ligado; a
  ponte, o envio e o modo real ficam DESLIGADOS até o piloto.
- A tela mostra UMA vez o **id da caixa** e a **chave do Mac**.

No Mac mini:

```
tuta-conector configurar --conta geral
```

Pede a URL do DaVinci, o id da caixa e a chave (a chave NÃO aparece enquanto
você digita e nunca é mostrada depois). Fica no Chaveiro, item
"davinci-tuta-conector-davinci" / "caixa:geral".

A caixa "Goslin — Tuta" hoje é lida pelo agente do outro dev (IMAP). **Uma
caixa = um agente**: para o conector assumir a Goslin, troque a chave dela na
Central (o agente antigo para sozinho com 401), cadastre os aliases 30–46 e
rode `configurar --conta goslin` — combinado com o outro dev.

**Antes de ligar o conector na Goslin (caixa PRIVADA):** o `/sync` do v2
grava em claro no banco (`mail_folders`) o NOME e o caminho de todas as pastas
da conta. Os aliases já não (numa caixa privada só ficam os de loja e os
"só contar"), mas os nomes de pasta pessoais ficariam. Na Goslin, ou o dono
aceita isso, ou primeiro se muda o `/sync` para guardar só a chave da pasta
nas caixas privadas (pendência registrada na revisão de 08/10).

### 2. Entrar na conta do Tuta (uma vez por conta)

```
tuta-conector entrar --conta geral
```

1. E-mail: na geral, Enter usa `061083.jf@tuta.com`; na goslin, digite.
2. Senha do Tuta: não aparece na tela.
3. Se a conta tiver segundo fator: o código de 6 números do app autenticador
   (Enter vazio desiste; 3 tentativas).

O conector cria no Tuta a sessão "DaVinci conector – Mac mini (geral)" e
guarda no Chaveiro (item "davinci-tuta-conector" / "sessao:geral"). A senha
não fica guardada em lugar nenhum e nunca vai ao Tuta (vai só um
verificador, como no app oficial).

Se a conta só tiver **chave de segurança** (U2F/WebAuthn) como segundo fator,
o conector avisa e não entra: cadastre também um app autenticador (TOTP) no
Tuta em Configurações › Login › Segundo fator e rode `entrar` de novo.

Na primeira vez (e depois de cada atualização do programa) o macOS pode
perguntar se o `tuta-conector` pode usar o item do Chaveiro: **Permitir sempre**.

### 3. Ver como está

```
tuta-conector estado --conta geral
```

Sessão, caixa da Central, versão do SDK, último sinal, cursores, o diário do
envio e as chaves deste Mac. Não mostra segredo e não acessa a internet.

```
tuta-conector canario
```

Pergunta ao Tuta (sem senha) se ele ainda aceita esta versão do SDK.

### 4. Ler (o piloto, passo a passo)

```
tuta-conector ler --conta geral --seco                 # 3 mais novos de cada pasta: prova que decifra; nada sai do Mac
tuta-conector ler --conta geral --contar --uma-volta   # só os ids de cada pasta (a conciliação tem de bater)
tuta-conector ler --conta geral --uma-volta            # uma volta de leitura e sai
tuta-conector rodar --conta geral                      # leitura + envio, para sempre (o do LaunchAgent)
```

A cada volta (90 s):

- o **sinal** v1 (a cada 60 s): online / sessão caída / erro, e se este Mac
  pode enviar; a Central devolve a chave de envio que a pessoa ligou;
- o **/v2/sync**: as pastas e os aliases ATIVOS da conta; a Central diz quais
  pastas ler com corpo (a regra de palavras + a escolha de pessoa na tela
  **Pastas**) e quais endereços só se contam;
- em cada pasta lida, os e-mails novos desde a última volta (na primeira vez,
  os dos últimos 7 dias), abertos sem marcar como lido, em lotes de até 20;
  os anexos vão JUNTO (até 10 de 10 MiB; `.exe`, `.zip`, `.html` e afins,
  os grandes demais e os que não decifram ficam só no rastro);
- a **contagem** do topo de cada pasta (só ids): o que falta na Central vai de
  novo; o que mudou de pasta ou foi apagado no Tuta é avisado; de hora em
  hora uma pasta é conferida a fundo (30 dias) e, depois das 06:00, o dia
  anterior é fechado (a conciliação do dia na Central).

Um e-mail ou pasta que o conector não consegue decifrar é **contado** e o
resto continua; ele tenta de novo depois de 1 h, 6 h e 1x por dia.

| Estado (no sinal) | Quer dizer | O que fazer |
|---|---|---|
| online | lendo normalmente | nada |
| online / ilegivel | alguns e-mails/pastas não decifram (contados) | abrir esses no Tuta; avisar o dev se crescer |
| online / atrasado | o SDK está 3+ releases atrás | atualizar o conector logo |
| online / limitado | o Tuta pediu para esperar (429) | nada: volta sozinho |
| online / tuta_fora | Tuta fora do ar ou sem internet (3 voltas seguidas) | conferir a internet do Mac mini |
| login_required / sessao_caiu | a sessão foi encerrada ou expirou | `sair` e `entrar` de novo |
| error / versao_recusada | o Tuta recusa esta versão (474): a leitura PAROU | atualizar o conector |
| error / sem_v2 | o servidor ainda não tem o v2 da Central | publicar o servidor |

Dois Macs (ou dois processos) na mesma caixa: o segundo recebe "outro agente
está lendo esta caixa" e para de ler (sem duplicar nada).

### 5. Enviar (só por clique de pessoa)

Nada sai sem uma PESSOA ter clicado em responder no DaVinci (pela conversa do
/atendimento ou pela caixa). A Central guarda a fila e não entrega duas vezes;
o conector pega o trabalho (lease), faz no Tuta uma **RESPOSTA** ao e-mail
original pelo **alias que recebeu** e manda o recibo. Por cima da Central:

- **duas chaves**: a da caixa na Central (a pessoa liga na tela) e a deste
  Mac, em `~/Library/Application Support/davinci-tuta-conector/geral/chaves.json`:

  ```
  { "envio": true, "marcar_respondido": false }
  ```

  Sem o arquivo, tudo fica DESLIGADO.
- o remetente tem de ser EXATAMENTE um endereço ATIVO da conta no Tuta (nunca
  cai no endereço principal); sem o fio do original, não sai como e-mail
  novo; destinatário do próprio Tuta (tuta.com, tutanota…) não sai por aqui
  (responder pelo app);
- tetos: 30 por hora e 300 por dia por conta, e 100 por hora da conta inteira
  (contados pela pasta Enviados, que soma a equipe e os robôs);
- um diário local (`diario-envio.json`, só ids e passos) é gravado ANTES de
  cada passo; logo antes de enviar o conector pergunta de novo à Central se a
  chave da caixa continua ligada;
- falha antes do rascunho = "não saiu"; **depois do rascunho = incerto**:
  uma pessoa confere no Tuta e marca **Saiu / Não saiu** no DaVinci. O
  conector **nunca reenvia sozinho**. Se o Mac cair no meio, o envio fica
  "interrompido" no `estado` e a Central o marca incerto em 15 min;
- o trabalho pego há mais de **12 min** não vai para o SendDraft (`failed`
  "lease_vencido"): depois de 15 min uma pessoa pode ter marcado "Não saiu"
  e respondido de novo. O rascunho pode ficar no Tuta (apague à mão);
- o **diário barra o job repetido**: um id que já passou do "recebido" (ou já
  fechou, ou ficou no meio) nunca é enviado de novo — recibo `uncertain`
  "ja_visto_no_diario";
- a Central também segura na hora de entregar (lease): numa caixa da
  EMPRESA ou numa resposta que veio de conversa, o freio geral fechado, a
  pausa ou a volta ao modo teste não deixam sair o que já estava na fila, e
  o que passou de 2 h na fila vira `failed` "queued_timeout"; o sinal devolve
  `send_enabled: false` com o freio fechado ou a pausa (caixa da empresa).

### 6. O LaunchAgent (rodar sozinho)

Em `launchd/` há um por conta, **não instalado**:
`com.davinci.tuta-conector.geral.plist` e `…goslin.plist`. Eles rodam
`tuta-conector rodar --conta <conta> --contar` (o piloto começa só contando;
tire o `--contar` do plist quando a conciliação bater).

```
mkdir -p ~/Library/Logs/davinci-tuta-conector
cp launchd/com.davinci.tuta-conector.geral.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davinci.tuta-conector.geral.plist
```

Parar: `launchctl bootout gui/$(id -u)/com.davinci.tuta-conector.geral`
(nada se perde: o que a Central não confirmou vai de novo). Religar: o
`bootstrap` acima. Ver o log: `~/Library/Logs/davinci-tuta-conector/geral.log`
(sem conteúdo de e-mail; e-mail e token mascarados).

### 7. Sair (encerrar a sessão)

```
tuta-conector sair --conta geral
```

Encerra a sessão NO TUTA e apaga o item do Chaveiro. Se o Tuta não
responder, o item fica; encerre à mão no Tuta — **Configurações › Login ›
Sessões ativas › "DaVinci conector – Mac mini (geral)"** — e rode
`sair --conta geral --forcar`.

**Se desconfiar que o Mac foi invadido**: pare o LaunchAgent, rode `sair`,
confira em Sessões ativas que a do conector sumiu, **troque a senha do
Tuta** (a sessão guardada também prova a senha) e troque a chave da caixa
na Central.

---

## Para quem desenvolve

### Pastas

```
SDK_VERSAO              tag e commit OFICIAIS do Tuta (nunca editar a versão do SDK)
remendos/*.patch        o que mudamos no SDK, em ordem; AUDITORIA.md explica cada linha
remendos/so-testes/     consertos SÓ dos testes oficiais da tag (nunca no binário)
scripts/atualizar-sdk.sh  baixa a tag, aplica os remendos, compila
vendor/                 o SDK montado (fora do git)
src/                    o nosso código (config.rs = as decisões e as contas)
launchd/                um LaunchAgent por conta (não instalado)
tests/                  Tuta FALSO cifrado de verdade e Central FALSA (127.0.0.1)
build.rs                recusa compilar se vendor/ não bater com SDK_VERSAO + remendos
```

### Compilar do zero

```
cd apps/tuta-conector
scripts/atualizar-sdk.sh          # baixa a tag fixada, aplica os remendos, cargo build --release --locked
```

### Testes

```
cargo test                          # Tuta falso, Central falsa, Chaveiro em memória
cargo clippy --all-targets          # sem aviso no nosso código
scripts/atualizar-sdk.sh --testar-sdk --sem-compilar   # testes do PRÓPRIO SDK + cripto, com os remendos
```

- `tests/leitura.rs`: a leitura inteira contra o Tuta falso e a Central falsa
  (formato, códigos e links mascarados, aliases que só se contam, paginação,
  movido/apagado, ilegível, pânico do SDK, 401/429/474/5xx, 413, lote em parte,
  reinício no meio, pasta desconhecida, anexos, `--contar`, `--seco`, outro
  agente, servidor sem v2, varredura funda e fechamento do dia).
- `tests/envio.rs`: o envio (REPLY pelo alias, recusas sem rascunho, chave
  desligada antes de enviar, incerto que nunca reenvia, recibo que não chegou,
  tetos, processo que caiu no meio, o serviço juntando leitura e envio).
- `tests/ponta_a_ponta.rs` (ignorado no `cargo test`): as duas fases contra a
  Central DE VERDADE rodando neste Mac — quem orquestra é o roteiro Python que
  sobe a API num schema local, roda a ponte e confere o banco.
- O contrato tem amostras em `tests/dados/contrato-amostras.json`, validadas
  do lado Python pelos schemas e pelas rotas de verdade
  (`apps/api/tests/test_mail_v2_conector_contrato.py`). Mudou o formato:
  `ATUALIZAR_AMOSTRAS=1 cargo test --test leitura contrato` e rode o teste Python.

O teste `chaveiro_real_cria_le_e_apaga` mexe no Chaveiro de verdade e só
roda à mão: `cargo test -- --ignored chaveiro_real`.

### O contrato com a Central

- **v1** (do outro dev, congelado): `POST /api/mail/agent/{caixa}/heartbeat`
  (`{state, can_send, error_code}` → `{ok, send_enabled}`),
  `…/outbox/lease` (corpo vazio → até 5 respostas de pessoa),
  `…/outbox/{job}/receipt` (`{lease_token, status: sent|failed|uncertain, message_id, error_code}`).
- **v2** (nosso, `routers/mail_agent_v2.py`, mesma chave por caixa):
  `/v2/sync`, `/v2/ingest` (o `MessageIn` do v1 + o bloco `tuta`,
  `delivered_to`, `omitted_attachments`, `raw_headers`; resultado POR
  e-mail), `/v2/count`, `/v2/changes`.
- O `source_id` é `tuta:<lista>/<elemento>` do Mail (não muda quando o
  e-mail troca de pasta). O `message_id` é o do ConversationEntry, sem mexer:
  ele volta no lease como `in_reply_to` e é o `previousMessageId` da resposta.

### Atualizar o SDK (release nova do Tuta)

O Tuta recusa versões velhas (erro 474) umas 4 a 6 semanas depois. O
canário roda sozinho dentro do `ler`/`rodar` (na subida e 1x por dia): com 3
ou mais releases mais novas o sinal diz `atrasado`; com 474,
`versao_recusada` e a leitura para. Para atualizar:

1. Ache a tag e o commit: `git ls-remote --tags https://github.com/tutao/tutanota.git 'refs/tags/tutanota-*-release-*'`.
2. `scripts/atualizar-sdk.sh <tag> <commit>`. Se um remendo não aplicar:
   refaça-o e AUDITE de novo (`remendos/AUDITORIA.md`, seção 8).
3. `scripts/atualizar-sdk.sh --testar-sdk --sem-compilar`, `cargo test`,
   `cargo clippy --all-targets`.
4. Revise `git diff Cargo.lock` e os avisos de segurança (RustSec).
5. Troque o binário em `…/davinci-tuta-conector/bin/` e religue os
   LaunchAgents (o macOS pode pedir de novo a permissão do Chaveiro).

### Por dentro

- `src/leitura/caixa.rs`: o que se lê do Tuta pelo SDK oficial (pastas uma a
  uma, e-mail cru → chave de sessão → decifrado → ConversationEntry →
  MailDetailsBlob → arquivos; ver AUDITORIA, seção 9).
- `src/leitura/pacote.rs`: o `MessageIn` v2; o que a Central recusaria é
  arrumado aqui (endereços pelo mesmo critério do `EmailStr`, cabeçalhos numa
  linha, tetos).
- `src/texto/`: HTML → texto (código nosso, sem parser de terceiros) e a
  proteção de códigos e links (a mesma lista de `codigos.py` do DaVinci).
- `src/leitura/volta.rs`: o laço da leitura.
- `src/envio.rs`: o envio (lease → rascunho REPLY → "vai enviar" → SendDraft
  → recibo), com o diário. Escrever no Tuta SÓ aqui.
- `src/servico.rs`: o `rodar` de uma conta (leitura + envio no mesmo processo).

### Regras que os testes guardam

- A leitura (`src/leitura.rs` e `src/leitura/`) não cita NADA que grava no
  Tuta (`tests/so_leitura.rs`, por texto) e, rodando, só faz GET no Tuta (fora
  o token de LEITURA de arquivo). O envio é outro módulo e não usa a leitura.
- Nenhum websocket: nem no código, nem no SDK montado, nem no Cargo.lock.
- Segredo só no Chaveiro (o token do lease só na memória); nunca em arquivo,
  log, argumento ou variável de ambiente. O log mascara e-mail/token.
- O cliente HTTP do Tuta só fala com `app.tuta.com` e `*.tuta.com`; o do
  DaVinci só com a URL configurada; o do canário só com `api.github.com`.
  HTTP só para 127.0.0.1 (testes).
- Em disco, só ids e cursores; nem assunto, nem endereço, nem corpo.
- Pânico do SDK é isolado (`sem_panico`) e vira "ilegível", sem derrubar o processo.

### Deploy do DaVinci

Nada do deploy usa esta pasta: os Dockerfiles e o compose constroem só
`apps/api`, `apps/web` e `infra/*`. `target/` e `vendor/` estão no
`.gitignore` daqui.

### Licença

O SDK do Tuta é GPL-3 e os remendos vêm da tutabridge (GPL-3). Para uso
interno do DaVinci não é preciso publicar nada; se um dia o programa for
entregue a outra empresa, vai junto com o código, sob GPL-3.
