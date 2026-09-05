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
        'metodos': [
            {'metodo': 'Volume em zona verde',
             'como': ('abaixo do primeiro limiar, horas. É onde o volume '
                      'plasmático e a capilarização se constroem'),
             'sinal_no_smo2': 'SmO2 mantém-se alto e estável',
             'fonte': 'Peikon, Training The Delivery Limited Athlete'},
            {'metodo': 'Intervalos longos perto do limiar',
             'como': ('blocos de 8–20 min entre o primeiro e o segundo '
                      'limiar, com recuperação completa'),
             'sinal_no_smo2': ('SmO2 desce e ESTABILIZA dentro do bloco — '
                               'se descer até ao fim, a carga é alta demais'),
             'fonte': 'Peikon, idem'},
            {'metodo': 'HIIT sistémico (Moxy)',
             'como': ('3–5 min a intensidade submáxima até atingir o SmO2 '
                      'mínimo individual; recuperação até voltar à linha '
                      'de base'),
             'sinal_no_smo2': 'chega ao mínimo em 2–5 min',
             'fonte': 'Moxy HIIT Guide, coluna "Systemic"'},
        ],
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
