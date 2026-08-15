"""
APP.PY — Versão SIMPLES com Aquecimento

Importa aquecimento_db_simples (lê/escreve /tmp/aquecimento.db)
Sincroniza com Google Drive automaticamente
"""

from flask import Flask, jsonify, request
from datetime import datetime, timedelta
import json

app = Flask(__name__)

# Importar todos os tabs existentes (manter como estava)
from tabs import tab_metabolismo as tab_metabol
# ... (imports dos outros tabs)

# NOVO: Importar módulo de aquecimento
import sys
sys.path.insert(0, './utils')
import aquecimento_db_simples as aq_db

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
    """Calibra aquecimento com datas específicas.
    
    GET: /api/aquecimento/calibrar?modalidade=Ski&datas=02/01/2024,06/01/2024,...
    POST: {"modalidade": "Ski", "datas": ["02/01/2024", ...]}
    """
    try:
        # Obter dados (GET query params ou POST JSON)
        if request.method == 'GET':
            modalidade = request.args.get('modalidade')
            datas_str = request.args.get('datas', '')
            datas = [d.strip() for d in datas_str.split(',') if d.strip()]
        else:
            data = request.get_json()
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
                
                # Buscar atividades
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
        
        # Processar
        processadas = 0
        aquecimentos_detectados = 0
        detalhes = []
        
        analyzer = AquecimentoAnalyzer(conn)
        
        for activity_id in atividades_para_processar:
            try:
                resultado = analyzer.analisar_atividade(activity_id, modalidade)
                
                if resultado.get('detectado'):
                    # Guardar
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

# ===== ROTAS METABOLISMO (existentes) =====

@app.route('/metabol')
def metabolismo():
    """Renderiza tab Metabolismo."""
    return tab_metabol.render()

@app.route('/api/metabol')
def api_metabolismo():
    """API do tab Metabolismo."""
    return tab_metabol.api_data()

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

# ... (copiar as outras rotas do app original)

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
