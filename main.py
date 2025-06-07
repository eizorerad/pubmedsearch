import asyncio
import logging
from typing import List, Optional
from contextlib import asynccontextmanager

import aiohttp
import uvicorn
import redis.asyncio as aioredis
from fastapi import FastAPI, Query, Depends, Path, HTTPException, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from schemas import Article, ArticleSummary, SpellCheckResponse, SemanticSearchResult, CitationResponse
from client import NCBIClient

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# --- Application Lifecycle Management ---
# Create a dictionary to store state, e.g., sessions and connection pools
app_state = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager to handle FastAPI's lifespan events.
    Creates and closes session/connection pools on startup and shutdown.
    """
    # Create aiohttp session
    logging.info("Application startup: creating aiohttp session.")
    app_state["http_session"] = aiohttp.ClientSession()
    
    # Create Redis connection pool
    logging.info(f"Application startup: creating Redis connection pool at {settings.REDIS_HOST}:{settings.REDIS_PORT}")
    try:
        redis_pool = aioredis.ConnectionPool.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
            max_connections=10,
            decode_responses=True
        )
        app_state["redis_pool"] = redis_pool
        # Check connection
        async with aioredis.Redis(connection_pool=redis_pool) as r:
            await r.ping()
        logging.info("Successfully connected to Redis.")
    except Exception as e:
        logging.error(f"Could not connect to Redis: {e}. Caching will be disabled.")
        app_state["redis_pool"] = None

    yield
    
    # Close connections
    logging.info("Application shutdown: closing aiohttp session.")
    await app_state["http_session"].close()
    
    redis_pool = app_state.get("redis_pool")
    if redis_pool:
        logging.info("Application shutdown: closing Redis connection pool.")
        await redis_pool.disconnect()

# --- FastAPI App Creation ---
app = FastAPI(
    title=settings.APP_TITLE,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Dependencies and Security ---
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def get_http_session() -> aiohttp.ClientSession:
    """Dependency to get the aiohttp session from the application state."""
    return app_state["http_session"]

def get_redis() -> Optional[aioredis.Redis]:
    """Dependency to get an async Redis client from the connection pool."""
    if app_state.get("redis_pool"):
        return aioredis.Redis(connection_pool=app_state["redis_pool"])
    return None

def get_ncbi_client(
    session: aiohttp.ClientSession = Depends(get_http_session),
    redis_client: Optional[aioredis.Redis] = Depends(get_redis)
) -> NCBIClient:
    """Dependency to get the NCBI client, injecting the Redis client."""
    return NCBIClient(session=session, redis_client=redis_client)

async def get_api_key(api_key: str = Security(api_key_header)):
    """Checks the API key provided in the X-API-Key header."""
    if api_key == settings.API_KEY:
        return api_key
    else:
        raise HTTPException(
            status_code=403,
            detail="Could not validate credentials",
        )

# --- API Endpoints ---
@app.get("/search", response_model=List[Article], summary="Search for articles (full data)", tags=["Search"], dependencies=[Depends(get_api_key)])
async def search_articles(
    query: str = Query(..., description="Search query"),
    client: NCBIClient = Depends(get_ncbi_client),
    search_field: Optional[str] = Query(None, regex=r"^\[[A-Za-z]{2,4}\]$", description="Search field, e.g., [AU] for author"),
    max_results: int = Query(10, ge=1, le=100, description="Maximum number of results"),
    start_date: Optional[str] = Query(None, regex=r"^\d{4}/\d{2}/\d{2}$", description="Start date in YYYY/MM/DD format"),
    end_date: Optional[str] = Query(None, regex=r"^\d{4}/\d{2}/\d{2}$", description="End date in YYYY/MM/DD format")
):
    """
    Searches for articles in PubMed and returns a full set of data for each article,
    including abstract, MeSH terms, and DOI.
    """
    pmids = await client.search_pubmed_ids(query, search_field, max_results, start_date, end_date)
    if not pmids:
        return []
    
    tasks = [client.fetch_article_details(pmid) for pmid in pmids]
    results = await asyncio.gather(*tasks)
    return [res for res in results if res]

@app.get("/search/summary", response_model=List[ArticleSummary], summary="Quick search (summary)", tags=["Search"], dependencies=[Depends(get_api_key)])
async def search_summaries(
    query: str = Query(..., description="Search query"),
    client: NCBIClient = Depends(get_ncbi_client),
    search_field: Optional[str] = Query(None, regex=r"^\[[A-Za-z]{2,4}\]$", description="Search field"),
    max_results: int = Query(10, ge=1, le=100, description="Maximum number of results"),
    start_date: Optional[str] = Query(None, regex=r"^\d{4}/\d{2}/\d{2}$", description="Start date"),
    end_date: Optional[str] = Query(None, regex=r"^\d{4}/\d{2}/\d{2}$", description="End date")
):
    """
    Performs a quick search and returns only a brief summary of the articles:
    PMID, title, date, journal, and authors.
    """
    pmids = await client.search_pubmed_ids(query, search_field, max_results, start_date, end_date)
    return await client.get_summaries(pmids)

@app.get("/search/guidelines", response_model=List[Article], summary="Search for guidelines with full text", tags=["Search"], dependencies=[Depends(get_api_key)])
async def search_guidelines(
    query: str = Query(..., description="Topic to search for guidelines"),
    client: NCBIClient = Depends(get_ncbi_client),
    max_results: int = Query(10, ge=1, le=100, description="Maximum number of results")
):
    """
    Performs a specialized search for clinical guidelines,
    with a mandatory filter for free full text availability.
    """
    # Construct the complex search query
    guideline_query = f'({query}) AND ("guideline"[Publication Type] OR "practice guideline"[Publication Type]) AND "free full text"[Filter]'
    
    pmids = await client.search_pubmed_ids(guideline_query, search_field=None, max_results=max_results, start_date=None, end_date=None)
    if not pmids:
        return []
    
    tasks = [client.fetch_article_details(pmid) for pmid in pmids]
    results = await asyncio.gather(*tasks)
    return [res for res in results if res]

@app.get("/search/citations", response_model=List[CitationResponse], summary="Search and get citations (AMA format)", tags=["Search"], dependencies=[Depends(get_api_key)])
async def search_and_get_citations(
    query: str = Query(..., description="Search query"),
    client: NCBIClient = Depends(get_ncbi_client),
    search_field: Optional[str] = Query(None, regex=r"^\[[A-Za-z]{2,4}\]$", description="Search field, e.g., [AU] for author"),
    max_results: int = Query(10, ge=1, le=100, description="Maximum number of results"),
    start_date: Optional[str] = Query(None, regex=r"^\d{4}/\d{2}/\d{2}$", description="Start date in YYYY/MM/DD format"),
    end_date: Optional[str] = Query(None, regex=r"^\d{4}/\d{2}/\d{2}$", description="End date in YYYY/MM/DD format")
):
    """
    Searches for articles and returns a list of structured citations in AMA format, including a link to each article.
    """
    pmids = await client.search_pubmed_ids(query, search_field, max_results, start_date, end_date)
    if not pmids:
        return []
    
    tasks = [client.fetch_article_details(pmid) for pmid in pmids]
    articles = await asyncio.gather(*tasks)
    
    response = [
        CitationResponse(citation=article.to_ama_citation(), link=article.link)
        for article in articles if article
    ]
    return response

@app.get("/semantic_search/{pmid}", response_model=List[SemanticSearchResult], summary="Search for semantically similar articles (Demo)", tags=["Discovery"], dependencies=[Depends(get_api_key)])
async def semantic_search(
    pmid: str = Path(..., regex=r"^\d+$", description="PMID of the source article for similarity search"),
    client: NCBIClient = Depends(get_ncbi_client),
    max_results: int = Query(10, ge=1, le=100, description="Maximum number of results")
):
    """
    **Demonstration endpoint.** Finds articles that are semantically similar to the source article.
    In a real application, this endpoint would use vector embeddings.
    Here, ELink logic is used for demonstration purposes.
    """
    # Step 1: Check if the source article exists
    source_article = await client.fetch_article_details(pmid)
    if not source_article:
        raise HTTPException(status_code=404, detail=f"Source article with PMID {pmid} not found.")

    # Step 2: Simulate a vector DB search using ELink
    logging.info("Demo mode: using ELink to simulate semantic search...")
    similar_pmids = await client.find_related_ids(pmid, max_results)
    if not similar_pmids:
        return []
    
    similar_pmids = [rel_pmid for rel_pmid in similar_pmids if rel_pmid != pmid][:max_results]

    # Step 3: Get full details for the found articles
    tasks = [client.fetch_article_details(rel_pmid) for rel_pmid in similar_pmids]
    articles = [res for res in await asyncio.gather(*tasks) if res is not None]

    # Step 4: Formulate the response with a simulated similarity score
    response = []
    for i, article in enumerate(articles):
        # Simulate a decreasing similarity score
        score = 0.95 - (i * 0.05)
        response.append(SemanticSearchResult(score=max(0.1, score), article=article))
        
    return response

@app.get("/spellcheck", response_model=SpellCheckResponse, summary="Spell Check", tags=["Utilities"], dependencies=[Depends(get_api_key)])
async def spell_check(
    query: str = Query(..., description="Short text to spell check (up to 500 characters)", max_length=500),
    client: NCBIClient = Depends(get_ncbi_client)
):
    """
    Checks the spelling of a search query and suggests a correction.
    **Note:** This endpoint is intended for short search phrases, not long texts or titles.
    """
    return await client.check_spelling(query)

@app.get("/", summary="Check Status", tags=["Status"])
def read_root():
    """Root endpoint to check the API's operational status."""
    return {"status": "PubMed Search API is running"}

# --- Application Entry Point ---
# In production, this application should be run via an ASGI server like Gunicorn with Uvicorn workers.
# Example command:
# gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app -b 0.0.0.0:8000
if __name__ == "__main__":
    # This part is for convenient local development, but without reload.
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
