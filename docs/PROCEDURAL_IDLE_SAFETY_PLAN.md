# Procedural idle: localization and safe patch candidate

## Localized failure

The failure is in the old `tickIdlePose`, not VRMA or spring bones:

- `baseline` and `no-vrma` produce non-finite world bounds.
- `no-idle-vrma` remains finite.
- The current product call is intentionally guarded by `if (false && ...)` and remains disabled.

The exact unsafe operation is direct mutation of `Object3D.rotation` components on VRM normalized proxy bones. Three.js Euler setters immediately rebuild the quaternion from all Euler components. If any pre-existing component is non-finite, or if the normalized proxy was left in an invalid state by another writer, assigning one component propagates NaN to the complete quaternion and then through the raw skeleton matrices and skinned bounds. The old code has no finite checks and no rollback.

Additional correctness defects:

1. `REST_UPPER_ARM_Z` and `REST_ELBOW_BEND` are hard-coded absolute Euler references, not cloned rest quaternions from the loaded model.
2. Each frame overwrites only selected Euler axes, leaving other axes from the previous writer/VRMA; this mixes references and can preserve invalid/stale components.
3. `idleCur` survives model reloads and events, so pose state is not scoped to a cloned base pose.
4. Event end only eases scalar channels toward zero; it does not atomically restore the captured pose.
5. Missing bones are partly tolerated, but there is no validation that the selected normalized node and its quaternion are usable.
6. This is not primarily quaternion component-order or lerp/slerp failure: the old block does not construct quaternion arrays and does not call lerp/slerp. It is unsafe Euler-to-quaternion reconstruction plus missing validation/rollback and hard-coded rest references.
7. Normalized/raw conflict is indirect: writes target normalized humanoid proxies while diagnostics observe raw bones. That is supported by three-vrm, but only if normalized proxy quaternions remain finite. Product code must not write raw and normalized nodes for the same gesture.

## Minimal safe mechanism

Patch candidate: `avatar/renderer/procedural-idle-candidate.js`.

- Resolve only `vrm.humanoid.getNormalizedBoneNode(name)`.
- Capture each available bone's normalized quaternion with `clone().normalize()` after the desired base/rest pose is established.
- At the start of every frame, restore all controlled bones from those clones.
- Build a finite delta quaternion from an explicit `Euler(..., 'XYZ')`.
- Compose `target = base * delta`, normalize it, and copy only when all four components are finite and length is non-zero.
- Skip absent bones.
- Never mutate bone position or scale.
- Use a zero-at-both-ends event envelope and call `restore()` when the event finishes/cancels.
- Do not combine this writer with VRMA; capture a fresh base after VRMA/rest restoration before starting an event.

## Integration gate

Do not import or call the candidate from `viewer.js` yet. Keep the existing product guard disabled until all invariants pass on `vita_avatar_app/Nima_Sexual.vrm`.

Harness: `avatar/tests/procedural_idle_invariants.cjs`.

The harness expects a test-only `window.__nimaInvariantProbe` installed by a later isolated test integration. For each of `hips`, `stretch`, `gesture`, `show`, `butt`, and `jiggle`, it samples a full cycle and requires:

- every controlled normalized bone quaternion and world matrix is finite;
- every skinned mesh world/deformed bound is finite;
- the final pose matches the cloned base quaternion (sign-insensitive dot comparison);
- no position or scale changes.

Only after the probe is wired and all six cycles pass should the product tick be enabled behind a feature flag, followed by a second full invariant run.
