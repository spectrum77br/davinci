"""Testes do serviço do Mac que aplica o IP no AdsPower.

Rodam com o Python do macOS, sem instalar nada:
    /usr/bin/python3 -m unittest -v test_adspower_ip_sync

O AdsPower e o proxy são simulados: nenhum teste toca em perfil de verdade.
"""

import unittest
from unittest import mock

import adspower_ip_sync as s

PLANO = {
    "proxy_soft": "other",
    "proxy_type": "socks5",
    "proxy_host": "72.60.1.1",
    "proxy_port": "7128",
    "proxy_user": "usuario-secreto",
    "proxy_password": "senha-secreta",
}


class AdsPowerFalso:
    """Guarda os perfis em memória e registra toda gravação."""

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


def perfil(uid, n, cfg):
    return {"user_id": uid, "serial_number": n, "name": uid, "user_proxy_config": dict(cfg)}


def pendencia(ip, *ids, **extra):
    return {
        "company_id": "c1",
        "apelido": "KFA",
        "ip": ip,
        "perfis": [{"user_id": u, "profile_no": str(i), "nome": u} for i, u in enumerate(ids, 1)],
        **extra,
    }


class Regras(unittest.TestCase):
    def setUp(self):
        s._SEGREDOS.clear()

    def test_perfil_com_proxy_mantem_o_plano_e_troca_so_o_endereco(self):
        novo = s.proxy_novo(PLANO, "72.60.9.9", padrao=None)
        self.assertEqual(novo["proxy_host"], "72.60.9.9")
        self.assertEqual(novo["proxy_port"], "7128")
        self.assertEqual(novo["proxy_user"], "usuario-secreto")

    def test_perfil_sem_proxy_usa_o_plano_padrao(self):
        novo = s.proxy_novo({"proxy_soft": "no_proxy"}, "72.60.9.9", padrao=PLANO)
        self.assertEqual((novo["proxy_host"], novo["proxy_port"]), ("72.60.9.9", "7128"))

    def test_sem_proxy_e_sem_plano_nao_inventa(self):
        with self.assertRaises(s.Falha):
            s.proxy_novo({"proxy_soft": "no_proxy"}, "72.60.9.9", padrao=None)

    def test_plano_padrao_e_o_mais_usado(self):
        outro = dict(PLANO, proxy_port="13500", proxy_password="outra")
        perfis = [perfil("a", 1, PLANO), perfil("b", 2, PLANO), perfil("c", 3, outro)]
        self.assertEqual(s.plano_padrao(perfis)["proxy_port"], "7128")

    def test_credencial_nunca_sai_em_texto(self):
        s._guardar_segredos(PLANO)
        texto = s._limpo("falhou com usuario-secreto e senha-secreta")
        self.assertNotIn("usuario-secreto", texto)
        self.assertNotIn("senha-secreta", texto)

    def test_credencial_vai_ao_curl_pela_entrada_e_nao_pelo_comando(self):
        """Na linha de comando, qualquer processo da máquina veria a senha."""
        cfg = dict(PLANO, proxy_host="72.60.9.9")
        with mock.patch.object(s.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="72.60.9.9\n")
            self.assertEqual(s.testar_proxy(cfg), "72.60.9.9")
        args, kwargs = run.call_args
        self.assertNotIn("senha-secreta", " ".join(args[0]))
        self.assertIn("senha-secreta", kwargs["input"])


