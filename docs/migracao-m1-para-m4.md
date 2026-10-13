# Mudança dos robôs do Mac M1 (Santiago) para o Mac mini M4 (Makisa)

Levantamento feito em 13/10/2026, só lendo (nada foi mudado nos dois Macs).
Objetivo: ficar com um computador só. Tudo que roda no M1 passa a rodar no
mini, **igual ao que está hoje**, e o M1 fica livre.

## Andamento

- **13/10 14:00 — Passo 0:** Vinicius pedindo ao dono da equipe do AdsPower os
  grupos Administrativo + Contabilidade pro login do mini. Claude do mini vai
  pro login dele.
- **13/10 14:00 — Passo 1 FEITO** (nada ligado, M1 intocado): Node v26.10.0 e
  Claude Code 2.1.284 em `~/.local` do mini; as três pastas copiadas pra
  `~/DaVinci` (com senhas, `node_modules`, estado e registros; sem `debug/` e
  `tmp/`); histórico do Hermes em `historico-hermes/`; os três despertadores
  prontos dentro de cada pasta (`*.mini.plist`), **fora** de
  `~/Library/LaunchAgents` de propósito (se o mini reiniciasse, subiriam
  junto com o M1); confiança da pasta da IA ajustada e provada (`claude -p`
  rodou o comando permitido e negou ler o `.env`); os dois executores
  compilam com o Node novo; atalho na Mesa. Faltam só os testes que abrem
  perfil, que ficam pro passo 3.
- **13/10 14:30 — Passo 2 FEITO** (código de convivência, testado sem tocar em
  loja): a IA só mexe no perfil que ela abriu (`estado/perfis-abertos.json`;
  `tela`/`abrir` param com "PERFIL EM USO" se for de outro; `fechar` só fecha
  o dela) — commit `769c77d2`, já nos dois Macs (no M1 entrou entre rodadas,
  sem perfil aberto; backups `*.bak-20261013`). Na Denúncia, o `servidor.py`
  vivo já dava `PerfilOcupado` pra perfil aberto por outro; o remendo foi só no
  `Fiscalizacao/perfil_aberto.py`: a anotação "deixei aberto" guarda a porta da
  sessão e, se a sessão trocou (outro abriu depois), ele não conecta nem fecha
  (backup `_arquivo/backups/perfil_aberto_20261013_antes_sessao.py`). Decisão
  dele: o mini fica com o login **Viena** do AdsPower, que ganha os grupos
  Administrativo + Contabilidade (ainda não concedidos às 14:30).
- **13/10 14:26 — mudança: o mini usa o login do Cairo** (= o do M1 e deste
  Mac). 112 "Uranyx - Amazon EUA" movido pro grupo Lojas (API do mini, OK
  dele). **14:36 ele logou o Cairo no mini e o M1 caiu no mesmo minuto**
  ("Login failed"): o Cairo não fica em dois Macs ao mesmo tempo. O AdsPower
  do mini fechou na troca; reaberto por ssh, API em 10 s, 151 perfis visíveis,
  todos os perfis-chave dos robôs. **Ele escolheu a Opção B (seguir agora)**:
  o M1 fica sem AdsPower e os três robôs vão pro mini hoje, um por vez.
  Anotações "deixei aberto" da Denúncia limpas (backup). Aquecer rodando em
  `~/DaVinci/_migracao/` (57 perfis). Voltar atrás = relogar o Cairo no M1
  (derruba o mini) e religar os três lá.
- **13/10 14:56–15:04 — VIRADA FEITA, um robô por vez (OK dele em cada):**
  leitor 14:56 (v1.4.0, mesmas 20 lojas Shopee / 27 ML com perfil), executor
  14:58 (v1.2.0, Melhor Envio + TikTok; os 502 na subida eram um deploy no
  servidor às 14:54, depois lease e sinal de vida 200), IA 15:04 (estado,
  registros, decisões e conversas copiados com o M1 parado). **Os três robôs
  rodam no mini.** No M1 ficaram os despertadores descarregados (reserva).
  Aquecimento: Melhor Envio logado; Shopee quase todas logadas; TikTok só
  Inova e Poofy logadas — as outras 9 são sessões vencidas (a IA avisa no
  Threema quando precisar de login; o leitor grava no chamado).
