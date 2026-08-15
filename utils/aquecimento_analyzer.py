"""
AQUECIMENTO_ANALYZER.PY — Detecta padrão de aquecimento e extrai métricas

Padrões:
  - Row/Ski: 5-1-5-1-5 (3 blocos de 5min com power, separados por 1min)
  - Bike: 5-1-5-1-5-1-5-1-5 (5 blocos de 5min)

Detecta automaticamente baseado em modalidade + padrão de watts.
"""

import numpy as np

class AquecimentoAnalyzer:
    def __init__(self, conn=None):
        """Inicializa o analisador.
        
        Args:
            conn: conexão SQLite à BD fisiologia_intervalos
        """
        self.conn = conn
    
    def detectar_padrao(self, modalidade):
        """Detecta se existe padrão de aquecimento para modalidade.
        
        Retorna padrão esperado:
          - Row/Ski: "5-1-5-1-5" (3 blocos)
          - Bike: "5-1-5-1-5-1-5-1-5" (5 blocos)
        """
        if modalidade in ['Row', 'Ski']:
            return "5-1-5-1-5", 3
        elif modalidade == 'Bike':
            return "5-1-5-1-5-1-5-1-5", 5
        else:
            return None, 0
    
    def analisar_atividade(self, activity_id, modalidade):
        """Analisa uma atividade em busca de aquecimento.
        
        Args:
            activity_id: ID da atividade
            modalidade: Row, Ski ou Bike
        
        Returns:
            {
                'detectado': True/False,
                'padrao': "5-1-5-1-5" ou similar,
                'n_blocos': número de blocos encontrados,
                'intervalos_aquecimento': [lista de interval_num],
                'metricas': {
                    'hr_avg': float,
                    'hr_min': float,
                    'hr_max': float,
                    'smo2_avg': float,
                    ...
                },
                'tempo_seg': tempo total de aquecimento,
                'n_intervalos': número de intervalos analisados
            }
        """
        
        padrao_esperado, n_blocos_esperados = self.detectar_padrao(modalidade)
        if not padrao_esperado:
            return {'detectado': False, 'motivo': 'Modalidade não suportada'}
        
        # Buscar intervalos da atividade
        try:
            intervalos = self.conn.execute("""
                SELECT 
                    interval_num, 
                    watts_medio,
                    tempo_intervalo_sec,
                    hr_avg_60s, hr_min_60s, hr_max_60s,
                    smo2_avg_60s, smo2_min_60s, smo2_max_60s,
                    resp_avg_60s, resp_min_60s, resp_max_60s,
                    dfa1_clean
                FROM fisiologia_intervalos
                WHERE activity_id = ? AND valido = 1
                ORDER BY interval_num
            """, (activity_id,)).fetchall()
        except Exception as e:
            return {'detectado': False, 'motivo': f'Erro BD: {str(e)}'}
        
        if not intervalos:
            return {'detectado': False, 'motivo': 'Sem intervalos'}
        
        # Detectar padrão
        # Procura padrão: watts altos (5min) alternando com watts baixos (1min)
        
        # Threshold de watts (adaptativo por modalidade)
        if modalidade == 'Row':
            watts_on_min = 150  # watts mínimo para "ON"
            watts_off_max = 100  # watts máximo para "OFF"
        elif modalidade == 'Ski':
            watts_on_min = 120
            watts_off_max = 80
        else:  # Bike
            watts_on_min = 180
            watts_off_max = 120
        
        # Encontrar sequência 5-1-5-1-5 ou 5-1-5-1-5-1-5-1-5
        padrao_encontrado = self._detectar_sequencia(
            intervalos, watts_on_min, watts_off_max, modalidade
        )
        
        if not padrao_encontrado['detectado']:
            return {'detectado': False, 'motivo': 'Padrão não encontrado'}
        
        # Extrair métricas dos blocos de "ON"
        intervalos_on = padrao_encontrado['intervalos_on']
        
        metricas = self._extrair_metricas(intervalos, intervalos_on)
        
        # Calcular tempo total de aquecimento
        tempo_total = sum(
            int.tuple[2] if int.tuple[2] else 0 
            for int.tuple in [intervalos[i-1] for i in intervalos_on]
        )
        
        return {
            'detectado': True,
            'padrao': padrao_encontrado['padrao'],
            'n_blocos': padrao_encontrado['n_blocos'],
            'intervalos_aquecimento': intervalos_on,
            'metricas': metricas,
            'tempo_aquecimento_seg': tempo_total,
            'n_intervalos': len(intervalos_on),
        }
    
    def _detectar_sequencia(self, intervalos, watts_on_min, watts_off_max, modalidade):
        """Detecta a sequência 5-1-5-1-5 ou 5-1-5-1-5-1-5-1-5."""
        
        # Classificar intervalos como ON (watts alto) ou OFF (watts baixo)
        classificacao = []
        for intervalo in intervalos:
            watts = intervalo[1]
            if watts is None or watts < 10:
                classificacao.append('OFF')
            elif watts >= watts_on_min:
                classificacao.append('ON')
            else:
                classificacao.append('LOW')  # Ambíguo
        
        # Procurar padrão no início da atividade
        # Padrão esperado: ON-OFF-ON-OFF-ON ou ON-OFF-ON-OFF-ON-OFF-ON-OFF-ON
        
        padrao_alvo = 'ON-OFF-ON-OFF-ON' if modalidade in ['Row', 'Ski'] else 'ON-OFF-ON-OFF-ON-OFF-ON-OFF-ON'
        
        # Procurar na sequência de classificação
        for inicio in range(len(classificacao) - 4):
            seq = '-'.join(classificacao[inicio:inicio+5])
            
            if modalidade in ['Row', 'Ski']:
                if seq == 'ON-OFF-ON-OFF-ON':
                    # Encontrado padrão 5-1-5-1-5
                    intervalos_on = [inicio+1, inicio+3, inicio+5]  # interval_num (1-indexed)
                    return {
                        'detectado': True,
                        'padrao': '5-1-5-1-5',
                        'n_blocos': 3,
                        'intervalos_on': intervalos_on
                    }
            else:  # Bike
                # Procurar padrão de 9 intervalos (5-1-5-1-5-1-5-1-5)
                if inicio + 8 < len(classificacao):
                    seq9 = '-'.join(classificacao[inicio:inicio+9])
                    if seq9 == 'ON-OFF-ON-OFF-ON-OFF-ON-OFF-ON':
                        intervalos_on = [inicio+1, inicio+3, inicio+5, inicio+7, inicio+9]
                        return {
                            'detectado': True,
                            'padrao': '5-1-5-1-5-1-5-1-5',
                            'n_blocos': 5,
                            'intervalos_on': intervalos_on
                        }
        
        return {'detectado': False}
    
    def _extrair_metricas(self, intervalos, intervalos_indices):
        """Extrai min/avg/max de HR, SmO2, Resp, DFA1 dos intervalos especificados."""
        
        metricas = {
            'hr_avg': None, 'hr_min': None, 'hr_max': None,
            'smo2_avg': None, 'smo2_min': None, 'smo2_max': None,
            'resp_avg': None, 'resp_min': None, 'resp_max': None,
            'dfa1_avg': None, 'dfa1_min': None, 'dfa1_max': None,
        }
        
        # Recolher valores dos intervalos "ON"
        hr_values = []
        smo2_values = []
        resp_values = []
        dfa1_values = []
        
        for idx in intervalos_indices:
            if idx < 1 or idx > len(intervalos):
                continue
            
            intervalo = intervalos[idx - 1]  # Converter para 0-indexed
            
            # HR (índice 4,5,6 — avg, min, max)
            if intervalo[4] is not None:  # hr_avg_60s
                hr_values.append(intervalo[4])
            
            # SmO2 (índice 7,8,9)
            if intervalo[7] is not None:  # smo2_avg_60s
                smo2_values.append(intervalo[7])
            
            # Respiração (índice 10,11,12)
            if intervalo[10] is not None:  # resp_avg_60s
                resp_values.append(intervalo[10])
            
            # DFA1 (índice 13)
            if intervalo[13] is not None:  # dfa1_clean
                dfa1_values.append(intervalo[13])
        
        # Calcular estatísticas
        if hr_values:
            metricas['hr_avg'] = float(np.mean(hr_values))
            metricas['hr_min'] = float(np.min(hr_values))
            metricas['hr_max'] = float(np.max(hr_values))
        
        if smo2_values:
            metricas['smo2_avg'] = float(np.mean(smo2_values))
            metricas['smo2_min'] = float(np.min(smo2_values))
            metricas['smo2_max'] = float(np.max(smo2_values))
        
        if resp_values:
            metricas['resp_avg'] = float(np.mean(resp_values))
            metricas['resp_min'] = float(np.min(resp_values))
            metricas['resp_max'] = float(np.max(resp_values))
        
        if dfa1_values:
            metricas['dfa1_avg'] = float(np.mean(dfa1_values))
            metricas['dfa1_min'] = float(np.min(dfa1_values))
            metricas['dfa1_max'] = float(np.max(dfa1_values))
        
        return metricas

# Função de acesso global
_analyzer = None

def get_analyzer(conn):
    global _analyzer
    if _analyzer is None:
        _analyzer = AquecimentoAnalyzer(conn)
    return _analyzer
