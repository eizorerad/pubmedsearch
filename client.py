import asyncio
import logging
from typing import List, Optional, Any
from xml.etree import ElementTree
import re
import json
import hashlib

import aiohttp
import redis.asyncio as aioredis
from pydantic import ValidationError, HttpUrl

from schemas import Article, ArticleSummary, SpellCheckResponse
from config import settings

class NCBIClient:
    """
    Client for asynchronous interaction with the NCBI E-utils API, with Redis caching.
    """
    BASE_PARAMS = {"db": "pubmed"}
    MAX_RETRIES = 4
    RETRY_DELAY = 2.0

    def __init__(self, session: aiohttp.ClientSession, redis_client: Optional[aioredis.Redis]):
        self._session = session
        self._redis = redis_client
        self._api_key = settings.NCBI_API_KEY
        
        concurrency_limit = settings.CONCURRENCY_LIMIT_WITH_KEY if self._api_key else settings.CONCURRENCY_LIMIT_WITHOUT_KEY
        self._semaphore = asyncio.Semaphore(concurrency_limit)
        
        logging.info(f"NCBIClient initialized. Concurrency limit: {concurrency_limit}")
        if not self._redis:
            logging.warning("Redis client not provided. Caching will be disabled.")

    def _generate_cache_key(self, url: str, params: dict) -> str:
        """Generates a consistent hash key for caching."""
        key_string = f"{url}?{json.dumps(params, sort_keys=True)}"
        return f"pubmed_cache:{hashlib.md5(key_string.encode()).hexdigest()}"

    async def _make_request(self, url: str, params: dict, response_type: str = 'json') -> Optional[Any]:
        """
        Performs an HTTP request to the API with retries and error handling,
        and uses Redis for caching.
        """
        if self._api_key:
            params["api_key"] = self._api_key

        cache_key = self._generate_cache_key(url, params)
        if self._redis:
            try:
                cached_result = await self._redis.get(cache_key)
                if cached_result:
                    logging.info(f"Cache hit for key: {cache_key}")
                    return json.loads(cached_result)
            except aioredis.RedisError as e:
                logging.error(f"Redis GET error: {e}. Proceeding without cache.")

        logging.info(f"Cache miss for key: {cache_key}. Fetching from source.")
        
        last_exception = None
        for attempt in range(self.MAX_RETRIES):
            try:
                async with self._semaphore:
                    timeout = aiohttp.ClientTimeout(total=30)
                    async with self._session.get(url, params=params, timeout=timeout) as response:
                        response.raise_for_status()
                        
                        result = await response.json() if response_type == 'json' else await response.text()
                        
                        if self._redis:
                            try:
                                await self._redis.set(cache_key, json.dumps(result), ex=settings.CACHE_TTL)
                                logging.info(f"Result for key {cache_key} stored in cache.")
                            except aioredis.RedisError as e:
                                logging.error(f"Redis SET error: {e}.")
                        
                        return result
            except aiohttp.ClientError as e:
                last_exception = e
                if isinstance(e, aiohttp.ClientResponseError) and (e.status == 429 or e.status >= 500):
                    delay = self.RETRY_DELAY ** attempt
                    logging.warning(f"Attempt {attempt + 1}/{self.MAX_RETRIES} failed ({e}). Retrying in {delay:.1f} sec.")
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(delay)
                    continue
                else:
                    logging.error(f"Unrecoverable client error for URL {url}: {e}")
                    break
        
        logging.error(f"Failed to execute request to {url} after {self.MAX_RETRIES} attempts. Last error: {last_exception}")
        return None

    async def search_pubmed_ids(self, query: str, search_field: Optional[str], max_results: int, start_date: Optional[str], end_date: Optional[str]) -> List[str]:
        """Search for article IDs in PubMed."""
        final_query = f"({query}){search_field}" if search_field and not re.search(r'\[[A-Za-z]{2,4}\]', query) else query
        params = {**self.BASE_PARAMS, "term": final_query, "retmode": "json", "retmax": str(max_results), "sort": "date"}
        if start_date:
            params.update({"datetype": "pdat", "mindate": start_date})
        if end_date:
            params.update({"datetype": "pdat", "maxdate": end_date})
        
        data = await self._make_request(f"{settings.EUTILS_BASE_URL}esearch.fcgi", params)
        return data.get("esearchresult", {}).get("idlist", []) if data else []

    async def check_spelling(self, query: str) -> SpellCheckResponse:
        """Check spelling of a query."""
        params = {**self.BASE_PARAMS, "term": query, "retmode": "json"}
        data = await self._make_request(f"{settings.EUTILS_BASE_URL}espell.fcgi", params)
        if not data: return SpellCheckResponse(original_query=query, corrected_query=None, has_correction=False)
        corrected = data.get("es-result", {}).get("corrected")
        return SpellCheckResponse(original_query=query, corrected_query=corrected, has_correction=bool(corrected and corrected.lower() != query.lower()))
    
    async def get_summaries(self, pmids: List[str]) -> List[ArticleSummary]:
        """Get brief summaries for a list of PMIDs."""
        if not pmids:
            return []
        params = {**self.BASE_PARAMS, "id": ",".join(pmids), "retmode": "json"}
        data = await self._make_request(f"{settings.EUTILS_BASE_URL}esummary.fcgi", params)
        if not (data and "result" in data):
            return []
        
        summaries = []
        result = data["result"]
        for pmid in result.get("uids", []):
            if pmid in result:
                info = result[pmid]
                try:
                    summary = ArticleSummary(
                        pmid=info.get("uid", ""),
                        title=info.get("title", "No Title"),
                        pub_date=info.get("pubdate", ""),
                        journal=info.get("source", ""),
                        authors=[a.get("name", "") for a in info.get("authors", [])]
                    )
                    summaries.append(summary)
                except ValidationError as e:
                    logging.warning(f"Validation error for PMID {pmid} in get_summaries: {e}")
        return summaries

    async def find_related_ids(self, source_pmid: str, max_results: int) -> List[str]:
        """Find related articles via ELink."""
        params = {**self.BASE_PARAMS, "id": source_pmid, "linkname": "pubmed_pubmed", "cmd": "neighbor_history"}
        data = await self._make_request(f"{settings.EUTILS_BASE_URL}elink.fcgi", params)
        if not (data and data.get("linksets")):
            return []
        
        linkset = data["linksets"][0]
        webenv = linkset.get("webenv")
        query_key = linkset["linksetdbs"][0].get("querykey")
        
        fetch_params = {**self.BASE_PARAMS, "query_key": query_key, "WebEnv": webenv, "retmode": "json", "retmax": str(max_results)}
        fetch_data = await self._make_request(f"{settings.EUTILS_BASE_URL}esearch.fcgi", fetch_params)
        return fetch_data.get("esearchresult", {}).get("idlist", []) if fetch_data else []

    async def fetch_article_details(self, pmid: str) -> Optional[Article]:
        """Fetch full details for an article by its PMID."""
        params = {**self.BASE_PARAMS, "id": pmid, "retmode": "xml", "rettype": "abstract"}
        url = f"{settings.EUTILS_BASE_URL}efetch.fcgi"
        try:
            xml_data = await self._make_request(url, params, 'text')
            if not xml_data:
                return None
            
            root = ElementTree.fromstring(xml_data)
            article_node = root.find(".//PubmedArticle")
            if article_node is None:
                return None

            title_node = article_node.find(".//ArticleTitle")
            title = "".join(title_node.itertext()).strip() if title_node is not None else "No Title Found"
            
            authors = [f"{a.findtext('LastName', '')} {a.findtext('Initials', '')}".strip() for a in article_node.findall(".//AuthorList/Author")]
            journal = article_node.findtext(".//Journal/Title", "N/A")
            pub_date = article_node.findtext(".//PubDate/Year", "N/A")
            volume = article_node.findtext(".//Journal/JournalIssue/Volume")
            issue = article_node.findtext(".//Journal/JournalIssue/Issue")
            pages = article_node.findtext(".//Pagination/MedlinePgn")
            
            mesh_terms = [term.text for term in article_node.findall(".//MeshHeadingList/MeshHeading/DescriptorName") if term.text]
            
            doi_node = article_node.find(f".//ArticleIdList/ArticleId[@IdType='doi']")
            doi = doi_node.text if doi_node is not None else None
            
            abstract_parts = []
            for node in article_node.findall(".//Abstract/AbstractText"):
                full_text = "".join(node.itertext()).strip()
                if not full_text:
                    continue
                
                label = node.get('Label')
                if label:
                    part = f"**{label.strip()}:** {full_text}"
                else:
                    part = full_text
                abstract_parts.append(part)
            abstract = "\n\n".join(abstract_parts) or "Abstract not available."

            return Article(
                pmid=pmid,
                title=title.strip(),
                abstract=abstract,
                authors=authors,
                journal=journal,
                pub_date=pub_date,
                mesh_terms=mesh_terms,
                doi=doi,
                link=HttpUrl(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"),
                volume=volume,
                issue=issue,
                pages=pages
            )
        except (ElementTree.ParseError, ValidationError) as e:
            logging.error(f"Error processing data for PMID {pmid}: {e}")
            return None
