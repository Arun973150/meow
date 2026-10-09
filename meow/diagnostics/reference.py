"""Does the manual's lookup survive the way people actually talk?

    meow reference

The teaching measurement in `meow/diagnostics/teaching.py` phrases every
question in the manual's OWN vocabulary - it is built from the operator
labels, so "Bevel" is asked as "Bevel". That is the best possible case for
word overlap and it is not how anybody speaks. It reported 46% -> 24/24 and
was measuring retrieval against its own index.

So this is the honest half: sentences a person says out loud, each with the
operator that answers it, written by hand because a person's words are
exactly what cannot be generated from the data.

Scored two ways, because they fail differently:

    found    is the right line anywhere in what gets injected?
    first    is it the FIRST line? A model told to prefer six lines over its
             own memory weighs the top one most, and the prompt says so.

⚠ **A WEAK MATCH IS WORSE THAN NO MATCH, which is why `missed` is reported
separately from `wrong`.** The block tells the model these lines are
authoritative and that its own memory is right about half the time. Handing
it lines about metaballs when it asked about a bouncing ball does not merely
waste tokens - it argues against the one source that would have been right.
"""

from __future__ import annotations

# What somebody says, and the operator that answers it. Hand-written: the
# point is the gap between a person's words and the manual's, so generating
# these from the manual would measure nothing.
#
# `None` means the manual genuinely has no answer - those are here to check
# that the lookup SAYS NOTHING rather than offering its best bad guess.
SPOKEN = [
    ("how do i bevel this edge", "mesh.bevel"),
    ("show me how to bevel this edge", "mesh.bevel"),
    ("how do i add a loop cut", "mesh.loopcut_slide"),
    ("how do i set a keyframe", "anim.keyframe_insert"),
    ("how do i delete a keyframe", "anim.keyframe_delete_v3d"),
    ("how do i duplicate this object", "object.duplicate_move"),
    ("how do i join these two objects", "object.join"),
    ("how do i parent this to that", "object.parent_set"),
    ("how do i move this", "transform.translate"),
    ("how do i scale it", "transform.resize"),
    ("how do i rotate it", "transform.rotate"),
    ("how do i render the scene", "render.render"),
    # Corrected: mesh.extrude_region_move is not BOUND to anything. The
    # keyed operator is the view3d wrapper, which is what E runs.
    ("how do i extrude this face", "view3d.edit_mesh_extrude_move_normal"),
    ("how do i inset a face", "mesh.inset"),
    # Corrected: object.transform_mirror is not bound; transform.mirror is,
    # on Ctrl+M, and the first run "got instead: transform.mirror" was right.
    ("how do i mirror this object", "transform.mirror"),
    ("how do i undo that", "ed.undo"),
    ("how do i save my file", "wm.save_mainfile"),
    # Corrected: object.editmode_toggle is not bound. TAB runs
    # object.mode_set, labelled "Set Object Mode".
    ("how do i go into edit mode", "object.mode_set"),
    ("how do i select everything", "object.select_all"),
    ("how do i hide this object", "object.hide_view_set"),
    ("teach me how to animate a bouncing ball", "anim.keyframe_insert"),
    ("how do i clear the scale on this", "object.scale_clear"),
    # Corrected: "my object disappeared" is usually H, and alt+H brings it
    # back - "Hide Objects" is a GOOD answer and was mislabelled as noise.
    ("my object disappeared", "object.hide_view_set"),
    # No shortcut answers these, so the lookup has to stay quiet. Materials
    # are panels and sliders, and "flat" is a lighting judgement.
    ("i want to make this object metallic", None),
    ("my render looks flat", None),
]


def main() -> int:
    from ..knowledge import manuals

    library = manuals.load()
    manual = library.for_window("blender.exe")
    if manual is None:
        print()
        print("  No Blender reference yet. Build it with:")
        print("    python scripts/blender_reference.py")
        print()
        return 1

    print(f"\n  reference - {manual.application} {manual.version}, "
          f"{len(manual.shortcuts):,} shortcuts, "
          f"{len(SPOKEN)} spoken sentences\n")

    answerable = [(said, want) for said, want in SPOKEN if want]
    unanswerable = [said for said, want in SPOKEN if want is None]

    found = first = 0
    for said, wanted in answerable:
        lines = manual.about(said)
        operators = [line.operator for line in lines]
        where = operators.index(wanted) + 1 if wanted in operators else 0
        found += 1 if where else 0
        first += 1 if where == 1 else 0
        mark = {0: "MISS", 1: "  1st"}.get(where, f"  {where}th")
        print(f"  {mark:<6} {said[:44]:<46} want {wanted}")
        if not where and operators:
            print(f"         got instead: {operators[0]}")

    print()
    quiet = 0
    for said in unanswerable:
        lines = manual.about(said)
        quiet += 1 if not lines else 0
        mark = "quiet" if not lines else "NOISE"
        print(f"  {mark:<6} {said[:44]:<46} "
              + ("" if not lines else f"offered {lines[0].operator}"))

    total = len(answerable)
    print(f"\n  found anywhere   {found:>3}/{total}  ({found / total:.0%})")
    print(f"  found FIRST      {first:>3}/{total}  ({first / total:.0%})")
    print(f"  silent when it should be  {quiet:>3}/{len(unanswerable)}")
    print()
    return 0