- **13/10 15:10–15:45 — fechamento do dia (ele saiu; "resolver tudo e deixar
  só as telas de login"):** executor renomeado pra `makisa` na Ouvidoria
  (`AGENT_NAME`, backup `.env.bak-20261013-santiago`); textos "Mac
  Santiago" → "Mac mini da Makisa" (aviso Threema da IA, LEIAME, READMEs,
  despertadores do repositório, commit `3dbabc3e`, publicado no
  `origin/main`); **o deploy ficou pra ele** (barrado pelo modo automático:
  `ssh davinci-prod 'sudo davinci-deploy'`, só texto). IA batendo a cada 10
  min no mini (15:14, 15:24…), sem caso ainda; executor já recebeu a 1ª
  tarefa (senha TikTok Mini 294738 → "precisa_login", espera o login do
  perfil 129); leitor passando sem erro, sem leitura vencida ainda.
  Aquecimento terminado: **42 logadas, 14 deslogadas, 1 bloqueio**.

### O que precisa de login (pela tela do mini: abrir o perfil, logar, FECHAR)

| Plataforma | Perfis |
|---|---|
| TikTok | **Mini - TikTok (129)** — tem tarefa de senha esperando (294738); Barbosa 131; JLAS 123; Injox 140; Atv 137; Eron 133; Uranyx 204; VR 211; Mini - ml sh tk (47, Firefox, a IA usa pra contestação) |
| Shopee | Aguiar 155 (presa em verificação desde 08/10); Minas 153; VR 210; ATV - sh tk te 159 (cópia da Contabilidade) |
| Mercado Livre | Vita 66 (pede identificação); **Nexus 83** (não é login: bloqueio antirrobô "experience-shield" — abrir e passar a verificação) |

Tudo o mais (42 perfis: Melhor Envio 70, as 15 Shopee e as 24 ML restantes,
Inova e Poofy TikTok) veio logado pela nuvem do AdsPower.

### Próximos dias

- Observar: Ouvidoria › Robôs (Vigia Robô Melhor Envio deve mostrar
  `logistica:makisa`; Vigia Robô Leitura), `registros/` da IA e `logs/` dos
  executores no mini (`~/DaVinci/...`), e a aba Robô da Denúncia (se as
  rodadas dela sofreram com os robôs do DaVinci na mesma máquina —
  "PerfilOcupado" nas contas da Réplica é o esperado, não erro).
- Rodar o deploy pendente (texto do aviso Threema).
- Nobreak pro mini.
- M1: fica duas semanas como reserva (despertadores descarregados, AdsPower
  deslogado). Voltar atrás = logar o Cairo no M1 (derruba o mini) e
  `launchctl load` dos três lá. Depois das duas semanas, apagar `~/DaVinci`,
  `~/.hermes`, prints em `debug/` (mostrar a lista antes).
- A pasta `~/DaVinci/_migracao` do mini (ferramenta de aquecer + resultado)
  pode ser apagada quando não servir mais.

## 1. Resposta curta

- **Dá pra fazer.** O mini aguenta (M4, 16 GB, ~85 % parado) e já está no
  mesmo escritório, na mesma rede e na mesma internet do M1 (os dois saem pelo
  mesmo endereço, 152.249.9.113 — em 02/10 eu disse que eram internets
  diferentes; estava errado, era o endereço mudando).
- **Três coisas o mini não tem** e precisam ser instaladas: o Node (a base
  dos dois executores), o Claude Code (a IA de Chamado) e as três pastas dos
  robôs com as senhas deles.
- **Trocar o login do AdsPower do mini pelo do M1 tem risco pro robô de
  Denúncia** (item 4). O caminho seguro é **manter o login do mini e dar a ele
  os grupos que faltam** no AdsPower. Com isso nada cai.
- **Dois robôs no mesmo Mac disputam as mesmas lojas no AdsPower.** Hoje
  isso não acontece porque estão em Macs diferentes. Precisa de duas mudanças
  pequenas de código (uma na IA de Chamado, uma no robô de Denúncia) antes da
  virada (item 5).
- A mudança é feita **um robô por vez**, com um dia de observação entre
  eles. O M1 fica guardado duas semanas como reserva.

## 2. O que roda hoje no M1

