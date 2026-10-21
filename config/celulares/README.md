# Configuração opcional de Celulares

O deploy normal (`docker-compose.yml` + `docker-compose.prod.yml`) inclui a página, a API e os pontos de configuração. O menu é exclusivo de administradores e começa desativado. Não é necessário acrescentar um terceiro arquivo Compose nem fixar imagens de uma publicação anterior.

O servidor mantém três arquivos fora do Git, dentro de `/etc/davinci/celulares` (diretório root 0700, arquivos root 0600):

- `runtime.env`: configurações não secretas da API, conforme o exemplo desta pasta.
- `api.env`: somente `CELULARES_ADMIN_TOKEN` e `CELULARES_STREAM_KEY`; criados diretamente pelo administrador, nunca copiados para o `.env` compartilhado.
- `web.env`: somente as duas opções públicas do exemplo desta pasta.

O Compose usa `required: false` e `format: raw` (Compose 2.30 ou superior): arquivos ausentes mantêm o recurso desligado, e valores secretos são lidos literalmente. Os arquivos adicionais entram somente em API e web, preservando o `.env` normal e sem distribuir as chaves aos workers. Não colocar segredos no arquivo web.

O diretório `/var/lib/davinci-celulares-tunnel` do host é montado somente na API, em `/run/davinci-celulares`, como leitura. Se ausente, Compose pode criar um diretório vazio; isso não cria túnel nem habilita acesso. O túnel privado é mantido separadamente do deploy da aplicação. A API aceita o socket fixo `/run/davinci-celulares/agent.sock` com `CELULARES_API_URL=http://localhost` e não faz fallback para TCP. O serviço precisa estar pronto antes da ativação.

A ativação depende de uma lista explícita de aparelhos e de uma origem HTTPS validada do gateway. O mesmo endereço público deve ser configurado em `CELULARES_STREAM_ORIGIN` (API) e `NUXT_PUBLIC_CELULARES_GATEWAY_ORIGIN` (web). O piloto de câmera exige sua opção própria e registro validado; não é habilitado pelo simples aparecimento de um aparelho. Este patch não instala infraestrutura, registra aparelhos nem inicia sessões.

Antes de publicar, validar `docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet` com o ambiente normal do servidor. Aplicar alterações de configuração com recriação apenas dos serviços API e web, conforme o procedimento de deploy existente. Não imprimir o resultado completo de `config` com arquivos secretos carregados.

Testes locais de aplicação: `pytest --noconftest tests/unit/test_celulares.py tests/unit/test_celulares_stream.py` em `apps/api`; `node tests/celulares-sfc.cjs` em `apps/web`. A verificação Python/Node com um gateway externo é opcional via `CELULARES_GATEWAY_TEST_MODULE=/caminho/session.cjs`; sem esse arquivo, apenas os testes específicos de interoperabilidade são ignorados.
