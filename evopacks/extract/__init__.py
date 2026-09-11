"""Text extraction: every document -> $RAG_DOCS/_TEXT/<corpus>/... .txt, audited.

text.py handles PDF (pdftotext subprocess), HTML, DOCX, XLSX and passes .txt
through. ocr.py handles what text.py flagged as image-only.
"""
