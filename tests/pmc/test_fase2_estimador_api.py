"""Fase 2 — estimador sobre o registo REAL da Fase 1 (SQLite temporario) e a rota experimental.

    python -m pytest tests/pmc/test_fase2_estimador_api.py

Offline: base temporaria, curvas e atividades simuladas. A rota so le; nada e gravado.
"""
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fase1_util as U  # noqa: E402
import test_fase1_registo_api as F  # noqa: E402

URL = '/api/modelo/estimador'


class Base(F.Base):
    def estim(self, **q):
        # estes testes cobrem o caminho da Fase 2 (observacoes = revisoes confirmadas); a fonte
        # automatica tem os seus testes em test_mmp_deteccao_automatica.py
        q = {'fonte': 'confirmacao_manual', **q}
        url = URL + ('?' + '&'.join(f'{k}={v}' for k, v in q.items()) if q else '')
        r = self.c.get(url)
        return r.status_code, r.get_json()

    def item(self, j, mod='Bike', dur=1200):
        return next(i for i in j['itens'] if i['modalidade'] == mod and i['duracao_s'] == dur)

    def confirma(self, *ids, mod='Bike', dur=1200):
        for aid in ids:
            r = self.post(mod=mod, dur=dur, aid=aid)
            self.assertEqual(r.status_code, 200, r.get_json())


