"""Refino visual do grafico comparativo: resumo por duracao, sensibilidade, auditoria recolhida.

    python -m unittest discover -s tests/pmc -p 'test_evolucao_refino_unit.py'

Os valores esperados sao recalculados em Python a partir do payload (backend), de forma
independente da pagina. Browser (desktop, mobile, toque) NAO e coberto aqui: ver a
inspecao registrada separadamente.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'dummy-teste-local')

from test_evolucao_ajuste_unit import _ll, _ajuste_ref, _dia, H  # referencia independente

HARNESS = os.path.join(HERE, 'js', 'render_grafico_evol.js')
NODE = '/opt/node22/bin/node' if os.path.exists('/opt/node22/bin/node') else 'node'
DURACOES = ('60', '300', '720', '1200')

def _fmt_data(t):
    import datetime as _dt
    return (_dt.date(1970, 1, 1) + _dt.timedelta(days=int(t))).strftime('%d/%m/%Y')
ROTULO = {'60': '1 min', '300': '5 min', '720': '12 min', '1200': '20 min'}


def _pp(v):
    return f'{v:+.1f}'


def _serie_sintetica(valores_delta, ref, med=300.0, status='ok', mediana_rec=None):
    """Serie no formato de pmc.evolucao_performance: valor = med*(1 + delta/100)."""
    pontos = []
    for k, dl in enumerate(valores_delta):
        d = date.fromordinal(ref.toordinal() - 7 * (len(valores_delta) - 1 - k)).isoformat()
        pontos.append({'data': d, 'id': f'a{k}', 'valor': round(med * (1 + dl / 100), 4),
                       'estado': 'valido', 'sinalizacoes': []})
    janela = {'status': status, 'motivos': [] if status == 'ok' else ['observacoes_insuficientes'],
              'mediana_anterior': med if status == 'ok' else None,
              'mediana_recente': mediana_rec if status == 'ok' else None,
              'diferenca': (mediana_rec - med) if (status == 'ok' and mediana_rec is not None) else None,
              'variacao_pct': (round((mediana_rec - med) / med * 100, 1) if (status == 'ok' and mediana_rec is not None) else None),
              'n_anterior': 26, 'n_recente': 13, 'semanas_recente': 13, 'semanas_necessarias': 10,
              'periodo_anterior': None, 'periodo_recente': None}
    return {'rotulo': '', 'semantica': '', 'qualidade': {'n_validos': len(pontos)},
            'ultimo_valor': pontos[-1]['valor'] if pontos else None, 'pontos': pontos,
            'janelas': {'90': dict(janela), '180': dict(janela), '365': dict(janela)}}


class RefinoResumoEAuditoria(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if shutil.which(NODE) is None and not os.path.exists(NODE):
            raise unittest.SkipTest('node nao disponivel')
        try:
            import tabs.tab_pmc as T
            from test_evolucao_visual_unit import _cenario, _gerar
        except BaseException as e:  # config.py encerra sem chave da API
            raise unittest.SkipTest(f'tab_pmc nao importa: {type(e).__name__}: {e}')
        html = T.render()
        js = '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))
        cls.tmp = tempfile.mkdtemp(prefix='evol_ref_')
        cls.pjs = os.path.join(cls.tmp, 'pmc.js')
        with open(cls.pjs, 'w', encoding='utf-8') as f:
            f.write(js)
        e, c = _cenario()
        cls.base = _gerar(e, c, mods=('Bike',))
        cls.ref = date(2026, 10, 14)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, payload, mod='Bike', jan='180', ocultas=''):
        pay = os.path.join(self.tmp, 'payload.json')
        with open(pay, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        r = subprocess.run([NODE, HARNESS, self.pjs, pay, mod, jan, 'todas', ocultas],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        return json.loads(r.stdout)

    def _mods(self, payload):
        return payload['modalidades']['Bike']['melhores_esforcos']

    def _obs(self, payload, k, jan):
        """Observacoes do ajuste, pela regra do backend: valido, >0, sem 'excluido'."""
        s = self._mods(payload)[k]
        med = s['janelas'][jan]['mediana_anterior']
        T, Y = [], []
        for p in s['pontos']:
            if p.get('estado') != 'valido' or not isinstance(p.get('valor'), (int, float)) or p['valor'] <= 0:
                continue
            if 'excluido' in (p.get('sinalizacoes') or []):
                continue
            T.append(_dia(p['data']))
            Y.append(100 * (p['valor'] / med - 1))
        return np.array(T, dtype=float), np.array(Y, dtype=float)

    # 1. resumo: medianas, diferenca em W e variacao % vindas do backend
    def test_resumo_medianas_diferenca_e_percentual_do_backend(self):
        reg = self._run(self.base, jan='180')
        html = reg['__html']
        for k in DURACOES:
            with self.subTest(duracao=k):
                w = self._mods(self.base)[k]['janelas']['180']
                self.assertIn(f"{w['mediana_anterior']:.1f} → {w['mediana_recente']:.1f} W", html)
                self.assertIn(f"{_pp(w['diferenca'])} W · {_pp(w['variacao_pct'])}%", html)

    # 2. medianas invalidas/insuficientes: motivo compacto e nenhum valor inventado
    def test_medianas_invalidas_mostram_motivo(self):
        casos = [('x', 'comparação indisponível: mediana anterior inválida'),
                 (0, 'comparação indisponível: mediana anterior igual a zero')]
        for valor, texto in casos:
            with self.subTest(mediana=valor):
                p = copy.deepcopy(self.base)
                p['modalidades']['Bike']['melhores_esforcos']['60']['janelas']['180']['mediana_anterior'] = valor
                reg = self._run(p)
                self.assertIn(texto, reg['__html'])
                self.assertNotIn('nan', reg['__html'].lower())
                self.assertFalse(reg['__ultimo_norm']['ajuste']['60']['ok'])

    def test_janela_insuficiente_mostra_motivo(self):
        p = copy.deepcopy(self.base)
        p['modalidades']['Bike']['melhores_esforcos']['1200']['janelas']['180']['status'] = 'insuficiente'
        reg = self._run(p)
        self.assertIn('comparação indisponível: dados insuficientes nesta janela', reg['__html'])

    # 3. quantidades de observacoes diferentes por duracao aparecem no resumo
    def test_contagens_de_observacoes_por_duracao(self):
        reg = self._run(self.base, jan='180')
        for k in DURACOES:
            with self.subTest(duracao=k):
                T, _ = self._obs(self.base, k, '180')
                self.assertIn(f'{len(T)} obs. · ', reg['__html'])
                self.assertEqual(reg['__ultimo_norm']['ajuste'][k]['n_obs'], len(T))

    # 4. todos os pontos do payload continuam no conjunto de dados (auditoria)
    def test_todos_os_pontos_preservados_na_auditoria(self):
        reg = self._run(self.base, jan='180')
        total = sum(len(self._mods(self.base)[k]['pontos']) for k in DURACOES)
        self.assertEqual(reg['__auditoria'].count('<tr><td>'), total)

    # 5. exclusao do ajuste so para 'excluido'; sinalizados ficam no ajuste
    def test_excluido_sai_do_ajuste_e_sinalizados_ficam(self):
        base_n = self._run(self.base)['__ultimo_norm']['ajuste']['60']['n_obs']
        p = copy.deepcopy(self.base)
        pts = p['modalidades']['Bike']['melhores_esforcos']['60']['pontos']
        val = [q for q in pts if q['estado'] == 'valido']
        val[0]['sinalizacoes'] = ['extremo']
        val[1]['sinalizacoes'] = ['suspeito']
        val[2]['sinalizacoes'] = ['excluido']
        reg = self._run(p)
        self.assertEqual(reg['__ultimo_norm']['ajuste']['60']['n_obs'], base_n - 1)
        self.assertIn('extremo', reg['__auditoria'])  # observacao extrema segue na auditoria

    # 6. extremos mantidos sem alterar dados; diagnostico presente
    def test_extremo_recente_mantido_e_diagnostico_sem_alterar_dados(self):
        p = copy.deepcopy(self.base)
        pts = p['modalidades']['Bike']['melhores_esforcos']['60']['pontos']
        recente = max((q for q in pts if q['estado'] == 'valido'), key=lambda q: q['data'])
        recente['valor'] = recente['valor'] * 3
        recente['sinalizacoes'] = ['extremo']
        reg = self._run(p)
        n = next(x['n'] for x in reg['__norm'] if x['k'] == '60')
        por_id = {q['id']: q for q in n['pontos']}
        self.assertIsNotNone(por_id[recente['id']]['delta'])        # mantido e plotado
        self.assertIn('extremo', por_id[recente['id']]['sinal'])
        self.assertEqual(por_id[recente['id']]['valor'], recente['valor'])  # dado passado sem alteracao
        self.assertIn('60', reg['__ultimo_norm']['sensibilidade'])  # diagnostico calculado

    # 6b. criterio de sensibilidade reproduzido em Python: retirada de 1 observacao vs 1 DP local
    def test_sensibilidade_segue_o_criterio_documentado(self):
        T, Y = self._obs(self.base, '60', '180')
        ref = [q for q in _ajuste_ref(T, Y) if q[1] is not None]
        g, f, sd = ref[-1]
        melhor = None
        for i in range(len(T)):
            if abs(T[i] - g) >= H:
                continue
            fi = _ll(float(g), np.delete(T, i), np.delete(Y, i))
            if fi is None:
                continue
            d = fi - f
            if melhor is None or abs(d) > abs(melhor):
                melhor = d
        reg = self._run(self.base, jan='180')
        s = reg['__ultimo_norm']['sensibilidade']['60']
        if sd is None or sd <= 0:
            self.assertIn('não calculável', s['sens'])
        else:
            razao = abs(melhor) / sd
            self.assertAlmostEqual(s['razao'], razao, places=6)
            esperado = 'Sensibilidade: elevada' if razao >= 1 else 'Sensibilidade: baixa segundo o critério utilizado'
            self.assertEqual(s['sens'], esperado)

    def test_sensivel_quando_observacao_recente_domina(self):
        """Caso sintetico: um unico ponto recente isolado domina a estimativa recente."""
        rng = np.random.default_rng(1)
        ruido = rng.normal(0, 2, 43).tolist()
        ruido[-1] += 60
        p = copy.deepcopy(self.base)
        p['modalidades']['Bike']['melhores_esforcos']['60'] = _serie_sintetica(ruido, self.ref, med=300.0, mediana_rec=300.0)
        reg = self._run(p, jan='180')
        s = reg['__ultimo_norm']['sensibilidade']['60']
        self.assertEqual(s['sens'], 'Sensibilidade: elevada')
        self.assertGreaterEqual(s['razao'], 1)
        self.assertIn('diagnóstico, sem a observação de', reg['__html'])
        # o ponto influente e o mais recente (o isolado), e o dado nao foi alterado
        ultimo = _serie_sintetica(ruido, self.ref, med=300.0, mediana_rec=300.0)['pontos'][-1]['data']
        self.assertIn(ultimo[8:10] + '/' + ultimo[5:7] + '/' + ultimo[:4], reg['__html'])

    # 7. consistencia: tooltip (tendencia estimada) == ajuste recalculado no mesmo dia
    def test_tooltip_tendencia_consistente_com_ajuste(self):
        reg = self._run(self.base, jan='180')
        tip = reg['__tooltip']
        m = re.search(r'tendência estimada: ([+-]?\d+\.\d)%', tip)
        self.assertIsNotNone(m, tip)
        valor_tip = float(m.group(1))
        data = re.search(r'· (\d{4}-\d{2}-\d{2})', tip).group(1)
        T, Y = self._obs(self.base, '60', '180')
        f = _ll(float(_dia(data)), T, Y)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(valor_tip, f, delta=0.06)  # arredondado a 1 casa
        self.assertIn('não observação', tip)

    # 8. consistencia: resumo, ajuste e auditoria usam a mesma contagem
    def test_resumo_ajuste_e_auditoria_consistentes(self):
        reg = self._run(self.base, jan='180')
        for k in DURACOES:
            with self.subTest(duracao=k):
                n_ajuste = reg['__ultimo_norm']['ajuste'][k]['n_obs']
                self.assertIn(f'ajuste de ', reg['__html']); self.assertIn(f' · {n_ajuste} obs. · ', reg['__html'])
                self.assertEqual(reg['__ultimo_norm']['curvas'][k], reg['__ultimo_norm']['ajuste'][k]['curva'])

    # 9. ocultar: resumo numerico permanece; grafico perde a serie
    def test_ocultar_mantem_resumo_numerico(self):
        reg = self._run(self.base, jan='180', ocultas='300')
        self.assertIn('(oculta no gráfico)', reg['__html'])
        w = self._mods(self.base)['300']['janelas']['180']
        self.assertIn(f"{w['mediana_anterior']:.1f} → {w['mediana_recente']:.1f} W", reg['__html'])
        self.assertNotIn('300', reg['__ultimo_norm']['curvas'])

    # 10. janelas 90/180/365 mostram as medianas do backend de cada janela
    def test_janelas_90_180_365_no_resumo(self):
        for jan in ('90', '180', '365'):
            with self.subTest(janela=jan):
                reg = self._run(self.base, jan=jan)
                for k in DURACOES:
                    w = self._mods(self.base)[k]['janelas'][jan]
                    self.assertIn(f"{w['mediana_anterior']:.1f} → {w['mediana_recente']:.1f} W", reg['__html'])

    # auditoria recolhida: nao renderizada por padrao; tabela extensa nao aparece no html principal
    def test_auditoria_recolhida_e_sem_tabela_extensa_por_padrao(self):
        reg = self._run(self.base, jan='180')
        html = reg['__html']
        self.assertIn('<div id="evAuditBody"></div>', html)
        # a unica tabela restante e a do eFTP (fora do escopo desta alteracao)
        self.assertEqual(html.count('<table'), 1)
        self.assertNotIn('Δ% vs mediana anterior', html)
        self.assertIn('registros nas 4 durações', html)

    def test_explicacao_recolhida_com_metodo_e_limites(self):
        reg = self._run(self.base, jan='180')
        html = reg['__html']
        self.assertIn('Como ler este gráfico e o método', html)
        self.assertIn('não é intervalo de confiança nem de predição', html)
        self.assertIn('não muda e nenhum dado é removido', html)

    # 11. eFTP separado e sem mudanca de semantica
    def test_eftp_fora_do_grafico_comparativo(self):
        reg = self._run(self.base, jan='180')
        self.assertIn('ev_eftp', reg)
        self.assertNotIn('eftp_atividade', reg['__ultimo_norm']['ajuste'])
        self.assertIn('não equivale ao FTP configurado', reg['__html'])


def _linha(html, rotulo):
    """Trecho HTML da linha de resumo da duracao indicada."""
    for pedaco in html.split('<div class="evrow">'):
        if f'<b>{rotulo}</b>' in pedaco:
            return pedaco
    raise AssertionError(f'linha {rotulo} ausente')


class DiagnosticoSensibilidade(unittest.TestCase):
    """Rotulos explicitos, separacao comparacao/tendencia/sensibilidade e casos de borda."""

    @classmethod
    def setUpClass(cls):
        RefinoResumoEAuditoria.setUpClass.__func__(cls)   # mesma montagem (harness, payload base, data de referencia)
        cls.base = copy.deepcopy(cls.base)

    @classmethod
    def tearDownClass(cls):
        RefinoResumoEAuditoria.tearDownClass.__func__(cls)

    def _run(self, payload, jan='180'):
        return RefinoResumoEAuditoria._run(self, payload, jan=jan)

    def test_limite_exato_e_rotulos(self):
        reg = self._run(self.base)
        r = reg['__rotulos']
        self.assertEqual(r['elevada_no_limite']['nivel'], 'elevada')   # razao == 1 => elevada
        self.assertEqual(r['baixa_logo_abaixo']['nivel'], 'baixa')
        self.assertEqual(r['elevada_acima']['nivel'], 'elevada')
        self.assertEqual(r['baixa_logo_abaixo']['rot'], 'Sensibilidade: baixa segundo o critério utilizado')
        self.assertEqual(r['elevada_no_limite']['rot'], 'Sensibilidade: elevada')

    def test_casos_nao_calculaveis_sem_nan(self):
        reg = self._run(self.base)
        r = reg['__rotulos']
        for k in ('dispersao_zero', 'sem_banda', 'sem_influente', 'sd_negativo', 'razao_nula'):
            with self.subTest(caso=k):
                self.assertEqual(r[k]['nivel'], 'nc')
                self.assertIn('não calculável', r[k]['rot'])
                self.assertNotIn('NaN', r[k]['rot'])

    def test_dispersao_zero_no_grafico_sem_nan(self):
        p = copy.deepcopy(self.base)
        p['modalidades']['Bike']['melhores_esforcos']['60'] = _serie_sintetica([10.0] * 43, self.ref, med=300.0, mediana_rec=300.0)
        reg = self._run(p)
        html = reg['__html']
        self.assertNotIn('NaN', html)
        self.assertNotIn('Infinity', html)
        self.assertIn('dispersão nula', html)
        self.assertNotIn('Sensibilidade: elevada', _linha(html, '1 min'))

    def test_comparacao_tendencia_e_sensibilidade_sao_indicadores_distintos(self):
        reg = self._run(self.base)
        html = reg['__html']
        for rotulo, k in (('1 min', '60'), ('5 min', '300'), ('12 min', '720'), ('20 min', '1200')):
            with self.subTest(duracao=rotulo):
                linha = _linha(html, rotulo)
                self.assertIn('Comparação entre períodos', html)
                self.assertIn('Tendência recente estimada', html)
                self.assertIn('Sensibilidade (diagnóstico)', html)
                dir_ = reg['__ultimo_norm']['sensibilidade'][k]['dir']
                if dir_:
                    self.assertIn(dir_ + ' (', linha)               # tendencia preservada na sua coluna
                self.assertIn(reg['__ultimo_norm']['sensibilidade'][k]['sens'], linha)
                self.assertNotIn(reg['__ultimo_norm']['sensibilidade'][k]['sens'], linha.split('Sensibilidade (diagnóstico)')[0])

    def test_diagnostico_no_mesmo_ponto_temporal(self):
        reg = self._run(self.base)
        s = reg['__ultimo_norm']['sensibilidade']['60']
        if s['inf'] is None:
            self.skipTest('sem observacao influente neste cenario')
        self.assertAlmostEqual(s['sem'] - s['oficial'], s['inf']['dlt'], places=9)
        self.assertIn('Não altera dados nem a curva oficial', reg['__html'])

    def test_extremo_na_borda_direita_entra_no_ajuste_oficial(self):
        p = copy.deepcopy(self.base)
        pts = p['modalidades']['Bike']['melhores_esforcos']['60']['pontos']
        ultimo = max((q for q in pts if q['estado'] == 'valido'), key=lambda q: q['data'])
        ultimo['valor'] = ultimo['valor'] * 3
        ultimo['sinalizacoes'] = ['extremo']
        reg = self._run(p)
        aj = reg['__ultimo_norm']['ajuste']['60']
        n = next(x['n'] for x in reg['__norm'] if x['k'] == '60')
        validos = [q for q in n['pontos'] if q['delta'] is not None and 'excluido' not in q['sinal']]
        self.assertEqual(aj['n_obs'], len(validos))                      # extremo continua no ajuste oficial
        self.assertIn('60', reg['__ultimo_norm']['desenhados'])

    def test_janelas_e_duracoes_com_novos_rotulos(self):
        for jan in ('90', '180', '365'):
            with self.subTest(janela=jan):
                reg = self._run(self.base, jan=jan)
                html = reg['__html']
                for rotulo in ('1 min', '5 min', '12 min', '20 min'):
                    self.assertIn(f'<b>{rotulo}</b>', html)
                self.assertIn(f'janela de {jan} dias', html)
                self.assertIn('Sensibilidade (diagnóstico)', html)

    def test_dados_insuficientes_nao_geram_tendencia_nem_sensibilidade(self):
        p = copy.deepcopy(self.base)
        p['modalidades']['Bike']['melhores_esforcos']['60'] = _serie_sintetica([1.0, 2.0, 3.0], self.ref, med=300.0, mediana_rec=300.0)
        reg = self._run(p)
        self.assertNotIn('60', reg['__ultimo_norm']['sensibilidade'])
        self.assertEqual(reg['__ultimo_norm']['curvas'].get('60', 0), 0)   # sem curva
        self.assertEqual(reg['__ultimo_norm']['bandas'].get('60', 0), 0)   # sem banda
        linha = _linha(reg['__html'], '1 min') if '<b>1 min</b>' in reg['__html'] else ''
        self.assertNotIn('Sensibilidade: elevada', linha)

    def test_payload_e_medianas_inalterados_pelo_diagnostico(self):
        antes = json.dumps(self.base, sort_keys=True)
        reg = self._run(self.base)
        self.assertEqual(json.dumps(self.base, sort_keys=True), antes)
        med = self.base['modalidades']['Bike']['melhores_esforcos']['60']['janelas']['180']['mediana_anterior']
        self.assertIn(f'{med:.1f} → ', reg['__html'])


def _obs_payload(payload, k, med):
    """Observacoes do ajuste como a pagina as monta: valido, >0, sem 'excluido'; Y = 100*(valor/mediana - 1)."""
    T, Y = [], []
    for q in payload['modalidades']['Bike']['melhores_esforcos'][k]['pontos']:
        if q.get('estado') != 'valido' or not q.get('valor') or q['valor'] <= 0:
            continue
        if 'excluido' in (q.get('sinalizacoes') or []):
            continue
        T.append(_dia(q['data'])); Y.append(100 * (q['valor'] / med - 1))
    return np.array(T, dtype=float), np.array(Y, dtype=float)


class SuporteUnilateral(unittest.TestCase):
    """Borda: sinalizacao discreta de suporte unilateral, sem alterar ajuste, pontos ou banda."""

    @classmethod
    def setUpClass(cls):
        RefinoResumoEAuditoria.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        RefinoResumoEAuditoria.tearDownClass.__func__(cls)

    def _run(self, payload, jan='180'):
        return RefinoResumoEAuditoria._run(self, payload, jan=jan)

    def _serie(self):
        rng = np.random.default_rng(7)
        ruido = (rng.normal(0, 2, 43) + 20.0).tolist()
        p = copy.deepcopy(self.base)
        p['modalidades']['Bike']['melhores_esforcos']['60'] = _serie_sintetica(ruido, self.ref, med=300.0, mediana_rec=300.0)
        return p, ruido

    def test_borda_direita_sinalizada_e_ajuste_inalterado(self):
        p, ruido = self._serie()
        reg = self._run(p)
        grade = reg['__ultimo_norm']['ajuste']['60']['grade']
        ultimo = [q for q in grade if q['y'] is not None][-1]
        self.assertEqual(ultimo['lado'], 'direita')            # ultimo ponto sem observacoes a direita
        self.assertEqual(ultimo['nR'], 0)
        meio = [q for q in grade if q['y'] is not None][len(grade) // 2]
        self.assertIsNone(meio['lado'])                         # interior com suporte dos dois lados
        # ajuste identico a referencia independente (sem nenhuma alteracao de estimativa)
        T, Y = _obs_payload(p, '60', 300.0)
        ref = {round(float(g), 6): f for g, f, sd in _ajuste_ref(T, Y) if f is not None}
        for q in grade:
            if q['y'] is not None and round(q['t'], 6) in ref:
                self.assertAlmostEqual(q['y'], ref[round(q['t'], 6)], places=6)

    def test_pontos_e_contagens_preservados(self):
        p, ruido = self._serie()
        reg = self._run(p)
        self.assertEqual(reg['__ultimo_norm']['desenhados']['60'], len(ruido))   # nenhum ponto removido
        self.assertEqual(reg['__ultimo_norm']['ajuste']['60']['n_obs'], len(ruido))

    def test_texto_no_resumo_e_legenda_sem_mudar_a_classificacao(self):
        p, _ = self._serie()
        reg = self._run(p)
        linha = _linha(reg['__html'], '1 min')
        self.assertIn('suporte unilateral à direita', linha)
        self.assertIn('ascendente' if reg['__ultimo_norm']['sensibilidade']['60']['dir'] == 'ascendente' else reg['__ultimo_norm']['sensibilidade']['60']['dir'], linha)
        self.assertIn('linha clara = trecho com suporte unilateral ou limitado nas bordas', reg['__html'])
        self.assertIn('Nenhum ponto é removido', reg['__html'])

    def test_aviso_nas_duas_extremidades(self):
        p, _ = self._serie()
        reg = self._run(p)
        html = reg['__html']
        # Só 1 min muda: a borda esquerda difere das demais durações -> avisos por linha, sem nota consolidada.
        linha = _linha(html, '1 min')
        self.assertNotIn('Suporte unilateral nas bordas', html)
        self.assertIn('suporte unilateral à direita nos dados recentes', linha)
        self.assertIn('suporte unilateral à esquerda no início da série', linha)
        self.assertNotIn('suporte limitado dos dois lados', linha)

    def test_nota_consolidada_com_datas_e_lados(self):
        p, ruido = self._serie()
        for k in DURACOES:   # mesma série em todas as durações -> mesmas bordas
            p['modalidades']['Bike']['melhores_esforcos'][k] = _serie_sintetica(ruido, self.ref, med=300.0, mediana_rec=300.0)
        reg = self._run(p)
        html = reg['__html']
        grades = {k: reg['__ultimo_norm']['ajuste'][k]['grade'] for k in DURACOES}
        def bordas(g):
            ok = [q for q in g if q['y'] is not None]
            d = [q['t'] for q in ok if q['lado'] in ('direita', 'ambos')]
            e = [q['t'] for q in ok if q['lado'] in ('esquerda', 'ambos')]
            return d, e
        d0, e0 = bordas(grades[DURACOES[0]])
        for k in DURACOES:
            self.assertEqual(bordas(grades[k]), (d0, e0))   # premissa do cenário
        self.assertTrue(d0 and e0)
        self.assertEqual(html.count('Suporte unilateral nas bordas'), 1)
        self.assertIn(_fmt_data(d0[0]) + ' a ' + _fmt_data(d0[-1]), html)
        self.assertIn(_fmt_data(e0[0]) + ' a ' + _fmt_data(e0[-1]), html)
        self.assertIn('nenhum ponto é removido nem alterado', html)
        self.assertNotIn('suporte unilateral à direita nos dados recentes', html)   # sem repetição por linha

    def test_avisos_individuais_quando_bordas_diferem(self):
        p, _ = self._serie()
        reg = self._run(p)
        html = reg['__html']
        grades = {k: reg['__ultimo_norm']['ajuste'][k]['grade'] for k in DURACOES}
        def lados(g):
            return [(q['t'], q['lado']) for q in g if q['y'] is not None]
        self.assertFalse(all(lados(grades[k]) == lados(grades[DURACOES[0]]) for k in DURACOES))
        self.assertNotIn('Suporte unilateral nas bordas', html)
        self.assertIn('suporte unilateral à direita nos dados recentes', html)

    def test_regra_de_lado_recomputada_para_todas_as_duracoes(self):
        """Para cada ponto da grade: lado e contagens batem com a regra (menos de 4 observacoes de um lado em +-90 dias)."""
        reg = self._run(self.base)
        for k in DURACOES:
            with self.subTest(duracao=k):
                med = self.base['modalidades']['Bike']['melhores_esforcos'][k]['janelas']['180']['mediana_anterior']
                T, _ = _obs_payload(self.base, k, med)
                for q in reg['__ultimo_norm']['ajuste'][k]['grade']:
                    if q['y'] is None:
                        continue
                    d = T - q['t']
                    dentro = np.abs(d) < H
                    nL = int(np.sum(dentro & (d < 0)))
                    nR = int(np.sum(dentro & (d > 0)))
                    self.assertEqual((q['nL'], q['nR']), (nL, nR))
                    esperado = ('ambos' if nR < 4 and nL < 4 else 'direita' if nR < 4 else 'esquerda' if nL < 4 else None)
                    self.assertEqual(q['lado'], esperado)

if __name__ == '__main__':
    unittest.main()
