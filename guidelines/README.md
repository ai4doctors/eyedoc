# Guideline engine

Purpose

Maneiro can do a second pass that goes beyond organizing the chart. It can compare the encounter against trusted guideline PDFs you provide, then suggest what might be missing and propose evidence based treatment options, with citations back to the guideline passages.

Key idea

Do not fine tune on guidelines. Use retrieval instead.

Fine tuning makes the model memorize patterns and it becomes harder to prove where an answer came from. Retrieval lets you show the exact paragraph it relied on.

What you add

Put guideline PDFs you legally have into:

`data/guidelines/<specialty>/`

Example

`data/guidelines/dry_eye/TFOS_DEWS_II_2017.pdf`
`data/guidelines/dry_eye/AAO_PPP_Dry_Eye_2023.pdf`

Build the index

Run this once on your machine or in a one off Render shell:

`python scripts/build_guideline_index.py`

It creates:

`data/guidelines/index.sqlite`

Enable in the app

Set environment variable:

`GUIDELINES_ENABLE=1`

Optional

`GUIDELINE_DB_PATH=data/guidelines/index.sqlite`
`OPENAI_EMBEDDING_MODEL=text-embedding-3-small`

How the enhancement shows up

After the normal analysis completes, the app can attach an extra object under `analysis.guideline_enhancement`.

Safety

Outputs are suggestions only. The code prompts the model to never invent patient facts and to cite the exact passage used.
