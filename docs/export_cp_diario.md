# Exportação Excel diária por modalidade (TRIMP, MMP do dia, CP recalculado)

Ferramenta **offline e só de leitura**: `tools/export_cp_diario.py`. Trabalha numa cópia temporária (API de backup do SQLite) da base indicada; não acede a Intervals.icu nem a produção, não grava CP, não toca em `cp_resultados`, P0 nem ajustes do IR.

```
pip install -r requirements-dev.txt          # openpyxl
python tools/export_cp_diario.py --db copia_local.db --out cp_diario.xlsx [--desde 2026-01-01] [--ate AAAA-MM-DD] \
       [--modelo auto|"M2 (WLS-1/t)"|...] [--modo coerente|season|recuo] [--marcos 2026-01-01=2026]
```

## O que é reutilizado (nada de segunda fórmula de CP)
- `api_cp._registos` (curvas por modalidade), `utils/cp_model.pontos_de_curvas` (durações, seleção de MMP, duplicados, janela de season, modos), `utils/cp_model.calcular_cp_completo` (M1/M2/M3, 2p, 3p, SEE%, validação), `config.season_de`, `distribuicao_carga.agregar('trimp')`, `perfil_metabolico._indice_curva/_watts_em` (leitura do MMP com a mesma tolerância).
- `utils/cp_model.py`, `tabs/tab_cp_model.py` e `api_cp.py` **não foram alterados**.

## Regra de cálculo (igual à gravação do CP: `/api/perfil/guardar`)
Para a data D entram só `power_curves` com data ≤ D, na season de D, modo `coerente`, `min_pts=3`, duplicados excluídos, sem override de pmax. O CP só é recalculado (coluna F) em dias com **teste elegível**: o ponto que o CP Model seleciona para alguma duração tem a data D, ou o conjunto de pontos do modelo mudou nesse dia. Dias sem teste ficam com F vazio (não se copia o CP anterior, não se preenche com zero).

Estados: `sem_teste`, `teste_nao_elegivel`, `dados_insuficientes`, `cp_calculado`, `cp_calculado_com_aviso` (o CP Model devolveu avisos de gravidade alta/média, p.ex. `sem_graus_de_liberdade` com 3 pontos), `erro_calculo`.

## Limitações
- Não existe data fiável de deteção/disponibilidade de um teste: usa-se a data da **atividade**. A tabela mostra o que o algoritmo produziria com os dados que hoje existem até D, **não** o que era conhecido em tempo real (um recálculo retroativo de curvas no Intervals não é detetável).
- Marcos de season (`SEASON_START`) vêm da API; offline usa-se o ano civil (`--marcos` permite fornecê-los).
- O CP aqui é **recalculado retrospetivo** — não é o CP confirmado nem o P0 do IR.
- No modo `coerente`, a season só é “completa” se existir também o MMP de 3600 s (que nunca entra no ajuste); o dia em que esse esforço aparece pode mudar o conjunto sem ser um novo MMP elegível (assinalado na observação).

Testes: `python -m pytest tests/pmc/test_export_cp_diario.py`.
