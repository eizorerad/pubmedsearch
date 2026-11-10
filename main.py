"""PubMed Search API: a small key-protected HTTP wrapper around the NCBI E-utilities."""
import logging
import os
import re
import secrets

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

import pubmed

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("API_KEY is not set: copy .env.example to .env and fill it in")

DATE = r"^[0-9]{4}/[0-9]{2}/[0-9]{2}$"
FIELD_TAG = re.compile(r"\[[A-Za-z][A-Za-z /:-]*\]")  # [AU], [MeSH Terms], ... but not [18F]
GUIDELINES = ('({}) AND ("guideline"[Publication Type] OR "practice guideline"[Publication Type]) '
              'AND "free full text"[Filter]')
log = logging.getLogger("uvicorn.error")


class ArticleSummary(BaseModel):
    pmid: str
    title: str
    pub_date: str
    journal: str
    authors: list[str]


class Article(ArticleSummary):
    abstract: str
    mesh_terms: list[str] = []
    doi: str | None = None
    link: str
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None


class CitationResponse(BaseModel):
    citation: str
    link: str


class SemanticSearchResult(BaseModel):
    score: float
    article: Article


class SpellCheckResponse(BaseModel):
    original_query: str
    corrected_query: str | None = None
    has_correction: bool


def check_api_key(key: str | None = Security(APIKeyHeader(name="X-API-Key", auto_error=False))):
    if not secrets.compare_digest((key or "").encode(), API_KEY.encode()):
        raise HTTPException(403, "Could not validate credentials")


app = FastAPI(title="PubMed Search API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["*"])
api = APIRouter(dependencies=[Depends(check_api_key)])


@app.exception_handler(pubmed.NCBIError)
async def ncbi_failed(request, exc):
    log.warning("NCBI request failed: %s", exc)
    return JSONResponse({"detail": f"PubMed request failed: {exc}"}, status_code=502)


@app.get("/")
def read_root():
    return {"status": "PubMed Search API is running"}


async def search_ids(
    query: str = Query(min_length=1, description="PubMed query"),
    search_field: str | None = Query(None, pattern=r"^\[[A-Za-z]{2,4}\]$",
                                     description="Field tag such as [AU], [TI], [MH] for a one-term or one-phrase "
                                                 "query; skipped if the query has its own tags"),
    max_results: int = Query(10, ge=1, le=100),
    start_date: str | None = Query(None, pattern=DATE, description="Publication date from, YYYY/MM/DD"),
    end_date: str | None = Query(None, pattern=DATE, description="Publication date to, YYYY/MM/DD"),
) -> list[str]:
    """Query parameters shared by the /search endpoints; returns the matching PMIDs."""
    term = f"({query}){search_field}" if search_field and not FIELD_TAG.search(query) else query
    return await pubmed.search(term, max_results, start_date, end_date)


@api.get("/search", response_model=list[Article])
async def search_articles(pmids: list[str] = Depends(search_ids)):
    """Full records (abstract, MeSH terms, DOI, ...), most recent first."""
    return await pubmed.fetch(pmids)


@api.get("/search/summary", response_model=list[ArticleSummary])
async def search_summaries(pmids: list[str] = Depends(search_ids)):
    """Short records: PMID, title, date, journal and authors."""
    return await pubmed.summaries(pmids)


@api.get("/search/citations", response_model=list[CitationResponse])
async def search_and_get_citations(pmids: list[str] = Depends(search_ids)):
    """AMA-style citations, each with its PubMed link."""
    return [{"citation": pubmed.ama_citation(a), "link": a["link"]} for a in await pubmed.fetch(pmids)]


@api.get("/search/guidelines", response_model=list[Article])
async def search_guidelines(query: str = Query(min_length=1, description="Topic"),
                            max_results: int = Query(10, ge=1, le=100)):
    """Guidelines and practice guidelines that have free full text."""
    return await pubmed.fetch(await pubmed.search(GUIDELINES.format(query), max_results))


@api.get("/semantic_search/{pmid}", response_model=list[SemanticSearchResult])
async def semantic_search(pmid: str = Path(pattern=r"^[0-9]+$", description="PMID of the source article"),
                          max_results: int = Query(10, ge=1, le=100)):
    """PubMed's "Similar articles". score is PubMed's similarity relative to the best match (1.0)."""
    links = (await pubmed.related(pmid))[:max_results]
    if not links:
        if not await pubmed.fetch([pmid]):
            raise HTTPException(404, f"Source article with PMID {pmid} not found.")
        return []
    best = links[0][1]
    scores = dict(links)
    articles = await pubmed.fetch(list(scores))
    return [{"score": round(scores[a["pmid"]] / best, 3), "article": a} for a in articles if a["pmid"] in scores]


@api.get("/spellcheck", response_model=SpellCheckResponse)
async def spell_check(query: str = Query(min_length=1, max_length=500, description="Short search phrase")):
    """NCBI's spelling suggestion for a search phrase."""
    corrected = await pubmed.spell(query)
    return {"original_query": query, "corrected_query": corrected or None,
            "has_correction": bool(corrected) and corrected.lower() != query.lower()}


app.include_router(api)  # must stay below the @api routes: it copies the routes defined so far
