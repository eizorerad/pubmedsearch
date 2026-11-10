# PubMed Search API

A small FastAPI service that searches PubMed through the NCBI E-utilities and returns JSON:
full records, short summaries, AMA citations, guidelines, similar articles and spelling suggestions.

## Run

```bash
cp .env.example .env            # set API_KEY, e.g. openssl rand -hex 32
docker compose up -d --build    # http://localhost (port 80)
```

Without Docker (Python 3.11+):

```bash
pip install -r requirements.txt
uvicorn main:app --env-file .env    # http://127.0.0.1:8000
```

`NCBI_API_KEY` in `.env` is optional; it raises the NCBI limit from 3 to 10 requests per second.
On a public server, put the API behind HTTPS, since the key travels in a header.

## Use

Every endpoint except `/` and the docs (`/docs`, `/redoc`, `/openapi.json`) needs the `X-API-Key` header:

```bash
curl -H "X-API-Key: $API_KEY" "http://localhost/search?query=asthma&max_results=5"
```

| Endpoint | Returns |
| --- | --- |
| `GET /search` | full records: abstract, MeSH terms, DOI, volume, issue, pages |
| `GET /search/summary` | PMID, title, date, journal, authors |
| `GET /search/citations` | AMA citations with PubMed links |
| `GET /search/guidelines?query=` | guidelines with free full text |
| `GET /semantic_search/{pmid}` | PubMed "Similar articles"; `score` is relative to the best match (1.0) |
| `GET /spellcheck?query=` | NCBI spelling suggestion |

The three `/search` endpoints take `query`, `max_results` (1–100, default 10), `start_date` / `end_date`
(`YYYY/MM/DD`, publication date) and `search_field`, a tag such as `[AU]`, `[TI]`, `[TIAB]`, `[MH]`, `[JOUR]`
or `[PT]` for a one-term or one-phrase query (skipped if the query has its own tags).
Results come in PubMed's default order, most recently added first.

If NCBI fails or reports an error, the API returns `502` with the reason; failed calls are retried
up to three times and never cached. Good replies are cached in memory for an hour. The cache and the
NCBI rate limit live in the process, so run a single process (the default).

## Test

```bash
pip install pytest && pytest
```
