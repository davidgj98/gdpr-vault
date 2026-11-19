"""Realistic surrogates: replace personal data with fake values of the same kind.

"Javier Morales Ruiz, DNI 12345678Z" becomes "Pablo Navarro Gil, DNI 73920184K" instead of
"[NOMBRE_1], DNI [DNI_1]". The text keeps reading naturally, which helps both humans and
external AI tools (they handle names better than bracketed tokens), and the mapping file can
still reverse it, so it remains pseudonymization in the sense of GDPR Art. 4(5).

Rules that keep this safe and reversible:

- **Consistent at token level**: once "Morales" becomes "Navarro", "el Sr. Morales" becomes
  "el Sr. Navarro" everywhere, so a reader can still follow who is who.
- **Never collides with the document**: no surrogate (or, for people, no surrogate word) may
  already appear in the original text. Otherwise restoring would rewrite unrelated text.
- **Format-preserving**: a fake IBAN keeps the original grouping and a valid checksum, a fake
  phone keeps the original spacing, so downstream parsers and the reader are not confused.
- **Random per job**, not derived from the original value, so surrogates leak nothing.
- **Health data is not faked**: inventing a different diagnosis would change the meaning of
  the document, so special-category data (Art. 9) keeps a neutral label.
"""

import random
import re
import secrets
import unicodedata

from app.detectors.regex import _DNI_LETTERS
from app.entities import PIIType

MALE = (
    "Pablo Andrés Sergio Rubén Adrián Hugo Marcos Iván Álvaro Gonzalo Diego Mario Óscar Tomás "
    "Rafael Víctor Ignacio Jaime Bruno Daniel Alberto Héctor Samuel Raúl Martín Emilio "
    "Lorenzo"
).split()
FEMALE = (
    "Laura Clara Irene Sara Nuria Alba Paula Noelia Sonia Beatriz Rocío Silvia Eva Inés Lidia "
    "Carmen Teresa Patricia Elisa Julia Celia Raquel Lorena Olga Marina Ainhoa Miriam Natalia"
).split()
SURNAMES = (
    "Navarro Gil Castro Ortiz Delgado Rubio Molina Serrano Blanco Suárez Ramos Vidal Prieto "
    "Iglesias Campos Medina Cortés Lozano Garrido Peña Herrera Santana Pascual Aguilar Calvo "
    "Vega Nieto Soler Bravo Rojas Varela Crespo Gallego Carmona Pastor Montero Sáez Benítez "
    "Méndez Arias Cabrera Rivas Hidalgo Marín Ferrer Mora Esteban Parra Velasco Caballero"
).split()
STREETS = (
    "de los Almendros|del Olivar|de la Fuente|del Pinar|de las Acacias|del Río|de la Estación|"
    "del Molino|de la Alameda|de los Tilos|del Mirador|de la Ribera|del Prado|de las Huertas"
).split("|")
STREET_TYPES = ("Calle", "Avenida", "Paseo", "Calle", "Plaza")
CITIES = (
    ("28", "Madrid"),
    ("08", "Barcelona"),
    ("46", "Valencia"),
    ("41", "Sevilla"),
    ("50", "Zaragoza"),
    ("29", "Málaga"),
    ("30", "Murcia"),
    ("48", "Bilbao"),
    ("47", "Valladolid"),
    ("15", "A Coruña"),
    ("33", "Oviedo"),
    ("18", "Granada"),
)
COMPANY_WORDS = (
    "Aurora Brisa Cumbre Delta Horizonte Litoral Meridiano Nexo Orbe Prisma Quasar Senda "
    "Templo Umbral Vértice Zenit Atalaya Faro"
).split()
COMPANY_SECTORS = ("Servicios", "Consultores", "Soluciones", "Distribución", "Ingeniería", "Gestión", "Logística")
MONTHS = ("enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre").split()

PARTICLES = {"de", "del", "la", "las", "los", "y", "i"}


def fold(s: str) -> str:
    """'Álvaro' -> 'alvaro' (case- and accent-insensitive comparisons)."""
    return "".join(c for c in unicodedata.normalize("NFD", s.casefold()) if unicodedata.category(c) != "Mn")


_EXTRA_GIVEN = (
    "javier juan josé jose maría maria ana lucía lucia elena marta luis carlos antonio manuel "
    "francisco david miguel pedro jorge alejandro fernando isabel cristina pilar dolores rosa "
    "mercedes josefa ángel angel enrique ramón andrea alicia"
).split()
_ALL_GIVEN = {fold(n) for n in MALE + FEMALE + _EXTRA_GIVEN}


