import { useEffect, useRef } from "react";
import * as THREE from "three";
import type { TwinState } from "../types";

interface Props {
  twin: TwinState | null;
}

export default function DigitalTwin({ twin }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({
    scene: null as THREE.Scene | null,
    camera: null as THREE.PerspectiveCamera | null,
    renderer: null as THREE.WebGLRenderer | null,
    drone: null as THREE.Group | null,
    trail: null as THREE.Line | null,
    groundGrid: null as THREE.GridHelper | null,
    animId: 0,
  });

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0e14);
    scene.fog = new THREE.Fog(0x0a0e14, 40, 80);

    const camera = new THREE.PerspectiveCamera(
      50,
      el.clientWidth / el.clientHeight,
      0.1,
      200,
    );
    camera.position.set(8, 6, 8);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(el.clientWidth, el.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);

    const ambientLight = new THREE.AmbientLight(0x4f8cff, 0.4);
    scene.add(ambientLight);
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(5, 10, 7);
    scene.add(dirLight);

    const grid = new THREE.GridHelper(30, 30, 0x24303f, 0x1a222f);
    scene.add(grid);

    const drone = createDroneModel();
    scene.add(drone);

    const trailGeometry = new THREE.BufferGeometry();
    const trailPositions = new Float32Array(300 * 3);
    trailGeometry.setAttribute("position", new THREE.BufferAttribute(trailPositions, 3));
    trailGeometry.setDrawRange(0, 0);
    const trailMaterial = new THREE.LineBasicMaterial({ color: 0x4f8cff, opacity: 0.6, transparent: true });
    const trail = new THREE.Line(trailGeometry, trailMaterial);
    scene.add(trail);

    const s = stateRef.current;
    s.scene = scene;
    s.camera = camera;
    s.renderer = renderer;
    s.drone = drone;
    s.trail = trail;
    s.groundGrid = grid;

    const animate = () => {
      s.animId = requestAnimationFrame(animate);
      renderer.render(scene, camera);
    };
    animate();

    const onResize = () => {
      if (!el) return;
      camera.aspect = el.clientWidth / el.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(el.clientWidth, el.clientHeight);
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(s.animId);
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  useEffect(() => {
    const s = stateRef.current;
    if (!s.drone || !twin) return;

    s.drone.position.set(twin.x * 0.5, twin.altitude * 0.5, twin.y * 0.5);
    s.drone.rotation.y = THREE.MathUtils.degToRad(-twin.heading);

    if (twin.armed) {
      const body = s.drone.children[0] as THREE.Mesh;
      if (body) (body.material as THREE.MeshStandardMaterial).emissiveIntensity = 0.3;
    } else {
      const body = s.drone.children[0] as THREE.Mesh;
      if (body) (body.material as THREE.MeshStandardMaterial).emissiveIntensity = 0;
    }

    if (s.trail && twin.trajectory.length > 0) {
      const geo = s.trail.geometry;
      const positions = geo.attributes.position as THREE.BufferAttribute;
      const count = Math.min(twin.trajectory.length, 100);
      for (let i = 0; i < count; i++) {
        const p = twin.trajectory[twin.trajectory.length - count + i];
        positions.setXYZ(i, p.x * 0.5, p.z * 0.5, p.y * 0.5);
      }
      positions.needsUpdate = true;
      geo.setDrawRange(0, count);
    }

    if (s.camera) {
      const target = new THREE.Vector3(twin.x * 0.5, twin.altitude * 0.5, twin.y * 0.5);
      s.camera.lookAt(target);
    }
  }, [twin]);

  return (
    <div className="digital-twin-panel">
      <div className="twin-header">
        <h3>Digital Twin</h3>
        {twin && (
          <span className={`twin-badge ${twin.connected ? "live" : "offline"}`}>
            {twin.connected ? "LIVE" : "OFFLINE"}
          </span>
        )}
      </div>
      <div className="twin-viewport" ref={mountRef} />
      {twin && (
        <div className="twin-readout">
          <span>ALT {twin.altitude.toFixed(1)}m</span>
          <span>HDG {twin.heading.toFixed(0)}&deg;</span>
          <span>SPD {twin.velocity.toFixed(1)}m/s</span>
          <span>BAT {twin.battery_percentage.toFixed(0)}%</span>
        </div>
      )}
    </div>
  );
}

function createDroneModel(): THREE.Group {
  const group = new THREE.Group();

  const bodyGeo = new THREE.BoxGeometry(0.6, 0.15, 0.6);
  const bodyMat = new THREE.MeshStandardMaterial({
    color: 0x4f8cff,
    metalness: 0.3,
    roughness: 0.7,
    emissive: 0x4f8cff,
    emissiveIntensity: 0,
  });
  const body = new THREE.Mesh(bodyGeo, bodyMat);
  group.add(body);

  const armGeo = new THREE.CylinderGeometry(0.03, 0.03, 0.7, 6);
  const armMat = new THREE.MeshStandardMaterial({ color: 0x8b98a9 });
  const offsets: [number, number][] = [
    [0.35, 0.35],
    [-0.35, 0.35],
    [0.35, -0.35],
    [-0.35, -0.35],
  ];
  for (const [ox, oz] of offsets) {
    const arm = new THREE.Mesh(armGeo, armMat);
    arm.rotation.z = Math.PI / 2;
    arm.position.set(ox, 0.1, oz);
    group.add(arm);

    const rotorGeo = new THREE.CylinderGeometry(0.18, 0.18, 0.02, 16);
    const rotorMat = new THREE.MeshStandardMaterial({ color: 0x2ecc71, opacity: 0.6, transparent: true });
    const rotor = new THREE.Mesh(rotorGeo, rotorMat);
    rotor.position.set(ox, 0.18, oz);
    group.add(rotor);
  }

  const noseGeo = new THREE.ConeGeometry(0.08, 0.2, 6);
  const noseMat = new THREE.MeshStandardMaterial({ color: 0xff5c5c });
  const nose = new THREE.Mesh(noseGeo, noseMat);
  nose.rotation.x = Math.PI / 2;
  nose.position.set(0, 0, -0.4);
  group.add(nose);

  return group;
}
