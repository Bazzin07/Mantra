import json
import os
import re
from typing import Dict, Iterable, List, Set, Tuple
from uuid import uuid4

from ..models import ExtractedEntity


ENTITY_PATTERNS: List[Tuple[str, re.Pattern, float]] = [
    (
        "PROCEDURE_ID",
        re.compile(r"\b(?:SOP|WI|PM|JSA)(?:-[A-Z]+)?-\d{2,5}(?:\s+REV\.?\s*\d+)?\b", re.IGNORECASE),
        0.95,
    ),
    ("REGULATORY_REF", re.compile(r"\b(?:OISD[-\s]?\d+|PESO|FACTORY\s+ACT|ISO[-\s]?\d+(?::\d{4})?)\b", re.IGNORECASE), 0.9),
    ("PART_NUMBER", re.compile(r"\b[A-Z]{2,5}-\d{3,6}(?:-[A-Z0-9]{1,6})?\b"), 0.82),
    # ponytail: all-caps prefix + [-#\s] separator catches coded tags and
    # instrument tags ("P-101A", "PSV #504", "PT 12"). The all-caps requirement is
    # the false-positive guard; tighten the prefix set if prose noise appears.
    ("EQUIPMENT_TAG", re.compile(r"\b[A-Z]{1,4}[-#\s]{0,2}\d{2,5}[A-Z]?(?=\b|[a-z])"), 0.88),
    ("DATE", re.compile(r"\b(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})\b"), 0.8),
]

FAILURE_TERMS = [
    "bearing failure",
    "blockage",
    "cavitation",
    "corrosion",
    "crack",
    "explosion",
    "fire",
    "fouling",
    "ignition",
    "leakage",
    "overheating",
    "overpressure",
    "pressure drop",
    "rupture",
    "seal failure",
    "vibration",
]

PERSON_PATTERN = re.compile(
    # [ \t]+, not \s+: a newline after the name means the next line's field
    # label (e.g. "Supervisor Vikram Nair\nSummary:") would otherwise get
    # swallowed as a third name word ("Vikram Nair Summary").
    r"\b(?:Technician|Operator|Engineer|Inspector|Supervisor|Manager|Foreman|Fitter|Electrician|Mechanic|Welder|Contractor)[ \t]+"
    r"([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,2})\b"
)

# --- Equipment ontology (site-calibratable) ----------------------------------
# Canonical equipment-type prefixes let a spelled-out reference such as
# "Pump 101-A" resolve to the same normalized tag ("P101A") as the coded form
# "P-101A" (FR-09). Defaults follow common ISA-5.1 / industrial conventions and
# are intentionally distinct so different equipment classes are never merged.
#
# CALIBRATION: real plants use site-specific tag conventions. Point
# IKI_EQUIPMENT_ONTOLOGY_PATH at a JSON file to extend/override without code
# changes, e.g.:
#   {"aliases": {"Cooling Tower": "CT"}, "prefixes": ["CT", "HXR"]}
# Overrides merge over the defaults; the spelled-out noun matcher and the
# part-vs-equipment prefix set are derived from the merged map automatically, so
# both ingestion-time extraction and query-time graph lookups stay consistent.
DEFAULT_EQUIPMENT_TYPE_ALIASES: Dict[str, str] = {
    "PUMP": "P",
    "VALVE": "V",
    "COMPRESSOR": "C",
    "MOTOR": "M",
    "HEAT EXCHANGER": "HX",
    "EXCHANGER": "HX",
    "CONDENSER": "CD",
    "COOLER": "CO",
    "CHILLER": "CH",
    "TANK": "TK",
    "VESSEL": "VE",
    "TURBINE": "TB",
    "GENERATOR": "GN",
    "BOILER": "B",
    "FURNACE": "FR",
    "FAN": "FN",
    "BLOWER": "BL",
    "REACTOR": "RX",
    "COLUMN": "CL",
    "TOWER": "TW",
    "DRUM": "DR",
    "FILTER": "FL",
    "STRAINER": "SR",
    "SEPARATOR": "SEP",
    "AGITATOR": "AG",
    "MIXER": "MX",
    "CONVEYOR": "CN",
    "CRUSHER": "CR",
    "DRYER": "DY",
    "CENTRIFUGE": "CF",
}

