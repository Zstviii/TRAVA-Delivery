import math
from collections.abc import Mapping, Sequence


def validate_polygon(geometry: object) -> dict[str, object]:
    if not isinstance(geometry, Mapping) or geometry.get("type") != "Polygon":
        raise ValueError("Для зоны требуется GeoJSON Polygon.")

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes)):
        raise ValueError("У зоны отсутствуют координаты.")

    rings: list[list[list[float]]] = []
    for ring in coordinates:
        if not isinstance(ring, Sequence) or isinstance(ring, (str, bytes)):
            raise ValueError("Некорректный контур зоны.")
        points: list[list[float]] = []
        for point in ring:
            if (
                not isinstance(point, Sequence)
                or isinstance(point, (str, bytes))
                or len(point) < 2
            ):
                raise ValueError("Некорректная точка в контуре зоны.")
            longitude, latitude = point[0], point[1]
            if isinstance(longitude, bool) or isinstance(latitude, bool):
                raise ValueError("Некорректные координаты зоны.")
            try:
                longitude = float(longitude)
                latitude = float(latitude)
            except (TypeError, ValueError) as error:
                raise ValueError("Некорректные координаты зоны.") from error
            if (
                not math.isfinite(longitude)
                or not math.isfinite(latitude)
                or not -180 <= longitude <= 180
                or not -90 <= latitude <= 90
            ):
                raise ValueError("Координаты зоны находятся вне допустимого диапазона.")
            points.append([longitude, latitude])

        if points and points[0] != points[-1]:
            points.append(points[0])
        if len(points) < 4 or len({tuple(point) for point in points[:-1]}) < 3:
            raise ValueError("У зоны должно быть не менее трёх различных точек.")
        rings.append(points)

    if not rings:
        raise ValueError("У зоны отсутствует внешний контур.")
    return {"type": "Polygon", "coordinates": rings}


def point_in_polygon(
    longitude: float,
    latitude: float,
    geometry: Mapping[str, object],
) -> tuple[bool, bool]:
    rings = geometry["coordinates"]
    outer_inside, outer_boundary = _point_in_ring(longitude, latitude, rings[0])
    if outer_boundary:
        return False, True
    if not outer_inside:
        return False, False

    for hole in rings[1:]:
        hole_inside, hole_boundary = _point_in_ring(longitude, latitude, hole)
        if hole_boundary:
            return False, True
        if hole_inside:
            return False, False
    return True, False


def _point_in_ring(
    longitude: float,
    latitude: float,
    ring: Sequence[Sequence[float]],
) -> tuple[bool, bool]:
    inside = False
    for first, second in zip(ring, (*ring[1:], ring[0])):
        x1, y1 = first
        x2, y2 = second
        cross = (longitude - x1) * (y2 - y1) - (latitude - y1) * (x2 - x1)
        if (
            abs(cross) <= 1e-10
            and min(x1, x2) - 1e-10 <= longitude <= max(x1, x2) + 1e-10
            and min(y1, y2) - 1e-10 <= latitude <= max(y1, y2) + 1e-10
        ):
            return False, True
        if (y1 > latitude) != (y2 > latitude):
            crossing_longitude = x1 + (latitude - y1) * (x2 - x1) / (y2 - y1)
            if longitude < crossing_longitude:
                inside = not inside
    return inside, False