| Robô | Pasta no M1 | Como acorda | O que faz | O que precisa |
|---|---|---|---|---|
| **Executor da Logística** (`com.davinci.executor`) | `~/DaVinci/executor-logistica` | sempre ligado, pergunta ao DaVinci a cada 15 s | "Suspender entrega" no Melhor Envio (perfil 70) e pedido de senha no chat da TikTok (perfis "<Loja> - Tiktok") | Node, AdsPower, `.env` (senha do Marketing, perfil 70, `EXECUTOR_FILAS=melhorenvio,tiktok`, nome `santiago`) |
| **Leitor de chamados** (`com.davinci.executor-leitura-chamado`, v1.4.0) | `~/DaVinci/executor-leitura-chamado` | sempre ligado, pergunta a fila a cada 10 min | lê o Histórico da Solicitação e o Portal da Shopee (20 lojas), lê e **responde** as consultas do ML (27 lojas) | Node, AdsPower, `.env` (senha do leitor, senha das "mãos do ML", `LEITURA_ML=real`, `RESPONDER_ML=real`, perfil extra Marquezini) |
| **IA de Chamado** (`com.davinci.ia-de-chamado`) | `~/DaVinci/ia-de-chamado` | despertador a cada 10 min (`rodada.sh`) | decide os chamados (Opus 5.5 / Sonnet 5.5), mexe na tela da loja pelo AdsPower, abre consulta no ML, manda evidência na Shopee | Claude Code + login (`.env.claude`), Node (usa o `node_modules` do leitor), Python do sistema, `.env` (senha da IA), `claude/` (confiança da pasta + conversas), `estado/` (casos já vistos), `decisoes.jsonl`, `registros/` |
| Hermes (`ai.hermes.gateway`) | `~/.hermes`, `~/DaVinci/cerebro` | gateway ligado, mas as duas tarefas estão **pausadas** desde 29/09 | nada (foi substituído pela IA de Chamado) | **não vai pro mini.** Só o histórico (`cerebro/decisoes.jsonl`, `tela.jsonl`, `tela_logs`, 2 MB) vai pra `historico-hermes/` |

Não são robôs e não migram: o Claude (app) e o Google Chrome do M1 (uso de
pessoa pelo Compartilhamento de Tela).

Fatos do M1 que importam:

- Node = o do Hermes (`~/.local/bin/node → ~/.hermes/node`, **v26.10.0**).
  Apagar o Hermes sem instalar outro Node derrubaria os executores.
- Claude Code 2.1.284 em `~/.local/bin/claude`. Python do sistema 3.9.6
  (o mini tem o mesmo). As ferramentas da IA usam só a biblioteca padrão.
- O leitor e a IA estão **iguais ao repositório**. O executor da Logística
  está com o código de 29/09; o repositório ganhou depois a "Conferência
  Shopee" (06–07/10), que só liga se estiver no `EXECUTOR_FILAS`. Pra ficar
  igualzinho, copiamos as pastas **do M1**, não do repositório.
- O despertador da IA grava `estado/servidos.json` e `decididos.json` a
  cada rodada: a cópia final desses arquivos tem que ser feita **com o M1
  parado**, senão a IA do mini repete casos.
- Ninguém no M1 recebe conexão de fora (todos perguntam ao DaVinci). Nada no
  servidor amarra os robôs a um endereço ou a um Mac: as senhas são as
  mesmas em qualquer máquina. Nenhuma mudança no servidor é necessária.
- `caffeinate` nos despertadores; FileVault ligado (reinício precisa de
  alguém digitar a senha). O mini é melhor nisso: FileVault desligado,
  entra sozinho na conta `makisatradingltda` depois de uma queda de energia.

## 3. O que o mini tem e o que falta

| | M1 (Santiago) | mini (Makisa) |
|---|---|---|
| Máquina | MacBook Air M1, 8 GB | Mac mini M4, 16 GB, 107 GB livres |
| macOS | 27.0 | 27.0 (igual) |
| Rede | Wi-Fi, 192.168.15.9, mesma saída | Wi-Fi, 192.168.15.11, mesma saída |
| AdsPower | 8.9.23, login `h19v0sf` (vê Administrativo, Lojas, Contabilidade — 150 perfis) | 8.7.23, login `h19v0u5` (vê Lojas, Amzon EUA, Viena — 118 perfis) |
| Navegadores do AdsPower já baixados | chrome 152/153, flower 138/141/144/147/150/153 | chrome 150–153, flower 138/147/150/153 (**faltam flower 141 e 144**: baixam na 1ª abertura de um perfil que use essas versões, ~5 min cada) |
| Node | v26.10.0 (do Hermes) | **não tem** |
| Claude Code (linha de comando) | 2.1.284 | **não tem** (só o app Claude, conta do Leonardo) |
| Python | 3.9.6 | 3.9.6 |
| Tailscale | 100.116.210.123 | 100.126.56.64 (`ssh mac-makisa`) |
| Entra sozinho ao ligar | não (FileVault) | sim |
| Pasta `~/DaVinci` | os 3 robôs | não existe (a pasta "DaVinci" da Denúncia fica na Mesa, dentro de `Denuncias/Ecomerce/`) |

