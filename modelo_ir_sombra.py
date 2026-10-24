"""Modo SOMBRA do classificador A/B/C. Nunca escreve a versao operacional nem promove.

Persistencia: tabela PROPRIA modelo_ir_confianca_tentativas (append-only, criada pela migracao
modelo_ir_confianca_migracao). Nunca toca em modelo_ir_ajustes, que ultimo_ajuste_ir() le como versao atual.

Motor: o mesmo SQL funciona em SQLite e PostgreSQL. Placeholders '?' sao convertidos por sql_para(dialeto).
  INSERT ... ON CONFLICT (chave) DO NOTHING RETURNING assinatura_dados
  -> linha devolvida = esta execucao gravou; nenhuma = outra ja gravou a mesma chave (atomico no motor).

Concorrencia (risco residual, documentado): NAO ha coordenacao entre processos. Dois processos podem avaliar a mesma
chave ao mesmo tempo (duplicacao de CALCULO). A persistencia continua correta: a chave primaria
(modalidade, duracao, assinatura, configuracao, politica) e o INSERT ... ON CONFLICT DO NOTHING garantem uma linha por
versao; o perdedor devolve 'concorrente_ja_gravou' e nao escreve. Nenhuma escrita operacional depende deste caminho.

Campos separados (decisao guardada em colunas proprias): esquema, nivel (A/B/C), estado, validada,
confianca_parametros (alta|media|baixa|restrito|nao_medido), confianca_previsao (alta|media|baixa|nula),
motivos (C), limitacoes (B). 'decisao' guarda o JSON completo para reconstruir a comparacao.
"""
import hashlib
import json
from datetime import datetime

import modelo_ir_confianca as mic

NOME_TABELA = 'modelo_ir_confianca_tentativas'
COLUNAS = ['modalidade', 'duracao_s', 'assinatura_dados', 'configuracao', 'politica_hash', 'tentado_em', 'origem',
           'esquema', 'nivel', 'estado', 'validada', 'confianca_parametros', 'confianca_previsao',
           'motivos', 'limitacoes', 'teria_promovido', 'acao_proposta', 'motivo_acao',
           'versao_operacional_ref', 'resultado', 'decisao']
CHAVE = ('modalidade', 'duracao_s', 'assinatura_dados', 'configuracao', 'politica_hash')

DDL = f"""CREATE TABLE IF NOT EXISTS {NOME_TABELA} (
    modalidade TEXT NOT NULL,
    duracao_s INTEGER NOT NULL,
    assinatura_dados TEXT NOT NULL,
    configuracao TEXT NOT NULL,
    politica_hash TEXT NOT NULL,
    tentado_em TEXT NOT NULL,
    origem TEXT NOT NULL,
    esquema TEXT NOT NULL,
    nivel TEXT NOT NULL,
    estado TEXT NOT NULL,
    validada INTEGER NOT NULL,
    confianca_parametros TEXT NOT NULL,
    confianca_previsao TEXT NOT NULL,
    motivos TEXT NOT NULL,
    limitacoes TEXT NOT NULL,
    teria_promovido INTEGER NOT NULL,
    acao_proposta TEXT NOT NULL,
    motivo_acao TEXT NOT NULL,
    versao_operacional_ref TEXT,
    resultado TEXT NOT NULL,
    decisao TEXT NOT NULL,
    PRIMARY KEY (modalidade, duracao_s, assinatura_dados, configuracao, politica_hash)
)"""

SQL_INSERIR = (f"INSERT INTO {NOME_TABELA} ({', '.join(COLUNAS)}) VALUES ({', '.join('?' * len(COLUNAS))}) "
               f"ON CONFLICT ({', '.join(CHAVE)}) DO NOTHING RETURNING assinatura_dados")
SQL_EXISTE = (f"SELECT 1 FROM {NOME_TABELA} WHERE modalidade=? AND duracao_s=? AND assinatura_dados=? "
              f"AND configuracao=? AND politica_hash=?")
SQL_ULTIMA_TEORICA = (f"SELECT decisao FROM {NOME_TABELA} WHERE modalidade=? AND duracao_s=? AND teria_promovido=1 "
                      f"ORDER BY tentado_em DESC, assinatura_dados DESC LIMIT 1")


def sql_para(dialeto, sql):
    """'?' -> '%s' em PostgreSQL. Idempotente: SQL ja convertido passa sem alteracao."""
    return sql.replace('?', '%s') if dialeto == 'postgres' else sql