# Short equipment-tag prefixes (ISA-5.1 style) used to disambiguate tags like
# "HX-2042" from vendor part numbers like "SKF-6312", which share the same shape.
DEFAULT_EQUIPMENT_PREFIXES: Set[str] = {
    "P", "V", "C", "M", "T", "K", "B", "E", "R", "D", "F", "G", "S",
    "PV", "FV", "LV", "TV", "HV", "CV", "MOV", "PSV", "ESD",
    "LT", "PT", "FT", "TT", "LIC", "PIC", "FIC", "TIC",
}


def _load_ontology_overrides() -> Tuple[Dict[str, str], Set[str]]:
    """Load optional site ontology overrides from IKI_EQUIPMENT_ONTOLOGY_PATH.

    Best-effort: a missing/invalid file leaves the defaults untouched.
    """
    path = os.environ.get("IKI_EQUIPMENT_ONTOLOGY_PATH", "").strip()
    if not path or not os.path.exists(path):
        return {}, set()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}, set()
    aliases = {
        " ".join(str(key).upper().split()): str(value).upper().strip()
        for key, value in (data.get("aliases") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    prefixes = {str(prefix).upper().strip() for prefix in (data.get("prefixes") or []) if str(prefix).strip()}
    return aliases, prefixes


_alias_overrides, _prefix_overrides = _load_ontology_overrides()
EQUIPMENT_TYPE_ALIASES: Dict[str, str] = {**DEFAULT_EQUIPMENT_TYPE_ALIASES, **_alias_overrides}
EQUIPMENT_PREFIXES: Set[str] = DEFAULT_EQUIPMENT_PREFIXES | set(EQUIPMENT_TYPE_ALIASES.values()) | _prefix_overrides


def _build_equipment_noun_pattern(aliases: Dict[str, str]) -> "re.Pattern[str]":
    # Longest keys first so multi-word nouns ("heat exchanger") win over "exchanger".
    alternation = "|".join(
        r"\s+".join(re.escape(part) for part in noun.lower().split())
        for noun in sorted(aliases, key=len, reverse=True)
    )
    return re.compile(
        rf"\b({alternation})\s*[-#]?\s*(\d{{2,5}})(?:\s*-?\s*([A-Za-z]{{1,2}}))?\b",
        re.IGNORECASE,
    )


EQUIPMENT_NOUN_PATTERN = _build_equipment_noun_pattern(EQUIPMENT_TYPE_ALIASES)


def _leading_prefix(raw: str) -> str:
    match = re.match(r"([A-Z]{1,4})", raw.upper())
    return match.group(1) if match else ""


def looks_like_equipment_tag(raw: str) -> bool:
    """True when a PART_NUMBER-shaped token is actually an equipment tag.

    A token whose alpha prefix is a known equipment class and that has no
    trailing part-number suffix (e.g. "-2Z", "-XY") should be treated as
    equipment, not a part. "HX-2042" -> equipment; "SKF-6312", "ABC-1234-XY"
    -> parts.
    """
    if _leading_prefix(raw) not in EQUIPMENT_PREFIXES:
        return False
    return not re.search(r"\d-[A-Z0-9]{1,6}$", raw.upper())


class IndustrialEntityExtractor:
    def extract_from_chunks(self, chunks: Iterable, document_id: str) -> List[ExtractedEntity]:
        entities: List[ExtractedEntity] = []
        for chunk in chunks:
            entities.extend(self.extract_from_text(chunk.content, document_id=document_id, chunk_id=chunk.id))
        return dedupe_by_chunk_span(entities)

    def extract_from_text(self, text: str, document_id: str, chunk_id: str) -> List[ExtractedEntity]:
        entities: List[ExtractedEntity] = []
        occupied_spans: List[Tuple[int, int]] = []

        def overlaps(span: Tuple[int, int]) -> bool:
            start, end = span
            return any(start < other_end and other_start < end for other_start, other_end in occupied_spans)

        for entity_type, pattern, confidence in ENTITY_PATTERNS:
            for match in pattern.finditer(text):
                span = match.span()
                # Patterns run in priority order; a higher-priority match blocks any
                # lower-priority match that overlaps it (e.g. the part "ABC-1234-XY"
                # blocks a spurious "ABC-1234" equipment tag, and "SOP-MAINT-042"
                # blocks a spurious "MAINT-042" part number).
                if overlaps(span):
                    continue
                raw = match.group(0)
                if entity_type == "EQUIPMENT_TAG" and looks_like_regulation(raw):
                    continue
                # Defer known equipment tags (e.g. "HX-2042") from PART_NUMBER so
                # the EQUIPMENT_TAG pattern claims them instead. Do not occupy the
                # span, so the later pattern can still match it.
                if entity_type == "PART_NUMBER" and looks_like_equipment_tag(raw):
                    continue
                occupied_spans.append(span)
                entities.append(
                    ExtractedEntity(
                        id=str(uuid4()),
                        document_id=document_id,
                        chunk_id=chunk_id,
                        entity_type=entity_type,
                        text=raw,
                        normalized_text=normalize_entity(entity_type, raw),
                        confidence=confidence,
                    )
                )

        for term in FAILURE_TERMS:
            # Word-boundary match so "fire" doesn't fire on "firewall" / "crack" on "cracked".
            match = re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE)
            if not match:
                continue
            entities.append(
                ExtractedEntity(
                    id=str(uuid4()),
                    document_id=document_id,
                    chunk_id=chunk_id,
                    entity_type="FAILURE_MODE",
                    text=match.group(0),
                    normalized_text=normalize_entity("FAILURE_MODE", term),
                    confidence=0.78,
                )
            )

        for match in PERSON_PATTERN.finditer(text):
            person_name = match.group(1)
            entities.append(
                ExtractedEntity(
                    id=str(uuid4()),
                    document_id=document_id,
                    chunk_id=chunk_id,
                    entity_type="PERSON",
                    text=person_name,
                    normalized_text=normalize_entity("PERSON", person_name),
                    confidence=0.72,
                )
            )

        for match in EQUIPMENT_NOUN_PATTERN.finditer(text):
            canonical = canonical_equipment_tag(match.group(1), match.group(2), match.group(3))
            if not canonical:
                continue
            entities.append(
                ExtractedEntity(
                    id=str(uuid4()),
                    document_id=document_id,
                    chunk_id=chunk_id,
                    entity_type="EQUIPMENT_TAG",
                    text=match.group(0).strip(),
                    normalized_text=normalize_entity("EQUIPMENT_TAG", canonical),
                    confidence=0.80,
                )
            )

        return entities


def canonical_equipment_tag(noun: str, digits: str, suffix: str) -> str:
    """Build a canonical equipment tag from a spelled-out reference.

    "Pump", "101", "A" -> "P101A"; returns "" for unknown equipment nouns so
    the caller skips the match.
    """
    noun_key = " ".join(noun.upper().split())
    prefix = EQUIPMENT_TYPE_ALIASES.get(noun_key)
    if not prefix:
        return ""
    return f"{prefix}{digits}{(suffix or '').upper()}"


def normalize_entity(entity_type: str, raw: str) -> str:
    value = " ".join(raw.upper().replace("_", " ").split())
    if entity_type == "EQUIPMENT_TAG":
        return "".join(char for char in value if char.isalnum())
    if entity_type in {"PROCEDURE_ID", "PART_NUMBER", "REGULATORY_REF"}:
        return value.replace(" ", "")
    return value


def looks_like_regulation(value: str) -> bool:
    return value.upper().startswith(("ISO", "OISD"))


def dedupe_by_chunk_span(entities: List[ExtractedEntity]) -> List[ExtractedEntity]:
    seen = set()
    deduped: List[ExtractedEntity] = []
    for entity in entities:
        key = (entity.chunk_id, entity.entity_type, entity.normalized_text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entity)
    return deduped
