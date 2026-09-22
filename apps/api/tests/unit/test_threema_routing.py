"""Roteamento Threema por assunto, isolado de banco e serviços externos.

Executar de apps/api com --confcutdir=tests/unit para não carregar as
fixtures de integração do conftest principal.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.config import Settings
from app.services import threema

CHANNELS = (
    "logistica",
    "margem",
    "estoque",
    "devolucoes",
    "juridico",
    "importacao",
    "flex",
)
ROUTES = tuple((channel, channel) for channel in CHANNELS) + (
    ("controle_estoque", "estoque"),
    ("margem_auto", "margem"),
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Não lê .env nem usa o singleton de configuração do ambiente real."""
    values = {
        "threema_gateway_id": "*GLOBAL1",
        "threema_gateway_secret": "global-test-secret",
        "threema_separate_chats": False,
        "threema_context_channels": {},
        "threema_recipients": "ABCD1234, EFGH5678",
        "threema_recipient_names": "ABCD1234:Primeiro;EFGH5678:Segundo",
    }
    for channel in CHANNELS:
        values[f"threema_{channel}_gateway_id"] = ""
        values[f"threema_{channel}_gateway_secret"] = ""
    config = SimpleNamespace(**values)
    monkeypatch.setattr(threema, "get_settings", lambda: config)
    return config


def configure_channel(
    settings: SimpleNamespace,
    channel: str,
    gateway_id: str,
    secret: str,
) -> None:
    setattr(settings, f"threema_{channel}_gateway_id", gateway_id)
    setattr(settings, f"threema_{channel}_gateway_secret", secret)


def form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


@pytest.mark.parametrize(("contexto", "channel"), ROUTES)
def test_channel_and_alias_select_complete_sender_pair(settings, contexto, channel):
    configure_channel(settings, channel, " *CANAL01 ", " channel-test-secret ")

    client = threema.ThreemaClient(contexto=contexto)
    client._require_config()

    assert client.gateway_id == "*CANAL01"
    assert client.secret == "channel-test-secret"  # noqa: S105 — credencial fictícia


@pytest.mark.parametrize("contexto", [None, *CHANNELS, "controle_estoque", "margem_auto"])
def test_legacy_mode_uses_global_pair_for_unconfigured_channel(settings, contexto):
    client = threema.ThreemaClient(contexto=contexto)
    client._require_config()

    assert (client.gateway_id, client.secret) == ("*GLOBAL1", "global-test-secret")


def test_legacy_explicit_credentials_keep_working_without_context(settings):
    client = threema.ThreemaClient(" *EXPLICT ", " explicit-test-secret ")
    client._require_config()

    assert (client.gateway_id, client.secret) == ("*EXPLICT", "explicit-test-secret")


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    ("gateway_id", "secret"),
    [("*CANAL01", ""), ("", "channel-test-secret")],
)
async def test_partial_channel_pair_never_borrows_global_credential(
    settings, strict, gateway_id, secret
):
    settings.threema_separate_chats = strict
    configure_channel(settings, "logistica", gateway_id, secret)
    client = threema.ThreemaClient(contexto="logistica")

    assert (client.gateway_id, client.secret) == (gateway_id, secret)
    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError):
            await client.send_simple("ABCD1234", "aviso de logística")
        assert len(router.calls) == 0


@pytest.mark.parametrize("contexto", CHANNELS)
async def test_strict_mode_rejects_unconfigured_channel_before_http(settings, contexto):
    settings.threema_separate_chats = True
    client = threema.ThreemaClient(contexto=contexto)

    assert (client.gateway_id, client.secret) == ("", "")
    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError):
            await client.send_to_all("aviso", recipients=["ABCD1234"])
        assert len(router.calls) == 0


@pytest.mark.parametrize("strict", [False, True])
async def test_unknown_context_is_rejected_on_send_not_construction(settings, strict):
    settings.threema_separate_chats = strict
    client = threema.ThreemaClient(contexto="logisitca")

    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


