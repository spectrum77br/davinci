# Robô de mudanças do DaVinci (Sistema › Mudanças)

O chefe pede uma mudança no próprio sistema pelo Claude dele (`pedir_mudanca` no
conector). Este robô, no Mac do Eduardo, **faz** a mudança numa cópia separada do código,
roda as conferências e manda o resultado para a tela **Sistema › Mudanças**, que só o
Eduardo e o Thorfinn veem. **Aprovado**, o robô **publica** (push no main + `davinci-deploy`
no servidor). Nada vai para o ar sem o Aprovar.

## Como funciona uma rodada (a cada 1 min, `rodada.py`)
1. Pergunta ao DaVinci se há trabalho (`/api/sistema-mudancas-robo/pegar`) — sem IA.
2. **fazer**: cria a worktree `~/davinci-mudancas/M-N` (branch `mudanca/M-N`) do `origin/main`,
   roda o Claude Code sem tela com `REGRAS.md` e a lista fechada `permissoes.json`, confere os
   arquivos (lista proibida: banco/migration, permissão, infra, robôs, dependências,
   `.gitattributes`, `conftest.py`; binário; até 12 arquivos e 400 linhas), roda as
   conferências NA CAIXA (ruff, a API carregando, pytest/node dos testes mexidos), faz o commit
   e manda o diff.
   Pedido que foge das regras volta como **Precisa da equipe**.
3. **publicar**: confere que a cópia é a APROVADA (mesmo patch), atualiza com o main e roda as
   conferências de novo na caixa, exige o servidor em dia com o main, confere o disco (≥ 5 GB)
   e a trava de deploy, faz o push e roda o `davinci-deploy` desacoplado da conexão (que recusa
   mexer em infra e roda a migration). Outro deploy, disco cheio ou mudança de outra pessoa
   pendente: volta para a fila e tenta de novo em 10 min (o DaVinci avisa depois de ~1 h).
4. Um trabalho por vez; avisa que está vivo a cada 5 min (senão o DaVinci devolve o pedido).

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

## Segurança (revisão de 17/11/2026)
- **O código da mudança ainda não aprovada nunca roda solto.** O Claude do robô só lê/edita
  arquivos da pasta do pedido e só executa o `testar.sh`; o `testar.sh` roda tudo dentro da
  **caixa** (`caixa.sb`, sandbox do macOS): sem rede (só o socket do Postgres), sem ler
  `~/.ssh`, a pasta do robô, `.env`, `~/.config`, o chaveiro; escrevendo só na pasta do pedido,
  no cache do uv e numa pasta temporária própria; com ambiente limpo (sem tokens). As
  conferências do próprio robô também passam pelo `testar.sh`.
- O git do robô roda sem ganchos; a lista de arquivos proibidos, binários e o tamanho são
  conferidos de novo no que foi commitado; o diff mostrado é o que vai para o ar (a aprovação
  vale para ele: `diff_sha` + patch-id na publicação).
- Publicar só com o servidor em dia com o main (nada de mudança de outra pessoa junto).
- Token próprio do robô (404 sem ele), guardado só no `.env` deste Mac e do servidor.
- O que a caixa NÃO impede: os testes leem o Postgres local (que aqui aceita qualquer conexão
  local), mas sem rede nada sai; e o próprio Claude do robô vê o código do repositório.
