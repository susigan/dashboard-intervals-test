"""api_recovery.py — endpoints da tab Recovery.

Registado com:  import api_recovery; api_recovery.registar(app)
"""

import os
import sys
import traceback
from datetime import datetime, timedelta

from flask import jsonify, request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'utils'))


def registar(app):

    @app.route('/api/recovery/dados')
    def api_recovery_dados():
        """Todos os modelos de uma vez.  ?dias=180  ?log_hf=0"""
        try:
            import hrv_recovery as hrec

            dias = request.args.get('dias', type=int) or 180
            corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d')

            # wellness: a folha é a fonte, com a Intervals.icu como reforço
            registos, origem = [], []
            try:
                import sheets_client
                w, _c, _e = sheets_client.carregar()
                for x in (w or []):
                    d = str(x.get('date') or x.get('Data') or '')[:10]
                    if d >= corte:
                        registos.append({
                            'data': d,
                            'hrv': x.get('hrv'),
                            'hf_power': x.get('hf_power'),
                            'rhr': x.get('rhr') or x.get('hr'),
                            'sono_h': x.get('sleep') or x.get('sono'),
                            'sono_qualidade': x.get('sleep_quality'),
                            'fadiga': x.get('fatiga') or x.get('fadiga'),
                            'stress': x.get('stress'),
                            'humor': x.get('humor'),
                            'dores': x.get('soreness') or x.get('dores'),
                            'peso': x.get('peso'),
                        })
                if registos:
                    origem.append('folha')
            except Exception as e:
                origem.append(f'folha falhou: {str(e)[:60]}')

            prep = hrec.preparar(registos)
            if not prep.get('ok'):
                return jsonify({'status': 'sem_dados',
                                'motivo': prep.get('motivo'),
                                'origem': origem}), 200

            ln = prep['lnrmssd']
            seq = prep['dias']
            res = {'status': 'ok', 'origem': origem,
                   'periodo': {'de': prep['de'], 'ate': prep['ate'],
                               'n_dias': prep['n_dias'],
                               'n_com_hrv': prep['n_com_hrv'],
                               'cobertura_pct': prep['cobertura_pct']}}

            # cada modelo diz por si se tem dados que cheguem
            if prep['n_com_hrv'] >= hrec.MINIMOS['swc']:
                res['swc'] = hrec.baseline_swc(ln)
            else:
                res['swc'] = {'ok': False,
                              'motivo': f"{prep['n_com_hrv']} dias com HRV; "
                                        f"são precisos {hrec.MINIMOS['swc']}"}
            if prep['n_com_hrv'] >= hrec.MINIMOS['javaloyes']:
                res['javaloyes'] = hrec.javaloyes(ln)
            else:
                res['javaloyes'] = {
                    'ok': False,
                    'motivo': f"são precisos {hrec.MINIMOS['javaloyes']} dias"}

            res['kiviniemi'] = hrec.kiviniemi(
                [d.get('hf_power') for d in seq],
                usar_log=request.args.get('log_hf') != '0')
            res['pslope'] = hrec.pslope(ln)
            res['beta'] = hrec.modelo_beta(ln)

            # wellness subjectivo: z-score dos últimos 28 dias
            res['wellness'] = _wellness(seq)

            # síntese
            _b = res['beta'] if res['beta'].get('ok') else {}
            res['sintese'] = hrec.sintetizar(
                swc=(res['swc'].get('estado') or [None])[-1]
                if res['swc'].get('estado') else None,
                jav=(res['javaloyes'].get('prescricao') or [None])[-1]
                if res['javaloyes'].get('prescricao') else None,
                kiv=(res['kiviniemi'].get('prescricao') or [None])[-1]
                if res['kiviniemi'].get('ok') else None,
                ps=res['pslope'].get('zona_actual'),
                beta=({'beta': _b.get('beta_hoje'),
                       'agudo': _b.get('agudo_hoje'),
                       'cronico': _b.get('cronico_hoje')} if _b else None),
                wellness=(res['wellness'] or {}).get('voto'))
            res['datas'] = [d['data'] for d in seq]
            return jsonify(res)
        except Exception as e:
            return jsonify({'status': 'erro', 'mensagem': str(e),
                            'trace': traceback.format_exc()}), 500

    return app


def _wellness(seq):
    """Sono, RHR, fadiga, stress e dores como um voto entre −1 e +1.

    Fica SEPARADO dos modelos de HRV de propósito: é a única fonte que não
    depende do sensor, e por isso é a que pode contradizê-lo de forma
    informativa. Fundi-la no HRV apagaria isso.
    """
    import hrv_recovery as hrec

    campos = {
        'sono_qualidade': +1, 'sono_h': +1,
        'rhr': -1,           # RHR alta é mau
        'fadiga': -1, 'stress': -1, 'dores': -1,
        'humor': +1,
    }
    votos, detalhe = [], {}
    for campo, sentido in campos.items():
        vs = [d.get(campo) for d in seq]
        reais = [float(v) for v in vs if isinstance(v, (int, float))]
        if len(reais) < 14:
            continue
        hoje = next((float(v) for v in reversed(vs)
                     if isinstance(v, (int, float))), None)
        if hoje is None:
            continue
        janela = reais[-28:]
        m = sum(janela) / len(janela)
        s = hrec._sd(janela)
        if not s:
            continue
        z = (hoje - m) / s * sentido
        votos.append(max(-1.0, min(1.0, z / 1.5)))
        detalhe[campo] = {'hoje': round(hoje, 1), 'media28': round(m, 1),
                          'z': round(z, 2), 'n': len(reais)}
    if not votos:
        return {'ok': False, 'motivo': 'sem campos com 14+ dias'}
    return {
        'ok': True,
        'voto': round(sum(votos) / len(votos), 2),
        'n_campos': len(votos),
        'campos': detalhe,
        'nota': ('z-score de cada campo contra os seus 28 dias, com o sinal '
                 'invertido nos que são maus quando sobem (RHR, fadiga, '
                 'stress, dores). É a única fonte independente do sensor'),
    }
