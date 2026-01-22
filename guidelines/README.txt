Canonical guideline engine

This folder is a framework. It does not ship any copyrighted guideline content.

How you add a guideline

1 Put the PDF somewhere you can access from your computer

2 Run ingest

python guidelines/scripts/ingest_pdf.py \
  --pdf path_to_pdf \
  --specialty dry_eye \
  --title TFOS DEWS II \
  --version 2017 \
  --year 2017 \
  --out guidelines/packs

This produces a jsonl pack with chunk metadata and page numbers

3 Build the embedding index

Set OPENAI_API_KEY in your environment and run

python guidelines/scripts/build_index.py --packs_dir guidelines/packs

This writes guidelines/guidelines.sqlite which the app can query fast

Notes

You can rerun ingest and build anytime. Upserts are by citation_id.

If you do not build the index, the app will still run, but the guideline enhancement will be empty.
