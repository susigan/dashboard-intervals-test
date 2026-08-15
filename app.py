"""
APP.PY — VERSÃO COMPLETA COM AQUECIMENTO INTEGRADO
"""

from flask import Flask, jsonify, request, render_template_string
from datetime import datetime, timedelta
import json
import sys

app = Flask(__name__)

# ===== IMPORTS AQUECIMENTO =====
sys.path.insert(0, './utils')
import aquecimento_db_simples as aq_db

# ===== IMPORTS DOS TABS EXISTENTES =====
from tabs import tab_metabol
# Adicionar outros tabs conforme necessário
# from tabs import tab_recovery
# from tabs import tab_wellness
# etc.

# ===== ROTAS AQUECIMENTO =====

@app.route('/api/aquecimento/dados')
def api_aquecimento_dados():
    """Retorna todas as sessões de aquecimento."""
    try:
        sessoes = aq_db.listar_todas()
        return jsonify({
            'status': 'ok',
            'sessoes': sessoes,
            'total': len(sessoes)
        })
    except Exception as e:
        return jsonify({
            'status': 'erro',
            'mensagem': str(e)
        }), 500

@app.route('/api/aquecimento/sessao/<activity_id>')
def api_aquecimento_sessao(activity_id):
    """Retorna dados de aquecimento de uma atividade."""
    try:
        sessao = aq_db.obter_sessao(activity_id)
        
        if not sessao:
            return jsonify({
                'status': 'erro',
                'mensagem': 'Sessão não encontrada'
            }), 404
        
        return jsonify({
            'status': 'ok',
            'sessao': sessao
        })
    except Exception as e:
        return jsonify({
            'status': 'erro',
            'mensagem': str(e)
        }), 500

@app.route('/api/aquecimento/calibrar', methods=['GET', 'POST'])
def api_aquecimento_calibrar():
    """Calibra aquecimento com datas específicas."""
    try:
        # Obter dados (GET ou POST)
        if request.method == 'GET':
            modalidade = request.args.get('modalidade')
            datas_str = request.args.get('datas', '')
            datas = [d.strip() for d in datas_str.split(',') if d.strip()]
        else:
            data = request.get_json()
            if not data:
                return jsonify({'status': 'erro', 'mensagem': 'Body vazio'}), 400
            modalidade = data.get('modalidade')
            datas = data.get('datas', [])
        
        if not modalidade or not datas:
            return jsonify({
                'status': 'erro',
                'mensagem': 'modalidade e datas obrigatórios'
            }), 400
        
        # Importar analisador
        from aquecimento_analyzer import AquecimentoAnalyzer
        import drive_db_fisiologia as ddf
        
        conn = ddf.get_conn()
        atividades_para_processar = []
        
        # Procurar atividades por data + modalidade
        for data_str in datas:
            try:
                if '/' in data_str:
                    data_obj = datetime.strptime(data_str, '%d/%m/%Y')
                else:
                    data_obj = datetime.strptime(data_str, '%Y-%m-%d')
                
                data_inicio = data_obj.date()
                
                query = """
                    SELECT DISTINCT activity_id 
                    FROM fisiologia_intervalos 
                    WHERE modalidade=? AND valido=1 
                    AND DATE(data) = ?
                """
                resultados = conn.execute(query, (modalidade, data_inicio)).fetchall()
                
                for (activity_id,) in resultados:
                    atividades_para_processar.append(activity_id)
            
            except ValueError:
                pass
        
        # Remover duplicatas
        atividades_para_processar = list(set(atividades_para_processar))
        
        if not atividades_para_processar:
            return jsonify({
                'status': 'aviso',
                'mensagem': 'Nenhuma atividade encontrada',
                'total': 0
            }), 200
        
        # Processar cada atividade
        processadas = 0
        aquecimentos_detectados = 0
        detalhes = []
        
        analyzer = AquecimentoAnalyzer(conn)
        
        for activity_id in atividades_para_processar:
            try:
                resultado = analyzer.analisar_atividade(activity_id, modalidade)
                
                if resultado.get('detectado'):
                    dados_aq = {
                        'modalidade': modalidade,
                        'data': datetime.now().isoformat(),
                        'padrao_detectado': resultado.get('padrao'),
                        'n_blocos': resultado.get('n_blocos'),
                        'hr_avg': resultado.get('metricas', {}).get('hr_avg'),
                        'hr_min': resultado.get('metricas', {}).get('hr_min'),
                        'hr_max': resultado.get('metricas', {}).get('hr_max'),
                        'smo2_avg': resultado.get('metricas', {}).get('smo2_avg'),
                        'smo2_min': resultado.get('metricas', {}).get('smo2_min'),
                        'smo2_max': resultado.get('metricas', {}).get('smo2_max'),
                        'resp_avg': resultado.get('metricas', {}).get('resp_avg'),
                        'resp_min': resultado.get('metricas', {}).get('resp_min'),
                        'resp_max': resultado.get('metricas', {}).get('resp_max'),
                        'dfa1_avg': resultado.get('metricas', {}).get('dfa1_avg'),
                        'dfa1_min': resultado.get('metricas', {}).get('dfa1_min'),
                        'dfa1_max': resultado.get('metricas', {}).get('dfa1_max'),
                        'tempo_aquecimento_seg': resultado.get('tempo_aquecimento_seg'),
                        'n_intervalos_analisados': resultado.get('n_intervalos'),
                    }
                    aq_db.salvar_sessao(activity_id, dados_aq)
                    aquecimentos_detectados += 1
                
                processadas += 1
                detalhes.append({
                    'activity_id': activity_id,
                    'detectado': resultado.get('detectado'),
                    'status': 'ok'
                })
            
            except Exception as e:
                detalhes.append({
                    'activity_id': activity_id,
                    'erro': str(e),
                    'status': 'erro'
                })
        
        return jsonify({
            'status': 'calibracao_completa',
            'total_solicitadas': len(atividades_para_processar),
            'processadas': processadas,
            'aquecimentos_detectados': aquecimentos_detectados,
            'detalhes': detalhes
        })
    
    except Exception as e:
        import traceback
        return jsonify({
            'status': 'erro',
            'mensagem': str(e),
            'trace': traceback.format_exc()
        }), 500

