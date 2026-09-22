# take a photo or record a video

when: photo, photos, picture, pictures, selfie, snap, capture, camera, webcam,
video, record, recording, film, shoot, take a photo, take a picture,
record a video, capture a photo, start recording, stop recording

Windows ships a **Camera** application. It is a Store app with no `.exe`, so
it opens with `open_app("camera")` — which goes through
`apps.SHELL_TARGETS` to `microsoft.windows.camera:`. Searching the installed
list for it finds nothing.

Everything below is the SAME application. Photo and video are two buttons in
it, not two programs:

- **A still photo** — open camera, then click **Take photo**.
- **A video** — open camera, click **Take video** to switch to video mode,
  then click it again to start recording. Click it once more to stop.
- The photo/video toggle sits down the right-hand side of the window, and the
  big round button in the middle is the shutter.

Whatever words were used — capture, snap, shoot, record, film, "get a
picture of this" — the application to open is the Camera. Do not go looking
for one called Capture or Record; there is no such thing, and the installed
search will confidently find something unrelated that contains the word. On
this machine a bare "video" matches the **VideoLAN website**.

Pictures and recordings land in `Pictures\Camera Roll`.

Taking a photo turns on a camera that is pointed at whoever is sitting there,
so say what is about to happen before clicking the shutter. Recording is
worth confirming rather than assuming: a recording that nobody knew had
started is the kind of thing this project asks about.
