"""Fase 3 — rota /api/modelo/validacao sobre o registo REAL da Fase 1 (SQLite temporario).

Dados SINTETICOS (testam o pipeline e a integracao, nao a performance do atleta).
Offline: base temporaria; nada e gravado pela rota.

    python -m pytest tests/pmc/test_fase3_validacao_api.py
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fase1_util as U  # noqa: E402
import test_fase1_registo_api as F  # noqa: E402

URL = '/api/modelo/validacao'
N = 26


def _curvas():
    out = []
    for i in range(N):
        d = (datetime(2025, 1, 6) + timedelta(days=14 * i)).date().isoformat()
        out.append({'activity_id': f'v{i}', 'type': 'Bike', 'date': d, 'weight': 70.0,
                    'secs': [60, 300, 1200], 'watts': [500.0, 350.0 + (i % 5), 300.0 + (i % 7)]})
    return out


CURV = _curvas()


def _linha(i, revisado_em):
    c = CURV[i]
    agora = revisado_em
    return {'test_id': f'Bike:1200:v{i}', 'revisao': 1, 'evento': 'confirmacao', 'estado': 'confirmado',
            'estado_anterior': None, 'modalidade': 'Bike', 'duracao_s': 1200, 'activity_id': f'v{i}',
            'data': c['date'], 'hora_local': None, 'watts': c['watts'][2], 'pct_do_melhor': 99.0,
            'melhor_recente': 305.0, 'deteccao': {}, 'elegibilidade': 'elegivel',
            'motivo_elegibilidade': None, 'icu_ignore_power': False,
            'aceita_elegibilidade_desconhecida': False, 'metodo_confirmacao': 'manual',
            'motivo': 'sintetico', 'responsavel': None, 'origem_registo': 'script',
            'versao_contrato': 1, 'registado_em': agora, 'revisado_em': agora, 'confirmado_em': agora}


class Base(F.Base):
    def setUp(self):
        super().setUp()
        self.ativ = [{'id': c['activity_id'], 'icu_ignore_power': False} for c in CURV]
        m = mock.patch.object(self.db, 'load_power_curves',
                              side_effect=lambda tipo=None, desde=None: [c for c in CURV
                                                                         if not tipo or c['type'] == tipo])
        m.start()
        self.addCleanup(m.stop)

    def popular(self, n=N, contemporaneo=True):
        for i in range(n):
            quando = (datetime.fromisoformat(CURV[i]['date']) + timedelta(hours=20)
                      if contemporaneo else datetime(2026, 9, 1))
            self.db.inserir_revisao_teste(_linha(i, quando))

    def val(self, **q):
        # caminho da Fase 3 (observacoes = revisoes confirmadas); a fonte automatica tem os
        # seus testes em test_mmp_deteccao_automatica.py
        q = {'fonte': 'confirmacao_manual', **q}
        url = URL + ('?' + '&'.join(f'{k}={v}' for k, v in q.items()) if q else '')
        r = self.c.get(url)
        return r.status_code, r.get_json()

    def item(self, j, modo='retrospectivo', mod='Bike', dur=1200):
        return next(i for i in j['modos'][modo]['itens']
                    if i['modalidade'] == mod and i['duracao_s'] == dur)


class Rota(Base):
    def test_sem_registo_tudo_inconclusivo_e_nada_e_gravado(self):
        st, j = self.val()
        self.assertEqual(st, 200)
        self.assertEqual(set(j['modos']), {'retrospectivo', 'conhecido_na_epoca'})
        for modo in j['modos'].values():
            self.assertEqual(len(modo['itens']), 12)
            self.assertTrue(all(i['estado_suficiencia'] == 'inconclusivo_sem_alvos' and
                                i['veredicto'] is None for i in modo['itens']))
        self.assertTrue(j['experimental'])
        self.assertFalse(j['promocao'])
        self.assertIn('EXPLORATORIA', j['aviso'])
        self.assertEqual(self.n(), 0)

    def test_registo_contemporaneo_da_avaliacao_nos_dois_modos(self):
        self.popular()
        st, j = self.val()
        self.assertEqual(st, 200)
        a, b = self.item(j), self.item(j, 'conhecido_na_epoca')
        self.assertEqual(a['n_alvos'], N - 1)
        self.assertEqual(a['estado_suficiencia'], 'avaliavel')
        self.assertEqual(a['previsoes'], b['previsoes'])
        self.assertEqual(self.n(), N)                      # a rota nao grava
        outros = [i for i in j['modos']['retrospectivo']['itens']
                  if (i['modalidade'], i['duracao_s']) != ('Bike', 1200)]
        self.assertTrue(all(i['n_alvos'] == 0 for i in outros))

    def test_registo_tardio_distingue_os_modos(self):
        self.popular(contemporaneo=False)
        j = self.val()[1]
        self.assertEqual(self.item(j)['n_alvos'], N - 1)
        e = self.item(j, 'conhecido_na_epoca')
        self.assertEqual((e['n_alvos'], e['estado_suficiencia']), (0, 'inconclusivo_sem_alvos'))
        self.assertGreater(e['treino_perdido_por_modo'], 0)

    def test_modo_unico_e_filtros(self):
        self.popular()
        st, j = self.val(modo='retrospectivo', tipo='Bike', duracao=1200)
        self.assertEqual((st, list(j['modos']), len(j['modos']['retrospectivo']['itens'])),
                         (200, ['retrospectivo'], 1))

    def test_erros(self):
        for q in ({'modo': 'x'}, {'tipo': 'Foo'}, {'duracao': 5}):
            self.assertEqual(self.val(**q)[0], 400, q)
        with mock.patch.object(self.db, 'ENABLED', False):
            self.assertEqual(self.val()[0], 503)
        self.assertEqual(self.c.post(URL, json={}).status_code, 405)

    def test_historico_ambiguo_da_409(self):
        with mock.patch.object(self.db, 'revisoes_testes_modelo', return_value=[
                {'test_id': 'Bike:1200:v1', 'revisao': 2, 'estado': 'confirmado', 'estado_anterior': None,
                 'evento': 'confirmacao', 'modalidade': 'Bike', 'duracao_s': 1200, 'activity_id': 'v1'}]):
            st, j = self.val()
        self.assertEqual(st, 409)
        self.assertIn('ambiguo', j['mensagem'])

    def test_valores_do_cliente_sao_ignorados_e_mesmo_pedido_mesmo_resultado(self):
        self.popular()
        a = self.val()[1]
        self.assertEqual(a, self.val(q=5, watts=999, grelha_q=1)[1])
        self.assertEqual(a['assinatura'], self.val()[1]['assinatura'])

    def test_elegibilidade_atual_conta(self):
        self.popular()
        self.ativ = [dict(a, icu_ignore_power=True) if a['id'] in ('v3', 'v4') else a for a in self.ativ]
        i = self.item(self.val()[1])
        self.assertEqual(i['n_observacoes_usaveis'], N - 2)
        self.assertEqual(i['motivos_exclusao_observacoes'], {'inelegivel:icu_ignore_power=true': 2})


class RegressaoProducao(Base):
    def test_validar_nao_muda_a_producao(self):
        antes = F.ConfirmarNaoAtualizaOModelo._resultados(self)
        self.popular()
        self.val()
        self.c.get('/api/modelo/estimador')
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        self.assertEqual(antes, F.ConfirmarNaoAtualizaOModelo._resultados(self))

    def test_nenhum_modulo_de_producao_referencia_a_validacao(self):
        for f in ('pmc.py', 'calibracao.py', 'ftlm.py', 'fmt.py', 'protocolo.py', 'export.py', 'sync.py',
                  'api_recovery.py', 'modelo_testes.py', 'modelo_estimador.py', 'db.py'):
            src = open(os.path.join(ROOT, f), encoding='utf-8').read()
            self.assertNotIn('modelo_validacao', src, f)

    def test_a_validacao_so_importa_o_estimador_e_stdlib(self):
        src = open(os.path.join(ROOT, 'modelo_validacao.py'), encoding='utf-8').read()
        imports = sorted(set(re.findall(r'^\s*(?:import|from)\s+(\w+)', src, re.M)))
        self.assertEqual(imports, ['datetime', 'hashlib', 'json', 'math', 'modelo_estimador', 'random'])

    def test_q_de_producao_e_o_predefinido_nao_mudam(self):
        import modelo_estimador as me
        import modelo_validacao as mv
        self.assertEqual((me.Q_DIA, mv.Q_PADRAO), (0.01, 0.01))
        src = open(os.path.join(ROOT, 'modelo_validacao.py'), encoding='utf-8').read()
        self.assertNotRegex(src, r'Q_DIA\s*=')

    def test_a_rota_nao_escreve(self):
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        bloco = src[src.index('def api_modelo_validacao'):src.index("@app.route('/api/calibracao')")]
        self.assertNotRegex(bloco, r'inserir_revisao_teste|_exec\(|INSERT|UPDATE|DELETE')

    def test_sem_tabelas_novas(self):
        nomes = {r[0] for r in self.db._exec("SELECT name FROM sqlite_master WHERE type='table'", fetch='all')}
        self.assertEqual({n for n in nomes if n.startswith('modelo')}, {'modelo_ir_ajustes', 'modelo_ir_p0_referencias', 'modelo_mmp3_yes_historico', 'modelo_observacoes_auto', 'modelo_testes_revisoes'})


if __name__ == '__main__':
    unittest.main()
