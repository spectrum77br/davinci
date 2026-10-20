"""Credenciais Temu do cadastro de Lojas, somente para o robô do Mac mini.

A relação é perfil AdsPower → nº no espelho → Servidor da loja ativa. O
nome enviado pelo robô é informativo: nunca autoriza procurar uma senha por
nome (os nomes da configuração podem ser diferentes dos cadastros atuais).
Sem uma única loja Temu vinculada, não se entrega credencial alguma.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import StoreInfo
from app.security.cipher import decrypt
from app.services.atendimento.robo import RoboRecusado
from app.services.mail_atendimento.enderecos import endereco_do_cadastro


@dataclass(frozen=True)
class CredenciaisLoja:
    perfil_id: str
    loja: str
    usuario: str = field(repr=False)
    senha: str = field(repr=False)


async def buscar(session: AsyncSession, *, perfil_id: str) -> CredenciaisLoja:
    """Lê a senha cifrada na hora do login; não grava estado nem cache."""
    # O schema é configuração da API; o único valor de entrada vai como bind.
    schema = get_settings().database_schema.replace('"', '""')
    perfil = (
        await session.execute(
            text(f'SELECT profile_no FROM "{schema}".adspower WHERE id = :perfil_id'),  # noqa: S608
            {"perfil_id": perfil_id},
        )
    ).one_or_none()
    servidor = str(perfil.profile_no or "").strip() if perfil is not None else ""
    if not servidor:
        raise RoboRecusado(
            "robo_perfil_nao_encontrado", "Perfil sem número no espelho do AdsPower.", status=404
        )
    lojas = (
        (
            await session.execute(
                select(StoreInfo).where(
                    StoreInfo.archived_at.is_(None),
                    func.lower(func.trim(StoreInfo.platform)) == "temu",
                    func.trim(StoreInfo.server) == servidor,
                )
            )
        )
        .scalars()
        .all()
    )
    if not lojas:
        raise RoboRecusado(
            "robo_loja_nao_encontrada", "Nenhuma loja Temu ativa vinculada ao perfil.", status=404
        )
    if len(lojas) != 1:
        raise RoboRecusado(
            "robo_loja_ambigua", "Mais de uma loja Temu ativa vinculada ao perfil.", status=409
        )
    loja = lojas[0]
    cadastrado = (loja.email or "").strip()
    # A mesma regra já usada no atendimento de e-mail: códigos sem @ são
    # contas @tuta.com (decisão do cadastro de 08/10). Endereço completo é
    # normalizado; valor que o resolvedor não reconhece continua como veio.
    usuario = endereco_do_cadastro(cadastrado) or cadastrado
    if not usuario or not loja.password_enc:
        raise RoboRecusado(
            "robo_credenciais_incompletas", "Preencha E-mail e Senha da loja em Lojas."
        )
    # decrypt pode falhar por configuração/chave/dado inválido; a rota trata
    # qualquer falha com código fixo e sem traceback ou valor do cadastro.
    senha = decrypt(loja.password_enc)
    if not senha:
        raise RoboRecusado("robo_credenciais_incompletas", "Preencha a Senha da loja em Lojas.")
    return CredenciaisLoja(
        perfil_id=perfil_id,
        loja=(loja.account_name or "").strip(),
        usuario=usuario,
        senha=senha,
    )
