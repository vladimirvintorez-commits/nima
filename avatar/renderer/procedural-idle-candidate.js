import * as THREE from 'three';

// Все процедурные жесты + 'sit' (базовая сидячая поза, не выбирается случайно).
export const PROCEDURAL_GESTURES = ['hips', 'stretch', 'gesture', 'show', 'butt', 'jiggle', 'ears', 'tail', 'fix', 'bosom'];
const BONE_NAMES = [
  'hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
  'leftShoulder', 'rightShoulder', 'leftUpperArm', 'rightUpperArm',
  'leftLowerArm', 'rightLowerArm',
  // Ноги: base нужен всегда (restore), а в жестах с поворотом таза на них
  // кладётся КОМПЕНСАЦИЯ, чтобы стопы оставались на месте.
  'leftUpperLeg', 'rightUpperLeg', 'leftLowerLeg', 'rightLowerLeg',
  'leftFoot', 'rightFoot',
];

// ОСИ (проверено покадрово с лица и с боку, 11-12.09):
//  - локоть: прямой сгиб вперёд = left y<0 / right y>0 (как REST_ELBOW_BEND);
//    обратный знак даёт ладонь у попы.
//  - плечо: x>0 — рука вперёд-вверх (проверено зондом по кадрам, модель в
//    платье, фаза 0.55: ладони у плеч), x<0 — назад; z — разведение в стороны.
const SIT_ROT = {
  // x>0 = прогиб назад = таз подворачивается, корпус вертикальнее.
  hips: [0.26, 0, 0],
  spine: [0.05, 0, 0],
  // Лотос (по-портновски): бедро вперёд и развёрнуто коленом наружу,
  // голень сложена перед собой. Скромные y/z — раньше ±0.75/±0.8 выламывали
  // ноги («как будто переломаны»).
  leftUpperLeg: [-1.35, 0, 0.12],
  rightUpperLeg: [-1.35, 0, -0.12],
  leftLowerLeg: [2.35, 0, 0],
  rightLowerLeg: [2.35, 0, 0],
  leftFoot: [-0.55, 0, 0],
  rightFoot: [-0.55, 0, 0],
  // Ладони на коленях: плечо ВПЕРЁД-вниз (x>0 — вперёд, проверено зондом),
  // лёгкое разведение, предплечье чуть согнуто вперёд (локоть left y<0).
  leftUpperArm: [0.5, 0, -0.55],
  rightUpperArm: [0.5, 0, 0.55],
  leftLowerArm: [0, -0.85, 0],
  rightLowerArm: [0, 0.85, 0],
};
// Таз опускается на «пол»: delta к захваченной позиции hips (подгоняется кадрами).
const SIT_HIPS_DROP = -0.5;

const _delta = new THREE.Quaternion();
const _target = new THREE.Quaternion();
const _euler = new THREE.Euler(0, 0, 0, 'XYZ');

// Сигнал для bust-пружин (желейная реакция груди) — заполняется в
// offsetsForGesture (u известен там), домножается на огибающую в apply().
const bustSignal = { power: 0, x: 0, y: 0, z: 0 };

function finiteQuaternion(q) {
  return Number.isFinite(q.x) && Number.isFinite(q.y) &&
    Number.isFinite(q.z) && Number.isFinite(q.w) && q.lengthSq() > 1e-12;
}

function envelope(t, duration) {
  if (!Number.isFinite(t) || !Number.isFinite(duration) || duration <= 0) return 0;
  const u = THREE.MathUtils.clamp(t / duration, 0, 1);
  return Math.sin(Math.PI * u) ** 2;
}

// Компенсация на верхние ноги, гасящая поворот таза: normalized-риг выравнивает
// оси костей, поэтому обратный эйлер на upperLeg удерживает ноги/стопы в мире
// на месте, пока таз качается (углы малые — приближение достаточно).
function legCounter(offsets, hx, hy, hz) {
  set(offsets, 'leftUpperLeg', -hx, -hy, -hz);
  set(offsets, 'rightUpperLeg', -hx, -hy, -hz);
}

function set(offsets, bone, x = 0, y = 0, z = 0) { offsets[bone] = [x, y, z]; }

