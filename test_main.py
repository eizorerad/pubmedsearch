"""API tests. NCBI is replaced by canned E-utilities replies, so no network is used."""
import asyncio
import json
import os
import time

os.environ["API_KEY"] = "test-key"  # read once on import; test_without_api_key_the_api_is_open unsets it

import httpx2
import pytest
from fastapi.testclient import TestClient

import main
import pubmed

AUTHORS = "".join(f"<Author><LastName>Name{i}</LastName><Initials>A</Initials></Author>" for i in range(1, 8))
EFETCH = f"""<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation><PMID>111</PMID>
    <Article>
      <Journal><JournalIssue><Volume>383</Volume><Issue>27</Issue><PubDate><Year>2020</Year></PubDate></JournalIssue>
        <Title>The New England journal of medicine</Title></Journal>
      <ArticleTitle>HLA-B*57:01 and <i>abacavir</i>?</ArticleTitle>
      <Pagination><MedlinePgn>2603-2615</MedlinePgn></Pagination>
      <Abstract><AbstractText Label="BACKGROUND">Why.</AbstractText><AbstractText Label="RESULTS">What.</AbstractText></Abstract>
      <AuthorList>{AUTHORS}</AuthorList>
    </Article>
    <MeshHeadingList><MeshHeading><DescriptorName>Humans</DescriptorName></MeshHeading></MeshHeadingList>
  </MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1056/x</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle>
<PubmedArticle>
  <MedlineCitation><PMID>222</PMID>
    <Article>
      <Journal><JournalIssue><PubDate><MedlineDate>1998 Dec-1999 Jan</MedlineDate></PubDate></JournalIssue>
        <Title>Integration</Title></Journal>
      <ArticleTitle>Declaration.</ArticleTitle>
      <AuthorList><Author><CollectiveName>WHO Group</CollectiveName></Author></AuthorList>
    </Article>
  </MedlineCitation>
  <PubmedData><ReferenceList><Reference><ArticleIdList>
    <ArticleId IdType="doi">10.1/a-cited-reference</ArticleId>
  </ArticleIdList></Reference></ReferenceList></PubmedData>
</PubmedArticle>
<PubmedBookArticle>
  <BookDocument><PMID>333</PMID>
    <Book><BookTitle>GeneReviews</BookTitle><PubDate><Year>1993</Year></PubDate>
      <AuthorList Type="editors"><Author><LastName>Editor</LastName><Initials>E</Initials></Author></AuthorList></Book>
    <ArticleTitle>Hermansky-Pudlak Syndrome</ArticleTitle>
    <ContributionDate><Year>2000</Year></ContributionDate>
    <AuthorList Type="authors"><Author><LastName>Huizing</LastName><Initials>M</Initials></Author></AuthorList>
    <Abstract><AbstractText>Summary.</AbstractText></Abstract>
  </BookDocument>
</PubmedBookArticle>
</PubmedArticleSet>"""

REPLIES = {
    "esearch.fcgi": json.dumps({"esearchresult": {"idlist": ["111", "222", "333"]}}),
    "efetch.fcgi": EFETCH,
    "esummary.fcgi": json.dumps({"result": {"uids": ["111"], "111": {
        "uid": "111", "title": "Short title", "pubdate": "2020 Dec 31", "source": "N Engl J Med",
        "authors": [{"name": "Polack FP"}]}}}),
    "elink.fcgi": json.dumps({"linksets": [{"linksetdbs": [{"links": [
        {"id": "111", "score": 200}, {"id": "222", "score": 100}]}]}]}),
    "espell.fcgi": "<eSpellResult><CorrectedQuery>asthma treatment</CorrectedQuery></eSpellResult>",
}

ARTICLE_111 = {
    "pmid": "111", "title": "HLA-B*57:01 and abacavir?", "pub_date": "2020",
    "journal": "The New England journal of medicine", "authors": [f"Name{i} A" for i in range(1, 8)],
    "abstract": "**BACKGROUND:** Why.\n\n**RESULTS:** What.", "mesh_terms": ["Humans"], "doi": "10.1056/x",
    "link": "https://pubmed.ncbi.nlm.nih.gov/111/", "volume": "383", "issue": "27", "pages": "2603-2615",
}
ARTICLE_222 = {
    "pmid": "222", "title": "Declaration.", "pub_date": "1998", "journal": "Integration", "authors": ["WHO Group"],
    "abstract": "Abstract not available.", "mesh_terms": [], "doi": None,
    "link": "https://pubmed.ncbi.nlm.nih.gov/222/", "volume": None, "issue": None, "pages": None,
}
BOOK_333 = {
    "pmid": "333", "title": "Hermansky-Pudlak Syndrome", "pub_date": "2000", "journal": "GeneReviews",
    "authors": ["Huizing M"], "abstract": "Summary.", "mesh_terms": [], "doi": None,
    "link": "https://pubmed.ncbi.nlm.nih.gov/333/", "volume": None, "issue": None, "pages": None,
}
ALL_RECORDS = [ARTICLE_111, ARTICLE_222, BOOK_333]


