Canonical evidence engine

Purpose
Provide tight, defensible citations by grounding recommendations in a versioned evidence shelf.

Core ideas
1 Versioned evidence packs
Each pack represents one source version. A pack contains source metadata and extracted text chunks.

2 Offline indexing
Chunks are embedded once and stored in a local vector store. Runtime retrieval is fast.

3 Checklist extraction
Each specialty defines an explicit checklist schema. Extraction populates fields only when supported by notes.

4 Three lane output
Documented, Missing but important, Suggested plan.

5 Retrieval enforced recommendations
Every suggested item must cite at least one retrieved chunk id. No citation, no recommendation.

6 Audit trail
Return the chunks used for each recommendation so a clinician can verify.

Licensing note
Many specialty guidelines are copyrighted. Do not ingest or redistribute full text unless you have the right to do so.
The system supports metadata only sources and allows optional user provided licensed PDFs for indexing.