class Rota(Base):
    def test_sem_registo_tudo_nao_calibrado_e_nada_e_gravado(self):
        st, j = self.estim()
        self.assertEqual(st, 200)
        self.assertEqual(len(j['itens']), 12)
        self.assertTrue(all(i['estado_calibracao'] == 'nao_calibrado' and i['nivel_w'] is None
                            for i in j['itens']))
        self.assertTrue(j['experimental'] and not j['validado_preditivamente'])
        self.assertIn('Experimental', j['aviso'])
        self.assertEqual(self.n(), 0)

    def test_candidatos_detetados_nao_confirmados_nao_alimentam_o_estimador(self):
        self.assertGreater(self.cand()['n'], 0)
        _, j = self.estim()
        self.assertTrue(all(i['n_confirmados'] == 0 and i['n_usadas'] == 0 for i in j['itens']))

    def test_confirmar_alimenta_so_a_chave_confirmada(self):
        self.confirma('Bike11')
        _, j = self.estim()
        i = self.item(j)
        self.assertEqual((i['estado_calibracao'], i['n_usadas'], i['nivel_w']), ('estimativa_unica', 1, 311.0))
        self.assertEqual(i['sequencia_usada'][0]['test_id'], 'Bike:1200:Bike11')
        outros = [x for x in j['itens'] if (x['modalidade'], x['duracao_s']) != ('Bike', 1200)]
        self.assertTrue(all(x['n_usadas'] == 0 and x['nivel_w'] is None for x in outros))

    def test_varios_testes_dao_incerteza_e_ordem_temporal(self):
        self.confirma('Bike11', 'Bike10', 'Bike9')
        _, j = self.estim()
        i = self.item(j)
        self.assertEqual(i['estado_calibracao'], 'experimental')
        self.assertEqual([s['test_id'] for s in i['sequencia_usada']],
                         ['Bike:1200:Bike9', 'Bike:1200:Bike10', 'Bike:1200:Bike11'])
        self.assertIsNotNone(i['incerteza'])
        self.assertEqual(self.n(), 3)

    def test_rejeitar_e_invalidar_removem_a_observacao(self):
        self.confirma('Bike11', 'Bike10')
        self.assertEqual(self.item(self.estim()[1])['n_usadas'], 2)
        self.assertEqual(self.post(aid='Bike10', dec='rejeitar', motivo='aquecimento incompleto').status_code, 200)
        i = self.item(self.estim()[1])
        self.assertEqual((i['n_usadas'], i['nivel_w']), (1, 311.0))
        self.assertEqual(i['motivos_exclusao'], {'revisao_atual_rejeitada': 1})
        # reconfirmar volta a usar (revisao 3)
        self.assertEqual(self.post(aid='Bike10').status_code, 200)
        i = self.item(self.estim()[1])
        self.assertEqual(i['n_usadas'], 2)
        self.assertIn(3, [s['revisao'] for s in i['sequencia_usada']])
        # o historico append-only continua com as 3 revisoes
        self.assertEqual(len(self.c.get('/api/protocolo/testes/historico?test_id=Bike:1200:Bike10')
                             .get_json()['revisoes']), 3)

    def test_icu_ignore_power_atual_decide_a_aprendizagem(self):
        self.confirma('Bike11', 'Bike10')
        self.ativ = [dict(a, icu_ignore_power=True) if a['id'] == 'Bike10' else a for a in self.ativ]
        i = self.item(self.estim()[1])
        self.assertEqual(i['n_usadas'], 1)
        self.assertEqual(i['motivos_exclusao'], {'inelegivel:icu_ignore_power=true': 1})

    def test_desconhecida_confirmada_com_opt_in_fica_fora_da_aprendizagem(self):
        self.ativ = U.atividades(**{c['activity_id']: ('ausente' if c['activity_id'] == 'Bike9' else False)
                                    for c in F.CURVAS})
        self.assertEqual(self.post(aid='Bike9', aceita_elegibilidade_desconhecida=True).status_code, 200)
        self.confirma('Bike11')
        i = self.item(self.estim()[1])
        self.assertEqual((i['n_confirmados'], i['n_usadas']), (2, 1))
        self.assertEqual(i['motivos_exclusao'],
                         {'elegibilidade_desconhecida:icu_ignore_power_ausente': 1})
        self.assertEqual(self.n(), 2)

    def test_curva_divergente_ou_ausente_exclui(self):
        self.confirma('Bike11')
        alterada = [dict(c, watts=[w + 5 for w in c['watts']]) if c['activity_id'] == 'Bike11' else c
                    for c in F.CURVAS]
        with mock.patch.object(self.db, 'load_power_curves',
                               side_effect=lambda tipo=None, desde=None: alterada):
            i = self.item(self.estim()[1])
        self.assertEqual(i['motivos_exclusao'], {'dados_divergente': 1})
        with mock.patch.object(self.db, 'load_power_curves', return_value=[]):
            i = self.item(self.estim()[1])
        self.assertEqual(i['motivos_exclusao'], {'dados_sem_curva': 1})

    def test_atividades_indisponiveis(self):
        self.confirma('Bike11')
        self.ativ = None
        j = self.estim()[1]
        self.assertFalse(j['atividades_disponiveis'])
        self.assertEqual(self.item(j)['n_usadas'], 0)

    def test_ate_exclui_observacoes_posteriores(self):
        self.confirma('Bike11', 'Bike10', 'Bike9')
        datas = [s['data'] for s in self.item(self.estim()[1])['sequencia_usada']]
        i = self.item(self.estim(ate=datas[0])[1])
        self.assertEqual(i['n_usadas'], 1)
        self.assertEqual(i['motivos_exclusao'], {'posterior_a_data_de_referencia': 2})

    def test_filtros_e_erros(self):
        st, j = self.estim(tipo='Bike', duracao=1200)
        self.assertEqual((st, len(j['itens'])), (200, 1))
        for q in ({'tipo': 'Foo'}, {'duracao': 999}, {'ate': '2026-13-01'}, {'ate': 'x'}, {'ate': '2026-1-1'}):
            self.assertEqual(self.estim(**q)[0], 400, q)

    def test_valores_do_cliente_sao_ignorados(self):
        self.confirma('Bike11')
        a = self.estim()[1]
        b = self.estim(watts=999, nivel_w=1, elegibilidade='elegivel', q=5)[1]
        self.assertEqual(a, b)

    def test_historico_ambiguo_da_409_e_nao_calcula(self):
        self.confirma('Bike11')
        with mock.patch.object(self.db, 'revisoes_testes_modelo', return_value=[
                {'test_id': 'Bike:1200:Bike11', 'revisao': 2, 'estado': 'confirmado',
                 'estado_anterior': None, 'evento': 'confirmacao', 'modalidade': 'Bike',
                 'duracao_s': 1200, 'activity_id': 'Bike11'}]):
            st, j = self.estim()
        self.assertEqual(st, 409)
        self.assertIn('ambiguo', j['mensagem'])

    def test_sem_base_de_dados_503(self):
        with mock.patch.object(self.db, 'ENABLED', False):
            self.assertEqual(self.estim()[0], 503)

    def test_so_aceita_get(self):
        self.assertEqual(self.c.post(URL, json={}).status_code, 405)

    def test_mesmo_pedido_mesmo_resultado(self):
        self.confirma('Bike11', 'Bike10', 'Bike9')
        self.assertEqual(self.estim(ate='2026-12-31')[1], self.estim(ate='2026-12-31')[1])


