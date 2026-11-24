# Robô de mudanças do DaVinci (Sistema › Mudanças)

O chefe pede uma mudança no próprio sistema pelo Claude dele (`pedir_mudanca` no
conector). Este robô, no Mac do Eduardo, **faz** a mudança numa cópia separada do código e
manda o diff para a tela **Sistema › Mudanças**, que só o Eduardo e o Thorfinn veem.
**Aprovado**, o robô roda as conferências e **publica** (push no main + `davinci-deploy` no
servidor). Nada do pedido roda — nem teste — antes do Aprovar; nada vai para o ar sem ele.
Exceção (0509, 24/11/2026): os pedidos de quem tem aprovação automática (o Harry) são
aprovados pelo próprio DaVinci quando o robô entrega — menos os que mexem em permissão,
login, Histórico ou na própria trava (`arquivos_sensiveis`), que esperam o clique.

## Como funciona uma rodada (a cada 1 min, `rodada.py`)
1. Pergunta ao DaVinci se há trabalho (`/api/sistema-mudancas-robo/pegar`) — sem IA.
2. **fazer**: cria a worktree `~/davinci-mudancas/M-N` (branch `mudanca/M-N`) do `origin/main`,
   roda o Claude Code sem tela SÓ com ferramentas de arquivo (Read/Edit/Write/Glob/Grep — sem
   terminal, sem rede, sem MCP, sem as configurações `.claude/` do projeto), confere os
   arquivos (lista proibida: banco/migration, permissão, infra, robôs, dependências e
   configuração de ferramentas, `conftest.py`, `setup.py`, `*.pth`; binário; até 12 arquivos e
   400 linhas), faz o commit e manda o diff. **Nada roda nesta etapa.**
   Pedido que foge das regras volta como **Precisa da equipe**.
3. **publicar** (só depois do Aprovar): cria uma cópia NOVA a partir do commit aprovado (nada
   do que ficou na pasta conta), põe em cima do main conferido com o servidor (tem de continuar
   o mesmo patch), instala as dependências DO MAIN (sem construir o projeto) e roda as
   conferências NA CAIXA: ruff de erro grave, a API carregando, pytest dos testes mexidos (num
   Postgres descartável), a tela compilando (.vue/.ts mexidos) e node dos testes de tela.
   **Falhou: não publica** (fica "Falhou" com o motivo; detalhes em `registros/`). Passou:
   exige que o main inteiro já esteja NO AR (não só no git do servidor — vale o que o
   `davinci-deploy` registrou ou imagens mais novas que o HEAD), sem build rodando (nem
   `docker compose` na mão), disco ≥ 6 GB (faltando, apaga SÓ o que não interfere: cache de build
   sem uso e imagens sem nome — nunca dados, volumes, banco, backups) e a trava livre — confere de novo logo antes do
   push; faz o push e roda o `davinci-deploy` desacoplado da conexão (que recusa mexer em
   infra e roda a migration). Ocupado/disco/mudança de outra pessoa ainda fora do ar: volta
   para a fila e tenta de novo em 10 min (o DaVinci avisa quem aprova a cada ~1 h, e na tela dá
   para **Cancelar aprovação**). Deploy que FALHA com a mudança já no main: não repete — fica
   "Falhou" pedindo uma pessoa (corrigir ou `git revert`). Mudança desfeita no main por uma
   pessoa: "Falhou", nunca "no ar".
4. Um trabalho por vez; avisa que está vivo a cada 5 min (senão o DaVinci devolve o pedido;
   recomeçar conta tentativa — 3 no máximo, e 3 h no total). Erro do ambiente (GitHub fora,
   login do Claude) não gasta tentativa; o pedido só falha se passar 12 h assim.

## Perguntas (P-N): "como funciona tal processo?" (17/11/2026)
O chefe também pode **perguntar** pelo Claude dele (`perguntar_sobre_o_sistema`). O robô cria
uma cópia do `origin/main` em `~/davinci-mudancas/P-N`, roda o Claude Code **só com
Read/Glob/Grep** (sem editar, sem terminal; regras em `REGRAS_PERGUNTA.md`), troca segredo com
cara conhecida por `[oculto]` e manda a resposta. **Ela vai direto para quem perguntou**, sem
aprovação (decisão do Eduardo: "a resposta de como funciona e o código pode ir para ele"). O
Claude dele lê com `ver_resposta_do_sistema`. Nada é commitado; a cópia é apagada no fim. Na
tela Sistema › Mudanças as perguntas aparecem com a etiqueta "Pergunta", só para histórico.

## Instalar (uma vez)
1. No servidor, `MUDANCAS_AGENT_TOKEN=<token longo aleatório>` no `/opt/davinci/.env` e
   `up -d` (o deploy normal) — sem ele as rotas do robô ficam fechadas.
2. Neste Mac: `./instalar.sh --sem-carregar` e preencher `~/DaVinci/mudancas-robo/.env`
   (o mesmo `MUDANCAS_AGENT_TOKEN`).
3. Login do Claude do robô (uma vez, vale 1 ano): num Terminal rodar o `setup-token` do
   Claude Code e depois `./guardar_token.sh` para colar o token sem ele aparecer.
4. `./instalar.sh` (carrega o despertador).

## Onde olhar
- `~/DaVinci/mudancas-robo/registros/AAAA-MM-DD.log` — o que cada rodada fez.
- `registros/M-N-claude.json` (o que o Claude respondeu) e `registros/M-N-deploy.log`.
- `estado/ultima-rodada` — prova de vida do despertador.
- Parar: `launchctl unload ~/Library/LaunchAgents/com.davinci.mudancas-robo.plist`.

## Segurança (3 revisões em 17/11/2026)
- **Nenhum código do pedido roda antes do Aprovar.** O Claude do robô só lê e edita arquivos
  da pasta do pedido (sem terminal; a leitura fora da pasta é bloqueada —
  `blockReadsOutsideWorkingDirectories`); cada pedido usa uma configuração do Claude NOVA e
  sem memória automática (nada de um pedido chega ao próximo).
- Só entra código/teste das pastas permitidas (`apps/api/app`, `apps/api/tests` e as pastas da
  tela); qualquer outro arquivo (configuração de ferramenta, dependência, migration, infra)
  devolve o pedido para a equipe. Caractere invisível/bidi no código também (e a tela marca
  ⟦U+…⟧ em vermelho, e o servidor recusa).
- Depois do Aprovar, as conferências rodam na **caixa** (`caixa.sb`, sandbox do macOS): o HOME
  inteiro é ilegível, menos a cópia do pedido, a pasta temporária, os node_modules da tela e o
  Python do uv; sem rede (só o socket do Postgres DESCARTÁVEL, que roda dentro da própria
  caixa); sem `open`/AppleScript/preferências de outros apps; escreve só na cópia e na pasta
  temporária; ambiente limpo (sem tokens). Testado: COPY … TO PROGRAM, ler ~/.ssh/~/.codex/
  ~/.claude.json, Redis em 127.0.0.1, `defaults write`, osascript — tudo barrado.
- O que vai para o DaVinci (e para quem pediu) é só status e nomes de teste; a saída completa
  fica em `registros/` neste Mac.
- O git do robô roda sem ganchos; a lista de arquivos é conferida de novo no que foi
  commitado (sem pular registro estranho); o diff mostrado é o que vai para o ar (a
  aprovação vale para ele: `diff_sha` + patch-id na publicação).
- Token próprio do robô (404 sem ele), guardado só no `.env` deste Mac e do servidor.