class FakeNCBI:
    """MockTransport handler. replies[tool] is a body, an HTTP status, an exception, or a list of those."""

    def __init__(self):
        self.replies = dict(REPLIES)
        self.calls = []

    def __call__(self, request):
        tool = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((tool, dict(httpx2.QueryParams(request.content.decode()))))
        reply = self.replies[tool]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return httpx2.Response(reply) if isinstance(reply, int) else httpx2.Response(200, text=reply)

    def params(self, tool):
        return [params for name, params in self.calls if name == tool]


@pytest.fixture
def ncbi(monkeypatch):
    fake = FakeNCBI()
    transport = httpx2.MockTransport(fake)
    monkeypatch.setattr(pubmed, "http", httpx2.AsyncClient(base_url=pubmed.EUTILS, transport=transport))
    monkeypatch.setattr(pubmed, "NCBI_API_KEY", None)
    monkeypatch.setattr(pubmed, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pubmed, "RETRY_DELAY", 0)
    pubmed.cache.clear()
    return fake


client = TestClient(main.app, headers={"X-API-Key": "test-key"})


def test_only_root_and_docs_are_public(ncbi):
    anonymous = TestClient(main.app)
    assert anonymous.get("/").json() == {"status": "PubMed Search API is running"}
    assert anonymous.get("/docs").status_code == 200
    assert anonymous.get("/search?query=x").status_code == 403
    assert client.get("/search?query=x", headers={"X-API-Key": "wrong"}).status_code == 403
    assert ncbi.calls == []


def test_without_api_key_the_api_is_open(ncbi, monkeypatch):
    monkeypatch.setattr(main, "API_KEY", None)
    assert TestClient(main.app).get("/search/summary?query=x").status_code == 200


def test_search_returns_full_records(ncbi):
    assert client.get("/search?query=asthma").json() == ALL_RECORDS
    assert len(ncbi.params("efetch.fcgi")) == 1  # one batch call, not one per article


def test_search_field_and_open_date_ranges(ncbi):
    client.get("/search", params={"query": "smith j", "search_field": "[AU]", "start_date": "2020/01/01"})
    client.get("/search", params={"query": "asthma[TI]", "search_field": "[AU]", "end_date": "2021/12/31"})
    client.get("/search", params={"query": "[18F]FDG PET", "search_field": "[TI]"})
    client.get("/search", params={"query": "asthma[MeSH Terms]", "search_field": "[TI]"})
    first, second, third, fourth = ncbi.params("esearch.fcgi")
    assert first == {"db": "pubmed", "term": "(smith j)[AU]", "retmax": "10", "retmode": "json",
                     "datetype": "pdat", "mindate": "2020/01/01", "maxdate": "3000/12/31"}
    assert (second["term"], second["mindate"], second["maxdate"]) == ("asthma[TI]", "1000/01/01", "2021/12/31")
    assert (third["term"], fourth["term"]) == ("([18F]FDG PET)[TI]", "asthma[MeSH Terms]")
    assert "mindate" not in third


def test_summary(ncbi):
    assert client.get("/search/summary?query=x").json() == [{
        "pmid": "111", "title": "Short title", "pub_date": "2020 Dec 31",
        "journal": "N Engl J Med", "authors": ["Polack FP"]}]


def test_citations_are_ama(ncbi):
    assert client.get("/search/citations?query=x").json() == [
        {"citation": "Name1 A, Name2 A, Name3 A, et al. HLA-B*57:01 and abacavir? "
                     "*The New England journal of medicine*. 2020;383(27):2603-2615.",
         "link": "https://pubmed.ncbi.nlm.nih.gov/111/"},
        {"citation": "WHO Group. Declaration. *Integration*. 1998.",
         "link": "https://pubmed.ncbi.nlm.nih.gov/222/"},
        {"citation": "Huizing M. Hermansky-Pudlak Syndrome. *GeneReviews*. 2000.",
         "link": "https://pubmed.ncbi.nlm.nih.gov/333/"},
    ]


