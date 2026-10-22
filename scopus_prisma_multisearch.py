import os
import re
import json
import time
import requests
import pandas as pd
from pathlib import Path

# Carga las variables del archivo .env (misma carpeta que este script)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    print("Aviso: falta python-dotenv (pip install python-dotenv). "
          "Se usarán solo las variables de entorno del sistema.")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "si", "sí")


def env_int(name, default=None):
    value = os.getenv(name, "").strip()
    if not value or value.lower() == "none":
        return default
    return int(value)


# ============================================================
# CONFIGURACIÓN
# ============================================================

API_KEY = os.getenv("ELSEVIER_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "No se encontró ELSEVIER_API_KEY. "
        "Configura la variable de entorno antes de ejecutar el script."
    )

INST_TOKEN = os.getenv("ELSEVIER_INST_TOKEN", "").strip() or None

BASE_DIR = Path("scopus_prisma_multisearch")
ABSTRACT_DIR = BASE_DIR / "abstracts_json"
PDF_DIR = BASE_DIR / "pdfs"

BASE_DIR.mkdir(exist_ok=True)
ABSTRACT_DIR.mkdir(exist_ok=True)
PDF_DIR.mkdir(exist_ok=True)

SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
ABSTRACT_URL = "https://api.elsevier.com/content/abstract/eid"
ARTICLE_URL = "https://api.elsevier.com/content/article"

# Año mínimo de publicación incluido en la búsqueda
START_YEAR = env_int("START_YEAR", 2019)

# Filtros adicionales (sintaxis de búsqueda avanzada de Scopus)
#   DOCTYPE(ar)      -> solo artículos
#   SRCTYPE(j)       -> solo revistas (excluye congresos)
#   LANGUAGE(english)-> solo inglés
#   SUBJAREA(COMP)   -> solo Computer Science (evita "cloud" de otras áreas)
#   Si quieres incluir congresos (muy comunes en microservicios), cambia a:
#   "(DOCTYPE(ar) OR DOCTYPE(cp)) AND LANGUAGE(english) AND SUBJAREA(COMP)"
EXTRA_FILTERS = os.getenv("EXTRA_FILTERS", "DOCTYPE(ar) AND SRCTYPE(j) AND LANGUAGE(english) AND SUBJAREA(COMP)")

# True = solo artículos Open Access (así casi todos se podrán descargar)
OPEN_ACCESS_ONLY = env_bool("OPEN_ACCESS_ONLY", False)

# Máximo de registros a bajar por búsqueda (None = todos).
# Con "cloud computing" hay decenas de miles; 250 por búsqueda es razonable.
MAX_RESULTS_PER_SEARCH = env_int("MAX_RESULTS_PER_SEARCH", 100)

# Intentar bajar PDF por la API de Elsevier. Casi nunca funciona sin token
# institucional y hace el script mucho más lento. Los PDFs se bajan luego
# con descarga_pdf.py (Semantic Scholar).
DOWNLOAD_ELSEVIER_PDF = env_bool("DOWNLOAD_ELSEVIER_PDF", False)

# Filtro por cuartil (opcional). Scopus API NO filtra por cuartil, así que se
# cruza el ISSN con el ranking de SCImago:
#   1. Entra a https://www.scimagojr.com/journalrank.php
#   2. Botón "Download data" -> guarda el CSV junto a este script
#   3. Pon aquí el nombre del archivo
SJR_FILE = os.getenv("SJR_FILE", "scimagojr.csv")
ONLY_Q1_Q2 = env_bool("ONLY_Q1_Q2", False)   # True = se queda solo con revistas Q1 y Q2

HEADERS = {
    "X-ELS-APIKey": API_KEY,
    "Accept": "application/json",
    "User-Agent": "Academic-PRISMA-Scopus-Multisearch/1.0"
}

if INST_TOKEN:
    HEADERS["X-ELS-Insttoken"] = INST_TOKEN

# ============================================================
# BLOQUES DE BÚSQUEDA
# ============================================================