Regra do mini que já conhecemos: **programa do despertador (launchd) não
consegue ler a Mesa**. Por isso os três robôs vão pra `~/DaVinci` (na pasta
pessoal, como no M1), com um atalho na Mesa.

## 4. AdsPower: trocar o login do mini pelo do M1?

> **Atualizado 13/10 15:00 — decisão: o mini passa a usar o login do Cairo.**
> Descobri que o login do M1 **é** o Cairo (o mesmo deste Mac do Vinicius:
> as pastas de perfil dos dois têm o código `h19v0sf`). E o risco que eu
> apontei abaixo ficou bem menor: o perfil 50 e o 148 **já abriram sob o
> Cairo neste Mac** (têm pasta aqui), e as pastas mostram que o AdsPower
> sincroniza pela nuvem cookies, local storage, IndexedDB, histórico e
> configurações de extensão — não só cookies. Ou seja, trocando o mini pro
> Cairo, o perfil 50 vem da nuvem logado, como vem neste Mac.
>
> **O que o Viena tem que o Cairo não tem:** só o perfil **112 "Uranyx -
> Amazon EUA"** (grupo Amzon EUA) e o grupo "Viena", vazio. Todo o resto que
> o Viena vê (os 117 de Lojas) o Cairo vê também, e o Cairo ainda vê
> Contabilidade (29) e Administrativo (3). Pra "trazer tudo": mover o 112
> pro grupo Lojas (pela API do mini, enquanto ainda está no Viena) ou dar ao
> Cairo o grupo Amzon EUA em Equipe › Membros.
>
> **Dados no disco do mini sob o Viena:** 21 perfis, 8,1 GB (50 com 2,2 GB,
> 148 com 1,2 GB, as contas da Réplica, 118 Mini Shopee, 87, 81, 155, 94,
> 113, 116, 160, 195, 83, 135, 164, 166, 32). Ficam no disco (não se apaga
> nada) e os mesmos perfis são usados hoje do M1/deste Mac sob o Cairo, logo
> vêm pela nuvem.
>
> **Cuidados da troca:** (1) fazer com o robô de Denúncia parado e nenhum
> perfil aberto no mini (a nuvem recebe o estado na hora de fechar); (2) a
> senha do Cairo é digitada por você no mini (Compartilhamento de Tela);
> (3) o Cairo fica em **três** Macs ao mesmo tempo até o M1 aposentar — se o
> plano do AdsPower limitar os aparelhos, o M1 pode ser desconectado na hora
> em que o mini entrar: eu confiro a API do M1 logo depois; (4) depois da
> troca, apagar as anotações "deixei aberto" da Denúncia
> (`_logs/perfis_deixados_abertos.json`) e rodar a checagem (passo 0) pra
> ver as quatro lojas do perfil 50 logadas.
>
> O texto abaixo é o levantamento original (ainda vale como explicação).

### O que cada login enxerga

| Grupo | login do M1 (`h19v0sf`) | login do mini (`h19v0u5`) | quem usa |
|---|---|---|---|
| Lojas (117) | vê | vê | todo mundo |
| Contabilidade (29) | vê | **não vê** | IA de Chamado e leitor: 110 "JLAS 2 - ml", 47 "Mini - ml sh tk", 159 "ATV - sh tk te"… |
| Administrativo (3) | vê | **não vê** | 195 "teste makisa 32" (o `SERIAL_MOVEL` do robô de Denúncia — hoje ele nem consegue ver) |
| Amzon EUA (1) | **não vê** | vê | 112 "Uranyx - Amazon EUA" — nenhum robô usa (pessoa) |
| Viena (0) | não vê | vê | vazio |

Os dois logins são da **mesma equipe** do AdsPower (os grupos têm os mesmos
números nos dois Macs). A diferença é só a permissão de grupo de cada membro.

### O que o robô de Denúncia usa

