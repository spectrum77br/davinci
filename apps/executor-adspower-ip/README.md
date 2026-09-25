# Executor do IP no AdsPower

Quando alguém coloca um IP novo numa empresa na tela **Empresas** do DaVinci,
este serviço troca o proxy de todos os perfis daquela empresa no AdsPower.

Roda neste Mac porque a API do AdsPower só responde na máquina onde ele está
aberto. A cada 60 segundos ele pergunta ao DaVinci o que está pendente, aplica e
devolve o resultado, que aparece ao lado do IP na tela:

| símbolo | quer dizer |
|---|---|
| ✓ | o IP já está no AdsPower |
| ⏳ | indo para o AdsPower (até 1 minuto) |
| ✗ | não foi; passe o mouse para ver o motivo |

## O que ele nunca faz

- Não mexe em empresa cujo IP já estava lá (as 25 preenchidas em 25/09/2026).
- Não tira o proxy de nenhum perfil. Sem proxy o marketplace veria o IP deste Mac.
- Não troca nada sem antes testar o proxy novo e confirmar que ele sai pelo IP
  certo. Se não sair, avisa e deixa tudo como estava.
- Não mexe em perfil que atende mais de uma empresa: trocaria o IP das duas.
- Não escreve usuário nem senha de proxy em lugar nenhum. Perfil que já tem
  proxy mantém a conta dele; perfil novo recebe a conta mais usada nos outros.

## Instalar, parar, testar

```bash
./instalar.sh        # instala e liga (pode repetir)
./desinstalar.sh     # para; nada muda no AdsPower
```

Ensaio num perfil, que **nunca grava**:

```bash
/usr/bin/python3 adspower_ip_sync.py --perfil <id do perfil> --ip <IP>
```

Testes: `/usr/bin/python3 -m unittest -v test_adspower_ip_sync`

Log: `~/DaVinci/executor-adspower-ip/logs/servico.log`

Precisa do arquivo `~/.davinci/adspower_agent_token` com o mesmo valor de
`ADSPOWER_AGENT_TOKEN` no servidor. Sem ele, ou com ele vazio no servidor, o
DaVinci recusa o serviço e nada vai para o AdsPower.
