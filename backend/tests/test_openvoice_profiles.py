from app.voice.openvoice_profiles import (
    OPENVOICE_CHILD_PROFILE_IDS,
    get_openvoice_profile,
    list_openvoice_profile_ids,
    openvoice_profile_for_character_id,
)


def test_openvoice_profile_registry_stays_fixed() -> None:
    profile_ids = list_openvoice_profile_ids()

    assert len(profile_ids) == 12
    assert len(OPENVOICE_CHILD_PROFILE_IDS) == 10
    assert "ov:teacher_li" in profile_ids
    assert "ov:thinker_elder" in profile_ids


def test_openvoice_character_bindings_remain_stable() -> None:
    assert openvoice_profile_for_character_id("moderator") == "ov:teacher_li"
    assert openvoice_profile_for_character_id("skeptic") == "ov:student_xiaoyi"
    assert openvoice_profile_for_character_id("innovator") == "ov:student_xiaoxiang"
    assert openvoice_profile_for_character_id("pragmatist") == "ov:student_xiaoxing"


def test_openvoice_teacher_reference_stays_on_preferred_female_sample() -> None:
    profile = get_openvoice_profile("ov:teacher_li")

    assert profile is not None
    assert profile.reference_audio.name == "student_xiaoxiang.wav"