class RegressaoProducao(Base):
    def test_confirmar_e_consultar_o_estimador_nao_muda_a_producao(self):
        antes = F.ConfirmarNaoAtualizaOModelo._resultados(self)
        self.confirma('Bike11', 'Bike10')
        self.post(mod='Bike', dur=300, aid='Bike10', dec='rejeitar', motivo='x')
        self.estim()
        self.estim(tipo='Bike')
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        depois = F.ConfirmarNaoAtualizaOModelo._resultados(self)
        self.assertEqual(antes, depois)

    def test_nenhum_modulo_de_producao_referencia_o_estimador(self):
        for f in ('pmc.py', 'calibracao.py', 'ftlm.py', 'fmt.py', 'protocolo.py', 'export.py',
                  'sync.py', 'api_recovery.py', 'modelo_testes.py', 'helpers.py'):
            src = open(os.path.join(ROOT, f), encoding='utf-8').read()
            self.assertNotIn('modelo_estimador', src, f)
            self.assertNotIn('calcular_estado', src, f)

    def test_estimador_nao_importa_a_producao_nem_a_base(self):
        src = open(os.path.join(ROOT, 'modelo_estimador.py'), encoding='utf-8').read()
        imports = re.findall(r'^\s*(?:import|from)\s+(\w+)', src, re.M)
        self.assertEqual(sorted(set(imports)),
                         ['datetime', 'hashlib', 'json', 'math', 'modelo_testes'])

    def test_sem_tabelas_novas_nem_ponteiro_de_producao(self):
        nomes = {r[0] for r in self.db._exec(
            "SELECT name FROM sqlite_master WHERE type='table'", fetch='all')}
        self.assertEqual({n for n in nomes if n.startswith('modelo')}, {'modelo_observacoes_auto', 'modelo_testes_revisoes'})

    def test_o_estimador_nao_escreve_na_base(self):
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        bloco = src[src.index("def api_modelo_estimador"):src.index("@app.route('/api/calibracao')")]
        self.assertNotRegex(bloco, r'inserir_revisao_teste|_exec\(|INSERT|UPDATE|DELETE')

    def test_detecao_e_sugestoes_da_fase_a_inalteradas(self):
        antes = self.c.get('/api/protocolo').get_json()
        self.confirma('Bike11', 'Bike10')
        self.estim()
        self.app._cache_ignore_power.update({'t': None, 'mapa': None})
        self.assertEqual(antes, self.c.get('/api/protocolo').get_json())


class CompatibilidadeFase1(Base):
    def test_endpoints_da_fase1_continuam_iguais(self):
        self.confirma('Bike11')
        j = self.c.get('/api/protocolo/testes').get_json()
        self.assertEqual((j['status'], j['n']), ('OK', 1))
        self.assertEqual(j['testes'][0]['test_id'], 'Bike:1200:Bike11')
        self.assertEqual(self.post().get_json()['alterado'], False)       # idempotencia


if __name__ == '__main__':
    unittest.main()
