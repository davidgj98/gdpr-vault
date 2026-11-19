"""Generate a realistic-looking Spanish employment contract filled with SYNTHETIC data.

All identifiers have valid checksums (so the validators accept them) but belong to no
one: DNI 12345678Z and IBAN ES91 2100 0418 4502 0005 1332 are the canonical examples
used in Spanish documentation.

    python samples/generate_sample.py  ->  frontend/samples/contrato_ficticio.pdf
"""

from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "frontend" / "samples" / "contrato_ficticio.pdf"

HTML = """
<h1>CONTRATO DE TRABAJO INDEFINIDO</h1>
<p class="meta">Ref. RRHH-2025/0417 · Madrid, 3 de marzo de 2025</p>

<h2>REUNIDOS</h2>
<p>De una parte, <b>Lucía Fernández Ortega</b>, con DNI 48291037F, en nombre y representación de
<b>Navalia Logística Integral S.L.</b>, con domicilio social en Calle de Alcalá 214, 3º B, 28028 Madrid,
en calidad de Directora de Personas.</p>
<p>De otra parte, <b>Javier Morales Ruiz</b>, mayor de edad, nacido el 14 de julio de 1991, con DNI
12345678Z y número de afiliación a la Seguridad Social 28/12345678/40, con domicilio en
Avenida de la Constitución 45, 2º izquierda, 41001 Sevilla.</p>

<h2>CLÁUSULAS</h2>
<p><b>Primera. Objeto.</b> El trabajador prestará servicios como Analista de Datos Senior, con categoría
profesional del Grupo 1 del convenio colectivo aplicable, en las oficinas de la empresa en Madrid
y en régimen de teletrabajo parcial (dos días por semana).</p>
<p><b>Segunda. Retribución.</b> La retribución bruta anual será de 42.500 euros, abonada en 14 pagas
mediante transferencia a la cuenta ES91 2100 0418 4502 0005 1332, de la que es titular el trabajador.
Los gastos de teletrabajo se compensarán con 35 euros mensuales.</p>
<p><b>Tercera. Jornada.</b> La jornada será de 40 horas semanales, de lunes a viernes, con flexibilidad
horaria de entrada entre las 8:00 y las 10:00.</p>
<p><b>Cuarta. Adaptación del puesto.</b> Conforme al informe del servicio de prevención, la empresa
facilitará una silla ergonómica y pantalla elevada, dado que el Sr. Morales presenta una hernia discal
lumbar diagnosticada en 2023.</p>
<p><b>Quinta. Contacto.</b> A efectos de notificaciones, el trabajador designa el correo
javier.morales.ruiz@correo-ejemplo.es y el teléfono +34 612 345 678. En caso de emergencia se contactará
con Elena Ruiz Navarro (NIE X5821937Q), teléfono 91 456 78 90.</p>
<p><b>Sexta. Protección de datos.</b> Los datos personales del trabajador serán tratados por
Navalia Logística Integral S.L. conforme al Reglamento (UE) 2016/679, con la finalidad de gestionar la
relación laboral. Puede ejercer sus derechos escribiendo a dpo@navalia-ejemplo.es.</p>

<p class="sign">Y en prueba de conformidad, ambas partes firman el presente contrato por duplicado.</p>
<p class="sig">Por la empresa: <b>Lucía Fernández Ortega</b> &#160;&#160;&#160;·&#160;&#160;&#160;
El trabajador: <b>Javier Morales Ruiz</b></p>
<p class="foot">Documento ficticio generado para la demo de GDPR-Vault. Todos los datos son sintéticos.</p>
"""

CSS = """
* { font-family: sans-serif; }
body { font-size: 10.5pt; line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 15pt; text-align: center; margin-bottom: 2pt; letter-spacing: 1pt; }
h2 { font-size: 11pt; margin-top: 12pt; border-bottom: 0.5pt solid #999; }
p { text-align: justify; margin: 5pt 0; }
.meta { text-align: center; color: #555; font-size: 9pt; }
.sign { margin-top: 14pt; }
.sig { text-align: center; margin-top: 22pt; }
.foot { margin-top: 18pt; font-size: 8pt; color: #888; text-align: center; }
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    story = pymupdf.Story(html=HTML, user_css=CSS)
    writer = pymupdf.DocumentWriter(str(OUT))
    page_rect = pymupdf.paper_rect("a4")
    content = pymupdf.Rect(56, 56, page_rect.width - 56, page_rect.height - 56)
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(content)
        story.draw(device)
        writer.end_page()
    writer.close()

    # Metadata is personal data too; the redactor must scrub it.
    doc = pymupdf.open(OUT)
    doc.set_metadata({"author": "Lucía Fernández Ortega", "title": "Contrato Javier Morales Ruiz"})
    doc.saveIncr()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
