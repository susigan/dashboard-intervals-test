"""PR #42 — modo IR exploratorio: inicializacao sem AttributeError e nao persistencia.

    python -m pytest tests/pmc/test_ir_exploratorio_config.py

Offline, base SQLite temporaria (fase1_util). Sem Intervals.icu, sem Railway, sem base real.
Cobre a causa do erro 'NoneType' has no attribute 'CICLICOS': o parametro de configuracao do IR nao pode
esconder o modulo config. Verifica a rota real sem configuracao injetada, a resposta com cores e dados, e que
nenhuma tabela de ajustes ou de referencias do IR calibrado recebe escritas.
"""
import inspect
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fase1_util as U  # noqa: E402
import test_ir_mmp3 as M  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402

MOD = 'Bike'
VALS = [300.0] * 5 + [330.0]


def _curva(i, w):
    return M.c180(f'x{i}', MOD, f'2026-{i + 1:02d}-10', w)


class ExploratorioSemConfigInjetada(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.app = U.importar_app()
        except BaseException as e:
            raise unittest.SkipTest(f'app nao importa: {type(e).__name__}: {e}')
        cls.db = cls.app.db

    def setUp(self):
        self.curvas = [_curva(i, w) for i, w in enumerate(VALS)]
        self.ativ = [{'id': c['activity_id'], 'start_date_local': c['date'] + 'T07:30:00',
                      'icu_ignore_power': False, 'tags': []} for c in self.curvas]
        self._ctx = U.base_temporaria(self.db)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        for nome, kw in (('load_power_curves', dict(side_effect=lambda tipo=None, desde=None: list(self.curvas))),
                         ('load_activities', dict(side_effect=lambda desde=None: list(self.ativ)))):
            p = mock.patch.object(self.db, nome, **kw)
            p.start()
            self.addCleanup(p.stop)
        self.c = self.app.app.test_client()

    # 1. a assinatura nao esconde o modulo config
    def test_parametro_de_configuracao_nao_se_chama_config(self):
        params = inspect.signature(mis.analise_exploratoria).parameters
        self.assertNotIn('config', params)
        self.assertIn('config_ir', params)

    # 2. a chamada direta (sem argumentos) nao falha
    def test_chamada_direta_sem_configuracao(self):
        res = mis.analise_exploratoria()
        self.assertNotEqual(res.get('mensagem', ''), "'NoneType' object has no attribute 'CICLICOS'")
        self.assertIn(res['status'], ('OK', 'ERRO'))
        self.assertFalse(res['persistido'])
        self.assertIn('cores', res)

    # 3. a rota real responde com sucesso, sem persistir, com cores e dados
    def test_rota_responde_sem_erro_e_nao_persiste(self):
        r = self.c.get('/api/modelo/ir/exploratorio')
        dados = r.get_json()
        self.assertEqual(r.status_code, 200, dados)
        self.assertEqual(dados['status'], 'OK')
        self.assertIs(dados['persistido'], False)
        self.assertEqual(set(dados['cores']), {'Bike', 'Row', 'Run', 'Ski'})
        self.assertIn('itens', dados)

    # 4. a rota com duracao explicita tambem funciona
    def test_rota_com_duracao_historica(self):
        r = self.c.get('/api/modelo/ir/exploratorio?duracao=300')
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()['duracoes'], [300])

    # 5. nenhuma escrita no IR calibrado
    def test_nao_escreve_em_ajustes_nem_referencias(self):
        antes = {t: U.contar(self.db, t) for t in ('modelo_ir_ajustes', 'modelo_ir_p0_referencias')}
        self.c.get('/api/modelo/ir/exploratorio')
        mis.analise_exploratoria()
        depois = {t: U.contar(self.db, t) for t in antes}
        self.assertEqual(antes, depois)


if __name__ == '__main__':
    unittest.main()