# Bloque MICRO: arquitectura de microservicios
MICRO = r'''(
microservice*
OR "micro-service*"
OR "microservices architecture"
)'''

# Bloque MONO: arquitectura monolítica
MONO = r'''(
monolith*
OR "monolithic architecture"
OR "modular monolith"
OR "monolithic application*"
)'''

# Bloque CLOUD: plataformas cloud
CLOUD = r'''(
"cloud computing"
OR "cloud-native"
OR "cloud platform*"
OR "cloud environment*"
OR "cloud infrastructure"
OR "cloud services"
OR "public cloud"
OR "multi-cloud"
OR "hybrid cloud"
OR Kubernetes
OR container*
OR serverless
)'''

# Bloque COMPARACION: ventajas y desafíos
COMPARA = r'''(
compar*
OR versus
OR "trade-off*"
OR tradeoff*
OR advantage*
OR benefit*
OR drawback*
OR challenge*
OR limitation*
OR "pros and cons"
)'''

# Bloque MIGRACION: paso de monolito a microservicios
MIGRA = r'''(
migrat*
OR decomposition
OR "monolith to microservice*"
OR refactor*
OR modernization
OR "legacy system*"
OR "service granularity"
OR "strangler"
)'''

# Bloque DESEMPEÑO / COSTO / OPERACIÓN
DESEMPENO = r'''(
performance
OR scalability
OR latency
OR throughput
OR elasticity
OR autoscaling
OR "resource utilization"
OR cost*
OR "energy efficiency"
OR resilience
OR availability
OR "fault tolerance"
OR observability
)'''

# Bloque CRITERIOS DE SELECCIÓN
CRITERIOS = r'''(
"selection criteria"
OR "decision framework"
OR "decision model"
OR "decision support"
OR "architectural decision*"
OR "architecture selection"
OR "architectural style*"
OR "technical debt"
OR maintainability
OR complexity
OR "team structure"
OR "organizational"
)'''

# Bloque de revisiones y retos abiertos (para extraer brechas)
REVIEWS = r'''(
"systematic literature review"
OR "systematic review"
OR "literature review"
OR "mapping study"
OR survey
OR "research challenges"
OR "open challenges"
OR "open issues"
OR "research gaps"
OR "future directions"
OR "future research"
)'''

SEARCHES = {
    "S1_COMPARATIVO":              f"{MICRO} AND {MONO} AND {CLOUD}",
    "S2_MIGRACION":                f"{MICRO} AND {CLOUD} AND {MIGRA}",
    "S3_DESEMPENO_COSTO":          f"{MICRO} AND {MONO} AND {DESEMPENO}",
    "S4_CRITERIOS_SELECCION":      f"{MICRO} AND {MONO} AND {CRITERIOS}",
    "S5_COMPARATIVO_REVISIONES":   f"{MICRO} AND {MONO} AND {CLOUD} AND {REVIEWS}",
    "S6_MIGRACION_REVISIONES":     f"{MICRO} AND {CLOUD} AND {MIGRA} AND {REVIEWS}",
    "S7_DESEMPENO_REVISIONES":     f"{MICRO} AND {MONO} AND {DESEMPENO} AND {REVIEWS}",
    "S8_CRITERIOS_REVISIONES":     f"{MICRO} AND {MONO} AND {CRITERIOS} AND {REVIEWS}",
}

# ============================================================
# UTILIDADES
# ============================================================

def safe_filename(value, max_length=160):
    value = str(value or "unknown")
    value = re.sub(r'[\\/*?:"<>|]', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_length]


