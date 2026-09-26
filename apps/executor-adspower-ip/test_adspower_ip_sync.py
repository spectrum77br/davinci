"""Testes do serviço do Mac que aplica o IP no AdsPower.

Rodam com o Python do macOS, sem instalar nada:
    /usr/bin/python3 -m unittest -v test_adspower_ip_sync

O AdsPower e o proxy são simulados: nenhum teste toca em perfil de verdade.
"""

import unittest
from unittest import mock

import adspower_ip_sync as s

# Duas contas de proxy, como no AdsPower real: mesmo usuário, senha por grupo.
CONTA_A = {"proxy_soft": "other", "proxy_type": "socks5", "proxy_port": "7128",
           "proxy_user": "usuario-secreto", "proxy_password": "senha-A-secreta"}
CONTA_B = dict(CONTA_A, proxy_password="senha-B-secreta")


def cfg(conta, host):
    return dict(conta, proxy_host=host)


def perfil(uid, n, config):
    return {"user_id": uid, "serial_number": n, "name": uid, "user_proxy_config": dict(config)}


def pendencia(ip, *ids, **extra):
    return {
        "company_id": "c1",
        "apelido": "KFA",
        "ip": ip,
        "perfis": [{"user_id": u, "profile_no": str(i), "nome": u} for i, u in enumerate(ids, 84)],
        **extra,
    }


class AdsPowerFalso:
    """Perfis em memória; registra toda gravação."""

    def __init__(self, perfis):
        self.perfis = {p["user_id"]: p for p in perfis}
        self.gravacoes = []

    def __call__(self, caminho, corpo=None):
        if caminho.startswith("/api/v1/user/update"):
            self.gravacoes.append(corpo)
            self.perfis[corpo["user_id"]]["user_proxy_config"] = dict(corpo["user_proxy_config"])
            return {}
        if "user_id=" in caminho:
            uid = caminho.split("user_id=")[1].split("&")[0]
            p = self.perfis.get(uid)
            return {"list": [p] if p else []}
        return {"list": list(self.perfis.values())}


def proxy_que_aceita(conta_certa, ip_de_saida=None):
    """Simula o proxy: só a conta certa passa, e sai pelo próprio endereço."""
    def testar(c):
        if s._conta(c) != s._conta(conta_certa):
            raise s.Falha(f"proxy {c['proxy_host']}:{c['proxy_port']}: o proxy recusou o usuário/senha")
        return ip_de_saida or c["proxy_host"]
    return testar


class Contas(unittest.TestCase):
    def setUp(self):
        s._SEGREDOS.clear()

    def test_quem_ja_usa_o_ip_vem_primeiro(self):
        todos = [perfil("x", 1, cfg(CONTA_A, "9.9.9.1")), perfil("y", 2, cfg(CONTA_B, "72.60.9.9"))]
        ordem = s.contas_candidatas("72.60.9.9", [cfg(CONTA_A, "1.1.1.1")], todos)
        self.assertEqual(ordem[0], s._conta(CONTA_B))

    def test_sem_ninguem_no_ip_tenta_a_da_empresa_e_depois_as_outras(self):
        todos = [perfil("x", 1, cfg(CONTA_A, "9.9.9.1")), perfil("y", 2, cfg(CONTA_B, "9.9.9.2"))]
        ordem = s.contas_candidatas("72.60.9.9", [cfg(CONTA_B, "1.1.1.1")], todos)
        self.assertEqual(ordem, [s._conta(CONTA_B), s._conta(CONTA_A)])

    def test_ip_de_outro_grupo_de_senha_acha_a_conta_certa(self):
        """O defeito da revisão: manter a senha antiga do perfil falhava sempre."""
        with mock.patch.object(s, "testar_proxy", proxy_que_aceita(CONTA_B)):
            conta = s.conta_que_funciona("72.60.9.9", [s._conta(CONTA_A), s._conta(CONTA_B)])
        self.assertEqual(conta, s._conta(CONTA_B))

    def test_nenhuma_conta_serve_explica_o_que_fazer(self):
        with mock.patch.object(s, "testar_proxy", proxy_que_aceita(dict(CONTA_A, proxy_port="1"))):
            with self.assertRaises(s.Falha) as ctx:
                s.conta_que_funciona("72.60.9.9", [s._conta(CONTA_A)])
        self.assertIn("compra nova", str(ctx.exception))
        self.assertIn("Nada foi trocado", str(ctx.exception))


