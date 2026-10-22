# ============================================================
# extraer_brechas.py
#
# Recorre los PDFs descargados y extrae las frases donde los autores
# hablan de: trabajo futuro, brechas/vacíos, retos abiertos y limitaciones.
# Clasifica cada frase en un tema (Rendimiento, Costos, Migración,
# Seguridad, Datos, Criterios de decisión) y genera un Excel para escoger 2-3 temas.
#
# Estructura esperada (la misma de descarga_pdf.py):
#
#   carpeta/
#   ├── extraer_brechas.py
#   ├── papers/                                  <- PDFs
#   └── scopus_prisma_multisearch/
#       └── scopus_unique_metadata_semantic_scholar.xlsx
#
# Instalar:  pip install pymupdf pandas openpyxl
# Ejecutar:  python extraer_brechas.py
# ============================================================

import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

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


try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    try:
        import fitz  # PyMuPDF versiones anteriores
    except ImportError:
        print("Falta PyMuPDF. Instala con:  pip install pymupdf")
        sys.exit(1)


# ============================================================
# CONFIGURACIÓN
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

# Carpetas donde buscar PDFs
PDF_DIRS = [
    BASE_DIR / "papers",
    BASE_DIR / "scopus_prisma_multisearch" / "pdfs",
]

METADATA_XLSX = (
    BASE_DIR
    / "scopus_prisma_multisearch"
    / "scopus_unique_metadata_semantic_scholar.xlsx"
)

OUTPUT_XLSX = BASE_DIR / "brechas_extraidas.xlsx"

# Máximo de frases guardadas por categoría y por paper (evita ruido)
MAX_PER_CATEGORY_PER_PAPER = env_int("MAX_PER_CATEGORY_PER_PAPER", 6)

# Máximo de caracteres por fragmento
MAX_SNIPPET_CHARS = env_int("MAX_SNIPPET_CHARS", 700)

# Para pruebas: procesar solo N PDFs (None = todos)
MAX_PDFS = env_int("MAX_PDFS", None)


# ============================================================
# PATRONES DE BRECHAS (inglés y español)
# Orden = prioridad si una frase coincide con varias categorías
# ============================================================

PATTERNS = {
    "brecha_explicita": [
        r"research gaps?",
        r"gaps? in (?:the )?(?:current |existing )?(?:literature|research|knowledge|state of the art)",
        r"knowledge gaps?",
        r"literature gaps?",
        r"remains? (?:largely |still )?(?:unexplored|understudied|underexplored|unresolved|unaddressed)",
        r"(?:under|less)[- ]?(?:studied|explored|researched|investigated)",
        r"(?:has|have) (?:not|yet to) (?:been )?(?:fully |sufficiently )?(?:explored|addressed|investigated|studied)",
        r"(?:little|limited|scarce) (?:attention|research|work|evidence|studies)",
        r"brechas? (?:de investigaci[oó]n|en la literatura|de conocimiento)",
        r"vac[ií]os? (?:de investigaci[oó]n|en la literatura|de conocimiento)",
        r"poco (?:explorad|estudiad|investigad)",
        r"escasa(?:s)? (?:investigaci[oó]n|investigaciones|estudios)",
    ],
    "linea_futura": [
        r"future (?:work|works|research|studies|study|directions?|investigations?|perspectives?)",
        r"further (?:research|work|studies|study|investigation|exploration)",
        r"in (?:the )?future,? (?:we|this|it|researchers)",
        r"trabajos? futuros?",
        r"l[ií]neas? (?:futuras?|de investigaci[oó]n futuras?)",
        r"investigaci[oó]n(?:es)? futuras?",
        r"futuras? (?:investigaciones|l[ií]neas)",
    ],
    "reto_abierto": [
        r"open (?:challenges?|issues?|problems?|questions?|research (?:challenges?|issues?))",
        r"(?:remaining|key|major|main|critical) challenges?",
        r"challenges? (?:remain|still remain)",
        r"unresolved (?:issues?|problems?|challenges?)",
        r"retos? abiertos?",
        r"desaf[ií]os? (?:abiertos?|pendientes)",
    ],
    "limitacion": [
        r"limitations? (?:of|in) (?:this|our|the present|the current) (?:study|work|research|paper|approach|review|framework)",
        r"(?:this|our|the present|the current) (?:study|work|research|paper|approach|review|framework) (?:has|have|presents?) (?:some |several |certain |a few )?limitations?",
        r"limitaciones? (?:de|del) (?:este|presente|nuestro) (?:estudio|trabajo|investigaci[oó]n)",
    ],
}

