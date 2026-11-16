from app.config.loader import load_task_config
from app.grading.scoring import round_to_step, score_submission


CONFIG = load_task_config("configs/tasks/narrative-travel-a2.yaml")


def checklist(**overrides):
    values = {
        "on_topic": True, "time": True, "place": True, "companions": True,
        "activities": [{"text": "a", "sid": 1}, {"text": "b", "sid": 2}],
        "concrete_detail": True, "feeling_or_reason": True,
        "sequence_markers": ["Zuerst", "Danach"], "chronological": True,
        "ending_states_best": True, "ending_has_reason": True,
        "ending_repeats_opening": False, "contradiction": False,
    }
    values.update(overrides)
    return {key: {"value": value, "valid": 1} for key, value in values.items()}


def flags(comprehensible=True, count=4):
    return [{"sid": i, "comprehensible": comprehensible} for i in range(1, count + 1)]


def error(eid, code, etype, pattern, sid=1, certainty="确定"):
    return {
        "id": eid, "sid": sid, "type": etype, "error_code": code, "pattern": pattern,
        "certainty": certainty, "effective": 1,
    }


def test_no_errors_scores_ten():
    result = score_submission(CONFIG, [], checklist(), flags(), 80, True)
    assert result.language == 5.0
    assert result.content == 3.0
    assert result.structure == 2.0
    assert result.total == 10.0


def test_two_distinct_errors_score_nine_point_five():
    errors = [
        error("e1", "AUX_MOVE", "助动词", "aux:sein+fahren"),
        error("e2", "CASE_PREP", "格", "prep:mit+Dat", sid=2),
    ]
    result = score_submission(CONFIG, errors, checklist(), flags(), 80, True)
    assert result.points == 2.0
    assert result.language == 4.5
    assert result.total == 9.5


def test_length_cap_then_title_deduction():
    values = checklist(
        on_topic=True, time=False, place=False, companions=False,
        activities=[], concrete_detail=False, feeling_or_reason=False,
        sequence_markers=[], chronological=False, ending_states_best=False,
        ending_has_reason=False, contradiction=True,
    )
    result = score_submission(CONFIG, [], values, flags(), 25, False)
    assert result.content == 1.0
    assert result.structure == 0.0
    assert result.language == 5.0
    assert result.cap_applied == 4.0
    assert result.title_deducted == 0.5
    assert result.total == 3.5


def test_repeat_weight_and_pattern_cap():
    errors = [error(f"e{i}", "CASE_PREP", "格", f"p{i}", sid=i) for i in range(1, 6)]
    result = score_submission(CONFIG, errors, checklist(), flags(), 80, True)
    assert result.points == 2.0
    assert result.language == 4.5


def test_verb_structure_caps_at_four_points():
    errors = [error(f"e{i}", "MISSING_VERB", "动词结构缺失", f"m{i}", sid=i) for i in range(1, 7)]
    result = score_submission(CONFIG, errors, checklist(), flags(), 80, True)
    assert result.points == 4.0
    assert result.language == 4.0


def test_comprehension_ceiling_and_floor():
    low = score_submission(CONFIG, [], checklist(), [{"sid": 1, "comprehensible": True}, {"sid": 2, "comprehensible": False}, {"sid": 3, "comprehensible": False}, {"sid": 4, "comprehensible": False}], 80, True)
    assert low.language == 1.0
    half = score_submission(CONFIG, [], checklist(), [{"sid": 1, "comprehensible": True}, {"sid": 2, "comprehensible": False}], 80, True)
    assert half.language == 5.0
    many = [
        error(f"e{i}", code, etype, f"p{i}", sid=i)
        for i, (code, etype) in enumerate([
            ("AUX_MOVE", "助动词"), ("CASE_PREP", "格"), ("V_CONJ", "变位"),
            ("ADJ_END", "词尾"), ("WORD_ORDER", "语序"), ("SPELL", "拼写"),
            ("WORD_CHOICE", "选词"), ("PUNCT", "标点"), ("TENSE", "时态"),
        ] * 5)
    ]
    floored = score_submission(CONFIG, many, checklist(), [{"sid": 1, "comprehensible": True}, {"sid": 2, "comprehensible": False}], 80, True)
    assert floored.language == 2.0
    none = score_submission(CONFIG, [], checklist(), [{"sid": 1, "comprehensible": False}], 80, True)
    assert none.language == 0.0


def test_content_missing_counts():
    result = score_submission(CONFIG, [], checklist(time=False), flags(), 80, True)
    assert result.content == 2.0
    result = score_submission(CONFIG, [], checklist(time=False, place=False), flags(), 80, True)
    assert result.content == 1.5
    result = score_submission(CONFIG, [], checklist(time=False, place=False, companions=False), flags(), 80, True)
    assert result.content == 1.0
    result = score_submission(CONFIG, [], checklist(on_topic=False), flags(), 80, True)
    assert result.content == 0.0


def test_structure_contradiction_is_zero():
    result = score_submission(CONFIG, [], checklist(contradiction=True), flags(), 80, True)
    assert result.structure == 0.0


def test_rounding_is_half_up_once():
    assert round_to_step(8.25, 0.5) == 8.5
    assert round_to_step(8.24, 0.5) == 8.0
    assert round_to_step(9.75, 0.5) == 10.0
