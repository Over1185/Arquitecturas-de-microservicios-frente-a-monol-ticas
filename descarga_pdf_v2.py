# ============================================================
# descarga_pdf_v2.py
#
# Descarga PDFs open access probando VARIAS fuentes por paper:
#   1. Unpaywall      (requiere solo un correo)
#   2. OpenAlex       (sin clave; consulta por lotes)
#   3. Semantic Scholar (consulta por LOTES -> casi sin errores 429)
#   4. Patrones del editor (Springer, MDPI...) y página del DOI
#   5. API de Elsevier (solo funciona si hay acceso institucional)
#
# Cuando una URL devuelve una página HTML en vez del PDF, el script
# busca dentro del HTML el enlace real (meta citation_pdf_url, etc.).
#
# Estructura (igual que antes):
#   carpeta/
#   ├── descarga_pdf_v2.py
#   ├── papers/
#   └── scopus_prisma_multisearch/scopus_unique_metadata.xlsx
#
# Variables: se leen del archivo .env (ver README.md). Alternativa PowerShell:
#   $env:CONTACT_EMAIL="tu_correo_institucional@pucese.edu.ec"   <- recomendado
#   $env:SEMANTIC_SCHOLAR_API_KEY="..."                        <- opcional
#   $env:ELSEVIER_API_KEY="..."                                <- opcional
#
# Ejecutar:  python descarga_pdf_v2.py
# Se puede interrumpir (Ctrl+C) y volver a ejecutar: no repite PDFs.
# ============================================================

import os
import re
import sys
import time
import random
from html import unescape
from pathlib import Path
from urllib.parse import quote, urljoin

import pandas as pd
import requests

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

BASE_DIR = Path(__file__).resolve().parent

INPUT_EXCEL = BASE_DIR / "scopus_prisma_multisearch" / "scopus_unique_metadata.xlsx"
OUTPUT_EXCEL = BASE_DIR / "scopus_prisma_multisearch" / "scopus_unique_metadata_semantic_scholar.xlsx"
PENDING_CSV = BASE_DIR / "pendientes_descarga_manual.csv"
PDF_DIR = BASE_DIR / "papers"

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
S2_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
ELSEVIER_KEY = os.getenv("ELSEVIER_API_KEY", "").strip()

S2_BASE = "https://api.semanticscholar.org/graph/v1"

MIN_PDF_SIZE = 5_000          # bytes
MAX_CANDIDATES = env_int("MAX_CANDIDATES", 8)            # URLs máximas a probar por paper
SAVE_EVERY = env_int("SAVE_EVERY", 10)
MAX_ROWS = env_int("MAX_ROWS", None)               # para pruebas: p. ej. 10

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html;q=0.9,application/xhtml+xml;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================
# UTILIDADES
# ============================================================

def clean_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def clean_doi(doi):
    doi = clean_text(doi)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.strip()


def safe_filename(text, max_length=140):
    text = clean_text(text) or "paper"
    text = re.sub(r'[<>:"/\\|?*]', "", text)
    text = re.sub(r"[\x00-\x1f]", "", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
    return (text or "paper")[:max_length]


def is_valid_pdf(path):
    try:
        path = Path(path)
        if not path.exists() or path.stat().st_size < MIN_PDF_SIZE:
            return False
        with open(path, "rb") as f:
            return b"%PDF-" in f.read(1024)
    except Exception:
        return False


def dedupe(seq):
    seen, out = set(), []
    for item in seq:
        key = item[1]
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def http_json(method, url, retries=5, **kwargs):
    """GET/POST que devuelve JSON, con reintentos para 429 y 5xx."""
    for attempt in range(1, retries + 1):
        try:
            r = requests.request(method, url, timeout=45, **kwargs)
        except requests.RequestException:
            time.sleep(2 * attempt)
            continue

        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None

        if r.status_code == 404:
            return None

        if r.status_code == 429 or r.status_code >= 500:
            try:
                wait = float(r.headers.get("Retry-After", 0))
            except ValueError:
                wait = 0
            wait = min(max(wait, 4 * attempt) + random.uniform(0.5, 2), 60)
            print(f"   ⚠ HTTP {r.status_code} en {url.split('?')[0][:60]}... esperando {wait:.0f}s")
            time.sleep(wait)
            continue

        print(f"   ⚠ HTTP {r.status_code} en {url.split('?')[0][:60]}")
        return None

    return None


# ============================================================
# FUENTES DE URLs CANDIDATAS
# Cada candidato = (fuente, url, headers_extra_o_None)
# ============================================================

def candidates_unpaywall(dois):
    out = {}
    if not CONTACT_EMAIL:
        print("⚠ CONTACT_EMAIL no definido: se omite Unpaywall.")
        return out

    print(f"→ Unpaywall ({len(dois)} DOIs)...")
    for i, doi in enumerate(dois, start=1):
        data = http_json(
            "GET",
            f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}",
            params={"email": CONTACT_EMAIL},
        )
        time.sleep(0.15)
        if not data:
            continue

        locs = [data.get("best_oa_location")] + (data.get("oa_locations") or [])
        urls = []
        for loc in locs:
            if loc and loc.get("url_for_pdf"):
                urls.append(("unpaywall", loc["url_for_pdf"], None))
        for loc in locs:
            if loc and loc.get("url"):
                urls.append(("unpaywall_landing", loc["url"], None))
        out[doi.lower()] = urls

        if i % 20 == 0:
            print(f"   Unpaywall {i}/{len(dois)}")
    return out


