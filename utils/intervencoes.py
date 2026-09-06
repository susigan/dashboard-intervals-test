"""utils/intervencoes.py — o que treinar, conforme o limitador.

De onde vem cada coisa está marcado. Nada aqui é inventado por mim: são
protocolos publicados, e a fonte de cada um está no campo `fonte`.

O PRINCÍPIO, antes das tabelas
──────────────────────────────
Peikon, na lição das intervenções:

  "the athlete's starting point dictates what intervention will get them
   to their goal outcome (...) This is why cookie cutter programs fail"

E, sobre escolher o protocolo:

  "If the protocol I choose doesn't address the underlying problem
   causing an athlete's limitation, it won't get us the desired result."

Ou seja: a tabela só serve depois de o limitador estar identificado. Dar
um protocolo de entrega a quem tem limitação de utilização não é
ineficiente — é inútil.

FUNDAÇÕES ANTES DE TUDO
───────────────────────
Peikon põe três pré-requisitos antes de qualquer trabalho de sistemas
energéticos: capacidade de movimento, coordenação e respiração.

  "If an athlete cannot comfortably perform all of the relevant movements
   for their sport, they lack coordination (...) or they have trouble
   breathing with movement appropriate mechanics, they need not spend
   time hammering out intensive energy system training."

Isto fica em primeiro lugar de propósito. É a parte que se salta.
"""

FUNDACOES = [
    {'item': 'Capacidade de movimento',
     'porque': ('sem conseguir executar confortavelmente os gestos do '
                'desporto, o trabalho de sistemas energéticos treina um '
                'padrão defeituoso')},
    {'item': 'Coordenação',
     'porque': ('a 5-1-5 detecta isto: SmO2 a SUBIR no último bloco com '
                'carga e FC constantes significa que o músculo medido está '
                'a ser recrutado menos — mudou a forma')},
    {'item': 'Respiração',
     'porque': ('respiração rápida e superficial causa hipocapnia, que '
                'IMITA limitação de utilização. Tratar a técnica antes de '
                'concluir que o músculo não extrai')},
]


# ══════════════════════════════════════════════════════════════════════════
# INTERVENCOES POR LIMITADOR
#
# Cada entrada tem: o que se pretende adaptar, o método, como se sabe que
# está a correr bem (o sinal no SmO2), e a fonte.
# ══════════════════════════════════════════════════════════════════════════