class Seguranca(unittest.TestCase):
    def setUp(self):
        s._SEGREDOS.clear()

    def test_credencial_nunca_sai_em_texto(self):
        s._guardar_segredos(CONTA_A)
        texto = s._limpo("falhou com usuario-secreto e senha-A-secreta")
        self.assertNotIn("usuario-secreto", texto)
        self.assertNotIn("senha-A-secreta", texto)

    def test_senha_que_contem_o_usuario_some_inteira(self):
        s._guardar_segredos({"proxy_user": "ab12", "proxy_password": "ab12xyz"})
        self.assertNotIn("xyz", s._limpo("senha: ab12xyz"))

    def test_credencial_vai_ao_curl_pela_entrada_e_curlrc_e_ignorado(self):
        c = cfg(CONTA_A, "72.60.9.9")
        with mock.patch.object(s.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="72.60.9.9\n")
            self.assertEqual(s.testar_proxy(c), "72.60.9.9")
        args, kwargs = run.call_args
        self.assertEqual(args[0][:2], ["/usr/bin/curl", "-q"])  # -q primeiro: ignora ~/.curlrc
        self.assertNotIn("senha-A-secreta", " ".join(args[0]))
        self.assertIn("senha-A-secreta", kwargs["input"])
        self.assertIn("fail", kwargs["input"].split("\n"))

    def test_quebra_de_linha_na_senha_nao_vira_outra_linha_do_curl(self):
        self.assertEqual(s._aspas('a"b\nc'), '"a\\"b\\nc"')

    def test_eco_que_responde_lixo_nao_vira_ip(self):
        c = cfg(CONTA_A, "72.60.9.9")
        with mock.patch.object(s.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="<html>erro</html>")
            with self.assertRaises(s.Falha):
                s.testar_proxy(c)

    def test_recusa_de_senha_e_explicada(self):
        c = cfg(CONTA_A, "72.60.9.9")
        with mock.patch.object(s.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=97, stdout="")
            with self.assertRaises(s.Falha) as ctx:
                s.testar_proxy(c)
        self.assertIn("recusou o usuário/senha", str(ctx.exception))
        self.assertEqual(run.call_count, 1)  # recusa é do proxy: não pergunta ao 2º eco

    def test_nao_usa_proxy_do_sistema_nem_segue_redirecionamento(self):
        handlers = [type(h).__name__ for h in s._ABRIR.handlers]
        self.assertIn("_SemRedirecionar", handlers)
        proxy = [h for h in s._ABRIR.handlers if isinstance(h, s.urllib.request.ProxyHandler)]
        self.assertTrue(all(h.proxies == {} for h in proxy))


