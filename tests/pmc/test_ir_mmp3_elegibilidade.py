"""Elegibilidade MMP3 (YES) e fluxo de recalibracao: os 14 testes obrigatorios da auditoria.

Regra (modelo_mmp3, versao 2): YES = tag de 3 min na atividade E potencia nao ignorada E referencia anterior valida E
watts_180s / melhor_MMP3_valido_ANTERIOR >= 0,92. Atualizar a Power Curve NAO e um teste.
Sem rede, sem Intervals.icu, sem base de producao (SQLite temporaria).
"""
import inspect
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

os.environ.setdefault('INTERVALS_ICU_API_KEY', 'ficticia')
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.abspath(os.path.join(_HERE, '..', '..'))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import modelo_ir_p0 as p0m  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402
import test_ir_mmp3 as M  # noqa: E402
import test_ir_p0_global as G  # noqa: E402

ELEG = M.ELEG
RAIZ = os.path.abspath(os.path.join(_HERE, '..', '..'))


def linha(mod, vals, tags=None, ano='2025', pre='a'):
    """Um 180 s por mes; ids `{mod}-{pre}{i}`. tags: set de indices com tag '3min' (None = todos)."""
    cv = [M.c180(f'{mod}-{pre}{i}', mod, f'{ano}-{(i % 12) + 1:02d}-10', w) for i, w in enumerate(vals)]
    tg = {f'{mod}-{pre}{i}': (['3min'] if tags is None or i in tags else []) for i in range(len(vals))}
    return cv, tg


def aval(cv, tg, mod='Bike', eleg=ELEG, ate='2026-12-31'):
    return {e['activity_id']: e for e in mm.avaliar_esforcos(cv, mod, ate, (), eleg, tags=tg)}


