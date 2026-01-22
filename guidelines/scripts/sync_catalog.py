import json, os, hashlib, urllib.request
from pathlib import Path

def sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent":"ManeiroCanonicalSync"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def main():
    root = Path(__file__).resolve().parents[1]
    catalog_path = root / "catalogs" / "ophthalmology_sources.json"
    packs_dir = root / "packs"
    packs_dir.mkdir(parents=True, exist_ok=True)

    catalog = json.loads(catalog_path.read_text(encoding="utf8"))
    sources = catalog.get("sources") or []

    for s in sources:
        url = (s.get("pdf_url") or "").strip()
        if not url:
            continue
        out_dir = packs_dir / s["id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / "source.pdf"
        meta_path = out_dir / "meta.json"

        data = download(url)
        pdf_path.write_bytes(data)

        meta = dict(s)
        meta["sha256"] = sha256_bytes(data)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf8")
        print("synced", s["id"])

if __name__ == "__main__":
    main()