def request_json(url, params=None, max_retries=5):
    for attempt in range(max_retries):
        response = requests.get(url, headers=HEADERS, params=params, timeout=60)

        if response.status_code == 200:
            return response.json()

        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", 10))
            print(f"Rate limit alcanzado. Esperando {wait} s...")
            time.sleep(wait)
            continue

        if response.status_code in (500, 502, 503, 504):
            wait = 2 ** attempt
            print(f"Error temporal {response.status_code}. Reintentando en {wait} s...")
            time.sleep(wait)
            continue

        print("\nError API")
        print("URL:", response.url)
        print("Status:", response.status_code)
        print(response.text[:1000])
        response.raise_for_status()

    raise RuntimeError(f"No fue posible recuperar {url}")


# ============================================================
# BÚSQUEDA SCOPUS
# ============================================================

def search_scopus(query_name, raw_query):
    query = f"TITLE-ABS-KEY({raw_query}) AND PUBYEAR > {START_YEAR - 1}"
    if EXTRA_FILTERS:
        query += f" AND {EXTRA_FILTERS}"
    if OPEN_ACCESS_ONLY:
        query += " AND OPENACCESS(1)"

    all_entries = []
    raw_pages = []
    start = 0
    count = 25
    total_results = None

    while True:
        params = {
            "query": query,
            "view": "STANDARD",
            "count": count,
            "start": start
        }

        print(f"\n[{query_name}] start={start}")
        data = request_json(SEARCH_URL, params=params)
        raw_pages.append(data)

        results = data.get("search-results") or {}

        try:
            total_results = int(results.get("opensearch:totalResults") or 0)
        except (TypeError, ValueError):
            total_results = 0

        if start == 0:
            print(f"[{query_name}] Total encontrados: {total_results}")

        raw_entries = results.get("entry") or []
        if isinstance(raw_entries, dict):
            raw_entries = [raw_entries]

        entries = []
        for e in raw_entries:
            if not isinstance(e, dict):
                continue
            if e.get("error"):
                continue
            if not any([
                e.get("eid"),
                e.get("dc:identifier"),
                e.get("prism:doi"),
                e.get("dc:title"),
            ]):
                continue
            entries.append(e)

        if total_results == 0:
            print(f"[{query_name}] Sin resultados bibliográficos.")
            break

        if not entries:
            print(f"[{query_name}] Sin registros válidos en start={start}; se detiene.")
            break

        all_entries.extend(entries)
        start += len(entries)

        print(f"[{query_name}] Descargados: {len(all_entries)} / {total_results}")

        if start >= total_results:
            break

        if MAX_RESULTS_PER_SEARCH and len(all_entries) >= MAX_RESULTS_PER_SEARCH:
            all_entries = all_entries[:MAX_RESULTS_PER_SEARCH]
            print(f"[{query_name}] Tope de {MAX_RESULTS_PER_SEARCH} registros alcanzado.")
            break

        time.sleep(0.25)

    return query, all_entries, raw_pages, total_results


# ============================================================
# NORMALIZACIÓN
# ============================================================

def normalize_search_entry(entry, search_name):
    if not isinstance(entry, dict) or entry.get("error"):
        return None

    authors = entry.get("author") or []
    if isinstance(authors, dict):
        authors = [authors]
    elif not isinstance(authors, list):
        authors = []

    author_names = []
    for author in authors:
        if isinstance(author, dict):
            name = author.get("authname") or author.get("surname") or ""
            if name:
                author_names.append(str(name))

    affiliations = entry.get("affiliation") or []
    if isinstance(affiliations, dict):
        affiliations = [affiliations]
    elif not isinstance(affiliations, list):
        affiliations = []

    affiliation_names = []
    for aff in affiliations:
        if isinstance(aff, dict):
            name = aff.get("affilname")
            if name:
                affiliation_names.append(str(name))

    links = entry.get("link") or []
    if isinstance(links, dict):
        links = [links]
    elif not isinstance(links, list):
        links = []

    cover_date = entry.get("prism:coverDate")

    return {
        "search_name": search_name,
        "eid": entry.get("eid"),
        "scopus_id": entry.get("dc:identifier"),
        "title": entry.get("dc:title"),
        "authors": "; ".join(author_names),
        "year": str(cover_date)[:4] if cover_date else None,
        "cover_date": cover_date,
        "publication": entry.get("prism:publicationName"),
        "doi": entry.get("prism:doi"),
        "issn": entry.get("prism:issn"),
        "eissn": entry.get("prism:eIssn"),
        "volume": entry.get("prism:volume"),
        "issue": entry.get("prism:issueIdentifier"),
        "pages": entry.get("prism:pageRange"),
        "document_type": entry.get("subtypeDescription"),
        "aggregation_type": entry.get("prism:aggregationType"),
        "cited_by": entry.get("citedby-count"),
        "open_access": entry.get("openaccessFlag"),
        "affiliations": "; ".join(affiliation_names),
        "scopus_url": next(
            (link.get("@href") for link in links
             if isinstance(link, dict) and link.get("@ref") == "scopus"),
            None
        )
    }


