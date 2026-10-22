"""Prueba consultas de menos a más para descubrir cuál devuelve 0 en Scopus."""
import os, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
KEY = os.getenv("ELSEVIER_API_KEY", "").strip()
TOKEN = os.getenv("ELSEVIER_INST_TOKEN", "").strip()
EXTRA = os.getenv("EXTRA_FILTERS", "").strip()
print(f"Clave cargada: {KEY[:4]}...{KEY[-4:]} (largo {len(KEY)})")

headers = {"X-ELS-APIKey": KEY, "Accept": "application/json"}
if TOKEN:
    headers["X-ELS-Insttoken"] = TOKEN

pruebas = {
    "1. solo microservice*":
        'TITLE-ABS-KEY(microservice*)',
    "2. micro + monolith":
        'TITLE-ABS-KEY(microservice* AND monolith*)',
    "3. + año 2021":
        'TITLE-ABS-KEY(microservice* AND monolith*) AND PUBYEAR > 2020',
    "4. + DOCTYPE(ar)":
        'TITLE-ABS-KEY(microservice* AND monolith*) AND PUBYEAR > 2020 AND DOCTYPE(ar)',
    "5. + LANGUAGE(english)":
        'TITLE-ABS-KEY(microservice* AND monolith*) AND PUBYEAR > 2020 AND LANGUAGE(english)',
    "6. + SUBJAREA(COMP)":
        'TITLE-ABS-KEY(microservice* AND monolith*) AND PUBYEAR > 2020 AND SUBJAREA(COMP)',
    "7. + OPENACCESS(1)":
        'TITLE-ABS-KEY(microservice* AND monolith*) AND PUBYEAR > 2020 AND OPENACCESS(1)',
    "8. + tus EXTRA_FILTERS":
        f'TITLE-ABS-KEY(microservice* AND monolith*) AND PUBYEAR > 2020 AND {EXTRA}',
}

for nombre, q in pruebas.items():
    r = requests.get("https://api.elsevier.com/content/search/scopus",
                     headers=headers, params={"query": q, "count": 1}, timeout=60)
    if r.status_code != 200:
        print(f"{nombre:28} -> HTTP {r.status_code}: {r.text[:200]}")
        continue
    total = r.json().get("search-results", {}).get("opensearch:totalResults")
    print(f"{nombre:28} -> {total} resultados")