def test_guidelines_filter(ncbi):
    assert client.get("/search/guidelines?query=hypertension&max_results=5").json() == ALL_RECORDS
    (params,) = ncbi.params("esearch.fcgi")
    assert params["retmax"] == "5"
    assert params["term"] == ('(hypertension) AND ("guideline"[Publication Type] OR '
                              '"practice guideline"[Publication Type]) AND "free full text"[Filter]')


def scores(url):
    return [(hit["score"], hit["article"]["pmid"]) for hit in client.get(url).json()]


def test_semantic_search_uses_pubmed_scores(ncbi):
    assert scores("/semantic_search/999") == [(1.0, "111"), (0.5, "222")]
    assert scores("/semantic_search/999?max_results=1") == [(1.0, "111")]
    assert scores("/semantic_search/111") == [(1.0, "222")]  # never lists the source article itself


def test_semantic_search_without_links(ncbi):
    ncbi.replies["elink.fcgi"] = json.dumps({"linksets": [{"dbfrom": "pubmed", "ids": ["999"]}]})
    assert client.get("/semantic_search/998").json() == []  # exists, nothing similar
    ncbi.replies["efetch.fcgi"] = "<PubmedArticleSet></PubmedArticleSet>"
    assert client.get("/semantic_search/999").status_code == 404  # no such PMID


def test_spellcheck(ncbi):
    assert client.get("/spellcheck?query=asthmaa treatmnt").json() == {
        "original_query": "asthmaa treatmnt", "corrected_query": "asthma treatment", "has_correction": True}
    ncbi.replies["espell.fcgi"] = "<eSpellResult><CorrectedQuery/></eSpellResult>"
    assert client.get("/spellcheck?query=asthma").json() == {
        "original_query": "asthma", "corrected_query": None, "has_correction": False}


def test_retries_rate_limit_then_caches(ncbi):
    ncbi.replies["esearch.fcgi"] = [429, REPLIES["esearch.fcgi"]]
    first = client.get("/search/summary?query=x").json()
    assert client.get("/search/summary?query=x").json() == first
    assert len(ncbi.params("esearch.fcgi")) == 2  # one retry, the second request came from the cache
    assert len(ncbi.params("esummary.fcgi")) == 1


def failure(url):
    response = client.get(url)
    assert response.status_code == 502
    return response.json()["detail"]


def test_ncbi_failure_is_502_after_three_tries(ncbi):
    ncbi.replies["esearch.fcgi"] = [500, httpx2.ConnectError("down"), 503]
    assert failure("/search?query=x") == "PubMed request failed: esearch.fcgi answered HTTP 503"
    ncbi.replies["esearch.fcgi"] = [503, 503, httpx2.ConnectError("down")]
    assert failure("/search?query=y") == "PubMed request failed: esearch.fcgi unreachable (ConnectError)"
    assert len(ncbi.params("esearch.fcgi")) == 6


def test_unusable_ncbi_replies_are_502_and_not_cached(ncbi):
    ncbi.replies["esearch.fcgi"] = json.dumps({"esearchresult": {"ERROR": "Search Backend failed"}})
    assert failure("/search?query=x") == "PubMed request failed: Search Backend failed"
    assert failure("/search?query=x") == "PubMed request failed: Search Backend failed"
    ncbi.replies["esearch.fcgi"] = "<html>Proxy Error</html>"
    assert failure("/search?query=x") == "PubMed request failed: esearch.fcgi sent an unreadable reply"
    ncbi.replies["esearch.fcgi"] = 400
    assert failure("/search?query=x") == "PubMed request failed: esearch.fcgi answered HTTP 400"
    assert len(ncbi.params("esearch.fcgi")) == 4  # nothing was cached, and a 400 is not retried


def test_input_validation(ncbi):
    for url in ["/search?query=", "/search?query=x&max_results=101", "/search?query=x&start_date=2020-01-01",
                "/search?query=x&search_field=AU", "/semantic_search/abc", "/spellcheck?query=" + "a" * 501,
                "/semantic_search/١٢٣", "/search?query=x&start_date=٢٠٢٠/٠١/٠١"]:
        assert client.get(url).status_code == 422, url
    assert ncbi.calls == []


def test_ncbi_api_key_is_sent(ncbi, monkeypatch):
    monkeypatch.setattr(pubmed, "NCBI_API_KEY", "ncbi-secret")
    client.get("/spellcheck?query=asthma")
    assert ncbi.calls[0][1]["api_key"] == "ncbi-secret"


def test_throttle_spaces_requests(monkeypatch):
    monkeypatch.setattr(pubmed, "MIN_INTERVAL", 0.05)

    async def three_requests():
        start = time.monotonic()
        for _ in range(3):
            await pubmed._throttle()
        return time.monotonic() - start

    assert asyncio.run(three_requests()) >= 0.1
