"""Etapa 7: validacao preditiva com P0 RECONSTRUIDO antes de cada alvo (sem fuga temporal).

A) Unitarios de p0_reconstruido_antes: entradas_mmp3, o conhecimento das observacoes e as rejeicoes manuais sao
   CONTROLADOS (falsos, com o mesmo corte temporal), mas as regras de producao (p0_global, resolver_p0) correm
   de verdade. O assento de teste que fixa o P0 e pausado nestes testes.
B) Ponta a ponta SINTETICO (base temporaria): cenario do assento de teste, com a deteccao marcada no dia do esforco e
   YES elegiveis SINTETICOS. Resultados sao sinteticos: nao sao evidencia de validacao real.
"""
import copy, os, sys, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')
import test_modelo_ir_servico as TS  # noqa: E402
import modelo_ir_servico as mis  # noqa: E402
import modelo_ir_p0 as p0m  # noqa: E402
import modelo_mmp3 as mmp3  # noqa: E402
import db  # noqa: E402

# Regra P0 REAL, capturada antes de qualquer assento de teste a substituir (o assento fixa P0=290).
P0_REAL = mis.p0_global_das_entradas


def setUpModule():
    TS.setUpModule()


def tearDownModule():
    TS.tearDownModule()


def yes(test_id, data, watts):
    """Esforco de 180 s no formato de modelo_mmp3.avaliar_esforcos (so os campos que o P0 le)."""
    return {'test_id': test_id, 'activity_id': test_id.split(':')[-1], 'modalidade': 'Bike', 'data': data,
            'inicio': None, 'watts': watts, 'estado': mmp3.E_ELEGIVEL, 'motivo': 'yes', 'tag_3min': True,
            'pr': False, 'pct_do_melhor': 1.0, 'origem_classificacao': 'teste', 'motivo_decisao': 'teste',
            'classe_intervalo': None, 'origem_registo': None, 'season': None}


class _Auto:
    """Conhecimento falso: conhecidas_antes(corte) -> observacoes com prova ANTERIOR ao corte (estrito)."""

    def __init__(self, conhecidas):
        self._c = conhecidas  # {test_id: 'AAAA-MM-DD' em que passou a ser conhecida}

    def conhecidas_antes(self, corte):
        return [{'test_id': t} for t, d in self._c.items() if d < corte]


def entradas_fake(esforcos):
    """entradas_mmp3 falso com o MESMO corte temporal do real: so esforcos com data <= ate. Regista ate."""
    def f(curvas, atividades, ate, marcos=None, revisoes=None):
        f.chamadas.append(ate)
        return {'Bike': ([], [copy.deepcopy(e) for e in esforcos if e['data'] <= ate])}
    f.chamadas = []
    return f


def rejeitados_fake(rev):
    """Rejeicoes manuais falsas: revisao com 'rej': True rejeita o test_id (a regra real lê o estado atual)."""
    return {r['test_id'] for r in rev or [] if r.get('rej')}


