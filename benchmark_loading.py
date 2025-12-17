import time
import trimesh
import numpy as np
import os

# Create a dummy mesh
mesh = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
mesh.export('test.stl')

print("Loading test.stl...")
start = time.time()
loaded = trimesh.load('test.stl', force='mesh')
end = time.time()
print(f"Trimesh load time: {end - start:.4f}s")

if hasattr(loaded, 'vertex_normals'):
    print("Has vertex_normals")
    print(loaded.vertex_normals.shape)
else:
    print("No vertex_normals")

# Benchmark manual normal calc
vertices = np.asarray(loaded.vertices)
faces = np.asarray(loaded.faces)

print("Calculating normals manually...")
start = time.time()
# Copy-paste from model_viewer.py
normals = np.zeros_like(vertices, dtype=np.float32)
tris = vertices[faces]
n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
lens = np.linalg.norm(n, axis=1)
valid = lens > 0
n[valid] /= lens[valid][:, None]
for i, face in enumerate(faces):
    normals[face] += n[i]
lens = np.linalg.norm(normals, axis=1)
valid = lens > 0
normals[valid] /= lens[valid][:, None]
end = time.time()
print(f"Manual normal calc time: {end - start:.4f}s")

os.remove('test.stl')
