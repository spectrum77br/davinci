"""Módulo "App Uranyx" do DaVinci (06/10/2026).

O painel do app dos clientes da Uranyx (repositório app-uranyx) dentro do
DaVinci. Contrato: `docs/integracao/conteudo-e-catalogo-v1.md`, seção 6, no
repositório do app.

  acesso   — quem vê o módulo (admin E e-mail em APP_URANYX_USUARIOS).
  repasse  — o pedido que vai para `{APP_URANYX_API_URL}/admin/*` com o token
             da equipe, que só o servidor conhece.

A rota é `routers/app_uranyx.py` (/api/app-uranyx/*).
"""