# ============================================================
# ABSTRACT RETRIEVAL
# ============================================================

def is_missing(value):
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    if isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "null"}:
        return True
    return False


def get_abstract(eid):
    if is_missing(eid):
        return None

    eid = str(eid).strip()
    url = f"{ABSTRACT_URL}/{eid}"

    for view in ("FULL", "META", "STANDARD"):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                params={"view": view},
                timeout=60
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code in (401, 403):
                continue

            if response.status_code == 404:
                return None

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", 10))
                print(f"Rate limit en Abstract Retrieval. Esperando {wait} s...")
                time.sleep(wait)
                continue

            print(f"Abstract {eid}: HTTP {response.status_code} con view={view}")

        except requests.RequestException as exc:
            print(f"No se pudo recuperar abstract {eid}: {exc}")
            return None

    return None


def parse_abstract(data):
    if not isinstance(data, dict):
        return {}

    response = data.get("abstracts-retrieval-response") or {}
    if not isinstance(response, dict):
        return {}

    coredata = response.get("coredata") or {}
    if not isinstance(coredata, dict):
        coredata = {}

    abstract = coredata.get("dc:description")

    authors_section = response.get("authors") or {}
    if not isinstance(authors_section, dict):
        authors_section = {}

    author_list = authors_section.get("author") or []
    if isinstance(author_list, dict):
        author_list = [author_list]
    elif not isinstance(author_list, list):
        author_list = []

    authors = []
    author_ids = []

    for a in author_list:
        if not isinstance(a, dict):
            continue

        preferred = a.get("preferred-name") or {}
        if not isinstance(preferred, dict):
            preferred = {}

        name = preferred.get("ce:indexed-name") or a.get("ce:indexed-name") or ""
        if name:
            authors.append(str(name))

        auid = a.get("@auid")
        if auid:
            author_ids.append(str(auid))

    affiliations = []
    aff_data = response.get("affiliation") or []
    if isinstance(aff_data, dict):
        aff_data = [aff_data]
    elif not isinstance(aff_data, list):
        aff_data = []

    for aff in aff_data:
        if not isinstance(aff, dict):
            continue
        name = aff.get("affilname")
        city = aff.get("affiliation-city")
        country = aff.get("affiliation-country")
        pieces = [str(x) for x in (name, city, country) if x]
        if pieces:
            affiliations.append(", ".join(pieces))

    keywords = []
    authkeywords = response.get("authkeywords") or {}
    if not isinstance(authkeywords, dict):
        authkeywords = {}

    keyword_list = authkeywords.get("author-keyword") or []
    if isinstance(keyword_list, dict):
        keyword_list = [keyword_list]
    elif not isinstance(keyword_list, list):
        keyword_list = []

    for kw in keyword_list:
        value = None
        if isinstance(kw, dict):
            value = kw.get("$") or kw.get("keyword")
        elif isinstance(kw, str):
            value = kw
        if value:
            keywords.append(str(value))

    return {
        "abstract": abstract,
        "full_authors": "; ".join(authors),
        "author_scopus_ids": "; ".join(author_ids),
        "full_affiliations": "; ".join(affiliations),
        "author_keywords": "; ".join(keywords),
        "abstract_cited_by": coredata.get("citedby-count")
    }


