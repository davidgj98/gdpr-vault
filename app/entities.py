"""Core data types shared by detectors, the anonymizer and the API."""

from dataclasses import dataclass
from enum import StrEnum


class PIIType(StrEnum):
    # Structured identifiers (regex + checksum validation)
    DNI = "DNI"
    NIE = "NIE"
    NSS = "NSS"  # Spanish Social Security number
    IBAN = "IBAN"
    CREDIT_CARD = "CREDIT_CARD"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    POSTAL_CODE = "POSTAL_CODE"
    # Semantic entities (LLM)
    PERSON = "PERSON"
    ADDRESS = "ADDRESS"
    ORGANIZATION = "ORGANIZATION"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    HEALTH = "HEALTH"  # special category, GDPR Art. 9


# Prefix used in the pseudonym, e.g. PERSON -> [NOMBRE_1]
LABEL_PREFIX: dict[PIIType, str] = {
    PIIType.DNI: "DNI",
    PIIType.NIE: "NIE",
    PIIType.NSS: "NSS",
    PIIType.IBAN: "IBAN",
    PIIType.CREDIT_CARD: "TARJETA",
    PIIType.PHONE: "TEL",
    PIIType.EMAIL: "EMAIL",
    PIIType.POSTAL_CODE: "CP",
    PIIType.PERSON: "NOMBRE",
    PIIType.ADDRESS: "DIRECCION",
    PIIType.ORGANIZATION: "EMPRESA",
    PIIType.DATE_OF_BIRTH: "FECHA_NAC",
    PIIType.HEALTH: "SALUD",
}

SEMANTIC_TYPES = {
    PIIType.PERSON,
    PIIType.ADDRESS,
    PIIType.ORGANIZATION,
    PIIType.DATE_OF_BIRTH,
    PIIType.HEALTH,
}


@dataclass(frozen=True, slots=True)
class Span:
    """A detected PII occurrence, addressed by character offsets in the source text."""

    start: int
    end: int
    text: str
    type: PIIType
    method: str  # "regex" | "llm"

    def __len__(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end
