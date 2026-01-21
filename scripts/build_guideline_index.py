import os
import glob

from guidelines.index import open_db, add_pdf, DB_DEFAULT


def main() -> None:
    base = os.path.join("data", "guidelines")
    db_path = (os.getenv("GUIDELINE_DB_PATH") or DB_DEFAULT).strip()

    if not os.path.isdir(base):
        print(f"Missing folder: {base}")
        return

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    if os.path.exists(db_path):
        os.remove(db_path)

    total_files = 0
    total_chunks = 0

    with open_db(db_path) as conn:
        # each immediate subfolder is a specialty
        for specialty in sorted([d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]):
            folder = os.path.join(base, specialty)
            pdfs = sorted(glob.glob(os.path.join(folder, "*.pdf")))
            if not pdfs:
                continue
            for pdf_path in pdfs:
                total_files += 1
                try:
                    inserted = add_pdf(conn, specialty=specialty, pdf_path=pdf_path, source_name=os.path.basename(pdf_path))
                    total_chunks += int(inserted)
                    print(f"Indexed {pdf_path}  chunks {inserted}")
                except Exception as e:
                    print(f"Failed {pdf_path}  {type(e).__name__}  {e}")

    print(f"Done. Files {total_files}  chunks {total_chunks}  db {db_path}")


if __name__ == "__main__":
    main()
