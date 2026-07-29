import asyncio
import json
import logging
import os
import tempfile
import time
from typing import List, Tuple

import cognee

from models import SlitherDetector, SlitherElement, SlitherReport

logger = logging.getLogger(__name__)

# Slither exit codes:
#   0   — success, no detectors triggered
#   1   — success, detectors triggered (findings found) — NOT an error
#   2   — compilation failure
#   255 — known Slither bug: exits 255 even on successful analysis with
#          certain solc versions. We trust the JSON stdout over the exit code.
_SLITHER_ERROR_THRESHOLD = 2


async def run_slither(source_code: str, contract_name: str) -> SlitherReport:
    """
    Write Solidity source to a temp file, run Slither with JSON output,
    parse the result, and clean up. Never raises — returns a failed
    SlitherReport on all error paths so the caller always gets a result.
    """
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".sol",
            prefix=f"{contract_name}_",
            delete=False,
        ) as f:
            f.write(source_code)
            tmp_path = f.name

        proc = await asyncio.create_subprocess_exec(
            "slither",
            tmp_path,
            "--json",
            "-",
            "--disable-color",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logger.error("Slither timed out on contract=%s", contract_name)
            return SlitherReport(success=False, detectors=[])

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        # Always log stderr at INFO so Render logs show the actual error
        if stderr_text:
            logger.info(
                "Slither stderr [%s]: %s",
                contract_name,
                stderr_text[:2000],
            )

        if stdout_text:
            report = _parse_slither_json(stdout_text, contract_name)
            if report.success or report.detectors:
                if proc.returncode not in (0, 1):
                    logger.info(
                        "Slither exited %d but stdout is valid — using JSON result for contract=%s",
                        proc.returncode,
                        contract_name,
                    )
                return report

        # stdout is empty or unparseable — now check exit code
        if proc.returncode is not None and proc.returncode >= _SLITHER_ERROR_THRESHOLD:
            logger.error(
                "Slither hard failure (exit=%d) for contract=%s stderr=%r",
                proc.returncode,
                contract_name,
                stderr_text[:500],
            )
            return SlitherReport(success=False, detectors=[])

        if not stdout_text:
            logger.warning(
                "Slither produced no stdout for contract=%s", contract_name
            )
            return SlitherReport(success=True, detectors=[])

        return _parse_slither_json(stdout_text, contract_name)

    except FileNotFoundError:
        logger.error("Slither binary not found in PATH")
        raise RuntimeError(
            "slither not found — ensure slither-analyzer is installed in the container"
        )

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError as e:
                logger.warning("Failed to remove temp file %s: %s", tmp_path, e)


def _parse_slither_json(raw: str, contract_name: str) -> SlitherReport:
    """
    Parse Slither's --json - stdout into a SlitherReport.

    Slither JSON structure:
      {
        "success": bool,
        "error": null | str,
        "results": {
          "detectors": [ { "check", "impact", "confidence",
                           "description", "elements": [...] } ]
        }
      }
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "Failed to parse Slither JSON for contract=%s: %s", contract_name, exc
        )
        return SlitherReport(success=False, detectors=[])

    success: bool = bool(data.get("success", False))
    results: dict = data.get("results", {})
    raw_detectors: list = results.get("detectors", [])

    detectors: List[SlitherDetector] = []
    for raw_det in raw_detectors:
        elements: List[SlitherElement] = [
            SlitherElement.from_slither(el)
            for el in raw_det.get("elements", [])
        ]
        detectors.append(
            SlitherDetector(
                check=raw_det.get("check", "unknown"),
                impact=raw_det.get("impact", "Informational"),
                confidence=raw_det.get("confidence", "Low"),
                description=raw_det.get("description", "").strip(),
                elements=elements,
            )
        )

    logger.info(
        "Slither parsed contract=%s success=%s detectors=%d",
        contract_name,
        success,
        len(detectors),
    )
    return SlitherReport(success=success, detectors=detectors)


def _build_cognee_text(
    report: SlitherReport,
    contract_name: str,
    node_set: List[str],
) -> str:
    """
    Serialize SlitherReport into structured plain text for Cognee ingestion.
    """
    lines = [
        f"Contract: {contract_name}",
        f"Tags: {', '.join(node_set)}",
        f"TotalFindings: {len(report.detectors)}",
        "",
    ]

    for i, det in enumerate(report.detectors):
        affected = ", ".join(el.name for el in det.elements) or "none"
        lines += [
            f"Finding[{i}]:",
            f"  VulnerabilityClass: {det.check}",
            f"  Impact: {det.impact}",
            f"  Confidence: {det.confidence}",
            f"  Description: {det.description}",
            f"  AffectedElements: {affected}",
            "",
        ]

    return "\n".join(lines)


async def run_cognee_pipeline(
    report: SlitherReport,
    contract_name: str,
    dataset: str,
    node_set: List[str],
) -> None:
    """
    Add Slither findings text to Cognee and run cognify for graph extraction.
    Cognee failure does not abort the audit — Slither results are still
    returned to Axum regardless.
    """
    if not report.detectors:
        logger.info(
            "No detectors for contract=%s — skipping Cognee ingestion",
            contract_name,
        )
        return

    text = _build_cognee_text(report, contract_name, node_set)

    await cognee.add(text, dataset_name=dataset, node_set=node_set)
    logger.info("cognee.add complete dataset=%s contract=%s", dataset, contract_name)

    await cognee.cognify(datasets=[dataset])
    logger.info("cognee.cognify complete dataset=%s", dataset)


async def run_audit_pipeline(
    source_code: str,
    contract_name: str,
    dataset: str,
    node_set: List[str],
) -> Tuple[SlitherReport, int]:
    """
    Orchestrate the full sidecar pipeline:
      1. Run Slither on the provided Solidity source
      2. Run Cognee cognify on the findings
      3. Return (SlitherReport, elapsed_ms)
    """
    start = time.monotonic()

    slither_report = await run_slither(source_code, contract_name)

    try:
        await run_cognee_pipeline(slither_report, contract_name, dataset, node_set)
    except Exception as exc:
        logger.error(
            "Cognee pipeline failed for contract=%s dataset=%s: %s",
            contract_name,
            dataset,
            exc,
            exc_info=True,
        )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return slither_report, elapsed_ms