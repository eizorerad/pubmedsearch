"""Small async client for PubMed via the NCBI E-utilities."""
import asyncio
import json
import os
import time
import xml.etree.ElementTree as ET

import httpx2
from cachetools import TTLCache

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
NCBI_API_KEY = os.getenv("NCBI_API_KEY")
MIN_INTERVAL = 1.1 / (10 if NCBI_API_KEY else 3)  # NCBI allows 3 requests/s (10 with a key); 10 % margin
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_DELAY = 1.0  # seconds, grows with each attempt
ATTEMPTS = 3

http = httpx2.AsyncClient(base_url=EUTILS, timeout=30)
cache = TTLCache(maxsize=64_000_000, ttl=3600, getsizeof=len)  # about 64 MB of replies, kept for an hour
_lock = asyncio.Lock()
_last_request = 0.0


class NCBIError(Exception):
    """NCBI failed, refused the request, or sent a reply that cannot be used."""


async def _throttle() -> None:
    """Wait so that requests from the whole app stay under the NCBI rate limit."""
    global _last_request
    async with _lock:
        await asyncio.sleep(max(0.0, _last_request + MIN_INTERVAL - time.monotonic()))
        _last_request = time.monotonic()


async def _post(tool: str, params: dict) -> str:
    """POST to an E-utility (keeps the API key out of URLs), retrying on 429, 5xx and network errors."""
    for attempt in range(1, ATTEMPTS + 1):
        await _throttle()
        try:
            response = await http.post(tool, data=params)
        except httpx2.RequestError as exc:
            if attempt == ATTEMPTS:
                raise NCBIError(f"{tool} unreachable ({type(exc).__name__})") from exc
        else:
            if response.is_success:
                return response.text
            if response.status_code not in RETRY_STATUS or attempt == ATTEMPTS:
                raise NCBIError(f"{tool} answered HTTP {response.status_code}")
        await asyncio.sleep(RETRY_DELAY * attempt)


async def _get(tool: str, parse, **params):
    """Call an E-utility and parse the reply. Replies are cached; one that fails to parse is not."""
    params = {"db": "pubmed", **params}
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    key = (tool, *sorted(params.items()))
    if (text := cache.get(key)) is not None:
        return parse(text)
    text = await _post(tool, params)
    try:
        result = parse(text)
    except (ValueError, ET.ParseError) as exc:
        raise NCBIError(f"{tool} sent an unreadable reply") from exc
    cache[key] = text
    return result


def _esearch(text: str) -> dict:
    result = json.loads(text).get("esearchresult", {})
    if "ERROR" in result:  # sent with HTTP 200, e.g. "Search Backend failed ..." (also for broken queries)
        raise NCBIError(result["ERROR"])
    return result


async def search(term: str, max_results: int = 10, start_date: str | None = None,
                 end_date: str | None = None) -> list[str]:
    """PMIDs for a PubMed query, most recent first. Dates are YYYY/MM/DD (publication date)."""
    params = {"term": term, "retmax": max_results, "retmode": "json"}
    if start_date or end_date:  # NCBI ignores a date range unless both ends are given
        params.update(datetype="pdat", mindate=start_date or "1000/01/01", maxdate=end_date or "3000/12/31")
    return (await _get("esearch.fcgi", _esearch, **params)).get("idlist", [])


async def fetch(pmids: list[str]) -> list[dict]:
    """Full records for the PMIDs, in the same order, with one efetch call."""
    if not pmids:
        return []
    root = await _get("efetch.fcgi", ET.fromstring, id=",".join(pmids), retmode="xml")
    return [_article(node) for node in root if node.tag in ("PubmedArticle", "PubmedBookArticle")]


async def summaries(pmids: list[str]) -> list[dict]:
    """Short records (title, date, journal, authors) with one esummary call."""
    if not pmids:
        return []
    data = await _get("esummary.fcgi", json.loads, id=",".join(pmids), retmode="json")
    result = data.get("result", {})
    return [_summary(result[uid]) for uid in result.get("uids", []) if uid in result]


