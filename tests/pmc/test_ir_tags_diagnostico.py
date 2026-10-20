"""Normalizacao das tags das atividades (lista, texto JSON, texto com virgulas) e diagnostico da ausencia de YES.

Nao altera a regra: a tag de 3 min continua obrigatoria, 0,92 inalterado. Sem rede nem producao.
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

import modelo_ir_servico as mis  # noqa: E402
import modelo_mmp3 as mm  # noqa: E402
import modelo_observacoes_auto as moa  # noqa: E402
import test_ir_mmp3 as M  # noqa: E402


def tg(v):
    return moa.tags_da_atividade({'tags': v})


class ParserDeTags(unittest.TestCase):
    def test_formas_equivalentes(self):
        esperado = ['3min', 'Moxy']
        self.assertEqual(tg(['3min', 'Moxy']), esperado)                 # lista nativa
        self.assertEqual(tg('["3min", "Moxy"]'), esperado)               # texto JSON
        self.assertEqual(tg('  ["3min","Moxy"]  '), esperado)
        self.assertEqual(tg('3min, Moxy'), esperado)                     # separado por virgulas
        self.assertEqual(tg(('3min', 'Moxy')), esperado)

    def test_vazio_nulo_e_invalido(self):
        for v in (None, '', '   ', [], '[]', 5, True, {}):
            self.assertEqual(tg(v), [], repr(v))
        self.assertEqual(moa.tags_da_atividade(None), [])
        self.assertEqual(moa.tags_da_atividade({}), [])
        # JSON invalido: sem excecao, comportamento anterior (virgulas), nada adivinhado -> nao e tag de teste
        for v in ('[bad', '["3min"', '{"a": 1}'):
            self.assertIsInstance(tg(v), list)
            self.assertEqual(moa.tags_de_teste(tg(v)), [], v)
        self.assertEqual(tg('["3min", null, {"x": 1}, true, 7]'), ['3min', '7'])   # so escalares de texto/numero

    def test_reconhece_3min_e_nao_ha_falsos_positivos(self):
        for v in ('["3min"]', ['3min'], '3min', '["3 min"]', '["180s"]', '["3MIN"]', ' 3min '):
            self.assertTrue(mm.tem_tag_3min(tg(v)), v)
        for v in ('["5min"]', '["12min"]', '["20min"]', '["2min"]', '["30min"]', '["1km"]', '["Moxy"]', '["VST"]',
                  '["VTest"]', '["Moxy","VTest"]', '["13min"]', '["3km"]', '["3m"]', '["3 minutos"]', '["teste 3min"]',
                  None, '', '[bad'):
            self.assertFalse(mm.tem_tag_3min(tg(v)), v)                  # 3m = 3 metros; variantes nao sao assumidas

    def test_tags_existentes_continuam_a_ser_lidas_como_antes(self):
        self.assertEqual(tg(['Moxy']), ['Moxy'])
        self.assertEqual(tg('["Moxy","VTest"]'), ['Moxy', 'VTest'])
        self.assertEqual(moa.tags_de_teste(tg('["Moxy","5min","2km"]'))[0]['segundos'], 300.0)
        self.assertEqual(moa.tags_de_teste(tg(['VST'])), [])

    def test_sem_criacao_automatica_de_tag(self):
        # um 180 s forte e recorde, mas sem tag: continua a nao ser teste
        cv = [M.c180(f'Bike-{i}', 'Bike', f'2025-{i + 1:02d}-10', w) for i, w in enumerate([300.0] * 5 + [400.0])]
        at = [{'id': f'Bike-{i}', 'type': 'Ride', 'start_date_local': f'2025-{i + 1:02d}-10T08:00:00',
               'icu_ignore_power': False, 'tags': None} for i in range(6)]
        e = {x['activity_id']: x for x in mm.avaliar_esforcos(cv, 'Bike', '2026-01-01', (), M.ELEG,
                                                              tags=mis._tags_por_atividade(at))}
        # regra 5: 400 W e recorde (133 %) sem tag: candidato A_VERIFICAR, nunca YES automatico
        self.assertEqual((e['Bike-5']['estado'], e['Bike-5']['motivo']), (mm.E_A_VERIFICAR, mm.M_SEM_SINAL))


class TagEmTextoJsonChegaAoYes(unittest.TestCase):
    def test_atividade_com_tags_json_text_e_yes_e_a_mesma_sem_tag_nao(self):
        vals = [300.0] * 5 + [290.0]
        cv = [M.c180(f'Bike-{i}', 'Bike', f'2025-{i + 1:02d}-10', w) for i, w in enumerate(vals)]

        def ativ(tags):
            return [{'id': f'Bike-{i}', 'type': 'Ride', 'start_date_local': f'2025-{i + 1:02d}-10T08:00:00',
                     'icu_ignore_power': False, 'tags': tags if i == 5 else None} for i in range(6)]
        def estado(tags):
            at = ativ(tags)
            return {e['activity_id']: e for e in mm.avaliar_esforcos(
                cv, 'Bike', '2026-01-01', (), M.ELEG, tags=mis._tags_por_atividade(at))}['Bike-5']
        for formato in (['3min'], '["3min"]', '3min'):
            self.assertEqual(estado(formato)['estado'], mm.E_ELEGIVEL, formato)
        for formato in (None, '["5min"]', '["Moxy"]'):
            # sem sinal de 3 min: MMP3 96,7 % e candidato A_VERIFICAR (nunca YES automatico)
            self.assertEqual(estado(formato)['estado'], mm.E_A_VERIFICAR, formato)
        self.assertEqual(estado('["3min"]')['pct_do_melhor'], 96.7)      # 290 / 300: regra e limiar inalterados


class DiagnosticoSemYes(unittest.TestCase):
    def esforcos(self, tags_ok):
        vals = [300.0] * 5 + [290.0, 200.0]
        cv = [M.c180(f'Bike-{i}', 'Bike', f'2025-{i + 1:02d}-10', w) for i, w in enumerate(vals)]
        tags = {f'Bike-{i}': (['3min'] if tags_ok else []) for i in range(len(vals))}
        return {'Bike': mm.avaliar_esforcos(cv, 'Bike', '2026-01-01', (), M.ELEG, tags=tags)}

    def test_sem_tags_explica_a_ausencia(self):
        g = mm.p0_global(self.esforcos(False))
        d = g['diagnostico']
        self.assertIsNone(g['registo'])
        self.assertEqual(d['motivo_sem_yes'], 'nenhuma_atividade_com_tag_3min')
        t = d['total']
        self.assertEqual((t['n_180'], t['n_tag_3min'], t['n_sem_tag'], t['n_yes']), (7, 0, 7, 0))
        self.assertEqual(t['n_proximidade_ok_sem_outros'], 5)            # a1..a4 (300) e a5 (290) cumprem a proximidade
        self.assertEqual(d['por_modalidade']['Bike']['n_180'], 7)

    def test_com_tags_ha_yes_e_sem_motivo_de_ausencia(self):
        g = mm.p0_global(self.esforcos(True))
        self.assertEqual(g['estado'], 'valida')
        self.assertIsNone(g['diagnostico']['motivo_sem_yes'])
        self.assertGreater(g['diagnostico']['total']['n_yes'], 0)
        self.assertEqual(g['diagnostico']['total']['n_abaixo_limiar'], 1)  # o de 200 W

    def test_tag_mas_nenhum_cumpre(self):
        cv = [M.c180(f'Bike-{i}', 'Bike', f'2025-{i + 1:02d}-10', w) for i, w in enumerate([400.0] * 5 + [200.0])]
        tags = {'Bike-5': ['3min']}
        esf = {'Bike': mm.avaliar_esforcos(cv, 'Bike', '2026-01-01', (), M.ELEG,
                                           tags={**tags, **{f'Bike-{i}': [] for i in range(5)}})}
        d = mm.p0_global(esf)['diagnostico']
        self.assertEqual(d['motivo_sem_yes'], 'nenhum_esforco_cumpre_todos_os_criterios')
        self.assertEqual(d['total']['n_abaixo_limiar'], 1)

    def test_ignorada_e_desconhecida_e_sem_esforcos(self):
        cv = [M.c180(f'Bike-{i}', 'Bike', f'2025-{i + 1:02d}-10', 300.0) for i in range(6)]
        tags = {f'Bike-{i}': ['3min'] for i in range(6)}
        eleg = lambda a: 'inelegivel' if a == 'Bike-5' else ('desconhecida' if a == 'Bike-4' else 'elegivel')  # noqa: E731
        d = mm.p0_global({'Bike': mm.avaliar_esforcos(cv, 'Bike', '2026-01-01', (), eleg, tags=tags)})['diagnostico']['total']
        self.assertEqual((d['n_potencia_ignorada'], d['n_elegibilidade_desconhecida']), (1, 1))
        self.assertEqual(mm.p0_global({'Bike': []})['diagnostico']['motivo_sem_yes'], 'sem_esforcos_180s')

    def test_o_diagnostico_nao_altera_o_resto_do_resultado(self):
        g = mm.p0_global(self.esforcos(True))
        sem = {k: v for k, v in g.items() if k != 'diagnostico'}
        self.assertEqual(set(sem), {'origem', 'duracao_s', 'versao_regra', 'n_yes', 'n_esforcos', 'n_dados_insuficientes',
                                    'estado', 'registo', 'n_yes_historicos'})   # so `diagnostico` (+ contagem de YES historicos) foi acrescentado
        self.assertEqual(g['versao_regra'], 5)                           # regra 5: identificacao sem tag, confirmacao manual


@unittest.skipIf(shutil.which('node') is None, 'node indisponivel')
class DiagnosticoNaInterface(unittest.TestCase):
    def render(self, g):
        import tabs.tab_pmc as tp
        i0, i1 = tp.JS.index('// <<modelo-ir>>'), tp.JS.index('// <</modelo-ir>>')
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'b.js'), 'w', encoding='utf-8').write(tp.JS[i0:i1])
            runner = ("const vm=require('vm'),fs=require('fs');const sb={document:{getElementById:()=>({})},"
                      "fetch:()=>Promise.reject(new Error('x')),Array,Set,Math,JSON,String,Object,Number,Promise,Date,"
                      "isFinite,console,setTimeout,ctx:()=>null,noData(){},registarTip(){}};vm.createContext(sb);"
                      "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sb);"
                      "console.log(vm.runInContext('irP0GlobalHtml',sb)({p0_global:JSON.parse(process.argv[3])}));")
            open(os.path.join(d, 'r.js'), 'w').write(runner)
            r = subprocess.run(['node', os.path.join(d, 'r.js'), os.path.join(d, 'b.js'), json.dumps(g)],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_mostra_as_contagens_do_servico_quando_nao_ha_p0(self):
        esf = {'Bike': mm.avaliar_esforcos(
            [M.c180(f'Bike-{i}', 'Bike', f'2025-{i + 1:02d}-10', 300.0) for i in range(6)], 'Bike', '2026-01-01', (), M.ELEG,
            tags={})}
        g = mm.p0_global(esf)
        h = self.render(g)
        self.assertIn('Porque não existe YES', h)
        self.assertIn('nenhuma atividade tem tag de 3 min nem um intervalo de 3 min', h)
        self.assertIn('Atividades com potência válida de 180 s: <b>6</b>', h)
        self.assertIn('Com tag 3min reconhecida: <b>0</b>', h)
        self.assertIn('Excluídas por ausência de sinal de teste (sem tag): <b>6</b>', h)
        self.assertIn('não há fallback para CP, FTP/eFTP', h)

    def test_com_p0_nao_mostra_o_diagnostico(self):
        vals = [300.0] * 5 + [290.0]
        cv = [M.c180(f'Bike-{i}', 'Bike', f'2025-{i + 1:02d}-10', w) for i, w in enumerate(vals)]
        g = mm.p0_global({'Bike': mm.avaliar_esforcos(cv, 'Bike', '2026-01-01', (), M.ELEG, tags=M.TAGS)})
        self.assertEqual(g['estado'], 'valida')
        self.assertNotIn('Porque não existe YES', self.render(g))


if __name__ == '__main__':
    unittest.main()