# ============================================================
# DESCARGA DE PDF (SOLO SI EXISTE ACCESO AUTORIZADO)
# ============================================================

def looks_like_pdf(response):
    content_type = (response.headers.get("Content-Type") or "").lower()
    return "application/pdf" in content_type or (response.content or b"")[:5] == b"%PDF-"


def download_pdf(record):
    """
    Intenta descargar el PDF desde Elsevier Article Retrieval API.
    Funcionará únicamente si el artículo es Open Access o si la API/IP/token
    tiene permisos institucionales para el texto completo.
    """
    candidates = []

    doi = record.get("doi")
    eid = record.get("eid")
    scopus_id = record.get("scopus_id")

    if not is_missing(doi):
        candidates.append(("doi", str(doi).strip()))

    if not is_missing(eid):
        candidates.append(("eid", str(eid).strip()))

    if not is_missing(scopus_id):
        sid = str(scopus_id).strip()
        if sid.upper().startswith("SCOPUS_ID:"):
            sid = sid.split(":", 1)[1]
        candidates.append(("scopus_id", sid))

    if not candidates:
        return {
            "pdf_downloaded": False,
            "pdf_path": None,
            "pdf_status": "sin_identificador"
        }

    pdf_headers = dict(HEADERS)
    pdf_headers["Accept"] = "application/pdf"

    for id_type, value in candidates:
        url = f"{ARTICLE_URL}/{id_type}/{value}"

        try:
            response = requests.get(
                url,
                headers=pdf_headers,
                params={
                    "httpAccept": "application/pdf",
                    "amsRedirect": "true"
                },
                timeout=90,
                allow_redirects=True
            )

            if response.status_code == 200 and looks_like_pdf(response):
                year = record.get("year") or "sin_anio"
                title = safe_filename(
                    record.get("title") or value,
                    max_length=150
                )
                filename = f"{year}_{title}.pdf"
                pdf_path = PDF_DIR / filename

                # Evitar sobreescritura accidental
                if pdf_path.exists():
                    suffix = safe_filename(value, max_length=35)
                    pdf_path = PDF_DIR / f"{year}_{title}_{suffix}.pdf"

                with open(pdf_path, "wb") as f:
                    f.write(response.content)

                return {
                    "pdf_downloaded": True,
                    "pdf_path": str(pdf_path),
                    "pdf_status": f"descargado_elsevier_{id_type}"
                }

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", 10))
                print(f"Rate limit PDF. Esperando {wait} s...")
                time.sleep(wait)

        except requests.RequestException as exc:
            print(f"    Error PDF {id_type}={value}: {exc}")

    return {
        "pdf_downloaded": False,
        "pdf_path": None,
        "pdf_status": "sin_acceso_o_no_disponible"
    }


# ============================================================
# DEDUPLICACIÓN
# ============================================================

def normalize_doi(doi):
    if not doi:
        return None
    return (
        str(doi).strip().lower()
        .replace("https://doi.org/", "")
        .replace("http://doi.org/", "")
    )


def deduplicate_records(df):
    df = df.copy()
    df["doi_norm"] = df["doi"].apply(normalize_doi)

    def norm_title(x):
        if not isinstance(x, str):
            return None
        x = x.lower().strip()
        x = re.sub(r"\s+", " ", x)
        return x

    df["title_norm"] = df["title"].apply(norm_title)

    def dedup_key(row):
        if pd.notna(row["eid"]) and row["eid"]:
            return f"EID::{row['eid']}"
        if pd.notna(row["doi_norm"]) and row["doi_norm"]:
            return f"DOI::{row['doi_norm']}"
        return f"TITLE::{row['title_norm']}"

    df["dedup_key"] = df.apply(dedup_key, axis=1)

    grouped = []
    for key, g in df.groupby("dedup_key", dropna=False):
        first = g.iloc[0].copy()
        first["search_name"] = "; ".join(sorted(g["search_name"].dropna().unique()))
        first["n_search_hits"] = g["search_name"].nunique()
        grouped.append(first)

    return pd.DataFrame(grouped)


