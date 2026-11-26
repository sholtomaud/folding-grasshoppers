"""Small closed test meshes as plain (vertices, faces) lists, in mm."""

from __future__ import annotations

import math


def box(dx=100.0, dy=60.0, dz=40.0, origin=(0.0, 0.0, 0.0)):
    """Axis-aligned box as 12 outward-facing triangles."""
    ox, oy, oz = origin
    v = [
        (ox, oy, oz), (ox + dx, oy, oz), (ox + dx, oy + dy, oz), (ox, oy + dy, oz),
        (ox, oy, oz + dz), (ox + dx, oy, oz + dz), (ox + dx, oy + dy, oz + dz), (ox, oy + dy, oz + dz),
    ]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    faces = []
    for a, b, c, d in quads:
        faces += [(a, b, c), (a, c, d)]
    return v, faces


def box_quads(dx=100.0, dy=60.0, dz=40.0):
    """The same box as 6 quads (what Grasshopper's Mesh Box gives)."""
    v, _ = box(dx, dy, dz)
    return v, [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def uv_sphere(radius=50.0, rings=16, segments=24, center=(0.0, 0.0, 0.0)):
    cx, cy, cz = center
    v = [(cx, cy, cz - radius)]
    for i in range(1, rings):
        phi = -math.pi / 2 + math.pi * i / rings
        for j in range(segments):
            th = 2 * math.pi * j / segments
            v.append((cx + radius * math.cos(phi) * math.cos(th),
                      cy + radius * math.cos(phi) * math.sin(th),
                      cz + radius * math.sin(phi)))
    v.append((cx, cy, cz + radius))
    top = len(v) - 1
    faces = []

    def ring(i, j):
        return 1 + (i - 1) * segments + (j % segments)

    for j in range(segments):
        faces.append((0, ring(1, j + 1), ring(1, j)))
    for i in range(1, rings - 1):
        for j in range(segments):
            a, b = ring(i, j), ring(i, j + 1)
            c, d = ring(i + 1, j + 1), ring(i + 1, j)
            faces += [(a, b, c), (a, c, d)]
    for j in range(segments):
        faces.append((ring(rings - 1, j), ring(rings - 1, j + 1), top))
    return v, faces


def torus(major=60.0, minor=20.0, n_major=32, n_minor=16):
    """Torus around the Z axis: its horizontal slices through the middle have a hole."""
    v = []
    for i in range(n_major):
        a = 2 * math.pi * i / n_major
        for j in range(n_minor):
            b = 2 * math.pi * j / n_minor
            r = major + minor * math.cos(b)
            v.append((r * math.cos(a), r * math.sin(a), minor * math.sin(b)))
    faces = []
    for i in range(n_major):
        for j in range(n_minor):
            a = i * n_minor + j
            b = ((i + 1) % n_major) * n_minor + j
            c = ((i + 1) % n_major) * n_minor + (j + 1) % n_minor
            d = i * n_minor + (j + 1) % n_minor
            faces += [(a, b, c), (a, c, d)]
    return v, faces


def to_rhino(vertices, faces):
    """Build a rhino3dm.Mesh (what Hops decodes from Grasshopper)."""
    import rhino3dm

    m = rhino3dm.Mesh()
    for x, y, z in vertices:
        m.Vertices.Add(x, y, z)
    for f in faces:
        if len(f) == 3:
            m.Faces.AddFace(f[0], f[1], f[2])
        else:
            m.Faces.AddFace(f[0], f[1], f[2], f[3])
    return m
