import math
from panda3d.core import (
    GeomVertexFormat,
    GeomVertexData,
    GeomVertexWriter,
    GeomTriangles,
    Geom,
    GeomNode,
    NodePath,
)


def make_uv_sphere(radius=1.0, rings=64, segments=128):
    """
    Create a smooth UV sphere NodePath.
    """
    fmt = GeomVertexFormat.getV3n3t2()
    vdata = GeomVertexData("sphere", fmt, Geom.UHStatic)

    vwriter = GeomVertexWriter(vdata, "vertex")
    nwriter = GeomVertexWriter(vdata, "normal")
    twriter = GeomVertexWriter(vdata, "texcoord")

    for r in range(rings + 1):
        v = r / rings
        theta = v * math.pi
        sin_t = math.sin(theta)
        cos_t = math.cos(theta)

        for s in range(segments + 1):
            u = s / segments
            phi = u * math.tau
            sin_p = math.sin(phi)
            cos_p = math.cos(phi)

            x = sin_t * cos_p
            y = sin_t * sin_p
            z = cos_t

            vwriter.addData3(x * radius, y * radius, z * radius)
            nwriter.addData3(x, y, z)
            twriter.addData2(u, 1.0 - v)

    tris = GeomTriangles(Geom.UHStatic)
    for r in range(rings):
        for s in range(segments):
            i0 = r * (segments + 1) + s
            i1 = i0 + 1
            i2 = i0 + (segments + 1)
            i3 = i2 + 1

            tris.addVertices(i0, i2, i1)
            tris.addVertices(i1, i2, i3)

    geom = Geom(vdata)
    geom.addPrimitive(tris)

    node = GeomNode("uv_sphere")
    node.addGeom(geom)
    return NodePath(node)
