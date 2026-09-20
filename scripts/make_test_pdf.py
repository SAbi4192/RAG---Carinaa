"""Generate a small multi-page PDF for testing page-aware retrieval.

WHY HAND-WRITTEN
----------------
The project has no PDF *writer* (only pypdf, which reads), so the page features could
only ever be tested against Markdown fixtures. That left one gap: page detection ->
page filter -> answer with a page citation had never run end to end against a real PDF.

PDF is a text format, so a valid multi-page document can be written directly. Each page
carries clearly distinct content, which makes it possible to prove that a question about
page 3 was answered from page 3 and not from luck.

Usage:
    .venv/Scripts/python.exe scripts/make_test_pdf.py
    .venv/Scripts/python.exe scripts/make_test_pdf.py --out data/test_manual.pdf
"""

from __future__ import annotations

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# One distinctive topic per page, so a correct answer is unambiguous.
PAGES: list[tuple[str, list[str]]] = [
    (
        "UNIT I - DESIGN THINKING PRINCIPLES",
        [
            "Design thinking is a human-centred approach to innovation that draws on the",
            "designer's toolkit to integrate the needs of people, the possibilities of",
            "technology, and the requirements for business success.",
            "",
            "The five stages of design thinking are Empathise, Define, Ideate, Prototype",
            "and Test. Each stage is iterative rather than strictly sequential, and teams",
            "routinely return to an earlier stage when testing reveals a wrong assumption.",
            "",
            "Empathise means observing and engaging with the people you are designing for.",
            "Define means stating the problem in a way that is specific enough to act on.",
            "Ideate means generating a wide range of possible solutions before judging any",
            "of them. Prototype means building a cheap representation you can learn from.",
            "Test means putting that representation in front of real users and watching.",
            "",
            "Human-centred design places the person at the centre of every decision. It",
            "asks not what the technology can do, but what the person is trying to achieve.",
            "This distinction is what separates a useful product from an impressive one.",
        ],
    ),
    (
        "UNIT II - DISCOVERING OPPORTUNITIES",
        [
            "Discovering areas of opportunity begins with careful observation of how",
            "people actually behave, rather than how they say they behave.",
            "",
            "Interviewing techniques are the primary tool at this stage. A good interview",
            "is open-ended, follows the interviewee's lead, and avoids leading questions.",
            "The goal is to hear stories, not to confirm a hypothesis you already hold.",
            "",
            "Best practices for effective interviews include asking about specific past",
            "events rather than hypothetical futures, remaining silent long enough for the",
            "interviewee to fill the gap, and never treating a single interview as proof.",
            "",
            "Empathy building requires suspending judgement. The most useful insight is",
            "often the one that contradicts what the team expected to hear, and that is",
            "exactly the insight a defensive interviewer will fail to notice.",
            "",
            "Combining techniques for comprehensive understanding means triangulating:",
            "interviews, observation and artefacts together reveal more than any one alone.",
        ],
    ),
    (
        "UNIT III - CONCEPT GENERATION",
        [
            "Systematic concept generation outlines steps for defining problems,",
            "researching insights, brainstorming ideas, and presenting concepts.",
            "",
            "The process begins with a clearly stated problem. A problem statement that is",
            "too broad produces ideas that cannot be evaluated; one that is too narrow",
            "presumes the solution. The useful middle ground names the user, the need, and",
            "the constraint without prescribing the answer.",
            "",
            "A sustainable packaging case study illustrates the approach. The team began",
            "with the observation that single-use packaging was being discarded within",
            "minutes of purchase, then generated concepts across materials, reuse models",
            "and supply-chain changes rather than limiting themselves to one category.",
            "",
            "Evaluation of technology alternatives provides a structured approach to",
            "selecting technologies that meet project requirements, scalability and",
            "sustainability. Each alternative is scored against criteria agreed in advance,",
            "which prevents the loudest voice in the room from deciding the outcome.",
        ],
    ),
    (
        "UNIT IV - DOCUMENT AND COMMUNICATE",
        [
            "Document and communicate stresses the importance of recording technical",
            "specifications, economic viability, and delivering stakeholder presentations.",
            "",
            "Technical specifications capture what the solution does and the conditions",
            "under which it works. Writing them down forces precision that discussion",
            "alone does not, and creates a reference when the team changes.",
            "",
            "Economic viability asks whether the solution can be sustained. A concept that",
            "works technically but cannot be funded, staffed or maintained is not a",
            "viable concept, however elegant it may be.",
            "",
            "Stakeholder presentations must address different audiences differently. An",
            "engineering audience wants tolerances and failure modes; a finance audience",
            "wants cost and payback. The same solution is described, not a different one.",
        ],
    ),
    (
        "UNIT V - FORGE INNOVATION RUBRIC",
        [
            "Applying the Forge Innovation Rubric describes assessing feasibility and",
            "viability for projects such as an AI-driven personalised learning platform.",
            "",
            "Feasibility is assessed across technical, resource and operational",
            "dimensions. Technical feasibility asks whether it can be built. Resource",
            "feasibility asks whether the team, time and budget exist. Operational",
            "feasibility asks whether it can be run once built.",
            "",
            "Viability is assessed across financial, market and business-model dimensions.",
            "Financial viability asks whether the numbers work. Market viability asks",
            "whether anyone wants it. Business-model viability asks whether it can sustain",
            "itself beyond the initial funding.",
            "",
            "Steps to mitigate validation risk using the rubric include testing the",
            "riskiest assumption first, defining in advance what evidence would falsify",
            "the concept, and being willing to stop when that evidence appears.",
        ],
    ),
]


