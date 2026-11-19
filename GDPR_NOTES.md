# GDPR notes

How GDPR-Vault's design maps to the Regulation (EU) 2016/679. This is an engineering
document, not legal advice: the tool reduces risk, it does not make a processing
operation compliant by itself.

## Pseudonymization, not anonymization (Art. 4(5), Recital 26)

Replacing `Javier Morales Ruiz` with `[NOMBRE_2]` while keeping a mapping file is
**pseudonymization**: the data can be re-identified with "additional information kept
separately". Pseudonymized data is still personal data under the GDPR, but it is an
explicitly recognised safeguard (Arts. 25, 32, 89).

That is why the tool produces two separate artifacts:

| Artifact | Contains personal data? | Intended handling |
|---|---|---|
| `anonymized.txt` / `redacted.pdf` | No direct identifiers | Can go to the processor / external AI |
| `mapping.json` | **Yes**: the re-identification key | Stays with the controller, access-controlled |
| `audit.json` | No: only labels, types, counts and masked previews (`J***** M******`) | Can be kept as evidence of the measure applied |

If the mapping is destroyed and the remaining text contains no indirect identifiers,
the output may approach anonymization, but quasi-identifiers (a rare job title plus a
town, for example) can still single people out. Review before treating output as
anonymous.

### Labels or realistic pseudonyms

The same entity can be replaced by a label (`[NOMBRE_2]`) or by a realistic fake value
(`Pablo Cabrera Iglesias`, a fake DNI with a valid check letter, a fake IBAN with valid
check digits). Both are pseudonymization in the Art. 4(5) sense: what makes it so is that
re-identification needs the separately kept mapping, not the shape of the replacement.

Realistic pseudonyms keep the text natural for readers and external AI tools, but they
need extra care, which the implementation handles:

- **Never derived from the original.** Surrogates are drawn at random per job, so they leak
  nothing about the real value (a hash or a deterministic transform could be reversed or
  linked across documents).
- **No collisions with the document.** A surrogate (or, for names, any word of it) that
  already appears in the original text is rejected; otherwise restoring would rewrite
  unrelated text.
- **Reserved values where they exist.** Fake emails use `example.com` (RFC 2606), which
  can never be a real mailbox. Spain has no reserved range for DNIs, IBANs or phone
  numbers, so a random but valid value could coincide with a real one. Without the rest
  of the original record it identifies no one, but the output should not be used to
  contact anyone or make payments.
- **Special-category data is not faked.** Replacing a diagnosis with a different one would
  change what the document says about a person, so `HEALTH` always keeps a label.
- **Visibly marked in the PDF.** Pseudonyms are printed on a light tint, so a reader of the
  redacted PDF can tell which values are not real.

## Data protection by design and by default (Art. 25)

- **Local processing**: the LLM runs on Ollama on the same machine or network. No
  document, prompt or result is sent to a third-party API.
- **No third-party requests from the browser**: the UI uses system fonts and ships no
  CDN assets. A strict `Content-Security-Policy: default-src 'self'` enforces it. (Loading
  Google Fonts from a CDN transmits the visitor's IP address to Google; a German court
  found that to be a GDPR violation, LG München I, 3 O 17493/20, January 2022.)
- **Safe defaults**: both detection layers are on, the app port is bound to `127.0.0.1`
  in docker compose, Ollama is not published to the host, and the app container runs
  read-only as a non-root user.

## Storage limitation (Art. 5(1)(e)) and erasure (Art. 17)

- Jobs live only in process memory and are purged after `JOB_TTL_SECONDS`
  (15 minutes by default). The UI shows the countdown.
- `DELETE /api/jobs/{id}` (the "Erase now" button) removes a job immediately.
- The uploaded PDF is dropped as soon as the redacted copy is generated.
- API responses carry `Cache-Control: no-store` so browsers do not cache personal data.
- Uploads are spooled to `/tmp`, which is a RAM-backed `tmpfs` in the container.

## Security of processing (Art. 32)

- Redaction in the PDF **removes** the underlying text (not a black rectangle drawn on
  top that can be copy-pasted) and scrubs document metadata such as author and title.
- Structured identifiers are validated with their checksums (DNI/NIE mod 23, IBAN
  mod 97, Social Security mod 97, Luhn), which avoids masking random numbers and
  missing real ones because of a loose regex.
- When the LLM layer is unavailable the job still completes, but the audit log and the
  UI state explicitly that names and addresses were **not** detected. Failing silently
  would give a false sense of protection.

## Special categories (Art. 9)

The LLM layer flags health information (diagnoses, sick leave reasons, disabilities)
as `HEALTH` so it is masked as well. Other Art. 9 categories (religion, union
membership, sexual orientation…) are not targeted yet.

## Data minimisation (Art. 5(1)(c))

The typical use case: you need an external AI to summarise or translate a contract, but
the AI does not need to know *who* the parties are. Sending `[NOMBRE_2]` instead of the
real name is the minimisation principle applied to an AI workflow. The **Restore** tab
puts the real values back into the AI's answer locally.

## Known gaps

- Detection is probabilistic for semantic entities: LLM recall is not 100%. Treat the
  output as a strong first pass that a human reviews, not as a guarantee.
- Scanned PDFs (no text layer) are rejected; OCR is not implemented.
- Indirect identifiers (job title + location, unique events) are not detected.
