import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any
import requests
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configurações

CONFIG_PATH = "config.json"
DEFAULT_CONFIG = {
    "entity": "repositories",
    "base_api_url": "https://api.github.com/search/repositories",
    "pages_to_ingest": 10 
}

def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        return DEFAULT_CONFIG

config = load_config()
ENTITY = config["entity"]
BASE_API_URL = config["base_api_url"]
PAGES_TO_INGEST = config["pages_to_ingest"]
BASE_DATA_PATH = "data"

# Funções auxiliares

def load_checkpoint(entity: str) -> int:
    checkpoint_path = os.path.join(BASE_DATA_PATH, "checkpoints", f"{entity}.json")
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            return json.load(f).get("last_page", 0)
    return 0

def save_checkpoint(entity: str, page: int):
    os.makedirs(os.path.join(BASE_DATA_PATH, "checkpoints"), exist_ok=True)
    checkpoint_path = os.path.join(BASE_DATA_PATH, "checkpoints", f"{entity}.json")
    with open(checkpoint_path, "w", encoding="utf-8") as f:
        json.dump({"last_page": page}, f, indent=4)

# Camada Bronze

def ingest_to_bronze(entity: str, pages_limit: int):
    start_time = time.time()
    logging.info(f"--- Iniciando Camada Bronze para '{entity}' ---")
    
    today = datetime.now()
    bronze_path = os.path.join(BASE_DATA_PATH, "bronze", entity, f"{today:%Y/%m/%d}")
    os.makedirs(bronze_path, exist_ok=True)

    headers = {"Accept": "application/vnd.github.v3+json"}
    params = {
        "q": "stars:>1000",
        "sort": "stars",
        "order": "desc",
        "per_page": 100
    }

    last_page = load_checkpoint(entity)
    logging.info(f"Retomando da página {last_page + 1}")

    for page_num in range(last_page + 1, pages_limit + 1):
        params["page"] = page_num
        max_retries = 3
        backoff_factor = 2

        for attempt in range(max_retries):
            try:
                response = requests.get(BASE_API_URL, headers=headers, params=params, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    repositories = data.get("items", [])
                    if not repositories:
                        logging.info("Nenhum dado adicional encontrado.")
                        return

                    file_path = os.path.join(bronze_path, f"page_{page_num}.json")
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(repositories, f, indent=4)

                    save_checkpoint(entity, page_num)
                    logging.info(f"Página {page_num} salva e checkpoint atualizado.")
                    break

                elif response.status_code in [429, 500, 502, 503, 504, 403]:
                    wait_time = backoff_factor * (2 ** attempt)
                    logging.warning(f"Erro {response.status_code}, tentando novamente em {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    response.raise_for_status()

            except requests.exceptions.RequestException as e:
                logging.error(f"Tentativa {attempt + 1} falhou: {e}")
                time.sleep(backoff_factor * (2 ** attempt))
                if attempt == max_retries - 1:
                    raise

    duration = round(time.time() - start_time, 2)
    logging.info(f"Camada Bronze finalizada em {duration}s")

# Camada Silver

def normalize_to_silver(entity: str):
    start_time = time.time()
    logging.info(f"--- Iniciando Camada Silver para '{entity}' ---")

    bronze_base = os.path.join(BASE_DATA_PATH, "bronze", entity)
    silver_path = os.path.join(BASE_DATA_PATH, "silver", entity)
    os.makedirs(silver_path, exist_ok=True)

    records = []
    for root, _, files in os.walk(bronze_base):
        for f_name in files:
            if f_name.endswith(".json"):
                with open(os.path.join(root, f_name), "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f)
                        if isinstance(data, list):
                            records.extend(data)
                    except json.JSONDecodeError:
                        logging.warning(f"Erro ao ler {f_name}")

    if not records:
        logging.warning("Nenhum dado encontrado para normalização.")
        return

    df = pd.DataFrame(records)
    df["updated_at"] = pd.to_datetime(df["updated_at"])
    df = df.sort_values("updated_at", ascending=False).drop_duplicates("id")

    df_owner = pd.json_normalize(df["owner"]).add_prefix("owner_")
    df["license_key"] = df["license"].apply(lambda x: x["key"] if isinstance(x, dict) else None)
    df_clean = pd.concat([df.drop(columns=["owner", "license"]), df_owner], axis=1)

    path_out = os.path.join(silver_path, f"{entity}.parquet")
    df_clean.to_parquet(path_out, index=False)

    duration = round(time.time() - start_time, 2)
    logging.info(f"Silver finalizada ({len(df_clean)} registros únicos) em {duration}s")

# Camada Gold

def create_gold_metrics(entity: str):
    start_time = time.time()
    logging.info(f"--- Iniciando Camada Gold para '{entity}' ---")

    silver_file = os.path.join(BASE_DATA_PATH, "silver", entity, f"{entity}.parquet")
    if not os.path.exists(silver_file):
        logging.error("Arquivo Silver não encontrado.")
        return

    df = pd.read_parquet(silver_file)
    df["creation_date"] = pd.to_datetime(df["created_at"]).dt.date

    daily_metrics = (
        df.groupby("creation_date")
        .agg(new_repositories_count=("id", "count"), avg_stars=("stargazers_count", "mean"))
        .reset_index()
    )
    daily_metrics["avg_stars"] = daily_metrics["avg_stars"].round(2)

    top_languages = df["language"].value_counts().nlargest(5).reset_index()
    top_languages.columns = ["language", "total_count"]

    gold_path = os.path.join(BASE_DATA_PATH, "gold")
    os.makedirs(gold_path, exist_ok=True)

    daily_metrics.to_parquet(os.path.join(gold_path, "daily_metrics.parquet"), index=False)
    top_languages.to_parquet(os.path.join(gold_path, "top_5_languages.parquet"), index=False)

    duration = round(time.time() - start_time, 2)
    logging.info(f"Gold finalizada em {duration}s")
    logging.info("\nTop 5 linguagens:\n" + str(top_languages))


if __name__ == "__main__":
    logging.info("===== Mini Pipeline de Dados =====")
    ingest_to_bronze(ENTITY, pages_limit=PAGES_TO_INGEST)
    normalize_to_silver(ENTITY)
    create_gold_metrics(ENTITY)
    logging.info("===== Pipeline executado com sucesso! =====")
