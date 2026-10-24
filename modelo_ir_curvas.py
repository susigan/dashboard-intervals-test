"""Historico verificavel das curvas de potencia (Etapa 13). Migracao PREPARADA, nao aplicada.

Porque existe: power_curves tem UMA linha por atividade e o upsert sobrescreve tudo a cada sincronizacao.
updated_at so diz a ultima gravacao; nao e historico. Esta tabela guarda, append-only, cada VERSAO do conteudo
de uma curva, com o instante em que a sincronizacao a observou.

Regras:
  * uma linha por alteracao material: a sincronizacao grava SO se o hash do conteudo mudou em relacao a ultima
    versao da mesma atividade. Repetir uma sincronizacao sem alteracoes nao escreve nada (idempotente).
  * conhecido_em = instante da sincronizacao que observou o conteudo. Nunca e uma data passada inventada.
  * versoes anteriores nunca sao alteradas nem apagadas (sem UPDATE/DELETE).
  * valor conhecido no alvo D = a ultima versao com conhecido_em ANTERIOR ao inicio de D (causal).
  * antes do primeiro instante do historico nao ha prova: o alvo e rejeitado (inicio_historico).
    As curvas antigas (anteriores a migracao) so ganham versao na primeira sincronizacao DEPOIS da migracao;
    essa versao e datada desse instante, nunca do esforco.

SQL igual em SQLite e PostgreSQL (TEXT, DOUBLE PRECISION). Placeholders '?' convertidos por sql_para.
"""
import hashlib
import json

import modelo_ir_temporal as tmp
from modelo_ir_sombra import sql_para

TAB = 'ir_curvas_versoes'
IX_TIPO = 'ix_ir_curvas_conhecido'   # filtro 'conhecido_em < corte' das reconstrucoes; PK (activity_id, conhecido_em) cobre o MAX por atividade
COLS = ['activity_id', 'conhecido_em', 'hash', 'tipo', 'data', 'peso', 'secs', 'watts']

DDL = (f"CREATE TABLE IF NOT EXISTS {TAB} ("
       "activity_id TEXT NOT NULL, "
       "conhecido_em TEXT NOT NULL, "
       "hash TEXT NOT NULL, "
       "tipo TEXT, "
       "data TEXT NOT NULL, "
       "peso DOUBLE PRECISION, "
       "secs TEXT NOT NULL, "
       "watts TEXT NOT NULL, "
       "PRIMARY KEY (activity_id, conhecido_em))")
INDICE = f"CREATE INDEX IF NOT EXISTS {IX_TIPO} ON {TAB} (conhecido_em)"


def _numero(x):
    return None if x is None else float(x)


def conteudo_canonico(c):
    """Forma canonica do conteudo de uma curva (mesma em SQLite e PostgreSQL). Numeros em float, listas ordenadas
    como vem da API. Duas leituras do mesmo conteudo dao sempre o mesmo texto, qualquer que seja o tipo numerico."""
    return {
        'tipo': c.get('type', c.get('tipo')),
        'data': str(c.get('date', c.get('data')))[:10],
        'peso': _numero(c.get('weight', c.get('peso'))),
        'secs': [None if s is None else int(s) for s in (c.get('secs') or [])],
        'watts': [_numero(w) for w in (c.get('watts') or [])],
    }


