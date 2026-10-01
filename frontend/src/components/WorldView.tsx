import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Vec3 } from "../types";

/**
 * A generic, orbitable 3D view of a world in local ENU metres (x east,
 * y north, z up). The Twin page and the VantaForge CourseLab both describe
 * what to draw as a `WorldScene`; this component only renders it.
 */
export interface WorldScene {
  /** Flight volume drawn as a wire box; also used to frame the camera. */
  bounds?: { min: Vec3; max: Vec3 } | null;
  /** Region to frame the camera on, when it differs from the drawn bounds. */
  frame?: { min: Vec3; max: Vec3 } | null;
  paths?: Array<{ points: Vec3[]; color: number; opacity?: number; width?: "thin" | "bold" }>;
  gates?: Array<{ position: Vec3; normal: Vec3; width: number; height: number; color: number }>;
  /** Footprints extruded from the ground (no-fly zones). */
  regions?: Array<{ vertices: Array<[number, number]>; height: number; color: number; opacity?: number }>;
  /** Axis-aligned boxes (course obstacles). */
  boxes?: Array<{ center: Vec3; size: Vec3; color: number }>;
  points?: Array<{ position: Vec3; color: number; radius: number }>;
  aircraft?: { position: Vec3; yawDeg: number } | null;
}

interface Props {
  scene: WorldScene;
  /** Change to re-frame the camera on `bounds` (e.g. a new course id). */
  frameKey?: string | number;
  height?: number;
  /** Look straight down (top view) instead of the default 3/4 view. */
  view?: "perspective" | "top" | "side";
  label?: string;
}

/** ENU metres -> three.js (y up). North maps to -z so the view is not mirrored. */
function toScene([x, y, z]: Vec3): THREE.Vector3 {
  return new THREE.Vector3(x, z, -y);
}

export default function WorldView({ scene, frameKey, height = 420, view = "perspective", label }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const ref = useRef({
    renderer: null as THREE.WebGLRenderer | null,
    camera: null as THREE.PerspectiveCamera | null,
    controls: null as OrbitControls | null,
    content: null as THREE.Group | null,
    aircraft: null as THREE.Group | null,
    animId: 0,
  });

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      el.textContent = "3D view unavailable (WebGL is disabled).";
      return;
    }
    const world = new THREE.Scene();
    world.background = new THREE.Color(0x070b12);

    const camera = new THREE.PerspectiveCamera(50, el.clientWidth / Math.max(1, el.clientHeight), 0.1, 2000);
    camera.position.set(30, 25, 30);

    renderer.setSize(el.clientWidth, el.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    world.add(new THREE.AmbientLight(0x6fa8ff, 0.6));
    const sun = new THREE.DirectionalLight(0xffffff, 0.8);
    sun.position.set(20, 40, 10);
    world.add(sun);
    world.add(new THREE.GridHelper(200, 100, 0x2f4466, 0x172234));

    const content = new THREE.Group();
    world.add(content);
    const aircraft = createAircraft();
    aircraft.visible = false;
    world.add(aircraft);

    const s = ref.current;
    Object.assign(s, { renderer, camera, controls, content, aircraft });

    const animate = () => {
      s.animId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(world, camera);
    };
    animate();

    const onResize = () => {
      camera.aspect = el.clientWidth / Math.max(1, el.clientHeight);
      camera.updateProjectionMatrix();
      renderer.setSize(el.clientWidth, el.clientHeight);
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(s.animId);
      controls.dispose();
      disposeTree(world);
      renderer.dispose();
      el.removeChild(renderer.domElement);
      s.renderer = null;
    };
  }, []);

  // Rebuild the static content whenever the scene description changes.
  useEffect(() => {
    const { content, aircraft } = ref.current;
    if (!content || !aircraft) return;
    disposeTree(content);
    content.clear();

    if (scene.bounds) content.add(boundsBox(scene.bounds.min, scene.bounds.max));
    for (const p of scene.paths ?? []) {
      if (p.points.length < 2) continue;
      const geo = new THREE.BufferGeometry().setFromPoints(p.points.map(toScene));
      content.add(
        new THREE.Line(
          geo,
          new THREE.LineBasicMaterial({ color: p.color, transparent: true, opacity: p.opacity ?? 0.9 }),
        ),
      );
    }
    for (const g of scene.gates ?? []) content.add(gateRing(g));
    for (const r of scene.regions ?? []) content.add(regionColumn(r));
    for (const b of scene.boxes ?? []) {
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(b.size[0], b.size[2], b.size[1]),
        new THREE.MeshStandardMaterial({ color: b.color, transparent: true, opacity: 0.35 }),
      );
      mesh.position.copy(toScene(b.center));
      content.add(mesh);
    }
    for (const p of scene.points ?? []) {
      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(p.radius, 16, 12),
        new THREE.MeshBasicMaterial({ color: p.color }),
      );
      dot.position.copy(toScene(p.position));
      content.add(dot);
    }

    if (scene.aircraft) {
      aircraft.visible = true;
      aircraft.position.copy(toScene(scene.aircraft.position));
      aircraft.rotation.y = THREE.MathUtils.degToRad(-scene.aircraft.yawDeg);
    } else {
      aircraft.visible = false;
    }
  }, [scene]);

  // Frame the camera on the bounds (or the origin) when asked to.
  useEffect(() => {
    const { camera, controls } = ref.current;
    if (!camera || !controls) return;
    const framed = scene.frame ?? scene.bounds;
    const min = framed?.min ?? [-15, -15, 0];
    const max = framed?.max ?? [15, 15, 10];
    const center = toScene([(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2]);
    const span = Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2], 10);
    controls.target.copy(center);
    if (view === "top") camera.position.set(center.x, center.y + span * 1.4, center.z + 0.01);
    else if (view === "side") camera.position.set(center.x, center.y + span * 0.1, center.z + span * 1.4);
    else camera.position.set(center.x + span * 0.9, center.y + span * 0.75, center.z + span * 0.9);
    camera.far = span * 20;
    camera.updateProjectionMatrix();
    controls.update();
    // Only re-frame on an explicit key change, not every live update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameKey, view]);

  return (
    <div className="world-view" style={{ height }}>
      {label && <span className="world-view-label">{label}</span>}
      <div className="world-view-canvas" ref={mountRef} />
    </div>
  );
}