def candidates_openalex(dois):
    out = {}
    print(f"→ OpenAlex ({len(dois)} DOIs)...")
    for i in range(0, len(dois), 40):
        chunk = dois[i:i + 40]
        params = {
            "filter": "doi:" + "|".join(f"https://doi.org/{d}" for d in chunk),
            "per-page": 50,
            "select": "doi,open_access,best_oa_location,locations",
        }
        if CONTACT_EMAIL:
            params["mailto"] = CONTACT_EMAIL

        data = http_json("GET", "https://api.openalex.org/works", params=params)
        for work in (data or {}).get("results", []):
            doi = clean_doi(work.get("doi")).lower()
            locs = [work.get("best_oa_location")] + (work.get("locations") or [])
            urls = []
            for loc in locs:
                if loc and loc.get("pdf_url"):
                    urls.append(("openalex", loc["pdf_url"], None))
            for loc in locs:
                if loc and loc.get("is_oa") and loc.get("landing_page_url"):
                    urls.append(("openalex_landing", loc["landing_page_url"], None))
            oa_url = (work.get("open_access") or {}).get("oa_url")
            if oa_url:
                urls.append(("openalex_oa_url", oa_url, None))
            out[doi] = urls
        time.sleep(0.3)
    return out


def candidates_semantic_scholar(dois):
    """Consulta POR LOTES: 1 petición cada 100 DOIs en vez de 1 por paper."""
    out = {}
    print(f"→ Semantic Scholar por lotes ({len(dois)} DOIs)...")
    headers = {"x-api-key": S2_KEY} if S2_KEY else {}

    for i in range(0, len(dois), 100):
        chunk = dois[i:i + 100]
        data = http_json(
            "POST",
            f"{S2_BASE}/paper/batch",
            params={"fields": "title,isOpenAccess,openAccessPdf"},
            json={"ids": [f"DOI:{d}" for d in chunk]},
            headers=headers,
        )
        if not isinstance(data, list):
            continue
        for doi, paper in zip(chunk, data):
            if not paper:
                continue
            oa = paper.get("openAccessPdf") or {}
            if oa.get("url"):
                out[doi.lower()] = [("semantic_scholar", oa["url"], None)]
        time.sleep(1.2)
    return out


def candidates_patterns(doi):
    urls = []
    if doi.startswith(("10.1007/", "10.1186/", "10.1038/")):       # Springer Nature / BMC
        urls.append(("patron_springer", f"https://link.springer.com/content/pdf/{doi}.pdf", None))
    urls.append(("doi_landing", f"https://doi.org/{doi}", None))   # se lee el HTML y se saca el PDF
    if ELSEVIER_KEY and doi.startswith("10.1016/"):
        urls.append((
            "elsevier_api",
            f"https://api.elsevier.com/content/article/doi/{quote(doi, safe='/')}",
            {"X-ELS-APIKey": ELSEVIER_KEY, "Accept": "application/pdf"},
        ))
    return urls


# ============================================================
# DESCARGA (con lectura de HTML para encontrar el PDF real)
# ============================================================

