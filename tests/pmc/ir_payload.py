"""Respostas REAIS de /api/modelo/ir/conjunto (Flask test client, ajustes e atividades SINTETICOS) para o
harness da UI: a interface e testada contra o contrato real do servidor, nao contra JSON escrito a mao."""
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

XSS = '<img src=x onerror=alert(1)>'
URL = '/api/modelo/ir/conjunto'


def gerar():
    import test_modelo_ir_conjunto as T
    import modelo_ir as ir
    import modelo_ir_servico as mis

    out = {}
    app = T.APP.app
    import ir_duracao_legado as _LEG
    import modelo_ir_p0 as p0m
    _LEG.definir_p0(270.0)      # P0 global injetado (MMP3 do ultimo YES); 'sem_cp' = nenhum YES
    reg = _LEG._falso_global({})['registo']
    with mock.patch.object(T.db, 'ENABLED', True):
        def obter(ajs, at, dur=1200):
            m = T._Mock()
            m.ajs, m.at = ajs, at
            ps = [mock.patch.object(T.db, 'ultimo_ajuste_ir', side_effect=lambda t, d: ajs.get(t) if (ajs.get(t) or {}).get('duracao_s') == d else None),
                  mock.patch.object(T.db, 'ajustes_ir_historico', side_effect=lambda t=None, d=None: [ajs[t]] if ajs.get(t) else []),
                  mock.patch.object(T.db, 'load_activities', side_effect=lambda **k: at)]
            for p in ps:
                p.start()
            try:
                return app.test_client().get(URL).get_json()
            finally:
                for p in ps:
                    p.stop()

        ajs, at = T.cenario_base()
        # Bike: ajuste com P0 fixo vindo do CP Model; as outras: ajustes antigos de P0 livre (sem referencia)
        ajs['Bike']['p0_origem'] = 'mmp3_ultimo_yes:' + reg['test_id']
        ajs['Bike']['entradas']['p0_ref'] = {
            'versao_referencia': 2, 'valor_p0': 270.0, 'formulacao': 'p0_mmp3_ultimo_yes', 'origem': 'mmp3_ultimo_yes',
            'registo': {k: reg[k] for k in ('test_id', 'activity_id', 'modalidade', 'data', 'inicio', 'watts')},
            'conhecida_desde': '2026-09-30T09:00:00', 'assinatura': p0m.assinatura_referencia(reg)}
        out['normal'] = obter(ajs, at)                              # 4 modalidades, uma nao identificavel
        ajs, at = T.cenario_base()
        ajs['Row'] = T.aj('Row', ir.EST_NAO_IDENTIFICAVEL, p0=250.0, k1=0.03, k2=0.04, t1=20.0, t2=1.0,
                          primeira_obs=10, ultima_obs=140,
                          avisos=['parametro_no_limite:t1:inferior', XSS])
        ajs['Run'] = T.aj('Run', ir.EST_NAO_IDENTIFICAVEL, p0=100.0, k1=0.01, k2=2.0, t1=40.0, t2=10.0,
                          primeira_obs=20, ultima_obs=130)
        ajs['Ski'] = T.aj('Ski', ir.EST_INSUFICIENTE)
        out['misto'] = obter(ajs, at)                               # nao identificavel + >100 % + insuficiente
        ajs, at = T.cenario_base()
        del ajs['Run']
        ajs['Bike']['p0'] = 0.0
        out['sem_ajuste_e_p0_invalido'] = obter(ajs, at)
        out['sem_curvas'] = obter({}, at)
        ajs, at = T.cenario_base()
        out['lag_e_decaimento'] = obter({'Bike': T.aj('Bike', ir.EST_IDENTIFICAVEL, k1=0.1, k2=0.2, primeira_obs=0, ultima_obs=60)},
                                        [T.ativ('Bike', 10, 100.0)])
        ajs, at = T.cenario_base()
        # o ajuste de Bike usou a referencia v1 mas ja existe a v2 (P0 = CP novo): nao foi reestimado;
        # o Solver de Row falhou ao reestimar com o P0 novo
        ajs['Bike']['entradas']['p0_ref'] = {
            'versao_referencia': 1, 'valor_p0': 270.0, 'formulacao': 'p0_igual_cp', 'modelo_cp': 'M1 (WLS-P)',
            'cp_w': 270.0, 'cp_data_referencia': '2025-01-01', 'conhecida_desde': '2026-09-30T09:00:00',
            'assinatura': 'antiga', 'cp_resultado_id': 1}
        ajs['Row'] = T.aj('Row', ir.EST_SEM_CONVERGENCIA)
        ajs['Row']['metricas'] = {'excecao': 'RuntimeError: boom'}
        ref_nova = {'modalidade': 'Bike', 'duracao_s': 1200, 'versao_referencia': 2, 'valor_p0': 275.0,
                    'formulacao': 'p0_igual_cp', 'modelo_cp': 'M1 (WLS-P)', 'cp_w': 275.0, 'wp_j': 24000.0,
                    'cp_data_referencia': '2026-03-01', 'cp_data_gravacao': '2026-03-02 10:00:00',
                    'conhecida_desde': '2026-10-10T09:00:00', 'assinatura': 'nova', 'cp_resultado_ref': {'id': 2},
                    'parametros': {}}
        with mock.patch.object(T.db, 'ultima_referencia_p0',
                               side_effect=lambda t, d: dict(ref_nova) if t in ('Bike', 'Row') else None):
            out['desatualizado_e_falha'] = obter(ajs, at)
        _LEG.sem_p0()
        try:
            ajs, at = T.cenario_base()
            out['sem_cp'] = obter(ajs, at)                          # nenhum registo YES: sem referencia MMP3
        finally:
            _LEG.definir_p0(270.0)
    return out
