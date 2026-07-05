from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SidecarAuditRequest(BaseModel):
    source_code: str
    contract_name: str
    dataset: str
    node_set: List[str]


class SlitherElement(BaseModel):
    """
    Maps to Rust's SlitherElement. The JSON field is `type` (a Python builtin),
    so we store it as `element_type` internally and alias it for both
    serialization and deserialization.
    """

    model_config = ConfigDict(populate_by_name=True)

    element_type: str = Field(..., alias="type")
    name: str
    source_mapping: Optional[Any] = None

    @classmethod
    def from_slither(cls, raw: dict) -> "SlitherElement":
        return cls.model_validate(
            {
                "type": raw.get("type", "unknown"),
                "name": raw.get("name", "unknown"),
                "source_mapping": raw.get("source_mapping"),
            }
        )


class SlitherDetector(BaseModel):
    check: str
    impact: str
    confidence: str
    description: str
    elements: List[SlitherElement] = Field(default_factory=list)


class SlitherReport(BaseModel):
    success: bool
    detectors: List[SlitherDetector] = Field(default_factory=list)


class SidecarAuditResult(BaseModel):
    slither_report: SlitherReport
    elapsed_ms: int