@pytest.mark.parametrize("explicit_credentials", [False, True])
async def test_strict_mode_requires_context_even_with_explicit_credentials(
    settings, explicit_credentials
):
    settings.threema_separate_chats = True
    kwargs = (
        {"gateway_id": "*EXPLICT", "secret": "explicit-test-secret"} if explicit_credentials else {}
    )
    client = threema.ThreemaClient(**kwargs)

    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


async def test_strict_mode_rejects_shared_sender_between_channels_before_http(settings):
    settings.threema_separate_chats = True
    configure_channel(settings, "logistica", "*SHARED1", "logistica-test-secret")
    configure_channel(settings, "margem", "*SHARED1", "margem-test-secret")
    client = threema.ThreemaClient(contexto="logistica")

    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


@pytest.mark.parametrize("strict", [False, True])
async def test_each_subject_has_its_sender_for_the_same_private_recipient(settings, strict):
    settings.threema_separate_chats = strict
    expected = []
    for index, channel in enumerate(CHANNELS):
        gateway_id = f"*CANAL{index:02d}"
        secret = f"{channel}-test-secret"
        configure_channel(settings, channel, gateway_id, secret)
        expected.append(
            {
                "from": gateway_id,
                "to": "ABCD1234",
                "secret": secret,
                "text": f"aviso exclusivo de {channel}",
            }
        )

    with respx.mock(base_url=threema.THREEMA_API_BASE, assert_all_mocked=True) as router:
        route = router.post("/send_simple").mock(
            return_value=httpx.Response(200, text="message-id")
        )
        for channel in CHANNELS:
            client = threema.ThreemaClient(contexto=channel)
            assert await client.send_simple(" abcd1234 ", f"aviso exclusivo de {channel}") == (
                "message-id"
            )

        assert [form(call.request) for call in route.calls] == expected


@pytest.mark.parametrize("custom_recipients", [None, ["EFGH5678"]])
async def test_channel_routing_preserves_default_or_selected_recipients(
    settings, custom_recipients
):
    settings.threema_separate_chats = True
    configure_channel(settings, "flex", "*FLEX001", "flex-test-secret")
    client = threema.ThreemaClient(contexto="flex")
    expected_recipients = custom_recipients or ["ABCD1234", "EFGH5678"]
    original_recipients = custom_recipients.copy() if custom_recipients else None
    original_config = settings.threema_recipients

    with respx.mock(base_url=threema.THREEMA_API_BASE, assert_all_mocked=True) as router:
        route = router.post("/send_simple").mock(
            return_value=httpx.Response(200, text="message-id")
        )
        result = await client.send_to_all("entrega Flex", recipients=custom_recipients)

        assert result == {"sent": expected_recipients, "failed": []}
        assert [form(call.request) for call in route.calls] == [
            {
                "from": "*FLEX001",
                "to": recipient,
                "secret": "flex-test-secret",
                "text": "entrega Flex",
            }
            for recipient in expected_recipients
        ]
    assert custom_recipients == original_recipients
    assert settings.threema_recipients == original_config


async def test_channel_sender_preserves_per_recipient_failure_reporting(settings):
    configure_channel(settings, "logistica", "*LOGIST1", "logistica-test-secret")

    def respond(request: httpx.Request) -> httpx.Response:
        payload = form(request)
        assert payload["from"] == "*LOGIST1"
        assert payload["secret"] == "logistica-test-secret"  # noqa: S105 — credencial fictícia
        if payload["to"] == "EFGH5678":
            return httpx.Response(404, text="recipient not found")
        return httpx.Response(200, text="message-id")

    with respx.mock(base_url=threema.THREEMA_API_BASE, assert_all_mocked=True) as router:
        route = router.post("/send_simple").mock(side_effect=respond)
        result = await threema.ThreemaClient(contexto="logistica").send_to_all("aviso")

        assert result == {"sent": ["ABCD1234"], "failed": ["EFGH5678"]}
        assert len(route.calls) == 2


