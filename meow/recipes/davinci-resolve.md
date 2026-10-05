# work in DaVinci Resolve

app: resolve, davinci, resolve.exe
when: resolve, davinci, timeline, clip, keyframe, inspector, colour grade,
color grade, grading, node, razor, blade, trim, fusion, fairlight, deliver,
render queue, playhead, power window, qualifier, lut

**Resolve's pages are the first question, always.** The seven page buttons
are along the BOTTOM of the window - Media, Cut, Edit, Fusion, Color,
Fairlight, Deliver - and almost every answer starts with being on the right
one. Somebody asking about grading who is on the Edit page needs the Color
page before anything else makes sense, so check which page is showing with
look_at_screen before answering, and mark the page button rather than
describing where it is.

**Where the common things live.**

- The Inspector is top right on the Edit page, and it is where a clip's
  transform, crop and composite settings are. The diamond beside any property
  creates a keyframe for it
- The Color page is nodes top right, wheels along the bottom, scopes top
  left. A new serial node is Alt+S, a parallel one Alt+P, a layer node Alt+L
- Power windows and the qualifier are tabs in the centre-left panel on the
  Color page
- Deliver is where a render is set up: pick a preset at the top left, set the
  filename and location, Add to Render Queue, then Render All

**Shortcuts that are the real answer.**

- B is the blade, A is the selection arrow, T trims
- Ctrl+B cuts the clip at the playhead on the Edit page
- Comma and full stop nudge by a frame, shift with them nudges by a second
- Alt+drag on a clip's handle does a rolling trim
- Ctrl+Shift+D disables a clip without deleting it

**Resolve has a project database, and nothing is saved by closing.** A
project lives in a library rather than in a file somebody can see, so "where
did it go" is usually the Project Manager rather than a lost file. Ctrl+S
saves, and Live Save in preferences does it continuously - worth saying when
somebody is about to do something large.
