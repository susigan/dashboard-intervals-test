"""Cadeia COMPLETA CP Model -> referencia de P0 -> ajuste IR, pelos pontos de integracao reais.

    python -m pytest tests/pmc/test_modelo_ir_p0_integracao.py

Decisao final: P0 = CP (exatamente o cp_w do resultado guardado). O CP Model e acionado pelo endpoint real POST /api/perfil/guardar (gravacao em cp_resultados, hook do IR,
recalibrar, leitura de cp_resultados pelo leitor REAL do servico, referencia, ajuste). Offline e sem producao:
  * a base de perfil e uma SQLite temporaria (esquema de teste de cp_resultados, o repo nao o define);
  * drive_db_perfil.get_conn aponta para ela e upload() e falso (nada vai ao Drive);
  * so o calculo numerico do CP (calcular_cp_completo) e substituido por um resultado sintetico, para controlar
    CP, W' e os pontos usados; a gravacao, a escolha do modelo e as durações passam pelo codigo real de api_cp.py.
"""
import copy
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
for _p in (HERE, ROOT, os.path.join(ROOT, 'utils')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cp_fixture as CPF  # noqa: E402
import modelo_ir as ir  # noqa: E402
import modelo_ir_p0 as p0m  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import test_modelo_ir_servico as S  # noqa: E402

db = S.db
CARREGAR_REAL = mis.carregar_instantaneos_cp
M1, M2, M3, P2, P3 = 'M1 (WLS-P)', 'M2 (WLS-1/t)', 'M3 (NL-2p)', '2p hiperbolico', '3p hiperbolico'

ESQUEMA_CP = """CREATE TABLE cp_resultados (
    id INTEGER PRIMARY KEY AUTOINCREMENT, data_referencia TEXT, data_gravacao TEXT, modalidade TEXT, season TEXT,
    modelo_escolhido TEXT, cp_w REAL, wp_j REAL, see_pct REAL, n_pts INTEGER, k_params INTEGER, pmax_w REAL,
    mmp60_validacao_w REAL, mmp_pts_json TEXT, modelos_json TEXT, veloclinic_json TEXT, origem TEXT, nota TEXT,
    UNIQUE (modalidade, season, data_referencia))"""


def modelo(cp=270.0, wp=24000.0, pontos=((300, 350.0), (720, 303.3), (1200, 290.0)), see=1.0, classico=True, k=2):
    return {'classico': classico, 'cp': round(cp, 1), 'wp': round(wp), 'wp_kj': round(wp / 1000.0, 2),
            'see_pct': see, 'n_pts': len(pontos), 'k_params': k,
            'pontos_usados': [{'w': w, 't': t} for t, w in pontos]}


def resultado_cp(escolhido=M1, **kw):
    """Saida sintetica de calcular_cp_completo: M1/M2/M3/2p com o mesmo CP e W' (a mesma hiperbole)."""
    m = modelo(**kw)
    modelos = {M1: dict(m), M2: dict(m, see_pct=m['see_pct'] + 0.3), M3: dict(m, see_pct=m['see_pct'] + 0.6),
               P2: dict(m, classico=False), P3: dict(m, classico=False, k_params=3)}
    return {'melhor': {'nome': M1}, 'modelos': modelos, 'pmax': None, 'mmp60_val': None, 'mmp_pts_full': [],
            'veloclinic': {}, 'escolhido_teste': escolhido}


class _Cadeia(S._Base):
    def setUp(self):
        super().setUp()
        self.cp_loader.stop()                                        # aqui usa-se o leitor REAL do servico
        import api_cp
        import cp_model as cpm
        import drive_db_perfil as ddp
        self.api_cp, self.cpm, self.ddp = api_cp, cpm, ddp
        self.tmp = tempfile.mkdtemp(prefix='perfil_teste_')
        self.perfil = os.path.join(self.tmp, 'perfil_historico.db')
        con = sqlite3.connect(self.perfil)
        con.execute(ESQUEMA_CP)
        for t in ('perfil_snapshots', 'limiares_snapshots'):          # o apagar do CP tambem as toca
            con.execute(f'CREATE TABLE {t} (id INTEGER PRIMARY KEY, modalidade TEXT, season TEXT, data_referencia TEXT)')
        con.commit()
        con.close()
        self.uploads = []
        self.res = resultado_cp()
        self.relogio = datetime(2026, 10, 17, 12, 0, 0)
        ps = [mock.patch.object(ddp, 'get_conn', side_effect=lambda: sqlite3.connect(self.perfil)),
              mock.patch.object(ddp, 'upload', side_effect=lambda: (self.uploads.append(1) or True, 'teste')),
              mock.patch.object(api_cp, '_registos', return_value=([{'x': 1}], None, [])),
              mock.patch.object(cpm, 'pontos_de_curvas', return_value={}),
              mock.patch.object(cpm, 'calcular_cp_completo', side_effect=lambda *a, **k: self.res),
              mock.patch.object(mis.mt, 'agora_utc', side_effect=lambda: self.relogio)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    # -- ajudantes --
    def guardar(self, mod='Bike', modelo_cp=M1, data_ref='2026-01-10', quais=('cp',), avanca_dias=1, **kw):
        """O mesmo POST que a interface do CP Model faz (tabs/tab_cp_model.py: cpGuardar)."""
        self.relogio += timedelta(days=avanca_dias)
        self.res = resultado_cp(**kw)
        r = self.c.post('/api/perfil/guardar', json={'modalidade': mod, 'season': '2026', 'data_referencia': data_ref,
                                                      'modelo_cp': modelo_cp, 'guardar': list(quais)})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def cp_linhas(self):
        con = sqlite3.connect(self.perfil)
        try:
            return con.execute('SELECT modalidade, data_referencia, modelo_escolhido, cp_w, wp_j FROM cp_resultados '
                               'ORDER BY id').fetchall()
        finally:
            con.close()

    def refs(self, mod='Bike', dur=1200):
        return db.referencias_p0_historico(mod, dur)

    def aj(self, dur=1200, mod='Bike'):
        return [x for x in db.ajustes_ir_historico(mod, dur)]


# ── a mudanca no CP chega ao P0 (= CP) e ao ajuste pelo fluxo real ─────────────────────────

class CadeiaCpParaIr(_Cadeia):
    def test_gravar_o_cp_propaga_ate_ao_ajuste_pelo_fluxo_real(self):
        self.prepara()
        j = self.guardar()
        self.assertEqual(j['status'], 'ok')
        self.assertEqual(j['escrito']['cp']['modelo'], M1)
        self.assertEqual(self.cp_linhas(), [('Bike', '2026-01-10', M1, 270.0, 24000)])
        ir_ = j['modelo_ir']
        self.assertTrue(ir_['ok'], ir_)
        u = ir_['unidades']['Bike:1200']
        self.assertEqual((u['acao'], u['motivo'], u['versao_referencia_p0']), ('ajustado', mis.MOTIVO_PRIMEIRO, 1))
        (ref,) = self.refs()
        self.assertEqual(ref['valor_p0'], 270.0)                         # P0 == CP, exatamente
        self.assertEqual(ref['modelo_cp'], M1)
        self.assertEqual(ref['cp_data_referencia'], '2026-01-10')
        self.assertTrue(ref['cp_resultado_ref']['id'])
        (l,) = self.aj()
        self.assertEqual(l['entradas']['p0_ref']['versao_referencia'], 1)
        self.assertEqual(l['entradas']['p0_ref']['cp_resultado_id'], ref['cp_resultado_ref']['id'])
        self.assertEqual(l['p0'], 270.0)                                 # P0 fixo: o Solver nao o mexeu
        self.assertEqual(l['identificabilidade']['parametros_livres'], ['k1', 'k2', 't1', 't2'])
        # todas as duracoes da modalidade partilham o mesmo P0 (a duracao identifica o alvo, nao o P0)
        for d in (60, 300, 1200):
            self.assertEqual(self.refs('Bike', d)[0]['valor_p0'], 270.0, d)
            self.assertEqual(len(self.aj(d)), 1, d)

    def test_cp_alterado_cria_referencia_nova_aciona_o_solver_e_preserva_o_anterior(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10')
        r1, a1 = copy.deepcopy(self.refs()), copy.deepcopy(self.aj())
        chamadas = []
        real = ir.ajustar_observacoes

        def espia(*a, **k):
            chamadas.append(k.get('p0_fixo'))
            return real(*a, **k)
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=espia):
            j = self.guardar(data_ref='2026-03-01', cp=272.0)
        u = j['modelo_ir']['unidades']['Bike:1200']
        self.assertEqual((u['acao'], u['motivo']), ('ajustado', mis.MOTIVO_CP))
        self.assertIn(272.0, chamadas)                                   # o Solver correu com P0 FIXO = novo CP
        self.assertNotIn(None, chamadas)
        v1, v2 = self.refs()
        self.assertEqual(v1, r1[0])                                      # versao 1 intacta, datas de conhecimento incluidas
        self.assertEqual((v2['versao_referencia'], v2['valor_p0']), (2, 272.0))
        self.assertGreater(v2['conhecida_desde'], v1['conhecida_desde'])
        l1, l2 = self.aj()
        self.assertEqual(l1, a1[0])                                      # o ajuste antigo nao foi tocado
        self.assertEqual((l2['versao_ajuste'], l2['entradas']['p0_ref']['versao_referencia']), (2, 2))
        self.assertEqual(l2['p0'], 272.0)
        self.assertEqual(l2['identificabilidade']['parametros_livres'], ['k1', 'k2', 't1', 't2'])

    def test_o_solver_estima_k1_k2_t1_t2_e_nunca_o_p0(self):
        self.prepara()
        self.guardar(cp=281.7)
        (l,) = self.aj()
        self.assertEqual(l['p0'], 281.7)
        for k in ('k1', 'k2', 't1', 't2'):
            self.assertIsNotNone(l[k], k)
        self.assertTrue(l['identificabilidade']['p0_fixo'])
        self.assertEqual(l['configuracao']['p0']['parametros_estimados'], ['k1', 'k2', 't1', 't2'])
        self.assertNotIn('p0', l['identificabilidade']['parametros_livres'])

    def test_o_p0_segue_o_modelo_gravado_e_nao_o_de_menor_see(self):
        self.prepara()
        res = resultado_cp()
        res['modelos'][M2] = modelo(cp=262.0, wp=29000.0, see=0.1)       # M2 teria o menor SEE
        self.relogio += timedelta(days=1)
        self.res = res
        j = self.c.post('/api/perfil/guardar', json={'modalidade': 'Bike', 'season': '2026', 'data_referencia': '2026-01-10',
                                                     'modelo_cp': M1, 'guardar': ['cp']}).get_json()
        self.assertEqual(j['escrito']['cp']['modelo'], M1)
        (ref,) = self.refs()
        self.assertEqual((ref['modelo_cp'], ref['valor_p0']), (M1, 270.0))   # M1 (escolhido), nunca M2 (262)
        self.guardar(data_ref='2026-02-01', modelo_cp=M3, cp=271.0)
        self.assertEqual((self.refs()[-1]['modelo_cp'], self.refs()[-1]['valor_p0']), (M3, 271.0))

    def test_escolha_m1_m2_m3_e_2p_continua_a_funcionar_e_cada_uma_da_a_sua_origem(self):
        self.prepara()
        for k, nome in enumerate((M1, M2, M3, P2)):
            j = self.guardar(data_ref=f'2026-0{k + 1}-10', modelo_cp=nome, cp=270.0 + k, wp=24000.0)
            self.assertEqual(j['escrito']['cp']['modelo'], nome)
            self.assertEqual(self.refs()[-1]['modelo_cp'], nome)
            self.assertEqual(self.refs()[-1]['valor_p0'], 270.0 + k)
        self.assertEqual([r[2] for r in self.cp_linhas()], [M1, M2, M3, P2])

    def test_modelo_de_3_parametros_e_aceite_com_aviso_e_p0_igual_ao_seu_cp(self):
        self.prepara()
        j = self.guardar(modelo_cp=P3, cp=280.0, pontos=((180, 400.0), (300, 350.0), (720, 303.3), (1200, 290.0)))
        self.assertEqual(j['modelo_ir']['unidades']['Bike:1200']['acao'], 'ajustado')
        ref = self.refs()[-1]
        self.assertEqual((ref['modelo_cp'], ref['valor_p0']), (P3, 280.0))
        self.assertIn('modelo_de_3_parametros', ref['parametros']['avisos_cp'])


# ── W' nao recalibra; revisao anual recalibra ───────────────────────────────────────────────

class WprimeERevisaoAnual(_Cadeia):
    def test_alterar_so_o_wprime_nao_cria_referencia_nem_aciona_o_solver(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10', wp=24000.0)
        n_ref, n_aj = len(self.refs()), len(self.aj())
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('W-only nao recalibra')):
            j = self.guardar(data_ref='2026-01-10', wp=19000.0)          # mesma chave: substitui a linha do CP
        self.assertEqual(j['modelo_ir']['n_ajustadas'], 0)
        self.assertEqual(self.cp_linhas()[-1][3:], (270.0, 19000))       # o CP gravou o W' novo
        self.assertEqual((len(self.refs()), len(self.aj())), (n_ref, n_aj))
        self.assertEqual(self.refs()[-1]['valor_p0'], 270.0)

    def test_revisao_anual_com_o_mesmo_cp_cria_referencia_nova_e_reajusta(self):
        self.prepara()
        self.guardar(data_ref='2025-01-10')
        a1 = copy.deepcopy(self.aj())
        j = self.guardar(data_ref='2026-01-10', avanca_dias=300)         # nova gravacao anual, mesmos valores
        u = j['modelo_ir']['unidades']['Bike:1200']
        self.assertEqual((u['acao'], u['motivo']), ('ajustado', mis.MOTIVO_REF_P0))
        v1, v2 = self.refs()
        self.assertEqual((v1['valor_p0'], v2['valor_p0']), (270.0, 270.0))
        self.assertEqual((v1['cp_data_referencia'], v2['cp_data_referencia']), ('2025-01-10', '2026-01-10'))
        l1, l2 = self.aj()
        self.assertEqual(l1, a1[0])
        self.assertEqual(l2['entradas']['p0_ref']['versao_referencia'], 2)

    def test_revisao_anual_com_cp_novo_usa_o_cp_novo(self):
        self.prepara()
        self.guardar(data_ref='2025-01-10')
        j = self.guardar(data_ref='2026-01-10', cp=262.5, avanca_dias=300)
        self.assertEqual(j['modelo_ir']['unidades']['Bike:1200']['motivo'], mis.MOTIVO_CP)
        self.assertEqual(self.refs()[-1]['valor_p0'], 262.5)
        self.assertEqual(self.aj()[-1]['p0'], 262.5)

    def test_revisao_anual_invalida_bloqueia_e_mantem_a_anterior(self):
        self.prepara()
        self.guardar(data_ref='2025-01-10')
        antes = (copy.deepcopy(self.refs()), copy.deepcopy(self.aj()))
        j = self.guardar(data_ref='2026-01-10', cp=-4.0, avanca_dias=300)
        u = j['modelo_ir']['unidades']['Bike:1200']
        self.assertEqual(u['acao'], mis.ACAO_BLOQUEADO)
        self.assertEqual(u['motivo'], p0m.M_CP)
        self.assertEqual((self.refs(), self.aj()), antes)


# ── idempotencia ───────────────────────────────────────────────────────────────────────

class Idempotencia(_Cadeia):
    def test_regravar_o_mesmo_resultado_nao_cria_versoes(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10')
        n_ref, n_aj = len(self.refs()), len(self.aj())
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=AssertionError('recalculou sem mudanca')):
            j = self.guardar(data_ref='2026-01-10')                      # mesma chave
        self.assertEqual(j['modelo_ir']['n_ajustadas'], 0)
        self.assertEqual((len(self.refs()), len(self.aj())), (n_ref, n_aj))
        self.assertEqual(j['modelo_ir']['unidades']['Bike:1200']['acao'], 'inalterado')

    def test_o_sync_e_a_recalibracao_manual_seguintes_tambem_nao_alteram_nada(self):
        self.prepara()
        self.guardar()
        antes = (copy.deepcopy(self.refs()), copy.deepcopy(db.ajustes_ir_historico()))
        out = mis.apos_deteccao({'ok': True}, origem='sync')
        self.assertEqual(out['n_ajustadas'], 0)
        with CPF.patch_loader(), mock.patch.object(mis, 'carregar_instantaneos_cp', CARREGAR_REAL):
            r = self.c.post('/api/modelo/ir/recalibrar', json={}).get_json()
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertEqual((self.refs(), db.ajustes_ir_historico()), antes)

    def test_gravar_so_perfil_ou_limiares_nao_toca_no_ir(self):
        with mock.patch.object(mis, 'recalibrar', side_effect=AssertionError('nao devia recalibrar')):
            self.assertEqual(mis.apos_cp_gravado({'escrito': {'perfil': {}}}, 'Bike')['ignorado'], 'cp_nao_gravado')
            self.assertEqual(mis.apos_cp_gravado({'escrito': {}}, 'Bike')['ignorado'], 'cp_nao_gravado')
            self.assertEqual(mis.apos_cp_gravado(None, 'Bike')['ignorado'], 'cp_nao_gravado')
            self.assertEqual(mis.apos_cp_gravado({'escrito': {'cp': {'modelo': M1}}}, 'Swim')['ignorado'],
                             'modalidade_sem_ir')

    def test_recalibracao_manual_apos_cp_novo_gravado_sem_hook_cria_a_versao(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10')
        with mock.patch.object(mis, 'apos_cp_gravado', return_value={'ignorado': 'teste'}):
            self.guardar(data_ref='2026-03-01', cp=275.0)                 # gravado sem passar pelo hook
        self.assertEqual(len(self.refs()), 1)
        r = mis.recalibrar(origem='manual')
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], mis.MOTIVO_CP)
        self.assertEqual(self.refs()[-1]['valor_p0'], 275.0)


# ── modalidades e duracoes independentes ───────────────────────────────────────────────────

class Independencia(_Cadeia):
    def test_gravar_outra_modalidade_nao_mexe_nas_referencias_da_primeira(self):
        self.prepara()
        self.guardar('Bike')
        antes = copy.deepcopy(self.refs('Bike', 1200)), copy.deepcopy(self.aj())
        j = self.guardar('Run', data_ref='2026-02-02', cp=250.0, wp=20000.0)
        self.assertTrue(all(k.startswith('Run:') for k in j['modelo_ir']['unidades']))
        self.assertEqual((self.refs('Bike', 1200), self.aj()), antes)
        self.assertEqual(db.referencias_p0_historico('Run'), [])        # Run sem observacoes no teste: nada a ajustar

    def test_a_duracao_nao_altera_o_p0_e_as_tres_ajustam_como_antes(self):
        self.prepara()
        j = self.guardar(data_ref='2026-01-10', pontos=((720, 303.0), (900, 295.0), (1200, 290.0)))    # sem pontos a 60/300 s
        for d in (60, 300, 1200):
            self.assertEqual(j['modelo_ir']['unidades'][f'Bike:{d}']['acao'], 'ajustado', d)
            self.assertEqual(self.refs('Bike', d)[-1]['valor_p0'], 270.0, d)
        self.assertEqual({self.aj(d)[-1]['p0'] for d in (60, 300, 1200)}, {270.0})

    def test_mudar_o_cp_reajusta_todas_as_duracoes_da_modalidade(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10')
        self.guardar(data_ref='2026-02-10', cp=268.0)
        for d in (60, 300, 1200):
            self.assertEqual([r['valor_p0'] for r in self.refs('Bike', d)], [270.0, 268.0], d)
            self.assertEqual(len(self.aj(d)), 2, d)


# ── CP invalido / Solver que falha ────────────────────────────────────────────────────────

class BloqueiosEFalhas(_Cadeia):
    def estado(self):
        return copy.deepcopy(self.refs()), copy.deepcopy(db.ajustes_ir_historico())

    def test_cp_invalido_bloqueia_sem_destruir_nada(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10')
        antes = self.estado()
        for k, cp in enumerate((-3.0, 0.0)):
            j = self.guardar(data_ref=f'2026-0{k + 2}-20', cp=cp)
            u = j['modelo_ir']['unidades']['Bike:1200']
            self.assertEqual((u['acao'], u['motivo']), (mis.ACAO_BLOQUEADO, p0m.M_CP), cp)
            self.assertEqual(u['ultimo_ajuste']['versao_ajuste'], 1)
            self.assertEqual(self.estado(), antes, cp)

    def test_sem_graus_de_liberdade_no_cp_bloqueia(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10')
        antes = self.estado()
        j = self.guardar(data_ref='2026-03-10', cp=280.0, pontos=((300, 350.0), (1200, 290.0)))   # 2 pontos, k=2
        u = j['modelo_ir']['unidades']['Bike:1200']
        self.assertEqual((u['acao'], u['motivo']), (mis.ACAO_BLOQUEADO, p0m.M_GL))
        self.assertEqual(self.estado(), antes)

    def test_solver_que_falha_preserva_o_ultimo_ajuste_valido_e_assinala_desatualizado(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10', cp=270.0)
        (a1,) = copy.deepcopy(self.aj())
        with mock.patch.object(ir, 'ajustar_observacoes', side_effect=RuntimeError('solver rebentou')):
            j = self.guardar(data_ref='2026-03-01', cp=280.0)
        u = j['modelo_ir']['unidades']['Bike:1200']
        self.assertEqual(u['acao'], mis.ACAO_FALHOU)
        self.assertIn('solver rebentou', str(u))
        v1, v2 = self.refs()                                              # a referencia nova FICA (e o CP tambem)
        self.assertEqual((v2['valor_p0'], self.cp_linhas()[-1][3]), (280.0, 280.0))
        linhas = self.aj()
        self.assertEqual(linhas[0], a1)                                   # o ultimo ajuste valido nao foi tocado
        self.assertNotIn(linhas[-1]['estado'], ('identificavel', 'nao_identificavel')) if len(linhas) > 1 else None
        # o estado visivel: P0 novo (280) NAO e apresentado com os parametros antigos como se fossem recalibrados
        c = self.c.get('/api/modelo/ir/conjunto?duracao=1200').get_json()['modalidades']['Bike']
        self.assertEqual(c['estado'], 'sem_convergencia')
        self.assertFalse(c['tem_curva'])                                  # nenhuma curva com parametros antigos + P0 novo
        self.assertIsNone(c['parametros'])
        self.assertIn('solver rebentou', c['excecao_solver'])
        self.assertTrue(c['p0_desatualizado'])
        self.assertTrue(c['p0']['desatualizado'])
        self.assertEqual(c['p0']['atual']['valor_p0'], 280.0)
        self.assertEqual(c['ajuste_valido_anterior']['p0'], 270.0)        # o ultimo valido continua identificado
        self.assertEqual(c['ajuste_valido_anterior']['versao_ajuste'], 1)
        # a passagem seguinte recupera sem intervencao
        r = self.rec()
        self.assertEqual(r['unidades']['Bike:1200']['acao'], 'ajustado')
        self.assertEqual(self.aj()[-1]['p0'], 280.0)
        self.assertEqual(self.aj()[0], a1)

    def test_falha_entre_a_referencia_e_o_ajuste_recupera_na_passagem_seguinte(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10', cp=270.0)
        (a1,) = copy.deepcopy(self.aj())
        real = db.inserir_ajuste_ir

        def falha_em_1200(linha, *a, **k):
            if linha['duracao_s'] == 1200:
                raise RuntimeError('disco cheio')
            return real(linha, *a, **k)
        with mock.patch.object(db, 'inserir_ajuste_ir', side_effect=falha_em_1200):
            self.guardar(data_ref='2026-03-01', cp=285.0)
        self.assertEqual([r['valor_p0'] for r in self.refs()], [270.0, 285.0])   # referencia gravada, ajuste nao
        self.assertEqual(self.aj(), [a1])
        r = self.rec()                                                    # a assinatura difere: refaz
        self.assertEqual(r['unidades']['Bike:1200']['acao'], 'ajustado')
        self.assertEqual(self.aj()[-1]['p0'], 285.0)
        self.assertEqual(len(self.refs()), 2)                              # sem referencia duplicada
        self.assertEqual(self.aj()[0], a1)

    def test_sem_nenhum_resultado_de_cp_nao_ha_ajuste(self):
        self.prepara()
        r = self.rec()
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertEqual(r['unidades']['Bike:1200']['motivo'], p0m.M_SEM_INSTANTANEO)
        self.assertEqual(db.ajustes_ir_historico(), [])
        self.assertEqual(db.referencias_p0_historico(), [])

    def test_base_de_perfil_ilegivel_bloqueia_com_o_motivo(self):
        self.prepara()
        self.ddp.get_conn.side_effect = RuntimeError('drive em baixo')
        r = self.rec()
        self.assertEqual(r['n_ajustadas'], 0)
        self.assertIn('drive em baixo', r['unidades']['Bike:1200']['erro_leitura_cp'])
        self.assertEqual(db.ajustes_ir_historico(), [])

    def test_falha_do_ir_nao_estraga_a_gravacao_do_cp(self):
        self.prepara()
        with mock.patch.object(mis, 'recalibrar', side_effect=RuntimeError('boom')):
            j = self.guardar()
        self.assertEqual(j['status'], 'ok')
        self.assertFalse(j['modelo_ir']['ok'])
        self.assertIn('boom', j['modelo_ir']['erro'])
        self.assertEqual(self.cp_linhas()[-1][:3], ('Bike', '2026-01-10', M1))
        self.assertTrue(self.uploads)
        r = self.rec()
        self.assertEqual(r['unidades']['Bike:1200']['acao'], 'ajustado')

    def test_falha_a_importar_ou_a_correr_o_ir_nao_da_500(self):
        self.prepara()
        with mock.patch.object(mis, 'apos_cp_gravado', side_effect=ImportError('sem ir')):
            j = self.guardar()
        self.assertEqual(j['status'], 'ok')
        self.assertIn('sem ir', j['modelo_ir']['erro'])


# ── caminhos que NAO disparam ─────────────────────────────────────────────────────────────

class CaminhosSemGatilho(_Cadeia):
    def test_apagar_um_resultado_de_cp_nao_recalibra_na_hora_e_nao_apaga_historico(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10', cp=270.0)
        self.guardar(data_ref='2026-03-01', cp=272.0)
        antes = (copy.deepcopy(self.refs()), copy.deepcopy(self.aj()))
        self.assertEqual(len(antes[0]), 2)
        with mock.patch.object(mis, 'recalibrar', side_effect=AssertionError('apagar nao dispara o IR')):
            r = self.c.post('/api/perfil/apagar', json={'modalidade': 'Bike', 'season': '2026',
                                                         'data_referencia': '2026-03-01'})
        self.assertEqual(r.get_json()['apagadas']['cp_resultados'], 1)
        self.assertEqual((self.refs(), self.aj()), antes)
        self.relogio += timedelta(days=2)
        out = self.rec()
        self.assertEqual(out['unidades']['Bike:1200']['acao'], 'ajustado')
        v = self.refs()
        self.assertEqual([x['versao_referencia'] for x in v], [1, 2, 3])
        self.assertEqual(v[:2], antes[0])
        self.assertEqual(v[2]['valor_p0'], v[0]['valor_p0'])

    def test_gravar_com_data_de_referencia_mais_antiga_nao_substitui_o_vigente(self):
        self.prepara()
        self.guardar(data_ref='2026-03-01', cp=272.0)
        n = len(self.refs())
        j = self.guardar(data_ref='2025-06-01', cp=255.0)
        self.assertEqual(j['modelo_ir']['n_ajustadas'], 0)
        self.assertEqual(len(self.refs()), n)

    def test_o_calculo_agora_do_cp_sem_gravar_nao_e_resultado_confirmado(self):
        self.prepara()
        r = self.c.get('/api/cp/actual/Bike')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.cp_linhas(), [])
        out = self.rec()
        self.assertEqual(out['n_ajustadas'], 0)
        self.assertEqual(db.referencias_p0_historico(), [])


# ── causalidade ─────────────────────────────────────────────────────────────────────────────

class Causalidade(_Cadeia):
    def test_referencias_posteriores_nao_entram_em_previsoes_anteriores(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10', cp=270.0)
        self.guardar(data_ref='2026-02-10', cp=272.0, avanca_dias=40)
        refs = self.refs()
        f = mis.p0_conhecido_antes
        self.assertIsNone(f(refs, refs[0]['conhecida_desde'][:10]))
        self.assertEqual(f(refs, '2026-11-01'), 270.0)
        self.assertEqual(f(refs, '2026-12-31'), 272.0)
        self.assertIsNone(f(refs, '2026-01-10'))                         # a data do instantaneo NAO e a de conhecimento
        j = mis.validar_preditivo(db.observacoes_auto_historico(), db.revisoes_testes_modelo(), self.ativ,
                                  db.load_power_curves() or [], modos=('retrospectivo', 'conhecido_na_epoca'),
                                  tipo='Bike', duracao=1200)
        for it in j['itens']:
            self.assertEqual(it['n_alvos_com_previsao_ir'], 0, it['modo'])
        retro = next(i for i in j['itens'] if i['modo'] == 'retrospectivo')
        self.assertIn('sem_referencia_p0_conhecida', retro['alvos_sem_previsao_ir'])

    def test_conhecida_desde_nunca_e_reescrita_numa_nova_versao(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10')
        t1 = self.refs()[0]['conhecida_desde']
        self.guardar(data_ref='2026-02-10', cp=272.0, avanca_dias=30)
        self.rec(forcar=True)
        self.assertEqual(self.refs()[0]['conhecida_desde'], t1)


# ── o que a interface recebe ─────────────────────────────────────────────────────────────────

class InterfaceRecebeAOrigem(_Cadeia):
    def test_a_resposta_do_conjunto_traz_origem_versao_e_bloqueio_pela_cadeia_real(self):
        self.prepara()
        self.guardar(data_ref='2026-01-10')
        j = self.c.get('/api/modelo/ir/conjunto?duracao=1200').get_json()
        bike = j['modalidades']['Bike']['p0']
        self.assertEqual(bike['origem_ajuste'], 'cp_model_confirmado')
        u = bike['usado_no_ajuste']
        self.assertEqual((u['versao_referencia'], u['modelo_cp'], u['cp_data_referencia']), (1, M1, '2026-01-10'))
        self.assertEqual(u['valor_p0'], 270.0)
        self.assertTrue(u['conhecida_desde'])
        self.assertTrue(bike['atual']['valida'])
        for m in ('Row', 'Run', 'Ski'):
            self.assertFalse(j['modalidades'][m]['p0']['atual']['valida'])
            self.assertEqual(j['modalidades'][m]['p0']['atual']['motivo'], p0m.M_SEM_INSTANTANEO)
        self.assertFalse(j['overall']['disponivel'])
        # depois de um bloqueio a interface continua a mostrar o ajuste anterior e diz porque nao atualizou
        self.guardar(data_ref='2026-03-10', cp=-1.0)
        b2 = self.c.get('/api/modelo/ir/conjunto?duracao=1200').get_json()['modalidades']['Bike']['p0']
        self.assertEqual(b2['usado_no_ajuste']['versao_referencia'], 1)
        self.assertFalse(b2['atual']['valida'])
        self.assertEqual(b2['atual']['motivo'], p0m.M_CP)


# ── as 12 combinacoes ───────────────────────────────────────────────────────────────────────

class DozeCombinacoes(unittest.TestCase):
    def test_as_12_combinacoes_tem_p0_igual_ao_cp_da_modalidade(self):
        snaps = [CPF.instantaneo(m, cp=cp) for m, cp in (('Bike', 290.0), ('Run', 330.0), ('Row', 210.0), ('Ski', 180.0))]
        esperado = {'Bike': 290.0, 'Run': 330.0, 'Row': 210.0, 'Ski': 180.0}
        n = 0
        for mod, cp in esperado.items():
            for dur in (60, 300, 1200):
                r = p0m.resolver_p0(mod, dur, snaps)
                self.assertTrue(r['valida'], (mod, dur, r.get('motivo')))
                self.assertEqual(r['valor_p0'], cp, (mod, dur))
                n += 1
        self.assertEqual(n, 12)


if __name__ == '__main__':
    unittest.main()
