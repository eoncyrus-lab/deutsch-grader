from app.grading.minimal_edit import check_minimal_edit


def test_valid_minimal_edits():
    assert check_minimal_edit("mit meine Mutter", "mit meiner Mutter").ok
    assert check_minimal_edit("am den Strand", "am Strand").ok
    assert check_minimal_edit("habe", "bin").ok
    assert check_minimal_edit("Wir haben am Strand gespielt.", "Am Strand haben wir gespielt.").ok


def test_semantic_replacement_is_rejected():
    assert not check_minimal_edit("Wand", "Strand").ok
    assert not check_minimal_edit("gegangen", "gefahren").ok


def test_inserted_style_word_is_rejected():
    verdict = check_minimal_edit("Das war gut.", "Das war sehr gut.")
    assert not verdict.ok
    assert "新增实词" in verdict.reason


def test_multiple_alternatives_are_rejected():
    assert not check_minimal_edit("am Strand", "am Strand / am Meer").ok
