# Revisión sistemática: microservicios vs. arquitecturas monolíticas en plataformas cloud

Herramientas en Python para apoyar la revisión de literatura del artículo
**"Arquitecturas de microservicios frente a monolíticas en plataformas cloud: un análisis comparativo de ventajas, desafíos y criterios de selección"**.

El flujo busca artículos en Scopus (con registro PRISMA), descarga los PDFs open access y extrae las frases donde los autores señalan brechas, retos abiertos, trabajo futuro y limitaciones.

## Estructura de la carpeta

```
proyecto/
├── .env                          <- tus claves y parámetros (NO se sube a GitHub)
├── .gitignore
├── requirements.txt
├── README.md
├── scopus_prisma_multisearch.py  <- paso 1
├── descarga_pdf_v2.py            <- paso 2
├── extraer_brechas.py            <- paso 3
├── scimagojr.csv                 <- opcional (cuartiles)
├── papers/                       <- se crea solo (PDFs)
└── scopus_prisma_multisearch/    <- se crea solo (Excel, JSON, PRISMA)
```

## Instalación

Requiere Python 3.10 o superior. En PowerShell, dentro de la carpeta del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuración (archivo `.env`)

Todas las variables se editan **solo en `.env`**; no hay que tocar los `.py`.
Los tres scripts lo cargan automáticamente al ejecutarse.

| Variable | ¿Obligatoria? | Para qué sirve |
|---|---|---|
| `ELSEVIER_API_KEY` | Sí (paso 1) | API key de Scopus, se obtiene en dev.elsevier.com |
| `CONTACT_EMAIL` | Recomendada (paso 2) | Correo institucional; lo piden Unpaywall y OpenAlex |
| `ELSEVIER_INST_TOKEN` | No | Token institucional de Elsevier, si tu universidad lo tiene |
| `SEMANTIC_SCHOLAR_API_KEY` | No | Reduce errores 429 en Semantic Scholar |
| `START_YEAR` | No | Año mínimo de publicación (por defecto 2019) |
| `MAX_RESULTS_PER_SEARCH` | No | Tope de registros por búsqueda (vacío = todos) |
| `OPEN_ACCESS_ONLY` | No | `true` = solo artículos Open Access |
| `EXTRA_FILTERS` | No | Filtros extra de la consulta Scopus (tipo de documento, idioma, área) |
| `SJR_FILE`, `ONLY_Q1_Q2` | No | Filtro por cuartil usando el CSV de SCImago |
| `DOWNLOAD_ELSEVIER_PDF` | No | Intentar bajar PDFs por la API de Elsevier |
| `MAX_ROWS` | No | Paso 2: limitar filas (útil para pruebas) |
| `MAX_CANDIDATES`, `SAVE_EVERY` | No | Paso 2: URLs a probar por paper y frecuencia de guardado |
| `MAX_PDFS` | No | Paso 3: limitar PDFs (útil para pruebas) |
| `MAX_PER_CATEGORY_PER_PAPER`, `MAX_SNIPPET_CHARS` | No | Paso 3: límites de frases por paper y longitud |

Reglas del formato: un valor por línea, sin comillas y sin espacios alrededor del `=`.
Una variable vacía (por ejemplo `MAX_ROWS=`) significa "sin límite" o "no usar".

> **Seguridad:** nunca escribas las claves dentro de los `.py` ni las pegues en chats o repositorios.
> El `.gitignore` incluido excluye `.env`, pero verifica con `git status` antes de hacer un commit.

## Orden de ejecución

### Paso 1. Búsqueda en Scopus

```powershell
python scopus_prisma_multisearch.py
```

Ejecuta 8 búsquedas (comparativo, migración, desempeño y costos, criterios de selección; cada una con y sin filtro de revisiones), elimina duplicados y obtiene los resúmenes.

Genera en `scopus_prisma_multisearch/`:

- `scopus_unique_metadata.xlsx` y `.csv`: registros únicos con metadatos
- `prisma_identification_scopus.xlsx`: cifras para el diagrama PRISMA
- `search_queries.txt`: consultas exactas (cítalas en tu metodología)
- `scopus_search_summary.csv` y `scopus_all_search_hits.csv`

### Paso 2. Descarga de PDFs

```powershell
python descarga_pdf_v2.py
```

Prueba Unpaywall, OpenAlex, Semantic Scholar y patrones de editores. Genera la carpeta `papers/`, el Excel `scopus_unique_metadata_semantic_scholar.xlsx` y `pendientes_descarga_manual.csv` con los PDFs que debes bajar a mano.
Puedes interrumpirlo con `Ctrl+C` y volver a ejecutarlo: no repite descargas.

### Paso 3. Extracción de brechas

```powershell
python extraer_brechas.py
```

Lee los PDFs y genera `brechas_extraidas.xlsx` con las hojas `Brechas`, `Resumen_papers`, `Conteo_por_tema`, `Terminos_por_tema` y `Errores`. Cada frase se clasifica en uno de seis temas:

1. Rendimiento y escalabilidad
2. Costos y operación
3. Migración y descomposición
4. Seguridad y resiliencia
5. Datos y consistencia
6. Criterios de decisión

## Prueba rápida antes de la corrida completa

En `.env` pon temporalmente:

```
MAX_RESULTS_PER_SEARCH=10
MAX_ROWS=10
MAX_PDFS=5
```

Verifica que cada paso termine bien y luego deja los valores originales.

## Personalizar las búsquedas y los temas

- **Consultas de Scopus:** en `scopus_prisma_multisearch.py`, los bloques `MICRO`, `MONO`, `CLOUD`, `COMPARA`, `MIGRA`, `DESEMPENO`, `CRITERIOS` y `REVIEWS`, combinados en el diccionario `SEARCHES`.
- **Temas de clasificación:** en `extraer_brechas.py`, el diccionario `THEMES` (palabras clave por tema).
- **Incluir congresos:** muchos trabajos sobre microservicios se publican en congresos. Para incluirlos, en `.env` usa:
  `EXTRA_FILTERS=(DOCTYPE(ar) OR DOCTYPE(cp)) AND LANGUAGE(english) AND SUBJAREA(COMP)`

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `No se encontró ELSEVIER_API_KEY` | `.env` no está en la misma carpeta que el script, o la variable está vacía |
| `Aviso: falta python-dotenv` | Ejecuta `pip install -r requirements.txt` con el entorno virtual activo |
| Error 401 o 403 de Elsevier | Clave inválida o sin permisos; genera una nueva en dev.elsevier.com |
| `PermissionError` al guardar un Excel | Cierra el archivo en Excel y vuelve a ejecutar |
| Muchos PDFs en `pendientes_descarga_manual.csv` | Es normal: solo se bajan los open access; descarga el resto con acceso institucional |