Perfil 50 "MAKISA" (Lojas), 148 "Luno - Shopee" (Lojas), as contas da
Réplica G1/G2 (93 Mega ML, 155 Aguiar Shopee, 137 Atv TikTok, 78 KIA ML,
146 Vortan Shopee, 131 Barbosa TikTok — todos em Lojas) e o 195 (que ele não
vê hoje). A API local do mini **não exige chave** (testei com uma chave
errada e respondeu normal). O endereço (`local.adspower.net:50325`) é o
mesmo com qualquer login.

### Por que trocar o login tem risco

O AdsPower guarda os dados de cada perfil no disco **em uma pasta por
login**. No mini, a pasta do perfil 50 é
`k13kseik_h19v0u5_flower` (2,2 GB) e a do 148 é `k1dofott_h19v0u5_flower`
(1,2 GB); as contas da Réplica também têm pasta com o `_h19v0u5`. No M1 as
pastas terminam em `_h19v0sf`.

Se o mini entrar com o login do M1, o AdsPower passa a usar pastas novas
(`…_h19v0sf`) e **ignora esses dados**. O que sobrevive é só o que a nuvem
do AdsPower sincroniza (normalmente os cookies). Há evidência de que essa
sincronização funciona (Vortan 146 e Barbosa 131 são abertas nos dois Macs,
com logins diferentes, e continuam logadas), mas o perfil 50 é o coração da
Denúncia e tem quatro lojas logadas (ML, Shopee, TikTok, Amazon): se uma
delas pedir login/código de novo, a rodada para até alguém resolver — e a
Shopee no 50 já vive em "muro" de verificação.

### Recomendação

**Não trocar o login.** Em vez disso, no AdsPower (quem administra a
equipe — Equipe › Membros › editar o membro do mini), **dar ao login do mini
os grupos Administrativo e Contabilidade**. Com isso:

- o robô de Denúncia continua exatamente como está (mesmas pastas, mesmos
  logins, e passa a ver o 195);
- os três robôs do M1 passam a ver os perfis que usam (110, 47, 159 etc.).

Se mesmo assim quiser trocar o login: fazer com o robô de Denúncia parado,
abrir o 50 e o 148 à mão e conferir as quatro lojas. Se algo cair, voltar
pro login do mini (as pastas antigas continuam no disco; só confirmar que
o AdsPower não as apagou ao sair).

### Primeira abertura no mini

Perfis que nunca abriram no mini (Melhor Envio 70, 110, 47, 123, 129, 140,
119, 136, 159 e a maioria das 20 lojas Shopee / 27 ML) nascem com pasta
nova no mini e dependem da sincronização da nuvem pra vir logados — igual
ao que já acontece no M1 toda vez que a IA abre uma loja pela primeira vez
(o M1 só tem 31 pastas de perfil pra 150 perfis, e funciona). O mini já tem
21 pastas. Mesmo assim, o plano "aquece" os perfis-chave antes da virada
(abre uma vez, confere o login, baixa o navegador que faltar).

## 5. Conflitos de dois robôs no mesmo Mac

O que **não** é problema (conferido no código):

- **Reiniciar o AdsPower:** ninguém mata o AdsPower. Os executores e o robô
  de Denúncia só dão `open -a` quando ele está fechado. (Em 02/10 eu falei
  em "reiniciam o AdsPower sozinhos"; era só reabrir.)
- **Limite de ~1 pedido/segundo da API local:** os dois lados já tentam de
  novo quando vem "Too many request per second".
- **Perfil 50 (MAKISA):** só a Denúncia usa.
- **Melhor Envio (perfil 70):** só o executor usa.

O que **é** problema — as mesmas lojas em dois robôs:

| Loja | Denúncia | Robôs do M1 |
|---|---|---|
| 148 Luno - Shopee | prints da Shopee | leitor (chamados da Luno) |
| 146 Vortan, 155 Aguiar (Shopee) | Réplica | leitor, IA |
| 93 Mega, 78 KIA (ML) | Réplica | leitor (consultas do ML), IA |
| 131 Barbosa, 137 Atv (TikTok) | Réplica | executor (senha no chat), IA |

Hoje, em Macs diferentes, o AdsPower da outra máquina diz "em uso em outro
computador" e cada robô espera. **No mesmo Mac** o AdsPower diz só "Active",
e aí:

