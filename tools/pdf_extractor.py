"""
PDF text extractor for bank statements.
Primary: pdfplumber (good at tables and structured layouts)
Fallback: PyMuPDF (handles more encoding edge cases)
"""
import os
import re
import sys


def extract_text(pdf_path: str) -> str:
    """
    Extract all text from a PDF bank statement.
    Returns a single string with page separators.
    Tries pdfplumber first, falls back to PyMuPDF.
    """
    text = _extract_with_pdfplumber(pdf_path)
    if not text.strip():
        print(f"[pdf_extractor] pdfplumber returned empty text, trying PyMuPDF...")
        text = _extract_with_pymupdf(pdf_path)
    if not text.strip():
        raise ValueError(
            f"Could not extract text from {os.path.basename(pdf_path)}. "
            "The PDF may be image-based (scanned) and requires OCR."
        )
    return text


def extract_account_suffix(pdf_path: str) -> str:
    """
    Try to extract the last 4 digits of the account number from a PDF.
    Uses regex patterns against the full raw text. Returns None if not found.
    """
    try:
        text = extract_text(pdf_path)
    except Exception:
        return None

    # Common patterns found in bank statements (ordered by specificity)
    patterns = [
        r'[Aa]ccount\s+[Nn]umber[:\s]+[X*•\-]{0,12}(\d{4})\b',
        r'[Aa]ccount\s+#[:\s]*[X*•\-]{0,12}(\d{4})\b',
        r'[Aa]ccount\s*#\s+\d{4}(?:\s+[X*•\-]+)+\s+(\d{4})\b',  # e.g. "Account# 4538 XXXX XXXX 9016"
        r'(?:[X*•]+\s+){3,}(\d{4})\b',                            # spaced masked card (≥3 groups): "XXXX XXXX XXXX 9016"
        r'[Aa]cct\.?\s*#?[:\s]+[X*•\-]{0,12}(\d{4})\b',
        r'[Ee]nding\s+in\s+(\d{4})\b',
        r'[Ee]nding\s+(\d{4})\b',
        r'[X*•]{3,}\s*-?\s*(\d{4})\b',        # ****1234 or ••••-1234
        r'\b[Xx]{3,}(\d{4})\b',                # XXXX1234
        r'[Cc]hecking\s+[X*•\-]{0,8}(\d{4})\b',
        r'[Ss]avings\s+[X*•\-]{0,8}(\d{4})\b',
        r'[Cc]ard\s+[X*•\-]{0,8}(\d{4})\b',
        # NOTE: The generic "5+ digit number → last 4" pattern was intentionally removed.
        # It was too greedy and matched dates, transaction amounts, and other numeric strings,
        # producing incorrect suffixes. Banks that need this fallback should add a specific pattern above.
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            suffix = match.group(1)
            print(f"[pdf_extractor] Found account suffix: {suffix}")
            return suffix

    print(f"[pdf_extractor] Could not detect account number in {os.path.basename(pdf_path)}")
    return None


def _extract_with_pdfplumber(pdf_path: str) -> str:
    import pdfplumber

    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            page_content = []

            # Always extract raw text first — this captures headers with account numbers
            raw = page.extract_text(x_tolerance=3, y_tolerance=3)
            if raw:
                page_content.append(raw)

            # Also extract tables separately and append — avoids missing structured data
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        if row:
                            cleaned = [str(cell).strip() if cell is not None else "" for cell in row]
                            line = "  |  ".join(cleaned)
                            if any(c.strip() for c in cleaned):
                                page_content.append(line)

            if page_content:
                pages_text.append(f"--- Page {i} ---\n" + "\n".join(page_content))

    return "\n\n".join(pages_text)


def _extract_with_pymupdf(pdf_path: str) -> str:
    import fitz  # PyMuPDF

    pages_text = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if text.strip():
                pages_text.append(f"--- Page {i} ---\n{text}")

    return "\n\n".join(pages_text)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pdf_extractor.py <path_to_pdf>")
        sys.exit(1)
    pdf_path = sys.argv[1]
    text = extract_text(pdf_path)
    print(f"\n=== Extracted {len(text)} characters ===\n")
    print(text[:3000])
    if len(text) > 3000:
        print(f"\n... (truncated, total {len(text)} chars)")
