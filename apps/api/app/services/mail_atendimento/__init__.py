"""A camada do atendimento por cima da Central de e-mail (08/10/2026).

A Central do outro dev (`services/mail_central.py`, `routers/mail.py`) é a
base: guarda, cifra, fila de respostas humanas e o contrato v1 do agente do
Mac. Aqui fica SÓ o que ela não faz, dividido por responsabilidade:

  caixa      — a configuração NOSSA de cada caixa (`mail_mailbox_settings`):
               privada × empresa, ponte, remetente estrito, modo de envio, tetos
               e pausa; e as travas que ela põe na caixa crua (quem MEXE no
               /atendimento, o que pode entrar na fila de uma caixa da empresa);
  ponte      — o e-mail da Central → a conversa do /atendimento (o job do
               worker, a cada minuto, só nas caixas com a ponte ligada) e a
               volta do status das respostas;
  responder  — a resposta pela conversa: prévia, travas, o endereço que
               recebeu, saindo pela fila DA Central;
  chamados   — os chamados dos sites (RF6): sugerir e agrupar;
  saude      — a saúde das caixas e a faixa "lojas sem ler" (só as da empresa);

e os módulos PUROS que vieram do wt-tuta: constantes, regras (palavras das
pastas), pastas, enderecos, rotear (destinatário → loja), pedido (nº por
plataforma e protocolo), suspeito (golpe), codigos (código, senha e link de
acesso fora do que a equipe vê) e texto (o texto novo, sem a citação).
"""