def find_pdf_links(html, base_url):
    links = []

    for m in re.finditer(r"<meta\s[^>]*>", html, re.I):
        tag = m.group(0)
        if re.search(r'name\s*=\s*["\']citation_pdf_url["\']', tag, re.I):
            c = re.search(r'content\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if c:
                links.append(c.group(1))
        elif re.search(r'http-equiv\s*=\s*["\']refresh["\']', tag, re.I):
            c = re.search(r'url\s*=\s*[\'"]?([^"\'>\s]+)', tag, re.I)
            if c:
                links.append(c.group(1))

    for m in re.finditer(
        r'href\s*=\s*["\']([^"\']*(?:\.pdf|/pdf)(?:[/?#][^"\']*)?)["\']', html, re.I
    ):
        links.append(m.group(1))

    resolved, seen = [], set()
    for link in links:
        link = unescape(link).strip()
        if not link or link.lower().startswith(("javascript:", "mailto:")):
            continue
        full = urljoin(base_url, link)
        if full not in seen:
            seen.add(full)
            resolved.append(full)
    return resolved[:5]


def finalize(tmp, dest):
    if not is_valid_pdf(tmp):
        Path(tmp).unlink(missing_ok=True)
        return False, "archivo_invalido"
    Path(tmp).replace(dest)
    return True, "ok"


def try_download(session, url, dest, extra_headers=None, referer=None, depth=0):
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)

    tmp = Path(str(dest) + ".part")
    next_links, final_url = [], url

    for attempt in (1, 2):
        try:
            with session.get(url, headers=headers, timeout=(15, 90),
                             stream=True, allow_redirects=True) as r:
                final_url = r.url

                if r.status_code in (429, 503) and attempt == 1:
                    time.sleep(5)
                    continue

                if r.status_code != 200:
                    return False, f"http_{r.status_code}"

                chunks = r.iter_content(65536)
                first = next(chunks, b"")

                if b"%PDF-" in first[:1024]:
                    with open(tmp, "wb") as f:
                        f.write(first)
                        for chunk in chunks:
                            f.write(chunk)
                    return finalize(tmp, dest)

                body = first
                for chunk in chunks:
                    body += chunk
                    if len(body) > 2_000_000:
                        break

                try:
                    html = body.decode(r.encoding or "utf-8", errors="ignore")
                except LookupError:
                    html = body.decode("utf-8", errors="ignore")
                next_links = find_pdf_links(html, final_url)
                break

        except requests.RequestException as exc:
            if attempt == 2:
                return False, f"red_{type(exc).__name__}"
            time.sleep(3)

    if depth >= 2 or not next_links:
        return False, "html_sin_pdf"

    last = "html_sin_pdf"
    for link in next_links[:3]:
        if link == url:
            continue
        ok, status = try_download(session, link, dest, extra_headers, final_url, depth + 1)
        if ok:
            return True, status
        last = status
    return False, last


# ============================================================
# PRINCIPAL
# ============================================================