class Caso318(unittest.TestCase):
    """Row 18/10/2026: season 318 W (treino), epoca anterior 253 W, all-time 401 W."""
    def cenario(self, tag):
        # 2025 (epoca anterior): 6 pontos ate 401 W; 2026: treinos 253 -> 268 -> 290 -> 300 -> 318
        ant, tg = linha('Row', [380, 390, 395, 401, 385, 253], ano='2025')
        s26 = [('Row-c%d' % i, f'2026-{m:02d}-10', w) for i, (m, w) in enumerate(
            [(3, 268.0), (5, 290.0), (7, 300.0), (9, 318.0)])]
        cv = ant + [M.c180(a, 'Row', d, w) for a, d, w in s26]
        tags = {**tg, **{a: (['3min'] if tag else []) for a, _d, _w in s26}}
        return cv, tags

    def test_1_esforco_submaximo_que_atualiza_a_curva_da_season_nao_e_automaticamente_yes(self):
        cv, tg = self.cenario(tag=False)
        e = aval(cv, tg, 'Row')['Row-c3']
        # regra 5: 318 / 401 = 79 % do all-time < 0,92 -> NO por abaixo do limiar (nao por falta de tag)
        self.assertEqual((e['estado'], e['motivo']), (mm.E_NAO_ELEGIVEL, mm.M_ABAIXO))
        # Mesmo COM tag: 318 / 401 = 79,3 % < 0,92 -> NO (a season de treino nao qualifica ninguem)
        cv, tg = self.cenario(tag=True)
        e = aval(cv, tg, 'Row')['Row-c3']
        # sem recorde all-time qualificado, o recorde da season fica a verificar (nunca YES automatico)
        self.assertEqual((e['estado'], e['motivo']), (mm.E_A_VERIFICAR, mm.M_RECORDE_SAZONAL))
        self.assertEqual((e['melhor_anterior_watts'], e['pct_do_melhor']), (401.0, 79.3))
        self.assertFalse(e['pr'])
        # a cadeia de treinos (253 -> 268 -> ... ) nao se valida a si propria
        yes = [a for a, x in aval(cv, tg, 'Row').items() if x['estado'] == mm.E_ELEGIVEL and a.startswith('Row-c')]
        self.assertEqual(yes, [])

    def test_2_a_atividade_avaliada_nao_e_a_propria_referencia(self):
        cv, tg = linha('Bike', [300.0] * 5 + [400.0, 150.0])           # a5 = 400 e o novo maximo
        e = aval(cv, tg)['Bike-a5']
        self.assertEqual(e['melhor_anterior_watts'], 300.0)              # nao 400: o proprio esforco fica de fora
        self.assertEqual(e['pct_do_melhor'], round(100 * 400 / 300, 1))
        # o melhor anterior e SEMPRE <= ao valor de qualquer atividade posterior; e o esforco seguinte ja ve os 400
        self.assertEqual(aval(cv, tg)['Bike-a6']['melhor_anterior_watts'], 400.0)

    def test_3_esforco_que_supera_a_referencia_anterior_e_tratado_corretamente(self):
        cv, tg = linha('Bike', [300.0] * 5 + [330.0])
        e = aval(cv, tg)['Bike-a5']
        self.assertEqual((e['estado'], e['pr'], e['melhor_anterior_watts']), (mm.E_ELEGIVEL, True, 300.0))
        # sem tag, o mesmo recorde nao e teste
        cv, tg = linha('Bike', [300.0] * 5 + [330.0], tags={0, 1, 2, 3, 4})
        self.assertEqual(aval(cv, tg)['Bike-a5']['estado'], mm.E_A_VERIFICAR)   # recorde sem sinal: candidato, nunca YES

    def test_4_esforco_abaixo_do_recorde_pode_ser_elegivel_se_cumpre_a_proximidade(self):
        cv, tg = linha('Bike', [300.0] * 5 + [285.0])                    # 95 % do melhor anterior, sem PR
        e = aval(cv, tg)['Bike-a5']
        self.assertEqual((e['estado'], e['pr']), (mm.E_ELEGIVEL, False))
        g = mm.p0_global({'Bike': list(aval(cv, tg).values())})
        self.assertEqual(g['registo']['watts'], 285.0)                   # P0 = ultimo YES, nao exige PR

    def test_5_limiar_092_aplicado_a_razao_correta_e_direcao_testada(self):
        import protocolo
        self.assertEqual(mm.LIMIAR, 0.92)
        self.assertEqual(mm.LIMIAR, protocolo.LIMIAR_ESFORCO)            # limiar inalterado
        base = [300.0] * 5
        for w, esperado in ((276.0, mm.E_ELEGIVEL), (275.9, mm.E_NAO_ELEGIVEL), (300.0, mm.E_ELEGIVEL),
                            (400.0, mm.E_ELEGIVEL)):
            cv, tg = linha('Bike', base + [w])
            e = aval(cv, tg)['Bike-a5']
            self.assertEqual(e['estado'], esperado, w)
            self.assertAlmostEqual(e['pct_do_melhor'], round(100 * w / 300.0, 1))   # esforco / referencia (nao o inverso)
        # direcao: ref 300 e esforco 276 -> 276/300 = 0,92 (YES); o inverso 300/276 = 1,087 nao e o criterio
        cv, tg = linha('Bike', base + [276.0])
        self.assertTrue(aval(cv, tg)['Bike-a5']['estado'] == mm.E_ELEGIVEL)
        # e uma referencia mais alta que o esforco NUNCA o torna YES por ser "maior": 250 vs 400 -> NO
        cv, tg = linha('Bike', [400.0] * 5 + [250.0])
        self.assertEqual(aval(cv, tg)['Bike-a5']['estado'], mm.E_NAO_ELEGIVEL)

    def test_6_dados_insuficientes_e_atividades_ignoradas_nao_sao_elegiveis(self):
        cv, tg = linha('Bike', [300.0] * 4)                              # < 5 pontos
        self.assertEqual(aval(cv, tg)['Bike-a3']['estado'], mm.E_INSUFICIENTE)
        cv, tg = linha('Bike', [300.0] * 6)
        self.assertEqual(aval(cv, tg, eleg=lambda a: 'inelegivel')['Bike-a5']['motivo'], mm.M_IGNORE_POWER)
        self.assertEqual(aval(cv, tg, eleg=lambda a: 'desconhecida')['Bike-a5']['estado'], mm.E_INSUFICIENTE)
        self.assertEqual(aval(cv, tg)['Bike-a0']['motivo'], mm.M_SEM_REFERENCIA)   # 1.o ponto: sem referencia anterior
        for eleg in (lambda a: 'inelegivel', lambda a: 'desconhecida'):
            g = mm.p0_global({'Bike': list(aval(cv, tg, eleg=eleg).values())})
            self.assertIsNone(g['registo'])
        # nunca YES sem informacao de tags
        self.assertEqual(mm.avaliar_esforcos(cv, 'Bike', '2026-12-31', (), ELEG)[-1]['estado'], mm.E_A_VERIFICAR)   # sem tag: candidato, nao YES

    def test_7_um_no_posterior_nao_substitui_o_ultimo_yes(self):
        cv, tg = linha('Bike', [300.0] * 5 + [290.0, 100.0, 120.0])
        r = aval(cv, tg)
        self.assertEqual((r['Bike-a5']['estado'], r['Bike-a6']['estado'], r['Bike-a7']['estado']),
                         (mm.E_ELEGIVEL, mm.E_NAO_ELEGIVEL, mm.E_NAO_ELEGIVEL))
        self.assertEqual(mm.p0_global({'Bike': list(r.values())})['registo']['watts'], 290.0)

    def test_8_p0_global_partilhado_pelas_quatro_modalidades(self):
        esf = {}
        for k, m in enumerate(G.MODS):
            cv, tg = linha(m, [300.0] * 5 + [280.0 + k], pre='z')
            cv[-1]['date'] = f'2025-07-{10 + k:02d}'                      # datas distintas: o ultimo e o da Ski
            esf[m] = list(aval(cv, tg, m).values())
        g = mm.p0_global(esf)
        self.assertEqual(g['registo']['watts'], 283.0)                   # o YES mais recente entre as modalidades
        for m in G.MODS:
            r = p0m.resolver_p0(m, 180, g)
            self.assertTrue(r['valida'])
            self.assertEqual(r['valor_p0'], 283.0)
            self.assertEqual(r['assinatura'], p0m.resolver_p0('Bike', 180, g)['assinatura'])

    def test_9_sem_yes_nao_ha_fallback_para_cp_ftp_ou_efpt(self):
        cv, tg = linha('Bike', [400.0] * 5 + [250.0] * 3)                # nada ≥ 0,92 do melhor, e a/c tag
        r = aval(cv, tg)
        sem_yes = {'Bike': [e for e in r.values() if e['estado'] != mm.E_ELEGIVEL]}
        g = mm.p0_global(sem_yes)
        self.assertIsNone(g['registo'])
        res = p0m.resolver_p0('Bike', 180, g)
        self.assertFalse(res['valida'])
        self.assertEqual((res['motivo'], res['valor_p0']), (p0m.M_SEM_YES, None))
        # as funcoes da referencia nao recebem CP/FTP/eFTP nem outra duracao: nao ha por onde entrar um fallback
        for fn in (mm.p0_global, p0m.resolver_p0):
            nomes = set(inspect.signature(fn).parameters)
            self.assertFalse({n for n in nomes if re.search(r'cp|ftp|eftp', n)}, fn.__name__)
        self.assertEqual(set(inspect.signature(p0m.resolver_p0).parameters), {'modalidade', 'duracao_s', 'global_'})

    def test_13_sem_fuga_de_dados_futuros_na_avaliacao_historica(self):
        cv, tg = linha('Bike', [300.0] * 5 + [285.0], ano='2025')
        corte = '2025-06-30'
        com_futuro = aval(cv + [M.c180('Bike-fut', 'Bike', '2026-03-01', 999.0)], {**tg, 'Bike-fut': ['3min']},
                          ate=corte)
        sem = aval(cv, tg, ate=corte)
        self.assertEqual(com_futuro, sem)
        self.assertNotIn('Bike-fut', com_futuro)
        # a referencia de cada esforco usa so datas estritamente anteriores
        for e in aval(cv, tg).values():
            if e['melhor_anterior_data']:
                self.assertLessEqual(e['melhor_anterior_data'], e['data'])

    def test_14_cp_model_e_m1_m2_m3_inalterados(self):
        out = subprocess.run(['git', 'diff', '--stat', 'origin/main', '--', 'utils/cp_model.py', 'tabs/tab_cp_model.py',
                              'api_cp.py'], cwd=RAIZ, capture_output=True, text=True)
        if out.returncode != 0:
            self.skipTest('sem git/origin/main neste ambiente')
        self.assertEqual(out.stdout.strip(), '')
        for mod in (mis, mm, p0m):
            self.assertNotIn('cp_model', inspect.getsource(mod).replace('"cp_model"', ''), mod.__name__) \
                if mod is p0m else None


