"""An example PlanetPDF plugin: counts the open document's pages and words through the local API
and prints them (PlanetPDF keeps a plugin's output in its log). Copy planetpdf_client.py beside it."""

import os
import tempfile

from planetpdf_client import PlanetPDF

with PlanetPDF.from_environment() as pdf:
    document = os.environ["PLANETPDF_DOCUMENT"]
    info = pdf.call("info", file=document)
    text = pdf.call("text", file=document)["text"]
    report = os.path.join(tempfile.gettempdir(), "page-count.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write(f"{os.path.basename(document)}: {info['pages']} pages, {len(text.split())} words\n")
    print(open(report, encoding="utf-8").read())
