"""IA pelo endereço da OpenAI (02/10/2026): o corpo sem `max_tokens`/`temperature`.

Os modelos de raciocínio da OpenAI recusam os dois (400) e gastam o teto no
raciocínio; o Groq (e qualquer outro compatível) segue com o corpo de sempre.
"""

from app.services.atendimento import ia


def _p(base: str, modelo: str = "modelo-x") -> ia.Provedor:
    return ia.Provedor(base_url=base, modelo=modelo, chave="k")


def test_openai_reconhecida_pelo_host():
    assert ia.e_openai("https://api.openai.com/v1")
    assert ia.e_openai("https://minha.openai.azure.com/openai")
    assert not ia.e_openai("https://api.groq.com/openai/v1")
    assert not ia.e_openai("https://api.openai.com.golpe.io/v1")
    assert not ia.e_openai(None)


def test_corpo_da_openai_sem_max_tokens_nem_temperature():
    corpo = ia.corpo_compativel(_p("https://api.openai.com/v1"), "sis", "usu", max_tokens=300)
    assert "max_tokens" not in corpo and "temperature" not in corpo
    # O raciocínio gasta do mesmo teto: nunca menos que TETO_OPENAI.
    assert corpo["max_completion_tokens"] == ia.TETO_OPENAI
    assert corpo["messages"][0] == {"role": "system", "content": "sis"}
    assert corpo["model"] == "modelo-x"


def test_corpo_do_groq_como_sempre():
    corpo = ia.corpo_compativel(_p("https://api.groq.com/openai/v1"), "sis", "usu", max_tokens=900)
    assert corpo["max_tokens"] == 900
    assert corpo["temperature"] == 0.2
    assert "max_completion_tokens" not in corpo