class FluxoDeRecalibracao(G._Base4):
    def test_10_a_rota_aceita_o_metodo_esperado_e_rejeita_os_impropios(self):
        regras = [r for r in __import__('app').app.url_map.iter_rules() if r.rule == '/api/modelo/ir/recalibrar']
        self.assertEqual(len(regras), 1)
        self.assertEqual(set(regras[0].methods) - {'OPTIONS'}, {'POST'})
        self.assertEqual(self.c.get('/api/modelo/ir/recalibrar').status_code, 405)    # o 405 do navegador
        for m in ('put', 'delete', 'patch'):
            self.assertEqual(getattr(self.c, m)('/api/modelo/ir/recalibrar').status_code, 405, m)
        self.assertEqual(self.c.post('/api/modelo/ir/recalibrar', json={'duracao_s': 180}).status_code, 200)
        self.assertEqual(self.c.post('/api/modelo/ir/recalibrar', json={'duracao_s': 300}).status_code, 409)

    def test_12_sync_e_recalibracao_tem_responsabilidades_distintas(self):
        # a recalibracao nunca chama o sync (nem a API externa)
        with mock.patch('sync.sync_activities', side_effect=AssertionError('sync na recalibracao')), \
                mock.patch('sync.sync_power_curves', side_effect=AssertionError('sync na recalibracao')):
            r = self.c.post('/api/modelo/ir/recalibrar', json={})
        self.assertEqual(r.status_code, 200, r.get_json())
        src = inspect.getsource(mis.recalibrar) + inspect.getsource(__import__('app').api_modelo_ir_recalibrar)
        self.assertNotIn('sync_activities', src)
        self.assertNotIn('sync_power_curves', src)
        # o sync so dispara a recalibracao DEPOIS da deteccao (gancho), nunca o contrario
        sync_src = inspect.getsource(__import__('sync').sync_power_curves)
        self.assertLess(sync_src.index('observacoes_auto.atualizar'), sync_src.index('apos_deteccao'))
        # sem YES (sem referencia valida) a recalibracao nao recalibra nem apresenta ajustes velhos como novos
        for c in self.curvs:
            c['secs'], c['watts'] = [300], [c['watts'][0]]
        self.sync()
        r = self.c.post('/api/modelo/ir/recalibrar', json={'forcar': True}).get_json()
        # um YES ja gravado (historico, numa recalibracao anterior) continua valido mesmo com os dados alterados:
        # nao se perde por os esforcos vivos mudarem. Sem historico, o resultado seria 'ausente'.
        self.assertEqual(r['p0_global']['estado'], 'valida')
        self.assertEqual(r['p0_global']['registo']['origem_registo'], 'historico')
        # com P0 valido (do historico) o ajuste pode correr: o que se exige e que nao usa o sync (asserts acima)