def assinatura_dados(obs, carga_sig, p0_ref_sig, configuracao_id, politica_hash):
    """Identidade das entradas (ordem irrelevante). carga_sig deve incluir TODAS as entradas auxiliares
    (carga, historico de deteccao, revisoes): qualquer mudanca nelas muda a assinatura."""
    linhas = sorted((str(o['test_id']), str(o['data']), round(float(o['watts']), 6)) for o in obs)
    conteudo = json.dumps({'obs': linhas, 'carga': carga_sig, 'p0': p0_ref_sig, 'cfg': configuracao_id,
                           'pol': politica_hash}, sort_keys=True)
    return hashlib.sha256(conteudo.encode('utf-8')).hexdigest()[:24]


class LojaSombra:
    """executor(sql_com_?, params, fetch) -> linhas. fetch in (None, 'one', 'all'). Uma chamada = uma transacao
    (o executor e responsavel pelo commit: db._exec faz autocommit; as lojas de teste fazem commit por chamada)."""

    def __init__(self, executor, dialeto):
        self._exec = executor
        self.dialeto = dialeto

    def _q(self, sql):
        return sql_para(self.dialeto, sql)

    def criar(self):
        """Apenas para testes/migracao revista. O modo sombra em producao NAO cria a tabela."""
        self._exec(DDL, (), None)

    def tabela_existe(self):
        try:
            self._exec(f'SELECT 1 FROM {NOME_TABELA} WHERE 1=0', (), 'one')
            return True
        except Exception:
            return False

    def existe(self, mod, dur, assinatura, cfg, ph):
        return self._exec(self._q(SQL_EXISTE), (mod, int(dur), assinatura, cfg, ph), 'one') is not None

    def ultima_teorica(self, mod, dur):
        row = self._exec(self._q(SQL_ULTIMA_TEORICA), (mod, int(dur)), 'one')
        return json.loads(row[0]) if row else None

    def inserir(self, linha):
        """True se esta execucao gravou; False se outra execucao ja gravou a mesma chave."""
        params = tuple(linha[c] for c in COLUNAS)
        return self._exec(self._q(SQL_INSERIR), params, 'one') is not None

    def linhas(self):
        return self._exec(f'SELECT * FROM {NOME_TABELA}', (), 'all') or []


def loja_sqlite(caminho):
    import sqlite3

    def executor(sql, params, fetch):
        con = sqlite3.connect(caminho, timeout=30)
        try:
            with con:
                cur = con.execute(sql, params)
                if fetch == 'one':
                    return cur.fetchone()
                if fetch == 'all':
                    return cur.fetchall()
                return None
        finally:
            con.close()
    return LojaSombra(executor, 'sqlite')


def loja_postgres(dsn):
    import psycopg2

    def executor(sql, params, fetch):
        con = psycopg2.connect(dsn)
        try:
            with con:  # commit ao sair sem erro; rollback em excecao
                with con.cursor() as cur:
                    cur.execute(sql_para('postgres', sql), params)
                    if fetch == 'one':
                        return cur.fetchone()
                    if fetch == 'all':
                        return cur.fetchall()
                    return None
        finally:
            con.close()
    return LojaSombra(executor, 'postgres')


