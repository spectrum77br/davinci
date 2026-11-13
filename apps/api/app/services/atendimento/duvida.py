"""A LINHA DA DÚVIDA em cima do rascunho que não saiu sozinho (13/11/2026). PURA.

O Eduardo: "rascunho escrito só quando não bater as %; se não, envia sozinho". O rascunho
que fica na caixa leva UMA linha dizendo por quê. O porquê é a última decisão que o
portão do envio sozinho gravou no rascunho (`fatos.auto_envio`, `ia._registrar_auto_envio`)
— nada é recalculado aqui. Quando a régua por tipo de pergunta está ligada
(`atendimento_regua_ativa`), o assunto não liberado diz os números dela: a APROVAÇÃO DA
EQUIPE (`regua.py`), nunca a certeza que o modelo declara.

Nenhum texto de cliente: só o código da decisão, o assunto e as contagens.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.atendimento import constantes

FATO_AUTO_ENVIO = "auto_envio"  # = ia.FATO_AUTO_ENVIO (o teste confere)

# O portão segurou porque o ASSUNTO não está liberado para sair sozinho no canal.
ASSUNTO_NAO_LIBERADO = frozenset(
    {
        "assunto_nao_liberado",
        "assunto_nao_liberado_agente",
        "orientacao_nao_liberada",
        "assunto_de_produto",
        "assunto_so_humano",
    }
)

# Os outros motivos do portão, em uma frase para a equipe. O que não está aqui não ganha
# linha (o código cru nunca vai para a tela).
FRASES: dict[str, str] = {
    "acao_pessoa": "Pede uma ação de pessoa na plataforma.",
    "em_andamento": "Já tem troca ou devolução em andamento — confira antes de enviar.",
    "agente_desligado": "O agente está desligado nesta loja.",
    "avisos_auto": "A IA deixou um aviso para conferir.",
    "sem_auto": "A IA marcou que este caso não sai sozinho.",
    "gerado_no_copiloto": "Escrita quando a loja estava no Copiloto.",
    "canal_fora_do_auto": "A loja não está no Automático.",
    "canal_sem_envio": "Este canal não envia pelo DaVinci.",
    "auto_desligado": "O envio sozinho está desligado.",
    "plataforma_sem_auto": "Esta plataforma não envia sozinha.",
    "verificador_desligado": "A conferência da resposta está desligada.",
    "verificador_nao_rodou": "A conferência da resposta não rodou.",
    "verificador_reprovou": "A conferência achou um ponto para revisar.",
    "verificador_texto_mudou": "O texto mudou depois da conferência.",
    "verificador_vencido": "A conferência ficou velha — o cliente escreveu de novo.",
    "validador_reprovou": "Uma regra da loja barrou o texto.",
    "gatilho_antigo": "A pergunta é de mais de 3 h atrás.",
    "sem_gatilho": "Sem a mensagem do cliente que ela responde.",
    "turno_mudou": "O cliente escreveu de novo depois dela.",
    "generica_espera_o_turno": "Só cumprimento — espera o cliente dizer o que precisa.",
    "pausa_humana": "Alguém da equipe respondeu há pouco; a IA espera.",
    "precisa_humano": "A IA marcou que precisa de pessoa.",
    "confianca_baixa": "A IA não teve segurança para mandar sozinha.",
    "reclamacao_aberta": "Tem reclamação aberta na plataforma.",
    "ia_pausada": "A IA está pausada nesta conversa.",
    "atribuida": "A conversa está com alguém da equipe.",
    "sem_texto": "A IA não escreveu uma resposta que possa sair.",
    # O portão novo (a resposta do banco que encaixa no caso, 13/11/2026).
    "sem_banco": "Sem resposta da equipe parecida no banco para este caso.",
}
CAIXA_HUMANO = "caixa_humano"


@dataclass(frozen=True)
class Aprovacao:
    """A régua do tipo no canal (uma linha de `atendimento_regua`)."""

    tipo: str
    decisoes: int
    acertos: int
    limiar: float
    liberado: bool
    escopo: str

    @property
    def fracao(self) -> float:
        return self.acertos / self.decisoes if self.decisoes else 0.0


def aprovacao_de(linha: Any) -> Aprovacao | None:
    """A `AtendimentoRegua` (ou algo com os mesmos campos) → `Aprovacao`; None sem linha."""
    if linha is None:
        return None
    return Aprovacao(
        tipo=str(linha.tipo),
        decisoes=int(linha.decisoes or 0),
        acertos=int(linha.acertos or 0),
        limiar=float(linha.limiar),
        liberado=bool(linha.liberado),
        escopo=str(linha.escopo or ""),
    )


def nome_do_assunto(categoria: str | None) -> str:
    if not categoria:
        return "Este assunto"
    info = constantes.CATEGORIAS_INFO.get(categoria)
    return info[0] if info else categoria.replace("_", " ").capitalize()


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def frase_da_regua(categoria: str | None, aprovacao: Aprovacao | None) -> str:
    """O assunto não liberado, com os números da régua quando há. PURA."""
    from app.services.atendimento import regua

    nome = nome_do_assunto(categoria)
    precisa = _pct(aprovacao.limiar if aprovacao else regua.limiar(categoria))
    if aprovacao is None or aprovacao.decisoes == 0:
        return (
            f"{nome}: a equipe ainda não decidiu rascunhos deste assunto "
            f"(sai sozinha com {precisa} de aprovação)."
        )
    if aprovacao.liberado:
        # A régua já soltou; as listas do canal acompanham no próximo cron (:28/:58).
        return (
            f"{nome}: {_pct(aprovacao.fracao)} de aprovação da equipe — liberado, "
            "vale a partir da próxima."
        )
    if aprovacao.decisoes < regua.MIN_DECISOES:
        return (
            f"{nome}: só {aprovacao.decisoes} decisões da equipe até agora "
            f"(a régua mede com {regua.MIN_DECISOES}; sai sozinha com {precisa})."
        )
    return (
        f"{nome}: {_pct(aprovacao.fracao)} de aprovação da equipe nas últimas "
        f"{aprovacao.decisoes} (sai sozinha com {precisa})."
    )


def linha(
    fatos: Mapping[str, Any] | None,
    categoria: str | None,
    *,
    aprovacao: Aprovacao | None = None,
    regua_ativa: bool = False,
) -> str | None:
    """Por que o rascunho não saiu sozinho, em uma linha; None sem decisão gravada. PURA."""
    decisao = (fatos or {}).get(FATO_AUTO_ENVIO) if isinstance(fatos, Mapping) else None
    if not isinstance(decisao, Mapping):
        return None
    motivo = str(decisao.get("motivo") or "")
    if motivo in ASSUNTO_NAO_LIBERADO:
        if regua_ativa:
            return frase_da_regua(categoria, aprovacao)
        return (
            f"{nome_do_assunto(categoria)}: o assunto não está liberado para sair "
            "sozinho nesta loja."
        )
    if motivo == CAIXA_HUMANO:
        rotulos = [
            constantes.ROTULO_HUMANO.get(str(m), "")
            for m in decisao.get("motivos_humano") or []
            if constantes.ROTULO_HUMANO.get(str(m))
        ]
        return "Precisa de pessoa" + (f": {', '.join(dict.fromkeys(rotulos))}." if rotulos else ".")
    return FRASES.get(motivo)
