# work in Photoshop

app: photoshop, photoshop.exe
when: photoshop, layer, layers, mask, layer mask, clipping mask, blend mode,
opacity, lasso, marquee, magic wand, healing brush, clone stamp, dodge, burn,
adjustment layer, curves, levels, hue saturation, smart object, artboard,
feather, refine edge, canvas size, image size

**Photoshop's tree is half blind.** The accessibility tree sees some of the
panels and almost none of the toolbar - measured, three of ten targets - and
the icon column down the left is the part it cannot see at all. Use
show_on_screen for anything in the toolbar, which finds it by sight, and
point_at_control only for a named panel or menu that is actually in the
control list.

**Say the modifier, because the modifier is the whole answer.** Most of
Photoshop's real operations are a modifier on something ordinary:

- Alt+click between two layers clips the upper one to the lower
- Alt+click a mask thumbnail shows the mask itself on the canvas
- Ctrl+click a layer thumbnail selects that layer's pixels
- Holding Alt while dragging a layer duplicates it
- Shift constrains a transform; Alt transforms about the centre
- Ctrl+Alt+Shift+E stamps every visible layer into a new one

**The panels, and what they are called.** Layers, Channels and Paths are
usually one panel group at the bottom right. Adjustments and Properties sit
above them. The Options bar is the strip under the menu bar and it changes
completely with the selected tool - which is why an instruction about a tool
has to say which tool is selected first.

**A mask is black and white, not an eraser.** Painting black on a layer mask
hides, white reveals, grey is partial, and nothing is destroyed. When
somebody asks how to erase part of a layer, the answer is usually a mask -
say so, briefly, rather than teaching the eraser.

**Non-destructive first.** An adjustment layer over a pixel layer, a smart
object before a filter, a mask before a delete. If somebody is about to do
something irreversible to their only copy, say it in one sentence and then
tell them how to do what they asked.