class AplicarEmpresa(unittest.TestCase):
    def setUp(self):
        s._SEGREDOS.clear()

    def rodar(self, perfis, pend, conta_certa, simular=False):
        falso = AdsPowerFalso(perfis)
        with mock.patch.object(s, "adspower", falso), mock.patch.object(
            s, "testar_proxy", proxy_que_aceita(conta_certa)
        ):
            try:
                return falso, s.aplicar_empresa(pend, simular=simular), None
            except s.Falha as e:
                return falso, None, e

    def test_ip_novo_troca_todos_os_perfis_com_a_conta_que_funciona(self):
        perfis = [perfil("a", 84, cfg(CONTA_A, "1.1.1.1")), perfil("b", 85, cfg(CONTA_A, "1.1.1.1")),
                  perfil("modelo", 90, cfg(CONTA_B, "2.2.2.2"))]
        falso, _, erro = self.rodar(perfis, pendencia("72.60.9.9", "a", "b"), CONTA_B)
        self.assertIsNone(erro)
        self.assertEqual(len(falso.gravacoes), 2)
        for uid in ("a", "b"):
            c = falso.perfis[uid]["user_proxy_config"]
            self.assertEqual(c["proxy_host"], "72.60.9.9")
            self.assertEqual(c["proxy_password"], "senha-B-secreta")

    def test_perfil_que_ja_esta_no_ip_nao_e_regravado_nem_testado(self):
        perfis = [perfil("a", 84, cfg(CONTA_A, "72.60.9.9"))]
        falso = AdsPowerFalso(perfis)
        with mock.patch.object(s, "adspower", falso), mock.patch.object(s, "testar_proxy") as teste:
            s.aplicar_empresa(pendencia("72.60.9.9", "a"), simular=False)
        self.assertEqual(falso.gravacoes, [])
        teste.assert_not_called()

    def test_nenhuma_conta_funciona_nada_e_gravado(self):
        perfis = [perfil("a", 84, cfg(CONTA_A, "1.1.1.1"))]
        falso, _, erro = self.rodar(perfis, pendencia("72.60.9.9", "a"), dict(CONTA_A, proxy_port="1"))
        self.assertIn("Nada foi trocado", str(erro))
        self.assertEqual(falso.gravacoes, [])

    def test_simulacao_nao_grava(self):
        perfis = [perfil("a", 84, cfg(CONTA_A, "1.1.1.1"))]
        falso, resumo, erro = self.rodar(perfis, pendencia("72.60.9.9", "a"), CONTA_A, simular=True)
        self.assertIsNone(erro)
        self.assertIn("SIMULAÇÃO", resumo)
        self.assertEqual(falso.gravacoes, [])

    def test_nunca_tira_o_proxy_de_um_perfil(self):
        perfis = [perfil("a", 84, cfg(CONTA_A, "1.1.1.1"))]
        falso, _, _ = self.rodar(perfis, pendencia("72.60.9.9", "a"), CONTA_A)
        for g in falso.gravacoes:
            self.assertNotEqual(g["user_proxy_config"]["proxy_soft"], "no_proxy")
            self.assertTrue(g["user_proxy_config"]["proxy_host"])

    def test_perfil_sem_proxy_recebe_a_conta_que_funciona(self):
        perfis = [perfil("novo", 170, {"proxy_soft": "no_proxy"}), perfil("m", 84, cfg(CONTA_A, "1.1.1.1"))]
        falso, _, erro = self.rodar(perfis, pendencia("72.60.9.9", "novo"), CONTA_A)
        self.assertIsNone(erro)
        self.assertEqual(falso.perfis["novo"]["user_proxy_config"]["proxy_host"], "72.60.9.9")

    def test_perfil_dividido_com_outra_empresa_fica_como_esta(self):
        perfis = [perfil("a", 84, cfg(CONTA_A, "1.1.1.1")), perfil("dividido", 83, cfg(CONTA_A, "1.1.1.1"))]
        pend = pendencia("72.60.9.9", "a",
                         compartilhados=[{"user_id": "dividido", "profile_no": "83", "nome": "x"}])
        falso, _, erro = self.rodar(perfis, pend, CONTA_A)
        self.assertIn("atende outra empresa", str(erro))
        self.assertEqual([g["user_id"] for g in falso.gravacoes], ["a"])
        self.assertEqual(falso.perfis["dividido"]["user_proxy_config"]["proxy_host"], "1.1.1.1")

    def test_perfil_do_outro_adspower_nao_impede_os_deste_mac(self):
        """O da Contabilidade fica em outro AdsPower: os deste Mac recebem o IP
        mesmo assim, e a tela diz qual trocar à mão."""
        perfis = [perfil("a", 84, cfg(CONTA_A, "1.1.1.1"))]
        falso, _, erro = self.rodar(perfis, pendencia("72.60.9.9", "a", "da-contabilidade"), CONTA_A)
        self.assertEqual([g["user_id"] for g in falso.gravacoes], ["a"])
        self.assertIn("não está no AdsPower deste Mac", str(erro))
        self.assertIn("à mão", str(erro))


