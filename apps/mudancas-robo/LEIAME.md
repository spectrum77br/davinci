# Robô de mudanças do DaVinci (Sistema › Mudanças)

O chefe pede uma mudança no próprio sistema pelo Claude dele (`pedir_mudanca` no
conector). Este robô, no Mac do Eduardo, **faz** a mudança numa cópia separada do código e
manda o diff para a tela **Sistema › Mudanças**, que só o Eduardo e o Thorfinn veem.
**Aprovado**, o robô roda as conferências e **publica** (push no main + `davinci-deploy` no
servidor). Nada do pedido roda — nem teste — antes do Aprovar; nada vai para o ar sem ele.

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
   do que ficou na pasta conta), atualiza com o main (tem de continuar o mesmo patch), instala
   as dependências DO MAIN e roda as conferências NA CAIXA (ruff, a API carregando, pytest/node
   dos testes mexidos). **Falhou: não publica** (fica "Falhou" com o motivo). Passou: exige o
   servidor em dia com o main, sem build rodando (nem `docker compose` na mão), disco ≥ 5 GB e
   a trava livre; faz o push e roda o `davinci-deploy` desacoplado da conexão (que recusa
   mexer em infra e roda a migration). Ocupado/disco/mudança de outra pessoa pendente: volta
   para a fila e tenta de novo em 10 min (o DaVinci avisa depois de ~1 h). Deploy que falha
   com a mudança já no main: tenta até 3 vezes e depois pede uma pessoa.
4. Um trabalho por vez; avisa que está vivo a cada 5 min (senão o DaVinci devolve o pedido;
   recomeçar conta tentativa — 3 no máximo, e 3 h no total).

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

## Segurança (2 revisões em 17/11/2026)
- **Nenhum código do pedido roda antes do Aprovar.** O Claude do robô só lê e edita arquivos
  da pasta do pedido (sem terminal). O pior que um pedido malicioso consegue antes da
  aprovação é um diff ruim — que quem aprova lê.
- Depois do Aprovar, as conferências rodam na **caixa** (`caixa.sb`, sandbox do macOS): sem
  rede (só o socket do Postgres), sem ler `~/.ssh`, a pasta do robô, `.env`, `~/.config`,
  `~/.pgpass`, históricos do terminal, o chaveiro; sem `open`/AppleScript; escrevendo só na
  cópia do pedido e numa pasta temporária própria; ambiente limpo (sem tokens).
- O git do robô roda sem ganchos; a lista de arquivos é conferida de novo no que foi
  commitado (sem pular registro estranho); o diff mostrado é o que vai para o ar (a
  aprovação vale para ele: `diff_sha` + patch-id na publicação).
- Publicar só com o servidor em dia com o main (nada de mudança de outra pessoa junto).
- Token próprio do robô (404 sem ele), guardado só no `.env` deste Mac e do servidor.
- O que a caixa NÃO impede (só vale para código JÁ aprovado): o Postgres local aceita
  qualquer conexão local como qualquer usuário (`trust`), então um teste aprovado poderia
  mexer nos bancos locais deste Mac — sem rede, nada sai daqui.
