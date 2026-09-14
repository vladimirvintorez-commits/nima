// Temporary renderer diagnostics loaded only when ?diag=<variant> is present.
// Collects runtime transforms/bounds and emits one JSON record to the console.
export function installRendererDiagnostics({ THREE, getVRM, camera, renderer, clock }) {
  const params = new URLSearchParams(location.search);
  const variant = params.get('diag');
  if (!variant) return null;

  const flags = {
    noIdle: variant === 'no-idle' || variant === 'no-idle-vrma',
    noVrma: variant === 'no-vrma' || variant === 'no-idle-vrma',
    noSpring: variant === 'no-spring',
    noRestore: variant === 'no-restore',
  };
  let emitted = false;

  function finiteArray(values) {
    return values.every(Number.isFinite);
  }

  function sample() {
    if (emitted || clock.elapsedTime < 5) return;
    const vrm = getVRM();
    if (!vrm) return;
    emitted = true;
    vrm.scene.updateMatrixWorld(true);

    const boneNames = ['hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
      'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand',
      'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand',
      'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'rightUpperLeg', 'rightLowerLeg', 'rightFoot'];
    const bones = {};
    const v = new THREE.Vector3();
    const q = new THREE.Quaternion();
    const s = new THREE.Vector3();
    let invalid = false;
    for (const name of boneNames) {
      const node = vrm.humanoid?.getRawBoneNode(name);
      if (!node) continue;
      node.matrixWorld.decompose(v, q, s);
      const values = [...v.toArray(), ...q.toArray(), ...s.toArray()];
      if (!finiteArray(values)) invalid = true;
      bones[name] = { position: v.toArray(), quaternion: q.toArray(), scale: s.toArray() };
    }

    const worldBounds = new THREE.Box3().setFromObject(vrm.scene, true);
    const size = worldBounds.getSize(new THREE.Vector3());
    const center = worldBounds.getCenter(new THREE.Vector3());
    const cameraInside = worldBounds.containsPoint(camera.position);
    const meshes = [];
    vrm.scene.traverse((o) => {
      if (!o.isSkinnedMesh) return;
      if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
      const box = o.geometry.boundingBox?.clone();
      if (box) box.applyMatrix4(o.matrixWorld);
      meshes.push({
        name: o.name,
        vertices: o.geometry.attributes.position?.count ?? null,
        bounds: box ? { min: box.min.toArray(), max: box.max.toArray() } : null,
      });
    });
    const boundsValues = [...worldBounds.min.toArray(), ...worldBounds.max.toArray(), ...size.toArray()];
    if (!finiteArray(boundsValues)) invalid = true;
    console.log('[nima-diag-json]', JSON.stringify({
      variant, flags, elapsed: clock.elapsedTime, invalid,
      camera: camera.position.toArray(), cameraInside,
      worldBounds: { min: worldBounds.min.toArray(), max: worldBounds.max.toArray(), size: size.toArray(), center: center.toArray() },
      scene: { position: vrm.scene.position.toArray(), rotation: vrm.scene.rotation.toArray(), scale: vrm.scene.scale.toArray() },
      bones, meshes, canvas: { width: renderer.domElement.width, height: renderer.domElement.height },
    }));
  }

  return { variant, flags, sample };
}