- o **leitor** e o **executor** pulam (bom: "aberto = alguém usando");
- a **IA de Chamado** conecta na janela aberta, seja de quem for, e no fim
  **fecha o perfil** (`tela.mjs` / `adspower.py`) — derrubaria a Réplica no
  meio;
- o **robô de Denúncia** (`perfil_aberto.py` → `servidor.abrir`) também
  conecta na janela aberta e, se não conseguir, **fecha e abre de novo** —
  derrubaria a IA no meio de uma conversa com atendente.

### Mudanças de código necessárias (antes da virada)

1. **IA de Chamado** (`apps/ia-de-chamado/ferramentas/tela.mjs` e
   `adspower.py`): guardar a lista dos perfis que *ela* abriu
   (`estado/perfis-abertos.json`). Perfil "Active" que não está na lista =
   "em uso por outro robô ou pessoa" → a IA não conecta, decide `esperar`
   e tenta na próxima rodada. Só fecha o que está na lista. O "deixar
   aberto pra pessoa resolver o captcha e voltar depois" continua igual
   (está na lista).
2. **Robô de Denúncia** (`Fiscalizacao/perfil_aberto.py` no mini): mesma
   regra — só conectar num perfil aberto se foi ele que deixou aberto
   (ele já tem essa memória pros captchas); senão, `PerfilOcupado` e a
   conta fica pra próxima rodada (a Réplica já trata esse erro). Nunca
   fechar perfil que não abriu.
3. **Opcional, depois de uma semana:** o robô de Denúncia mexe com mouse de
   verdade no Tuta (código do SEI, leitura de e-mails) e em partes do
   acesso ao SEI. As janelas das lojas que os robôs do M1 abrem aparecem na
   frente e podem fazer um clique desses falhar (ele já confere se o ponto
   é do Tuta antes de clicar, então o risco é a etapa falhar e repetir, não
   clicar em lugar errado). Se isso aparecer nos registros, criamos uma
   "trava da mesa": enquanto a Denúncia usa o Tuta/Safari, os outros três
   esperam pra abrir loja.

## 6. Plano, passo a passo (um OK por passo)

### Passo 0 — decisões suas

- AdsPower: dar os grupos ao login do mini (recomendado) **ou** trocar o
  login. Quem administra a equipe no AdsPower faz isso.
- Claude do mini: você vai trocar pro seu login. Não afeta os robôs (a IA de
  Chamado usa a chave própria do `.env.claude`), mas o projeto "Fiscal", as
  12 tarefas agendadas pausadas e o "Vigia do canal de problemas" ficam na
  conta do Leonardo (não somem; só deixam de aparecer no mini).
- Nome do executor no DaVinci: continua `santiago` (igualzinho) ou vira
  `makisa` (Ouvidoria › Robôs mostra `logistica:<nome>`). Sugiro `makisa`
  na virada.
- Nobreak pro mini: com tudo lá, uma queda de energia para Denúncia,
  chamados, Melhor Envio e leitura ao mesmo tempo (09:18 de 02/10 já parou
  a Denúncia).

### Passo 1 — preparar o mini (sem tocar no M1, nada liga ainda)

1. Node v26.10.0 (o mesmo do M1) em `~/.local/node`, com `node`, `npm`,
   `npx` em `~/.local/bin` (sem senha de administrador; é o que o Hermes fez
   no M1). Está disponível em nodejs.org (conferido).
2. Claude Code em `~/.local/bin/claude` (instalador oficial, sem senha).
3. Copiar do M1 pro mini (passando por este Mac, nada fica aqui):
   `~/DaVinci/executor-logistica`, `~/DaVinci/executor-leitura-chamado` e
   `~/DaVinci/ia-de-chamado` — com `.env`, `.env.claude`, `node_modules`
   (mesma arquitetura, funciona), `estado/`, `registros/`, `decisoes.jsonl`,
   `claude/` (480 MB são conversas antigas de 30 dias; opcional). **Sem**
   `debug/` (prints de clientes) e sem `tmp/`.
4. `historico-hermes/` recebe o que sobrou do Hermes (2 MB).
5. Três despertadores (`.plist`) com os caminhos do mini
   (`/Users/makisatradingltda/…`), copiados pra `~/Library/LaunchAgents`
   **sem carregar**.
6. A confiança da pasta da IA: o `claude/.claude.json` guarda a confiança
   pelo caminho `/Users/santiagogeral/DaVinci/ia-de-chamado`; no mini o
   caminho muda, e **sem isso a lista de permissões da IA é ignorada**.
   Ajustar e conferir com um comando de teste.