class AplicarEmpresa(unittest.TestCase):
    def setUp(self):
        s._SEGREDOS.clear()

    def rodar(self, perfis, pend, saida_do_proxy, simular=False):
        falso = AdsPowerFalso(perfis)
        with mock.patch.object(s, "adspower", falso), mock.patch.object(
            s, "testar_proxy", return_value=saida_do_proxy
        ) as teste:
            try:
                resumo = s.aplicar_empresa(pend, simular=simular)
                erro = None
            except s.Falha as e:
                resumo, erro = None, str(e)
        return falso, teste, resumo, erro

    def test_ip_novo_troca_todos_os_perfis_da_empresa(self):
        perfis = [perfil("a", 84, PLANO), perfil("b", 119, PLANO)]
        falso, _, resumo, erro = self.rodar(perfis, pendencia("72.60.9.9", "a", "b"), "72.60.9.9")
        self.assertIsNone(erro)
        self.assertEqual(len(falso.gravacoes), 2)
        self.assertTrue(all(p["user_proxy_config"]["proxy_host"] == "72.60.9.9" for p in falso.perfis.values()))

    def test_perfil_que_ja_esta_no_ip_nao_e_regravado(self):
        """O caso das 25 empresas já preenchidas: nada a fazer."""
        perfis = [perfil("a", 84, dict(PLANO, proxy_host="72.60.9.9"))]
        falso, teste, _, erro = self.rodar(perfis, pendencia("72.60.9.9", "a"), "72.60.9.9")
        self.assertIsNone(erro)
        self.assertEqual(falso.gravacoes, [])
        teste.assert_not_called()

    def test_proxy_que_sai_por_outro_ip_nao_mexe_em_nada(self):
        perfis = [perfil("a", 84, PLANO)]
        falso, _, _, erro = self.rodar(perfis, pendencia("72.60.9.9", "a"), "8.8.8.8")
        self.assertIn("nada foi trocado", erro)
        self.assertEqual(falso.gravacoes, [])

    def test_simulacao_nao_grava(self):
        perfis = [perfil("a", 84, PLANO)]
        falso, _, resumo, erro = self.rodar(perfis, pendencia("72.60.9.9", "a"), "72.60.9.9", simular=True)
        self.assertIsNone(erro)
        self.assertIn("SIMULAÇÃO", resumo)
        self.assertEqual(falso.gravacoes, [])

    def test_nunca_tira_o_proxy_de_um_perfil(self):
        perfis = [perfil("a", 84, PLANO)]
        falso, _, _, _ = self.rodar(perfis, pendencia("72.60.9.9", "a"), "72.60.9.9")
        for g in falso.gravacoes:
            self.assertNotEqual(g["user_proxy_config"]["proxy_soft"], "no_proxy")
            self.assertTrue(g["user_proxy_config"]["proxy_host"])

    def test_perfil_dividido_com_outra_empresa_fica_como_esta(self):
        perfis = [perfil("a", 84, PLANO), perfil("dividido", 83, PLANO)]
        pend = pendencia(
            "72.60.9.9", "a",
            compartilhados=[{"user_id": "dividido", "profile_no": "83", "nome": "x"}],
        )
        falso, _, _, erro = self.rodar(perfis, pend, "72.60.9.9")
        self.assertIn("atende outra empresa", erro)
        self.assertEqual([g["user_id"] for g in falso.gravacoes], ["a"])
        self.assertEqual(falso.perfis["dividido"]["user_proxy_config"]["proxy_host"], "72.60.1.1")

    def test_perfil_que_nao_esta_neste_adspower_e_avisado(self):
        falso, _, _, erro = self.rodar([], pendencia("72.60.9.9", "sumiu"), "72.60.9.9")
        self.assertIn("não está no AdsPower deste Mac", erro)

    def test_perfil_sem_proxy_recebe_o_plano_padrao(self):
        perfis = [perfil("novo", 170, {"proxy_soft": "no_proxy"}), perfil("modelo", 84, PLANO)]
        falso, _, _, erro = self.rodar(perfis, pendencia("72.60.9.9", "novo"), "72.60.9.9")
        self.assertIsNone(erro)
        cfg = falso.perfis["novo"]["user_proxy_config"]
        self.assertEqual((cfg["proxy_host"], cfg["proxy_port"]), ("72.60.9.9", "7128"))

    def test_erro_nao_carrega_credencial(self):
        perfis = [perfil("a", 84, PLANO)]
        falso = AdsPowerFalso(perfis)

        def adspower_que_falha(caminho, corpo=None):
            if corpo is not None:
                raise s.Falha("o AdsPower recusou: senha-secreta invalida")
            return falso(caminho, corpo)

        with mock.patch.object(s, "adspower", adspower_que_falha), mock.patch.object(
            s, "testar_proxy", return_value="72.60.9.9"
        ):
            with self.assertRaises(s.Falha) as ctx:
                s.aplicar_empresa(pendencia("72.60.9.9", "a"), simular=False)
        self.assertNotIn("senha-secreta", s._limpo(ctx.exception))


if __name__ == "__main__":
    unittest.main()
