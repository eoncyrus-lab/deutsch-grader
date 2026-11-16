import pytest

from app.config.loader import load_task_config
from app.grading.validation import SchemaError, validate_s2, validate_s2b, validate_s3, validate_s5


CONFIG = load_task_config("configs/tasks/narrative-travel-a2.yaml")
SENTENCES = [
    {"sid": 1, "text": "Ich habe mit meiner Familie gefahren."},
    {"sid": 2, "text": "Wir haben am den Strand gespielt."},
]


def test_s2_valid_error_gets_absolute_span():
    data = {"errors": [{"sid": 2, "original": "am den Strand", "corrected": "am Strand", "type": "格", "code": "CASE_PREP", "pattern": "prep:an+Dat", "certainty": "确定", "rule": "an + Dat"}]}
    result = validate_s2(data, SENTENCES, CONFIG)
    assert len(result.errors) == 1
    assert result.errors[0]["span_start"] == 10
    assert result.errors[0]["certainty"] == "确定"


def test_s2_invalid_span_is_dropped():
    data = {"errors": [{"sid": 2, "original": "nicht da", "corrected": "x", "type": "格", "code": "CASE_PREP", "rule": "x"}]}
    result = validate_s2(data, SENTENCES, CONFIG)
    assert result.errors == []
    assert result.dropped


def test_s2_semantic_replacement_is_downgraded():
    data = {"errors": [{"sid": 2, "original": "Strand", "corrected": "Meer", "type": "选词", "code": "WORD_CHOICE", "rule": "x", "certainty": "确定"}]}
    result = validate_s2(data, SENTENCES, CONFIG)
    assert result.errors[0]["certainty"] == "建议核对"
    assert result.errors[0]["guard_status"] == "violation"


def test_s2b_requires_all_verdicts():
    with pytest.raises(SchemaError):
        validate_s2b({"verdicts": []}, ["e1"])


def test_s3_invalid_evidence_becomes_unknown_not_false():
    data = {
        "fields": {
            "on_topic": {"value": True, "evidence": "Meine Reise"},
            "time": {"value": True, "evidence": "im Sommer"},
            "place": {"value": True, "evidence": "Qingdao"},
            "companions": {"value": True, "evidence": "mit meiner Familie"},
            "activities": {"value": [{"text": "am Strand gespielt", "sid": 2}], "evidence": "x"},
            "concrete_detail": {"value": False, "evidence": ""},
            "feeling_or_reason": {"value": False, "evidence": ""},
            "sequence_markers": {"value": [], "evidence": []},
            "chronological": {"value": False, "evidence": ""},
            "ending_states_best": {"value": False, "evidence": ""},
            "ending_has_reason": {"value": False, "evidence": ""},
            "ending_repeats_opening": {"value": False, "evidence": ""},
            "contradiction": {"value": False, "evidence": ""},
        },
        "sentences": [{"sid": 1, "comprehensible": True}, {"sid": 2, "comprehensible": True}],
    }
    checklist, flags, invalid = validate_s3(data, CONFIG, SENTENCES, "Meine Reise")
    assert checklist["activities"]["value"] == []
    assert checklist["activities"]["valid"] == 0
    assert "activities" in invalid
    assert checklist["on_topic"]["value"] is True


def test_s5_rejects_bad_quote_and_long_german():
    data = {
        "content_comment": "「nicht im Text」写得好。",
        "structure_comment": "结构清楚。",
        "language_summary": "先改助动词。",
        "encouragement_de": "eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn",
    }
    _, issues = validate_s5(data, "Ich habe mit meiner Familie gefahren.")
    assert any("引文" in issue for issue in issues)
    assert any("12 词" in issue for issue in issues)
