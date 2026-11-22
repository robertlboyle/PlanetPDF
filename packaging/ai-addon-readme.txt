PlanetPDF AI Text Reading (add-on)

This adds AI text reading to PlanetPDF's exports: when you export a PDF to Word, Rich Text, text or
HTML, PlanetPDF can read the pages with Unlimited-OCR, an AI model (MIT license, by Baidu), and write
one flowing document, with headings, paragraphs, lists and tables in reading order across pages.
It works best on scanned documents.

WHAT IT NEEDS
  * Windows 10 or 11, 64-bit, with PlanetPDF already installed.
  * An NVIDIA graphics card with about 8 GB of memory or more, and a current NVIDIA driver
    (version 570 or later). It does NOT run on Intel or AMD graphics, or without a graphics card:
    on such computers the option stays grayed out in PlanetPDF.
  * About 12 GB of free disk space.

WHAT IT INSTALLS
  Into PlanetPDF's folder (ai-ocr): its own Python 3.12, PyTorch with NVIDIA CUDA, Hugging Face
  Transformers and the Unlimited-OCR model. PlanetPDF's updates never change or remove it; uninstall
  it from Windows' Apps list ("PlanetPDF AI Text Reading").

HOW IT'S USED
  In PlanetPDF: Export (Ctrl+E), choose Word, Rich Text, text or HTML, and tick "Read the pages with
  AI (Unlimited-OCR)". From the command line: planetpdf export FILE --format docx --ai --out FILE.
  The first export loads the model onto the graphics card (that takes a little while); it stays
  loaded for five minutes for the next export, then frees the card's memory.

Everything runs on this computer: no document is sent anywhere.

Keep Setup.exe and its .bin files together in one folder when installing.