# ===== ROTAS METABOLISMO =====

@app.route('/metabol')
def metabolismo():
    """Renderiza tab Metabolismo."""
    return tab_metabol.render()

@app.route('/api/metabol')
def api_metabolismo():
    """API do tab Metabolismo."""
    return tab_metabol.api_data()

@app.route('/api/fisiologia/perfil_robusto/<modalidade>')
def api_fisiologia_perfil_robusto(modalidade):
    """API: Perfil metabolico robusto por modalidade."""
    try:
        import drive_db_fisiologia as ddf
        conn = ddf.get_conn()
        
        largura_bin = int(request.args.get('largura_bin', 50))
        hr_agg = request.args.get('hr', 'max')
        resp_agg = request.args.get('resp', 'avg')
        smo2_agg = request.args.get('smo2', 'min')
        dfa1_agg = request.args.get('dfa1', 'avg')
        
        sql = f"""
            SELECT 
                ROUND(watts_medio, -CAST(LOG10(CAST(? AS FLOAT)) AS INT)) as watts_bin,
                COUNT(*) as n,
                ROUND(AVG(hr_{hr_agg}_60s), 1) as hr_{hr_agg},
                ROUND(AVG(resp_{resp_agg}_60s), 1) as resp_{resp_agg},
                ROUND(AVG(smo2_{smo2_agg}_60s), 1) as smo2_{smo2_agg},
                ROUND(AVG(dfa1_{dfa1_agg}_60s), 3) as dfa1_{dfa1_agg}
            FROM fisiologia_intervalos
            WHERE modalidade = ? AND valido = 1
            GROUP BY watts_bin
            ORDER BY watts_bin
        """
        
        resultados = conn.execute(sql, (largura_bin, modalidade)).fetchall()
        
        return jsonify({
            'status': 'ok',
            'modalidade': modalidade,
            'dados': [dict(zip([desc[0] for desc in conn.execute(sql, (largura_bin, modalidade)).description], r)) for r in resultados]
        })
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

