from typing import List, Optional, Annotated
from pydantic import BaseModel, HttpUrl, Field

class ArticleSummary(BaseModel):
    """A brief summary of an article."""
    pmid: str
    title: str
    pub_date: str
    journal: str
    authors: List[str]

class Article(ArticleSummary):
    """Complete information about an article."""
    abstract: str
    mesh_terms: List[str] = Field(default_factory=list)
    doi: Optional[str] = None
    link: HttpUrl
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None

    def to_ama_citation(self) -> str:
        """Formats the article data into an AMA citation string."""
        
        # Author formatting
        authors_str = ""
        if self.authors:
            # AMA style: list all authors if 6 or fewer; if more than 6, list the first 3, followed by et al.
            if len(self.authors) > 6:
                authors_str = ", ".join(self.authors[:3]) + ", et al"
            else:
                authors_str = ", ".join(self.authors)
        
        # Title - remove any lingering markdown and end with a period.
        title = self.title.replace('*', '').strip()
        if not title.endswith('.'):
            title += '.'

        # Journal name - AMA style often uses standard abbreviations, but full name is acceptable.
        # Italicized and followed by a period.
        journal = f"*{self.journal.strip()}*." if self.journal else ""

        # Year, followed by a semicolon.
        year = f"{self.pub_date};" if self.pub_date else ""

        # Citation details: Volume(Issue):Pages.
        cit_details = ""
        if self.volume:
            cit_details += self.volume
        if self.issue:
            cit_details += f"({self.issue})"
        if self.pages:
            cit_details += f":{self.pages}"
        if cit_details:
            cit_details += "."
        
        # Final assembly
        # Format: Author(s). Title. Journal. Year;Volume(Issue):Pages.
        parts = [part for part in [authors_str, title, journal, year, cit_details] if part]
        return " ".join(parts)

class SpellCheckResponse(BaseModel):
    """Response from the spell check service."""
    original_query: str
    corrected_query: Optional[str] = None
    has_correction: bool

class SemanticSearchResult(BaseModel):
    """Model for semantic search results, including a similarity score."""
    score: Annotated[float, Field(ge=0, le=1)]  # Similarity score from 0 to 1
    article: Article

class CitationResponse(BaseModel):
    """Model for a formatted citation with a link."""
    citation: str
    link: HttpUrl
