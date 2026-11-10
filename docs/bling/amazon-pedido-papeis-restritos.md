# Pedido dos papéis restritos da Amazon (rascunho para revisão do Eduardo)

Onde: Solution Provider Portal (solutionproviderportal.amazon.com), entrando com a conta da KFA (perfil "KFA - Amazon" no AdsPower) → perfil de desenvolvedor do app do DaVinci → editar papéis (roles).

Papéis a marcar: **Direct-to-Consumer Shipping (Restricted)** e **Tax Invoicing (Restricted)**. Nada mais muda no perfil. Os papéis que o app já tem continuam; o Bling (ORGANISYS) e os outros apps autorizados não são tocados.

Só envie depois de conferir cada resposta. Os itens marcados **[confirmar]** dependem de você ou de configuração no servidor.

## Antes de enviar (precisa estar verdade)

1. **Chave mestra no servidor [confirmar]:** sem ela o ERP recusa guardar dados do comprador da Amazon. É um arquivo fora da pasta de dados do ERP (por exemplo `/etc/davinci/erp/pii.key`), lido por `ERP_PII_KEY_FILE`. É uma mudança de configuração do servidor, só com o seu OK.
2. **Login:** o DaVinci e o ERP entram por código de uso único enviado por e-mail, sem senha. Se a Amazon exigir um segundo fator, isso ainda precisa ser feito **[confirmar]**.
3. **Fase 2 (antes da troca):** quando um pedido da Amazon virar pedido próprio, nome e endereço passam para o pedido, o contato e a NF-e. A NF-e é guarda fiscal obrigatória (5 anos). O pedido e o contato ainda não são cifrados; isso precisa ser feito antes de ligar a troca da Amazon.

## Respostas (em inglês, para colar no formulário)

**Use case — Direct-to-Consumer Shipping**
We are the seller (KFA and our other Amazon Brazil seller accounts) and fulfill our own merchant-fulfilled orders. Our in-house ERP needs the shipping address only to print the shipping label and the legally required packing documents for orders shipped by us. The data is not used for marketing and is never shared with third parties.

**Use case — Tax Invoicing**
Brazilian law requires an electronic tax invoice (NF-e) for every sale, issued with the buyer's name, CPF/CNPJ and address. Our ERP issues the NF-e for our own orders and sends it to the buyer through Amazon's messaging, as required.

**Who uses the data**
Only our own company's operations staff, through our in-house application, after logging in with a personal account. There are no external clients; this is a private application for our own seller accounts.

**Encryption in transit**
All access goes through HTTPS (TLS 1.2 or higher) behind our reverse proxy. Calls to the SP-API are made over TLS from our server.

**Encryption at rest**
Buyer PII retrieved from Amazon is encrypted at the field level with AES-256-GCM before it is stored. Each order has its own data key; data keys are wrapped by a master key that is stored outside the database and outside the backups. Without the master key configured, the system refuses to store Amazon buyer data.

**Data retention and deletion**
Amazon buyer PII is deleted automatically 30 days after the order is shipped or cancelled (counted from shipment, which is earlier than delivery). Deletion destroys the order's data key, which makes every stored copy unreadable (crypto-shredding). Backups contain only the encrypted fields (never the master key) and rotate after 30 days. Only the tax invoice (NF-e) is kept for the period required by Brazilian tax law.

**Access control**
Unique personal accounts of our own staff only; every access requires login; permissions by role (administrators and operations profiles). Accounts are removed when a person leaves.

**Logging and monitoring**
Every write is recorded in an event log with user and time; buyer fields in it are stored encrypted. The ERP's service logs record counts and states, not buyer data.

**Incident response [confirmar]**
Suspected incidents are escalated to the company owner, who notifies Amazon (security@amazon.com) within 24 hours, revokes credentials, rotates the master key and the SP-API tokens, and records the incident.

**Network protection [confirmar]**
The application runs on a dedicated server with only HTTPS exposed publicly; the database is not reachable from the internet.

**Data sharing**
None. Buyer data is used only to ship the order and issue its tax invoice.

## O que foi feito no ERP (10/11)

- `pii.py`: cifragem AES-256-GCM por pedido, chave mestra fora do banco (`ERP_PII_KEY` ou `ERP_PII_KEY_FILE`), apagamento diário pelo prazo (Amazon: 30 dias após envio ou cancelamento; no máximo 60 dias após a leitura), inclusive de leituras antigas em texto no histórico.
- `recipient_capture.py`: a leitura do comprador fica cifrada na tabela e no histórico de comandos quando há chave mestra; sem chave, a Amazon não é guardada e as outras lojas seguem como antes.
- Testes: `test_pii.py` (6 casos), suíte completa passando.

## Achado em 10/11 (Solution Provider Portal da KFA, só leitura)
- O pedido dos papéis restritos JÁ foi feito e foi NEGADO em 29/09/2026 (chamado 21731531021, "SP-API Developer Profile Update: Restricted Access Request"). Para recorrer, atualizar as respostas do perfil em solutionproviderportal.amazon.com/developer/register (abre um caso novo).
- Pontos que a Amazon disse que não atendiam: Proteção de rede 1.1; Retenção 2.1 e 2.7 (cópias cifradas em local geograficamente separado e procedimento de restauração com RTO/RPO); Registro e monitoramento 2.6; Plano de resposta a incidentes 1.6; Gestão de credenciais 1.4 (tamanho, complexidade e validade de senha); Gestão de vulnerabilidades 2.7.
- Apps no portal: "Gestao_KFA" e "Stock Sync Hub", ambos em rascunho (desenvolvedor privado). AVISO DA AMAZON: o segredo do Login com a Amazon (LWA) dos apps vence em menos de 30 dias; se vencer, a integração do DaVinci com a Amazon para. Trocar em "Visualizar" (credenciais) e atualizar no DaVinci antes do vencimento — passo do Eduardo (chave).

