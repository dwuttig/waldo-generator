"""Prompt templates. Kept in one place so they are easy to tune."""

from __future__ import annotations


def character(style: str, outfit: str, variation: int, n_photos: int) -> str:
    photos = "the photo" if n_photos == 1 else f"the {n_photos} photos (all the same person)"
    outfit_line = f"Outfit: {outfit}." if outfit.strip() else "Outfit: casual everyday clothes in distinctive colours."
    return (
        f"Create a cartoon character of the person in {photos}. "
        f"Art style: {style} "
        "Keep the person clearly recognisable: same face shape, hairstyle, hair colour, "
        "facial hair, glasses, skin tone, age and body proportions. "
        f"{outfit_line} "
        "Full body, standing, front three-quarter view, single character centred on a plain "
        "white background, no text, no frame. "
        f"Character design variation #{variation}: vary pose and small accessories slightly."
    )


def scene(style: str, description: str) -> str:
    return (
        f"A very large, extremely crowded hidden-object picture: {description}. "
        f"Art style: {style} "
        "Elevated three-quarter bird's-eye view so the whole place is visible. "
        "Hundreds of small, varied people doing many different funny little activities, "
        "packed edge to edge, lots of tiny detail everywhere, no empty areas, "
        "consistent small character scale, no text, no letters, no logos, no frame."
    )


def place(style: str, name: str) -> str:
    return (
        "This image is a small section of a large crowded illustration. "
        "Add exactly one more person to it: the cartoon character shown in the other image(s) "
        "(if there are several, they all show the same character from different angles). "
        "Place them near the centre of this section, at the same small size as the surrounding "
        "people, naturally doing something that fits the scene, partly blending into the crowd. "
        "Keep their face, hair and outfit consistent with the reference. "
        f"Match the existing art style exactly ({style}). "
        "Do not change anything else: keep every other person, object, colour and the edges of "
        "the image exactly as they are. Same framing, no zoom, no text."
    )


def detail(style: str, names: list[str]) -> str:
    keep = ""
    if names:
        keep = (" The reference images show characters that appear in this section; "
                "keep them recognisable and in the same place.")
    return (
        "Redraw this section of a crowded illustration at higher resolution with crisp, clean "
        "fine detail: sharp ink lines, clear faces, tidy small objects. "
        "Keep the exact same composition, layout, people, poses, colours and framing; "
        f"do not add, remove or move anything. Art style: {style}{keep}"
    )