class Passada(unittest.TestCase):
    """O laço de uma passada: o que é reportado ao DaVinci."""

    def setUp(self):
        s._SEGREDOS.clear()
        self.reportes = []

        def davinci(metodo, caminho, corpo=None):
            if metodo == "GET":
                return [pendencia("72.60.9.9", "a")]
            self.reportes.append(corpo)
            return {"registrado": True}

        self.davinci = davinci

    def test_adspower_ocupado_sem_nada_gravado_nao_castiga_a_empresa(self):
        """'Too many request' é passageiro: tenta no minuto seguinte, não em 1 hora."""
        def ocupado(caminho, corpo=None):
            raise s.FalhaPassageira("o AdsPower está recusando por excesso de chamadas")

        with mock.patch.object(s, "davinci", self.davinci), mock.patch.object(s, "adspower", ocupado):
            s.passada(simular=False, tambem_sem_proxy=True)
        self.assertEqual(self.reportes, [])

    def test_falha_de_verdade_e_reportada(self):
        perfis = [perfil("a", 84, cfg(CONTA_A, "1.1.1.1"))]
        with mock.patch.object(s, "davinci", self.davinci), mock.patch.object(
            s, "adspower", AdsPowerFalso(perfis)
        ), mock.patch.object(s, "testar_proxy", proxy_que_aceita(dict(CONTA_A, proxy_port="1"))):
            s.passada(simular=False, tambem_sem_proxy=True)
        self.assertEqual(len(self.reportes), 1)
        self.assertFalse(self.reportes[0]["ok"])
        self.assertNotIn("senha-A-secreta", self.reportes[0]["erro"])

    def test_sucesso_e_reportado(self):
        perfis = [perfil("a", 84, cfg(CONTA_A, "1.1.1.1"))]
        with mock.patch.object(s, "davinci", self.davinci), mock.patch.object(
            s, "adspower", AdsPowerFalso(perfis)
        ), mock.patch.object(s, "testar_proxy", proxy_que_aceita(CONTA_A)):
            s.passada(simular=False, tambem_sem_proxy=True)
        self.assertEqual(self.reportes, [{"company_id": "c1", "ip": "72.60.9.9", "ok": True, "erro": None,
                                          "rev": None}])

    def test_sem_proxy_cadastrado_fica_com_o_servico_antigo(self):
        """O serviço antigo, no outro computador, ainda cuida delas: os dois
        juntos brigariam pela mesma empresa."""
        falso = AdsPowerFalso([perfil("a", 84, cfg(CONTA_A, "1.1.1.1"))])
        with mock.patch.object(s, "davinci", self.davinci), mock.patch.object(s, "adspower", falso), \
                mock.patch.object(s, "testar_proxy", proxy_que_aceita(CONTA_A)):
            s.passada(simular=False)
        self.assertEqual((falso.gravacoes, self.reportes), ([], []))


# --- versão 2: proxy inteiro cadastrado na empresa (26/09/2026) ---------------
CONTA_NOVA = {"proxy_soft": "other", "proxy_type": "socks5", "proxy_port": "7128",
              "proxy_user": "pxnovo01", "proxy_password": "SenhaDoVpsNovo123"}


def pend_v2(ip, *ids, rev=3, **extra):
    return pendencia(ip, *ids, rev=rev, proxy={"tipo": "socks5", "porta": 7128,
                                               "usuario": "pxnovo01", "senha": "SenhaDoVpsNovo123"}, **extra)


