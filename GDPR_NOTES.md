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