7. Atalho `~/Desktop/DaVinci` → `~/DaVinci` (como no M1).
8. Testes secos, sem mandar nada: leitor `--teste-pedido` numa devolução
   Shopee e `--teste-ml` numa consulta; executor `--teste-tiktok` (sempre
   seco) e o Melhor Envio só abrindo a lista; IA: uma chamada `claude -p`
   que roda uma ferramenta e prova que a permissão vale.

### Passo 2 — código de convivência (item 5)

IA de Chamado (no repositório, testada aqui e copiada pros dois Macs) e
`perfil_aberto.py` da Denúncia (no mini, com backup em `_arquivo/backups`,
como sempre). Cada um testado em seco com um perfil aberto à mão.

### Passo 3 — aquecer os perfis no mini

Com o AdsPower do mini já vendo todos os grupos: abrir uma vez, fora do
horário da Denúncia, os perfis que os robôs do M1 usam (70 Melhor Envio,
110, 47, 123, 129, 140, 119, 136, 159 e as lojas Shopee/ML com chamado
aberto), conferir que estão logados e fechar. É aqui que o navegador
flower 141/144 baixa, e é aqui que se descobre loja deslogada **antes** da
virada (o M1 vê "em uso em outro computador" e espera; não atrapalha).

### Passo 4 — a virada, um robô por vez (do mais simples ao mais delicado)

Para cada robô: (a) desligar o despertador no M1 (`launchctl unload`),
(b) copiar o estado final (`estado/`, `registros/`, `decisoes.jsonl`,
`logs/`), (c) carregar o despertador no mini, (d) conferir no mesmo minuto.

1. **Leitor de chamados** — conferir: linha "rodando — pergunta a fila a
   cada 10 min" no log do mini; uma leitura real entrando no chamado;
   Ouvidoria › Robôs sem "Vigia Robô Leitura" reclamando. Observar 1 dia.
2. **Executor (Melhor Envio + TikTok)** — conferir: heartbeat novo na
   Ouvidoria ("Vigia Robô Melhor Envio" vê o robô), uma tarefa `tiktok_senha`
   em seco ou real. Observar 1 dia.
3. **IA de Chamado** — IA desligada na aba IA de Chamado durante a troca;
   copiar `estado/` com o M1 parado; ligar no mini; primeira rodada
   acompanhada pelo `registros/<dia>.log` e pelo histórico do chamado.
   Observar 2 dias.

### Passo 5 — depois da virada

- Trocar "Mac Santiago" por "Mac mini" nos textos que as pessoas leem: o
  aviso Threema da IA (`chamados_ia_aviso.py`: "A loja ficou aberta nessa
  tela no Mac Santiago"), `CLAUDE.md` e `LEIAME.md` da IA, READMEs dos
  executores. O Cairo passa a resolver captcha no mini
  (`vnc://100.126.56.64`).
- Uma semana de observação: Vigias da Ouvidoria, `registros/` da IA, logs
  dos executores, aba Robô da Denúncia (ver se as rodadas dela sofreram).

### Passo 6 — aposentar o M1

Duas semanas com os despertadores descarregados (reserva: voltar é
`launchctl load` no M1 e `unload` no mini). Depois: apagar `~/DaVinci`,
`~/.hermes`, prints em `debug/`, sair do AdsPower e do Claude. Mostrar a
lista antes de apagar.

## 7. O que precisa de você

| O quê | Por quê |
|---|---|
| Grupos no AdsPower pro login do mini (ou a troca de login) | sem isso o mini não vê os perfis 110, 47, 159… |
| Login do Claude no app do mini (você disse que troca) | não afeta os robôs; é só pra você |
| OK em cada passo e nas duas mudanças de código | regra combinada |
| Nobreak pro mini | um só computador = um só ponto de falha |
| Estar por perto na virada da IA (captcha/loja deslogada) | a IA para e avisa no Threema |

## 8. Como voltar atrás

Cada robô volta sozinho: `launchctl unload` do despertador no mini e
`launchctl load` no M1 (as pastas do M1 ficam intactas até o passo 6). O
estado que muda de lado é só `estado/` da IA — copiar de volta. Se o login
do AdsPower tiver sido trocado, voltar pro `h19v0u5` no mini.