@pytest.mark.parametrize("strict", [False, True])
async def test_explicit_general_contexts_share_sender_and_keep_subject_and_recipients(
    settings, strict
):
    settings.threema_separate_chats = strict
    settings.threema_context_channels = {
        " CONTROLE_ESTOQUE ": " GERAL ",
        "estoque": "geral",
        "Importacao": "geral",
    }
    configure_channel(settings, "logistica", "*LOGIST1", "logistica-test-secret")
    expected = []

    with respx.mock(base_url=threema.THREEMA_API_BASE, assert_all_mocked=True) as router:
        route = router.post("/send_simple").mock(
            return_value=httpx.Response(200, text="message-id")
        )
        for contexto, normalized, canal, sender, secret in (
            (" Controle_Estoque ", "estoque", "geral", "*GLOBAL1", "global-test-secret"),
            ("importacao", "importacao", "geral", "*GLOBAL1", "global-test-secret"),
            ("logistica", "logistica", "logistica", "*LOGIST1", "logistica-test-secret"),
        ):
            client = threema.ThreemaClient(contexto=contexto)
            assert (client.contexto, client.canal) == (normalized, canal)
            result = await client.send_to_all(f"aviso de {normalized}", ["ABCD1234"])
            assert result == {"sent": ["ABCD1234"], "failed": []}
            expected.append(
                {
                    "from": sender,
                    "to": "ABCD1234",
                    "text": f"aviso de {normalized}",
                    "secret": secret,
                }
            )
        assert [form(call.request) for call in route.calls] == expected


async def test_general_contexts_do_not_enable_strict_fallback_for_other_subjects(settings):
    settings.threema_separate_chats = True
    settings.threema_context_channels = {"estoque": "geral", "importacao": "geral"}
    client = threema.ThreemaClient(contexto="logistica")

    assert (client.gateway_id, client.secret) == ("", "")
    assert client.canal == "logistica"
    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError, match="threema_gateway_id_missing"):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "mapping",
    [
        {"logisitca": "geral"},
        {"logistica": "gerla"},
        {"controle_estoque": "geral", "estoque": "margem"},
        {"estoque": "margem", "controle_estoque": "geral"},
        {"importacao": "controle_estoque"},
    ],
)
async def test_invalid_mapping_fails_at_send_even_for_dedicated_sender(settings, strict, mapping):
    settings.threema_separate_chats = strict
    settings.threema_context_channels = mapping
    configure_channel(settings, "margem", "*MARGEM1", "margem-test-secret")
    client = threema.ThreemaClient(contexto="margem")

    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError, match="threema_context_channels_invalid"):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


@pytest.mark.parametrize("contexto", ["estoque", "margem"])
async def test_strict_general_and_dedicated_channels_cannot_share_sender(settings, contexto):
    settings.threema_separate_chats = True
    settings.threema_context_channels = {"estoque": "geral", "importacao": "geral"}
    configure_channel(settings, "margem", " *global1 ", "margem-test-secret")
    client = threema.ThreemaClient(contexto=contexto)

    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(
            threema.ThreemaConfigError, match="threema_contexto_gateway_id_repetido"
        ):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


def test_general_assignment_ignores_inactive_dedicated_credentials_in_duplicate_check(settings):
    settings.threema_separate_chats = True
    settings.threema_context_channels = {"estoque": "geral", "importacao": "geral"}
    configure_channel(settings, "estoque", "*MARGEM1", "old-estoque-test-secret")
    configure_channel(settings, "margem", "*MARGEM1", "margem-test-secret")

    for contexto in ("estoque", "importacao", "margem"):
        client = threema.ThreemaClient(contexto=contexto)
        client._require_config()
        expected = (
            ("*MARGEM1", "margem-test-secret")
            if contexto == "margem"
            else ("*GLOBAL1", "global-test-secret")
        )
        assert (client.gateway_id, client.secret) == expected


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(("gateway_id", "secret"), [("", "global-test-secret"), ("*GLOBAL1", "")])
async def test_general_channel_requires_complete_global_pair(settings, strict, gateway_id, secret):
    settings.threema_separate_chats = strict
    settings.threema_context_channels = {"estoque": "geral"}
    settings.threema_gateway_id = gateway_id
    settings.threema_gateway_secret = secret
    configure_channel(settings, "estoque", "*ESTOQ01", "estoque-test-secret")
    client = threema.ThreemaClient(contexto="estoque")

    assert (client.gateway_id, client.secret) == (gateway_id, secret)
    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


