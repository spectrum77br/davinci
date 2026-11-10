# Segurança: plano de resposta a incidentes e política de credenciais

Rascunho de 10/11/2026 para aprovação do Eduardo. Vale para o DaVinci, o ERP próprio e o servidor de produção. Atende aos pontos 1.4 (credenciais) e 1.6 (incidentes) que a Amazon cobrou na negativa de 29/09/2026. Os itens marcados **[a fazer]** ainda não existem e não podem ser declarados à Amazon antes de feitos.

## 1. Plano de resposta a incidentes

**Responsável:** Eduardo Felipe (dono). Substituto: **[definir]**.

**O que é incidente:** acesso não autorizado ao servidor, banco ou painéis; vazamento ou suspeita de vazamento de dados de compradores; chave, token ou senha exposta; perda ou corrupção de dados; programa malicioso numa máquina com acesso ao servidor.

**Passos:**
1. **Detectar e registrar:** alerta automático (ver item 3) ou aviso de alguém. Anotar quando, o que e quem viu.
2. **Conter (na primeira hora):** bloquear o IP ou o usuário; tirar do ar o que estiver exposto; trocar a chave SSH afetada; revogar sessões do DaVinci.
3. **Trocar credenciais:** segredos dos apps das lojas (Amazon LWA, Mercado Livre, Shopee, TikTok, Bling), chave mestra dos dados de comprador (`ERP_PII_KEY`, com recifragem), senha das cópias e tokens internos.
4. **Avisar:**
   - **Amazon:** security@amazon.com em até 24 horas quando houver dado de comprador da Amazon envolvido.
   - **Demais lojas:** conforme as regras de cada uma.
   - **Titulares e ANPD:** quando a LGPD exigir.
5. **Investigar:** logs do servidor (SSH, fail2ban, Caddy), histórico de comandos do ERP e logs da api. Descobrir a causa e o alcance.
6. **Recuperar:** restaurar das cópias se preciso (ver `docs/backup-restauracao.md`) e conferir os dados.
7. **Aprender:** registrar o incidente (data, causa, alcance, ações) em `docs/seguranca/incidentes/` e corrigir a causa.

**Contatos:** Eduardo (telefone e e-mail **[preencher]**); Hetzner (painel e suporte); Amazon security@amazon.com.

**Teste:** uma simulação por ano **[a fazer]**.

## 2. Política de credenciais

**Login no DaVinci e no ERP (como é hoje, conferido no código em 10/11):**
- Código de uso único por e-mail: 8 dígitos e um prefixo de 4, válido por 10 minutos, no máximo 5 tentativas, limite de 3 pedidos por IP e 3 por e-mail. O código é guardado com bcrypt, nunca em texto.
- Login por senha, quando usado: mínimo de 8 caracteres e limite de tentativas por hora (20 por IP, 8 por e-mail).
- Sessão de até 7 dias.
- **[a fazer, decidir]:** subir o mínimo da senha para 12 caracteres, exigir letras e números e oferecer um segundo fator para administradores.

**Servidor:**
- SSH só por chave: senha desligada e root só por chave. fail2ban ativo.
- Cada pessoa ou máquina tem a sua chave. A chave é removida quando a pessoa sai.

**Segredos das integrações:**
- Ficam só no servidor: em `.env` e `runtime.env` com permissão 600, e cifrados no banco (AES-GCM). Nunca no repositório nem em mensagens.
- Os segredos das lojas são trocados quando a loja pede ou a cada 180 dias. O LWA da Amazon tem vencimento obrigatório e a Amazon avisa 30 dias antes.

**Contas das lojas:**
- Acesso por perfil no AdsPower, com o login feito pelo dono ou pela equipe autorizada.
- Senhas guardadas num gerenciador de senhas **[confirmar qual]**.

**Máquinas com acesso ao servidor:**
- Disco cifrado. **[a fazer]:** ligar o FileVault no Mac mini.

## 3. Pendências para a Amazon
- Cópias cifradas fora do servidor, com teste de restauração (`scripts/backup-offsite.sh`, aguardando o destino).
- Alerta de atividade suspeita: SSH novo, muitas falhas de login, fail2ban e acessos fora do normal **[a fazer]**.
- Verificação de dependências: `scripts/security-scan.sh` (Python da api/ERP com pip-audit e Node do site com pnpm audit), toda segunda-feira, com o resultado em `docs/seguranca/varreduras/`. Prazo de correção: crítica em 7 dias e alta em 30. Primeira varredura em 10/11: 9 bibliotecas Python com falha (todas com correção disponível) e 121 falhas no site (7 críticas e 56 altas). A correção ainda não foi feita **[a fazer]**.
- FileVault no Mac mini e os itens [a fazer] acima.
