from pathlib import Path
import pypdfium2 as pdfium
p = Path(__file__).resolve().parent
doc = pdfium.PdfDocument(p / "SIGNIX_Demo_Summary.pdf")
doc[0].render(scale=2).to_pil().save(p / "pdf-page.png")
