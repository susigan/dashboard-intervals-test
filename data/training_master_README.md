# Training Master DB

Banco SQLite mestre do Training Engine.

## Objetivo

A tabela mestre **não decide automaticamente o que o atleta deve treinar agora**.
Ela fornece opções de treino classificadas por:

- limitador fisiológico;
- zona Z1/Z2/Z3;
- tipo/formato de treino;
- modalidade;
- RPE esperado;
- regra dinâmica para potência do WORK;
- regra dinâmica para FC/bpm do WORK;
- monitorização por HR, RF, SmO2, potência, RPE e recovery.

## Regra importante

Os valores de **watts e bpm não ficam fixos na tabela mestre**.

O engine deve calcular em runtime:

1. buscar o resultado VST sincronizado com MOXY para a modalidade;
2. se não houver VST sincronizado, procurar a atividade MOXY mais recente disponível;
3. cruzar BP1/BP2, histórico comparável, eFTP/CP/Pmax e resposta da sessão;
4. calcular a faixa de potência do WORK;
5. calcular a faixa de FC do WORK;
6. apresentar as opções ordenadas por relevância para o limitador encontrado.

RPE é mantido como faixa esperada da regra, mas não substitui os sinais fisiológicos.

## Domínios que não devem ser misturados

- 5-1-5 US: Utilização / Fornecimento / Misto.
- 5-1-5 PC: Cardíaco / Pulmonar / Misto.
- Rede causal: cardíaco / periférico / respiratório / autonômico.

A tabela Training usa o domínio fisiológico do engine e não deve transformar o rótulo US "Utilização" em sistema da Rede Causal.

## Tipos de treino

VO2, Threshold, HIIT, SIT/SIIT, RST e Over/Under são **formatos/tipos de sessão**.
O nome não determina sozinho o limitador.

UT2, UT1, AT, TR e AN são aliases de nomenclatura principalmente para Row/Ski.

SIT/SIIT e RST não devem ser automaticamente classificados como "respiratório" ou "cardíaco"; dependem do protocolo e do padrão fisiológico que o engine quer atacar.

## Tabelas principais

- `training_rules`: regras mestre.
- `training_types`: taxonomia dos tipos de treino.
- `rule_modalities`: vínculo regra × modalidade.
- `limiters`: limitadores fisiológicos.
- `monitoring_rules`: como calcular os valores dinâmicos.
- `aliases`: nomenclatura Row/Ski.
- `workout_library`: espaço para novos treinos adicionados pelo usuário. Está vazio de propósito.
- `sources`: fontes usadas para construir a taxonomia.

## Atualização

O arquivo pode ser versionado/substituído por uma nova versão do SQLite.
Para inspeção humana ou migração, use também `training_master.sql`.