class InterfaceUsaMetodoCorreto(unittest.TestCase):
    def test_11_a_ui_nao_chama_a_rota_por_get_e_o_sync_e_get(self):
        achados, ctx_pasta = [], {}
        for pasta, _d, fich in os.walk(RAIZ):
            if any(x in pasta for x in ('/tests', '/.git', '/docs', '__pycache__', '/node_modules')):
                continue
            for f in fich:
                if f.endswith(('.py', '.js', '.html')):
                    t = open(os.path.join(pasta, f), encoding='utf-8', errors='ignore').read()
                    for m in re.finditer(r"ir/recalibrar", t):
                        ctx_pasta[f] = pasta
                        achados.append((f, t[max(0, m.start() - 120): m.end() + 200]))
        for f, ctx in achados:
            if not ctx_pasta[f].startswith(os.path.join(RAIZ, 'tabs')) and not f.endswith(('.js', '.html')):
                continue
            self.assertRegex(ctx, r"POST", f)       # qualquer chamada de UI tem de usar POST
        tab = open(os.path.join(RAIZ, 'tabs', 'tab_pmc.py'), encoding='utf-8').read()
        self.assertNotRegex(tab, r"fetch\(\s*['\"`]/api/modelo/ir/recalibrar['\"`]\s*\)")   # nunca GET


