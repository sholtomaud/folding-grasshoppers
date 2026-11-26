"""Loading a model exported from Rhino (OBJ or STL) for the Design Assistant."""

import os
import struct

import pytest

from cardboard_engine.components.mesh_io import (
    MeshFileError,
    load_mesh_file,
    parse_obj,
    parse_stl,
    scale_mesh,
)
from tests.meshes import box

OBJ_QUAD_CUBE = """# a cube
o cube
v 0 0 0
v 10 0 0
v 10 10 0
v 0 10 0
v 0 0 10
v 10 0 10
v 10 10 10
v 0 10 10
vn 0 0 1
vt 0 0
f 1/1/1 4/1/1 3/1/1 2/1/1
f 5//1 6//1 7//1 8//1
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f -4 -8 -5 -1
"""


def test_obj_with_quads_slashes_and_negative_indices():
    m = parse_obj(OBJ_QUAD_CUBE)
    assert len(m.vertices) == 8
    assert len(m.faces) == 6
    assert m.bbox() == ((0, 0, 0), (10, 10, 10))
    assert m.faces[5] == (4, 0, 3, 7)


def test_obj_polygons_with_more_than_four_sides_are_fanned():
    text = "v 0 0 0\nv 2 0 0\nv 3 1 0\nv 1 2 0\nv -1 1 0\nf 1 2 3 4 5\n"
    m = parse_obj(text)
    assert m.faces == ((0, 1, 2), (0, 2, 3), (0, 3, 4))


def test_obj_without_faces_is_an_error():
    with pytest.raises(MeshFileError, match="no faces"):
        parse_obj("v 0 0 0\nv 1 0 0\nv 0 1 0\n")


def test_obj_bad_index_is_an_error():
    with pytest.raises(MeshFileError, match="line 4"):
        parse_obj("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n")


def _ascii_stl(v, faces):
    lines = ["solid test"]
    for f in faces:
        lines += ["facet normal 0 0 0", " outer loop"]
        lines += [f"  vertex {v[i][0]} {v[i][1]} {v[i][2]}" for i in f]
        lines += [" endloop", "endfacet"]
    lines.append("endsolid test")
    return "\n".join(lines).encode()


def _binary_stl(v, faces, header=b"binary"):
    out = header.ljust(80, b" ") + struct.pack("<I", len(faces))
    for f in faces:
        out += struct.pack("<3f", 0, 0, 0)
        for i in f:
            out += struct.pack("<3f", *v[i])
        out += b"\0\0"
    return out


@pytest.mark.parametrize("encode", [_ascii_stl, _binary_stl])
def test_stl_triangles_are_welded(encode):
    v, faces = box(100, 60, 40)
    m = parse_stl(encode(v, faces))
    assert len(m.faces) == 12
    assert len(m.vertices) == 8, "shared corners become one vertex"
    assert m.bbox() == ((0, 0, 0), (100, 60, 40))


def test_binary_stl_whose_header_starts_with_solid_is_still_binary():
    v, faces = box(10, 10, 10)
    m = parse_stl(_binary_stl(v, faces, header=b"solid exported by Rhino"))
    assert len(m.faces) == 12


def test_garbage_stl_is_an_error():
    with pytest.raises(MeshFileError):
        parse_stl(b"not a mesh at all")


def test_load_mesh_file_reads_obj_and_stl_inside_home(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "cube.obj").write_text(OBJ_QUAD_CUBE)
    v, faces = box(100, 60, 40)
    (tmp_path / "models" / "box.STL").write_bytes(_binary_stl(v, faces))
    assert len(load_mesh_file(str(tmp_path / "models" / "cube.obj"), home=str(tmp_path)).faces) == 6
    assert len(load_mesh_file("~/models/box.STL", home=str(tmp_path)).faces) == 12


def test_load_mesh_file_refuses_paths_outside_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "secret.obj"
    outside.write_text(OBJ_QUAD_CUBE)
    with pytest.raises(MeshFileError, match="home folder"):
        load_mesh_file(str(outside), home=str(home))
    os.symlink(outside, home / "link.obj")
    with pytest.raises(MeshFileError, match="home folder"):
        load_mesh_file(str(home / "link.obj"), home=str(home))


def test_load_mesh_file_checks_extension_and_existence(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    with pytest.raises(MeshFileError, match=r"\.stl or \.obj"):
        load_mesh_file(str(tmp_path / "notes.txt"), home=str(tmp_path))
    with pytest.raises(MeshFileError, match="not found"):
        load_mesh_file(str(tmp_path / "missing.stl"), home=str(tmp_path))
    with pytest.raises(MeshFileError, match="absolute"):
        load_mesh_file("relative/model.stl", home=str(tmp_path))


def test_scale_mesh_scales_about_the_base_centre():
    v, faces = box(100, 60, 40)
    from cardboard_engine.components.geometry import Mesh

    m = scale_mesh(Mesh.from_lists(v, faces), 2.0)
    assert m.bbox() == ((-50, -30, 0), (150, 90, 80))
    with pytest.raises(MeshFileError, match="factor"):
        scale_mesh(m, 0)
