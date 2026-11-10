# Cópias de segurança do DaVinci e do ERP: como funcionam e como restaurar

## O que é copiado
- Banco `davinci` (pg18), sem o esquema `uranyx` (a política publicada do app Uranyx diz que os dados dele não têm cópia) e sem os dados dos registros regeneráveis (`sync_logs*`, `background_job*`; a estrutura dessas tabelas é copiada).
- Banco do ERP (`/var/lib/davinci-erp/erp.sqlite3`). Os dados de comprador já estão cifrados dentro dele; a chave mestra (`ERP_PII_KEY`) NÃO vai na cópia e fica guardada à parte pelo Eduardo.
- Não entram: os bancos `legacy`, `appdb`, `DaVinciDev` e `postgres` do pg18, e os arquivos de `/opt/davinci/data` (vídeos e uploads). Se algum for necessário, incluir no script.

## Como
- `scripts/backup-offsite.sh`, uma vez por dia às 04:00 (horário de Brasília), pelo restic: cifrado (AES-256), incremental e deduplicado, enviado direto para um destino fora do servidor, em outro local geográfico. O banco do DaVinci vai em fluxo, sem arquivo grande no disco do servidor (o disco está com 90% de uso).
- Guarda: 7 cópias diárias e 4 semanais (cerca de 30 dias).
- A cada execução, `restic check` lê uma amostra de 5% dos dados para provar que a cópia abre.
- Senha do repositório: `/etc/davinci/backup/restic.pass` no servidor (root, 600) e uma segunda via com o Eduardo. Sem ela, a cópia não abre.

## Metas
- RPO (quanto se pode perder): até 24 horas.
- RTO (tempo para voltar): até 4 horas para o banco do DaVinci e 30 minutos para o ERP, num servidor com espaço livre.

## Restaurar
1. Ver as cópias: `restic snapshots` (com o `backup.env` carregado).
2. ERP: `restic restore latest --tag erp --target /tmp/restauro`, parar o `erp_worker`, conferir com `python -m app.erp_local.release restore <arquivo> <novo>` e trocar o arquivo em `/var/lib/davinci-erp/`. Subir o `erp_worker` de novo.
3. DaVinci: criar um banco vazio (`createdb -U pguser davinci_restauro`), carregar `restic dump latest davinci.sql --tag davinci | docker exec -i pg18 psql -U pguser -d davinci_restauro`, conferir contagens das tabelas principais e só então trocar o nome dos bancos, com a api e os workers parados.
4. Teste de restauração completo: uma vez por mês, restaurar o ERP inteiro e uma tabela grande do DaVinci num banco temporário e registrar o tempo gasto aqui.

## Histórico
- 10/11/2026: script e passo a passo escritos; destino ainda não escolhido; nada agendado no servidor.