INTERVENCOES = {
    'entrega': {
        'nome': 'Limitação de entrega (cardíaco)',
        'o_que_e': ('o débito não acompanha: o músculo extrai tudo o que '
                    'lhe chega e continua com fome'),
        'sinais': ['SmO2 desce muito e não recupera entre blocos',
                   'THb a descer ao longo da sessão',
                   'FC estabiliza ou desce nos últimos degraus',
                   'SmO2 mínimo de trabalho abaixo de 30%'],
        'adaptacoes_alvo': [
            'fluxo sanguíneo e circulação periférica',
            'volume telediastólico e volume sistólico',
            'hipertrofia ventricular esquerda',
            'coordenação cardio-pulmonar'],
        # Peikon organiza a "endurance basica" em quatro categorias D0-D3,
        # com alvos de FC e de SmO2 explicitos. Sao mais operacionais do
        # que "volume em zona verde": dizem a duracao, a intensidade e o
        # que o SmO2 deve fazer.
        'metodos': [
            {'metodo': 'D0 — regeneração',
             'como': ('intensidade que produz o SmO2 MÁXIMO local. Faixa '
                      'muito estreita: é preciso volume sanguíneo elevado, '
                      'logo alguma intensidade, mas se passar disso o SmO2 '
                      'começa a descer'),
             'alvo_smo2': 'no máximo local de SmO2 — "andar na linha fina"',
             'alvo_fc': None,
             'objectivo': ('não produz adaptação: estimula o sistema '
                           'linfático e leva ao estado parassimpático. É '
                           'recuperação'),
             'precisa_de_moxy': True,
             'fonte': 'Peikon, Training The Delivery Limited Athlete'},

            {'metodo': 'D1 — endurance básica',
             'como': ('20 min a 3–5 h contínuos. Para atletas pesados e '
                      'potentes, intervalos curtos: 40 s de trabalho, 20 s '
                      'de descanso, 40 séries — em vez de 30 min seguidos'),
             'alvo_smo2': ('SmO2 a SUBIR ao longo do intervalo, ou estável '
                           'num máximo local'),
             'alvo_fc': '50–65% da FC máxima',
             'alvo_lactato': 'sem acumulação acima da base',
             'sensacao': ('se não consegue manter conversa fluida, está a '
                          'trabalhar demais'),
             'frequencia': ('várias sessões no mesmo dia ou em dias '
                            'seguidos, sem consequências'),
             'fonte': 'Peikon, idem'},

            {'metodo': 'D2 — endurance moderada',
             'como': ('20 a 180 min contínuos, ou intervalos de 10–30 min, '
                      '2 a 6 séries, com 30–90 s de descanso'),
             'alvo_smo2': 'SmO2 estabilizado entre 40% e 70%',
             'alvo_fc': '65–75% da FC máxima',
             'alvo_lactato': 'muito pouca acumulação acima da base',
             'sensacao': ('~70–75% do esforço; deve conseguir dizer uma '
                          'frase completa'),
             'nota': ('em atletas avançados com historial de trabalho '
                      'contínuo longo, o D2 pode substituir o grosso do D1'),
             'fonte': 'Peikon, idem'},

            {'metodo': 'HIIT sistémico (Moxy)',
             'como': ('3–5 min a intensidade submáxima até atingir o SmO2 '
                      'mínimo individual; recuperação até voltar à linha '
                      'de base'),
             'alvo_smo2': 'chega ao mínimo em 2–5 min',
             'fonte': 'Moxy HIIT Guide, coluna "Systemic"'},
        ],
        'nota_categorias': (
            'o D1 pode substituir o D2 ou o D3 em semanas de descarga. E o '
            'D1 é ESTIMULATIVO — o D2 e o D3 já impõem carga a sério'),
        'nao_fazer': ('mais intensidade não resolve: o músculo já usa tudo '
                      'o que recebe. O travão está a montante'),
    },

    'utilizacao': {
        'nome': 'Limitação de utilização (oxidativa muscular)',
        'o_que_e': ('chega oxigénio mas o músculo não o extrai. Mitocôndrias, '
                    'enzimas oxidativas, recrutamento'),
        'sinais': ['SmO2 de trabalho fica alto mesmo em carga alta (>30%)',
                   'queda fraca durante o trabalho intenso',
                   'recuperação forte ou prolongada nos intervalos'],
        'causas_possiveis': [
            'densidade mitocondrial baixa',
            'alteração da estrutura das fibras',
            'coordenação intra e intermuscular após lesão',
            'sobretreino crónico',
            ('desvio à esquerda da curva de dissociação da hemoglobina por '
             'respiração hipocápnica — ESTA não se trata com treino, '
             'trata-se com técnica respiratória')],
        'metodos': [
            {'metodo': 'HIIT local, O2-dependente',
             'como': ('sprints de menos de 30 s até o SmO2 chegar ao mínimo '
                      'individual; recuperação até voltar à linha de base. '
                      'Terminar ~10 pontos antes do mínimo, por causa do '
                      'atraso de medição'),
             'sinal_no_smo2': 'queda rápida e forte até ao mínimo',
             'fonte': 'Moxy HIIT Guide, coluna "Local O2 dependent"'},
            {'metodo': 'HIIT local, O2-independente',
             'como': ('30–120 s, mantendo o SmO2 no mínimo o máximo de '
                      'tempo possível. Parar quando o patamar já não se '
                      'sustenta. Recuperar até ao fim da hiperemia'),
             'sinal_no_smo2': 'patamar no mínimo, prolongado',
             'fonte': 'Moxy HIIT Guide, coluna "Local O2 independent"'},
            {'metodo': 'Força específica, cadência baixa e torque alto',
             'como': 'obriga o músculo a puxar oxigénio',
             'sinal_no_smo2': 'queda mais profunda à mesma potência',
             'fonte': 'Peikon, Training The Utilization Limited Athlete'},
        ],
        'nao_fazer': ('antes de concluir que é utilização, descartar '
                      'hipocapnia: respiração rápida e superficial produz '
                      'exactamente o mesmo padrão'),
    },

    'respiratorio': {
        'nome': 'Limitação respiratória (pulmonar)',
        'o_que_e': ('a ventilação não chega: CO2 acumula-se (hipercapnia) '
                    'ou o oxigénio não é trocado (EIAH)'),
        'sinais': ['THb de repouso e de trabalho a SUBIR ao longo da sessão',
                   'atraso do SmO2 face ao THb na recuperação',
                   'atraso que cresce nas cargas mais altas'],
        'metodos': [
            {'metodo': 'Treino de dessaturação prolongada (EDT)',
             'como': (
                 'potência fixa e rápida mas NÃO máxima: ~80–85% do máximo '
                 'no remo, 60–65% na bicicleta de ar, 85–90% no SkiErg. '
                 'Manter até o SmO2 deixar de descer e assentar num mínimo '
                 'local — ou seja, até a taxa de queda chegar a zero'),
             'sinal_no_smo2': ('a ΔSmO2 passa de negativa a ~0 %/s. É esse '
                               'o momento de parar o intervalo'),
             'porque_funciona': (
                 'acumula tempo a alta percentagem do VO2pico sem o volume '
                 'que os músculos e articulações não tolerariam'),
             'fonte': 'Peikon, Extended Desaturation Training'},
            {'metodo': 'Trabalho respiratório dedicado',
             'como': 'SpiroTiger ou equivalente, fora do treino',
             'sinal_no_smo2': '—',
             'fonte': 'Peikon, Training The Respiratory Limited Athlete'},
        ],
        'fundacoes_estruturais': (
            'posição das costelas, do diafragma e da pélvis. Peikon põe '
            'estas antes de qualquer intervenção de sistemas energéticos '
            'no atleta respiratoriamente limitado'),
    },
}


