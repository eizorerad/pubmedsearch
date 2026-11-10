# PubMed Search API

A small FastAPI service that puts PubMed behind a few simple HTTP endpoints with JSON answers:
full records, short summaries, AMA citations, guidelines, similar articles and spelling suggestions.

## Why

PubMed's official API (NCBI E-utilities) is awkward to use directly. Search, records, summaries,
similar articles and spelling are five different utilities, the replies are XML, only 3 requests
per second are allowed without a key, and there are quiet traps, such as a date filter that does
nothing unless both ends are given. This service handles all of that: it stays under the NCBI limit,
retries failed calls and caches replies for an hour.

It is handy for scripts and notebooks that collect literature, for your own apps, and as a PubMed
tool for an LLM assistant: it is protected by a key and described by an OpenAPI schema (`/openapi.json`).

## Quick start

1. Clone the repo. You need Git, plus Docker or Python 3.11+.

   ```bash
   git clone https://github.com/eizorerad/pubmedsearch.git
   cd pubmedsearch
   ```

2. Create `.env` and set `API_KEY`, a password you choose for your service. Clients must send it in
   the `X-API-Key` header, and the service won't start without it. Any long random string works;
   `openssl rand -hex 32` just prints one. `NCBI_API_KEY` is optional: a free key from your NCBI
   account settings raises the limit from 3 to 10 requests per second.

   ```bash
   cp .env.example .env
   ```

3. Start it with Docker (http://localhost, port 80):

   ```bash
   docker compose up -d --build
   ```

   or without Docker (http://127.0.0.1:8000):

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn main:app --env-file .env
   ```

4. Send a first request, or open `/docs`, press Authorize and paste the key:

   ```bash
   curl -H "X-API-Key: YOUR_API_KEY" "http://localhost/search/summary?query=crispr&max_results=3"
   ```

On a public server, put the API behind HTTPS (for example with Caddy), since the key travels in a header.

## Endpoints

Every endpoint except `/` and the docs (`/docs`, `/redoc`, `/openapi.json`) needs the `X-API-Key` header.

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
up to three times and never cached. The cache and the NCBI rate limit live in the process,
so run a single process (the default).

## Test

```bash
pip install pytest && pytest
```
