import time

import pymupdf

from tests.conftest import make_client


def wait_for(client, job_id: str, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise TimeoutError(job_id)


def submit_pdf(client, pdf: bytes, **form) -> dict:
    r = client.post("/api/jobs", files={"file": ("contrato.pdf", pdf, "application/pdf")}, data=form)
    assert r.status_code == 202, r.text
    return wait_for(client, r.json()["job_id"])


def test_health_reports_llm(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert data["llm"] == {"available": True, "reachable": True, "model": "test-model"}


def test_full_pipeline_on_sample_pdf(client, sample_pdf):
    job = submit_pdf(client, sample_pdf)
    assert job["status"] == "done", job
    anonymized = job["anonymized_text"]

    for secret in [
        "Javier Morales Ruiz",
        "Lucía Fernández Ortega",
        "12345678Z",
        "48291037F",
        "X5821937Q",
        "ES91 2100 0418 4502 0005 1332",
        "28/12345678/40",
        "612 345 678",
        "91 456 78 90",
        "javier.morales.ruiz@correo-ejemplo.es",
        "Calle de Alcalá 214",
        "hernia",
        "Sr. Morales",
    ]:
        assert secret not in anonymized, secret

    # Non-personal content survives
    assert "42.500 euros" in anonymized and "Reglamento (UE) 2016/679" in anonymized

    audit = job["audit"]
    assert audit["layers"]["llm"]["status"] == "ok"
    assert audit["layers"]["llm"]["discarded_hallucinations"] == 1
    assert audit["by_type"]["DNI"] == 2
    assert "PERSON" in audit["by_type"] and "HEALTH" in audit["by_type"]
    # The audit log must not contain raw personal data
    assert "Javier" not in str(audit["entities"]) and "12345678Z" not in str(audit["entities"])


def test_redacted_pdf_has_no_pii_text_or_metadata(client, sample_pdf):
    job = submit_pdf(client, sample_pdf)
    r = client.get(f"/api/jobs/{job['job_id']}/files/redacted.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"

    doc = pymupdf.open(stream=r.content, filetype="pdf")
    text = "".join(p.get_text() for p in doc)
    assert "12345678Z" not in text and "Morales" not in text and "ES91" not in text
    assert "[DNI_1]" in text or "[DNI_2]" in text
    assert not doc.metadata.get("author")
    # Neighbouring non-personal text must survive intact (regression: glyphs on the
    # line below a redaction were being removed)
    assert text.count("teléfono") == 2 and "42.500 euros" in text


def test_llm_down_degrades_with_warning(settings, sample_pdf):
    with make_client(settings, fail=True) as client:
        job = submit_pdf(client, sample_pdf)
    assert job["status"] == "done"
    assert job["audit"]["layers"]["llm"]["status"] == "unavailable"
    assert job["audit"]["warnings"]
    assert "12345678Z" not in job["anonymized_text"]  # regex layer still protected this
    assert client.get("/api/health").json()["llm"]["available"] is False


def test_layers_can_be_disabled(client, sample_pdf):
    job = submit_pdf(client, sample_pdf, use_llm="false")
    assert job["audit"]["layers"]["llm"]["status"] == "disabled"
    assert "Javier Morales Ruiz" in job["anonymized_text"]
    assert "12345678Z" not in job["anonymized_text"]

    r = client.post("/api/jobs", data={"text": "hola", "use_llm": "false", "use_regex": "false"})
    assert r.status_code == 400


def test_plain_text_job(client):
    r = client.post("/api/jobs", data={"text": "Javier Morales Ruiz, móvil 612345678"})
    job = wait_for(client, r.json()["job_id"])
    assert job["anonymized_text"] == "[NOMBRE_1], móvil [TEL_1]"
    assert job["has_redacted_pdf"] is False


def test_downloads_and_mapping_roundtrip(client):
    r = client.post("/api/jobs", data={"text": "Javier Morales Ruiz, DNI 12345678Z"})
    job = wait_for(client, r.json()["job_id"])
    jid = job["job_id"]

    assert client.get(f"/api/jobs/{jid}/files/anonymized.txt").text == job["anonymized_text"]
    assert client.get(f"/api/jobs/{jid}/files/audit.json").json()["entities_found"] == 2
    mapping = client.get(f"/api/jobs/{jid}/files/mapping.json").json()["mapping"]

    # Simulate a reply from an external AI that only ever saw the labels
    reply = "Resumen: [NOMBRE_1] (documento [DNI_1]) acepta las condiciones."
    restored = client.post("/api/deanonymize", json={"text": reply, "mapping": mapping}).json()
    assert restored == {
        "text": "Resumen: Javier Morales Ruiz (documento 12345678Z) acepta las condiciones.",
        "restored": 2,
    }


def test_delete_erases_job(client):
    jid = client.post("/api/jobs", data={"text": "Javier Morales Ruiz"}).json()["job_id"]
    wait_for(client, jid)
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_jobs_expire(settings):
    settings.job_ttl_seconds = 0
    with make_client(settings) as client:
        jid = client.post("/api/jobs", data={"text": "Javier", "use_llm": "false"}).json()["job_id"]
        assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_rejects_non_pdf_and_bad_pdf(client):
    r = client.post("/api/jobs", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 415
    r = client.post("/api/jobs", files={"file": ("x.pdf", b"%PDF-1.4 garbage", "application/pdf")})
    assert wait_for(client, r.json()["job_id"])["status"] == "error"


def test_scanned_pdf_gives_clear_error(client):
    doc = pymupdf.open()
    doc.new_page()
    job = submit_pdf(client, doc.tobytes())
    assert job["status"] == "error" and "OCR" in job["error"]


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/api/health").headers["cache-control"] == "no-store"
