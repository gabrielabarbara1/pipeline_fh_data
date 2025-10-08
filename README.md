# Mini Data Pipeline com a API do GitHub

Este projeto implementa um pipeline de dados simplificado (Bronze, Silver, Gold) que busca dados de repositórios populares do GitHub, os processa, normaliza e gera métricas analíticas.

## 🎯 Objetivo do Projeto

O objetivo é demonstrar a construção de um pipeline de dados robusto e modular, seguindo as melhores práticas de engenharia de dados, como a separação em camadas (ingestão, normalização e analytics), tratamento de erros e capacidade de recuperação.

## ✨ Principais Funcionalidades

* **Arquitetura em Camadas:** Organização clara em Bronze (dados brutos), Silver (dados limpos) e Gold (métricas de negócio).
* **Ingestão Resiliente:** Utiliza um sistema de checkpoint para retomar a busca de dados de onde parou, evitando trabalho duplicado.
* **Tolerância a Falhas:** Implementa um mecanismo de *retry* com *backoff* exponencial para lidar com erros de API (como limites de taxa ou instabilidades do servidor).
* **Eficiência de Armazenamento:** Salva os dados processados em formato Parquet, otimizado para performance e análise.
* **Modularidade:** O código é estruturado em funções claras e distintas para cada etapa do pipeline.

## ⚙️ Como Funciona: Detalhes Técnicos

O pipeline é orquestrado pelo script `main.py` e executa três estágios principais:

### 🥉 Camada Bronze (Ingestão de Dados Brutos)

Nesta fase, o pipeline busca dados da API de busca de repositórios do GitHub.

* **Endpoint:** `https://api.github.com/search/repositories`
* **Critérios de Busca:** A consulta é configurada para buscar repositórios com mais de 1000 estrelas (`stars:>1000`), ordenados pelo número de estrelas em ordem decrescente.
* **Checkpoint:** O processo salva a última página buscada com sucesso. Se o pipeline for interrompido e executado novamente, ele continuará a partir da página seguinte, garantindo que nenhum dado seja perdido e evitando reprocessamento desnecessário.
* **Tratamento de Erros:** O pipeline tentará novamente (com tempo de espera crescente) em caso de erros de servidor (`5xx`) ou de limite de taxa (`403`, `429`).
* **Saída:** Os dados brutos de cada página são salvos como arquivos JSON separados em `data/bronze/repositories/YYYY/MM/DD/page_{numero_da_pagina}.json`.

### 🥈 Camada Silver (Limpeza e Normalização)

A camada Silver transforma os dados brutos em um formato tabular, limpo e pronto para análise.

* **Leitura:** Lê todos os arquivos JSON da camada Bronze.
* **Deduplicação:** Remove registros duplicados com base no `id` do repositório, mantendo a versão mais recente com base na data de `updated_at`.
* **Normalização:**
    * Expande o objeto aninhado `owner` em colunas separadas (ex: `owner_id`, `owner_login`).
    * Extrai a chave da licença (`license.key`) para uma nova coluna `license_key`.
* **Saída:** A tabela limpa e normalizada é salva como um único arquivo Parquet em `data/silver/repositories/repositories.parquet`.

### 🥇 Camada Gold (Métricas e Agregação)

A camada final agrega os dados limpos para criar métricas de negócio de alto nível.

* **Leitura:** Carrega o arquivo Parquet da camada Silver.
* **Métricas Calculadas:**
    1.  **Novos Repositórios por Dia:** Contagem de quantos repositórios foram criados em cada data.
    2.  **Média de Estrelas por Dia:** A média de "estrelas" (`stargazers_count`) dos repositórios, agrupada pelo dia de criação.
    3.  **Ranking de Linguagens:** As 5 linguagens de programação mais comuns entre os repositórios.
* **Saída:** As tabelas de métricas são salvas em formato Parquet no diretório `data/gold/`.

### Por que usei o formato Parquet?

Neste projeto, os dados das camadas Silver e Gold são salvos em formato **Parquet** em vez de formatos mais comuns como CSV ou JSON. A escolha foi intencional e baseia-se em duas vantagens principais para pipelines de dados:

1.  **Eficiência e Performance:** O Parquet é um formato de armazenamento colunar. Isso significa que, ao fazer uma consulta, o sistema pode ler apenas as colunas necessárias para a operação, ignorando as demais. No nosso caso, ao criar as métricas da camada Gold, não precisamos de todas as colunas da camada Silver. A leitura se torna muito mais rápida e consome menos recursos computacionais.

2.  **Alta Taxa de Compressão:** Por agrupar dados do mesmo tipo (ex: todos os números da coluna "stars" ficam juntos), o Parquet consegue uma compressão muito mais eficaz. Os arquivos `.parquet` são significativamente menores que seus equivalentes em `.csv` ou `.json`, o que economiza espaço de armazenamento e acelera a transferência de dados.

Em resumo, o Parquet é o padrão da indústria para dados analíticos, pois oferece um desempenho superior e maior eficiência de armazenamento, tornando o pipeline mais robusto e escalável.

## 📂 Estrutura do Projeto

mini-pipeline-github/
├── data/                 # Diretório raiz para os dados (não versionado)
│   ├── bronze/           # Dados brutos da API
│   ├── silver/           # Dados limpos e normalizados
│   ├── gold/             # Tabelas analíticas e métricas
│   └── checkpoints/      # Arquivos de estado para a ingestão
├── run_pipeline.py       # Script principal com a lógica do pipeline
├── requirements.txt      # Dependências do projeto Python
└── README.md             # Este arquivo

## 🚀 Como Executar

### Pré-requisitos

* Python 3.8 ou superior

### Passos

1.  **Clone o repositório:**
    ```bash
    git clone [https://github.com/gabrielabarbara1/pipeline_fh_data.git](https://github.com/gabrielabarbara1/pipeline_fh_data.git)
    cd pipeline_fh_data
    ```

2.  **Crie e ative um ambiente virtual:**
    ```bash
    # Cria o ambiente
    python -m venv venv

    # Ativa no Windows
    venv\Scripts\activate

    # Ativa no Linux/macOS
    source venv/bin/activate
    ```

3.  **Instale as dependências:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Execute o pipeline:**
    ```bash
    python main.py
    ```

Após a execução, os diretórios `data/bronze`, `data/silver` e `data/gold` estarão populados com os arquivos gerados em cada etapa. Você poderá ver o log de execução no terminal, incluindo o ranking das 5 linguagens mais populares ao final.