function offsetsForGesture(type, t, side, duration) {
  const s = side < 0 ? -1 : 1;
  const offsets = {};
  // Колебания привязаны к фазе жеста u = t/duration ∈ [0,1]: полный цикл
  // укладывается в жест, поэтому поза всегда заметна в средней фазе.
  const u = (Number.isFinite(t) && Number.isFinite(duration) && duration > 0)
    ? THREE.MathUtils.clamp(t / duration, 0, 1) : 0;
  bustSignal.power = 0; bustSignal.x = 0; bustSignal.y = 0; bustSignal.z = 0;
  if (type === 'hips') {
    // Переминаясь с ноги на ногу: таз переносится вбок и обратно, ноги
    // компенсируются — стопы на месте, корпус чуть в противофазе.
    const sway = Math.sin(u * Math.PI * 2);
    const hx = sway * 0.02, hy = sway * 0.1 * s, hz = sway * 0.09;
    set(offsets, 'hips', hx, hy, hz);
    legCounter(offsets, hx, hy, hz);
    set(offsets, 'spine', -sway * 0.015, -sway * 0.045 * s, -sway * 0.055);
    set(offsets, 'chest', 0, -sway * 0.03 * s, -sway * 0.03);
    set(offsets, 'upperChest', 0, -sway * 0.018 * s, -sway * 0.018);
    set(offsets, 'neck', 0, sway * 0.015 * s, sway * 0.015);
    set(offsets, 'head', 0, sway * 0.015 * s, sway * 0.022);
  } else if (type === 'stretch') {
    const lift = Math.sin(u * Math.PI);
    set(offsets, 'spine', -0.04 * lift, 0, 0);
    set(offsets, 'chest', -0.085 * lift, 0, 0);
    set(offsets, 'upperChest', -0.06 * lift, 0, 0);
    set(offsets, 'neck', 0.028 * lift, 0, 0);
    set(offsets, 'head', -0.05 * lift, 0, 0);
    set(offsets, 'leftShoulder', -0.05 * lift, 0, -0.1 * lift);
    set(offsets, 'rightShoulder', -0.05 * lift, 0, 0.1 * lift);
    set(offsets, 'leftUpperArm', -0.16 * lift, -0.1 * lift, -0.5 * lift);
    set(offsets, 'rightUpperArm', -0.16 * lift, 0.1 * lift, 0.5 * lift);
    set(offsets, 'leftLowerArm', 0, -0.2 * lift, -0.07 * lift);
    set(offsets, 'rightLowerArm', 0, 0.2 * lift, 0.07 * lift);
  } else if (type === 'gesture') {
    // Один взмах: предплечье поднимается и опускается за время жеста.
    const beat = Math.sin(u * Math.PI);
    const active = s < 0 ? 'left' : 'right';
    const mirror = active === 'left' ? 1 : -1;
    set(offsets, `${active}Shoulder`, -0.04 * beat, 0, -0.1 * mirror * beat);
    set(offsets, `${active}UpperArm`, -0.3 * beat, 0.16 * mirror * beat, -0.3 * mirror * beat);
    set(offsets, `${active}LowerArm`, -0.08 * beat, -0.95 * mirror * beat, 0.12 * mirror * beat);
    set(offsets, 'upperChest', 0, 0.07 * s * beat, 0);
    set(offsets, 'neck', 0, -0.04 * s * beat, 0.018 * s * beat);
    set(offsets, 'head', 0.03 * beat, 0.1 * s * beat, 0.03 * s * beat);
  } else if (type === 'show') {
    // «Показать попу»: разворот почти вполоборота от камеры (~160°, 2.75 рад),
    // корпус наклоняется немного вперёд, таз отставлен назад и виляет.
    // Таз ведёт — ноги/стопы едут вместе с ним (дети hips). Лицо доворачивается
    // через плечо к камере всем верхом (spine+chest+neck+head).
    // Поворот за первые 35%, ПОЗА ДЕРЖИТСЯ до 78% (раньше огибающая гасила
    // поворот сразу после середины — выглядело «крутнулась и тут же обратно»).
    const turn = Math.sin(Math.min(u / 0.35, 1) * Math.PI / 2);
    const hold = u > 0.35 && u < 0.78 ? 1 : u <= 0.35 ? u / 0.35 : Math.max(0, 1 - (u - 0.78) / 0.22);
    const wig = Math.sin((u - 0.35) * Math.PI * 4.5) * turn * (u > 0.35 ? 1 : 0) * hold;
    // hips x>0 = прогиб назад = попу ОТСТАВЛЯЕТ назад (та же ось, что у spine).
    set(offsets, 'hips', 0.2 * turn, (2.75 + 0.14 * wig) * s * turn, 0.07 * wig);
    // Проверено кадрами: spine/chest x<0 — наклон ВПЕРЁД (x>0 прогибает спину
    // назад, «грудь колёсиком»). Наклон вперёд = минус.
    set(offsets, 'spine', -0.3 * turn, -0.25 * s * turn, -0.03 * wig);
    set(offsets, 'chest', -0.12 * turn, -0.32 * s * turn, 0);
    set(offsets, 'neck', 0, -0.3 * s * turn, 0.04 * s * turn);
    set(offsets, 'head', 0.07 * turn, -1.15 * s * turn, 0.05 * s * turn);
    set(offsets, 'leftShoulder', 0, 0, -0.05 * turn);
    set(offsets, 'rightShoulder', 0, 0, 0.05 * turn);
    set(offsets, 'leftUpperArm', -0.05 * turn, 0, -0.12 * turn);
    set(offsets, 'rightUpperArm', -0.05 * turn, 0, 0.12 * turn);
  } else if (type === 'butt') {
    // Полтора цикла: круговое покачивание тазом, ноги компенсируются.
    const sway = Math.sin(u * Math.PI * 3);
    const roll = Math.cos(u * Math.PI * 3);
    const hx = roll * 0.055, hy = sway * 0.09 * s, hz = sway * 0.11;
    set(offsets, 'hips', hx, hy, hz);
    legCounter(offsets, hx, hy, hz);
    set(offsets, 'spine', -roll * 0.025, -sway * 0.03 * s, -sway * 0.05);
    set(offsets, 'chest', 0, -sway * 0.02 * s, -sway * 0.026);
    set(offsets, 'upperChest', 0, -sway * 0.014 * s, -sway * 0.014);
    set(offsets, 'head', 0, sway * 0.02 * s, sway * 0.02);
  } else if (type === 'jiggle') {
    // Короткое высокочастотное потряхивание: ~5 циклов на жест.
    const shake = Math.sin(u * Math.PI * 10) + Math.sin(u * Math.PI * 13 + 0.6) * 0.35;
    const lateral = Math.sin(u * Math.PI * 11 + 1.1);
    set(offsets, 'spine', -shake * 0.035, -lateral * 0.02, 0);
    set(offsets, 'chest', shake * 0.09, lateral * 0.04, 0);
    set(offsets, 'upperChest', -shake * 0.04, -lateral * 0.022, 0);
    set(offsets, 'neck', shake * 0.01, lateral * 0.008, 0);
    set(offsets, 'head', -shake * 0.008, -lateral * 0.006, 0);
  } else if (type === 'ears') {
    // Ушки дёргаются поочерёдно (raw-костям ушей, они не humanoid — их пишет
    // сам модуль, см. capture/restore ниже); голова чуть вздрагивает.
    const twitch = Math.sin(u * Math.PI * 4) * Math.sin(u * Math.PI);
    set(offsets, 'head', 0.02 * twitch, 0.03 * s * twitch, 0.02 * twitch);
  } else if (type === 'tail') {
    // Виляние хвостом: хвост — пружинная цепочка, напрямую кость не повернёшь
    // (физика перезаписывает). Качаем бёдрами вбок — пружина хвоста тянется
    // следом и виляет. 3 взмаха на жест, ноги компенсируются (стопы на месте).
    const wag = Math.sin(u * Math.PI * 6);
    const hx = 0, hy = wag * 0.07 * s, hz = wag * 0.055;
    set(offsets, 'hips', hx, hy, hz);
    legCounter(offsets, hx, hy, hz);
    set(offsets, 'spine', 0, -wag * 0.022 * s, -wag * 0.02);
    set(offsets, 'chest', 0, -wag * 0.014 * s, 0);
    set(offsets, 'head', 0, wag * 0.028 * s, wag * 0.02);
  } else if (type === 'fix') {
    // «Поправляет грудь»: ОБЕ ладони подходят к груди и 3 раза приподнимают
    // её (pulse). ОСИ (проверено зондом по кадрам, платье, фаза 055): плечо
    // x>0 — рука вперёд-вверх, локоть left y<0 / right y>0 — сгиб вперёд.
    const reach = Math.sin(Math.min(u / 0.45, 1) * Math.PI / 2);
    // 2 медленных приподнимания вместо 3 быстрых.
    const pulse = Math.abs(Math.sin(u * Math.PI * 2)) ** 0.8;
    set(offsets, 'leftUpperArm', 0.38 * reach + 0.02 * pulse, 0.08 * reach, -0.14 * reach);
    set(offsets, 'leftLowerArm', 0, -2.15 * reach, 0.26 * reach);
    set(offsets, 'rightUpperArm', 0.38 * reach + 0.02 * pulse, -0.08 * reach, 0.14 * reach);
    set(offsets, 'rightLowerArm', 0, 2.15 * reach, -0.26 * reach);
    set(offsets, 'leftShoulder', 0, 0, -0.08 * reach);
    set(offsets, 'rightShoulder', 0, 0, 0.08 * reach);
    // Грудь навстречу рукам: x>0 = прогиб назад = грудь вперёд-вверх.
    set(offsets, 'chest', 0.06 * reach + 0.04 * pulse, 0, 0);
    set(offsets, 'head', -0.14 * reach, 0, 0);
  } else if (type === 'bosom') {
    // Руки убраны ЗА СПИНУ (плечо назад: x<0 — проверено зондом по кадрам),
    // грудь виляет влево-вправо ~3 раза.
    const sway = Math.sin(u * Math.PI * 4);
    const back = Math.sin(Math.min(u / 0.45, 1) * Math.PI / 2);
    set(offsets, 'leftUpperArm', -0.55 * back, 0, 0.05 * back);
    set(offsets, 'rightUpperArm', -0.55 * back, 0, -0.05 * back);
    set(offsets, 'leftLowerArm', 0, -0.35 * back, 0);
    set(offsets, 'rightLowerArm', 0, 0.35 * back, 0);
    set(offsets, 'chest', 0, sway * 0.09 * back, sway * 0.035 * back);
    set(offsets, 'upperChest', 0, sway * 0.05 * back, 0);
    set(offsets, 'spine', 0, sway * 0.05 * back, 0);
    set(offsets, 'head', 0.02 * back, sway * 0.06 * back, 0);
  }
  return offsets;
}