# ============================================================
# CUARTIL (SCImago, opcional)
# ============================================================

def norm_issn(value):
    if is_missing(value):
        return ""
    return re.sub(r"[^0-9Xx]", "", str(value)).upper()


def load_sjr(path):
    """Devuelve {issn_sin_guion: 'Q1'..'Q4'} desde el CSV de SCImago."""
    path = Path(path)
    if not path.exists():
        return None

    try:
        sjr = pd.read_csv(path, sep=";", dtype=str, encoding="utf-8-sig", low_memory=False)
    except UnicodeDecodeError:
        sjr = pd.read_csv(path, sep=";", dtype=str, encoding="latin-1", low_memory=False)

    cols = {c.lower().strip(): c for c in sjr.columns}
    issn_col = cols.get("issn")
    q_col = next((v for k, v in cols.items() if "quartile" in k), None)

    if not issn_col or not q_col:
        raise RuntimeError(
            f"El archivo {path} no tiene columnas 'Issn' y '... Quartile'. "
            f"Columnas encontradas: {list(sjr.columns)}"
        )

    lookup = {}
    for issns, q in zip(sjr[issn_col], sjr[q_col]):
        if pd.isna(issns) or pd.isna(q):
            continue
        for item in re.split(r"[,\s]+", str(issns)):
            item = norm_issn(item)
            if len(item) == 8:
                lookup[item] = str(q).strip()
    return lookup


def add_quartile(df, lookup):
    def find(row):
        for col in ("issn", "eissn"):
            q = lookup.get(norm_issn(row.get(col)))
            if q:
                return q
        return None

    df = df.copy()
    df["sjr_quartile"] = df.apply(find, axis=1)
    return df


# ============================================================
# MAIN
# ============================================================

