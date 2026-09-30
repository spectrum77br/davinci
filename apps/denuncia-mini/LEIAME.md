# Denúncia — envio do Mac mini da Makisa para o DaVinci

O sistema de Fiscalização (anúncios, denúncias, casos) roda no Mac mini da
Makisa, junto do robô que varre os marketplaces e denuncia. O DaVinci não
alcança o mini (roteador do escritório), então o mini **manda**: a cada 5 min o
`enviar_ao_davinci.py` lê o banco do sistema em modo só-leitura e posta no
DaVinci o que mudou. O DaVinci mostra em **Denúncia › Anúncios / Denúncias /
Casos** (só consulta; quem muda algo é o sistema do mini).

## Onde fica no mini (`ssh mac-makisa`, usuário `makisatradingltda`)

| O quê | Onde |
|---|---|
| script | `~/DaVinci/denuncia/enviar_ao_davinci.py` |
| agendador | `~/Library/LaunchAgents/com.davinci.denuncia-envio.plist` (a cada 300 s) |
| configuração (token!) | `~/.davinci_denuncia.json`, permissão 600 |
| o que já foi mandado | `~/.davinci_denuncia_estado.sqlite` (um hash por linha) |
| log | `~/Library/Logs/davinci_denuncia.log` (e `.launchd.log`) |

`~/.davinci_denuncia.json`:

```json
{"url": "https://app.hadken.com", "token": "dnc_…",
 "banco": "~/Fiscalizacao-Backup/banco/fiscalizacao_*.sqlite",
 "provas": "~/Fiscalizacao-Backup/provas"}
```

- **Antes** do sistema rodar no mini, `banco` aponta pro backup diário das 03:00
  (o `*` pega o mais novo; arquivo mexido há menos de 5 min é ignorado).
- **Depois**, aponta pro banco vivo do sistema e pra pasta `data/provas` dele.

O token é o do remetente "Mac mini da Makisa" (`denuncia_remetentes`, migration
0344 — lá fica só o sha256). Trocar o token = nova linha/migration com o hash
novo e revogar a antiga (`revoked_at`).

## Travas

- Banco aberto só-leitura (`mode=ro`; backup recém-baixado sem `-shm` abre com
  `immutable=1`). O script nunca escreve no sistema.
- Se mais de 20% das linhas já mandadas de uma tabela "somem" numa rodada, ele
  para sem apagar nada no DaVinci (banco errado ou pela metade) e registra no log.
- Linhas de teste da API (`teste = 1`) não saem do mini.
- Provas: o DaVinci confere o sha256 de cada arquivo; até `MAX_MB_RODADA`
  (400 MB) por rodada, então a primeira carga (~4 GB) leva algumas horas.

## Comandos

```bash
# rodar agora, na mão
/usr/bin/python3 ~/DaVinci/denuncia/enviar_ao_davinci.py
# ligar / desligar o agendador
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davinci.denuncia-envio.plist
launchctl bootout gui/$(id -u)/com.davinci.denuncia-envio
# ver o que aconteceu
tail -20 ~/Library/Logs/davinci_denuncia.log
```

Recomeçar do zero (manda tudo de novo): apague `~/.davinci_denuncia_estado.sqlite`.