## Fatos levantados para responder com verdade (10/11)
- Servidor: portas públicas só 22 (SSH só por chave, senha desligada, root só por chave) e 80/443 (Caddy, HTTPS). Bancos sem porta pública (rede interna do Docker). fail2ban ativo. Disco do servidor cifrado (LUKS no sistema inteiro). Atualizações automáticas do sistema (apt-daily-upgrade).
- Código: análise estática com regras de segurança (ruff "S", estilo bandit) em todo commit da api; nenhum CI com varredura de dependências.
- Login: DaVinci e ERP sem senha, por código de uso único por e-mail com limite por IP.
- Lacunas reais: (1) NÃO há cópia de segurança automática do banco do DaVinci nem do ERP (nada agendado; /opt/backups e /var/lib/davinci-erp/backups vazios de dumps); (2) nada fora do servidor (geograficamente separado); (3) sem monitoramento com alerta de atividade suspeita; (4) sem plano de incidentes escrito; (5) sem varredura de dependências; (6) o Mac mini (máquina de desenvolvimento com acesso ao servidor) está com FileVault DESLIGADO.
- 10/11 11h: cópias cifradas diárias ligadas (restic → MEGA pelo MEGAcmd do DaVinci, 04:00). Primeira cópia do ERP e restauração conferidas; a primeira do DaVinci roda em 11/11 04:00. Detalhes em docs/backup-restauracao.md.

## Respostas do recurso, ponto a ponto da negativa de 29/09 (rascunho de 10/11)
Colar no perfil de desenvolvedor (solutionproviderportal.amazon.com/developer/register), nas perguntas de segurança correspondentes. Os trechos entre [ ] dependem de decisão do Eduardo e saem ou entram conforme a decisão.

**Network Protection 1.1** — Our production server exposes only SSH (public-key authentication only; password and keyboard-interactive login disabled; root login only with keys) and HTTPS through a reverse proxy. Databases (PostgreSQL and the ERP's SQLite) and internal services run on a private Docker network with no public ports. fail2ban blocks repeated failed SSH attempts. The server disk is fully encrypted (LUKS). A monitor checks every 10 minutes for any new public port. Developer endpoints reach the server only with personal SSH keys.

**Data Retention 2.1 / encrypted backups 2.7** — Daily encrypted backups (restic, AES-256), incremental, sent directly to a storage provider in a different location from our hosting provider (server in Nuremberg, Germany; backups stored with MEGA). Retention is about 30 days (7 daily and 4 weekly). Each run verifies a random 5% sample of the stored data. The restore procedure is documented and was tested on 10/11/2026 (ERP database restored and verified by checksum and record counts). RPO: 24 hours. RTO: 4 hours for the main database and 30 minutes for the ERP. The backup password is kept on the server with root-only permission and a second copy is held offline by the owner. Amazon buyer PII is deleted 30 days after shipment by destroying its per-order key, so it cannot be recovered from backups either.

**Logging and Monitoring 2.6** — System and SSH authentication logs (journald), fail2ban bans, reverse-proxy access logs and an application event log that records every write with user and time. A security monitor runs every 10 minutes and records an alert for: SSH logins by a user or key never seen before, changes to authorized SSH keys, user accounts, groups or sudoers, bursts of failed logins (more than 300 in 10 minutes) or of fail2ban bans (more than 20), and any new public port. Alerts are reviewed by the owner at every administrative session; any alert opens the incident procedure below.

**Risk Management and Incident Response Plan 1.6** — Written plan with the company owner as the responsible person. Steps: detect and record; contain within the first hour (block IP or user, take exposed services offline, revoke sessions and SSH keys); rotate all affected credentials (marketplace app secrets, master encryption key, backup password); notify Amazon at security@amazon.com within 24 hours when Amazon information is involved, and other platforms and authorities as required by law (LGPD); investigate using the logs above; restore from backups if needed; record the incident and fix the root cause.

**Credential Management 1.4** — Staff log in with one-time codes sent by e-mail: 8 digits plus a 4-character prefix, valid for 10 minutes, at most 5 attempts, rate-limited per IP and per e-mail, stored only as bcrypt hashes. Password login, where used, requires at least 8 characters and is rate-limited (20 attempts per IP and 8 per account per hour). Server access only with personal SSH keys, removed when a person leaves. Application secrets live only on the server, in root-only files and encrypted in the database (AES-GCM); marketplace secrets are rotated every 180 days or when the platform requires (Amazon LWA rotation is mandatory).

**Vulnerability Management 2.7** — Static security analysis (ruff with the flake8-bandit rule set) on every code change; weekly dependency scan of Python (pip-audit) and Node (pnpm audit) components with results archived; remediation deadlines: critical in 7 days, high in 30 days; automatic operating-system security updates on the server. Findings are reviewed by the owner, who schedules the updates.

### Decisões do Eduardo (10/11) e risco
- Não mexer no DaVinci agora: as 9 bibliotecas com falha seguem como estão; sem prazo de correção declarado.
- Senha: fica como está (mínimo 8). Sem segundo fator.
- Plano de incidentes: só o Eduardo como responsável, sem substituto.
- FileVault no Mac mini: não agora.
- Risco: 3 dos 6 pontos da negativa (credenciais 1.4, vulnerabilidades 2.7 e a parte de máquinas de desenvolvimento em rede 1.1) continuam fracos; a chance de nova negativa nesses pontos é alta. Os outros 3 (cópias 2.1/2.7, monitoramento 2.6, incidentes 1.6) agora têm controles reais.