export function createProceduralIdleCandidate(vrm) {
  const bones = new Map();
  const base = new Map();
  const transforms = new Map();
  // Уши — не humanoid-кости (пружинные цепочки): ловим их отдельным списком.
  const ears = [];
  const earBase = new Map();
  const tails = [];
  const tailBase = new Map();
  // Bust-пружины: в жестах fix/bosom/jiggle на них подаётся внешняя сила —
  // грудь реагирует желейно (та же физика, что дёргает её при перетаскивании).
  const bustJoints = [];
  const bustBase = [];
  // Хвостовые пружины: жест 'tail' раскачивает их боковой гравитацией —
  // прямой поворот сырых костей не виден (локальная z у цепочки хвоста
  // вдоль кости, вращение = круч).
  const tailJoints = [];
  {
    const mgr = vrm?.springBoneManager;
    if (mgr?.joints) {
      for (const joint of mgr.joints) {
        if (/bust/i.test(String(joint.bone?.name || ''))) {
          bustJoints.push(joint);
          bustBase.push({ power: joint.settings.gravityPower, dir: joint.settings.gravityDir.clone() });
        } else if (/tail|foxtail/i.test(String(joint.bone?.name || ''))) {
          tailJoints.push({ joint, power: joint.settings.gravityPower, dir: joint.settings.gravityDir.clone() });
        }
      }
    }
  }
  let bustActive = false;
  let tailActive = false;
  function applyBust(weight) {
    const power = bustSignal.power * weight;
    if (power === 0 || !bustJoints.length) {
      if (bustActive) {
        bustJoints.forEach((joint, i) => {
          joint.settings.gravityPower = bustBase[i].power;
          joint.settings.gravityDir.copy(bustBase[i].dir);
        });
        bustActive = false;
      }
      return;
    }
    bustJoints.forEach((joint, i) => {
      joint.settings.gravityDir.set(bustSignal.x, bustSignal.y, bustSignal.z).normalize();
      joint.settings.gravityPower = bustBase[i].power + power;
    });
    bustActive = true;
  }
  // wag ∈ [-1,1] — фаза виляния (знак = сторона), weight — огибающая жеста.
  // Направление в мировых координатах (gravityDir пружин складывается в мире).
  function applyTail(wag, weight) {
    const mag = Math.abs(wag) * weight;
    if (mag === 0 || !tailJoints.length) {
      if (tailActive) {
        tailJoints.forEach((t) => {
          t.joint.settings.gravityPower = t.power;
          t.joint.settings.gravityDir.copy(t.dir);
        });
        tailActive = false;
      }
    } else {
      tailJoints.forEach((t) => {
        t.joint.settings.gravityDir.set(wag, 0, 0).normalize();
        t.joint.settings.gravityPower = t.power + mag;
      });
      tailActive = true;
    }
    if (state) state.tailGestureActive = tailActive;
  }

  function capture() {
    bones.clear();
    base.clear();
    transforms.clear();
    for (const name of BONE_NAMES) {
      const node = vrm?.humanoid?.getNormalizedBoneNode(name) || null;
      if (!node || !finiteQuaternion(node.quaternion)) continue;
      bones.set(name, node);
      base.set(name, node.quaternion.clone().normalize());
      transforms.set(name, {
        position: node.position.clone(),
        scale: node.scale.clone(),
      });
    }
    ears.length = 0;
    earBase.clear();
    tails.length = 0;
    tailBase.clear();
    vrm?.scene?.traverse((o) => {
      if (!o.isBone || !finiteQuaternion(o.quaternion)) return;
      // Уши и хвост — не humanoid-кости (пружинные цепочки): ловим списком.
      if (/ear/i.test(o.name)) {
        ears.push(o);
        earBase.set(o, o.quaternion.clone().normalize());
      } else if (/tail|foxtail/i.test(o.name)) {
        tails.push(o);
        tailBase.set(o, o.quaternion.clone().normalize());
      }
    });
    return bones.size > 0;
  }

  capture();

  function restore() {
    for (const [name, node] of bones) {
      const rest = base.get(name);
      const transform = transforms.get(name);
      if (rest) node.quaternion.copy(rest);
      if (transform) {
        node.position.copy(transform.position);
        node.scale.copy(transform.scale);
      }
    }
    for (const [node, rest] of earBase) node.quaternion.copy(rest);
    for (const [node, rest] of tailBase) node.quaternion.copy(rest);
    if (tailActive) applyTail(0, 0);
  }

  // sitWeight ∈ [0,1] — плавный вход/выход из сидячей позы (лотос).
  function apply(type, elapsed, duration, side = 1, micro = null, sitWeight = 0) {
    restore();
    applyBust(0);
    if (type !== 'tail') {
      // Хвост виляет ПОСТОЯННО (решение пользователя): мягкий фоновый ритм
      // ~0.9 Гц всё время, пока живёт idle. Жест 'tail' даёт более бойкое
      // виляние поверх, а restore() (старт VRMA) сбрасывает к базе —
      // во время анимаций хвостом рулит инерция, как раньше.
      const tw = performance.now() / 1000;
      applyTail(Math.sin(tw * Math.PI * 2 * 0.45), 0.35);
    }
    const offsets = {};
    // 1) Базовая сидячая поза (без огибающей, вес = sitWeight).
    if (sitWeight > 0) {
      for (const [name, angles] of Object.entries(SIT_ROT)) {
        if (!angles.every(Number.isFinite)) continue;
        offsets[name] = [angles[0] * sitWeight, angles[1] * sitWeight, angles[2] * sitWeight];
      }
    }
    // 2) Микро-жизнь (дыхание/взгляд/микро-голова) — БЕЗ огибающей.
    if (micro) {
      for (const [name, angles] of Object.entries(micro)) {
        if (angles && angles.length === 3 && angles.every(Number.isFinite)) offsets[name] = angles;
      }
    }
    // 3) Сам жест — под огибающей.
    if (PROCEDURAL_GESTURES.includes(type)) {
      // show сам управляет входом/удержанием/выходом — огибающая sin²
      // гасила его с середины («крутнулась и тут же обратно»).
      const weight = type === 'show' ? 1 : envelope(elapsed, duration);
      const gesture = offsetsForGesture(type, elapsed, side, duration);
      for (const [name, angles] of Object.entries(gesture)) {
        const m = offsets[name];
        offsets[name] = m
          ? [m[0] + angles[0] * weight, m[1] + angles[1] * weight, m[2] + angles[2] * weight]
          : [angles[0] * weight, angles[1] * weight, angles[2] * weight];
      }
      if (type === 'ears') {
        // Сырым костям ушей — лёгкий поочерёдный дёрг (модуль сам restore-ит).
        const twitch = Math.sin(
          (Number.isFinite(elapsed) && duration > 0 ? THREE.MathUtils.clamp(elapsed / duration, 0, 1) : 0)
          * Math.PI * 5) * envelope(elapsed, duration);
        ears.forEach((node, i) => {
          const rest = earBase.get(node);
          if (!rest) return;
          const dir = i % 2 === 0 ? 1 : -1;
          _euler.set(0, 0, twitch * 0.4 * dir, 'XYZ');
          _delta.setFromEuler(_euler);
          node.quaternion.copy(rest).multiply(_delta);
        });
      }
      if (type === 'tail') {
        // Хвост — пружинная цепочка с grav=0: раскачиваем её боковой силой
        // (мировая ось X, знак чередуется синусом), пружина тянется и виляет.
        const u = Number.isFinite(elapsed) && duration > 0
          ? THREE.MathUtils.clamp(elapsed / duration, 0, 1) : 0;
        const env = envelope(elapsed, duration);
        // Мягкое виляние: сильная/быстрая сила сминала цепочку в комок.
        applyTail(Math.sin(u * Math.PI * 4), 0.45 * env);
      }
    }
    for (const [name, angles] of Object.entries(offsets)) {
      const node = bones.get(name);
      const rest = base.get(name);
      if (!node || !rest || !angles.every(Number.isFinite)) continue;
      _euler.set(angles[0], angles[1], angles[2], 'XYZ');
      _delta.setFromEuler(_euler).normalize();
      _target.copy(rest).multiply(_delta).normalize();
      if (finiteQuaternion(_target)) node.quaternion.copy(_target);
      else node.quaternion.copy(rest);
    }
    // Позиция таза: приседание на «пол» в сидячей позе.
    if (sitWeight > 0) {
      const node = bones.get('hips');
      const transform = transforms.get('hips');
      if (node && transform) {
        node.position.set(transform.position.x, transform.position.y + SIT_HIPS_DROP * sitWeight,
          transform.position.z);
      }
    }
    return true;
  }

  function isRestored(epsilon = 1e-6) {
    for (const [name, node] of bones) {
      const rest = base.get(name);
      const transform = transforms.get(name);
      if (!finiteQuaternion(node.quaternion) || 1 - Math.abs(node.quaternion.dot(rest)) > epsilon) return false;
      if (!transform || node.position.distanceToSquared(transform.position) > epsilon * epsilon ||
          node.scale.distanceToSquared(transform.scale) > epsilon * epsilon) return false;
    }
    for (const [node, rest] of earBase) {
      if (1 - Math.abs(node.quaternion.dot(rest)) > epsilon) return false;
    }
    for (const [node, rest] of tailBase) {
      if (1 - Math.abs(node.quaternion.dot(rest)) > epsilon) return false;
    }
    return true;
  }

  // Хвост-флаг читает viewer (tickInertia не трогает tail-пружины во время жеста)
  const state = { apply, restore, capture, isRestored, bones, base, transforms,
    ears, earBase, tails, tailBase, tailGestureActive: false, SIT_ROT, SIT_HIPS_DROP };
  return state;
}
