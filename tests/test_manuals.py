"""An application's own reference, and who is allowed to see it.

Pure: a bundle in, retrieved lines out. No Blender, no network, no disk beyond
a tmp_path.

The reason this exists is measured. On 26 shortcut questions built from
Blender's own keymap, the teaching model scored 17 from memory and 26 with
these lines - and two of the nine it fixed would have damaged the user's work:
"delete a keyframe" came back as X, which deletes the OBJECT, and "mirror
this" as shift+D, which duplicates it.
"""

from __future__ import annotations

import json

import pytest

from meow.knowledge import manuals


def bundle(**overrides):
    """A small Blender-shaped reference, in the generator's output format."""
    base = {
        "application": "blender",
        "executable": "blender.exe",
        "version": "4.1.1",
        "shortcuts": [
            {"context": "Mesh", "keys": "ctrl+B", "operator": "mesh.bevel",
             "properties": {"affect": "EDGES"}},
            {"context": "Mesh", "keys": "ctrl+R",
             "operator": "mesh.loopcut_slide", "properties": {}},
            {"context": "Object Mode", "keys": "alt+I",
             "operator": "anim.keyframe_delete_v3d", "properties": {}},
            {"context": "Object Mode", "keys": "X",
             "operator": "object.delete", "properties": {}},
            {"context": "UV Editor", "keys": "G",
             "operator": "transform.translate", "properties": {}},
        ],
        "operators": [
            {"operator": "mesh.bevel", "label": "Bevel",
             "description": "Cut into selected items at an angle"},
            {"operator": "mesh.loopcut_slide", "label": "Loop Cut and Slide",
             "description": "Cut mesh loop and slide it"},
            {"operator": "anim.keyframe_delete_v3d", "label": "Delete Keyframe",
             "description": "Remove keyframes on current frame"},
            {"operator": "object.delete", "label": "Delete",
             "description": "Delete selected objects"},
            {"operator": "transform.translate", "label": "Move",
             "description": "Move selected items"},
        ],
    }
    base.update(overrides)
    return base


def library_from(tmp_path, *bundles):
    for number, one in enumerate(bundles):
        (tmp_path / f"app{number}.json").write_text(
            json.dumps(one), encoding="utf-8")
    return manuals.load(tmp_path)


def test_a_manual_loads_and_knows_which_window_it_is_for(tmp_path):
    library = library_from(tmp_path, bundle())
    manual = library.for_window("blender.exe")
    assert manual is not None
    assert manual.version == "4.1.1"
    assert len(manual.shortcuts) == 5


def test_the_right_binding_comes_back_first(tmp_path):
    """The whole point. "How do I bevel" has to surface ctrl+B, not whatever
    else happens to contain the word.
    """
    manual = library_from(tmp_path, bundle()).for_window("blender.exe")
    found = manual.about("how do i bevel an edge")
    assert found, "nothing matched"
    assert found[0].keys == "ctrl+B"
    assert found[0].operator == "mesh.bevel"


def test_the_keyframe_case_that_justifies_the_file(tmp_path):
    """Asked this, the model answered X - which deletes the OBJECT. The
    manual has to put the real answer in front of it.
    """
    manual = library_from(tmp_path, bundle()).for_window("blender.exe")
    found = manual.about("how do i delete a keyframe")
    assert found[0].keys == "alt+I"
    assert "keyframe" in found[0].label.lower()


def test_a_shortcut_carries_the_CONTEXT_it_is_correct_in(tmp_path):
    """G is Move in the UV Editor and in the 3D View and in four other
    editors. A line without its context is right by luck.
    """
    manual = library_from(tmp_path, bundle()).for_window("blender.exe")
    line = next(s for s in manual.shortcuts if s.keys == "G")
    assert "UV Editor" in line.describe()
    assert line.properties == {}

    bevel = next(s for s in manual.shortcuts if s.keys == "ctrl+B")
    # Properties are part of the answer: mesh.bevel on edges and on vertices
    # are different instructions wearing one operator.
    assert "affect=EDGES" in bevel.describe()


def test_the_prompt_block_says_where_the_lines_came_from(tmp_path):
    """The model is being asked to prefer these over its own memory, so the
    block has to say why they are better - and warn about the context.
    """
    manual = library_from(tmp_path, bundle()).for_window("blender.exe")
    block = manual.to_prompt("how do i bevel")
    assert "INSTALLED ON THIS MACHINE" in block
    assert "4.1.1" in block
    assert "context" in block.lower()


