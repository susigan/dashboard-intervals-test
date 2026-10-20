"""SEPARACAO das tres referencias do IR: deteccao sazonal, historico de YES e P0; e modo EXPLORATORIO isolado.

    python -m pytest tests/pmc/test_ir_separacao.py

Offline, dados SINTETICOS. Puros (sem base de dados) salvo a classe de persistencia (SQLite temporaria, da suite).
"""
import inspect
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
for _p in (HERE, ROOT, os.path.join(ROOT, 'utils')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import db  # noqa: E402
import modelo_ir as ir  # noqa: E402
import modelo_ir_exploratorio as mex  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402
import modelo_testes as mt  # noqa: E402


def esf(test_id, data, watts, estado=mm.E_ELEGIVEL, motivo=mm.M_ABAIXO, season='2026', modalidade='Bike'):
    """Esforco minimo no formato de avaliar_esforcos (campos lidos por p0_global e diagnostico)."""
    return {'test_id': test_id, 'activity_id': test_id, 'modalidade': modalidade, 'data': data,
            'inicio': f'{data}T08:00:00', 'watts': float(watts), 'pr': False, 'pct_do_melhor': 100.0,
            'estado': estado, 'motivo': motivo, 'a_verificar': estado == mm.E_A_VERIFICAR,
            'origem_classificacao': 'tag_manual', 'motivo_decisao': 'teste', 'classe_intervalo': None,
            'season': season, 'melhor_anterior_watts': None}


class P0ComHistorico(unittest.TestCase):
    """P0 = MMP3 do YES mais recente; o historico gravado e separado da deteccao e so sai por rejeicao manual."""

    def p0(self, esforcos, historico=None, rejeitados=None):
        return mm.p0_global({'Bike': esforcos}, historico_yes=historico, rejeitados=rejeitados)

    def test_yes_gravado_sobrevive_a_mudanca_de_temporada(self):
        # 2025 gravou YES 290 W; em 2026 nao ha nenhum YES vivo: o P0 continua 290 W (nao cai para outra fonte)
        hist = [{'test_id': 'Bike-2025', 'activity_id': 'Bike-2025', 'modalidade': 'Bike', 'data': '2025-06-10',
                 'inicio': None, 'watts': 290.0, 'season': '2025', 'origem_classificacao': 'tag_manual',
                 'classe_intervalo': None, 'motivo_decisao': 'x', 'referencia_watts': 300.0}]
        g = self.p0([esf('Bike-2026a', '2026-03-01', 200.0, estado=mm.E_NAO_ELEGIVEL, season='2026')], hist)
        self.assertEqual((g['estado'], g['registo']['watts'], g['registo']['origem_registo']),
                         ('valida', 290.0, 'historico'))

    def test_rejeicao_manual_retira_o_yes_gravado(self):
        hist = [{'test_id': 'Bike-2025', 'data': '2025-06-10', 'watts': 290.0, 'activity_id': 'Bike-2025',
                 'modalidade': 'Bike', 'season': '2025'}]
        g = self.p0([], hist, rejeitados={'Bike-2025'})
        self.assertEqual((g['estado'], g['registo'], g['n_yes']), ('ausente', None, 0))

    def test_no_pendente_e_a_verificar_nao_substituem_o_ultimo_yes(self):
        hist = [{'test_id': 'Bike-old', 'data': '2025-06-10', 'watts': 290.0, 'activity_id': 'Bike-old',
                 'modalidade': 'Bike', 'season': '2025'}]
        esforcos = [esf('Bike-no', '2026-05-01', 320.0, estado=mm.E_NAO_ELEGIVEL),
                    esf('Bike-av', '2026-06-01', 330.0, estado=mm.E_A_VERIFICAR, motivo=mm.M_RECORDE_SAZONAL)]
        g = self.p0(esforcos, hist)
        self.assertEqual(g['registo']['test_id'], 'Bike-old')            # nem NO nem a verificar entram no P0
        self.assertEqual(g['n_yes'], 1)

    def test_a_verificar_nunca_conta_como_yes(self):
        g = self.p0([esf('Bike-av', '2026-06-01', 330.0, estado=mm.E_A_VERIFICAR, motivo=mm.M_PRIMEIRO_DA_TEMPORADA)])
        self.assertEqual((g['estado'], g['n_yes']), ('ausente', 0))

    def test_ausente_difere_de_dados_insuficientes(self):
        self.assertEqual(self.p0([])['estado'], 'ausente')
        g = self.p0([esf('Bike-i', '2026-06-01', 100.0, estado=mm.E_INSUFICIENTE, motivo=mm.M_AMOSTRA)])
        self.assertEqual(g['estado'], 'dados_insuficientes')

    def test_yes_vivo_e_gravado_do_mesmo_teste_nao_duplicam(self):
        vivo = esf('Bike-x', '2026-06-01', 300.0)
        hist = [{'test_id': 'Bike-x', 'data': '2026-06-01', 'watts': 300.0, 'activity_id': 'Bike-x', 'modalidade': 'Bike'}]
        g = self.p0([vivo], hist)
        self.assertEqual(g['n_yes'], 1)
        self.assertEqual(g['n_yes_historicos'], 0)

    def test_sem_historico_o_resultado_e_o_de_antes(self):
        vivo = esf('Bike-x', '2026-06-01', 300.0)
        self.assertEqual(self.p0([vivo])['registo']['test_id'], 'Bike-x')
        self.assertEqual(self.p0([vivo])['n_yes_historicos'], 0)

    def test_p0_nao_muda_com_a_temporada_sem_novo_yes(self):
        # o mesmo historico com temporadas diferentes nao muda o P0 (a season so entra no contexto de deteccao)
        hist = [{'test_id': 'Bike-t', 'data': '2025-06-10', 'watts': 290.0, 'activity_id': 'Bike-t',
                 'modalidade': 'Bike', 'season': '2025'}]
        a = self.p0([esf('Bike-n', '2026-02-10', 250.0, estado=mm.E_NAO_ELEGIVEL, season='2026')], hist)
        b = self.p0([esf('Bike-n', '2026-02-10', 250.0, estado=mm.E_NAO_ELEGIVEL, season='2027')], hist)
        self.assertEqual(a['registo'], b['registo'])


class ExploratorioIsolado(unittest.TestCase):
    """Modo exploratorio: P0 livre, nada persistido, nada partilhado com o IR calibrado."""

    def test_modulo_exploratorio_nao_importa_base_nem_p0(self):
        importados = {m for linha in inspect.getsource(mex).splitlines()
                      for m in re.findall(r'^(?:import|from)\s+(\w+)', linha)}
        self.assertFalse(importados & {'db', 'modelo_ir_p0', 'modelo_ir_servico'}, importados)
        src = inspect.getsource(mex)
        self.assertNotIn('garantir_referencia', src)
        self.assertNotIn('inserir_', src)

    def test_resultado_e_rotulado_e_nao_persistido(self):
        it = mex.ajustar_unidade('Bike', 300, [], {}, '2026-10-19', {'lag_dias': 1})
        self.assertFalse(it['persistido'])
        self.assertEqual(it['modo'], mex.MODO)
        self.assertEqual(it['p0_origem'], 'livre_estimado')
        self.assertEqual(it['estado'], 'sem_observacoes')
        self.assertIsNone(it['curva'])                                  # sem dados: nenhuma curva artificial

    def test_analisar_cobre_as_duracoes_historicas_sem_curva_sem_dados(self):
        itens = mex.analisar({}, {}, '2026-10-19', {'lag_dias': 1})
        self.assertEqual({i['duracao_s'] for i in itens}, set(mex.DURACOES_HISTORICAS))
        self.assertEqual(len(itens), len(mex.DURACOES_HISTORICAS) * len(mt.MODALIDADES))
        self.assertTrue(all(i['curva'] is None and i['persistido'] is False for i in itens))

    def test_exploratorio_nao_e_o_ir_de_180_s(self):
        self.assertNotIn(180, mex.DURACOES_HISTORICAS)
        self.assertEqual(mex.DURACOES_HISTORICAS, (60, 300, 1200))


class PersistenciaYesAppendOnly(unittest.TestCase):
    """Historico de YES: insercao idempotente, uma linha por teste e versao, leitura sem duplicados."""

    @classmethod
    def setUpClass(cls):
        db.init_schema()

    def tearDown(self):
        # a base e partilhada pela suite: o historico de YES de teste nao pode sobrar para outros testes
        if db.ENABLED:
            db._exec("DELETE FROM modelo_mmp3_yes_historico WHERE test_id LIKE 'SEP-%'")

    def test_gravar_duas_vezes_nao_duplica(self):
        if not db.ENABLED:
            self.skipTest('base de dados indisponivel')
        linha = {'test_id': 'SEP-T1', 'regra_versao': 4, 'activity_id': 'SEP-T1', 'modalidade': 'Bike',
                 'data': '2026-07-01', 'inicio': None, 'watts': 300.0, 'season': '2026',
                 'origem_classificacao': 'tag_manual', 'classe_intervalo': None, 'motivo_decisao': 'x',
                 'referencia_watts': 290.0}
        db.gravar_yes_historico([linha], '2026-07-02T00:00:00')
        db.gravar_yes_historico([dict(linha, watts=999.0)], '2026-07-03T00:00:00')   # tentativa de alterar
        rows = [r for r in db.yes_historico() if r['test_id'] == 'SEP-T1']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['watts'], 300.0)                        # a linha original fica

    def test_codigo_da_tabela_e_so_insert(self):
        src = inspect.getsource(db)
        bloco = src[src.index('def gravar_yes_historico'):src.index('def referencias_p0_historico')]
        self.assertIsNone(re.search(r'\b(UPDATE|DELETE)\b', bloco))