async def related(pmid: str) -> list[tuple[str, int]]:
    """(PMID, score) pairs of PubMed's "Similar articles", best first."""
    data = await _get("elink.fcgi", json.loads, dbfrom="pubmed", id=pmid, linkname="pubmed_pubmed",
                      cmd="neighbor_score", retmode="json")
    try:
        links = data["linksets"][0]["linksetdbs"][0]["links"]
    except (KeyError, IndexError):
        return []
    return [(link["id"], link["score"]) for link in links if link["id"] != pmid]


async def spell(query: str) -> str:
    """NCBI's spelling suggestion for a query, or '' if it has none."""
    root = await _get("espell.fcgi", ET.fromstring, term=query)
    return root.findtext("CorrectedQuery", "")


def ama_citation(article: dict) -> str:
    """Author(s). Title. *Journal*. Year;Volume(Issue):Pages."""
    authors = article["authors"]
    if len(authors) > 6:  # AMA: list up to 6 authors, otherwise the first 3 and "et al"
        authors = authors[:3] + ["et al"]
    details = "".join([article["volume"] or "",
                       f"({article['issue']})" if article["issue"] else "",
                       f":{article['pages']}" if article["pages"] else ""])
    published = ";".join(part for part in (article["pub_date"], details) if part)
    journal = f"*{article['journal']}*" if article["journal"] else ""
    parts = [", ".join(authors), article["title"], journal, published]
    return " ".join(part if part.endswith((".", "?", "!")) else part + "." for part in parts if part)


def _text(node: ET.Element, path: str) -> str:
    """All text under node/path, including inline tags such as <i>, or '' if missing."""
    found = node.find(path)
    return "".join(found.itertext()).strip() if found is not None else ""


def _author(node: ET.Element) -> str:
    name = f"{node.findtext('LastName', '')} {node.findtext('Initials', '')}".strip()
    return name or _text(node, "CollectiveName")


def _article(node: ET.Element) -> dict:
    """A journal article, or a book chapter (GeneReviews, StatPearls, ...), which uses mostly the same tags."""
    article = node.find("MedlineCitation/Article")
    if article is None:
        article = node.find("BookDocument")
    pmid = node.findtext("*/PMID")
    abstract = []
    for part in article.findall("Abstract/AbstractText"):
        text = "".join(part.itertext()).strip()
        label = part.get("Label")
        if text:
            abstract.append(f"**{label}:** {text}" if label else text)
    return {
        "pmid": pmid,
        "title": _text(article, "ArticleTitle") or _text(article, "Book/BookTitle") or "No Title Found",
        "pub_date": (article.findtext("ContributionDate/Year")  # book chapters; Book/PubDate is the series start
                     or article.findtext(".//PubDate/Year") or article.findtext(".//PubDate/MedlineDate", "")[:4]),
        "journal": article.findtext("Journal/Title") or _text(article, "Book/BookTitle"),
        "authors": [name for name in map(_author, article.findall("AuthorList/Author")) if name],
        "abstract": "\n\n".join(abstract) or "Abstract not available.",
        "mesh_terms": [d.text for d in node.findall("MedlineCitation/MeshHeadingList/MeshHeading/DescriptorName")
                       if d.text],
        "doi": (node.findtext("PubmedData/ArticleIdList/ArticleId[@IdType='doi']")
                or article.findtext("ArticleIdList/ArticleId[@IdType='doi']")),
        "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "volume": article.findtext("Journal/JournalIssue/Volume"),
        "issue": article.findtext("Journal/JournalIssue/Issue"),
        "pages": article.findtext("Pagination/MedlinePgn"),
    }


def _summary(record: dict) -> dict:
    return {
        "pmid": record.get("uid", ""),
        "title": record.get("title", "No Title"),
        "pub_date": record.get("pubdate", ""),
        "journal": record.get("source", ""),
        "authors": [author.get("name", "") for author in record.get("authors", [])],
    }