# ══════════════════════════════════════════════════════════════════════════
# ZONAS POR SmO2 — quatro zonas
#
# Peikon, Zoning Energy System Training with NIRS. Ao contrário das zonas
# por potência ou FC, estas definem-se pelo COMPORTAMENTO do sinal, e por
# isso adaptam-se ao dia.
# ══════════════════════════════════════════════════════════════════════════

ZONAS_SMO2 = [
    {'zona': 'Recuperação activa',
     'sinal': 'oferta excede procura — o SmO2 SOBE',
     'o_que_acontece': 'nenhum sistema é stressado, o limitador não é tocado',
     'usar_para': 'recuperação, aquecimento, aquisição de técnica'},
    {'zona': 'Endurance estrutural',
     'sinal': 'oferta e procura equilibradas — SmO2 estável',
     'o_que_acontece': ('o limitador é estimulado mas não excedido; esforço '
                        'sub-limiar'),
     'usar_para': 'o grosso do volume'},
    {'zona': 'Endurance funcional',
     'sinal': 'procura excede oferta — SmO2 desce e assenta mais baixo',
     'o_que_acontece': ('o limitador ficou sobrecarregado e entram os '
                        'padrões de compensação'),
     'usar_para': 'trabalho de limiar'},
    {'zona': 'Alta intensidade',
     'sinal': 'procura excede muito a oferta — queda rápida',
     'o_que_acontece': 'limitador E compensadores sobrecarregados',
     'usar_para': 'HIIT, SIT'},
]

FONTE_ZONAS = 'Peikon, Zoning Energy System Training with NIRS'


# ══════════════════════════════════════════════════════════════════════════
# LINHAS DE BASE — o que permite auto-regular
# ══════════════════════════════════════════════════════════════════════════

LINHAS_DE_BASE = {
    'recuperacao': {
        'o_que_e': ('valor estável de SmO2 durante um período de descanso, '
                    'depois de um aquecimento completo'),
        'usar_para': ('saber quando o intervalo seguinte pode começar, e '
                      'quando parar a sessão — se já não voltar à linha de '
                      'base, acabou')},
    'desempenho': {
        'o_que_e': ('o SmO2 MÍNIMO atingido numa série depois do '
                    'aquecimento'),
        'usar_para': ('saber quando parar: se já não desce até à linha de '
                      'base de desempenho apesar do esforço máximo, a '
                      'sessão terminou. Uma variação de 5–10% é normal')},
    'fonte': 'Peikon, Offensive Load Management Strategies',
    'aviso': ('estas linhas mudam com o dia. É esse o ponto: são o que '
              'permite adaptar a sessão ao estado de hoje em vez de seguir '
              'watts fixos de um teste de há três meses'),
}


def para_limitador(chave):
    """Devolve a intervenção para um limitador, aceitando vários nomes."""
    mapa = {
        'fornecimento': 'entrega', 'supply': 'entrega',
        'cardíaco': 'entrega', 'cardiaco': 'entrega', 'entrega': 'entrega',
        'periférico': 'utilizacao', 'periferico': 'utilizacao',
        'utilização': 'utilizacao', 'utilizacao': 'utilizacao',
        'utilization': 'utilizacao', 'muscular': 'utilizacao',
        'pulmonar': 'respiratorio', 'respiratório': 'respiratorio',
        'respiratorio': 'respiratorio', 'pulmonary': 'respiratorio',
    }
    k = mapa.get(str(chave).strip().lower())
    return INTERVENCOES.get(k) if k else None


def tudo():
    return {
        'fundacoes': FUNDACOES,
        'intervencoes': INTERVENCOES,
        'zonas_smo2': ZONAS_SMO2,
        'fonte_zonas': FONTE_ZONAS,
        'linhas_de_base': LINHAS_DE_BASE,
        'aviso': (
            'a tabela só serve depois de o limitador estar identificado, e '
            'um limitador de UMA sessão não é um limitador. Repetir em '
            'várias sessões antes de reorganizar o treino'),
        'principio': (
            'Peikon: "the athlete\'s starting point dictates what '
            'intervention will get them to their goal outcome. This is why '
            'cookie cutter programs fail"'),
    }