async def test_general_assignment_does_not_allow_missing_context_in_strict_mode(settings):
    settings.threema_separate_chats = True
    settings.threema_context_channels = dict.fromkeys(CHANNELS, "geral")
    client = threema.ThreemaClient()

    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError, match="threema_contexto_missing"):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


async def test_four_profiles_deliver_active_subjects_and_block_juridico(settings):
    settings.threema_separate_chats = True
    settings.threema_context_channels = {
        "logistica": "geral",
        "juridico": "desativado",
        "importacao": "estoque",
    }
    for channel, sender in (
        ("margem", "*MARGEM1"),
        ("estoque", "*ESTOQ01"),
        ("devolucoes", "*DEVOL01"),
    ):
        configure_channel(settings, channel, sender, f"{channel}-test-secret")
    # Credenciais antigas do contexto desativado não tornam Margem duplicada.
    configure_channel(settings, "juridico", "*MARGEM1", "old-juridico-test-secret")
    expected = []
    with respx.mock(base_url=threema.THREEMA_API_BASE, assert_all_mocked=True) as router:
        route = router.post("/send_simple").mock(
            return_value=httpx.Response(200, text="message-id")
        )
        for contexto, canal, sender in (
            ("logistica", "geral", "*GLOBAL1"),
            ("margem", "margem", "*MARGEM1"),
            ("margem_auto", "margem", "*MARGEM1"),
            ("estoque", "estoque", "*ESTOQ01"),
            ("controle_estoque", "estoque", "*ESTOQ01"),
            ("importacao", "estoque", "*ESTOQ01"),
            ("devolucoes", "devolucoes", "*DEVOL01"),
        ):
            client = threema.ThreemaClient(contexto=contexto)
            assert client.canal == canal
            assert client.disabled is False
            assert await client.send_simple("ABCD1234", f"aviso de {contexto}") == "message-id"
            expected.append(
                {
                    "from": sender,
                    "to": "ABCD1234",
                    "text": f"aviso de {contexto}",
                    "secret": "global-test-secret" if canal == "geral" else f"{canal}-test-secret",
                }
            )
        assert [form(call.request) for call in route.calls] == expected
        assert len({request["from"] for request in expected}) == 4
        client = threema.ThreemaClient(contexto="juridico")
        assert client.disabled is True
        with pytest.raises(threema.ThreemaConfigError, match="threema_contexto_desativado"):
            await client.send_simple("ABCD1234", "aviso jurídico")
        assert [form(call.request) for call in route.calls] == expected


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    ("gateway_id", "secret"), [("", ""), ("*ESTOQ01", ""), ("", "estoque-test-secret")]
)
async def test_explicit_dedicated_mapping_never_falls_back_to_global(
    settings, strict, gateway_id, secret
):
    settings.threema_separate_chats = strict
    settings.threema_context_channels = {"importacao": "estoque"}
    configure_channel(settings, "estoque", gateway_id, secret)
    client = threema.ThreemaClient(contexto="importacao")

    assert (client.gateway_id, client.secret) == (gateway_id, secret)
    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError):
            await client.send_simple("ABCD1234", "aviso")
        assert len(router.calls) == 0