COMPILED = {
    cat: re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
    for cat, pats in PATTERNS.items()
}


# ============================================================
# TEMAS del artículo: microservicios vs monolitos en cloud
# Ajusta las palabras si quieres otros ejes de análisis.
# ============================================================

THEMES = {
    "T1_Rendimiento_Escalabilidad": [
        "performance", "scalab", "latency", "throughput", "elasticity",
        "autoscal", "auto-scal", "load balanc", "resource utilization",
        "rendimiento", "escalabilidad", "latencia",
    ],
    "T2_Costos_Operacion": [
        "cost", "economic", "energy", "operational", "overhead", "finops",
        "billing", "pricing", "monitoring", "observability", "devops",
        "costos", "costo", "operaci",
    ],
    "T3_Migracion_Descomposicion": [
        "migration", "decomposition", "refactor", "legacy", "modernization",
        "strangler", "granularity", "boundar", "monolith to microservice",
        "migraci", "descomposici",
    ],
    "T4_Seguridad_Resiliencia": [
        "security", "resilience", "fault tolerance", "availability",
        "reliability", "failure", "attack", "authentication", "zero trust",
        "seguridad", "resiliencia", "disponibilidad",
    ],
    "T5_Datos_Consistencia": [
        "data consistency", "distributed transaction", "database per service",
        "saga", "eventual consistency", "data management", "database",
        "consistencia", "base de datos",
    ],
    "T6_Criterios_Decision": [
        "decision", "selection criteria", "trade-off", "tradeoff", "team",
        "organizational", "maintainability", "technical debt", "complexity",
        "conway", "framework", "criterio", "decisi",
    ],
}


# ============================================================
# STOPWORDS para frecuencia de términos
# ============================================================

STOPWORDS = set("""
a about above after again all also an and any are as at be been being because
between both but by can could did do does during each few for from further had
has have having he her here him his how i if in into is it its may might more
most much must no nor not of on one only or other our out over own same she
should so some such than that the their them then there these they this those
through to too under until up us use used using very was we were what when
where which while who why will with within without would you your
future work works research study studies paper approach approaches
however thus therefore moreover although among based due well also et al
de la el los las un una unos unas y o en por para con sin sobre entre que
se su sus del al es son fue ser como más pero también este esta estos estas
fig table section results result
""".split())

TOKEN_RE = re.compile(r"[a-záéíóúñü][a-záéíóúñü\-]{2,}", re.IGNORECASE)


# ============================================================
# UTILIDADES
# ============================================================

ILLEGAL_XLSX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_cell(value):
    """Quita caracteres que Excel no acepta."""
    if isinstance(value, str):
        return ILLEGAL_XLSX.sub("", value)
    return value


def clean_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def normalize_spaces(text):
    text = text.replace("­", "")          # guion blando
    text = re.sub(r"-\s*\n\s*", "", text)      # palabras cortadas por guion
    text = re.sub(r"\s*\n\s*", " ", text)      # saltos de línea -> espacio
    text = re.sub(r"\s+", " ", text)
    return text.strip()


SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡0-9\[])")