class P0Reconstruido(unittest.TestCase):

    def setUp(self):
        TS._LEG.parar()                       # o assento fixa o P0 (290): nestes testes queremos a regra real
        self.addCleanup(TS._LEG.iniciar)

    def rec(self, esforcos, conhecidas, data, revisoes=None, cache=None):
        with mock.patch.object(mis, 'entradas_mmp3', side_effect=entradas_fake(esforcos)), \
                mock.patch.object(mis, 'rejeitados_manualmente', side_effect=rejeitados_fake):
            return mis.p0_reconstruido_antes([], [], None, revisoes or [], _Auto(conhecidas), data, cache)

    def test_usa_o_p0_do_ultimo_yes_conhecido_antes_do_alvo(self):
        ef = [yes('Bike:180:a', '2025-03-01', 300.0), yes('Bike:180:b', '2025-04-01', 320.0)]
        r = self.rec(ef, {'Bike:180:a': '2025-03-02', 'Bike:180:b': '2025-04-02'}, '2025-05-01')
        self.assertTrue(r['valida'])
        self.assertEqual(r['valor_p0'], 320.0)
        self.assertEqual(r['test_id_yes'], 'Bike:180:b')

    def test_yes_detetado_so_depois_do_alvo_fica_fora_mesmo_sendo_mais_recente(self):
        ef = [yes('Bike:180:a', '2025-03-01', 300.0), yes('Bike:180:b', '2025-04-01', 999.0)]
        r = self.rec(ef, {'Bike:180:a': '2025-03-02', 'Bike:180:b': '2025-05-10'}, '2025-05-01')
        self.assertEqual(r['valor_p0'], 300.0)

    def test_yes_futuro_nao_muda_o_p0_passado(self):
        cona = {'Bike:180:a': '2025-03-02'}
        antes = self.rec([yes('Bike:180:a', '2025-03-01', 300.0)], cona, '2025-05-01')['valor_p0']
        futuro = [yes('Bike:180:a', '2025-03-01', 300.0), yes('Bike:180:z', '2025-06-01', 9999.0)]
        depois = self.rec(futuro, dict(cona, **{'Bike:180:z': '2025-06-02'}), '2025-05-01')['valor_p0']
        self.assertEqual(depois, antes)

    def test_ate_e_sempre_o_dia_anterior_ao_alvo(self):
        with mock.patch.object(mis, 'entradas_mmp3', side_effect=entradas_fake([])) as m:
            mis.p0_reconstruido_antes([], [], None, [], _Auto({}), '2025-05-01')
        self.assertEqual(m.call_args.args[2], '2025-04-30')

    def test_revisao_posterior_ao_alvo_nao_entra(self):
        ef = [yes('Bike:180:a', '2025-03-01', 300.0), yes('Bike:180:b', '2025-04-01', 320.0)]
        cona = {'Bike:180:a': '2025-03-02', 'Bike:180:b': '2025-04-02'}
        rev = [{'test_id': 'Bike:180:b', 'rej': True, 'revisado_em': '2025-05-20'}]
        self.assertEqual(self.rec(ef, cona, '2025-05-01', rev)['valor_p0'], 320.0)

    def test_revisao_anterior_ao_alvo_rejeita_o_yes(self):
        ef = [yes('Bike:180:a', '2025-03-01', 300.0), yes('Bike:180:b', '2025-04-01', 320.0)]
        cona = {'Bike:180:a': '2025-03-02', 'Bike:180:b': '2025-04-02'}
        rev = [{'test_id': 'Bike:180:b', 'rej': True, 'revisado_em': '2025-04-20'}]
        self.assertEqual(self.rec(ef, cona, '2025-05-01', rev)['valor_p0'], 300.0)

    def test_sem_yes_conhecido_tem_motivo_da_regra(self):
        r = self.rec([yes('Bike:180:a', '2025-03-01', 300.0)], {'Bike:180:a': '2025-03-02'}, '2025-03-01')
        self.assertFalse(r['valida'])
        self.assertEqual(r['motivo'], p0m.M_SEM_YES)

    def test_yes_com_valor_invalido_e_referencia_invalida(self):
        r = self.rec([yes('Bike:180:a', '2025-03-01', None)], {'Bike:180:a': '2025-03-02'}, '2025-05-01')
        self.assertFalse(r['valida'])
        self.assertEqual(r['motivo'], p0m.M_P0)

    def test_cache_evita_recalcular_a_mesma_data(self):
        cache = {}
        with mock.patch.object(mis, 'entradas_mmp3', side_effect=entradas_fake([])) as m, \
                mock.patch.object(mis, 'rejeitados_manualmente', side_effect=rejeitados_fake):
            for _ in range(3):
                mis.p0_reconstruido_antes([], [], None, [], _Auto({}), '2025-05-01', cache)
        self.assertEqual(m.call_count, 1)


