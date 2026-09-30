# Denúncia — Sistema de Fiscalização no Mac mini da Makisa + cópia pro DaVinci

Desde 30/09/2026 o sistema de Fiscalização (anúncios, denúncias, casos) **roda
no próprio Mac mini da Makisa** — saiu do servidor da Hostinger — junto do robô
que varre os marketplaces e denuncia. Tudo mora em `~/Desktop/Denuncias/Ecomerce`
(Vinicius: "quero que fique tudo dentro daquela pasta Denuncias que tá na Mesa").

O DaVinci não alcança o mini (roteador do escritório), então o mini **manda**:
a cada 5 min o `enviar_ao_davinci.py` lê o banco do sistema (só leitura) e
posta no DaVinci o que mudou. O DaVinci mostra em **Ouvidoria › Denúncia**
(abas Anúncios · Denúncias · Casos · Jurídico) — só consulta.

## Onde fica no mini (`ssh mac-makisa`, usuário `makisatradingltda`)

| O quê | Onde |
|---|---|
| sistema (Flask) | `~/Desktop/Denuncias/Ecomerce/fiscalizacao-sistema/app` (venv em `.venv`) |
| banco + provas | `…/fiscalizacao-sistema/data/fiscalizacao.db` e `data/provas/<anúncio>/` |
| janela que liga tudo | `…/Fiscalizacao/00 - Sistema no Mac mini.command` → `…/DaVinci/servidor_mini.py` |
| cópia pro DaVinci | `…/DaVinci/enviar_ao_davinci.py` (roda de dentro da janela, a cada 5 min) |
| robô | `…/Fiscalizacao/6 - Agente da varredura.command` (inalterado) |
| início automático | `~/.fiscalizacao-inicio.sh` (LaunchAgent `com.makisa.fiscalizacao.inicio`): abre a janela do sistema e depois o robô |
| config do DaVinci (token!) | `~/.davinci_denuncia.json`, 600 |
| o que já foi mandado | `~/.davinci_denuncia_estado.sqlite` (um hash por linha) |
| logs | `data/servidor_mini.log` e `~/Library/Logs/davinci_denuncia.log` |

O robô e o Cowork falam com o sistema em `http://127.0.0.1:8710`
(`~/.fiscalizacao.json` → `url`; `~/Fiscalizacao/config/api_url`). As senhas
(token do robô, chave do Cowork) vieram junto com o banco — as mesmas de antes.

## Por que uma janela do Terminal e não LaunchAgent

O macOS barra tarefa de fundo de ler a Mesa ("Operation not permitted" —
testado em 30/09). O robô já rodava numa janela do Terminal; o sistema segue o
mesmo caminho, e tudo que ele dispara herda a permissão. A janela
`00 - Sistema no Mac mini` roda: o sistema, a cópia pro DaVinci (5 min), o MEGA
(2 min, quando o MEGAcmd estiver instalado em `/Applications/MEGAcmd.app` e
logado na conta da empresa), o `status_mac.py` do robô (antes LaunchAgent
`com.makisa.fiscalizacao.status`, desligado — plist guardado como
`.desativado-20260930`) e o backup diário local em `data/backups`.
O backup das 03:00 (`com.makisa.fiscalizacao.backup`, `~/.fiscalizacao/`) continua,
agora baixando do sistema local para `~/Fiscalizacao-Backup`.

## Travas da cópia pro DaVinci

- Leitura com `PRAGMA query_only` (o SQLite recusa escrita). O sistema usa WAL e
  apaga o `-shm` ao fechar a última conexão; `mode=ro` puro não abre nessa hora.
- Se mais de 20% das linhas já mandadas de uma tabela "somem" numa rodada, para
  sem apagar nada no DaVinci e registra no log.
- `banco` com `*` (modo backup) pega o arquivo mais novo e, se ele foi mexido há
  menos de 5 min, pula a rodada — nunca cai pro de ontem.
- Linhas de teste da API (`teste = 1`) não saem do mini.
- **Provas: só a ficha vai pro DaVinci** (tipo, data, tamanho, `mega_caminho`);
  os arquivos ficam no mini e no MEGA (disco do servidor do DaVinci é curto).

## Comandos

```bash
# a janela fechou? abrir de novo
open ~/Desktop/Denuncias/Ecomerce/Fiscalizacao/"00 - Sistema no Mac mini.command"
# o sistema está no ar?
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8710/api/v1/ping   # 401 = no ar
# o que aconteceu
tail -20 ~/Desktop/Denuncias/Ecomerce/fiscalizacao-sistema/data/servidor_mini.log
tail -20 ~/Library/Logs/davinci_denuncia.log
```

Recomeçar a cópia do zero (manda tudo de novo): apague `~/.davinci_denuncia_estado.sqlite`.