class ProxyCadastrado(unittest.TestCase):
    """Eduardo: 'quando mudarmos o ip corrige corretamente e salva e já deixa no ar'."""

    def setUp(self):
        s._SEGREDOS.clear()

    def rodar(self, perfis, pend, conta_certa, ip_de_saida=None, simular=False):
        falso = AdsPowerFalso(perfis)
        testados = []

        def testar(c):
            testados.append(dict(c))
            return proxy_que_aceita(conta_certa, ip_de_saida)(c)

        with mock.patch.object(s, "adspower", falso), mock.patch.object(s, "testar_proxy", testar):
            try:
                return falso, testados, s.aplicar_empresa(pend, simular=simular), None
            except s.Falha as e:
                return falso, testados, None, e

    def test_usa_so_o_proxy_cadastrado_e_grava_em_todos(self):
        perfis = [perfil("a", 34, cfg(CONTA_A, "198.51.100.7")), perfil("b", 113, cfg(CONTA_B, "198.51.100.7")),
                  perfil("novo", 61, {"proxy_soft": "no_proxy"})]
        falso, testados, _, erro = self.rodar(perfis, pend_v2("203.0.113.10", "a", "b", "novo"), CONTA_NOVA)
        self.assertIsNone(erro)
        self.assertEqual(len(testados), 1)  # não sai tentando outras contas
        self.assertEqual(testados[0]["proxy_user"], "pxnovo01")
        self.assertEqual(len(falso.gravacoes), 3)
        for uid in ("a", "b", "novo"):
            c = falso.perfis[uid]["user_proxy_config"]
            self.assertEqual((c["proxy_host"], c["proxy_port"], c["proxy_user"], c["proxy_password"]),
                             ("203.0.113.10", "7128", "pxnovo01", "SenhaDoVpsNovo123"))

    def test_mesmo_ip_com_senha_errada_no_perfil_e_corrigido(self):
        """Antes só o IP importava: perfil já no IP com a senha antiga ficava errado."""
        perfis = [perfil("a", 34, cfg(dict(CONTA_NOVA, proxy_password="antiga"), "203.0.113.10")),
                  perfil("b", 113, cfg(CONTA_NOVA, "203.0.113.10"))]
        falso, _, _, erro = self.rodar(perfis, pend_v2("203.0.113.10", "a", "b"), CONTA_NOVA)
        self.assertIsNone(erro)
        self.assertEqual([g["user_id"] for g in falso.gravacoes], ["a"])  # o b já estava certo

    def test_tudo_certo_nao_grava_nem_testa(self):
        perfis = [perfil("a", 34, cfg(CONTA_NOVA, "203.0.113.10"))]
        falso, testados, _, erro = self.rodar(perfis, pend_v2("203.0.113.10", "a"), CONTA_NOVA)
        self.assertIsNone(erro)
        self.assertEqual((falso.gravacoes, testados), ([], []))

    def test_proxy_cadastrado_que_nao_funciona_nao_troca_nada(self):
        perfis = [perfil("a", 34, cfg(CONTA_A, "198.51.100.7"))]
        falso, testados, _, erro = self.rodar(perfis, pend_v2("203.0.113.10", "a"), CONTA_A)
        self.assertIn("proxy cadastrado no DaVinci não funcionou", str(erro))
        self.assertIn("Nada foi trocado", str(erro))
        self.assertEqual(falso.gravacoes, [])
        self.assertEqual(len(testados), 1)  # não tenta as contas do AdsPower

    def test_proxy_que_sai_por_outro_ip_nao_troca_nada(self):
        perfis = [perfil("a", 34, cfg(CONTA_A, "198.51.100.7"))]
        falso, _, _, erro = self.rodar(perfis, pend_v2("203.0.113.10", "a"), CONTA_NOVA, ip_de_saida="8.8.8.8")
        self.assertIn("saiu pelo IP 8.8.8.8", str(erro))
        self.assertEqual(falso.gravacoes, [])

    def test_cadastro_incompleto_e_recusado(self):
        pend = pendencia("203.0.113.10", "a", proxy={"tipo": "socks5", "porta": 7128, "usuario": "px", "senha": ""})
        falso, _, _, erro = self.rodar([perfil("a", 34, cfg(CONTA_A, "1.1.1.1"))], pend, CONTA_NOVA)
        self.assertIn("incompleto", str(erro))
        self.assertEqual(falso.gravacoes, [])

    def test_simulacao_nao_grava(self):
        perfis = [perfil("a", 34, cfg(CONTA_A, "198.51.100.7"))]
        falso, _, resumo, erro = self.rodar(perfis, pend_v2("203.0.113.10", "a"), CONTA_NOVA, simular=True)
        self.assertIsNone(erro)
        self.assertIn("trocaria 1 perfil", resumo)
        self.assertEqual(falso.gravacoes, [])

    def test_extra_que_esta_no_ip_de_outra_empresa_nao_e_trocado(self):
        """Número de perfil extra digitado errado não tira outra empresa do ar."""
        pend = pend_v2("203.0.113.10", "a", "x", ips_de_outras=["203.0.113.20", "198.51.100.99"])
        pend["perfis"][1]["origem"] = "extra"
        perfis = [perfil("a", 34, cfg(CONTA_A, "198.51.100.7")), perfil("x", 85, cfg(CONTA_B, "203.0.113.20"))]
        falso, _, _, erro = self.rodar(perfis, pend, CONTA_NOVA)
        self.assertEqual([g["user_id"] for g in falso.gravacoes], ["a"])
        self.assertIn("n85 em 203.0.113.20", str(erro))
        self.assertEqual(falso.perfis["x"]["user_proxy_config"]["proxy_host"], "203.0.113.20")

    def test_perfil_de_loja_no_ip_antigo_de_outra_empresa_e_trocado(self):
        """A trava é só para extra: o perfil da loja vem do cadastro da loja."""
        pend = pend_v2("203.0.113.10", "a", ips_de_outras=["198.51.100.7"])
        falso, _, _, erro = self.rodar([perfil("a", 34, cfg(CONTA_A, "198.51.100.7"))], pend, CONTA_NOVA)
        self.assertIsNone(erro)
        self.assertEqual([g["user_id"] for g in falso.gravacoes], ["a"])