@app.route('/api/fisiologia/evolucao_robusta')
def api_fisiologia_evolucao_robusta():
    """API: Evolução temporal da métrica."""
    try:
        import drive_db_fisiologia as ddf
        conn = ddf.get_conn()
        
        modalidade = request.args.get('modalidade', 'Row')
        metrica = request.args.get('metrica', 'hr')
        agregacao = request.args.get('agregacao', 'max')
        watts_min = int(request.args.get('watts_min', 0))
        watts_max = int(request.args.get('watts_max', 500))
        
        sql = f"""
            SELECT 
                DATE(data) as data,
                ROUND(AVG({metrica}_{agregacao}_60s), 1) as valor_medio,
                ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP(ORDER BY {metrica}_{agregacao}_60s), 1) as p25,
                ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP(ORDER BY {metrica}_{agregacao}_60s), 1) as p50,
                ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP(ORDER BY {metrica}_{agregacao}_60s), 1) as p75,
                COUNT(*) as n
            FROM fisiologia_intervalos
            WHERE modalidade = ? AND valido = 1
            AND watts_medio BETWEEN ? AND ?
            GROUP BY DATE(data)
            ORDER BY data DESC
        """
        
        resultados = conn.execute(sql, (modalidade, watts_min, watts_max)).fetchall()
        
        return jsonify({
            'status': 'ok',
            'modalidade': modalidade,
            'metrica': metrica,
            'agregacao': agregacao,
            'dados': [dict(zip([desc[0] for desc in conn.execute(sql, (modalidade, watts_min, watts_max)).description], r)) for r in resultados]
        })
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

@app.route('/api/fisiologia/processar')
def api_fisiologia_processar():
    """Processa lote de atividades com worker."""
    try:
        import fisiologia_worker
        n = request.args.get('n', 10, type=int)
        resultado = fisiologia_worker.processar_lote(n)
        return jsonify(resultado)
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

@app.route('/api/fisiologia/dinamica_resposta')
def api_fisiologia_dinamica_resposta():
    """API: Dinâmica de resposta (lag/recovery)."""
    try:
        import drive_db_fisiologia as ddf
        conn = ddf.get_conn()
        
        modalidade = request.args.get('modalidade', 'Row')
        metrica = request.args.get('metrica', 'hr')
        fase = request.args.get('fase', 'lag')
        largura_bin = int(request.args.get('largura_bin', 50))
        min_n = int(request.args.get('min_n', 15))
        
        col_name = f"{fase}_{metrica}_50" if fase == 'lag' else f"{fase}_{metrica}_75"
        
        sql = f"""
            SELECT 
                ROUND(watts_medio, -CAST(LOG10(CAST(? AS FLOAT)) AS INT)) as watts_bin,
                COUNT(*) as n,
                ROUND(AVG({col_name}), 1) as valor_medio,
                ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP(ORDER BY {col_name}), 1) as p25,
                ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP(ORDER BY {col_name}), 1) as p50,
                ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP(ORDER BY {col_name}), 1) as p75
            FROM fisiologia_intervalos
            WHERE modalidade = ? AND valido = 1 AND {col_name} IS NOT NULL
            GROUP BY watts_bin
            HAVING COUNT(*) >= ?
            ORDER BY watts_bin
        """
        
        resultados = conn.execute(sql, (largura_bin, modalidade, min_n)).fetchall()
        
        return jsonify({
            'status': 'ok',
            'modalidade': modalidade,
            'metrica': metrica,
            'fase': fase,
            'dados': [dict(zip([desc[0] for desc in conn.execute(sql, (largura_bin, modalidade, min_n)).description], r)) for r in resultados]
        })
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500

# ===== HEALTH CHECK =====

@app.route('/health')
def health():
    """Health check."""
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

# ===== RUN =====

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
