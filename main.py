import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import requests
import pandas as pd


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

ENTITY = "repositories"
BASE_API_URL = "https://api.github.com/search/repositories" 
BASE_DATA_PATH = "data"
PAGES_TO_INGEST = 5  

# --- Camada Bronze: Ingestão ---

def ingest_to_bronze(entity: str, pages_limit: int):
    """
    Ingere dados da API de Busca do GitHub com paginação e retry com backoff.
    Salva os dados brutos na camada Bronze.
    """
    logging.info(f"--- Iniciando Camada Bronze para '{entity}' (usando API de Busca) ---")
    
    today = datetime.now()
    bronze_path = os.path.join(
        BASE_DATA_PATH, "bronze", entity, 
        f"{today.year}", f"{today.month:02d}", f"{today.day:02d}"
    )
    os.makedirs(bronze_path, exist_ok=True)

    headers = {"Accept": "application/vnd.github.v3+json"}

    params = {
        "q": "stars:>1000",
        "sort": "stars",
        "order": "desc",
        "per_page": 100 
    }

    for page_num in range(1, pages_limit + 1):
        params["page"] = page_num
        url = BASE_API_URL

        max_retries = 3
        backoff_factor = 2
        
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    repositories_list = data.get('items', [])
                    
                    if not repositories_list:
                        logging.info("Não há mais dados para buscar.")
                        return

                    file_path = os.path.join(bronze_path, f"page_{page_num}.json")
                    with open(file_path, 'w', encoding='utf-8') as f:
                        json.dump(repositories_list, f, indent=4)
                    
                    logging.info(f"Página {page_num} ingerida e salva em {file_path}")
                    break 

                elif response.status_code == 403: 
                    wait_time = 60 
                    logging.warning(
                        f"Erro 403 (Rate Limit). Aguardando {wait_time} segundos..."
                    )
                    time.sleep(wait_time)

                elif response.status_code in [429, 500, 502, 503, 504]:
                    wait_time = backoff_factor * (2 ** attempt)
                    logging.warning(
                        f"Erro {response.status_code}. Tentando novamente em {wait_time} segundos..."
                    )
                    time.sleep(wait_time)
                else:
                    response.raise_for_status() 

            except requests.exceptions.RequestException as e:
                logging.error(f"Erro de requisição na tentativa {attempt + 1}: {e}")
                if attempt == max_retries - 1:
                    raise 
                time.sleep(backoff_factor * (2 ** attempt))

# --- Camada Silver: Normalização ---

def normalize_to_silver(entity: str):
    """
    Lê os JSONs da camada Bronze, normaliza-os em tabelas (DataFrames),
    deduplica e salva na camada Silver em formato Parquet.
    """
    logging.info(f"--- Iniciando Camada Silver para '{entity}' ---")
    
    bronze_base_path = os.path.join(BASE_DATA_PATH, "bronze", entity)
    silver_path = os.path.join(BASE_DATA_PATH, "silver", entity)
    os.makedirs(silver_path, exist_ok=True)
    
    all_records = []
    for root, _, files in os.walk(bronze_base_path):
        for file in files:
            if file.endswith(".json"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_records.extend(data)
                        else:
                            logging.warning(f"Dado em '{file_path}' não é uma lista e será ignorado.")
                except json.JSONDecodeError:
                    logging.error(f"Erro ao decodificar o arquivo JSON: {file_path}")
    
    if not all_records:
        logging.warning("Nenhum dado encontrado na camada Bronze para processar.")
        return

    df = pd.DataFrame(all_records)
    
    df['updated_at'] = pd.to_datetime(df['updated_at'])
    df = df.sort_values('updated_at', ascending=False)
    df = df.drop_duplicates(subset=['id'], keep='first')
    
    df_main = df[[
        'id', 'name', 'full_name', 'private', 'html_url', 'description', 
        'fork', 'url', 'created_at', 'updated_at', 'pushed_at', 
        'homepage', 'size', 'stargazers_count', 'watchers_count', 
        'language', 'forks_count', 'open_issues_count', 'license', 'owner'
    ]].copy()

    df_owner = pd.json_normalize(df['owner'])
    df_owner.columns = [f"owner_{col}" for col in df_owner.columns]
    
    df_clean = df_main.join(df_owner)
    df_clean = df_clean.drop(columns=['owner'])
    
    df_clean['license_key'] = df['license'].apply(
        lambda x: x['key'] if x and isinstance(x, dict) else None
    )
    df_clean = df_clean.drop(columns=['license'])

    main_table_path = os.path.join(silver_path, f"{entity}.parquet")
    df_clean.to_parquet(main_table_path, index=False)
    
    logging.info(f"Tabela normalizada salva em {main_table_path}. Total de {len(df_clean)} registros únicos.")

# --- Camada Gold: Métricas ---

def create_gold_metrics(entity: str):
    """
    Cria uma tabela analítica com métricas a partir da camada Silver.
    """
    logging.info(f"--- Iniciando Camada Gold para '{entity}' ---")
    
    silver_file = os.path.join(BASE_DATA_PATH, "silver", entity, f"{entity}.parquet")
    if not os.path.exists(silver_file):
        logging.warning(f"Arquivo Silver não encontrado em {silver_file}. Abortando camada Gold.")
        return
        
    df = pd.read_parquet(silver_file)
    
    df['creation_date'] = pd.to_datetime(df['created_at']).dt.date
    
    daily_creations = df.groupby('creation_date').size().reset_index(name='new_repositories_count')
    
    avg_stars = df.groupby('creation_date')['stargazers_count'].mean().reset_index(name='avg_stars')
    avg_stars['avg_stars'] = avg_stars['avg_stars'].round(2)
    
    top_languages = df['language'].value_counts().nlargest(5).reset_index()
    top_languages.columns = ['language', 'total_count']
    
    daily_metrics = pd.merge(daily_creations, avg_stars, on='creation_date', how='left')
    
    gold_path = os.path.join(BASE_DATA_PATH, "gold")
    os.makedirs(gold_path, exist_ok=True)
    
    daily_metrics_path = os.path.join(gold_path, "daily_repository_metrics.parquet")
    top_languages_path = os.path.join(gold_path, "top_5_languages.parquet")
    
    daily_metrics.to_parquet(daily_metrics_path, index=False)
    top_languages.to_parquet(top_languages_path, index=False)
    
    logging.info(f"Métricas diárias salvas em: {daily_metrics_path}")
    logging.info(f"Top 5 linguagens salvas em: {top_languages_path}")
    
    logging.info("\nAmostra de Métricas Diárias:")
    print(daily_metrics.sort_values('creation_date', ascending=False).head())
    
    logging.info("\nTop 5 Linguagens:")
    print(top_languages)


if __name__ == "__main__":
    
    logging.info("   Iniciando Mini-Pipeline de Dados   ")
    ingest_to_bronze(ENTITY, pages_limit=PAGES_TO_INGEST)
    normalize_to_silver(ENTITY)
    create_gold_metrics(ENTITY)
    logging.info("      Pipeline executado com sucesso!      ")