def main():
    all_records = []
    search_summary = []
    queries_used = {}

    for search_name, raw_query in SEARCHES.items():
        scopus_query, entries, raw_pages, total_results = search_scopus(
            search_name,
            raw_query
        )

        queries_used[search_name] = scopus_query

        raw_path = BASE_DIR / f"{search_name}_raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(raw_pages, f, ensure_ascii=False, indent=2)

        records = [normalize_search_entry(e, search_name) for e in entries]
        records = [r for r in records if r is not None]
        all_records.extend(records)

        search_summary.append({
            "database": "Scopus",
            "search_name": search_name,
            "query": scopus_query,
            "records_found": total_results,
            "records_downloaded": len(records)
        })

    df_all = pd.DataFrame(all_records)

    if df_all.empty:
        raise RuntimeError("No se recuperaron registros bibliográficos válidos.")

    all_csv = BASE_DIR / "scopus_all_search_hits.csv"
    df_all.to_csv(all_csv, index=False, encoding="utf-8-sig")

    df_dedup = deduplicate_records(df_all)

    print("\n====================================")
    print("DEDUPLICACIÓN")
    print("====================================")
    print(f"Registros acumulados: {len(df_all)}")
    print(f"Registros únicos:      {len(df_dedup)}")
    print(f"Duplicados eliminados: {len(df_all) - len(df_dedup)}")

    n_unique_before_quartile = len(df_dedup)
    sjr_lookup = load_sjr(SJR_FILE)

    if sjr_lookup is None:
        print(f"\n(Sin archivo de cuartiles '{SJR_FILE}': no se aplica filtro de cuartil.)")
        if ONLY_Q1_Q2:
            print("AVISO: ONLY_Q1_Q2=True pero falta el CSV de SCImago; se continúa sin filtrar.")
    else:
        df_dedup = add_quartile(df_dedup, sjr_lookup)
        print("\nCuartiles encontrados:")
        print(df_dedup["sjr_quartile"].fillna("sin dato").value_counts().to_string())
        if ONLY_Q1_Q2:
            df_dedup = df_dedup[df_dedup["sjr_quartile"].isin(["Q1", "Q2"])].copy()
            print(f"Registros tras filtro Q1/Q2: {len(df_dedup)}")

    enriched_records = []

    for i, (_, row) in enumerate(df_dedup.iterrows(), start=1):
        record = row.to_dict()
        eid = record.get("eid")

        print(f"\n[{i}/{len(df_dedup)}] {record.get('title', '')}")

        abstract_data = get_abstract(eid)

        if abstract_data:
            json_path = ABSTRACT_DIR / f"{safe_filename(eid)}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(abstract_data, f, ensure_ascii=False, indent=2)

            record.update(parse_abstract(abstract_data))
        else:
            record.update({
                "abstract": None,
                "full_authors": None,
                "author_scopus_ids": None,
                "full_affiliations": None,
                "author_keywords": None,
                "abstract_cited_by": None
            })

        if DOWNLOAD_ELSEVIER_PDF:
            pdf_result = download_pdf(record)
        else:
            pdf_result = {
                "pdf_downloaded": False,
                "pdf_path": None,
                "pdf_status": "no_intentado_usar_descarga_pdf"
            }
        record.update(pdf_result)

        if record.get("pdf_downloaded"):
            print(f"    PDF descargado: {record.get('pdf_path')}")

        enriched_records.append(record)
        time.sleep(0.25)

    df_final = pd.DataFrame(enriched_records)

    final_csv = BASE_DIR / "scopus_unique_metadata.csv"
    final_xlsx = BASE_DIR / "scopus_unique_metadata.xlsx"
    summary_csv = BASE_DIR / "scopus_search_summary.csv"
    queries_txt = BASE_DIR / "search_queries.txt"
    prisma_path = BASE_DIR / "prisma_identification_scopus.xlsx"

    df_final.to_csv(final_csv, index=False, encoding="utf-8-sig")
    df_final.to_excel(final_xlsx, index=False)

    pd.DataFrame(search_summary).to_csv(
        summary_csv,
        index=False,
        encoding="utf-8-sig"
    )

    with open(queries_txt, "w", encoding="utf-8") as f:
        for name, query in queries_used.items():
            f.write("=" * 80 + "\n")
            f.write(name + "\n")
            f.write("=" * 80 + "\n")
            f.write(query + "\n\n")

    prisma_summary = pd.DataFrame(search_summary)

    with pd.ExcelWriter(prisma_path, engine="openpyxl") as writer:
        prisma_summary.to_excel(writer, sheet_name="Searches", index=False)

        pd.DataFrame({
            "metric": [
                "Total hits across searches",
                "Unique records after deduplication",
                "Duplicates removed",
                "Records after quartile filter (if applied)"
            ],
            "value": [
                len(df_all),
                n_unique_before_quartile,
                len(df_all) - n_unique_before_quartile,
                len(df_dedup)
            ]
        }).to_excel(writer, sheet_name="PRISMA_Summary", index=False)

    print("\n====================================")
    print("PROCESO FINALIZADO")
    print("====================================")
    print(f"Hits acumulados:       {len(df_all)}")
    print(f"Registros únicos:      {n_unique_before_quartile}")
    print(f"Duplicados eliminados: {len(df_all) - n_unique_before_quartile}")
    print(f"Registros finales:     {len(df_dedup)}")
    if "pdf_downloaded" in df_final.columns:
        print(f"PDFs descargados:      {int(df_final['pdf_downloaded'].fillna(False).sum())}")
        print(f"Carpeta PDFs:          {PDF_DIR}")
    print(f"CSV únicos: {final_csv}")
    print(f"Excel únicos: {final_xlsx}")
    print(f"Resumen búsquedas: {summary_csv}")
    print(f"PRISMA: {prisma_path}")
    print(f"Queries: {queries_txt}")


if __name__ == "__main__":
    main()