def build_pdf(pages: list[tuple[str, list[str]]]) -> bytes:
    """Assemble a minimal but valid PDF with one page per entry."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)  # 1-based object number

    # Every indirect reference has to be known BEFORE the object that uses it is
    # written, so the three shared objects are reserved up front. Computing the font
    # number per page instead made it drift after the first page, and pypdf then
    # resolved /F1 to a content stream rather than a font.
    catalog_num = add(b"")
    pages_num = add(b"")
    font_num = add(b"")

    page_numbers: list[int] = []
    for title, lines in pages:
        # The page and its content stream are appended together, so the stream's
        # number is the page's number plus one.
        page_num = len(objects) + 1
        content_num = page_num + 1

        stream_lines = ["BT", "/F1 16 Tf", "72 720 Td", f"({_escape(title)}) Tj", "/F1 11 Tf"]
        y = 690
        for line in lines:
            stream_lines.append(f"1 0 0 1 72 {y} Tm")
            stream_lines.append(f"({_escape(line)}) Tj")
            y -= 18
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1")

        add(
            (
                f"<< /Type /Page /Parent {pages_num} 0 R "
                f"/MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
                f"/Contents {content_num} 0 R >>"
            ).encode("latin-1")
        )
        page_numbers.append(page_num)
        add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

    objects[font_num - 1] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    objects[catalog_num - 1] = f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode("latin-1")
    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    objects[pages_num - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_numbers)} >>".encode("latin-1")
    )

    # ---- serialise with a correct cross-reference table -------------------
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")

    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_num} 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/test_manual.pdf")
    args = parser.parse_args()

    target = PROJECT_ROOT / args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_pdf(PAGES))

    # Read it straight back, so a malformed file fails here rather than during
    # ingestion where the cause would be harder to see.
    from pypdf import PdfReader

    reader = PdfReader(str(target))
    extracted = [page.extract_text() or "" for page in reader.pages]

    print(f"wrote {target} ({target.stat().st_size} bytes, {len(reader.pages)} pages)")
    for index, text in enumerate(extracted, start=1):
        first_line = next((line for line in text.splitlines() if line.strip()), "")
        print(f"  page {index}: {first_line[:70]!r}")

    expected = len(PAGES)
    if len(reader.pages) != expected:
        print(f"FAIL expected {expected} pages, pypdf read {len(reader.pages)}")
        return 1
    if not all(text.strip() for text in extracted):
        print("FAIL at least one page has no extractable text")
        return 1

    print("OK: valid PDF, all pages carry extractable text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
