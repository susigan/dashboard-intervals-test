"""API e pagina da Curva progressiva (Etapa 28). Base SIMULADA (mock): nunca toca em producao.

    INTERVALS_ICU_API_KEY=valor-ficticio-de-teste python3 -m unittest tests.pmc.test_curva_progressiva_api -v
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

import db  # noqa: E402
import app as aplicacao  # noqa: E402  (as rotas vivem em app.py)
from tests.pmc.test_curva_progressiva import fixture  # noqa: E402


def atividades_de(curvas):
    return [{'id': c['activity_id'], 'icu_ignore_power': False, 'type': c['type'],
             'start_date_local': c['date'] + 'T08:00:00'} for c in curvas]


class CurvaApi(unittest.TestCase):
    def setUp(self):
        self.curvas = fixture()
        self.client = aplicacao.app.test_client()
        self.patches = [
            mock.patch.object(db, 'ENABLED', True),
            mock.patch.object(db, 'load_power_curves', return_value=self.curvas),
            mock.patch.object(db, 'load_activities', return_value=atividades_de(self.curvas)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def get(self, qs):
        r = self.client.get('/api/curva-progressiva' + qs)
        return r.status_code, json.loads(r.data)

    def test_resposta_base_com_janela_42(self):
        code, j = self.get('?ate=2026-10-24')
        self.assertEqual(code, 200)
        self.assertTrue(j['ok'])
        self.assertEqual(j['serie'], 'envelope_janela_42d')
        self.assertEqual(j['estimativas_ajustadas'], 'nao_produzidas')
        self.assertEqual(j['n_curvas_lidas'], 4)
        bike = j['modalidades']['Bike']
        self.assertEqual({p['duracao_s'] for p in bike['pontos']}, {60, 300, 1200})

    def test_grelhas_diferentes_no_mesmo_pedido(self):
        _, j = self.get('?ate=2026-10-24&janela=historico')
        self.assertEqual(j['modalidades']['Row']['duracoes_disponiveis'], [60, 1200])
        self.assertEqual(j['modalidades']['Run']['duracoes_disponiveis'], [300])
        self.assertEqual(j['modalidades']['Ski']['estado'], 'sem_dados')
        self.assertEqual(j['serie'], 'envelope_historico_completo')

    def test_modalidade_e_duracao_pedidas_filtram(self):
        _, j = self.get('?ate=2026-10-24&modalidades=Bike&duracoes=300')
        self.assertEqual(list(j['modalidades']), ['Bike'])
        self.assertEqual([p['duracao_s'] for p in j['modalidades']['Bike']['pontos']], [300])

    def test_validacao_de_parametros(self):
        self.assertEqual(self.get('?modalidades=Swim')[0], 400)
        self.assertEqual(self.get('?duracoes=abc')[0], 400)
        self.assertEqual(self.get('?duracoes=0')[0], 400)
        self.assertEqual(self.get('?janela=ontem')[0], 400)
        self.assertEqual(self.get('?janela=0')[0], 400)
        self.assertEqual(self.get('?ate=24-10-2026')[0], 400)
        self.assertEqual(self.get('?evolucao=1&passo=0')[0], 400)

    def test_evolucao_sem_interpolacao(self):
        _, j = self.get('?ate=2026-10-24&evolucao=1&desde=2026-08-01&passo=7&modalidades=Row&janela=42')
        ev = j['evolucao']
        self.assertEqual(ev['as_of'][0], '2026-08-01')
        self.assertEqual(ev['as_of'][-1], '2026-10-24')
        self.assertIsNone(ev['modalidades']['Row']['60'][0])          # antes de existir o treino
        self.assertNotIn('300', ev['modalidades']['Row'])

    def test_repeticao_e_identica(self):
        a = self.get('?ate=2026-10-24&evolucao=1&desde=2026-08-01')[1]
        b = self.get('?ate=2026-10-24&evolucao=1&desde=2026-08-01')[1]
        self.assertEqual(a, b)

    def test_base_indisponivel_responde_erro_em_json(self):
        with mock.patch.object(db, 'ENABLED', False):
            code, j = self.get('?ate=2026-10-24')
        self.assertEqual(code, 200)
        self.assertFalse(j['ok'])
        self.assertIn('sem base', j['erro'])

    def test_atividade_ausente_fica_excluida_com_motivo(self):
        with mock.patch.object(db, 'load_activities', return_value=[]):
            _, j = self.get('?ate=2026-10-24')
        self.assertEqual(j['modalidades']['Bike']['pontos'], [])
        self.assertTrue(all('atividade_nao_encontrada' in x['motivo'] for x in j['exclusoes']))

    def test_pagina_renderiza_e_chama_a_api(self):
        r = self.client.get('/curva-progressiva')
        self.assertEqual(r.status_code, 200)
        html = r.data.decode('utf-8')
        self.assertIn('Curva progressiva', html)
        self.assertIn('/api/curva-progressiva', html)
        self.assertIn('href="/curva-progressiva"', html)              # entrada de navegacao
        self.assertIn('<canvas id="cv"', html)


class RevisaoFinal(unittest.TestCase):
    """Fecho da Etapa 28: fuso, janela de 42 dias, sinalizacao IQR, Other, consistencia e isolamento do IR."""

    def setUp(self):
        self.curvas = fixture()
        self.client = aplicacao.app.test_client()
        self.patches = [
            mock.patch.object(db, 'ENABLED', True),
            mock.patch.object(db, 'load_power_curves', return_value=self.curvas),
            mock.patch.object(db, 'load_activities', return_value=atividades_de(self.curvas)),
            mock.patch.object(db, '_nomes_actividades', return_value={}),
        ]
        for p in self.patches:
            p.start()
        # calcular_recordes pede load_power_curves(tipo, desde): o mock tem de filtrar por tipo como a base.
        db.load_power_curves.side_effect = lambda tipo=None, desde=None: [
            c for c in self.curvas if tipo is None or c['type'] == tipo]

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def get(self, qs):
        r = self.client.get('/api/curva-progressiva' + qs)
        return r.status_code, json.loads(r.data)

    def test_data_de_referencia_usa_o_fuso_configurado(self):
        import tabs.tab_curva_progressiva as tcp
        from datetime import date
        with mock.patch.object(tcp, '_hoje', return_value=date(2026, 10, 24)):
            _, j = self.get('')
        self.assertEqual(j['ate'], '2026-10-24')
        self.assertEqual(j['data_referencia'], {'ate': '2026-10-24', 'origem': 'padrao',
                                                'fuso': tcp.FUSO})
        self.assertEqual(tcp.FUSO, 'America/Sao_Paulo')

    def test_data_pedida_declara_origem_parametro(self):
        _, j = self.get('?ate=2026-10-10')
        self.assertEqual(j['data_referencia']['origem'], 'parametro')
        self.assertIsNone(j['data_referencia']['fuso'])

    def test_janela_42_limite_no_inicio_e_inclusiva_no_fim(self):
        # ate=2026-10-24: a janela e (ate-42, ate] = (2026-09-12, 2026-10-24]
        cs = [cp_curva('dentro', 'Bike', '2026-09-13', {300: 500}),
              cp_curva('fora', 'Bike', '2026-09-12', {300: 900}),
              cp_curva('hoje', 'Bike', '2026-10-24', {300: 480})]
        db.load_power_curves.side_effect = lambda tipo=None, desde=None: [c for c in cs if tipo is None or c['type'] == tipo]
        db.load_activities.return_value = atividades_de(cs)
        _, j = self.get('?ate=2026-10-24&duracoes=300')
        p = j['modalidades']['Bike']['pontos'][0]
        self.assertEqual((p['watts'], p['activity_id']), (500, 'dentro'))

    def test_iqr_sinaliza_sem_remover(self):
        cs = [cp_curva(f'b{i}', 'Bike', f'2026-0{i}-10', {300: 300 + i}) for i in range(1, 7)]
        cs.append(cp_curva('pico', 'Bike', '2026-10-01', {300: 2000}))
        db.load_power_curves.side_effect = lambda tipo=None, desde=None: [c for c in cs if tipo is None or c['type'] == tipo]
        db.load_activities.return_value = atividades_de(cs)
        _, j = self.get('?ate=2026-10-24&janela=historico&duracoes=300')
        p = j['modalidades']['Bike']['pontos'][0]
        self.assertEqual((p['activity_id'], p['watts']), ('pico', 2000))
        self.assertTrue(p['outlier_iqr'])
        self.assertIn('mantido', p['outlier_iqr_motivo'])

    def test_other_fica_listado_e_fora_do_grafico(self):
        cs = fixture() + [cp_curva('oth', 'Other', '2026-10-03', {60: 250})]
        db.load_power_curves.side_effect = lambda tipo=None, desde=None: [c for c in cs if tipo is None or c['type'] == tipo]
        db.load_activities.return_value = atividades_de(cs)
        _, j = self.get('?ate=2026-10-24')
        self.assertEqual(set(j['modalidades']), {'Bike', 'Row', 'Ski', 'Run'})
        self.assertEqual([x['activity_id'] for x in j['outras_modalidades']], ['oth'])
        self.assertEqual(j['outras_modalidades'][0]['motivo'], 'modalidade_fora_do_grafico')

    def test_consistencia_com_calcular_recordes(self):
        """Mesmos dados: o envelope historico ate D (inclusivo) = melhor de sempre de calcular_recordes ate D+1
        (exclusivo), por duracao, para cada modalidade."""
        from datetime import date, timedelta
        ate = '2026-10-24'
        for mod in ('Bike', 'Row', 'Run'):
            _, j = self.get(f'?ate={ate}&janela=historico&modalidades={mod}')
            seu = {p['duracao_s']: p['watts'] for p in j['modalidades'][mod]['pontos']}
            prox = (date.fromisoformat(ate) + timedelta(days=1)).isoformat()
            rec = db.calcular_recordes(mod, None, prox)['melhores']
            for d, w in seu.items():
                self.assertIn(d, rec, f'{mod} {d}s em falta em calcular_recordes')
                self.assertEqual(rec[d]['watts'], w, f'{mod} {d}s divergente')

    def test_modulo_da_tab_nao_toca_no_ir_nem_escreve(self):
        import tabs.tab_curva_progressiva as tcp
        import modelo_curva_progressiva as mcp_
        for mod in (tcp, mcp_):
            with open(mod.__file__, encoding='utf-8') as fh:
                texto = fh.read()
            for proibido in ('modelo_ir', 'ultimo_ajuste', 'INSERT ', 'UPDATE ', 'DELETE ', 'upsert',
                             'recalibrar', 'promover'):
                self.assertNotIn(proibido, texto, f'{mod.__name__} contem {proibido!r}')


class InterfaceFinal(unittest.TestCase):
    def setUp(self):
        self.curvas = fixture()
        self.client = aplicacao.app.test_client()
        self.patches = [
            mock.patch.object(db, 'ENABLED', True),
            mock.patch.object(db, 'load_power_curves', side_effect=lambda tipo=None, desde=None: [
                c for c in self.curvas if tipo is None or c['type'] == tipo]),
            mock.patch.object(db, 'load_activities', return_value=atividades_de(self.curvas)),
            mock.patch.object(db, '_nomes_actividades', return_value={}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_janelas_90_180_e_historico_sao_enviadas_e_declaradas(self):
        for jan, serie in (('90', 'envelope_janela_90d'), ('180', 'envelope_janela_180d'),
                           ('historico', 'envelope_historico_completo')):
            r = self.client.get(f'/api/curva-progressiva?ate=2026-10-24&janela={jan}')
            j = json.loads(r.data)
            self.assertEqual(j['serie'], serie)

    def test_janela_90_inclui_treino_de_ha_51_dias(self):
        # ate=2026-11-10: treino de 2026-09-20 fica a 51 dias -> fora de 42, dentro de 90
        _, j = self.get_('?ate=2026-11-10&janela=90&modalidades=Row&duracoes=60')
        self.assertEqual(j['modalidades']['Row']['pontos'][0]['activity_id'], 'r1')

    def test_janela_42_exclui_treino_de_ha_51_dias(self):
        _, j = self.get_('?ate=2026-11-10&janela=42&modalidades=Row&duracoes=60')
        self.assertEqual(j['modalidades']['Row']['pontos'], [])
        self.assertEqual(j['modalidades']['Row']['em_falta'], [60])

    def get_(self, qs):
        r = self.client.get('/api/curva-progressiva' + qs)
        return r.status_code, json.loads(r.data)

    def test_sem_dados_responde_estado_explicito(self):
        db.load_power_curves.side_effect = lambda tipo=None, desde=None: []
        _, j = self.get_('?ate=2026-10-24')
        self.assertTrue(j['ok'])
        self.assertEqual(j['modalidades']['Bike']['estado'], 'sem_dados')
        self.assertEqual(j['modalidades']['Bike']['pontos'], [])
        self.assertEqual(j['outras_modalidades'], [])

    def test_pagina_tem_avisos_iqr_outras_modalidades_e_estados(self):
        html = self.client.get('/curva-progressiva').data.decode('utf-8')
        for ident in ('id="iqr"', 'id="outras"', 'id="estados"', 'AVISO IQR', 'outlier_iqr',
                      'data_referencia', 'outras_modalidades'):
            self.assertIn(ident, html)


def cp_curva(aid, mod, dia, pares):
    from tests.pmc.test_curva_progressiva import curva as _c
    return _c(aid, mod, dia, pares)


if __name__ == '__main__':
    unittest.main()