# ══════════════════════════════════════════════════════════════════════════
# TRADUZIR OS ALVOS PARA OS WATTS E BPM DESTE ATLETA
#
# As categorias vêm em % da FC máxima. Sem isto, o utilizador tem de fazer
# a conta de cabeça — e a % de FCmax é justamente o tipo de âncora que
# este dashboard evita: varia com o dia, com o calor, com a fadiga.
#
# Onde houver limiar medido, prefere-se o limiar. A % da FCmax só entra
# quando não há alternativa, e vai marcada como tal.
# ══════════════════════════════════════════════════════════════════════════

def _pct(v, lo, hi):
    return (round(v * lo), round(v * hi)) if v else None


def alvos_para_atleta(fc_max=None, lt1_w=None, lt2_w=None,
                      lt1_bpm=None, lt2_bpm=None, smo2_max=None):
    """Converte os alvos das categorias para valores concretos.

    Devolve, por categoria, o que se pode dizer com o que existe — e diz
    o que falta quando não dá.
    """
    fora = {}

    # ── D0: só com Moxy ──────────────────────────────────────────────
    fora['D0'] = {
        'nome': 'D0 — regeneração',
        'watts': None,
        'bpm': None,
        'smo2': (f'manter no máximo local (~{round(smo2_max)}% nesta '
                 'modalidade)' if smo2_max else 'no máximo local de SmO2'),
        'so_com_moxy': True,
        'porque': ('a intensidade certa é a que produz o SmO2 mais alto, e '
                   'isso não se sabe sem o sensor — é a definição da '
                   'categoria, não uma limitação do cálculo'),
    }

    # ── D1: abaixo do primeiro limiar ────────────────────────────────
    d1 = {'nome': 'D1 — endurance básica', 'ancora': None}
    if lt1_w:
        # o D1 fica claramente abaixo do LT1: é onde o SmO2 ainda sobe
        d1['watts'] = (round(lt1_w * 0.60), round(lt1_w * 0.90))
        d1['ancora'] = 'LT1 medido'
    if lt1_bpm:
        d1['bpm'] = (round(lt1_bpm * 0.82), round(lt1_bpm * 0.97))
        d1['ancora'] = d1['ancora'] or 'LT1 medido'
    if not d1.get('bpm') and fc_max:
        d1['bpm'] = _pct(fc_max, 0.50, 0.65)
        d1['ancora'] = '% da FC máxima (sem LT1 medido)'
    d1['smo2'] = 'a SUBIR ao longo do intervalo, ou estável no máximo'
    d1['duracao'] = '20 min a 3–5 h'
    d1['teste'] = 'consegue manter conversa fluida'
    fora['D1'] = d1

    # ── D2: entre os dois limiares, na metade de baixo ───────────────
    d2 = {'nome': 'D2 — endurance moderada', 'ancora': None}
    if lt1_w and lt2_w:
        d2['watts'] = (round(lt1_w * 0.95), round(lt1_w + (lt2_w - lt1_w) * 0.5))
        d2['ancora'] = 'entre LT1 e metade do caminho para o LT2'
    elif lt1_w:
        d2['watts'] = (round(lt1_w * 0.95), round(lt1_w * 1.15))
        d2['ancora'] = 'LT1 medido (sem LT2)'
    if lt1_bpm and lt2_bpm:
        d2['bpm'] = (round(lt1_bpm), round(lt1_bpm + (lt2_bpm - lt1_bpm) * 0.5))
        d2['ancora'] = d2['ancora'] or 'entre LT1 e LT2 medidos'
    if not d2.get('bpm') and fc_max:
        d2['bpm'] = _pct(fc_max, 0.65, 0.75)
        d2['ancora'] = '% da FC máxima (sem limiares medidos)'
    d2['smo2'] = 'estabilizado entre 40% e 70%'
    d2['duracao'] = '20–180 min, ou 2–6 × 10–30 min com 30–90 s de pausa'
    d2['teste'] = 'consegue dizer uma frase completa'
    fora['D2'] = d2

    faltam = []
    if not (lt1_w or lt1_bpm):
        faltam.append('LT1 medido')
    if not (lt2_w or lt2_bpm):
        faltam.append('LT2 medido')
    if not fc_max:
        faltam.append('FC máxima')
    if smo2_max is None:
        faltam.append('SmO2 máximo de uma sessão Moxy')

    return {
        'categorias': fora,
        'faltam': faltam,
        'nota': ('onde há limiar medido, os alvos ancoram nele. A % da FC '
                 'máxima só entra quando não há alternativa, e fica '
                 'assinalada — é o tipo de âncora que varia com o dia, o '
                 'calor e a fadiga'),
        'aviso': ('as fronteiras entre categorias não são linhas: são '
                  'transições. O que decide é o comportamento do SmO2, não '
                  'o número'),
    }
