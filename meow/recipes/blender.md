# work in Blender

app: blender, blender.exe
when: blender, modifier, subdivision, subdivide, bevel, mesh, viewport,
vertex, vertices, edge mode, face mode, extrude, loop cut, orbit, pan,
shading, render, keyframe, timeline, object mode, edit mode, outliner,
properties panel, n panel, t panel, gizmo

**Blender's interface is INVISIBLE to the accessibility tree.** It draws
everything in OpenGL, so the control list has five entries - Minimize,
Maximize, Close, System, System - and nothing else. An empty control list
here does not mean an empty window. Never say you cannot see anything, and
never use point_at_control: use look_at_screen to read it, and show_on_screen
to mark things, which finds them by sight.

**Most of Blender is keyboard, and that is the fastest honest answer.** The
shortcut is usually the real route and the menu is the long way round:

- Tab toggles Edit Mode and Object Mode, with the pointer over the viewport
- 1, 2, 3 in Edit Mode are vertex, edge and face select
- G moves, R rotates, S scales. Then X, Y or Z constrains to an axis, and a
  typed number is an exact amount. Left click or Enter confirms, Escape or
  right click cancels
- E extrudes, I insets, Ctrl+R is a loop cut, Ctrl+B bevels
- Shift+A is the Add menu, X is delete
- Middle mouse orbits, Shift+middle pans, scroll zooms. Numpad period frames
  the selection, and Home frames everything
- Ctrl+Z undoes, and Blender's undo stack is deep

**The panels, and what they are called.** The right-hand column is the
Properties editor, and its tabs run down the left edge of it as small icons.
The one that looks like a blue spanner is Modifier Properties - that is where
Add Modifier lives, and it is where Subdivision Surface, Array, Mirror,
Solidify and Boolean are added. The orange square tab is Object Properties.
The green triangle is Object Data. The checkered sphere is Material.

The Outliner is the tree at the top right listing every object by name. The N
panel is the sidebar inside the viewport, toggled with N, holding the
selected object's exact location, rotation and scale.

**Say the shortcut and mark the panel.** Somebody learning Blender needs
both: "press Tab" on its own is the right answer and does not tell them where
Edit Mode is, and a circle round the spanner tab does not tell them that G
moves things. Give the key, then show_on_screen with the panel or button, and
say what it is.

**Nothing in Blender is confirmed before it happens.** Transforms apply live
and are confirmed by a click, so a half-finished G is a model that has moved.
If a step did not land, say so rather than carrying on - and Escape, not
undo, is how a transform in progress is abandoned.
