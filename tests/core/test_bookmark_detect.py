import pymupdf

from planetpdf.core import bookmark_detect as B

W, H = 612, 792
BODY = "This is ordinary body text that runs along the line."


def _body(page, y):
    for n in range(4):
        page.insert_text((72, y + n * 14), BODY, fontsize=10)


def _toc_document(links: bool) -> pymupdf.Document:
    doc = pymupdf.open()
    toc = doc.new_page(width=W, height=H)
    toc.insert_text((72, 80), "Table of Contents", fontsize=16, fontname="hebo")
    entries = [("1 Introduction", 3, 72), ("1.1 Purpose", 3, 90), ("2 Installation", 4, 72),
               ("2.1 Requirements", 4, 90), ("3 Appendix", 5, 72)]
    for n, (title, printed, x) in enumerate(entries):
        y = 120 + n * 20
        toc.insert_text((x, y), title + " " + "." * 40, fontsize=10)
        toc.insert_text((500, y), str(printed), fontsize=10)
    # the printed numbers start at the cover (page "1" = the cover, so page 3 = index 2)
    doc.new_page(width=W, height=H)  # (a blank page between)
    pages = {}
    for title, printed, _x in entries:
        index = printed - 1
        while doc.page_count <= index:
            doc.new_page(width=W, height=H)
        page = doc[index]
        y = 100 + 160 * sum(1 for t, p, _ in entries if p == printed and t < title)
        page.insert_text((72, y), title, fontsize=14, fontname="hebo")
        _body(page, y + 24)
        pages[title] = (index, y)
    if links:
        toc = doc[0]  # (adding pages invalidates Page objects)
        for n, (title, _printed, _x) in enumerate(entries):
            index, y = pages[title]
            toc.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 110 + n * 20, 520, 124 + n * 20),
                             "page": index, "to": pymupdf.Point(0, y - 14)})
    return doc


def test_table_of_contents_found_by_title():
    doc = _toc_document(links=False)
    sections = B.find_sections(doc)
    assert [(s.level, s.title, s.page) for s in sections] == [
        (1, "1 Introduction", 2), (2, "1.1 Purpose", 2), (1, "2 Installation", 3),
        (2, "2.1 Requirements", 3), (1, "3 Appendix", 4)]
    assert all(s.found == "toc" for s in sections)
    intro = sections[0]
    assert doc[2].search_for("1 Introduction")[0].y0 - intro.y < 10


def test_table_of_contents_links_win():
    doc = _toc_document(links=True)
    sections = B.find_sections(doc)
    assert [s.page for s in sections] == [2, 2, 3, 3, 4]


def test_headings_without_contents():
    doc = pymupdf.open()
    for chapter in range(1, 4):
        page = doc.new_page(width=W, height=H)
        page.insert_text((72, 60), "Company Handbook", fontsize=9)  # (running head)
        page.insert_text((72, 100), f"Chapter {chapter}: Topic {chapter}", fontsize=18, fontname="hebo")
        _body(page, 130)
        page.insert_text((72, 220), f"{chapter}.1 Details", fontsize=12, fontname="hebo")
        _body(page, 245)
    sections = B.find_sections(doc)
    assert [(s.level, s.title, s.page) for s in sections] == [
        (1, "Chapter 1: Topic 1", 0), (2, "1.1 Details", 0),
        (1, "Chapter 2: Topic 2", 1), (2, "2.1 Details", 1),
        (1, "Chapter 3: Topic 3", 2), (2, "3.1 Details", 2)]


def test_converted_word_file_gets_contents_bookmarks(tmp_path):
    from planetpdf.core.batch import add_contents_bookmarks

    path = tmp_path / "converted.pdf"
    _toc_document(links=True).save(path)
    assert add_contents_bookmarks(path) == 5
    with pymupdf.open(path) as doc:
        assert [e[:3] for e in doc.get_toc()] == [[1, "1 Introduction", 3], [2, "1.1 Purpose", 3],
                                                  [1, "2 Installation", 4], [2, "2.1 Requirements", 4],
                                                  [1, "3 Appendix", 5]]
    assert add_contents_bookmarks(path) == 0  # (Word already made bookmarks: left alone)


def test_plain_text_has_no_sections():
    doc = pymupdf.open()
    for _ in range(2):
        _body(doc.new_page(width=W, height=H), 100)
    assert B.find_sections(doc) == []