function boundsBox(min: Vec3, max: Vec3): THREE.LineSegments {
  const size = new THREE.Vector3(max[0] - min[0], max[2] - min[2], max[1] - min[1]);
  const box = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(size.x, size.y, size.z)),
    new THREE.LineDashedMaterial({ color: 0x5b6b82, dashSize: 0.6, gapSize: 0.4 }),
  );
  box.computeLineDistances();
  box.position.copy(toScene([(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2]));
  return box;
}

function gateRing(g: { position: Vec3; normal: Vec3; width: number; height: number; color: number }): THREE.Group {
  const group = new THREE.Group();
  const radius = Math.max(0.2, Math.min(g.width, g.height) / 2);
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(radius, Math.max(0.05, radius * 0.08), 12, 48),
    new THREE.MeshStandardMaterial({ color: g.color, emissive: g.color, emissiveIntensity: 0.8 }),
  );
  group.add(ring);
  group.position.copy(toScene(g.position));
  // The torus faces +z; turn it to face the gate normal.
  const normal = toScene(g.normal).normalize();
  if (normal.lengthSq() > 0) group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
  // A post down to the ground, like a real race gate stand.
  const base = toScene(g.position);
  if (base.y - radius > 0.05) {
    const postHeight = base.y - radius;
    const post = new THREE.Mesh(
      new THREE.CylinderGeometry(0.04, 0.04, postHeight, 6),
      new THREE.MeshStandardMaterial({ color: 0x8b98a9 }),
    );
    post.position.set(base.x, postHeight / 2, base.z);
    const holder = new THREE.Group();
    holder.add(group, post);
    return holder;
  }
  return group;
}

function regionColumn(r: { vertices: Array<[number, number]>; height: number; color: number; opacity?: number }): THREE.Mesh {
  // Shape in the x/(-z) plane, extruded upward.
  const shape = new THREE.Shape(r.vertices.map(([x, y]) => new THREE.Vector2(x, y)));
  const mesh = new THREE.Mesh(
    new THREE.ExtrudeGeometry(shape, { depth: r.height, bevelEnabled: false }),
    new THREE.MeshBasicMaterial({ color: r.color, transparent: true, opacity: r.opacity ?? 0.22, depthWrite: false }),
  );
  // Shape x/y -> scene x/-z, extrusion along +y (up).
  mesh.rotation.x = -Math.PI / 2;
  return mesh;
}

function createAircraft(): THREE.Group {
  const group = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.BoxGeometry(0.6, 0.15, 0.6),
    new THREE.MeshStandardMaterial({ color: 0x4f8cff, emissive: 0x4f8cff, emissiveIntensity: 0.4 }),
  );
  group.add(body);
  for (const [ox, oz] of [[0.35, 0.35], [-0.35, 0.35], [0.35, -0.35], [-0.35, -0.35]]) {
    const rotor = new THREE.Mesh(
      new THREE.CylinderGeometry(0.18, 0.18, 0.02, 16),
      new THREE.MeshStandardMaterial({ color: 0x2ecc71, transparent: true, opacity: 0.7 }),
    );
    rotor.position.set(ox, 0.12, oz);
    group.add(rotor);
  }
  const nose = new THREE.Mesh(new THREE.ConeGeometry(0.08, 0.2, 6), new THREE.MeshStandardMaterial({ color: 0xff5c5c }));
  nose.rotation.x = -Math.PI / 2;
  nose.position.set(0, 0, -0.4);
  group.add(nose);
  return group;
}

function disposeTree(root: THREE.Object3D): void {
  root.traverse((obj) => {
    if (obj instanceof THREE.Mesh || obj instanceof THREE.Line) {
      obj.geometry?.dispose();
      const mat = obj.material;
      if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
      else mat?.dispose();
    }
  });
}