HONORIFICS = {fold(h) for h in ("sr", "sra", "srta", "dr", "dra", "d", "dña", "don", "doña")}
FEMALE_HONORIFICS = {fold(h) for h in ("sra", "srta", "dra", "dña", "doña")}


def _reshape(original: str, compact: str) -> str:
    """Pour the characters of `compact` into the layout of `original` ('ES91 2100…' grouping)."""
    it = iter(compact)
    return "".join(next(it) if c.isalnum() else c for c in original)


def _match_case(original: str, fake: str) -> str:
    return fake.upper() if original.isupper() and len(original) > 1 else fake


class SurrogateFactory:
    """Generates surrogates for one document. Create one per job."""

    def __init__(self, text: str, seed: int | None = None):
        self.rng = random.Random(seed if seed is not None else secrets.randbits(64))
        self._text = fold(text)
        self._words = set(re.findall(r"\w+", self._text))
        self._used: set[str] = set()
        self._given: dict[str, str] = {}  # folded original first name -> fake
        self._surname: dict[str, str] = {}  # folded original surname -> fake

    # ── public ────────────────────────────────────────────────────────────
    def make(self, pii_type: PIIType, original: str) -> str | None:
        """A fake value for `original`, or None when the type should keep a label."""
        generator = {
            PIIType.PERSON: self._person,
            PIIType.DNI: self._dni,
            PIIType.NIE: self._nie,
            PIIType.NSS: self._nss,
            PIIType.IBAN: self._iban,
            PIIType.CREDIT_CARD: self._card,
            PIIType.PHONE: self._phone,
            PIIType.EMAIL: self._email,
            PIIType.POSTAL_CODE: self._postal_code,
            PIIType.ADDRESS: self._address,
            PIIType.ORGANIZATION: self._organization,
            PIIType.DATE_OF_BIRTH: self._date,
        }.get(pii_type)
        if generator is None:
            return None
        for _ in range(50):
            fake = generator(original)
            if fake and self._is_free(fake):
                self._used.add(fold(fake))
                return fake
        return None  # could not find a safe surrogate: fall back to a label

    # ── safety ────────────────────────────────────────────────────────────
    def _is_free(self, fake: str) -> bool:
        key = fold(fake)
        if key in self._used:
            return False
        return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", self._text) is None

    def _pick_word(self, pool: list[str], taken: set[str]) -> str:
        options = [w for w in pool if fold(w) not in self._words and w not in taken]
        return self.rng.choice(options or pool)

    # ── people ────────────────────────────────────────────────────────────
    def _person(self, original: str) -> str:
        out: list[str] = []
        female = after_honorific = False
        only_given_so_far = True
        seen = 0
        for i, tok in enumerate(original.split()):
            bare = fold(tok.rstrip("."))
            if i == 0 and bare in HONORIFICS:  # "Sr. Morales": keep the title, it is not data
                female, after_honorific = bare in FEMALE_HONORIFICS, True
                out.append(tok)
                continue
            if bare in PARTICLES:  # "de la Fuente"
                out.append(tok)
                continue
            first = seen == 0 and not after_honorific
            if only_given_so_far and (first or bare in _ALL_GIVEN):
                if seen == 0:
                    female = female or self._looks_female(bare)
                out.append(_match_case(tok, self._fake_given(bare, female)))
            else:
                only_given_so_far = False
                out.append(_match_case(tok, self._fake_surname(bare)))
            seen += 1
        return " ".join(out)

    @staticmethod
    def _looks_female(name: str) -> bool:
        if name in {fold(n) for n in FEMALE} | {"maria", "isabel", "pilar", "dolores", "mercedes", "rocio"}:
            return True
        return name.endswith("a") and name not in {"luca", "borja", "josema"}

    def _fake_given(self, bare: str, female: bool) -> str:
        if bare not in self._given:
            self._given[bare] = self._pick_word(FEMALE if female else MALE, set(self._given.values()))
        return self._given[bare]

    def _fake_surname(self, bare: str) -> str:
        if bare not in self._surname:
            self._surname[bare] = self._pick_word(SURNAMES, set(self._surname.values()))
        return self._surname[bare]

    # ── identifiers (valid checksums, original layout) ────────────────────
    def _digits(self, n: int) -> str:
        return "".join(str(self.rng.randrange(10)) for _ in range(n))

    def _dni(self, original: str) -> str | None:
        number = self.rng.randrange(10_000_000, 99_999_999)
        letter = _DNI_LETTERS[number % 23]
        return _reshape(original, f"{number:08d}{letter}") if len(re.sub(r"\W", "", original)) == 9 else None

    def _nie(self, original: str) -> str | None:
        prefix = self.rng.randrange(3)
        number = self.rng.randrange(1_000_000, 9_999_999)
        letter = _DNI_LETTERS[int(f"{prefix}{number:07d}") % 23]
        compact = f"{'XYZ'[prefix]}{number:07d}{letter}"
        return _reshape(original, compact) if len(re.sub(r"\W", "", original)) == 9 else None

    def _nss(self, original: str) -> str | None:
        province = self.rng.randrange(1, 53)
        number = self.rng.randrange(10_000_000, 99_999_999)
        control = (province * 10**8 + number) % 97
        compact = f"{province:02d}{number:08d}{control:02d}"
        return _reshape(original, compact) if len(re.sub(r"\D", "", original)) == 12 else None

    def _iban(self, original: str) -> str:
        compact = re.sub(r"\s", "", original).upper()
        country, bban = compact[:2], self._digits(len(compact) - 4)
        numeric = "".join(str(int(c, 36)) for c in bban + country + "00")
        check = 98 - int(numeric) % 97
        return _reshape(original, f"{country}{check:02d}{bban}")

    def _card(self, original: str) -> str:
        digits = re.sub(r"\D", "", original)
        body = digits[0] + self._digits(len(digits) - 2)
        total = 0
        for i, d in enumerate(reversed(body)):  # Luhn check digit
            n = int(d) * (2 if i % 2 == 0 else 1)
            total += n - 9 if n > 9 else n
        return _reshape(original, body + str((10 - total % 10) % 10))

    def _phone(self, original: str) -> str:
        digits = re.sub(r"\D", "", original)
        country = next((p for p in ("0034", "34") if digits.startswith(p) and len(digits) == len(p) + 9), "")
        prefix = digits[: len(country) + 1]  # keep the line type: 6/7 mobile, 8/9 landline
        return _reshape(original, prefix + self._digits(len(digits) - len(prefix)))

    def _email(self, original: str) -> str:
        # "javier.morales.ruiz@…" follows the person's pseudonym when that person is already
        # known ("victor.campos.medina@…"), so the document stays coherent.
        local = original.split("@")[0]
        parts = re.split(r"([._-])", fold(local))
        known = {**self._given, **self._surname}
        if any(part in known for part in parts):
            fake_local = "".join(fold(known.get(part, part)) if part.isalpha() else part for part in parts)
            if not any(part.isalpha() and part not in known for part in parts):
                return f"{fake_local}@example.com"
        given, surname = self.rng.choice(MALE + FEMALE), self.rng.choice(SURNAMES)
        return f"{fold(given)}.{fold(surname)}@example.com"  # RFC 2606 reserved domain: never a real mailbox

    def _postal_code(self, _original: str) -> str:
        province, _ = self.rng.choice(CITIES)
        return f"{province}{self.rng.randrange(1, 999):03d}"

    def _address(self, original: str) -> str:
        street = f"{self.rng.choice(STREET_TYPES)} {self.rng.choice(STREETS)} {self.rng.randrange(1, 180)}"
        if re.search(r"\d+\s*º|\bpiso\b|\bpuerta\b|\bizquierda\b|\bderecha\b", original, re.I):
            street += f", {self.rng.randrange(1, 9)}º {self.rng.choice('ABCD')}"
        if re.search(r"\b\d{5}\b", original):
            province, city = self.rng.choice(CITIES)
            street += f", {province}{self.rng.randrange(1, 999):03d} {city}"
        return street

    def _organization(self, original: str) -> str:
        suffix = re.search(r"\s*,?\s*(S\.?\s?L\.?\s?U\.?|S\.?\s?L\.?|S\.?\s?A\.?|S\.?\s?Coop\.?)$", original)
        name = f"{self.rng.choice(COMPANY_WORDS)} {self.rng.choice(COMPANY_SECTORS)}"
        return name + (" " + suffix.group(1).strip() if suffix else "")

    def _date(self, original: str) -> str | None:
        year_match = re.search(r"(19|20)\d{2}", original)
        year = int(year_match.group()) if year_match else 1980
        year += self.rng.choice((-2, -1, 1, 2))  # keep the age roughly right
        day, month = self.rng.randrange(1, 29), self.rng.randrange(1, 13)
        if m := re.fullmatch(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{2,4})", original.strip()):
            sep = m.group(2)
            y = str(year) if len(m.group(4)) == 4 else f"{year % 100:02d}"
            return f"{day:02d}{sep}{month:02d}{sep}{y}"
        if re.search(r"\bde\b", original):
            return f"{day} de {MONTHS[month - 1]} de {year}"
        return None
