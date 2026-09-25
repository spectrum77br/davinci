# Conexão Magalu pelo Mac mini

A API e os workers continuam no servidor. Somente o tráfego configurado em
`MAGALU_PROXY_URL` passa pelo Mac; outras plataformas não usam essa saída.

Fluxo: cliente Magalu → serviço Docker interno `magalu_proxy:13129` → socket
Unix privado no host → túnel reverso SSH → proxy em `127.0.0.1:13129` no Mac →
`id.magalu.com:443` ou `api.magalu.com:443`.

O proxy exige autenticação própria, permite apenas CONNECT para esses dois
destinos e mantém o TLS entre o cliente da API e a Magalu. Não interpreta os
tokens ou dados das contas. Não há porta publicada no host nem bind público no
Mac. O bridge não recebe variáveis de ambiente com credenciais; roda com disco
somente leitura e sem capabilities.

## Instalação

Editar e testar no Mac; atualizar com `git pull --ff-only origin main`, commitar
arquivos explícitos e enviar para `origin/main`. No servidor, fazer fetch/reset
conforme o fluxo de publicação do projeto. Não editar as fontes no servidor.

No Mac, a partir do checkout publicado:

```sh
/usr/bin/python3 scripts/install_magalu_mac_proxy.py --check
/usr/bin/python3 scripts/install_magalu_mac_proxy.py --apply
```

Os LaunchAgents `com.davinci.magalu-proxy` e `com.davinci.magalu-tunnel` reiniciam
os processos automaticamente. As fontes são copiadas para
`~/Library/Application Support/DaVinci/magalu-proxy`; o serviço não depende da
existência da worktree. O arquivo `credentials.json` é privado (0600), gerado
uma vez e preservado em novas instalações. Não contém tokens da Magalu.

O túnel usa o alias SSH existente `davinci-prod` e cria apenas
`/opt/davinci/data/magalu-proxy/magalu.sock`. Um socket antigo só é removido se
não houver listener; arquivos regulares e sockets ativos não são substituídos.
Não é necessário alterar `GatewayPorts` nem a configuração global do SSH.
Um heartbeat a cada 15 segundos acompanha a sessão. Após 75 segundos sem sinal
ou fechamento da sessão, o monitor libera somente o inode criado por ela;
um socket de uma conexão nova é preservado. O processo SSH antigo pode esperar
seu timeout TCP, mas não impede a reconexão pelo mesmo caminho.

No servidor, iniciar o bridge interno:

```sh
cd /opt/davinci
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build magalu_proxy
```

O healthcheck espera 407 (autenticação exigida) do proxy Mac, comprovando o
trajeto sem consultar dados ou consumir tokens da Magalu. Testar também o
caminho autenticado com GET antes de trocar a configuração dos processos.

Enviar as credenciais do proxy pelo stdin do SSH, sem registrá-las em comandos,
logs ou no Git. `scripts/configure_magalu_proxy.py --check` valida sem gravar;
`--apply` altera apenas `MAGALU_PROXY_URL` no `.env`, faz backup privado e usa
substituição atômica. Não imprime a URL autenticada. Os demais campos do `.env`
são preservados. Depois reconstruir/recriar `api`, `web`, `worker`, `worker_ui`,
`worker_marketplace`, `worker_financials`, `worker_sync` e `worker_marketing_agent`
com ambos os arquivos Compose para todos adotarem a mesma configuração.

## Operação e validação

- O Mac precisa estar ligado, conectado e com a sessão do usuário iniciada para
  os LaunchAgents. Se ficar indisponível, a integração falha explicitamente;
  não retorna silenciosamente ao proxy antigo nem à conexão direta.
- O teste da integração pode renovar a autorização. Deve persistir os tokens
  retornados; não testar refresh avulso sem salvar a resposta cifrada.
- Clientes de integrações salvas serializam a renovação por integração com
  `FOR NO KEY UPDATE` no PostgreSQL. Releem os tokens sob o lock, reaproveitam
  uma renovação concluída por outro processo e só usam novos tokens depois do
  commit. O lock permite inserts com FK na sessão chamadora sem deadlock.
- Consultas de teste usam GET. Não é necessário mudar preço, estoque ou pedido
  para confirmar conectividade e autorização.
- Uma autorização inválida/expirada exige novo consentimento do titular no
  ID Magalu. Conectividade funcionando não substitui esse consentimento.
- Para rollback, recuperar o `.env` do backup privado, preservando outras
  mudanças posteriores, e recriar os processos. O proxy antigo pode continuar
  indisponível; restaurar sua URL não conserta aquele serviço.
- Para interromper a saída Mac, remover os dois LaunchAgents com `launchctl
  bootout` e parar o serviço Docker `magalu_proxy`; não apagar credenciais ou
  outros dados do usuário por padrão.

## Referências oficiais

- [OAuth Marketplace](https://developers.magalu.com/docs/first-steps/create-an-application/authentication-authorization/index.html)
- [FAQ de integração](https://developers.magalu.com/docs/apis/faq/onboarding/index.html)
- [Consulta de SKUs](https://developers.magalu.com/docs/apis/products/ref/portfolios-v-1-list-skus/index.html)

As medições distinguiram a falha de conexão do proxy anterior e a recusa da
conexão direta do servidor. Não comprovam uma política geral de bloqueio por
país. A configuração acima usa a conexão autorizada do proprietário.