class SemPersistenciaDoModoExploratorio(unittest.TestCase):
    def test_servico_exploratorio_nao_chama_escritas(self):
        src = inspect.getsource(mis.analise_exploratoria)
        for escrita in ('gravar', 'inserir', 'garantir_referencia', 'INSERT', 'UPDATE'):
            self.assertNotIn(escrita, src, escrita)


@unittest.skipIf(__import__('shutil').which('node') is None, 'node indisponivel')
class BlocoExploratorioNaInterface(unittest.TestCase):
    """O bloco JS exploratorio: sintaxe valida, endpoint proprio, nenhum pedido ao IR calibrado nem a gravacao."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        import subprocess
        import tabs.tab_pmc as tp
        i0 = tp.JS.index('// <<ir-exploratorio>>')
        i1 = tp.JS.index('// <</ir-exploratorio>>')
        cls.bloco = tp.JS[i0:i1]
        cls.d = tempfile.mkdtemp()
        cls.f = os.path.join(cls.d, 'x.js')
        open(cls.f, 'w', encoding='utf-8').write(cls.bloco)
        cls.r = subprocess.run(['node', '--check', cls.f], capture_output=True, text=True, timeout=30)

    def test_sintaxe_valida(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr)

    def test_so_chama_o_endpoint_exploratorio(self):
        self.assertIn('/api/modelo/ir/exploratorio', self.bloco)
        for proibido in ('/api/modelo/ir/recalibrar', '/api/modelo/ir/conjunto', '/api/modelo/ir/global', 'POST'):
            self.assertNotIn(proibido, self.bloco, proibido)

    def test_rotulo_nao_validado_visivel(self):
        self.assertIn('nao validado', self.bloco.lower().replace('ã', 'a').replace('ç', 'c'))


if __name__ == '__main__':
    unittest.main()
