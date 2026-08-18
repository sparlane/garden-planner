"""
Garden layout geometry

Where :mod:`garden.geometry` says what one grid step measures, this module says
where a piece of geometry may sit. An area, bed, row, and square are integer
rectangles on their parent's grid, and two rules hold for all of them: a child
lies wholly inside its parent, and it does not overlap a sibling of its own
kind.

Rows and squares are deliberately not compared with each other. A bed divided
into rows and also marked out in squares describes one piece of ground in two
useful ways, and refusing that would make the square-foot and row templates
mutually exclusive.
"""

from typing import NamedTuple

from django.core.exceptions import NON_FIELD_ERRORS


class Rect(NamedTuple):
    """An integer rectangle on a parent's grid, anchored at its lower left."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self):
        """The first column past the rectangle's right edge."""
        return self.x + self.width

    @property
    def top(self):
        """The first row past the rectangle's top edge."""
        return self.y + self.height


def rect_of(geometry):
    """Return the placed rectangle a bed, row, or square occupies."""
    return Rect(
        geometry.placement_x,
        geometry.placement_y,
        geometry.size_x,
        geometry.size_y,
    )


def extent_of(parent):
    """Return the parent's own grid, which every child is placed against."""
    return Rect(0, 0, parent.size_x, parent.size_y)


def overlaps(first, second):
    """Report whether two rectangles share at least one grid cell."""
    return first.x < second.right and second.x < first.right and first.y < second.top and second.y < first.top


def containment_errors(rect, extent, child_label, parent_label):
    """Describe each axis on which a child runs past its parent's edge."""
    errors = {}
    if rect.right > extent.width:
        errors['placement_x'] = (
            f'{child_label} reaches {rect.right} across, but {parent_label} '
            f'is only {extent.width} wide.'
        )
    if rect.top > extent.height:
        errors['placement_y'] = (
            f'{child_label} reaches {rect.top} up, but {parent_label} is only '
            f'{extent.height} tall.'
        )
    return errors


def overlap_message(rect, sibling_label):
    """Describe the sibling a placement collides with, and where it sits."""
    return (
        f'This overlaps {sibling_label}, which occupies {rect.x} to '
        f'{rect.right} across and {rect.y} to {rect.top} up.'
    )


def _is_measured(geometry):
    """Report whether a rectangle's four integers are all present."""
    return None not in (
        geometry.placement_x,
        geometry.placement_y,
        geometry.size_x,
        geometry.size_y,
    )


def placement_errors(child, parent, siblings, child_label, parent_label):
    """Report why a child cannot sit where it is placed, if it cannot.

    Field errors are returned per axis so a form can point at the number the
    user typed. An overlap is reported against the whole placement, because no
    single field is the wrong one when two rectangles collide.

    A child whose own integers are missing is left alone: field validation has
    already rejected it and a second complaint would only obscure the first.
    """
    if parent is None or not _is_measured(child):
        return {}
    if parent.size_x is None or parent.size_y is None:
        return {}
    rect = rect_of(child)
    errors = containment_errors(rect, extent_of(parent), child_label, parent_label)
    if errors:
        return errors
    for sibling in siblings:
        sibling_rect = rect_of(sibling)
        if overlaps(rect, sibling_rect):
            return {NON_FIELD_ERRORS: overlap_message(sibling_rect, f'"{sibling.name}"')}
    return {}
