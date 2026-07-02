#!/usr/bin/env python3
"""전국평생학습강좌표준데이터(#15013110) 205개 CSV 다운로드.

레시피 (2026-07-02 실증, 실행명세_PRD.md §5):
  1) selectFileDataDownload.do → fileDataRegistVO.atchFileId
  2) cmm/fileDownload.do → CSV 바이트 (raw 저장, 인코딩 판별은 build_db에서)
UA+Referer 헤더 필수, 요청 간 0.3s 딜레이, 재실행 시 기존 파일 스킵.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "파일카탈로그_uddi목록.json"
RAW_DIR = ROOT / "data" / "raw"
FAIL_LOG = ROOT / "data" / "download_failures.json"

PUBLIC_DATA_PK = "15013110"
def headers(pk: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://www.data.go.kr/data/{pk}/standard.do",
    }
META_URL = (
    "https://www.data.go.kr/tcs/dss/selectFileDataDownload.do"
    "?publicDataPk={pk}&publicDataDetailPk={uddi}&fileDetailSn=1"
)
FILE_URL = (
    "https://www.data.go.kr/cmm/cmm/fileDownload.do"
    "?atchFileId={atch}&fileDetailSn=1"
)


def fetch(url: str, pk: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=headers(pk))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def safe_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", name)


def download_one(
    uddi: str, name: str, pk: str = PUBLIC_DATA_PK, raw_dir: Path = RAW_DIR
) -> Path:
    meta = json.loads(fetch(META_URL.format(pk=pk, uddi=uddi), pk))
    atch = meta["fileDataRegistVO"]["atchFileId"]
    data = fetch(FILE_URL.format(atch=atch), pk, timeout=60)
    if len(data) < 100:
        raise ValueError(f"suspiciously small response: {len(data)} bytes")
    out = raw_dir / f"{safe_name(name)}.csv"
    out.write_bytes(data)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", type=Path, default=CATALOG)
    ap.add_argument("--pk", default=PUBLIC_DATA_PK)
    ap.add_argument("--out", type=Path, default=RAW_DIR)
    ap.add_argument("--fail-log", type=Path, default=FAIL_LOG)
    args = ap.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    raw_dir: Path = args.out
    raw_dir.mkdir(parents=True, exist_ok=True)
    failures = {}
    done = skipped = 0
    total = len(catalog)
    for i, (uddi, name) in enumerate(catalog.items(), 1):
        out = raw_dir / f"{safe_name(name)}.csv"
        if out.exists() and out.stat().st_size > 100:
            skipped += 1
            continue
        for attempt in (1, 2, 3):
            try:
                download_one(uddi, name, args.pk, raw_dir)
                done += 1
                print(f"[{i}/{total}] OK {name}", flush=True)
                break
            except Exception as e:
                if attempt == 3:
                    failures[uddi] = {"name": name, "error": str(e)}
                    print(f"[{i}/{total}] FAIL {name}: {e}", flush=True)
                else:
                    time.sleep(1.0 * attempt)
        time.sleep(0.3)
    args.fail_log.write_text(
        json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"\ndone={done} skipped={skipped} failed={len(failures)} total={total}",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
