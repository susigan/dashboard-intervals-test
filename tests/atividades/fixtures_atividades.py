"""Fixtures FICTICIAS para a exportacao CSV da aba Atividades. Nenhum dado real."""
from export import CAMPOS_EXCLUIDOS

def atividades_fixture():
    base = [
        {  # zeros explicitos, campo personalizado, nome com virgula/aspas/quebra, derivados e segredos
            'id': 'i100001', 'start_date_local': '2024-03-10T07:00:00', 'start_date': '2024-03-10T06:00:00Z',
            'type': 'VirtualRide', 'name': 'Treino "Limiar", série 2\nlinha dois', 'source': 'ZWIFT',
            'moving_time': 3600, 'elapsed_time': 3700, 'icu_training_load': 0, 'trimp': 0.0,
            'icu_rpe': 5, 'icu_joules': 123400.0, 'average_heartrate': 150, 'max_heartrate': 170,
            'tags': ['Lactato'], 'Aerobic': 12.5, 'SS': 0.0,
            '_kj': 123.4, '_type_norm': 'Bike', 'oauth_client_id': 'cliente-fic',
            'access_token': 'SEGREDO-FICTICIO', 'sub_type': None,
        },
        {  # null e ausente distintos de zero; formula no nome
            'id': 'i100002', 'start_date_local': '2024-03-11T06:30:00', 'type': 'Run',
            'name': '=HYPERLINK("http://exemplo.invalido","clique")', 'moving_time': 2400,
            'icu_training_load': 44, 'trimp': 47.39868, 'tags': None,
        },
        {  # tags com barra vertical; nomes com sinais; telefone fictiticio
            'id': 'i100003', 'start_date_local': '2024-05-02T18:00:00', 'type': 'WeightTraining',
            'name': '-- Força, ação', 'moving_time': 1800, 'icu_training_load': 20,
            'tags': ['a|b', 'Teste'], 'telefone_fic': '+00 000 000 000',
        },
        {  # trimp ausente da chave; datas com acento no nome
            'id': 'i100004', 'start_date_local': '2025-01-15T09:00:00', 'type': 'AlpineSki',
            'name': 'Ski "São Bento" 1º dia', 'moving_time': 5400, 'icu_training_load': 37,
            'icu_rpe': None,
        },
        {  # distancia e remo; sem tags
            'id': 'i100005', 'start_date_local': '2025-06-30T05:45:00', 'type': 'Rowing',
            'name': 'Remo ☃ ok', 'moving_time': 2000, 'distance': 5000.0, 'icu_training_load': 30,
        },
    ]
    # campos excluidos por regra entram nos dados de entrada para provar a exclusao
    for chave in CAMPOS_EXCLUIDOS:
        base[0][chave] = 300
    # volume: 1200 atividades para provar que nao ha corte por pagina
    for k in range(1200):
        base.append({
            'id': f'i2{k:06d}', 'start_date_local': f'2023-{(k % 12) + 1:02d}-{(k % 27) + 1:02d}T08:00:00',
            'type': 'VirtualRide' if k % 3 == 0 else ('Run' if k % 3 == 1 else 'Rowing'),
            'name': f'Treino {k}', 'moving_time': 1000 + k, 'icu_training_load': k % 90,
            'trimp': float(k % 50) + 0.5, 'tags': ['Base'] if k % 2 else None,
        })
    return base
