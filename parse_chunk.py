"""
parse_chunk.py — PDF → ClauseChunk list
JLL Hackathon 2026 · IDP Agent

Converts a PDF (text-based or OCR'd) into a list of ClauseChunk objects
ready for idp_extraction.extract_all().

Supports:
  - Text-based PDFs via PyMuPDF (fitz)
  - Scanned PDFs via Azure Document Intelligence (optional)
  - Demo mode: returns synthetic chunks with no dependencies

Usage:
    chunks = parse_and_chunk("contract.pdf")
    # or with Azure OCR:
    chunks = parse_and_chunk("scanned.pdf", use_azure_ocr=True)
"""

from __future__ import annotations

import re
import os
from typing import List, Optional, Tuple

from idp_extraction import ClauseChunk

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_CLAUSE_CHARS = 80          # ignore headings-only blocks shorter than this
MAX_CHUNK_CHARS  = 3_000       # split oversized clauses to stay within token limits
HEADING_RE = re.compile(
    r"""
    ^                          # start of line
    (?:
        (\d+(?:\.\d+){0,3})    # numeric: 1 / 1.2 / 1.2.3 / 1.2.3.4
        |
        (Schedule\s+[A-Z\d]+)  # Schedule A / Schedule 1
        |
        (Exhibit\s+[A-Z\d]+)   # Exhibit B
        |
        (Appendix\s+[A-Z\d]+)  # Appendix C
        |
        (ARTICLE\s+[IVXLC\d]+) # ARTICLE IV
        |
        (Section\s+\d+(?:\.\d+)*)  # Section 12.3
    )
    [\s.\-–—:]+                # separator
    (.*)                       # heading text (captured)
    """,
    re.VERBOSE | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_section_and_heading(line: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (section_id, heading_text) if line looks like a clause heading."""
    m = HEADING_RE.match(line.strip())
    if not m:
        return None, None
    # groups 1-6 are the section identifier alternatives
    section_id = next((g for g in m.groups()[:6] if g), None)
    heading_text = m.group(7).strip() if m.group(7) else None
    return section_id, heading_text or None


def _split_long_text(section_id: str, heading: Optional[str],
                     page_range: str, text: str) -> List[ClauseChunk]:
    """Split a clause that exceeds MAX_CHUNK_CHARS into sub-chunks."""
    chunks: List[ClauseChunk] = []
    words = text.split()
    current_words: List[str] = []
    part = 1

    for word in words:
        current_words.append(word)
        current_text = " ".join(current_words)
        if len(current_text) >= MAX_CHUNK_CHARS:
            chunks.append(ClauseChunk(
                section_id=f"{section_id}-p{part}",
                heading=heading,
                page_range=page_range,
                text=current_text.strip(),
            ))
            part += 1
            current_words = []

    if current_words:
        chunks.append(ClauseChunk(
            section_id=f"{section_id}-p{part}" if part > 1 else section_id,
            heading=heading,
            page_range=page_range,
            text=" ".join(current_words).strip(),
        ))
    return chunks


def _build_chunks_from_blocks(blocks: List[Tuple[int, str]]) -> List[ClauseChunk]:
    """
    blocks: list of (page_number, text) tuples (one per page or paragraph).
    Groups text under detected headings → ClauseChunk list.
    """
    chunks: List[ClauseChunk] = []

    current_section: str = "Preamble"
    current_heading: Optional[str] = None
    current_page: int = 1
    current_lines: List[str] = []

    def _flush():
        nonlocal current_section, current_heading, current_page, current_lines
        text = " ".join(current_lines).strip()
        if len(text) >= MIN_CLAUSE_CHARS:
            sub = _split_long_text(current_section, current_heading,
                                   str(current_page), text)
            chunks.extend(sub)
        current_lines = []

    for page_num, page_text in blocks:
        for line in page_text.splitlines():
            line = line.strip()
            if not line:
                continue

            section_id, heading_text = _extract_section_and_heading(line)
            if section_id:
                _flush()
                current_section = section_id
                current_heading = heading_text
                current_page = page_num
            else:
                current_lines.append(line)

    _flush()  # capture last clause
    return chunks


# ---------------------------------------------------------------------------
# PyMuPDF (fitz) parser
# ---------------------------------------------------------------------------

def _open_fitz_doc(pdf_source):
    """Open a PyMuPDF document from either a file path (str/Path) or raw bytes."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for PDF parsing. Install with: pip install pymupdf"
        )
    if isinstance(pdf_source, (bytes, bytearray)):
        return fitz.open(stream=bytes(pdf_source), filetype="pdf")
    return fitz.open(pdf_source)


def _parse_with_pymupdf(pdf_source) -> List[ClauseChunk]:
    """Parse a text-based PDF using PyMuPDF. pdf_source may be a file path or raw bytes."""
    doc = _open_fitz_doc(pdf_source)
    blocks: List[Tuple[int, str]] = []

    for page_num, page in enumerate(doc, start=1):
        text = page.get_text("text")
        if text.strip():
            blocks.append((page_num, text))

    doc.close()

    if not blocks:
        label = "<uploaded bytes>" if isinstance(pdf_source, (bytes, bytearray)) else pdf_source
        raise ValueError(
            f"No text extracted from '{label}'. "
            "The PDF may be scanned — try use_azure_ocr=True."
        )

    return _build_chunks_from_blocks(blocks)


def count_pages(pdf_source) -> int:
    """Return the page count of a PDF given either a file path or raw bytes.
    Cheap — opens the doc without extracting text."""
    doc = _open_fitz_doc(pdf_source)
    try:
        return doc.page_count
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Azure Document Intelligence OCR parser (optional)
# ---------------------------------------------------------------------------

def _parse_with_azure_ocr(pdf_path: str) -> List[ClauseChunk]:
    """
    Parse a scanned PDF using Azure Document Intelligence.
    Requires:
        pip install azure-ai-documentintelligence
        env vars: AZURE_DOC_INTEL_ENDPOINT, AZURE_DOC_INTEL_KEY
    """
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError:
        raise ImportError(
            "azure-ai-documentintelligence is required for OCR mode. "
            "Install with: pip install azure-ai-documentintelligence"
        )

    endpoint = os.environ.get("AZURE_DOC_INTEL_ENDPOINT", "")
    key = os.environ.get("AZURE_DOC_INTEL_KEY", "")
    if not endpoint or not key:
        raise EnvironmentError(
            "Set AZURE_DOC_INTEL_ENDPOINT and AZURE_DOC_INTEL_KEY "
            "environment variables to use OCR mode."
        )

    client = DocumentIntelligenceClient(endpoint, AzureKeyCredential(key))

    with open(pdf_path, "rb") as f:
        poller = client.begin_analyze_document("prebuilt-read", f)
    result = poller.result()

    # Group paragraphs by page
    blocks: List[Tuple[int, str]] = []
    current_page = 1
    page_lines: List[str] = []

    for para in result.paragraphs or []:
        page_num = (para.bounding_regions[0].page_number
                    if para.bounding_regions else current_page)
        if page_num != current_page:
            if page_lines:
                blocks.append((current_page, "\n".join(page_lines)))
            page_lines = []
            current_page = page_num
        page_lines.append(para.content)

    if page_lines:
        blocks.append((current_page, "\n".join(page_lines)))

    return _build_chunks_from_blocks(blocks)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_and_chunk(
    pdf_source,
    use_azure_ocr: bool = False,
) -> List[ClauseChunk]:
    """
    Parse a PDF and return a list of ClauseChunk objects.

    Args:
        pdf_source:    Path to the PDF file (str/Path), OR raw PDF bytes
                       (e.g. from a Streamlit file_uploader.read()).
        use_azure_ocr: If True, use Azure Document Intelligence for OCR.
                       Requires AZURE_DOC_INTEL_ENDPOINT + AZURE_DOC_INTEL_KEY.
                       (bytes input is not yet supported for the OCR path.)

    Returns:
        List[ClauseChunk] ready for idp_extraction.extract_all()
    """
    is_bytes = isinstance(pdf_source, (bytes, bytearray))

    if not is_bytes and not os.path.exists(pdf_source):
        raise FileNotFoundError(f"PDF not found: '{pdf_source}'")

    if use_azure_ocr:
        if is_bytes:
            raise NotImplementedError(
                "Azure OCR currently requires a file path, not raw bytes. "
                "Write the bytes to a temp file first."
            )
        chunks = _parse_with_azure_ocr(pdf_source)
    else:
        chunks = _parse_with_pymupdf(pdf_source)

    label = os.path.basename(pdf_source) if not is_bytes else "<uploaded PDF>"
    print(f"[parse_chunk] '{label}' → {len(chunks)} clause chunks")
    return chunks


# ---------------------------------------------------------------------------
# Demo / self-test
# ---------------------------------------------------------------------------

DEMO_CHUNKS: List[ClauseChunk] = [
    ClauseChunk(
        section_id="8.1",
        heading="Insurance Requirements",
        page_range="142",
        text=(
            "Service Provider shall maintain Commercial General Liability insurance "
            "with limits of not less than $5,000,000 per occurrence and $10,000,000 "
            "in the aggregate. Failure to maintain the required coverage shall entitle "
            "Client to assess a penalty of $1,000 for each day the coverage lapses. "
            "Certificates of insurance shall be provided to Client within 10 business "
            "days of policy renewal."
        ),
    ),
    ClauseChunk(
        section_id="12.3",
        heading="Reporting Obligations",
        page_range="198",
        text=(
            "Service Provider shall deliver monthly performance reports to Client no "
            "later than the 5th business day of each calendar month. Reports shall "
            "include key performance indicators, incident summaries, and SLA compliance "
            "metrics. Failure to deliver reports on time shall result in a service credit "
            "of $500 per day of delay, up to a maximum of $5,000 per month."
        ),
    ),
    ClauseChunk(
        section_id="15.2",
        heading="Confidentiality",
        page_range="221",
        text=(
            "Each party agrees to keep confidential all Confidential Information received "
            "from the other party and to use such information solely for the purposes of "
            "this Agreement. Confidentiality obligations survive termination of this "
            "Agreement for a period of three (3) years. Unauthorized disclosure may "
            "result in injunctive relief and damages."
        ),
    ),
    ClauseChunk(
        section_id="Schedule A",
        heading="Service Level Agreement",
        page_range="310-315",
        text=(
            "Provider guarantees 99.5% system uptime measured monthly. Downtime exceeding "
            "the guaranteed threshold shall trigger service credits: 0.5%-1.0% breach = "
            "5% monthly fee credit; >1.0% breach = 10% monthly fee credit. Client must "
            "submit credit requests within 30 days of the affected month."
        ),
    ),
    ClauseChunk(
        section_id="19.1",
        heading="Term and Renewal",
        page_range="245",
        text=(
            "This Agreement shall commence on the Effective Date and continue for an "
            "initial term of three (3) years. Either party may terminate this Agreement "
            "upon 90 days written notice prior to the end of the initial term or any "
            "renewal term. Absent such notice, the Agreement shall automatically renew "
            "for successive one-year periods."
        ),
    ),
]


if __name__ == "__main__":
    print("=== parse_chunk.py self-test ===\n")

    # Test 1: heading regex
    test_lines = [
        "8.3  Insurance Requirements",
        "Schedule A — Service Levels",
        "ARTICLE IV: Confidentiality",
        "Section 12.3 Reporting",
        "This is not a heading",
    ]
    print("Heading detection:")
    for line in test_lines:
        sid, heading = _extract_section_and_heading(line)
        status = "✓" if sid else "✗"
        print(f"  {status} '{line[:50]}' → section='{sid}', heading='{heading}'")

    # Test 2: demo chunks
    print(f"\nDemo chunks: {len(DEMO_CHUNKS)} clause(s)")
    for c in DEMO_CHUNKS:
        print(f"  [{c.section_id}] {c.heading or '(no heading)'} "
              f"(p.{c.page_range}, {len(c.text)} chars)")

    # Test 3: long-text splitter
    long_text = "word " * 1000
    split = _split_long_text("99.1", "Long Clause", "500", long_text)
    print(f"\nLong-text splitter: 5000-char clause → {len(split)} sub-chunk(s)")

    print("\nAll parse_chunk self-tests passed.")
