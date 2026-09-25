"""Histórico de alterações do DaVinci (Sistema › Histórico).

Eduardo, 25/09/2026: "uma lista de histórico do que está sendo mudado no
DaVinci, por exemplo alterou a tabela de preços, tipo logs para irmos vendo as
movimentações". Decisões dele: só o que PESSOAS mudam (robôs ficam de fora) e
só ele vê — nem admin — até ele liberar alguém.

Como funciona, em três peças:

1. `middleware.HistoricoMiddleware` abre, para cada pedido à API, um `Ator`
   (contexto.py). Quem é a pessoa entra quando a autenticação a encontra
   (`contexto.identificar`, chamada por `deps/auth.get_current_user` e pelo
   conector do Claude).
2. Em pedido de escrita de uma pessoa, cada transação do banco recebe
   `set_config('davinci.ator', <id>, true)` (banco.py). O gatilho genérico
   `historico_captura` (sql.py), presente em todas as tabelas de negócio, só
   grava quando essa marca existe — robô não marca nada, então não grava nada
   e o custo para ele é uma checagem por linha. Pega ORM, comando direto,
   exclusão em massa e cascata, com o valor de antes e o de depois.
3. No fim do pedido o middleware grava o "evento" (quem, quando, tela, ação),
   que junta as alterações do mesmo pedido pelo `req_id`.

Segredos nunca entram: coluna com nome de senha/token/chave vira "(oculto)",
arquivo vira nome e tamanho, e texto livre com "senha 1234" vira "senha ***".
"""