def test_nothing_comes_back_for_an_unrelated_question(tmp_path):
    """A manual that answers everything is noise in every prompt."""
    manual = library_from(tmp_path, bundle()).for_window("blender.exe")
    assert manual.about("what is the weather in chennai") == []
    assert manual.to_prompt("send an email to arun") == ""


def test_filler_words_alone_never_match(tmp_path):
    """"How do I change the selected object" is all filler and would
    otherwise score against every line in a 2,501 line manual.
    """
    manual = library_from(tmp_path, bundle()).for_window("blender.exe")
    assert manual.about("change the selected object") == []


def test_a_CHROME_TAB_ABOUT_BLENDER_GETS_NOTHING(tmp_path):
    """The dangerous case. Matching on the title would hand Blender's keymap
    to a web page called "Blender tutorial" - and then the cat teaches
    Blender shortcuts to somebody looking at a browser.
    """
    library = library_from(tmp_path, bundle())
    assert library.for_window("chrome.exe",
                              "Blender Tutorial - YouTube") is None
    assert library.to_prompt("how do i bevel", app="chrome.exe",
                             title="Blender donut tutorial") == ""


def test_the_manual_for_a_DIFFERENT_application_is_not_offered(tmp_path):
    photoshop = bundle(application="photoshop", executable="Photoshop.exe",
                       version="2019")
    library = library_from(tmp_path, bundle(), photoshop)
    assert library.for_window("blender.exe").application == "blender"
    assert library.for_window("Photoshop.exe").application == "photoshop"
    assert library.for_window("notepad.exe") is None


def test_a_broken_file_is_reported_not_fatal(tmp_path):
    """Generated by a script running inside another application, across
    versions nobody here controls. A reference that loses one malformed row
    is worth far more than one that refuses to load.
    """
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "empty.json").write_text("{}", encoding="utf-8")
    library = library_from(tmp_path, bundle())
    assert library.for_window("blender.exe") is not None
    assert len(library.skipped) == 2


def test_a_row_missing_its_operator_is_dropped_and_the_rest_survive(tmp_path):
    rough = bundle()
    rough["shortcuts"] = [{"context": "Mesh", "keys": "Q"}] + rough["shortcuts"]
    manual = library_from(tmp_path, rough).for_window("blender.exe")
    assert len(manual.shortcuts) == 5
    assert manual.about("bevel")[0].keys == "ctrl+B"


def test_no_manuals_at_all_is_silence(tmp_path):
    library = manuals.load(tmp_path)
    assert library.manuals == []
    assert library.to_prompt("how do i bevel", app="blender.exe") == ""


@pytest.mark.parametrize("question, expected", [
    ("how do i bevel an edge", "ctrl+B"),
    ("loop cut", "ctrl+R"),
    ("delete a keyframe", "alt+I"),
    ("how do i move this", "G"),
])
def test_the_questions_a_learner_actually_asks(tmp_path, question, expected):
    manual = library_from(tmp_path, bundle()).for_window("blender.exe")
    found = manual.about(question)
    assert found, f"{question!r} matched nothing"
    assert found[0].keys == expected


# --- surviving the way people actually talk ---------------------------------
#
# The first version scored with `word in haystack`, which is SUBSTRING
# matching, and on a 2,501 line manual that is catastrophic rather than
# merely loose. Measured on 23 spoken sentences with the answer known
# (`meow reference`): 8/23 right first before these fixes, 20/23 after.


def test_a_word_is_never_matched_as_a_SUBSTRING(tmp_path):
    """The bug that started this. "bouncing ball" retrieved metaball
    operators, "scale" retrieved wm.context_scale_float, and "edit mode"
    retrieved object.voxel_size_edit.
    """
    rough = bundle()
    rough["shortcuts"].append(
        {"context": "Metaball", "keys": "shift+A",
         "operator": "object.metaball_add", "properties": {}})
    rough["operators"].append(
        {"operator": "object.metaball_add", "label": "Add Metaball",
         "description": "Add an metaball object to the scene"})
    manual = library_from(tmp_path, rough).for_window("blender.exe")
    found = manual.about("teach me how to animate a bouncing ball")
    assert "object.metaball_add" not in [line.operator for line in found]


def test_an_operator_name_is_several_words(tmp_path):
    """`mesh.loopcut_slide` is four words. Splitting only on non-word
    characters leaves "loopcut_slide" as one token nothing matches.
    """
    from meow.knowledge.manuals import _words

    # Stemmed, so "slide" lands on "slid" - both sides of every comparison
    # go through the same stemmer, so that costs nothing.
    assert _words("mesh.loopcut_slide") >= {"loopcut", "slid", "mesh"}