class ValidacaoSintetica(TS._Base):
    """Ponta a ponta SINTETICO (base temporaria): deteccao marcada no dia do esforco; YES elegiveis sinteticos."""

    def setUp(self):
        super().setUp()                       # cenario do assento, sincronizacao (BaseDB)
        self._efeitos = [yes(f'Bike:1200:{c["activity_id"]}', c['date'], float(c['watts'][-1])) for c in self.CURVAS]
        patch = mock.patch.object(mis, 'entradas_mmp3', side_effect=self._entradas)
        patch.start()
        self.addCleanup(patch.stop)
        p0 = mock.patch.object(mis, 'p0_global_das_entradas', new=P0_REAL)   # regra real, nao o P0 fixo do assento
        p0.start()
        self.addCleanup(p0.stop)

    def preparar_marcado(self):
        """prepara() regrava o historico; a marcacao SINTETICA e feita DEPOIS: detetado_em = dia do esforco
        (um sistema que detetasse em tempo real)."""
        self.prepara()
        db._exec("UPDATE modelo_observacoes_auto SET detetado_em = data || 'T12:00:00' "
                 "WHERE modalidade = 'Bike' AND duracao_s = 1200", (), None)

    def _entradas(self, curvas, atividades, ate, marcos=None, revisoes=None):
        return {'Bike': ([], [copy.deepcopy(e) for e in self._efeitos if e['data'] <= ate]),
                'Row': ([], []), 'Ski': ([], []), 'Run': ([], [])}

    def val(self):
        return mis.validar_preditivo(db.observacoes_auto_historico(), db.revisoes_testes_modelo(), self.ativ,
                                     db.load_power_curves() or [], modos=('conhecido_na_epoca',), tipo='Bike',
                                     duracao=1200)

    def test_contagens_fecham_e_motivos_sao_explicitos(self):
        self.preparar_marcado()
        (it,) = self.val()['itens']
        self.assertEqual(it['n_alvos'], it['n_alvos_com_previsao_ir'] + it['n_alvos_rejeitados'])
        self.assertEqual(it['n_alvos_rejeitados'], sum(it['alvos_sem_previsao_ir'].values()))
        self.assertEqual(len(it['alvos_rejeitados']), it['n_alvos_rejeitados'])

    def test_ha_alvos_previstos_com_p0_reconstruido(self):
        self.preparar_marcado()
        (it,) = self.val()['itens']
        self.assertGreater(it['n_alvos_com_previsao_ir'], 0,
                           'sem alvos previstos: ' + str(it['alvos_sem_previsao_ir']))
        for p in it['previsoes']:
            self.assertGreaterEqual(p['n_treino'], 15)

    def test_metricas_por_bloco_trimestral_somam_aos_alvos_avaliaveis(self):
        self.preparar_marcado()
        (it,) = self.val()['itens']
        self.assertTrue(it['metricas_por_bloco'])
        self.assertEqual(sum(b['n_alvos'] for b in it['metricas_por_bloco'].values()), it['n_alvos_com_previsao_ir'])

    def test_sem_yes_resulta_em_saida_explicita_nao_em_vazio(self):
        """Sem nenhum YES conhecido, o resultado tem de dizer porque (motivos e veredicto), nunca um vazio mudo."""
        self.preparar_marcado()
        self._efeitos[:] = []                       # nenhum YES: a regra P0 nao tem referencia
        (it,) = self.val()['itens']
        self.assertEqual(it['n_alvos_com_previsao_ir'], 0)
        self.assertEqual(it['n_alvos_rejeitados'], it['n_alvos'])
        self.assertIn('sem_yes_conhecido_antes_do_alvo', it['alvos_sem_previsao_ir'])
        self.assertIsNone(it['veredicto'])
        self.assertEqual(it['motivo_sem_veredicto'], 'sem_alvos_com_previsao_ir')

    def test_previsoes_passadas_nao_mudam_se_o_futuro_mudar(self):
        self.preparar_marcado()
        antes = {p['test_id']: p for p in self.val()['itens'][0]['previsoes']}
        corte = sorted(p['data'] for p in antes.values())[len(antes) // 2]
        for e in self._efeitos:                       # futuro: YES depois do corte passam a valer outra coisa
            if e['data'] >= corte:
                e['watts'] = e['watts'] * 2 + 500
        for a in self.ativ:                           # e a carga futura muda
            if a['start_date_local'][:10] >= corte:
                a['icu_training_load'] = a['icu_training_load'] * 3 + 100
        depois = {p['test_id']: p for p in self.val()['itens'][0]['previsoes']}
        passadas = [t for t, p in antes.items() if p['data'] < corte and t in depois]
        self.assertTrue(passadas)
        for t in passadas:
            self.assertEqual(depois[t]['ir'], antes[t]['ir'], t)


if __name__ == '__main__':
    unittest.main()
