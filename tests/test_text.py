from app.grading.text import find_span, split_sentences, word_count


def test_split_sentences_keeps_abbreviations_and_offsets():
    text = "Zuerst waren wir am Strand. Danach haben wir z.B. Volleyball gespielt! Es war 3.5 Stunden toll."
    sentences = split_sentences(text)
    assert [s.text for s in sentences] == [
        "Zuerst waren wir am Strand.",
        "Danach haben wir z.B. Volleyball gespielt!",
        "Es war 3.5 Stunden toll.",
    ]
    for sentence in sentences:
        assert text[sentence.start:sentence.end] == sentence.text


def test_word_count_handles_hyphen_apostrophe_numbers_and_placeholders():
    assert word_count("E-Mail geht's 2024 [NAME] [?Wand]") == 5
    assert word_count("Das ist gut.") == 3


def test_find_span_tolerates_whitespace_and_prefers_valid_span():
    sentence = "Wir haben  am   Strand gespielt."
    assert find_span(sentence, "am Strand", None, None) == (11, 22)
    assert find_span(sentence, "am Strand", None, [11, 22]) == (11, 22)
    assert find_span(sentence, "nicht da", None, None) is None