class PassadaV2(unittest.TestCase):
    def setUp(self):
        s._SEGREDOS.clear()
        self.reportes, self.caminhos = [], []

    def davinci(self, pendentes):
        def f(metodo, caminho, corpo=None):
            if metodo == "GET":
                self.caminhos.append(caminho)
                return pendentes
            self.reportes.append(corpo)
            return {"registrado": True}
        return f

    def test_pede_a_versao_2_e_devolve_o_rev(self):
        perfis = [perfil("a", 34, cfg(CONTA_A, "1.1.1.1"))]
        with mock.patch.object(s, "davinci", self.davinci([pend_v2("203.0.113.10", "a", rev=7)])), \
                mock.patch.object(s, "adspower", AdsPowerFalso(perfis)), \
                mock.patch.object(s, "testar_proxy", proxy_que_aceita(CONTA_NOVA)):
            s.passada(simular=False)
        self.assertTrue(self.caminhos[0].endswith("?v=2"))
        self.assertEqual(self.reportes[0]["rev"], 7)
        self.assertTrue(self.reportes[0]["ok"])

    def test_senha_cadastrada_nunca_vai_no_erro_nem_no_log(self):
        perfis = [perfil("a", 34, cfg(CONTA_A, "1.1.1.1"))]

        def testar_que_vaza(c):
            raise s.Falha(f"falhou com {c['proxy_user']}:{c['proxy_password']}")

        with mock.patch.object(s, "davinci", self.davinci([pend_v2("203.0.113.10", "a")])), \
                mock.patch.object(s, "adspower", AdsPowerFalso(perfis)), \
                mock.patch.object(s, "testar_proxy", testar_que_vaza), \
                self.assertLogs("adspower_ip", level="INFO") as logs:
            s.passada(simular=False)
        tudo = self.reportes[0]["erro"] + "\n".join(logs.output)
        self.assertNotIn("SenhaDoVpsNovo123", tudo)
        self.assertNotIn("pxnovo01", tudo)

    def test_empresa_filtra_so_uma(self):
        outra = dict(pend_v2("203.0.113.20", "b"), apelido="Inova", company_id="c2")
        barbosa = dict(pend_v2("203.0.113.10", "a"), apelido="Barbosa")
        perfis = [perfil("a", 34, cfg(CONTA_A, "1.1.1.1")), perfil("b", 53, cfg(CONTA_A, "1.1.1.1"))]
        falso = AdsPowerFalso(perfis)
        with mock.patch.object(s, "davinci", self.davinci([outra, barbosa])), \
                mock.patch.object(s, "adspower", falso), \
                mock.patch.object(s, "testar_proxy", proxy_que_aceita(CONTA_NOVA)):
            s.passada(simular=False, empresa=" barbosa ")
        self.assertEqual([g["user_id"] for g in falso.gravacoes], ["a"])
        self.assertEqual([r["company_id"] for r in self.reportes], ["c1"])

    def test_loop_repassa_empresa_e_sem_proxy(self):
        vistos = []

        def parar(_segundos):
            raise KeyboardInterrupt

        with mock.patch.object(s, "passada", lambda **k: vistos.append(k)), \
                mock.patch.object(s.time, "sleep", parar), self.assertRaises(KeyboardInterrupt):
            s.em_loop(simular=True, empresa="barbosa")
        self.assertEqual(vistos, [{"simular": True, "empresa": "barbosa", "tambem_sem_proxy": False}])


if __name__ == "__main__":
    unittest.main()
