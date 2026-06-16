from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from runtime_guard import require_internal_context


DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.5"


def bool_arg(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def submit_job(
    source: str,
    token: str,
    job_url: str,
    model: str,
    optional_payload: dict[str, Any],
    timeout: int,
) -> str:
    headers = {"Authorization": f"bearer {token}"}
    if source.startswith(("http://", "https://")):
        headers["Content-Type"] = "application/json"
        payload = {"fileUrl": source, "model": model, "optionalPayload": optional_payload}
        response = requests.post(job_url, json=payload, headers=headers, timeout=timeout)
    else:
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"File not found: {source_path}")
        data = {"model": model, "optionalPayload": json.dumps(optional_payload, ensure_ascii=False)}
        with source_path.open("rb") as handle:
            response = requests.post(job_url, headers=headers, data=data, files={"file": handle}, timeout=timeout)

    if response.status_code != 200:
        raise RuntimeError(f"OCR job submit failed: HTTP {response.status_code}: {response.text}")
    payload = response.json()
    try:
        return payload["data"]["jobId"]
    except KeyError as exc:
        raise RuntimeError(f"OCR job response missing data.jobId: {payload}") from exc


def poll_job(job_id: str, token: str, job_url: str, poll_seconds: int, timeout: int) -> dict[str, Any]:
    headers = {"Authorization": f"bearer {token}"}
    while True:
        response = requests.get(f"{job_url}/{job_id}", headers=headers, timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(f"OCR job poll failed: HTTP {response.status_code}: {response.text}")
        payload = response.json()
        data = payload.get("data", {})
        state = data.get("state")

        if state == "done":
            return data
        if state == "failed":
            raise RuntimeError(f"OCR job failed: {data.get('errorMsg', 'unknown error')}")
        if state not in {"pending", "running"}:
            raise RuntimeError(f"OCR job returned unexpected state {state!r}: {payload}")

        progress = data.get("extractProgress", {})
        total_pages = progress.get("totalPages", "?")
        extracted_pages = progress.get("extractedPages", "?")
        print(f"state={state}, total_pages={total_pages}, extracted_pages={extracted_pages}", flush=True)
        time.sleep(poll_seconds)


def download_binary(url: str, timeout: int) -> bytes:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def write_results(jsonl_text: str, output_dir: Path, timeout: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    page_num = 0

    for line_num, line in enumerate(jsonl_text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        layout_results = record.get("result", {}).get("layoutParsingResults", [])
        for result_idx, result in enumerate(layout_results):
            page_dir = output_dir / f"page-{page_num:04d}"
            page_dir.mkdir(parents=True, exist_ok=True)

            markdown = result.get("markdown", {})
            md_path = page_dir / "page.md"
            md_path.write_text(markdown.get("text", ""), encoding="utf-8")

            markdown_images = []
            for img_path, img_url in markdown.get("images", {}).items():
                local_img_path = page_dir / img_path
                local_img_path.parent.mkdir(parents=True, exist_ok=True)
                local_img_path.write_bytes(download_binary(img_url, timeout))
                markdown_images.append(str(local_img_path))

            output_images = []
            for img_name, img_url in result.get("outputImages", {}).items():
                local_img_path = page_dir / f"{img_name}.jpg"
                local_img_path.write_bytes(download_binary(img_url, timeout))
                output_images.append(str(local_img_path))

            pages.append(
                {
                    "page_num": page_num,
                    "line_num": line_num,
                    "result_index": result_idx,
                    "markdown_path": str(md_path),
                    "markdown_images": markdown_images,
                    "output_images": output_images,
                }
            )
            page_num += 1

    combined_md = output_dir / "combined.md"
    combined_md.write_text(
        "\n\n".join(
            [
                f"<!-- page {page['page_num']} source: {page['markdown_path']} -->\n"
                + Path(page["markdown_path"]).read_text(encoding="utf-8")
                for page in pages
            ]
        ),
        encoding="utf-8",
    )

    return {"page_count": len(pages), "combined_markdown": str(combined_md), "pages": pages}


def main() -> None:
    require_internal_context("extract_reference_pdf_ocr.py")
    parser = argparse.ArgumentParser(description="Extract structured Markdown from reference PDF/image files using PaddleOCR-VL.")
    parser.add_argument("source", help="Local PDF/image path or file URL.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--job-url", default=DEFAULT_JOB_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--token-env", default="PADDLEOCR_TOKEN")
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--use-doc-orientation-classify", type=bool_arg, default=False)
    parser.add_argument("--use-doc-unwarping", type=bool_arg, default=False)
    parser.add_argument("--use-chart-recognition", type=bool_arg, default=False)
    args = parser.parse_args()

    token = os.environ.get(args.token_env)
    if not token:
        print(f"Missing OCR token. Set ${args.token_env} before running this script.", file=sys.stderr)
        sys.exit(2)

    optional_payload = {
        "useDocOrientationClassify": args.use_doc_orientation_classify,
        "useDocUnwarping": args.use_doc_unwarping,
        "useChartRecognition": args.use_chart_recognition,
    }

    print(f"Submitting OCR job for: {args.source}", flush=True)
    job_id = submit_job(args.source, token, args.job_url, args.model, optional_payload, args.timeout)
    print(f"job_id={job_id}", flush=True)

    job_data = poll_job(job_id, token, args.job_url, args.poll_seconds, args.timeout)
    result_url = job_data.get("resultUrl", {}).get("jsonUrl")
    if not result_url:
        raise RuntimeError(f"OCR job completed but resultUrl.jsonUrl is missing: {job_data}")

    jsonl_text = download_binary(result_url, args.timeout).decode("utf-8")
    extraction = write_results(jsonl_text, args.output_dir, args.timeout)
    manifest = {
        "source": args.source,
        "job_id": job_id,
        "model": args.model,
        "job_url": args.job_url,
        "optional_payload": optional_payload,
        "extract_progress": job_data.get("extractProgress", {}),
        "result_json_url": result_url,
        **extraction,
    }

    manifest_path = args.manifest_out or (args.output_dir / "ocr-manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "combined_markdown": extraction["combined_markdown"]}, ensure_ascii=False, indent=2))


# Internal module — do not invoke directly. Use skill_step.py as the public entry point.
if __name__ == "__main__":
    main()
