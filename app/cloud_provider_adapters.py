"""Conservative, provenance-preserving extraction of public compute claims.

Extracted values are observations from page text, not verified provider guarantees.
No model is asked to infer missing limits and no claim is accepted automatically.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExtractedResourceClaim:
    claim_type: str
    value: Any
    unit: str | None
    evidence_quote: str
    adapter_id: str


class CloudProviderAdapter(Protocol):
    adapter_id: str

    def matches(self, domain: str, source_url: str | None = None) -> bool: ...

    def extract_claims(self, title: str, text: str) -> list[ExtractedResourceClaim]: ...


def _number(value: str) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


class KaggleCliDocsAdapter:
    """Extract documented Kaggle CLI options, not account-specific quota or entitlement."""

    adapter_id = "kaggle-cli-docs-v1"
    _official_paths = {
        "/kaggle/kaggle-cli/blob/main/docs/kernels.md",
        "/kaggle/kaggle-cli/blob/main/skills/references/kernels.md",
    }
    _accelerators = (
        "NvidiaTeslaT4",
        "NvidiaTeslaA100",
        "NvidiaL4",
        "TpuV5E8",
        "TpuV6E8",
        "NvidiaH100",
        "NvidiaRtxPro6000",
    )
    _operations = (
        "kaggle kernels push",
        "kaggle kernels status",
        "kaggle kernels output",
        "kaggle kernels logs",
    )

    def matches(self, domain: str, source_url: str | None = None) -> bool:
        if not isinstance(source_url, str):
            return False
        parsed = urllib.parse.urlsplit(source_url)
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").casefold() == "github.com"
            and parsed.path.casefold() in self._official_paths
            and not parsed.query
            and not parsed.fragment
            and domain.casefold().strip(".") == "github.com"
        )

    def extract_claims(self, title: str, text: str) -> list[ExtractedResourceClaim]:
        content = f"{title}\n{text}"
        claims: list[ExtractedResourceClaim] = []
        for accelerator in self._accelerators:
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(accelerator)}(?![A-Za-z0-9])")
            for match in pattern.finditer(content):
                claims.append(
                    ExtractedResourceClaim(
                        claim_type="provider_accelerator_option",
                        value=accelerator,
                        unit="documented CLI option; account entitlement and availability unverified",
                        evidence_quote=" ".join(match.group(0).split()),
                        adapter_id=self.adapter_id,
                    )
                )
        folded = content.casefold()
        for operation in self._operations:
            if operation.casefold() in folded:
                claims.append(
                    ExtractedResourceClaim(
                        claim_type="provider_operation",
                        value=operation,
                        unit="documented CLI operation; not an execution authorization",
                        evidence_quote=operation,
                        adapter_id=self.adapter_id,
                    )
                )
        # Keep each claim tied to text that actually appeared in the fetched document.
        return claims[:40]


class GenericPublicPageAdapter:
    """Fallback regex adapter; intentionally extracts only explicit textual claims."""

    adapter_id = "generic-public-page-regex-v1"

    _patterns = (
        ("cpu_count", re.compile(r"\b(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>vCPU|CPU cores?|cores?)\b", re.I)),
        ("memory_capacity", re.compile(r"\b(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>GiB|GB|MiB|MB)\s*(?:of\s+)?(?:RAM|memory)\b", re.I)),
        ("storage_capacity", re.compile(r"\b(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>GiB|GB|MiB|MB)\s*(?:of\s+)?(?:persistent\s+)?(?:disk|storage|volume)\b", re.I)),
        ("monthly_compute_hours", re.compile(r"\b(?P<value>\d+(?:\.\d+)?)\s*(?:compute\s*)?hours?\s*(?:per|/)\s*month\b", re.I)),
        ("session_limit", re.compile(r"\b(?:maximum|max|up to|limited to|limit(?:ed)? to)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>minutes?|hours?|hrs?)\s*(?:per\s+)?session\b", re.I)),
        ("session_limit", re.compile(r"\bsession(?: time)? limit\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>minutes?|hours?|hrs?)\b", re.I)),
        ("credit_amount", re.compile(r"(?<!\w)(?:USD\s*)?\$\s?(?P<value>\d+(?:\.\d{1,2})?)\s*(?:in\s+)?(?:cloud\s+)?credits?\b", re.I)),
        ("price_rate", re.compile(r"\$\s?(?P<value>\d+(?:\.\d{1,2})?)\s*(?:/|per\s+)(?P<unit>hour|month)\b", re.I)),
        ("gpu_model", re.compile(r"\b(?:NVIDIA\s+)?(?P<value>A100|H100|T4|L4|V100|A10G|RTX\s?\d{3,4})\s*(?:GPU)?\b", re.I)),
        ("region", re.compile(r"\b(?P<value>us-east-1|us-west-1|us-west-2|eu-west-1|eu-central-1|ap-southeast-1|us-central1|europe-west\d|eastus|westus2|westeurope)\b", re.I)),
        ("free_tier_mention", re.compile(r"\b(?P<value>free tier|free plan|free credits|free trial|always free)\b", re.I)),
        ("usage_quota", re.compile(r"\b(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>hours?|credits?|requests?|tokens?)\s*(?:per|/)\s*(?P<period>month|day|week)\b", re.I)),
    )

    def matches(self, domain: str, source_url: str | None = None) -> bool:
        return bool(domain)

    def extract_claims(self, title: str, text: str) -> list[ExtractedResourceClaim]:
        content = f"{title}\n{text}"
        claims: list[ExtractedResourceClaim] = []
        seen: set[tuple[str, str, str | None, str]] = set()
        for claim_type, pattern in self._patterns:
            for match in pattern.finditer(content):
                raw_value = match.group("value").strip()
                unit = match.groupdict().get("unit")
                period = match.groupdict().get("period")
                if claim_type in {"cpu_count", "memory_capacity", "storage_capacity",
                                  "monthly_compute_hours", "session_limit", "credit_amount",
                                  "price_rate", "usage_quota"}:
                    value: Any = _number(raw_value)
                else:
                    value = raw_value
                if claim_type == "usage_quota" and period:
                    unit = f"{unit} per {period}"
                elif claim_type == "monthly_compute_hours":
                    unit = "hours per month"
                elif claim_type == "credit_amount":
                    unit = "USD credits"
                elif claim_type == "price_rate":
                    unit = f"USD per {unit}"
                elif claim_type == "free_tier_mention":
                    unit = None
                quote = " ".join(match.group(0).split())[:240]
                key = (claim_type, str(value), unit, quote)
                if key in seen:
                    continue
                seen.add(key)
                claims.append(
                    ExtractedResourceClaim(
                        claim_type=claim_type,
                        value=value,
                        unit=unit,
                        evidence_quote=quote,
                        adapter_id=self.adapter_id,
                    )
                )
                if len(claims) >= 40:
                    return claims
        return claims


_ADAPTERS: tuple[CloudProviderAdapter, ...] = (
    KaggleCliDocsAdapter(),
    GenericPublicPageAdapter(),
)


def extract_resource_claims(
    domain: str,
    title: str,
    text: str,
    source_url: str | None = None,
) -> list[ExtractedResourceClaim]:
    """Select a source-aware adapter; extracted claims always remain unreviewed."""
    for adapter in _ADAPTERS:
        if adapter.matches(domain, source_url):
            return adapter.extract_claims(title, text)
    return []
