#!/usr/bin/env python3
"""Validacao REAL do HRR por degrau de Aquecimento (so' leitura, sem persistencia).

Correr num computador ou servidor AUTORIZADO, onde a autenticacao da API ja'
esta' configurada (variavel de ambiente usada por api_client). O script nao le,
nao imprime nem grava chaves. Escreve apenas o JSON de saida indicado.

Exemplo:
  python3 scripts/validar_hrr_real.py --modalidade Bike \
      --atividades i000000001,i000000002,i000000003 --saida /caminho/fora/do/repo/hrr_real.json

Saida: para cada atividade, status, cada degrau (alvo, fim efetivo, HR_end,
HR_60, HR_120, HRR60, HRR120 e motivos) e contagem de motivos. Nao substitui
a revisao humana dos valores.
"""
import argparse
import json
import os
import sys
from collections import Counter

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'utils'))


def _streams_da_api(api, aid):
    raw, err = api.icu_get(f'/activity/{aid}/streams')
    if err:
        return None, str(err)[:200]
    lista = (raw.get('streams') or raw.get('content') or []) if isinstance(raw, dict) else raw
    streams = {}
    for st in (lista or []):
        if isinstance(st, dict) and (st.get('type') or st.get('name')):
            streams[st.get('type') or st.get('name')] = st.get('data') or []
    return streams, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--modalidade', required=True, choices=['Bike', 'Row', 'Ski'])
    ap.add_argument('--atividades', required=True, help='IDs separados por virgula, ex.: i123,i456')
    ap.add_argument('--saida', required=True, help='ficheiro JSON de saida (fora do repositorio)')
    args = ap.parse_args()
    # Protecao: o resultado NAO pode ficar dentro do repositorio (evita commit acidental).
    saida = os.path.realpath(args.saida)
    if saida == RAIZ or saida.startswith(RAIZ + os.sep):
        ap.error('--saida tem de ficar FORA do repositorio')
    if not os.path.isdir(os.path.dirname(saida)):
        ap.error('pasta de --saida nao existe')

    import api_client
    import aquecimento_analyzer as aa
    import aquecimento_hrr as hrr

    ids = [a.strip() for a in args.atividades.split(',') if a.strip()]
    resultado = {'modalidade': args.modalidade, 'atividades': []}
    motivos = Counter()
    for aid in ids:
        streams, err = _streams_da_api(api_client, aid)
        if err:
            resultado['atividades'].append({'activity_id': aid, 'status': 'erro_api', 'mensagem': err})
            print(f'{aid}: erro_api: {err}')
            continue
        r = hrr.hrr_por_degrau(streams, args.modalidade, aa.PROTOCOLOS)
        r['activity_id'] = aid
        resultado['atividades'].append(r)
        print(f'{aid}: {r["status"]} · {len(r["degraus"])} degrau(s)')
        for d in r['degraus']:
            for k in ('hrr60_motivo', 'hrr120_motivo'):
                if d[k]:
                    motivos[d[k]] += 1
            fe = d['work']['fim_efetivo']
            print(f"   alvo {d['alvo_w']:>3} W · fim {fe['t_fim_s']} s (desloc. {fe['deslocamento_s']}) · "
                  f"HRR60 {d['hrr60']} · HRR120 {d['hrr120']}"
                  + (f" · motivos: {d['hrr60_motivo'] or ''} {d['hrr120_motivo'] or ''}".rstrip()
                     if (d['hrr60'] is None or d['hrr120'] is None) else ''))
    resultado['contagem_motivos'] = dict(motivos)
    with open(args.saida, 'w', encoding='utf-8') as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f'Saida: {args.saida}')
    print('Motivos:', dict(motivos) or 'nenhum')


if __name__ == '__main__':
    main()
