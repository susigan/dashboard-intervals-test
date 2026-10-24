"""Consistencia detetor/seletor: atividades nao elegiveis nao servem de referencia.

Antes, observacoes_auto.atualizar so retirava de referencia as atividades com icu_ignore_power=true,
enquanto o seletor (modelo_estimador.selecionar_observacoes_auto) excluia como observacoes as que
tinham ignore_parts nao vazio. Resultado: uma referencia sem elegibilidade provada entrava no calculo.
Testes puros (sem base de dados).
"""
import os
import sys
import unittest

# app.py (importado por outros testes do mesmo processo) sai se a chave nao estiver definida: mesmo padrao dos
# restantes testes IR. Sem isto, correr este ficheiro antes de test_mmp_auto_ui impede a geracao do payload.
os.environ.setdefault('INTERVALS_ICU_API_KEY', 'valor-ficticio-de-teste')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import modelo_observacoes_auto as moa  # noqa: E402
import observacoes_auto as oa  # noqa: E402

ELEGIVEL = 'elegivel'


def detetar(curvas, ignorar=()):
    # moa.detetar devolve lista; indexa por test_id como os testes existentes
    return {c['test_id']: c for c in moa.detetar(curvas, ignorar)}


def cur(aid, date, watts, tipo='Run'):
    # secs/watts no mesmo formato que db.load_power_curves (60, 300, 1200 s)
    return {'type': tipo, 'date': date, 'activity_id': aid,
            'secs': [60, 300, 1200], 'watts': [watts, watts - 50, watts - 100]}


class TestIgnoradosDaReferencia(unittest.TestCase):
    def test_icu_ignore_power_sai_da_pool(self):
        s = oa._ignorados_da_referencia({'a1': True, 'a9': False}, {}, ELEGIVEL)
        self.assertEqual(s, {'a1'})

    def test_ignore_parts_desconhecida_sai_da_pool(self):
        eleg = {'a2': {'estado': 'desconhecida', 'motivo': 'ignore_parts_nao_associavel_ao_trecho'}}
        self.assertIn('a2', oa._ignorados_da_referencia({}, eleg, ELEGIVEL))

    def test_elegivel_continua_na_pool(self):
        eleg = {'a3': {'estado': ELEGIVEL, 'motivo': None}}
        self.assertNotIn('a3', oa._ignorados_da_referencia({}, eleg, ELEGIVEL))

    def test_inelegivel_e_desconhecida_sem_eleg_sao_excluidas(self):
        eleg = {'a4': {'estado': 'inelegivel'}, 'a5': None}
        s = oa._ignorados_da_referencia({}, eleg, ELEGIVEL)
        self.assertEqual(s, {'a4', 'a5'})


class TestDetetorNaoUsaReferenciaNaoElegivel(unittest.TestCase):
    def test_referencia_com_ignore_parts_nao_e_usada_pelo_detetor(self):
        # 6 esforcos validos anteriores a 300 W; a atividade 'ip' (ignore_parts) tem 548 W.
        curvas = [cur(f'p{i}', f'2025-0{i}-01', 300) for i in range(1, 7)]
        curvas.append(cur('ip', '2025-09-10', 548))
        curvas.append(cur('x', '2025-10-05', 520))

        # comportamento antigo: so icu_ignore_power era retirado; 'ip' tem ignore_parts (icu_ignore_power ausente)
        ign_antigo = oa._ignorados_da_referencia({}, {}, ELEGIVEL)
        ign_novo = oa._ignorados_da_referencia(
            {}, {'ip': {'estado': 'desconhecida'}}, ELEGIVEL)                    # regra nova

        antigo = detetar(curvas, ign_antigo)
        novo = detetar(curvas, ign_novo)

        ref_antigo = antigo['Run:60:x']['referencias']['all_time']
        ref_novo = novo['Run:60:x']['referencias']['all_time']
        self.assertEqual(ref_antigo['referencia_activity_id'], 'ip')   # era o bug
        self.assertNotEqual(ref_novo['referencia_activity_id'], 'ip')  # corrigido
        self.assertEqual(ref_novo['referencia_watts'], 300)

    def test_icu_ignore_power_continua_fora_da_referencia(self):
        curvas = [cur(f'p{i}', f'2025-0{i}-01', 300) for i in range(1, 7)]
        curvas.append(cur('ip', '2025-09-10', 548))
        curvas.append(cur('x', '2025-10-05', 520))
        ign = oa._ignorados_da_referencia({'ip': True}, {}, ELEGIVEL)
        out = detetar(curvas, ign)
        self.assertNotEqual(out['Run:60:x']['referencias']['all_time']['referencia_activity_id'], 'ip')


if __name__ == '__main__':
    unittest.main()
