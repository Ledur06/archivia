from pdf_excel_converter.pdf_extract import detect_tables, extract_rows_from_text, parse_page_selection, split_table_like_line


def test_split_pipe_table_line():
    assert split_table_like_line("Nom | Prenom | Montant") == ["Nom", "Prenom", "Montant"]


def test_split_semicolon_table_line():
    assert split_table_like_line("A; B; C") == ["A", "B", "C"]


def test_extract_text_and_table_rows():
    rows = extract_rows_from_text("Titre document\nNom | Montant\nAlice | 1000")
    assert rows[0].kind == "text"
    assert rows[1].kind == "table"
    assert rows[1].values == ["Nom", "Montant"]


def test_detect_table_from_consecutive_table_rows():
    rows = extract_rows_from_text("Nom | Montant\nAlice | 1000\nBob | 2000")
    tables = detect_tables(rows, page_number=1)
    assert len(tables) == 1
    assert tables[0].row_count == 3
    assert tables[0].column_count == 2


def test_parse_page_selection():
    assert parse_page_selection("1,3,5-7", total_pages=10) == [1, 3, 5, 6, 7]