@pytest.mark.parametrize(
    "mapping",
    [
        {"importacao": "estoque", "estoque": "geral"},
        {"importacao": "estoque", "estoque": "importacao"},
    ],
)
def test_channel_mapping_resolves_directly_without_following_chains_or_cycles(settings, mapping):
    settings.threema_separate_chats = True
    settings.threema_context_channels = mapping
    configure_channel(settings, "estoque", "*ESTOQ01", "estoque-test-secret")
    configure_channel(settings, "importacao", "*IMPORT1", "importacao-test-secret")

    client = threema.ThreemaClient(contexto="importacao")
    client._require_config()
    assert (client.contexto, client.canal) == ("importacao", "estoque")
    assert (client.gateway_id, client.secret) == ("*ESTOQ01", "estoque-test-secret")


def test_settings_load_context_channels_from_json_environment(monkeypatch):
    monkeypatch.setenv(
        "THREEMA_CONTEXT_CHANNELS", '{"importacao":"estoque","juridico":"desativado"}'
    )
    config = Settings(_env_file=None, database_url="postgresql://unit-test/davinci")
    assert config.threema_context_channels == {"importacao": "estoque", "juridico": "desativado"}


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("explicit_credentials", [False, True])
@pytest.mark.parametrize("method", ["send_simple", "send_to_all"])
async def test_disabled_context_blocks_send_even_with_available_credentials(
    settings, strict, explicit_credentials, method
):
    settings.threema_separate_chats = strict
    settings.threema_context_channels = {"juridico": "desativado"}
    configure_channel(settings, "juridico", "*JURIDI1", "juridico-test-secret")
    kwargs = (
        {"gateway_id": "*EXPLICT", "secret": "explicit-test-secret"} if explicit_credentials else {}
    )
    client = threema.ThreemaClient(contexto="juridico", **kwargs)

    assert (client.contexto, client.canal, client.disabled) == ("juridico", "desativado", True)
    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError, match="threema_contexto_desativado"):
            if method == "send_simple":
                await client.send_simple("ABCD1234", "aviso jurídico")
            else:
                await client.send_to_all("aviso jurídico", ["ABCD1234"])
        assert len(router.calls) == 0


@pytest.mark.parametrize(
    ("alias", "contexto"), [(" Controle_Estoque ", "estoque"), (" MARGEM_AUTO ", "margem")]
)
async def test_disabled_target_normalizes_alias_and_case(settings, alias, contexto):
    settings.threema_context_channels = {alias: " DESATIVADO "}
    client = threema.ThreemaClient(contexto=contexto)

    assert client.contexto == contexto
    assert client.disabled is True
    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(threema.ThreemaConfigError, match="threema_contexto_desativado"):
            await client.send_to_all("aviso")
        assert len(router.calls) == 0


async def test_disabled_juridico_forwarding_stops_before_queries_or_chamado_mutation(
    settings, monkeypatch
):
    from app import config as app_config

    isolated = Settings(_env_file=None, database_url="postgresql://unit-test/davinci")
    monkeypatch.setattr(app_config, "get_settings", lambda: isolated)
    from app.services import chamados_juridico

    settings.threema_context_channels = {"juridico": "desativado"}
    session = Mock()
    recipients = AsyncMock(side_effect=AssertionError("destinatários não devem ser consultados"))
    dossier = AsyncMock(side_effect=AssertionError("dossiê não deve ser carregado"))
    monkeypatch.setattr(chamados_juridico, "recipients_juridico", recipients)
    monkeypatch.setattr(chamados_juridico, "dados_dossie", dossier)
    chamado = SimpleNamespace(
        juridico_token=None,
        juridico_enviado_at=None,
        juridico_enviado_por=None,
        juridico_obs=None,
        juridico_destinatarios=None,
    )
    original = vars(chamado).copy()
    user = SimpleNamespace(id="unit-user", name="Unit User", email="unit@example.invalid")

    with respx.mock(assert_all_mocked=True) as router:
        with pytest.raises(
            chamados_juridico.chamados_svc.ChamadoError, match="threema_juridico_desativado"
        ):
            await chamados_juridico.encaminhar(session, chamado, user, "observação")
        assert len(router.calls) == 0
    assert vars(chamado) == original
    assert session.mock_calls == []
    recipients.assert_not_awaited()
    dossier.assert_not_awaited()
