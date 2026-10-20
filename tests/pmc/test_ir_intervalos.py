"""Deteccao de esforcos de 3 min a partir dos intervalos das atividades (`interval_summary`) e diagnostico.

Pura e offline: sem Intervals.icu, sem producao. A tag manual continua valida; um intervalo so promove a YES nas
condicoes explicitas de modelo_mmp3_intervalos; o resto fica `a_verificar`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault('INTERVALS_ICU_API_KEY', 'ficticia')
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..'))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import modelo_ir_p0 as p0m  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402
import modelo_mmp3_intervalos as mi  # noqa: E402
import test_ir_mmp3 as M  # noqa: E402

ELEG = M.ELEG


def serie(mod='Bike', vals=(300.0,) * 5, ano='2025', pre='a'):
    return [M.c180(f'{mod}-{pre}{i}', mod, f'{ano}-{(i % 12) + 1:02d}-10', w) for i, w in enumerate(vals)]


def ativs(cv, resumos=None, tags=None):
    """Atividades minimas das curvas; resumos/tags: {activity_id: valor}."""
    return [{'id': c['activity_id'], 'type': 'Ride', 'start_date_local': c['date'] + 'T08:00:00',
             'icu_ignore_power': False, 'interval_summary': (resumos or {}).get(c['activity_id']),
             'tags': (tags or {}).get(c['activity_id'])} for c in cv]


def avaliar(cv, at, mod='Bike', ate='2026-12-31', eleg=ELEG):
    por_id = mis._atividades_por_id(at)
    return {e['activity_id']: e for e in mm.avaliar_esforcos(
        cv, mod, ate, (), eleg, tags=mis._tags_por_atividade(at), atividades_por_id=por_id)}


def com_ultimo(vals, resumo, tag=None, mod='Bike'):
    cv = serie(mod, vals)
    alvo = cv[-1]['activity_id']
    at = ativs(cv, {alvo: resumo}, {alvo: tag})
    return avaliar(cv, at, mod)[alvo]


class Parser(unittest.TestCase):
    def test_formatos(self):
        g, n, d = mi.grupos_de_potencia({'interval_summary': '["1x 2m56s 189w","3x 3m1s 156bpm","3x 4m9s 5:10","1x 90s 200w"]'})
        self.assertEqual(g, [{'n': 1, 'duracao_s': 176, 'watts': 189.0}, {'n': 1, 'duracao_s': 90, 'watts': 200.0}])
        self.assertEqual((n, d), (4, 2))                                # bpm e ritmo nao sao potencia
        self.assertEqual(mi.grupos_de_potencia({'interval_summary': ['2x 5m 250w']})[0], [{'n': 2, 'duracao_s': 300, 'watts': 250.0}])
        self.assertEqual(mi.grupos_de_potencia({'interval_summary': '1x 1h2m3s 150w'})[0][0]['duracao_s'], 3723)

    def test_ausente_vazio_invalido(self):
        for v in (None, '', '[]', '[bad', 5, [], [None, 3], '["lixo"]', '["0x 3m 200w"]', '["1x 0s 200w"]'):
            self.assertEqual(mi.grupos_de_potencia({'interval_summary': v})[0], [], repr(v))
        self.assertEqual(mi.grupos_de_potencia(None)[0], [])
        self.assertEqual(mi.classificar({'interval_summary': None}, 300, 300, 0.92)['classe'], mi.C_SEM_DADOS)


class Classificacao(unittest.TestCase):
    def cls(self, resumo, w180=300.0, ref=300.0):
        return mi.classificar({'interval_summary': resumo}, w180, ref, 0.92)['classe']

    def test_1_intervalo_de_3_min_isolado_e_automatico(self):
        for r in ('["1x 3m 290w"]', '["1x 2m55s 290w"]', '["1x 3m5s 290w"]', '["1x 3m 276w"]'):     # 276 = 0,92 x 300
            self.assertEqual(self.cls(r), mi.C_AUTO, r)

    def test_2_5_min_proximo_do_mmp3_e_so_a_verificar(self):
        c = mi.classificar({'interval_summary': '["1x 5m 290w"]'}, 300.0, 300.0, 0.92)
        self.assertEqual(c['classe'], mi.C_LONGO)
        self.assertIsNone(c['origem'])                                  # sem origem de teste: nunca YES

    def test_3_abaixo_do_limiar(self):
        self.assertEqual(self.cls('["1x 3m 275w"]'), mi.C_ABAIXO)       # 275 / 300 < 0,92
        self.assertEqual(self.cls('["1x 5m 275w"]'), mi.C_SEM_3MIN)

    def test_4_duracao_fora_da_janela(self):
        for r in ('["1x 2m54s 300w"]', '["1x 3m6s 300w"]', '["1x 60s 300w"]', '["1x 20m 300w"]'):
            self.assertIn(self.cls(r), (mi.C_ABAIXO, mi.C_SEM_3MIN, mi.C_LONGO), r)
            self.assertNotEqual(self.cls(r), mi.C_AUTO, r)

    def test_5_repeticoes_nao_sao_um_unico_esforco_maximo(self):
        self.assertEqual(self.cls('["3x 3m 295w"]'), mi.C_REPETICOES)

    def test_6_pico_de_180_s_noutro_trecho(self):
        self.assertEqual(self.cls('["1x 3m 280w"]', w180=330.0), mi.C_PICO_FORA)   # 280 / 330 < 0,92

    def test_7_sem_referencia_nao_ha_candidato(self):
        self.assertEqual(mi.classificar({'interval_summary': '["1x 3m 290w"]'}, 300.0, None, 0.92)['classe'], mi.C_ABAIXO)


class NoEsforco(unittest.TestCase):
    VALS = [300.0] * 5 + [290.0]

    def test_intervalo_isolado_de_3_min_torna_o_esforco_yes_com_origem(self):
        e = com_ultimo(self.VALS, '["1x 3m 288w"]')
        self.assertEqual((e['estado'], e['origem_classificacao']), (mm.E_ELEGIVEL, 'intervalo_detectado'))
        self.assertEqual(e['watts'], 290.0)                             # P0 = MMP3 (maximo movel) do registo, nao a media
        self.assertIn('intervalo isolado de 180s', e['motivo_decisao'])

    def test_tag_manual_continua_a_valer_e_tem_prioridade(self):
        e = com_ultimo(self.VALS, None, tag=['3min'])
        self.assertEqual((e['estado'], e['origem_classificacao']), (mm.E_ELEGIVEL, 'tag_manual'))
        e = com_ultimo(self.VALS, '["1x 3m 288w"]', tag='["3min"]')
        self.assertEqual(e['origem_classificacao'], 'tag_manual')       # um so registo, uma so origem

    def test_5_min_nao_e_equivalente_a_um_teste_de_180_s(self):
        e = com_ultimo(self.VALS, '["1x 5m 288w"]')
        # 5 min nao e um teste de 3 min nem de 180 s: fica a verificar (revisao manual), nunca YES
        self.assertEqual((e['estado'], e['motivo'], e['a_verificar']), (mm.E_A_VERIFICAR, mi.C_LONGO, True))
        self.assertEqual(e['classe_intervalo'], mi.C_LONGO)
        self.assertIsNone(e['origem_classificacao'])

    def test_repeticoes_ficam_a_verificar(self):
        e = com_ultimo(self.VALS, '["4x 3m 289w"]')
        self.assertEqual((e['estado'], e['a_verificar'], e['classe_intervalo']), (mm.E_A_VERIFICAR, True, mi.C_REPETICOES))

    def test_sem_dados_de_intervalos_ou_potencia_invalida(self):
        for r in (None, '', '["3x 3m1s 156bpm"]', '["1x 3m 5:10"]'):
            e = com_ultimo(self.VALS, r)
            self.assertEqual((e['estado'], e['origem_classificacao'], e['a_verificar']), (mm.E_NAO_ELEGIVEL, None, False), r)

    def test_treino_forte_nao_e_teste_so_por_elevar_a_curva_ou_bater_recorde(self):
        e = com_ultimo([300.0] * 5 + [400.0], '["1x 20m 280w"]')        # recorde, mas sem sinal de teste
        self.assertEqual((e['estado'], e['pr']), (mm.E_NAO_ELEGIVEL, True))

    def test_o_esforco_continua_a_exigir_a_regra_de_proximidade_e_referencia(self):
        self.assertEqual(com_ultimo([300.0] * 5 + [250.0], '["1x 3m 250w"]')['estado'], mm.E_NAO_ELEGIVEL)   # 83 %
        cv = serie('Bike', [300.0] * 4)                                 # amostra < 5
        a = ativs(cv, {cv[-1]['activity_id']: '["1x 3m 300w"]'})
        self.assertEqual(avaliar(cv, a)[cv[-1]['activity_id']]['estado'], mm.E_NAO_ELEGIVEL)   # sem sinal sem referencia
        cv = serie('Bike', self.VALS)
        a = ativs(cv, {cv[-1]['activity_id']: '["1x 3m 288w"]'})
        a[-1]['icu_ignore_power'] = True
        e = avaliar(cv, a, eleg=lambda i: 'inelegivel' if i == cv[-1]['activity_id'] else 'elegivel')[cv[-1]['activity_id']]
        self.assertEqual(e['motivo'], mm.M_IGNORE_POWER)                # ignorada nunca e YES
        e = avaliar(cv, a, eleg=lambda i: 'desconhecida')[cv[-1]['activity_id']]
        self.assertEqual(e['estado'], mm.E_INSUFICIENTE)

    def test_atividade_excluida_da_propria_referencia(self):
        cv = serie('Bike', [300.0] * 5 + [330.0])
        alvo = cv[-1]['activity_id']
        e = avaliar(cv, ativs(cv, {alvo: '["1x 3m 325w"]'}))[alvo]
        self.assertEqual(e['melhor_anterior_watts'], 300.0)             # nao 330: o proprio esforco fica fora
        self.assertEqual(e['origem_classificacao'], 'intervalo_detectado')

    def test_sem_fuga_de_dados_futuros(self):
        cv = serie('Bike', self.VALS)
        alvo = cv[5]['activity_id']
        base = ativs(cv, {alvo: '["1x 3m 288w"]'})
        futuro = cv + [M.c180('Bike-fut', 'Bike', '2027-03-01', 999.0)]
        a2 = base + ativs([futuro[-1]], {'Bike-fut': '["1x 3m 990w"]'})
        self.assertEqual(avaliar(futuro, a2, ate='2025-12-31')[alvo], avaliar(cv, base, ate='2025-12-31')[alvo])
        self.assertNotIn('Bike-fut', avaliar(futuro, a2, ate='2025-12-31'))

    def test_deduplicacao_um_esforco_por_atividade_mesmo_com_varios_intervalos(self):
        cv = serie('Bike', self.VALS)
        alvo = cv[5]['activity_id']
        at = ativs(cv, {alvo: '["1x 3m 288w","1x 2m58s 289w","2x 3m2s 285w","1x 5m 280w"]'})
        esf = mm.avaliar_esforcos(cv, 'Bike', '2026-12-31', (), ELEG, tags={}, atividades_por_id=mis._atividades_por_id(at))
        self.assertEqual(sum(1 for e in esf if e['activity_id'] == alvo), 1)
        self.assertEqual(len(mm.observacoes_ir(esf)), 1)                # uma observacao do IR por atividade
        self.assertEqual(mm.observacoes_ir(esf)[0]['watts'], 290.0)

    def test_modalidades_isoladas(self):
        cv = serie('Bike', self.VALS) + serie('Row', [200.0] * 5 + [190.0])
        at = ativs(cv, {'Row-a5': '["1x 3m 188w"]'})
        bike = avaliar(cv, at, 'Bike')
        row = avaliar(cv, at, 'Row')
        self.assertTrue(all(k.startswith('Bike-') for k in bike) and all(k.startswith('Row-') for k in row))
        self.assertEqual(row['Row-a5']['estado'], mm.E_ELEGIVEL)
        self.assertEqual(bike['Bike-a5']['estado'], mm.E_NAO_ELEGIVEL)   # o intervalo do Row nao valida o Bike


class P0Global(unittest.TestCase):
    def test_p0_so_muda_com_novo_yes_valido_e_e_o_mesmo_nas_quatro(self):
        cv = serie('Bike', [300.0] * 5 + [290.0, 250.0]) + serie('Row', [200.0] * 6, ano='2024', pre='r')
        at = ativs(cv, {'Bike-a5': '["1x 3m 288w"]', 'Bike-a6': '["1x 5m 248w"]'})
        esf = {m: list(avaliar(cv, at, m).values()) for m in ('Bike', 'Row', 'Run', 'Ski')}
        g = mm.p0_global(esf)
        self.assertEqual((g['registo']['watts'], g['registo']['origem_classificacao']), (290.0, 'intervalo_detectado'))
        for m in ('Bike', 'Row', 'Run', 'Ski'):
            self.assertEqual(p0m.resolver_p0(m, 180, g)['valor_p0'], 290.0)
        # um treino posterior mais forte mas sem sinal (so intervalo longo) nao muda o P0
        cv2 = cv + [M.c180('Bike-a7', 'Bike', '2026-08-10', 320.0)]
        at2 = at + ativs([cv2[-1]], {'Bike-a7': '["1x 5m 310w"]'})
        esf2 = {m: list(avaliar(cv2, at2, m).values()) for m in ('Bike', 'Row', 'Run', 'Ski')}
        self.assertEqual(mm.p0_global(esf2)['registo']['test_id'], g['registo']['test_id'])

    def test_sem_yes_nao_ha_fallback(self):
        cv = serie('Bike', [300.0] * 6)
        at = ativs(cv, {c['activity_id']: '["1x 5m 295w"]' for c in cv})
        g = mm.p0_global({'Bike': list(avaliar(cv, at).values())})
        self.assertIsNone(g['registo'])
        r = p0m.resolver_p0('Bike', 180, g)
        self.assertEqual((r['valida'], r['motivo'], r['valor_p0']), (False, p0m.M_SEM_YES, None))


class DiagnosticoEIndicacoes(unittest.TestCase):
    def test_contagens_por_modalidade_e_total(self):
        cv = serie('Bike', [300.0] * 5 + [290.0, 289.0, 288.0])
        at = ativs(cv, {'Bike-a5': '["1x 3m 288w","3x 3m1s 156bpm"]', 'Bike-a6': '["4x 3m 289w"]', 'Bike-a7': '["1x 5m 287w"]'},
                   {'Bike-a4': ['3min']})
        d = mm.diagnostico_esforcos({'Bike': list(avaliar(cv, at).values()), 'Row': []})
        t = d['total']
        self.assertEqual(t['n_intervalos_analisados'], 3)               # so grupos com potencia
        self.assertEqual((t['n_candidatos_duracao_3min'], t['n_candidatos_potencia_3min']), (2, 2))
        self.assertEqual(t['n_candidatos_longos_potencia'], 1)
        self.assertEqual((t['n_confirmados_tag_manual'], t['n_elegiveis_automaticos']), (1, 1))
        self.assertEqual(t['n_a_verificar'], 2)                         # repeticoes + longo
        self.assertEqual(d['por_modalidade']['Bike']['n_yes'], 2)
        self.assertEqual(d['por_modalidade']['Row']['n_180'], 0)

    def test_motivo_sem_yes_considera_os_intervalos(self):
        cv = serie('Bike', [300.0] * 6)
        d = mm.diagnostico_esforcos({'Bike': list(avaliar(cv, ativs(cv)).values())})
        self.assertEqual(d['motivo_sem_yes'], 'nenhuma_atividade_com_tag_3min')


class EstadoDoSolver(unittest.TestCase):
    def test_nunca_diz_falha_quando_nao_foi_chamado(self):
        sem_p0 = {'valida': False}
        self.assertEqual(mis.estado_solver({'estado': 'sem_ajuste'}, sem_p0, 0)['estado'], 'nao_executado')
        self.assertEqual(mis.estado_solver({'estado': 'sem_ajuste'}, sem_p0, 0)['motivo'], 'sem_p0')
        r = mis.estado_solver({'estado': 'sem_ajuste'}, {'valida': True}, 0)
        self.assertEqual((r['estado'], r['motivo']), ('nao_executado', 'sem_observacoes'))
        self.assertEqual((r['min_obs'], r['faltam_observacoes']), (15, 15))

    def test_estados_com_ajuste(self):
        ok = {'valida': True}
        self.assertEqual(mis.estado_solver({'estado': 'dados_insuficientes'}, ok, 3)['estado'], 'dados_insuficientes')
        self.assertEqual(mis.estado_solver({'estado': 'dados_insuficientes'}, ok, 3)['faltam_observacoes'], 12)
        self.assertEqual(mis.estado_solver({'estado': 'sem_convergencia'}, ok, 20)['estado'], 'falha')
        self.assertEqual(mis.estado_solver({'estado': 'ajustado_nao_identificavel'}, ok, 20)['estado'],
                         'executado_nao_identificavel')
        self.assertEqual(mis.estado_solver({'estado': 'ajustado_identificavel'}, ok, 20)['estado'], 'executado_ok')


@unittest.skipIf(shutil.which('node') is None, 'node indisponivel')
class Interface(unittest.TestCase):
    def js(self, expr, args):
        import tabs.tab_pmc as tp
        i0, i1 = tp.JS.index('// <<modelo-ir>>'), tp.JS.index('// <</modelo-ir>>')
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'b.js'), 'w', encoding='utf-8').write(tp.JS[i0:i1])
            runner = ("const vm=require('vm'),fs=require('fs');const sb={document:{getElementById:()=>({})},"
                      "fetch:()=>Promise.reject(new Error('x')),Array,Set,Math,JSON,String,Object,Number,Promise,Date,"
                      "isFinite,console,setTimeout,ctx:()=>null,noData(){},registarTip(){}};vm.createContext(sb);"
                      "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sb);"
                      "console.log(vm.runInContext(process.argv[3],sb)(JSON.parse(process.argv[4])));")
            open(os.path.join(d, 'r.js'), 'w').write(runner)
            r = subprocess.run(['node', os.path.join(d, 'r.js'), os.path.join(d, 'b.js'), expr, json.dumps(args)],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_diagnostico_com_intervalos_e_origem_do_yes(self):
        cv = serie('Bike', [300.0] * 5 + [290.0, 289.0])
        at = ativs(cv, {'Bike-a5': '["1x 3m 288w"]', 'Bike-a6': '["1x 5m 288w"]'})
        g = mm.p0_global({'Bike': list(avaliar(cv, at).values())})
        h = self.js('irP0GlobalHtml', {'p0_global': g})
        self.assertIn('intervalo_detectado', h)
        self.assertIn('Elegíveis automaticamente por intervalo (YES): <b>1</b>', h)
        self.assertIn('A verificar (repetições, pico noutro trecho ou intervalo longo): <b>1</b>', h)
        self.assertIn('Bike-a5', h)

    def test_sem_p0_explica_e_nao_inventa(self):
        cv = serie('Bike', [300.0] * 6)
        g = mm.p0_global({'Bike': list(avaliar(cv, ativs(cv)).values())})
        h = self.js('irP0GlobalHtml', {'p0_global': g})
        self.assertIn('Porque não existe YES', h)
        self.assertIn('não há fallback para CP, FTP/eFTP', h)

    def test_solver_texto(self):
        h = self.js('irSolverTxt', {'estado': 'nao_executado', 'motivo': 'sem_p0', 'n_observacoes_yes': 0, 'min_obs': 15,
                                    'faltam_observacoes': 15})
        self.assertIn('não executado', h)
        self.assertIn('sem P0', h)
        self.assertNotIn('falhou', h)
        self.assertIn('dados insuficientes', self.js('irSolverTxt', {'estado': 'dados_insuficientes', 'n_observacoes_yes': 3,
                                                                       'min_obs': 15, 'faltam_observacoes': 12}))
        self.assertIn('ajuste válido', self.js('irSolverTxt', {'estado': 'executado_ok', 'n_observacoes_yes': 20, 'min_obs': 15,
                                                                 'faltam_observacoes': 0}))


if __name__ == '__main__':
    unittest.main()