def main():
    print("\n==============================================")
    print(" DESCARGADOR DE PAPERS v2 (multi-fuente)")
    print("==============================================")

    if not INPUT_EXCEL.exists():
        print(f"\nERROR: no se encontró {INPUT_EXCEL}")
        sys.exit(1)

    PDF_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(INPUT_EXCEL).astype(object)
    for col, default in {
        "pdf_downloaded": False, "pdf_path": "", "pdf_status": "",
        "pdf_source": "", "pdf_url_used": "", "doi_url": "",
    }.items():
        df[col] = default
        df[col] = df[col].astype(object)

    if MAX_ROWS:
        df = df.head(MAX_ROWS).copy()

    total = len(df)
    print(f"\n✓ Registros: {total}")
    print(f"  Correo (Unpaywall/OpenAlex): {CONTACT_EMAIL or 'NO DEFINIDO'}")
    print(f"  Semantic Scholar key: {'sí' if S2_KEY else 'no'} | Elsevier key: {'sí' if ELSEVIER_KEY else 'no'}")

    # ---------- 1. Qué falta por descargar ----------
    todo = []   # (index, number, title, doi, dest)

    for pos, (idx, row) in enumerate(df.iterrows(), start=1):
        title = clean_text(row.get("title"))
        doi = clean_doi(row.get("doi"))
        dest = PDF_DIR / f"{pos:04d}_{safe_filename(title)}.pdf"
        df.at[idx, "doi_url"] = f"https://doi.org/{doi}" if doi else ""

        existing = [p for p in PDF_DIR.glob(f"{pos:04d}*.pdf") if is_valid_pdf(p)]
        if existing:
            df.at[idx, "pdf_downloaded"] = True
            df.at[idx, "pdf_path"] = str(existing[0].relative_to(BASE_DIR))
            df.at[idx, "pdf_status"] = "ya_existia"
            continue

        if not doi:
            df.at[idx, "pdf_status"] = "sin_doi"
            continue

        todo.append((idx, pos, title, doi, dest))

    print(f"  Ya descargados: {total - len(todo) - int((df['pdf_status'] == 'sin_doi').sum())}"
          f" | Por intentar: {len(todo)}\n")

    # ---------- 2. Candidatos de todas las fuentes ----------
    dois = [t[3] for t in todo]
    cands = {}
    if dois:
        for source_map in (
            candidates_unpaywall(dois),
            candidates_openalex(dois),
            candidates_semantic_scholar(dois),
        ):
            for doi, urls in source_map.items():
                cands.setdefault(doi, []).extend(urls)

    # ---------- 3. Descargar ----------
    session = requests.Session()
    stats = {"descargados": 0, "fallidos": 0}

    try:
        for n, (idx, pos, title, doi, dest) in enumerate(todo, start=1):
            print("\n----------------------------------------------")
            print(f"[{pos}/{total}] {title[:100]}")
            print(f"DOI: {doi}")

            # Primero URLs directas a PDF; después páginas HTML; patrones al final
            found = cands.get(doi.lower(), [])
            direct = [c for c in found if not c[0].endswith("landing") and c[0] != "openalex_oa_url"]
            landing = [c for c in found if c[0].endswith("landing") or c[0] == "openalex_oa_url"]
            ordered = dedupe(direct + candidates_patterns(doi)[:1] + landing + candidates_patterns(doi)[1:])
            ordered = ordered[:MAX_CANDIDATES]

            errors, success = [], False
            for source, url, extra in ordered:
                print(f"   → [{source}] {url[:95]}")
                ok, status = try_download(session, url, dest, extra, referer="https://www.google.com/")
                if ok:
                    size = dest.stat().st_size / 1024 / 1024
                    print(f"   ✓ DESCARGADO ({size:.2f} MB) vía {source}")
                    df.at[idx, "pdf_downloaded"] = True
                    df.at[idx, "pdf_path"] = str(dest.relative_to(BASE_DIR))
                    df.at[idx, "pdf_status"] = "descargado"
                    df.at[idx, "pdf_source"] = source
                    df.at[idx, "pdf_url_used"] = url
                    stats["descargados"] += 1
                    success = True
                    break
                errors.append(f"{source}:{status}")

            if not success:
                print("   ✗ No se pudo descargar:", ", ".join(errors) or "sin candidatos")
                df.at[idx, "pdf_status"] = "fallido: " + (", ".join(errors) or "sin_candidatos")
                stats["fallidos"] += 1

            if n % SAVE_EVERY == 0:
                df.to_excel(OUTPUT_EXCEL, index=False)
                print(f"   💾 Progreso guardado ({n}/{len(todo)})")

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n\nInterrumpido. Guardando progreso... (puedes volver a ejecutar)")

    # ---------- 4. Guardar y resumen ----------
    try:
        df.to_excel(OUTPUT_EXCEL, index=False)
    except PermissionError:
        print("\n⚠ Cierra el Excel si lo tienes abierto y vuelve a ejecutar.")

    pending = df[~df["pdf_downloaded"].astype(bool)].copy()
    pending_out = pd.DataFrame({
        "numero": [int(i) + 1 for i in pending.index],
        "guardar_como": [f"{int(i) + 1:04d}.pdf" for i in pending.index],
        "titulo": pending["title"],
        "enlace_doi": pending["doi_url"],
        "open_access_scopus": pending.get("open_access"),
        "motivo": pending["pdf_status"],
    })
    pending_out.to_csv(PENDING_CSV, index=False, encoding="utf-8-sig")

    ok_total = int(df["pdf_downloaded"].astype(bool).sum())
    print("\n==============================================")
    print(" PROCESO FINALIZADO")
    print("==============================================")
    print(f"PDFs disponibles:   {ok_total} / {total}")
    print(f"Nuevos esta vez:    {stats['descargados']}")
    print(f"Pendientes:         {total - ok_total}  -> {PENDING_CSV.name}")
    print("\nPor fuente:")
    print(df.loc[df["pdf_downloaded"].astype(bool), "pdf_source"]
          .replace("", "ya_existia").value_counts().to_string())
    print(f"\nExcel: {OUTPUT_EXCEL}")
    print(f"PDFs:  {PDF_DIR}\n")


if __name__ == "__main__":
    main()