def test_endings_are_trimmed_so_rotating_finds_rotate(tmp_path):
    from meow.knowledge.manuals import _words

    assert _words("rotating") == _words("rotate") == _words("rotates")
    # But not so eagerly that short words are destroyed.
    assert "ask" in _words("ask")


def test_a_WEAK_match_returns_NOTHING(tmp_path):
    """A weak match is worse than no match: the block tells the model these
    lines are authoritative and that its own memory is half right, so a bad
    line argues against the one source that would have been right.
    """
    manual = library_from(tmp_path, bundle()).for_window("blender.exe")
    assert manual.about("i want to make this object metallic") == []
    assert manual.to_prompt("my render looks flat") == ""


def test_a_label_ABOUT_the_request_beats_one_that_merely_contains_it(tmp_path):
    """Asked "how do i move this", "Move" and "Extrude and Move on Normals"
    both hit once, and only one of them is the answer.
    """
    rough = bundle()
    rough["shortcuts"] += [
        {"context": "Object Mode", "keys": "G",
         "operator": "transform.translate", "properties": {}},
        {"context": "Mesh", "keys": "E",
         "operator": "view3d.edit_mesh_extrude_move_normal", "properties": {}},
    ]
    rough["operators"] += [
        {"operator": "view3d.edit_mesh_extrude_move_normal",
         "label": "Extrude and Move on Normals",
         "description": "Extrude region together along the average normal"},
    ]
    manual = library_from(tmp_path, rough).for_window("blender.exe")
    assert manual.about("how do i move this")[0].operator == "transform.translate"


def test_what_people_SAY_is_mapped_to_what_blender_CALLS_it(tmp_path):
    """No amount of scoring turns "scale" into "resize". Blender's label is
    "Resize" and nobody says it.
    """
    rough = bundle()
    rough["shortcuts"].append(
        {"context": "Object Mode", "keys": "S",
         "operator": "transform.resize", "properties": {}})
    rough["operators"].append(
        {"operator": "transform.resize", "label": "Resize",
         "description": "Scale (resize) selected items"})
    manual = library_from(tmp_path, rough).for_window("blender.exe")
    assert manual.about("how do i scale it")[0].operator == "transform.resize"


def test_the_LIKELY_context_wins_a_tie(tmp_path):
    """`object.duplicate_move` and `nla.duplicate_move` share the label
    "Duplicate", and somebody saying "this object" is looking at the
    viewport, not the NLA editor.
    """
    rough = bundle()
    rough["shortcuts"] += [
        {"context": "Object Mode", "keys": "shift+D",
         "operator": "object.duplicate_move", "properties": {}},
        {"context": "NLA Editor", "keys": "shift+D",
         "operator": "nla.duplicate_move", "properties": {}},
    ]
    for operator in ("object.duplicate_move", "nla.duplicate_move"):
        rough["operators"].append(
            {"operator": operator, "label": "Duplicate",
             "description": "Duplicate selected strips and move them"})
    manual = library_from(tmp_path, rough).for_window("blender.exe")
    first = manual.about("how do i duplicate this")[0]
    assert first.operator == "object.duplicate_move", first.context


def test_the_LIVE_context_beats_the_standing_preference(tmp_path):
    """When something can read the mode from the application itself, that
    outranks a guess about where people usually are. This is the one real
    job the live read has.
    """
    rough = bundle()
    rough["shortcuts"] += [
        {"context": "Object Mode", "keys": "shift+D",
         "operator": "object.duplicate_move", "properties": {}},
        {"context": "NLA Editor", "keys": "shift+D",
         "operator": "nla.duplicate_move", "properties": {}},
    ]
    for operator in ("object.duplicate_move", "nla.duplicate_move"):
        rough["operators"].append(
            {"operator": operator, "label": "Duplicate",
             "description": "Duplicate selected strips and move them"})
    manual = library_from(tmp_path, rough).for_window("blender.exe")
    first = manual.about("how do i duplicate this", context="NLA Editor")[0]
    assert first.context == "NLA Editor"


def test_an_unlikely_context_is_still_REACHABLE(tmp_path):
    """Not a filter. A question about the Sequencer is real; its lines just
    do not win a tie against the viewport.
    """
    rough = bundle()
    rough["shortcuts"].append(
        {"context": "Sequencer", "keys": "K",
         "operator": "sequencer.split", "properties": {}})
    rough["operators"].append(
        {"operator": "sequencer.split", "label": "Split Strips",
         "description": "Split the selected strips in two"})
    manual = library_from(tmp_path, rough).for_window("blender.exe")
    found = [line.operator for line in manual.about("how do i split a strip")]
    assert "sequencer.split" in found