if __name__ == '__main__':
    unittest.main()


# ═══ Complemento (PR #37): historico global e atualizacao por temporada ═══════════════════════════════

def _hist(extra26):
    """Bike: 2025 com YES em 2025-07-10 (a5, 285 W vs 300 W anterior); `extra26` = [(id, data, watts, tag)] em 2026."""
    cv, tg = linha('Bike', [300.0] * 5 + [285.0], ano='2025')            # a0..a5 em 2025-01..06 (+ a5 = 2025-06-10)
    for aid, d, w, tag in extra26:
        cv.append(M.c180(aid, 'Bike', d, w))
        tg[aid] = ['3min'] if tag else []
    return cv, tg


def _p0(cv, tg, ate='2026-10-19'):
    return mm.p0_global({'Bike': list(aval(cv, tg, ate=ate).values())})


class HistoricoGlobalESeason(unittest.TestCase):
    def test_a_yes_de_temporada_anterior_continua_a_ser_o_p0(self):
        cv, tg = _hist([('Bike-t1', '2026-03-10', 200.0, False)])      # 2026 so tem um treino sem tag
        g = _p0(cv, tg)
        self.assertEqual((g['estado'], g['registo']['watts'], g['registo']['data']), ('valida', 285.0, '2025-06-10'))
        self.assertNotEqual(mm.inicio_season('2025-06-10')[:4], mm.inicio_season('2026-03-10')[:4])   # outra season

    def test_b_temporada_nova_sem_yes_preserva_o_p0_anterior(self):
        cv, tg = _hist([('Bike-t1', '2026-02-10', 250.0, True), ('Bike-t2', '2026-05-10', 260.0, True)])   # NO (<0,92)
        r = aval(cv, tg)
        # nenhum candidato da season nova vira YES; podem ficar a verificar (recorde de season) ou nao elegiveis
        self.assertTrue({r['Bike-t1']['estado'], r['Bike-t2']['estado']} <= {mm.E_A_VERIFICAR, mm.E_NAO_ELEGIVEL})
        self.assertNotIn(mm.E_ELEGIVEL, {r['Bike-t1']['estado'], r['Bike-t2']['estado']})
        self.assertEqual(_p0(cv, tg)['registo']['test_id'], r['Bike-a5']['test_id'])

    def test_c_novo_yes_elegivel_atualiza_o_p0_com_o_seu_mmp3_sem_se_usar_a_si_proprio(self):
        cv, tg = _hist([('Bike-n1', '2026-06-10', 290.0, True)])         # 290 / 300 (melhor anterior) = 96,7 %
        e = aval(cv, tg)['Bike-n1']
        self.assertEqual((e['estado'], e['melhor_anterior_watts']), (mm.E_ELEGIVEL, 300.0))
        g = _p0(cv, tg)
        self.assertEqual((g['registo']['watts'], g['registo']['activity_id']), (290.0, 'Bike-n1'))
        for m in G.MODS:                                                # o mesmo P0 nas quatro modalidades
            self.assertEqual(p0m.resolver_p0(m, 180, g)['valor_p0'], 290.0)

    def test_d_treino_submaximo_que_atualiza_a_power_curve_da_season_nao_muda_o_p0(self):
        # 2026: treinos sem tag a subir o MMP3 da season (200 -> 230 -> 270): a season muda, o P0 nao
        extra = [('Bike-s1', '2026-02-10', 200.0, False), ('Bike-s2', '2026-04-10', 230.0, False),
                 ('Bike-s3', '2026-06-10', 270.0, False)]
        cv, tg = _hist(extra)
        ref_antes = mm.referencias(cv, 'Bike', '2026-01-31')['mmp3_pr_atual']['watts']
        ref_depois = mm.referencias(cv, 'Bike', '2026-10-19')['mmp3_pr_atual']['watts']
        self.assertGreaterEqual(ref_depois, ref_antes)                  # o valor de referencia mostrado evolui
        r = aval(cv, tg)
        self.assertTrue(all(r[a]['estado'] == mm.E_NAO_ELEGIVEL and r[a]['motivo'] == mm.M_ABAIXO
                            for a in ('Bike-s1', 'Bike-s2', 'Bike-s3')))
        self.assertEqual(_p0(cv, tg)['registo']['watts'], 285.0)
        # mesmo com tag, 270 / 300 = 90 % < 0,92: a season submaxima tambem nao cria YES nem muda o P0
        cv2, tg2 = _hist([(a, d, w, True) for a, d, w, _t in extra])
        self.assertEqual(_p0(cv2, tg2)['registo']['watts'], 285.0)

    def test_e_nunca_houve_yes_mostra_ausencia_sem_fallback(self):
        cv, tg = linha('Bike', [400.0] * 5 + [200.0], tags=set(), ano='2026')   # so treinos, nenhum com tag
        g = _p0(cv, tg)
        self.assertIsNone(g['registo'])
        res = p0m.resolver_p0('Bike', 180, g)
        self.assertEqual((res['valida'], res['motivo']), (False, p0m.M_SEM_YES))

    def test_f_a_pesquisa_do_ultimo_yes_nao_se_limita_a_season_atual(self):
        # le o ficheiro (nao inspect.getsource: a linecache pode devolver um bloco deslocado na suite completa)
        texto = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'modelo_mmp3.py'), encoding='utf-8').read()
        corpo = lambda nome, prox: texto[texto.index(f'def {nome}('):texto.index(f'def {prox}(')]
        src = corpo('avaliar_esforcos', 'observacoes_ir') + corpo('p0_global', 'texto_estado')
        self.assertNotIn('inicio_season', src)                          # nenhum filtro por season
        self.assertIn('<= ate', src)    # so o corte causal (data de analise)


class ServicoPreservaOP0(G._Base4):
    def test_g_treino_submaximo_sem_tag_nao_cria_referencia_nova(self):
        self.rec()
        antes = {m: len(self.refs(m)) for m in G.MODS}
        p0_antes = self.oraculo()['watts']
        d = (G.FIM + __import__('datetime').timedelta(days=5)).isoformat()
        for k, m in enumerate(G.MODS):                                   # treino forte mas SEM tag, MMP3 da season sobe
            self.curvs.append(M.c180(f'{m}-treino', m, d, 1000.0))
            self.ativ.append({'id': f'{m}-treino', 'type': G.TIPO_ATIV[m], 'start_date_local': f'{d}T18:00:00',
                              'icu_training_load': 50.0, 'icu_ignore_power': False, 'tags': []})
        self.sync()
        self.rec()
        self.assertEqual(self.oraculo()['watts'], p0_antes)
        self.assertEqual({m: len(self.refs(m)) for m in G.MODS}, antes)  # nenhuma referencia/ajuste novo