def hash_conteudo(c):
    txt = json.dumps(conteudo_canonico(c), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(txt.encode('utf-8')).hexdigest()


class HistoricoCurvas:
    """executor(sql_com_?, params, fetch) -> linhas. Mesma interface de RegistoTemporal e LojaSombra."""

    def __init__(self, executor, dialeto):
        self._exec = executor
        self.dialeto = dialeto

    def _q(self, sql):
        return sql_para(self.dialeto, sql)

    def tabela_existe(self):
        """Consulta ao catalogo (nao executa SQL que falhe: um erro faz o db reconectar-se)."""
        if self.dialeto == 'sqlite':
            sql = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?"
        else:
            sql = "SELECT table_name FROM information_schema.tables WHERE table_name = %s"
        try:
            rows = self._exec(sql, (TAB,), 'all') or []
        except Exception:  # noqa: BLE001
            return False
        return len(rows) == 1

    def _hashes_atuais(self):
        """{activity_id: hash da ultima versao} numa so consulta."""
        rows = self._exec(self._q(
            f'SELECT t.activity_id, t.hash FROM {TAB} t WHERE t.conhecido_em = ('
            f'SELECT MAX(x.conhecido_em) FROM {TAB} x WHERE x.activity_id = t.activity_id)'), (), 'all') or []
        return {r[0]: r[1] for r in rows}

    def registar(self, linhas, agora):
        """Grava uma versao por curva CUJO conteudo mudou. Devolve {'inseridas', 'inalteradas'}.
        linhas: forma de sync.py (activity_id, type, date, weight, secs, watts)."""
        inst = tmp.instante(agora)
        atuais = self._hashes_atuais()
        inseridas = inalteradas = 0
        for c in linhas or []:
            aid = str(c['activity_id'])
            canon = conteudo_canonico(c)
            h = hash_conteudo(c)
            if atuais.get(aid) == h:
                inalteradas += 1
                continue
            self._exec(self._q(
                f'INSERT INTO {TAB} ({", ".join(COLS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT (activity_id, conhecido_em) DO NOTHING'),
                (aid, inst, h, canon['tipo'], canon['data'], canon['peso'],
                 json.dumps(canon['secs']), json.dumps(canon['watts'])), None)
            atuais[aid] = h
            inseridas += 1
        return {'inseridas': inseridas, 'inalteradas': inalteradas}

    def contar(self):
        """(n_atividades, n_versoes). Usado pelo contrato da API para distinguir 'vazio' de 'disponivel'."""
        row = self._exec(f'SELECT COUNT(DISTINCT activity_id), COUNT(*) FROM {TAB}', (), 'one')
        return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)

    def inicio_historico(self):
        """Primeiro instante em que existe uma versao (None se o historico estiver vazio)."""
        row = self._exec(f'SELECT MIN(conhecido_em) FROM {TAB}', (), 'one')
        return row[0] if row and row[0] is not None else None

    def curvas_conhecidas_antes(self, data_alvo):
        """Curvas (forma de db.load_power_curves) com a ultima versao conhecida ANTES do inicio de data_alvo.
        Curvas cuja primeira versao e posterior ao alvo NAO entram (nao eram conhecidas nesse momento)."""
        corte = tmp._corte_inicio(data_alvo)
        rows = self._exec(self._q(
            f'SELECT t.activity_id, t.tipo, t.data, t.peso, t.secs, t.watts FROM {TAB} t '
            'WHERE t.conhecido_em < ? AND t.conhecido_em = ('
            f'SELECT MAX(x.conhecido_em) FROM {TAB} x WHERE x.activity_id = t.activity_id AND x.conhecido_em < ?) '
            'ORDER BY t.data, t.activity_id'), (corte, corte), 'all') or []
        out = []
        for aid, tipo, dt, peso, secs, watts in rows:
            out.append({'activity_id': aid, 'type': tipo, 'date': str(dt)[:10],
                        'weight': None if peso is None else float(peso),
                        'secs': json.loads(secs), 'watts': json.loads(watts)})
        return out

    def curvas_para_alvo(self, data_alvo):
        """Curvas provadamente conhecidas ANTES do inicio de data_alvo, ou None se o historico nao cobre esse dia.
        Sem historico, ou com o alvo anterior ou igual ao primeiro instante gravado, nao ha prova: None."""
        inicio = self.inicio_historico()
        if inicio is None or tmp._corte_inicio(data_alvo) <= inicio:
            return None
        return self.curvas_conhecidas_antes(data_alvo)

    def versoes(self, activity_id):
        """Todas as versoes de uma atividade, por ordem de conhecimento (auditoria e testes)."""
        rows = self._exec(self._q(
            f'SELECT conhecido_em, hash, tipo, data, peso, secs, watts FROM {TAB} '
            'WHERE activity_id = ? ORDER BY conhecido_em'), (str(activity_id),), 'all') or []
        return [{'conhecido_em': r[0], 'hash': r[1], 'tipo': r[2], 'data': r[3], 'peso': r[4],
                 'secs': json.loads(r[5]), 'watts': json.loads(r[6])} for r in rows]

    def assinatura_estado(self):
        """Resumo do historico (contagem e ultimo instante). Entra na assinatura da sombra."""
        row = self._exec(f'SELECT COUNT(*), MAX(conhecido_em) FROM {TAB}', (), 'one')
        return json.dumps(list(row) if row else None)


# ── invólucros para o fluxo operacional: nunca levantam ─────────────────────────

def _loja_producao():
    import db
    if not db.ENABLED or db.DRIVER is None:
        return None
    return HistoricoCurvas(db._exec, db.DRIVER)


def leitor_producao():
    """(HistoricoCurvas | None, estado). Estado: 'ok' | 'sem_base' | 'tabelas_ausentes' | 'erro'."""
    try:
        loja = _loja_producao()
        if loja is None:
            return None, 'sem_base'
        if not loja.tabela_existe():
            return None, 'tabelas_ausentes'
        return loja, 'ok'
    except Exception:  # noqa: BLE001
        return None, 'erro'


def registar_seguro(linhas, agora):
    """Chamado pela sincronizacao DEPOIS do upsert de power_curves. Falhar nao interrompe a sincronizacao."""
    try:
        loja, estado = leitor_producao()
        if loja is None:
            return {'estado': estado}
        return {'estado': 'ok', **loja.registar(linhas, agora)}
    except Exception as e:  # noqa: BLE001
        return {'estado': 'erro', 'erro': f'{type(e).__name__}: {e}'}
