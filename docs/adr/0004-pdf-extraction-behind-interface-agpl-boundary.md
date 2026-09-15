# ADR-0004: PDF text extraction sits behind an interface because the best library is AGPL

- Status: accepted
- Date: 2026-09-15

## Context

pymupdf4llm produces the best markdown from born-digital PDFs (reading order, multi-column,
tables) with no machine-learning dependencies, but PyMuPDF is licensed AGPL-3.0 or commercial.
For a single-user tool run locally and never conveyed to others, AGPL imposes no obligations;
that changes if raida is ever offered as a shared network service or distributed outside the
organization. pypdfium2 (Apache-2.0/BSD) extracts plain text quickly with permissive terms.
Scanned pages need OCR; Apple Vision through ocrmac runs on-device with no model download.

## Decision

- `TextExtractor` protocol with two implementations selected by `pdf.extractor`:
  `pymupdf4llm` (default) and `pypdfium2`.
- Pages with too little extracted text escalate to OCR: render at 300 DPI with pypdfium2, then
  Apple Vision via ocrmac, page by page in the CPU process pool.
- PDF export also has two renderers behind `PdfRenderer` (WeasyPrint, fpdf2) for a different
  reason: WeasyPrint needs Homebrew libraries and must not be a hard requirement.

## Consequences

- Switching to a permissive-only stack is a one-line config change with no code changes.
- Anyone turning raida into a multi-user service must revisit the PyMuPDF license first.
- OCR quality depends on scan quality more than on engine choice; Docling or a vision LLM remain
  available as a later opt-in tier for difficult layouts.
