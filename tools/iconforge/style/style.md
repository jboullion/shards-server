# Iconforge style

The look we want: Meridian 59's original 1996 item sprites. They are small (about 60–120 px),
painted rather than rendered, with a limited, slightly muddy palette, strong dark outlines in
places, hard edges against the background and simple top-left light. Photographic textures,
smooth 3D shading and glossy highlights are what make the fan art stand out, so they go.

The block between the markers is appended to every prompt. Edit it and the next batch uses it
(cached results are keyed on the prompt, so a change re-runs those icons).

<!-- style-block -->
Style: a classic 1990s fantasy RPG inventory item sprite, like the reference images (the original
Meridian 59 item art). Hand-painted low-resolution pixel art: chunky readable shapes, a limited
earthy palette with few shades per colour, flat cel-like shading lit from the top left, crisp hard
edges and a thin dark outline where the object meets the background. No photographic texture, no
smooth 3D render shading, no glossy specular highlights, no gradients, no drop shadow, no text.
Show the whole object, centred, filling most of the frame, at the same angle as the first image.
Background: a single flat solid magenta (#FF00FF) everywhere around the object, with no shadow or
floor.
<!-- /style-block -->

## Style references

Classic client icons sent as style references with each request (providers that accept several
images: openai, fal). Names are .bgf files in the classic client's resource folder; their
inventory frame is used. Pick ones that span materials (metal, food, cloth, glass, wood).

<!-- refs -->
hammer.bgf
meatpie.bgf
potion01.bgf
drumstik.bgf
helm.bgf
mug.bgf
<!-- /refs -->