def executar_sombra(loja, mod, dur, obs, carga_sig, p0_ref_sig, configuracao_id, avaliar, origem='sync',
                    versao_operacional_ref=None, agora=None, pol=None):
    """Passo de sombra. 'avaliar' (sem argumentos) devolve a avaliacao no esquema correto e so e chamado quando a
    chave ainda nao foi avaliada. Nunca grava versao operacional."""
    pol = pol or mic.POLITICA
    ph = mic._hash_politica(pol)
    sig = assinatura_dados(obs, carga_sig, p0_ref_sig, configuracao_id, ph)
    if loja.existe(mod, dur, sig, configuracao_id, ph):
        return {'acao_sombra': 'sem_trabalho', 'assinatura_dados': sig, 'avaliou': False,
                'escrito_operacional': False}
    erro = None
    try:
        aval = avaliar()
    except Exception as e:  # falha de ajuste/avaliacao: candidato rejeitado, sem promocao
        erro = f'{type(e).__name__}: {e}'
        aval = {'esquema': 'cortes_treino_teste', 'configuracao': {'id': configuracao_id, 'familia': 'completo',
                                                                    'fixos': {}},
                'n_observacoes': len(obs), 'falha_ajuste': True, 'suspeita_fuga': False,
                'validacao_causal_verificada': True, 'cortes': []}
    dec = mic.classificar(aval, pol)
    if erro:
        dec['motivos'] = [f'falha_avaliacao: {erro}'] + dec['motivos']
    # Contagem de rejeicoes por categoria e natureza da avaliacao (vazio/None em falha de avaliacao).
    dec['rejeicoes_por_categoria'] = aval.get('rejeicoes_por_categoria') or {}
    dec['natureza'] = aval.get('natureza')
    dec['historico_temporal'] = aval.get('historico_temporal')
    atual = loja.ultima_teorica(mod, dur)
    comp = mic.decidir_atualizacao(atual, dec)
    teria = 1 if comp['acao'] == 'promover' else 0
    agora = agora or datetime.now().isoformat(timespec='microseconds')
    linha = {'modalidade': mod, 'duracao_s': int(dur), 'assinatura_dados': sig, 'configuracao': configuracao_id,
             'politica_hash': ph, 'tentado_em': agora, 'origem': origem, 'esquema': dec['esquema'],
             'nivel': dec['nivel'], 'estado': dec['estado_operacional'], 'validada': 1 if dec['validada'] else 0,
             'confianca_parametros': dec['confianca_parametros'], 'confianca_previsao': dec['confianca_previsao'],
             'motivos': json.dumps(dec['motivos'], ensure_ascii=False),
             'limitacoes': json.dumps(dec['limitacoes'], ensure_ascii=False),
             'teria_promovido': teria, 'acao_proposta': comp['acao'], 'motivo_acao': comp['motivo'],
             'versao_operacional_ref': versao_operacional_ref,
             'resultado': 'proposta_sombra' if teria else 'registada_sem_promocao',
             'decisao': json.dumps(dec, ensure_ascii=False, default=str, sort_keys=True)}
    inseriu = loja.inserir(linha)
    return {'acao_sombra': 'avaliado' if inseriu else 'concorrente_ja_gravou', 'assinatura_dados': sig,
            'avaliou': True, 'esquema': dec['esquema'], 'nivel': dec['nivel'], 'validada': dec['validada'],
            'confianca_parametros': dec['confianca_parametros'], 'confianca_previsao': dec['confianca_previsao'],
            'motivos': dec['motivos'], 'limitacoes': dec['limitacoes'],
            'acao_proposta': comp['acao'], 'motivo_acao': comp['motivo'], 'teria_promovido': bool(teria),
            'rejeicoes_por_categoria': dec['rejeicoes_por_categoria'], 'natureza': dec['natureza'],
            'historico_temporal': dec['historico_temporal'],
            'escrito_operacional': False, 'erro': erro}


def apos_deteccao_sombra(loja, resultado_deteccao, origem, preparar):
    """Gancho generico: preparar(mod, dur) -> dict(obs, carga_sig, p0_ref_sig, configuracao_id, avaliar,
    versao_operacional_ref) ou None. Nunca levanta. Escreve apenas na tabela de sombra."""
    try:
        unidades = (resultado_deteccao or {}).get('unidades_alteradas') or []
        saida = []
        for mod, dur in unidades:
            try:
                prep = preparar(mod, dur)
                if prep is None:
                    saida.append({'modalidade': mod, 'duracao_s': dur, 'acao_sombra': 'sem_entradas'})
                    continue
                r = executar_sombra(loja, mod, dur, prep['obs'], prep['carga_sig'], prep['p0_ref_sig'],
                                    prep['configuracao_id'], prep['avaliar'], origem=origem,
                                    versao_operacional_ref=prep.get('versao_operacional_ref'))
                saida.append({'modalidade': mod, 'duracao_s': dur, **r})
            except Exception as e:
                saida.append({'modalidade': mod, 'duracao_s': dur, 'acao_sombra': 'erro',
                              'erro': f'{type(e).__name__}: {e}'})
        return {'ok': True, 'unidades': saida, 'escrito_operacional': False}
    except Exception as e:
        return {'ok': False, 'erro': f'{type(e).__name__}: {e}', 'escrito_operacional': False}


ACOES_QUE_DISPARAM = ('ajustado', 'inalterado')  # o filtro real e a assinatura; recalibrar so indica a unidade


def unidades_alteradas(resultado_recalibrar):
    """(modalidade, duracao) das unidades que recalibrar() tratou como ajustadas ou inalteradas (o caminho de
    sombra decide por assinatura). Formato: {'ok': True, 'unidades': {'Bike:180': {'acao': ...}}}."""
    if not isinstance(resultado_recalibrar, dict) or not resultado_recalibrar.get('ok'):
        return []
    out = []
    for chave, info in (resultado_recalibrar.get('unidades') or {}).items():
        if isinstance(info, dict) and info.get('acao') in ACOES_QUE_DISPARAM:
            mod, _, dur = chave.rpartition(':')
            out.append((mod, int(dur)))
    return out