REFS_HEADING = re.compile(
    r"^\s*(?:\d+\.?\s*)?(references|bibliography|referencias|bibliograf[ií]a|literatura citada)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def extract_pages(pdf_path):
    """Devuelve lista de textos por página, sin la lista de referencias."""
    pages = []

    with fitz.open(pdf_path) as doc:
        if getattr(doc, "needs_pass", False):
            raise RuntimeError("PDF con contraseña")

        total = len(doc)
        reached_refs = False

        for i, page in enumerate(doc):
            if reached_refs:
                break

            raw = page.get_text("text") or ""

            # La sección de referencias suele estar en la segunda mitad
            if i >= total * 0.5:
                match = REFS_HEADING.search(raw)
                if match:
                    raw = raw[: match.start()]
                    reached_refs = True

            pages.append(raw)

    return pages


def classify_theme(text):
    """Asigna la unidad con más coincidencias de palabras."""
    low = text.lower()
    scores = {
        theme: sum(low.count(word) for word in words)
        for theme, words in THEMES.items()
    }
    best_theme = max(scores, key=scores.get)

    if scores[best_theme] == 0:
        return "Sin_clasificar"
    return best_theme


def extract_gap_snippets(pages):
    """
    Devuelve lista de dicts: categoria, frase_clave, pagina, fragmento.
    El fragmento es la frase que coincide + la siguiente (contexto).
    """
    found = []
    seen = set()
    per_category = Counter()

    for page_number, raw in enumerate(pages, start=1):
        text = normalize_spaces(raw)

        if not text:
            continue

        sentences = SENTENCE_SPLIT.split(text)

        for idx, sentence in enumerate(sentences):

            if len(sentence) < 40:
                continue

            for category, regex in COMPILED.items():

                match = regex.search(sentence)

                if not match:
                    continue

                if per_category[category] >= MAX_PER_CATEGORY_PER_PAPER:
                    break

                snippet = sentence

                if idx + 1 < len(sentences):
                    snippet = snippet + " " + sentences[idx + 1]

                snippet = snippet[:MAX_SNIPPET_CHARS].strip()

                key = re.sub(r"\W+", "", snippet.lower())[:150]

                if key in seen:
                    break

                seen.add(key)
                per_category[category] += 1

                found.append({
                    "categoria": category,
                    "frase_clave": match.group(0),
                    "pagina": page_number,
                    "fragmento": snippet,
                })

                # Una frase solo cuenta en la primera categoría que coincide
                break

    return found


def tokens_for_terms(text):
    words = [
        w.lower()
        for w in TOKEN_RE.findall(text)
        if w.lower() not in STOPWORDS
    ]
    return words


def bigrams(words):
    return [f"{a} {b}" for a, b in zip(words, words[1:])]


# ============================================================
# METADATOS (título, año, DOI) a partir del Excel de descarga
# ============================================================

def load_metadata():
    """
    Devuelve dict {ruta_absoluta_pdf: fila_metadatos}.
    Si no hay Excel, el script funciona igual usando el nombre del archivo.
    """
    if not METADATA_XLSX.exists():
        print(
            "\n(No se encontró el Excel de metadatos; "
            "se usará el nombre del archivo como título.)"
        )
        return {}

    df = pd.read_excel(METADATA_XLSX)

    if "pdf_path" not in df.columns:
        return {}

    meta = {}

    for _, row in df.iterrows():
        path_value = clean_text(row.get("pdf_path"))

        if not path_value:
            continue

        absolute = (BASE_DIR / path_value).resolve()
        meta[absolute] = row

    return meta


# ============================================================
# PRINCIPAL
# ============================================================

def main():

    print("\n==============================================")
    print(" EXTRACCIÓN DE BRECHAS DESDE PDFs")
    print("==============================================\n")

    pdfs = []

    for folder in PDF_DIRS:
        if folder.exists():
            pdfs.extend(sorted(folder.glob("*.pdf")))

    pdfs = sorted(set(pdfs))

    if MAX_PDFS:
        pdfs = pdfs[:MAX_PDFS]

    if not pdfs:
        print("No se encontraron PDFs en:")
        for folder in PDF_DIRS:
            print(f" - {folder}")
        sys.exit(1)

    print(f"PDFs a procesar: {len(pdfs)}\n")

    metadata = load_metadata()

    rows = []
    paper_rows = []
    errors = []

    for number, pdf in enumerate(pdfs, start=1):

        meta = metadata.get(pdf.resolve())

        title = clean_text(meta.get("title")) if meta is not None else ""
        year = clean_text(meta.get("year")) if meta is not None else ""
        doi = clean_text(meta.get("doi")) if meta is not None else ""
        journal = clean_text(meta.get("publication")) if meta is not None else ""
        cited_by = clean_text(meta.get("cited_by")) if meta is not None else ""
        keywords = clean_text(meta.get("author_keywords")) if meta is not None else ""
        quartile = clean_text(meta.get("sjr_quartile")) if meta is not None else ""

        if not title:
            title = pdf.stem

        print(f"[{number}/{len(pdfs)}] {title[:90]}")

        try:
            pages = extract_pages(pdf)
            snippets = extract_gap_snippets(pages)

        except Exception as exc:
            print(f"   ✗ Error: {type(exc).__name__}: {exc}")
            errors.append({"archivo": pdf.name, "error": f"{type(exc).__name__}: {exc}"})
            continue

        if not snippets:
            print("   - Sin frases de brechas (¿PDF escaneado o sin esas secciones?)")

        paper_counts = Counter(s["categoria"] for s in snippets)

        # El tema del paper se decide con su título + palabras clave
        paper_theme = classify_theme(f"{title} {keywords}")

        for item in snippets:

            theme = classify_theme(f"{item['fragmento']} {title} {keywords}")

            rows.append({
                "archivo": pdf.name,
                "titulo": title,
                "anio": year,
                "doi": doi,
                "revista": journal,
                "cuartil": quartile,
                "citas": cited_by,
                "tema_unidad": theme,
                "categoria": item["categoria"],
                "frase_clave": item["frase_clave"],
                "pagina": item["pagina"],
                "fragmento": item["fragmento"],
            })

        paper_rows.append({
            "archivo": pdf.name,
            "titulo": title,
            "anio": year,
            "doi": doi,
            "revista": journal,
            "cuartil": quartile,
            "citas": cited_by,
            "tema_paper": paper_theme,
            "n_paginas_leidas": len(pages),
            "n_brecha_explicita": paper_counts.get("brecha_explicita", 0),
            "n_linea_futura": paper_counts.get("linea_futura", 0),
            "n_reto_abierto": paper_counts.get("reto_abierto", 0),
            "n_limitacion": paper_counts.get("limitacion", 0),
            "n_total": len(snippets),
        })

    if not rows:
        print("\nNo se extrajo ninguna frase. Revisa que los PDFs tengan texto.")
        sys.exit(1)

    df_gaps = pd.DataFrame(rows)
    df_papers = pd.DataFrame(paper_rows)

    # --------------------------------------------------------
    # Conteo por tema y categoría
    # --------------------------------------------------------

    df_theme = (
        df_gaps
        .groupby(["tema_unidad", "categoria"])
        .agg(
            n_frases=("fragmento", "count"),
            n_papers=("archivo", "nunique"),
        )
        .reset_index()
        .sort_values(["tema_unidad", "n_papers"], ascending=[True, False])
    )

    # --------------------------------------------------------
    # Términos frecuentes (bigramas) por tema
    # Cuenta en cuántos papers distintos aparece cada término
    # --------------------------------------------------------

    term_papers = defaultdict(lambda: defaultdict(set))
    term_mentions = defaultdict(Counter)

    for _, r in df_gaps.iterrows():

        words = tokens_for_terms(r["fragmento"])

        for term in bigrams(words):
            term_papers[r["tema_unidad"]][term].add(r["archivo"])
            term_mentions[r["tema_unidad"]][term] += 1

    term_rows = []

    for theme, terms in term_papers.items():
        ranked = sorted(
            terms.items(),
            key=lambda kv: (len(kv[1]), term_mentions[theme][kv[0]]),
            reverse=True,
        )[:40]

        for term, papers_set in ranked:
            if len(papers_set) < 2:
                continue

            term_rows.append({
                "tema_unidad": theme,
                "termino": term,
                "n_papers": len(papers_set),
                "n_menciones": term_mentions[theme][term],
            })

    df_terms = pd.DataFrame(
        term_rows,
        columns=["tema_unidad", "termino", "n_papers", "n_menciones"],
    )

    df_errors = pd.DataFrame(errors, columns=["archivo", "error"])

    # --------------------------------------------------------
    # Guardar Excel
    # --------------------------------------------------------

    sheets = {
        "Brechas": df_gaps,
        "Resumen_papers": df_papers,
        "Conteo_por_tema": df_theme,
        "Terminos_por_tema": df_terms,
        "Errores": df_errors,
    }

    try:
        with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:

            for name, frame in sheets.items():

                frame = frame.apply(
                    lambda col: col.map(clean_cell)
                    if (col.dtype == object or pd.api.types.is_string_dtype(col))
                    else col
                )

                frame.to_excel(writer, sheet_name=name, index=False)

                ws = writer.sheets[name]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions

                widths = {
                    "titulo": 55, "fragmento": 100, "doi": 30,
                    "archivo": 40, "revista": 30, "frase_clave": 25,
                    "termino": 40,
                }

                for idx, col_name in enumerate(frame.columns, start=1):
                    letter = ws.cell(row=1, column=idx).column_letter
                    ws.column_dimensions[letter].width = widths.get(col_name, 16)

    except PermissionError:
        print(f"\nCierra {OUTPUT_XLSX.name} si lo tienes abierto en Excel y vuelve a ejecutar.")
        sys.exit(1)

    print("\n==============================================")
    print(" PROCESO FINALIZADO")
    print("==============================================")
    print(f"\nPDFs procesados:      {len(paper_rows)}")
    print(f"PDFs con error:       {len(errors)}")
    print(f"Frases extraídas:     {len(df_gaps)}")
    print("\nPor categoría:")
    print(df_gaps["categoria"].value_counts().to_string())
    print("\nPor unidad / tema:")
    print(df_gaps["tema_unidad"].value_counts().to_string())
    print(f"\nExcel generado:\n{OUTPUT_XLSX}\n")


if __name__ == "__main__":
    main()
