"""Validacao preditiva: M1 no mesmo dia, orientacao de dados por unidade e painel na aba PMC.

    python -m pytest tests/pmc/test_validacao_orientacao.py

Offline. Dados SINTETICOS: testam o codigo, nao o atleta nem o modelo. Cobrem:
  - M1 ("ultimo valor") quando o dia anterior tem varias observacoes sem ordem provada: o
    activity_id NAO decide (seria simular uma sequencia que os dados nao provam);
  - orientacao_dados: amostra insuficiente, sem alvos, avaliavel inconclusivo/melhor/pior, sem
    alterar nenhum criterio de suficiencia;
  - integracao com o endpoint e com o painel da aba PMC (JS real, resposta real do endpoint).
A ausencia de fuga temporal (futuro, proprio alvo, q aninhado, conhecido_na_epoca) ja esta
coberta por test_fase3_validacao_unit.py (SemFuga, Modos) e por test_mmp_deteccao_automatica.py.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_mmp_deteccao_automatica as T  # noqa: E402  (base de testes e dados sinteticos)
import modelo_estimador as E  # noqa: E402
import modelo_validacao as V  # noqa: E402
import protocolo  # noqa: E402

U, db, cv = T.U, T.db, T.cv


def ob(tid, data, w, aid=None, hora=None):
    return {'test_id': tid, 'revisao': 1, 'activity_id': aid or tid, 'data': data, 'watts': float(w),
            'hora_local': hora, 'elegibilidade_aprendizagem': 'elegivel'}


class UltimoValorM1(unittest.TestCase):
    """M1 = ultimo valor. Varias observacoes no ultimo dia: so a hora real as ordena."""

    def test_um_unico_valor_no_ultimo_dia_e_o_ultimo(self):
        self.assertEqual(V.ultimo_valor([ob('a', '2026-01-01', 100), ob('b', '2026-01-02', 120)]), (120.0, False))

    def test_sem_hora_a_ordem_e_desconhecida_e_o_activity_id_nao_decide(self):
        for ids in (('a', 'b'), ('b', 'a')):                  # trocar os ids nao muda nada
            t = [ob('x', '2026-01-01', 100), ob('t1', '2026-01-02', 200, aid=ids[0]),
                 ob('t2', '2026-01-02', 300, aid=ids[1])]
            self.assertEqual(V.ultimo_valor(t), (250.0, True))

    def test_horas_reais_distintas_a_mais_tardia_e_o_ultimo_valor(self):
        for ids in (('a', 'b'), ('b', 'a')):
            t = [ob('t1', '2026-01-02', 200, aid=ids[0], hora='2026-01-02T08:00:00'),
                 ob('t2', '2026-01-02', 300, aid=ids[1], hora='2026-01-02T17:30:00')]
            self.assertEqual(V.ultimo_valor(t), (300.0, False))
            t.reverse()
            self.assertEqual(V.ultimo_valor(t), (300.0, False))

    def test_horas_iguais_ou_incompletas_ou_de_outro_dia_nao_provam_a_ordem(self):
        casos = {
            'iguais': ('2026-01-02T08:00:00', '2026-01-02T08:00:00'),
            'so_uma': ('2026-01-02T08:00:00', None),
            'outro_dia': ('2026-01-02T08:00:00', '2026-01-03T09:00:00'),
            'texto_invalido': ('2026-01-02T08:00:00', 'amanha'),
            'hora_impossivel': ('2026-01-02T08:00:00', '2026-01-02T25:61:00'),
            'hora_impossivel_sem_segundos': ('2026-01-02T99:99', '2026-01-02T08:00'),
            'sem_hora': ('2026-01-02', '2026-01-02')}
        for nome, (h1, h2) in casos.items():
            t = [ob('t1', '2026-01-02', 200, hora=h1), ob('t2', '2026-01-02', 300, hora=h2)]
            self.assertEqual(V.ultimo_valor(t), (250.0, True), nome)

    def test_horas_validas_sem_segundos_ou_com_espaco_provam_a_ordem(self):
        t = [ob('t1', '2026-01-02', 200, hora='2026-01-02 08:00'), ob('t2', '2026-01-02', 300, hora='2026-01-02T17:30')]
        self.assertEqual(V.ultimo_valor(t), (300.0, False))

    def test_dias_diferentes_nunca_se_misturam(self):
        t = [ob('a', '2026-01-01', 100), ob('b', '2026-01-01', 110), ob('c', '2026-01-03', 200)]
        self.assertEqual(V.ultimo_valor(t), (200.0, False))             # so conta o ultimo dia

    def test_previsoes_metodos_m1_independe_do_activity_id_e_m0_m2_nao_mudam(self):
        res = []
        for ids in (('a', 'b'), ('b', 'a')):
            treino = [ob('x', '2026-01-01', 100), ob('t1', '2026-01-02', 200, aid=ids[0]),
                      ob('t2', '2026-01-02', 300, aid=ids[1])]
            p, motivo = V.previsoes_metodos(treino, '2026-01-10')
            self.assertIsNone(motivo)
            res.append(p)
        self.assertEqual(res[0]['M1'], 250.0)
        self.assertTrue(res[0]['m1_ordem_ambigua'])
        for k in ('M0', 'M1', 'M2', 'm1_ordem_ambigua'):
            self.assertEqual(res[0][k], res[1][k], k)

    def test_sem_ambiguidade_o_comportamento_anterior_e_igual(self):
        treino = [ob('x', '2026-01-01', 100), ob('y', '2026-01-05', 140)]
        p, _ = V.previsoes_metodos(treino, '2026-01-10')
        self.assertEqual((p['M1'], p['m1_ordem_ambigua']), (140.0, False))

    def test_origens_ambiguas_contadas_e_mesmos_alvos_nos_tres_metodos(self):
        alvos = [ob('o1', '2026-01-01', 100), ob('o2', '2026-01-02', 110, aid='b'),
                 ob('o3', '2026-01-02', 130, aid='a'), ob('o4', '2026-01-09', 125),
                 ob('o5', '2026-01-20', 128)]
        it = V._avaliar_chave('retrospectivo', 'Bike', 300, alvos,
                              lambda a: [o for o in alvos if o['data'] < a['data']], 0, {},
                              V.Q_PADRAO, V.GRELHA_Q)
        # so o alvo o4 tem um ultimo dia (02) com duas observacoes sem ordem provada
        self.assertEqual(it['n_origens_m1_ordem_ambigua'], 1)
        self.assertEqual([p['m1_ordem_ambigua'] for p in it['previsoes']], [False, False, True, False])
        self.assertEqual({m: it['metodos'][m]['n'] for m in ('M0', 'M1', 'M2')}, {'M0': 4, 'M1': 4, 'M2': 4})
        self.assertEqual(it['previsoes'][2]['M1'], 120.0)

    def test_alvos_do_mesmo_dia_continuam_a_nao_se_ver_entre_si(self):
        alvos = [ob('o1', '2026-01-01', 100), ob('o2', '2026-01-05', 110), ob('o3', '2026-01-05', 500)]
        it = V._avaliar_chave('retrospectivo', 'Bike', 300, alvos,
                              lambda a: [o for o in alvos if o['data'] < a['data']], 0, {},
                              V.Q_PADRAO, V.GRELHA_Q)
        p = {x['test_id']: x for x in it['previsoes']}
        for tid in ('o2', 'o3'):
            self.assertEqual((p[tid]['M0'], p[tid]['M1'], p[tid]['n_treino']), (100.0, 100.0, 1))


def item(estado, n=0, datas=0, dias=0, veredicto=None, mod='Bike', dur=300, motivos=None):
    return {'modalidade': mod, 'duracao_s': dur, 'estado_suficiencia': estado, 'n_alvos': n,
            'n_datas_distintas': datas, 'dias_cobertura': dias, 'veredicto': veredicto,
            'criterios': {'min_alvos': 20, 'min_datas_distintas': 15, 'min_dias_cobertura': 90},
            'motivos_exclusao_observacoes': motivos or {}}


class OrientacaoDados(unittest.TestCase):
    def test_criterios_de_suficiencia_e_parametros_nao_foram_alterados(self):
        self.assertEqual((V.MIN_ALVOS_AVALIAVEL, V.MIN_DATAS_AVALIAVEL, V.MIN_DIAS_AVALIAVEL), (20, 15, 90))
        self.assertEqual((V.MARGEM_RELATIVA, V.Q_PADRAO, V.GRELHA_Q), (0.05, 0.01, (0.001, 0.003, 0.01, 0.03, 0.1)))
        self.assertEqual(protocolo.LIMIAR_ESFORCO, 0.92)

    def test_sem_alvos_e_provisorio_e_sugere_um_esforco(self):
        o = V.orientacao_dados(item('inconclusivo_sem_alvos'), protocolo.TESTES, 0.92)
        self.assertEqual((o['situacao'], o['conclusao']), ('sem_alvos', 'provisoria'))
        self.assertEqual(o['faltam'], {'alvos': 20, 'datas_distintas': 15, 'dias_cobertura': 90})
        self.assertEqual(o['observacoes_a_acrescentar'], 20)
        self.assertEqual(o['teste_util']['nome'], protocolo.TESTES[300]['nome'])
        self.assertEqual(o['teste_util']['mede'], protocolo.TESTES[300]['mede'])

    def test_amostra_insuficiente_diz_exatamente_o_que_falta(self):
        o = V.orientacao_dados(item('descritivo', n=19, datas=19, dias=1118, dur=60), protocolo.TESTES, 0.92)
        self.assertEqual(o['faltam'], {'alvos': 1, 'datas_distintas': 0, 'dias_cobertura': 0})
        self.assertEqual(o['observacoes_a_acrescentar'], 1)
        self.assertEqual(o['teste_util']['nome'], protocolo.TESTES[60]['nome'])
        o = V.orientacao_dados(item('descritivo', n=11, datas=11, dias=224, dur=60), protocolo.TESTES)
        self.assertEqual(o['faltam'], {'alvos': 9, 'datas_distintas': 4, 'dias_cobertura': 0})
        self.assertEqual(o['observacoes_a_acrescentar'], 9)

    def test_so_a_cobertura_temporal_em_falta_tambem_e_amostra_insuficiente(self):
        o = V.orientacao_dados(item('descritivo', n=25, datas=25, dias=30), protocolo.TESTES)
        self.assertEqual(o['observacoes_a_acrescentar'], 0)
        self.assertEqual(o['faltam']['dias_cobertura'], 60)
        self.assertIsNotNone(o['teste_util'])                 # um esforco mais tarde alarga a cobertura

    def test_amostra_suficiente_nunca_sugere_mais_testes(self):
        for ver, sit in ((None, 'avaliavel_diferenca_nao_demonstrada'),
                         ('diferenca_nao_demonstrada', 'avaliavel_diferenca_nao_demonstrada'),
                         ('m2_melhor_que_ambos_baselines', 'avaliavel_m2_melhor'),
                         ('m2_pior_que_algum_baseline', 'avaliavel_m2_pior')):
            o = V.orientacao_dados(item('avaliavel', n=40, datas=40, dias=900, veredicto=ver), protocolo.TESTES)
            self.assertEqual(o['situacao'], sit)
            self.assertIsNone(o['teste_util'])
            self.assertIsNone(o['faltam'])
            self.assertEqual(o['motivo_sem_sugestao'], 'amostra_suficiente_para_avaliar')
            self.assertEqual(o['conclusao'], 'avaliada_exploratoria')

    def test_inconclusivo_nao_vira_equivalencia_nem_o_pior_vira_global(self):
        inc = V.orientacao_dados(item('avaliavel', n=40, datas=40, dias=900,
                                      veredicto='diferenca_nao_demonstrada'))
        self.assertIn('Nao prova equivalencia', inc['resumo'])
        pior = V.orientacao_dados(item('avaliavel', n=40, datas=40, dias=900,
                                       veredicto='m2_pior_que_algum_baseline'))
        self.assertIn('pior', pior['resumo'])
        self.assertIn('Nao se generaliza', pior['resumo'])
        melhor = V.orientacao_dados(item('avaliavel', n=40, datas=40, dias=900,
                                         veredicto='m2_melhor_que_ambos_baselines'))
        self.assertIn('Nao se generaliza', melhor['resumo'])

    def test_amostra_insuficiente_nao_valida_nem_rejeita(self):
        o = V.orientacao_dados(item('descritivo', n=5, datas=5, dias=10))
        self.assertIn('nao valida nem rejeita', o['resumo'])
        self.assertEqual(o['conclusao'], 'provisoria')

    def test_item_incompleto_nunca_e_tratado_como_amostra_suficiente(self):
        for it in ({'modalidade': 'Bike', 'duracao_s': 300}, {'estado_suficiencia': 'desconhecido', 'duracao_s': 300}):
            o = V.orientacao_dados(it, protocolo.TESTES)
            self.assertEqual((o['situacao'], o['conclusao']), ('amostra_insuficiente', 'provisoria'))
            self.assertNotEqual(o['motivo_sem_sugestao'], 'amostra_suficiente_para_avaliar')

    def test_o_esforco_e_uma_oportunidade_e_nao_um_teste_padronizado(self):
        o = V.orientacao_dados(item('descritivo', n=5, datas=5, dias=10), protocolo.TESTES, 0.92)
        c = o['teste_util']['condicao']
        for frag in ('Nao e um teste padronizado', 'nem obrigatorio', '92%', 'icu_ignore_power=false', 'ignore_parts'):
            self.assertIn(frag, c)

    def test_duracao_sem_protocolo_definido_nao_inventa_um(self):
        o = V.orientacao_dados(item('descritivo', n=5, datas=5, dias=10, dur=45), protocolo.TESTES)
        self.assertIsNone(o['teste_util'])
        self.assertEqual(o['motivo_sem_sugestao'], 'duracao_sem_protocolo_definido')
        o = V.orientacao_dados(item('descritivo', n=5, datas=5, dias=10))                 # sem protocolo
        self.assertEqual(o['motivo_sem_sugestao'], 'duracao_sem_protocolo_definido')

    def test_usa_os_criterios_do_proprio_item(self):
        it = item('descritivo', n=5, datas=5, dias=10)
        it['criterios'] = {'min_alvos': 8, 'min_datas_distintas': 7, 'min_dias_cobertura': 30}
        o = V.orientacao_dados(it, protocolo.TESTES)
        self.assertEqual(o['faltam'], {'alvos': 3, 'datas_distintas': 2, 'dias_cobertura': 20})

    def test_conta_as_excluidas_por_elegibilidade_sem_mexer_na_regra(self):
        o = V.orientacao_dados(item('descritivo', n=5, datas=5, dias=10, motivos={
            'elegibilidade_desconhecida:ignore_parts_nao_associavel_ao_trecho': 2,
            'inelegivel:icu_ignore_power': 1, 'sem_referencia_anterior': 1}), protocolo.TESTES)
        self.assertEqual(o['excluidas_por_elegibilidade'], 3)

    def test_nao_altera_o_item_recebido(self):
        it = item('descritivo', n=5, datas=5, dias=10)
        antes = copy.deepcopy(it)
        V.orientacao_dados(it, protocolo.TESTES, 0.92)
        self.assertEqual(it, antes)


def serie(prefixo, tipo, n, dur_w, inicio=(2025, 1, 5), passo=7):
    """n curvas semanais, ~constantes (todas proximas do melhor anterior)."""
    from datetime import date, timedelta
    d0 = date(*inicio)
    out = []
    for i in range(n):
        d = (d0 + timedelta(days=passo * i)).isoformat()
        out.append(cv(f'{prefixo}{i}', tipo, d, w1200=dur_w + (i % 5)) if tipo == 'Bike'
                   else cv(f'{prefixo}{i}', tipo, d, w300=dur_w + (i % 5)))
    return out


class _Base(T.BaseDB):
    CURVAS = serie('s', 'Bike', 30, 300) + serie('r', 'Row', 12, 200)

    def validacao(self, q=''):
        r = self.c.get('/api/modelo/validacao' + q)
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def unidade(self, j, modo, mod, dur):
        return next(i for i in j['modos'][modo]['itens'] if (i['modalidade'], i['duracao_s']) == (mod, dur))


class ApiOrientacao(_Base):
    def test_sem_nenhuma_observacao_tudo_funciona_e_orienta(self):
        j = self.validacao()                                   # antes de qualquer deteccao
        for modo in ('retrospectivo', 'conhecido_na_epoca'):
            itens = j['modos'][modo]['itens']
            self.assertEqual(len(itens), 12)
            for it in itens:
                self.assertEqual(it['estado_suficiencia'], 'inconclusivo_sem_alvos')
                self.assertEqual(it['orientacao_dados']['situacao'], 'sem_alvos')
                self.assertIsNotNone(it['orientacao_dados']['teste_util'])
                self.assertIn(it['duracao_s'], (60, 300, 1200))

    def test_amostra_suficiente_e_insuficiente_na_mesma_resposta(self):
        self.atualiza()
        j = self.validacao()
        suf = self.unidade(j, 'retrospectivo', 'Bike', 1200)
        ins = self.unidade(j, 'retrospectivo', 'Row', 300)
        self.assertEqual(suf['estado_suficiencia'], 'avaliavel')
        self.assertNotIn(suf['orientacao_dados']['situacao'], ('sem_alvos', 'amostra_insuficiente'))
        self.assertIsNone(suf['orientacao_dados']['teste_util'])          # ja ha amostra: nao pedir mais
        self.assertEqual(ins['estado_suficiencia'], 'descritivo')
        o = ins['orientacao_dados']
        self.assertEqual(o['situacao'], 'amostra_insuficiente')
        self.assertEqual(o['conclusao'], 'provisoria')
        self.assertEqual(o['faltam']['alvos'], 20 - ins['n_alvos'])
        self.assertEqual(o['teste_util']['nome'], protocolo.TESTES[300]['nome'])

    def test_falta_de_amostra_nao_esconde_a_informacao_descritiva(self):
        self.atualiza()
        ins = self.unidade(self.validacao(), 'retrospectivo', 'Row', 300)
        self.assertGreater(ins['n_alvos'], 0)
        for m in ('M0', 'M1', 'M2'):
            self.assertIsNotNone(ins['metodos'][m]['mae'])
            self.assertIsNotNone(ins['metodos'][m]['erro_medio'])
        self.assertIsNone(ins['veredicto'])                               # sem veredicto, mas com metricas
        self.assertEqual(ins['diferencas_m2_menos_baseline']['M1']['motivo_sem_ic'], 'amostra_insuficiente')

    def test_conhecido_na_epoca_sem_historico_nao_bloqueia_e_orienta(self):
        self.atualiza()
        e = self.unidade(self.validacao(), 'conhecido_na_epoca', 'Bike', 1200)
        self.assertEqual(e['estado_suficiencia'], 'inconclusivo_sem_alvos')    # nada conhecido antes dos alvos
        self.assertEqual(e['orientacao_dados']['situacao'], 'sem_alvos')
        self.assertEqual(e['historico_temporal']['estado'], 'sem_historico_temporal')

    def test_o_aviso_diz_o_que_o_alvo_e_e_que_o_retrospetivo_e_exploratorio(self):
        a = self.validacao()['aviso']
        for frag in ('proximo esforco detetado', 'nao a capacidade', 'retrospetivo', 'conhecido_na_epoca', 'EXPLORATORIA'):
            self.assertIn(frag, a)

    def test_so_leitura_e_nao_muda_a_assinatura_dos_resultados(self):
        self.atualiza()
        antes = (self.n_linhas(), U.contar(db))
        j1, j2 = self.validacao(), self.validacao()
        self.assertEqual((self.n_linhas(), U.contar(db)), antes)
        self.assertEqual(j1['assinatura'], j2['assinatura'])
        self.assertEqual(j1['configuracao']['q_predefinido'], 0.01)

    def test_a_orientacao_nao_duplica_as_sugestoes_de_testes_em_atraso(self):
        # as sugestoes existentes (protocolo.sugerir) continuam a responder a outra pergunta
        r = self.c.get('/api/protocolo').get_json()
        self.assertIn('sugestoes', r)
        for s in r['sugestoes']:
            self.assertNotIn('orientacao_dados', s)
            self.assertIn('atraso_dias', s)


class UiValidacao(_Base):
    """O JS real do painel, alimentado com a resposta REAL do endpoint."""

    @classmethod
    def setUpClass(cls):
        import tabs.tab_pmc as tp
        cls.js, cls.body = tp.JS, tp.BODY

    def correr(self):
        if shutil.which('node') is None:
            self.skipTest('node nao disponivel')
        self.atualiza()
        real = self.validacao()
        with tempfile.TemporaryDirectory() as d:
            p, q = os.path.join(d, 'pmc_js.js'), os.path.join(d, 'real.json')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(self.js)
            with open(q, 'w', encoding='utf-8') as f:
                json.dump(real, f)
            r = subprocess.run(['node', os.path.join(HERE, 'js', 'harness_validacao.js'), p, q],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout[-300:] + r.stderr[-300:])
        return real, json.loads(r.stdout)

    def test_painel_legado_removido_mas_codigo_so_leitura(self):
        # Decisao: a validacao LEGADO saiu da PMC. O codigo e o endpoint mantem-se, so de leitura.
        self.assertNotIn('id="validacaoExp"', self.body)
        bloco = self.js[self.js.index('// <<validacao-exploratoria>>'):self.js.index('// <</validacao-exploratoria>>')]
        self.assertEqual(bloco.count("fetch("), 1)
        self.assertIn("/api/modelo/validacao'", bloco)
        self.assertNotIn("method:'POST'", bloco)
        self.assertNotIn('localStorage', bloco)

    def test_pedido_so_ao_abrir_e_uma_vez(self):
        _, r = self.correr()
        self.assertEqual(r['definir_nao_pede_nada']['chamadas'], 0)
        self.assertEqual(r['fechado_nao_pede']['chamadas'], 0)
        self.assertEqual(r['abrir_pede_uma_vez']['chamadas'], ['/api/modelo/validacao'])

    def test_erro_de_rede_mostra_mensagem_e_permite_repetir(self):
        _, r = self.correr()
        e = r['erro_de_rede_permite_repetir']
        self.assertIn('Erro ao carregar a validacao', e['msg'])
        self.assertEqual(e['chamadas'], 2)
        self.assertIn('<table', e['depois'])

    def test_resposta_de_erro_e_texto_do_servidor_sao_escapados(self):
        _, r = self.correr()
        self.assertNotIn('<b>x</b>', r['resposta_de_erro']['html'])
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', r['resposta_de_erro']['html'])
        self.assertNotIn('<script>', r['escape']['html'])
        self.assertIn('&lt;script&gt;', r['escape']['html'])

    def test_mostra_as_12_unidades_com_a_orientacao_do_servidor(self):
        real, r = self.correr()
        h = r['real']['html']
        self.assertEqual(h.count('<tr><td>'), 12)
        self.assertIn('amostra insuficiente (provisorio)', h)
        self.assertIn('sem alvos (provisorio)', h)
        self.assertRegex(h, r'avaliavel: (sem diferenca demonstrada|estimador (melhor|pior))')
        ins = self.unidade(real, 'retrospectivo', 'Row', 300)
        self.assertIn(f"{ins['n_alvos']}/20 alvos", h)
        self.assertIn('Util: <b>' + protocolo.TESTES[300]['nome'] + '</b>', h)
        suf = self.unidade(real, 'retrospectivo', 'Bike', 1200)
        self.assertIn(f"{suf['n_alvos']}/20 alvos", h)
        self.assertIn('nao ha teste a sugerir so para aumentar o numero', h)

    def test_texto_distingue_detecao_elegibilidade_validacao_e_alvo(self):
        _, r = self.correr()
        h = r['real']['html']
        for frag in ('nao validado', '<b>melhor valor da duracao em toda a atividade</b>', 'nao um teste padronizado',
                     '<b>Detetar</b>', '<b>elegivel</b>', '<b>validar</b>', 'nao prova capacidade preditiva',
                     'exploratorio: usa observacoes detetadas depois do alvo', 'nao obrigacoes'):
            self.assertIn(frag, h)
        self.assertNotIn('estimador validado', h)

    def test_erro_do_servidor_mostra_mensagem_e_permite_nova_tentativa(self):
        _, r = self.correr()
        e = r['erro_do_servidor_permite_repetir']
        self.assertEqual(e['chamadas'], 2)
        self.assertIn('Nao foi possivel calcular: falhou', e['html'])

    def test_item_incompleto_nao_mostra_undefined_nem_rebenta(self):
        _, r = self.correr()
        h = r['item_incompleto']['html']
        self.assertEqual(h.count('<tr><td>'), 12)
        for mau in ('undefined', 'NaN', 'null'):
            self.assertNotIn(mau, h)
        self.assertIn('&mdash;/&mdash; alvos', h)

    def test_fallback_da_media_de_m1_e_explicado_na_interface(self):
        real, r = self.correr()
        self.assertIn('M1 usou a media desse dia', r['m1_ambiguo']['html'])
        self.assertIn('fallback metodologico, nao o ultimo valor literal', r['m1_ambiguo']['html'])
        self.assertNotIn('M1 usou a media desse dia', r['real']['html'])          # so quando houve origens afetadas
        self.assertIn('usa a media desse dia (fallback)', r['real']['html'])
        for it in real['modos']['retrospectivo']['itens']:
            self.assertIn('n_origens_m1_ordem_ambigua', it)

    def test_sem_modo_na_epoca_ou_sem_itens_nao_rebenta(self):
        _, r = self.correr()
        self.assertEqual(r['sem_modo_na_epoca']['html'].count('<tr><td>'), 12)
        self.assertIn('<table', r['sem_itens']['html'])


if __name__ == '__main__':
    unittest.main()
