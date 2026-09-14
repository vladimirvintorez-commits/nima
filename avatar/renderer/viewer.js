// viewer.js — рендерер Нимфеи: VRM-модель в прозрачном окне.
//
// Источник истины по визуалу. После правок: npm run build-vrm
// (esbuild пересоберёт viewer.bundle.js).
//
// Управление:
//  • верхняя центральная точка — перетащить окно; двойной щелчок — блокировка/
//    разблокировка изменений окна;
//  • верхняя правая точка — изменение размера (симметрично, от центра окна);
//  • перетаскивание самой Нимфы ЛЕВОЙ кнопкой по полю — двигает её внутри окна
//    (позиция запоминается);
//  • зажатая СРЕДНЯЯ кнопка (СКМ) + движение мыши — вращение Нимфы во все
//    стороны (гориз. — вокруг вертикали, верт. — наклон); двойной клик СКМ —
//    сброс поворота. Поворот запоминается;
//  • колёсико — зум камеры.
//
// Состояние из Python (мост bridge.py → main.cjs): mood, action,
// current_outfit (файл VRM), speaking, mouth (0..1 — липсинк).
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import {
  VRMSpringBoneCollider, VRMSpringBoneColliderShapeSphere,
} from '@pixiv/three-vrm-springbone';
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from '@pixiv/three-vrm-animation';
import { installRendererDiagnostics } from './diagnostics.js';
import { createProceduralIdleCandidate, PROCEDURAL_GESTURES } from './procedural-idle-candidate.js';

// --- состояние ---
let vrm = null;
let currentOutfit = '';
let locked = false;
let speaking = false;
let mouthValue = 0;
let moodTarget = 'normal';

// Сохранённое управление камерой/моделью. Версия нужна, чтобы один раз
// восстановить профили, повреждённые старой записью camDist/transform state.
const VIEW_STATE_MIGRATION_KEY = 'nima_view_state_migration';
const VIEW_STATE_MIGRATION_VERSION = '1';
const CAM_DEFAULT = 2.1, CAM_MIN = 1.0, CAM_MAX = 4.0;
const OFFSET_DEFAULT = { x: 0, y: 0 };
const OFFSET_X_MIN = -0.55, OFFSET_X_MAX = 0.55;
const OFFSET_Y_MIN = -0.25, OFFSET_Y_MAX = 0.4;
const ROT_DEFAULT = { y: 0, x: 0 };
const USER_ROT_X_MIN = -Math.PI / 2, USER_ROT_X_MAX = Math.PI / 2;

function readFiniteNumber(value) {
  if (value === null || typeof value === 'boolean') return null;
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function readStoredObject(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  } catch (_) {
    return null;
  }
}

function migrateViewState() {
  if (localStorage.getItem(VIEW_STATE_MIGRATION_KEY) === VIEW_STATE_MIGRATION_VERSION) return;

  const rawCamDist = readFiniteNumber(localStorage.getItem('nima_cam_dist'));
  // Старые значения ниже штатного минимума могли поставить камеру внутрь модели.
  const camDist = rawCamDist !== null && rawCamDist >= CAM_MIN && rawCamDist <= CAM_MAX
    ? rawCamDist : CAM_DEFAULT;
  localStorage.setItem('nima_cam_dist', String(camDist));

  const rawOffset = readStoredObject('nima_char_offset');
  const offsetX = readFiniteNumber(rawOffset?.x);
  const offsetY = readFiniteNumber(rawOffset?.y);
  const offset = offsetX !== null && offsetY !== null
    && offsetX >= OFFSET_X_MIN && offsetX <= OFFSET_X_MAX
    && offsetY >= OFFSET_Y_MIN && offsetY <= OFFSET_Y_MAX
    ? { x: offsetX, y: offsetY } : OFFSET_DEFAULT;
  localStorage.setItem('nima_char_offset', JSON.stringify(offset));

  const rawRot = readStoredObject('nima_char_rot');
  const rotY = readFiniteNumber(rawRot?.y);
  const rotX = readFiniteNumber(rawRot?.x);
  const rot = rotY !== null && rotX !== null
    && rotY >= -Math.PI && rotY <= Math.PI
    && rotX >= USER_ROT_X_MIN && rotX <= USER_ROT_X_MAX
    ? { y: rotY, x: rotX } : ROT_DEFAULT;
  localStorage.setItem('nima_char_rot', JSON.stringify(rot));

  localStorage.setItem(VIEW_STATE_MIGRATION_KEY, VIEW_STATE_MIGRATION_VERSION);
}

try { migrateViewState(); } catch (_) {}

let charOffset = { ...OFFSET_DEFAULT };
const storedOffset = readStoredObject('nima_char_offset');
const storedOffsetX = readFiniteNumber(storedOffset?.x);
const storedOffsetY = readFiniteNumber(storedOffset?.y);
if (storedOffsetX !== null && storedOffsetY !== null
    && storedOffsetX >= OFFSET_X_MIN && storedOffsetX <= OFFSET_X_MAX
    && storedOffsetY >= OFFSET_Y_MIN && storedOffsetY <= OFFSET_Y_MAX) {
  charOffset = { x: storedOffsetX, y: storedOffsetY };
} else {
  try { localStorage.setItem('nima_char_offset', JSON.stringify(OFFSET_DEFAULT)); } catch (_) {}
}

const errorBox = document.getElementById('error');
const hint = document.getElementById('hint');
const dragDot = document.getElementById('dot-drag');
const resizeDot = document.getElementById('dot-resize');

function showError(msg) { errorBox.textContent = msg || ''; }

function flashHint(text) {
  hint.textContent = text;
  hint.classList.add('show');
  clearTimeout(flashHint._t);
  flashHint._t = setTimeout(() => hint.classList.remove('show'), 1600);
}

// --- сцена ---
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30.0, 1, 0.1, 20);
camera.position.set(0, 1.05, 2.1);
camera.lookAt(0, 0.95, 0);

const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
const OPAQUE_DEBUG = new URLSearchParams(location.search).has('opaque');
// ?cam=front|side|back|<deg> — азимут камеры для покадровых проверок жестов
const CAM_AZIM = (() => {
  const raw = new URLSearchParams(location.search).get('cam');
  if (!raw) return 0;
  const named = { front: 0, side: 90, back: 180 };
  const deg = raw in named ? named[raw] : Number(raw);
  return Number.isFinite(deg) ? deg : 0;
})();
// ?focus=<y> — высота точки взгляда камеры; ?dist=<m> — дистанция (отладка поз)
const CAM_FOCUS_Y = (() => {
  const v = Number(new URLSearchParams(location.search).get('focus'));
  return Number.isFinite(v) && v > 0 ? v : 0.95;
})();
const CAM_DIST_OVERRIDE = (() => {
  const v = Number(new URLSearchParams(location.search).get('dist'));
  return Number.isFinite(v) && v >= 1.0 && v <= 4.0 ? v : null;
})();
// ?debugbones — раз в 2 с печатать мировые позиции hips/head: ловим «модель
// просела/уменьшилась после VRMA» (живой баг 12.09 «пол-модели пропало»)
const DEBUG_BONES = new URLSearchParams(location.search).has('debugbones');
const _dbgV1 = new THREE.Vector3(), _dbgV2 = new THREE.Vector3();
renderer.setClearColor(0x101418, OPAQUE_DEBUG ? 1 : 0);
renderer.setSize(window.innerWidth, window.innerHeight);
document.getElementById('scene').appendChild(renderer.domElement);

scene.add(new THREE.AmbientLight(0xffffff, 1.15));
const dirLight = new THREE.DirectionalLight(0xffe9d2, 1.5);
dirLight.position.set(1.0, 1.6, 1.2).normalize();
scene.add(dirLight);

// --- загрузка VRM ---
// Файл читает main-процесс (fs) и отдаёт ArrayBuffer: fetch по file:// в
// Chromium ненадёжен — это была причина «пустого окна».
const loader = new GLTFLoader();
loader.register((parser) => new VRMLoaderPlugin(parser));
loader.register((parser) => new VRMAnimationLoaderPlugin(parser));

// --- VRMA-анимации (vita_avatar_app/animations/*.vrma) ---
// Клин-карта семантических действий на клипы; действия песочницы вида
// «vrma_<ИмяФайла>» играются напрямую. Новые .vrma достаточно положить в папку.
const ACTION_VRMA = {
  greeting: 'VRMA_03.vrma',
  dance:    'VRMA_01.vrma',
  jump:     'VRMA_02.vrma',
  spinning: 'VRMA_04.vrma',
  wave:     'Goodbye.vrma',   // был VRMA_04 (= spinning) — «машет» играла кружение
  stretch:  'VRMA_07.vrma',
  // семантические имена для папки animations/ (v14.8.39): файл = <Имя>.vrma
  angry: 'Angry.vrma', blush: 'Blush.vrma', clapping: 'Clapping.vrma',
  goodbye: 'Goodbye.vrma', lookaround: 'LookAround.vrma', relax: 'Relax.vrma',
  sad: 'Sad.vrma', sleepy: 'Sleepy.vrma', surprised: 'Surprised.vrma',
  thinking: 'Thinking.vrma',
  vrma_01: 'VRMA_01.vrma', vrma_02: 'VRMA_02.vrma', vrma_03: 'VRMA_03.vrma',
  vrma_04: 'VRMA_04.vrma', vrma_05: 'VRMA_05.vrma', vrma_06: 'VRMA_06.vrma',
  vrma_07: 'VRMA_07.vrma',
};

let animMixer = null;        // THREE.AnimationMixer поверх vrm.scene
let activeVrmaAction = null; // текущее действие (для кроссфейда и фильтра finished)
let animating = false;       // идёт one-shot VRMA (дыхание/покачивание заморожены)
let currentActionName = '';
let lastActionSeq = null;    // seq из bridge.py: повтор команды = перезапуск анимации
const vrmaCache = new Map(); // fileName → VRMAnimation

// Семантическое действие → клип; «vrma_<ИмяФайла>» (песочница debug menu и
// рантайм) → vita_avatar_app/animations/<ИмяФайла>.vrma. Новые .vrma достаточно
// положить в папку — ничего править не нужно.
function resolveVrmaFile(action) {
  if (!action) return null;
  if (ACTION_VRMA[action]) return ACTION_VRMA[action];
  if (action.startsWith('vrma_')) {
    const stem = action.slice(5);
    return stem.toLowerCase().endsWith('.vrma') ? stem : stem + '.vrma';
  }
  return null;
}

// Расслабленная стойка (замена A-позы — факт от пользователя 11.09: A-поза
// «манекенна»). Руки почти вдоль тела, лёгкий сгиб в локтях вперёд. Ставится
// при загрузке модели и после каждой VRMA — миксер оставляет кости в последнем
// кадре. Ось: 0 рад = руки горизонтально (T-поза), ±π/2 = вдоль тела.
const REST_UPPER_ARM_Z = 1.42;   // ≈8° от корпуса
const REST_ELBOW_BEND = 0.25;    // сгиб локтя вперёд (знаки у рук зеркальны)
function applyRestPose(model) {
  if (!model.humanoid) return;
  const lu = model.humanoid.getNormalizedBoneNode('leftUpperArm');
  const ru = model.humanoid.getNormalizedBoneNode('rightUpperArm');
  const ll = model.humanoid.getNormalizedBoneNode('leftLowerArm');
  const rl = model.humanoid.getNormalizedBoneNode('rightLowerArm');
  if (lu) lu.rotation.z = REST_UPPER_ARM_Z;
  if (ru) ru.rotation.z = -REST_UPPER_ARM_Z;
  // Ось локтя (проверено боковыми кадрами): вперёд = left y<0 / right y>0.
  if (ll) ll.rotation.y = -REST_ELBOW_BEND;
  if (rl) rl.rotation.y = REST_ELBOW_BEND;
}

function stopVrma() {
  if (animMixer && vrm) {
    animMixer.stopAllAction();
    animMixer.uncacheRoot(vrm.scene);
  }
  animMixer = null;
  activeVrmaAction = null;
  animating = false;
  if (vrm) {
    safeProceduralIdle?.restore();
    applyRestPose(vrm);      // вернуть стойку после сброса костей миксером
    safeProceduralIdle?.capture();
    safeIdleWasAnimating = false;
    if (vrm.expressionManager) {
      for (const name of Object.keys(exprCurrent)) vrm.expressionManager.setValue(name, 0);
    }
  }
}

function finishVrma(e) {
  // «finished» приходит и от затухающей старой анимации при кроссфейде —
  // её финал не должен убивать миксер раньше новой.
  if (e && e.action && activeVrmaAction && e.action !== activeVrmaAction) return;
  stopVrma();
  // Отложенная на время VRMA смена наряда — теперь можно.
  if (pendingOutfitSwitch) { loadOutfit(pendingOutfitSwitch); pendingOutfitSwitch = null; }
  // ВАЖНО: currentActionName НЕ сбрасываем. Мост хранит в avatar_state.json
  // последний action («greeting») вечно — если имя сбросить, следующее же
  // обновление state (mood/mouth/субтитр) посчитает «greeting !== ''» и запустит
  // анимацию заново: бесконечный перезапуск, idle-пластика никогда не работает.
  // Повтор команды и так ловится через seq (retrigger).
  console.log('[nima] VRMA finished');
}

async function playVrma(fileName) {
  if (!window.nima || !fileName) {
    console.warn('[nima] VRMA skip: nima=', !!window.nima, fileName);
    return;
  }
  if (!vrm) {
    // Модель ещё парсится (state приходит раньше конца загрузки) — запомним
    // и проиграем сразу после готовности, а не потеряем команду.
    pendingVrma = fileName;
    console.log('[nima] VRMA queued до загрузки модели:', fileName);
    return;
  }
  try {
    let anim = vrmaCache.get(fileName);
    if (!anim) {
      const buf = await window.nima.readVrm(fileName);
      if (!buf) { console.error('[nima] main не отдал анимацию', fileName); showError('main не отдал анимацию ' + fileName); return; }
      const gltf = await loader.parseAsync(buf, '');
      // three-vrm-animation v3 кладёт МАССИВ vrmAnimations (по клипу на анимацию)
      const anims = gltf.userData.vrmAnimations || [];
      anim = anims[0];
      if (!anim) { console.error('[nima] нет VRM-анимации в', fileName); showError('В файле нет VRM-анимации: ' + fileName); return; }
      vrmaCache.set(fileName, anim);
    }
    const wasAnimating = !!(animating && animMixer && activeVrmaAction);
    if (wasAnimating) {
      // Плавный кроссфейд вместо телепорта: раньше stopVrma() ронял кости в
      // стойку, а новый клип мгновенно ставил первый кадр — видимый рывок.
      const clip = createVRMAnimationClip(anim, vrm);
      const action = animMixer.clipAction(clip);
      action.setLoop(THREE.LoopOnce, 1);
      action.clampWhenFinished = false;
      action.reset().play();
      action.fadeIn(0.35);
      activeVrmaAction.fadeOut(0.35);
      activeVrmaAction = action;
      console.log('[nima] VRMA crossfade ->', fileName);
    } else {
      stopVrma();
      animating = true;
      animMixer = new THREE.AnimationMixer(vrm.scene);
      animMixer.addEventListener('finished', finishVrma);
      const clip = createVRMAnimationClip(anim, vrm);
      const action = animMixer.clipAction(clip);
      action.setLoop(THREE.LoopOnce, 1);
      action.clampWhenFinished = false;
      activeVrmaAction = action;
      action.play();
      // Первый кадр VRMA телепортирует кости из стойки в позу анимации — без
      // сброса пружины получают рывок и подол взрывается вверх. reset() ставит
      // цепочки в исходное состояние с нулевой скоростью.
      vrm.springBoneManager?.reset?.();
    }
    console.log('[nima] VRMA play:', fileName);
    showError('');
  } catch (err) {
    console.error('[nima] VRMA failed:', err);
    animating = false;
    showError('Не удалось проиграть ' + fileName + ': ' + err);
  }
}

let loadInFlight = false;  // init+state могут позвать одновременно — не парсим дважды
let pendingVrma = null;    // action пришёл раньше модели — играем после загрузки

// --- физика тела: коллайдеры + податливость пружин ---
// Волосы/хвост/юбка — пружинные цепочки; без коллайдеров они проваливаются
// сквозь голову/тело/ноги. Коллайдеры-сферы вешаются на СЫРЫЕ кости (joint.bone
// — сырое дерево), каждая сфера — в происхождении кости. Один набор сфер
// привязывается ко всем цепочкам (порядок 70×16 коллизий — дёшево).
function setupSpringColliders(v) {
  const mgr = v.springBoneManager;
  if (!mgr?.joints || !v.humanoid) return;
  // [humanoid-кость, радиус]
  const SPHERES = [
    ['head', 0.09], ['neck', 0.05], ['upperChest', 0.105], ['chest', 0.105],
    ['spine', 0.10], ['hips', 0.13],
    ['leftUpperArm', 0.055], ['leftLowerArm', 0.05], ['leftHand', 0.045],
    ['rightUpperArm', 0.055], ['rightLowerArm', 0.05], ['rightHand', 0.045],
    ['leftUpperLeg', 0.11], ['leftLowerLeg', 0.085], ['leftFoot', 0.06],
    ['rightUpperLeg', 0.11], ['rightLowerLeg', 0.085], ['rightFoot', 0.06],
  ];
  // Кость-пара, на середине которой ставится вторая сфера: одиночная сфера
  // в суставе оставляла щели — подол прошивал бедро/голень насквозь, а
  // предплечья/кисти прошивали подол в покое («платье сквозь руки»).
  const MID_SPHERES = ['leftUpperLeg', 'rightUpperLeg', 'leftLowerLeg', 'rightLowerLeg',
    'leftUpperArm', 'rightUpperArm', 'leftLowerArm', 'rightLowerArm'];
  const colliders = [];
  for (const [name, radius] of SPHERES) {
    const bone = v.humanoid.getRawBoneNode(name);
    if (!bone) continue;
    const collider = new VRMSpringBoneCollider(
      new VRMSpringBoneColliderShapeSphere({ radius, offset: new THREE.Vector3() }));
    bone.add(collider);
    colliders.push(collider);
    if (MID_SPHERES.includes(name)) {
      // середина кости = половина локальной позиции дочерней кости
      const kid = bone.children[0];
      if (kid && kid.position.length() > 0.05) {
        const mid = kid.position.clone().multiplyScalar(0.5);
        const rMid = kid.position.length() * 0.5;
        const c2 = new VRMSpringBoneCollider(
          new VRMSpringBoneColliderShapeSphere({ radius: Math.max(rMid, radius), offset: mid }));
        bone.add(c2);
        colliders.push(c2);
      }
    }
  }
  const group = { colliders };
  let n = 0;
  for (const joint of mgr.joints) {
    // Грудь не сталкиваем с грудной клеткой (иначе собственные коллайдеры
    // выдавят её вечно вперёд); остальным цепочкам — весь набор.
    if (/bust/i.test(String(joint.bone?.name || ''))) continue;
    joint.colliderGroups.push(group);
    n++;
  }
  console.log('[nima] colliders: сфер =', colliders.length, 'на цепочках =', n);
}

// РОДНЫЕ НАСТРОЙКИ ПРУЖИН (v14.8.18). Попытки «оживить» цепочки вручную
// (stiffness ×0.12 в v14.8.15, dragForce ×0.5/×0.7 в v14.8.16) давали либо
// кол, либо «вертолёты»: недодемпфированные волосы/хвост машут при любом
// движении. Автора модели это уже откалибровано — не трогаем вообще.
// Живая реакция на перетаскивание/поворот даётся инерцией (tickInertia).
function livenSpringbones(v) {
  // Тюнер по группам (v14.8.19): у модели grav=0 всюду, вся динамика — из
  // stiffness/dragForce. Родные значения дают проблемы: юбка drag=0.05 —
  // «отрывает» при движении; уши stiffness=1.5 — задубевшие; волосы drag=0.4 —
  // слишком хлёсткие. Хвост (drag=0.5) пользователь одобрил — эталон.
  const mgr = v.springBoneManager;
  if (!mgr?.joints) return;
  let nSkirt = 0, nEar = 0, nHair = 0;
  for (const joint of mgr.joints) {
    const nName = String(joint.bone?.name || '');
    if (/tail|foxtail/i.test(nName)) continue;          // хвост — эталон, не трогаем
    if (/catear|ear/i.test(nName)) {
      // уши: мягче и с демпфером — качаются при движении, как хвост
      joint.settings.stiffness = Math.min(joint.settings.stiffness, 0.8);
      joint.settings.dragForce = Math.max(joint.settings.dragForce, 0.35);
      nEar++;
    } else if (/hair/i.test(nName)) {
      // волосы: чуть спокойнее хлёсткости (drag 0.4 -> 0.45)
      joint.settings.dragForce = Math.max(joint.settings.dragForce, 0.45);
      nHair++;
    } else if (inertiaClass(nName) === 'skirt') {
      // v14.8.42: классификатор как в инерции (раньше /skirt|coat/ ловил 18
      // из 22 суставов — 4 оставались с drag=0.05 и прошивали ноги).
      joint.settings.dragForce = Math.max(joint.settings.dragForce, 0.4);
      nSkirt++;
    }
  }
  console.log('[nima] springs: юбка =', nSkirt, 'уши =', nEar, 'волосы =', nHair, '(хвост родной)');
}

async function loadOutfit(fileName) {
  if (!window.nima || !fileName || fileName === currentOutfit || loadInFlight) return;
  loadInFlight = true;
  try {
    console.log('[nima] loadOutfit:', fileName, '(было:', currentOutfit || 'пусто', ')');
    const buf = await window.nima.readVrm(fileName);
    if (!buf) { showError('main не отдал файл ' + fileName); return; }
    console.log('[nima] файл получен, байт:', buf.byteLength);
    const gltf = await loader.parseAsync(buf, '');
    const next = gltf.userData.vrm;
    if (!next) { showError('В файле нет VRM-данных: ' + fileName); return; }
    VRMUtils.removeUnnecessaryVertices(next.scene);
    VRMUtils.combineSkeletons(next.scene);
    VRMUtils.rotateVRM0(next);           // лицом к камере
    applyRestPose(next);                 // стойка вместо T-позы
    next.scene.traverse((o) => { o.frustumCulled = false; });
    if (vrm) scene.remove(vrm.scene);
    scene.add(next.scene);               // ← было пропущено: модель не рисовалась
    vrm = next;
    setupIdleBones(next);                // кости для живой idle-позы
    setupSafeProceduralIdle(next);
    currentOutfit = fileName;
    try { localStorage.setItem('nima_last_outfit', fileName); } catch (_) {}
    baseRotationY = next.scene.rotation.y;
    vrm.scene.position.set(charOffset.x, charOffset.y, 0);
    vrm.scene.updateMatrixWorld(true);   // мировые позиции костей до jelly-патча
    setupJelly(next);
    livenSpringbones(next);
    setupSpringColliders(next);
    // Смена наряда = НОВАЯ модель = новые пружинные цепочки. Кэши ниже ссылались
    // на пружины прежней модели: tickInertia/tickClothSpin молча переставали
    // работать до перезагрузки страницы («переключил наряд — механики умерли»).
    inertiaJoints = null;
    clothJoints = null;
    clothLifted = false;
    inertiaPrev = false;
    _prevPos.set(0, 0, 0); _prevVel.set(0, 0, 0); _accSmooth.set(0, 0, 0);
    _omegaSmV.set(0, 0, 0); _alphaSmV.set(0, 0, 0);
    // ?nospring — диагностика: полностью выключить обновление springbone-физики
    // (волосы/юбка/цепочки). Если баг «скомканного меша» исчезает с этим флагом —
    // виновата физика, если остаётся — рендер/скиннинг.
    if (new URLSearchParams(location.search).has('nospring') && next.springBoneManager) {
      next.springBoneManager.update = function () {};
      console.log('[nima] springbone update ВЫКЛЮЧЕН (?nospring)');
    }
    // Ловушка на подмену position-атрибута: баг «пол модельки пропало» = у мешей
    // после VRMA внезапно 0 вершин. Оборачиваем атрибут в get/set и пишем стек
    // того, кто его подменяет (первый раз).
    next.scene.traverse((o) => {
      if (!o.isSkinnedMesh || !o.geometry || !o.geometry.attributes) return;
      const attrs = o.geometry.attributes;
      let cur = attrs.position;
      if (!cur) return;
      // count могут обнулить на месте (attr.count = 0) — тоже ловим
      let curCount = cur.count;
      try {
        Object.defineProperty(cur, 'count', {
          configurable: true,
          get() { return curCount; },
          set(v) {
            console.warn('[nima] TRAP count set on', o.name,
              'newCount=', v, 'stack=', new Error().stack);
            curCount = v;
          },
        });
      } catch (e) { /* frozen — не критично */ }
      Object.defineProperty(attrs, 'position', {
        configurable: true,
        get() { return cur; },
        set(v) {
          console.warn('[nima] TRAP position attr set on', o.name,
            'newCount=', v && v.count, 'stack=', new Error().stack);
          cur = v;
        },
      });
      // Геометрию могут подменить ЦЕЛИКОМ (mesh.geometry = ...) — ловим и это
      let curGeom = o.geometry;
      Object.defineProperty(o, 'geometry', {
        configurable: true,
        get() { return curGeom; },
        set(v) {
          console.warn('[nima] TRAP geometry set on', o.name,
            'newCount=', v && v.attributes && v.attributes.position
              ? v.attributes.position.count : null,
            'stack=', new Error().stack);
          curGeom = v;
        },
      });
    });
    console.log('[nima] модель готова:', fileName, '| rotation.y =', baseRotationY.toFixed(2));
    showError('');
    if (pendingVrma) {
      const queued = pendingVrma;   // имя .vrma, с outfit (.vrm) не совпадает
      pendingVrma = null;
      playVrma(queued);
    }
  } catch (err) {
    console.error('[nima] parse failed:', err);
    showError('Не удалось разобрать ' + fileName + ': ' + err);
  } finally {
    loadInFlight = false;
  }
}

// --- желейная физика груди: вся масса, а не только spring bones на сосках ---
// Деформация вершинная (шейдер): вес вершины — гладкое падение от опорной
// точки (середина bust-костей, иначе chest) по эллипсоиду. Смещение считает
// CPU-симуляция пружины с демпфером по осям, возбуждение — реальное ускорение
// опорной кости (движения, VRMA, перетаскивание). ?jelly=0 — выключить,
// ?jellylog — писать смещение в консоль (renderer_error.log).
// --- желейная физика груди: ДВЕ массы (левая/правая), не только соски ---
// Урок v14.0.2: один эллипс «между сосками» с большим радиусом двигал грудину,
// а соски оставались на краю спада веса. Теперь у каждой груди свой эллипс с
// центром в середине кости (Bust1→Bust2, чуть к соску) и радиусом от её длины:
// грудина между эллипсами почти не двигается, сосок — в полном весе.
// Пружины у сторон независимые (левая чуть мягче правой) — живой рассинхрон.
// ?jelly=0 — выключить, ?jellylog — лог смещений в renderer_error.log.
const JELLY = {
  gain: 0.5,                      // насколько ускорения «встряхивают»
  animGain: 0.9,                  // возбуждение от анимаций (ускорение chest в системе модели)
  gravity: 0.6,                  // провисание: масштабировано под пружину (0.6/75 ≈ тот же саг, что 0.4/45)
  stiffness: [55, 75, 65],       // v14.8.43: по фидбеку «чрезмерно желейные» — середина между 35/45/40 и старыми 70/95/85
  damping: 2.2,                  // колебание есть, но гаснет быстрее, чем на 1.6
  maxOffset: 0.048,              // предохранитель: смещение относительно объёма груди
  radiusFactor: 1.0,             // радиус влияния = 1.0 × длина кости груди (1.7 заливало весь лиф платья)
  fallbackRadii: new THREE.Vector3(0.075, 0.07, 0.075),
};
const jellyEntries = [];  // { mesh, uniforms } по каждому патчу-материалу
const jellyState = {
  sides: [],                // [{bone1, bone2, offset, vel, prevPos, prevVel, k}]
  anchor: null,             // fallback-кость (chest), если bust-костей не нашли
  root: null,               // scene модели — для локальной системы (аним-возбуждение)
};
const JELLY_ACTIVE = new URLSearchParams(location.search).get('jelly') !== '0';
const JELLY_LOG = new URLSearchParams(location.search).has('jellylog');

const JELLY_UNIFORM_DECL = `
uniform vec3 uJellyCenterL;
uniform vec3 uJellyCenterR;
uniform vec3 uJellyRadii;
uniform vec3 uJellyOffsetL;
uniform vec3 uJellyOffsetR;
uniform float uJellyAmount;
`;
// Вклейка после скиннинга: position — бинд-поза (стабильный вес), transformed —
// уже скиннутая вершина; outline-путь ниже тоже читает transformed, так что
// обводка поедет вместе с массой. a/b — вклады сторон; в зоне перекрытия
// смещение смешивается и нормируется, чтобы не удваивалось.
const JELLY_CHUNK = `
{
  vec3 jL = ( position - uJellyCenterL ) / uJellyRadii;
  vec3 jR = ( position - uJellyCenterR ) / uJellyRadii;
  float a = 1.0 - smoothstep( 0.3, 1.0, length( jL ) );
  float b = 1.0 - smoothstep( 0.3, 1.0, length( jR ) );
  a *= a; b *= b;
  float s = a + b;
  if ( s > 0.0001 ) {
    float jY = ( jL.y * a + jR.y * b ) / s;      // низ массы тяжелее
    float jW = min( 1.0, s ) * ( 1.0 + 0.4 * clamp( -jY, 0.0, 1.0 ) );
    transformed += ( uJellyOffsetL * a + uJellyOffsetR * b ) / s
                   * ( jW * uJellyAmount );
  }
}
`;

function patchJellyMaterial(mat, mesh, centerL, centerR, radii) {
  const uniforms = {
    uJellyCenterL: { value: centerL.clone() },
    uJellyCenterR: { value: centerR.clone() },
    uJellyRadii: { value: radii.clone() },
    uJellyOffsetL: { value: new THREE.Vector3() },
    uJellyOffsetR: { value: new THREE.Vector3() },
    uJellyAmount: { value: JELLY_ACTIVE ? 1 : 0 },
  };
  const prevCompile = mat.onBeforeCompile;
  mat.onBeforeCompile = (shader, renderer) => {
    if (prevCompile) prevCompile(shader, renderer);   // MToon препендит свои defines
    Object.assign(shader.uniforms, uniforms);
    if (!shader.vertexShader.includes('uJellyOffsetL')) {
      shader.vertexShader = JELLY_UNIFORM_DECL + shader.vertexShader.replace(
        '#include <skinning_vertex>',
        '#include <skinning_vertex>\n' + JELLY_CHUNK);
    }
  };
  // чужой cacheKey (у MToon свой) сохраняем — иначе три смешает варианты шейдера
  const prevKeyFn = mat.customProgramCacheKey;
  mat.customProgramCacheKey = () => 'nimaJelly|' + (prevKeyFn ? prevKeyFn.call(mat) : '');
  jellyEntries.push({ mesh, uniforms });
}

function makeJellySide(bone1, bone2, kScale) {
  return {
    bone1, bone2, kScale,
    offset: new THREE.Vector3(), vel: new THREE.Vector3(),
    prevPos: null, prevVel: new THREE.Vector3(),
  };
}

function setupJelly(nextVrm) {
  jellyEntries.length = 0;
  jellyState.sides = [];
  jellyState.anchor = null;
  jellyState.root = nextVrm ? nextVrm.scene : null;
  jellyAnim.has = false;      // сброс трекера аним-возбуждения при смене модели
  if (!JELLY_ACTIVE) return;

  // кости груди: base (Bust1 у грудной клетки) и tip (Bust2, у соска)
  const b1 = [], b2 = [];
  nextVrm.scene.traverse((o) => {
    if (!o.isBone) return;
    if (/bust1$/i.test(o.name)) b1.push(o);
    if (/bust2$/i.test(o.name)) b2.push(o);
  });
  const chest = nextVrm.humanoid ? nextVrm.humanoid.getRawBoneNode('chest') : null;
  // НИКОГДА не Bust-кость (v14.8.36): якорь-bust превращает «относительное
  // возбуждение» в схему «грудь против груди» — противофазный вертолёт.
  jellyState.anchor = chest || b1[0];
  if (b1.length < 2) {
    console.warn('[nima] jelly: bust-костей не найдено (', b1.length, ') — физика выключена');
    return;
  }
  // стороны сортируем по локальному x кости — левая/правая не важны, важно
  // что это ДВЕ разные массы с независимыми пружинами
  b1.sort((p, q) => p.position.x - q.position.x);
  const tipFor = (base) => b2.find((t) => t.name.toLowerCase().includes(
    base.name.toLowerCase().includes('_l_') ? '_l_' : '_r_'))
    || b2[b1.indexOf(base) % Math.max(1, b2.length)] || base;
  jellyState.sides = [
    makeJellySide(b1[0], tipFor(b1[0]), 0.95),
    makeJellySide(b1[1], tipFor(b1[1]), 1.0),
  ];

  // радиус эллипса от длины кости груди — масштаб модели учитывается сам
  const p1 = new THREE.Vector3(), p2 = new THREE.Vector3();
  const s0 = jellyState.sides[0];
  s0.bone1.getWorldPosition(p1);
  s0.bone2.getWorldPosition(p2);
  const len = p1.distanceTo(p2);
  const r = Math.min(0.12, Math.max(0.045, len * JELLY.radiusFactor));
  const radii = new THREE.Vector3(r, r * 0.92, r);

  // каждому материалу — центры сторон в локальном пространстве ЕГО меша.
  // РАДИУСЫ тоже переводим в локальное пространство: до v14.8.23 они были в
  // мировых единицах, а position/центры — в локальных (модель отмасштабирована,
  // scale≈0.06): нормализованная координата jL раздувалась, маска
  // smoothstep насыщалась на всём лифе, и смещение груди деформировало платье
  // конусом («сиська-вертолёт» при перетаскивании).
  const matDone = new Map();   // material → mesh
  const _jScale = new THREE.Vector3();
  const radiiLocal = new THREE.Vector3();
  nextVrm.scene.traverse((o) => {
    if (!o.isMesh && !o.isSkinnedMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (!m || matDone.has(m)) continue;
      matDone.set(m, o);
      o.getWorldScale(_jScale);
      radiiLocal.set(radii.x / Math.max(1e-6, _jScale.x),
        radii.y / Math.max(1e-6, _jScale.y), radii.z / Math.max(1e-6, _jScale.z));
      const cL = o.worldToLocal(jellyCenterWorld(jellyState.sides[0], p1, p2).clone());
      const cR = o.worldToLocal(jellyCenterWorld(jellyState.sides[1], p1, p2).clone());
      patchJellyMaterial(m, o, cL, cR, radiiLocal);
    }
  });
  console.log('[nima] jelly: патчу материалов =', matDone.size,
    '| радиус =', r.toFixed(3),
    '| стороны =', jellyState.sides.map((s) => s.bone1.name).join(' / '));
}

// центр массы стороны: середина Bust1→Bust2, смещённая к соску (60%)
const _jc1 = new THREE.Vector3(), _jc2 = new THREE.Vector3();
function jellyCenterWorld(side, out1, out2) {
  side.bone1.getWorldPosition(out1);
  side.bone2.getWorldPosition(out2);
  return out1.lerp(out2, 0.6);
}

const _jPos = new THREE.Vector3(), _jVel = new THREE.Vector3(),
      _jAcc = new THREE.Vector3(), _jInv = new THREE.Matrix4(),
      _jr = new THREE.Vector3(), _jRigid = new THREE.Vector3(),
      _jTmp = new THREE.Vector3(), _jTmp2 = new THREE.Vector3(),
      _jW = new THREE.Vector3(), _jVL = new THREE.Vector3();

// v14.8.43: возбуждение от АНИМАЦИЙ. VRMA двигает кости груди ОТНОСИТЕЛЬНО
// корня (приседания, прыжки, взмахи) — пивот-трекинг корня этого не видит,
// и при анимациях грудь «не прыгала». Берём ускорение кости chest в СИСТЕМЕ
// МОДЕЛИ (не мира: перетаскивание корня уже учтено в _accSmooth — не
// дублируем). Классическая цепочка позиция→скорость→EMA→ускорение здесь
// безопасна: анимационный боб — поступательное движение, разворота вектора
// скорости, как на вращении (урок v14.8.40), нет.
const jellyAnim = { has: false, p: new THREE.Vector3(), v: new THREE.Vector3(),
  a: new THREE.Vector3() };
const _animP = new THREE.Vector3(), _animV2 = new THREE.Vector3();

function jellyAnimAccel(dt) {
  const anchor = jellyState.anchor;
  if (!anchor || !jellyState.root) return null;
  anchor.getWorldPosition(_animP);
  jellyState.root.worldToLocal(_animP);
  if (!jellyAnim.has) {
    jellyAnim.has = true;
    jellyAnim.p.copy(_animP);
    jellyAnim.v.set(0, 0, 0);
    jellyAnim.a.set(0, 0, 0);
    return null;
  }
  const inv = 1 / Math.max(dt, 1 / 120);
  _jW.subVectors(_animP, jellyAnim.p).multiplyScalar(inv);    // сырая скорость
  _animV2.copy(jellyAnim.v).lerp(_jW, 0.35);                  // EMA скорости
  _jTmp.subVectors(_animV2, jellyAnim.v).multiplyScalar(inv); // сырое ускорение
  jellyAnim.a.lerp(_jTmp, 0.35);                              // EMA ускорения
  jellyAnim.v.copy(_animV2);
  jellyAnim.p.copy(_animP);
  if (jellyAnim.a.length() > 14) jellyAnim.a.setLength(14);   // шипы VRMA-миксера
  return jellyAnim.a;
}

function updateJelly(delta) {
  if (!jellyEntries.length || !jellyState.sides.length) return;
  const dt = Math.min(delta, 1 / 30);

  // v14.8.40: псевдо-сила считается НАПРЯМО из сглаженных a/α/ω модели —
  // НЕ дифференцированием позиции центра груди. Старый путь (скорость центра
  // → EMA → ускорение) на вращении разворачивал силу на 60–90° от радиуса:
  // вектор скорости центра крутится с ω, два каскада фильтров + пружина
  // (резонанс ~12 рад/с) давали фазовый лаг — левая грудь уходила ВНУТРЬ
  // (телеметрия: radial −0.9), правая наружу, плюс шипы при остановке.
  // Псевдо-ускорение неинерциальной системы: a_пивот + α×r + ω×(ω×r);
  // сила = −a. Центростремительный член оставляем в сумме — с минусом
  // в силе он и есть центробежная «наружу».
  const animA = jellyAnimAccel(dt);   // ускорение chest в системе модели (VRMA)
  for (const side of jellyState.sides) {
    jellyCenterWorld(side, _jPos, _jc2);
    _jr.subVectors(_jPos, _pivot);
    _jW.crossVectors(_omegaSmV, _jr);            // ω×r — тангенциальная скорость
    _jAcc.copy(_accSmooth)
      .add(_jTmp.crossVectors(_alphaSmV, _jr))   // α×r — угловой разгон
      .add(_jTmp2.crossVectors(_omegaSmV, _jW)); // ω×(ω×r) — центростремительная
    if (animA) _jAcc.addScaledVector(animA, JELLY.animGain);

    if (_jAcc.length() > 10) _jAcc.setLength(10);

    const o = side.offset, vel = side.vel;
    for (let i = 0; i < 3; i++) {
      let force = -_jAcc.getComponent(i) * JELLY.gain;
      if (i === 1) force -= JELLY.gravity;           // провисание вниз
      const k = JELLY.stiffness[i] * side.kScale;
      const acc = -k * o.getComponent(i)
                - JELLY.damping * vel.getComponent(i) + force;
      vel.setComponent(i, vel.getComponent(i) + acc * dt);
      o.setComponent(i, o.getComponent(i) + vel.getComponent(i) * dt);
    }
    if (o.length() > JELLY.maxOffset) o.setLength(JELLY.maxOffset);
  }

  // мировые смещения сторон → локальное пространство каждого меша
  for (const e of jellyEntries) {
    _jInv.copy(e.mesh.matrixWorld).invert();
    _jInv.elements[12] = _jInv.elements[13] = _jInv.elements[14] = 0;
    e.uniforms.uJellyOffsetL.value.copy(jellyState.sides[0].offset)
      .applyMatrix4(_jInv);
    e.uniforms.uJellyOffsetR.value.copy(jellyState.sides[1].offset)
      .applyMatrix4(_jInv);
  }

  // v14.8.42: radial-телеметрия всегда, вместе с inertia-логом (активность 1с /
  // покой 10с), чтобы видеть знак смещения в ЖИВОМ сценарии пользователя.
  updateJelly._logAcc = (updateJelly._logAcc || 0) + dt;
  if (updateJelly._logAcc > 1) {
    updateJelly._logAcc = 0;
      const parts = jellyState.sides.map((s) => {
        s.bone1.getWorldPosition(_jc1);
        s.bone2.getWorldPosition(_jc2);
        const outDir = _jRigid.subVectors(_jc2, _jc1).normalize(); // наружу груди
        const radial = _jVL.copy(s.offset).dot(outDir);
        return (radial >= 0 ? '+' : '') + radial.toFixed(4) + '/' + _jVL.length().toFixed(4);
      });
      console.log('[nima] jelly: radialL/R =', parts.join(' / '),
        '|w| =', _omegaSmV.length().toFixed(2),
        '|acc| =', _accSmooth.length().toFixed(2),
        '|animA| =', (animA ? animA.length() : 0).toFixed(2));
  }
}

// --- инерция: волосы, хвост, уши и одежда реагируют на движение модели ---
// Пружины three-vrm чувствуют только изменение МИРОВЫХ позиций костей.
// Сдвиг модели мышью (перетаскивание) двигает все кости одинаково — пружины
// не чувствуют ничего, и волосы/хвост/юбка висят как приклеенные (грудь
// шевелилась, потому что у неё свой jelly-шейдер). Решение: считаем
// ускорение модели (линейное + вращательное) и подмешиваем «инерционную
// гравитацию» — противоположно ускорению, как в неинерциальной системе
// отсчёта. gravityDir в three-vrm складывается в мировых координатах
// (addScaledVector в VRMSpringBoneJoint.update), поэтому мировой вектор
// можно подставлять прямо в настройки, а на выходе восстанавливать исходные.
// Прежний механизм «подъёма ткани при вращении» (гравитация вверх) удалён —
// он задирал платье «ракетой» от любого быстрого поворота.
let inertiaJoints = null;      // [{joint, power, dir}] — исходные настройки
let inertiaOn = false;         // сейчас подмешана инерция
let inertiaPrev = false;       // есть валидный предыдущий кадр
// Инерция по группам (v14.8.19): ускорение почти реальное (масштаб 1),
// но у каждой группы свой потолок силы — как у «эталонного» хвоста.
// На быстром вращении центробежная составляющая честно поднимает подол,
// хвост и волосы отстают с запаздыванием.
// ВАЖНО: гравитация пружин модели = 0, восстанавливающая сила — только
// stiffness (0.5-0.8). Потолок инерционной силы напрямую задаёт УГОЛ застоя
// подола/хвоста: 22 единицы клали подол горизонтально («полка»). Потолки
// подобраны под stiffness: умеренный подъём на спине, без застывших «крыльев».
// Хвост возвращён (v14.8.21): живой хвост при движении давала именно добавка;
// «естественного запаздывания» пружин при переносе модели недостаточно.
// v14.8.35: skirt/hair/ear урезаны — прежние потолки (skirt 0.9 ≈ 1g вбок)
// при резкой мышиной драге/развороте вгоняли подол в ноги и в бок.
const INERTIA_LIMITS = { skirt: 0.5, hair: 0.5, tail: 1.4, ear: 0.35 };
const INERTIA_SCALE = { skirt: 0.16, hair: 0.14, tail: 0.25, ear: 0.12 };
function inertiaClass(boneName) {
  const n = String(boneName || '');
  return /tail|foxtail/i.test(n) ? 'tail'
    : /catear|ear/i.test(n) ? 'ear'
    : /hair/i.test(n) ? 'hair' : 'skirt';
}
const _prevPos = new THREE.Vector3();
const _prevVel = new THREE.Vector3();   // (устарело, оставлено для сбросов)
const _vel = new THREE.Vector3();
const _velF = new THREE.Vector3();      // EMA скорости (фильтр до d/dt)
const _velFPrev = new THREE.Vector3();
const _omegaF = new THREE.Vector3();    // EMA угловой скорости
const _omegaFPrev = new THREE.Vector3();
const _acc = new THREE.Vector3();
const _accSmooth = new THREE.Vector3();
const _prevRot = { x: 0, y: 0 };
const _prevOmega = { y: 0 };
let _omegaSm = 0, _alphaSm = 0;   // сглаженные угловая скорость и ускорение
const _pivot = new THREE.Vector3();
const _pvt = new THREE.Vector3();    // мировая позиция пивота этого кадра
const _inertiaPos = new THREE.Vector3();
const _r = new THREE.Vector3();
const _aJoint = new THREE.Vector3();
const _gExtra = new THREE.Vector3();
const _gTotal = new THREE.Vector3();
// ВЕКТОРНАЯ угловая скорость (v14.8.23): раньше мерили только rotation.y и
// считали вращение строго в горизонтальной плоскости. При наклоне мышью
// (userRotX ≠ 0) реальная ось вращения наклонена, и центробежная сила уходила
// вбок («сносит не по центробежной, а в сторону»). Теперь ω — полный вектор
// из дельты кватерниона сцены, формула честная для любой оси.
const _curQ = new THREE.Quaternion();
const _qDelta = new THREE.Quaternion();
const _prevQ = new THREE.Quaternion();
const _omega = new THREE.Vector3();
const _alpha = new THREE.Vector3();
const _omegaSmV = new THREE.Vector3();
const _alphaSmV = new THREE.Vector3();
const _prevOmegaV = new THREE.Vector3();
const _alphaEff = new THREE.Vector3();
const _tmpV = new THREE.Vector3();
function wrapAngle(d) {
  return ((d + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
}
function collectInertiaJoints() {
  inertiaJoints = [];
  const joints = vrm?.springBoneManager?.joints;
  if (!joints) return;
  const stat = {};
  for (const joint of joints) {
    const nName = String(joint.bone?.name || '');
    const cls = /tail|foxtail/i.test(nName) ? 'tail'
      : /catear|ear/i.test(nName) ? 'ear'
      : /hair/i.test(nName) ? 'hair' : 'skirt';
    inertiaJoints.push({ joint, power: joint.settings.gravityPower,
      dir: joint.settings.gravityDir.clone(), cls: inertiaClass(nName) });
    const st = stat[cls] = stat[cls] || { n: 0, gMin: 9e9, gMax: -9e9, sMin: 9e9, sMax: -9e9, dMin: 9e9, dMax: -9e9 };
    st.n++;
    st.gMin = Math.min(st.gMin, joint.settings.gravityPower); st.gMax = Math.max(st.gMax, joint.settings.gravityPower);
    st.sMin = Math.min(st.sMin, joint.settings.stiffness); st.sMax = Math.max(st.sMax, joint.settings.stiffness);
    st.dMin = Math.min(st.dMin, joint.settings.dragForce); st.dMax = Math.max(st.dMax, joint.settings.dragForce);
  }
  console.log('[nima] inertia:', Object.entries(stat).map(([k, v]) =>
    `${k}: n=${v.n} grav=${v.gMin.toFixed(2)}..${v.gMax.toFixed(2)} stiff=${v.sMin.toFixed(2)}..${v.sMax.toFixed(2)} drag=${v.dMin.toFixed(3)}..${v.dMax.toFixed(3)}`).join(' | '));
}
function tickInertia(delta) {
  if (!vrm?.scene || delta <= 0) return;
  const sc = vrm.scene;
  if (!inertiaJoints) collectInertiaJoints();
  if (!inertiaJoints.length) return;
  // Линейные скорость/ускорение — ТОЧКИ ПИВОТА (0,PIVOT_Y,0) в мире, не
  // корня (v14.8.36). При наклоне корень перемещается компенсацией пивота
  // (pivotDZ/pivotDY), и ускорение корня = ложная сила, сносящая юбку вбок
  // при любом вертикальном/наклонном вращении (при горизонтальном кручении
  // корень неподвижен — потому «по горизонтали работало»). Формула
  // a = a_пивот + α×r + ω×(ω×r) консистентна только с ускорением пивота.
  _pvt.set(0, PIVOT_Y, 0).applyQuaternion(sc.quaternion).add(sc.position);
  _vel.subVectors(_pvt, _prevPos).divideScalar(delta);
  _prevPos.copy(_pvt);
  // Фильтр скорости ДО дифференцирования (v14.8.36): дрожание кадра (dt) при
  // двойном дифференцировании сырой позиции взрывается — |acc| до ~10 при
  // реальных ~1.4, юбку «уносило в бок» и вгоняло в ноги. Сначала EMA
  // скорости (гасит dt-шум), только потом ускорение.
  _velF.lerp(_vel, Math.min(1, delta * 6));
  _acc.subVectors(_velF, _velFPrev).divideScalar(delta);
  _velFPrev.copy(_velF);
  // угловая скорость и ускорение — из кватерниона сцены (любая ось вращения)
  _curQ.copy(sc.quaternion);
  let omegaOk = inertiaPrev;
  if (inertiaPrev) {
    _qDelta.copy(_curQ).multiply(_prevQ.clone().invert());
    if (_qDelta.w < 0) { _qDelta.x *= -1; _qDelta.y *= -1; _qDelta.z *= -1; _qDelta.w *= -1; }
    const half = Math.min(1, Math.max(-1, _qDelta.w));
    const ang = 2 * Math.acos(half);
    const s = Math.sqrt(Math.max(0, 1 - half * half));
    if (s < 1e-6) {
      _omega.set(0, 0, 0);
    } else {
      _omega.set(_qDelta.x / s, _qDelta.y / s, _qDelta.z / s)
        .multiplyScalar(wrapAngle(ang) / delta);
    }
    // ω тоже через EMA до дифференцирования: α из сырой ω при дрожащем dt
    // даёт α-шипы, хлещущие ткань на развороте мыши
    _omegaF.lerp(_omega, Math.min(1, delta * 6));
    _alpha.subVectors(_omegaF, _omegaFPrev).divideScalar(delta);
    _omegaFPrev.copy(_omegaF);
  } else {
    _omega.set(0, 0, 0); _alpha.set(0, 0, 0);
    _omegaF.set(0, 0, 0); _omegaFPrev.set(0, 0, 0);
    _velF.set(0, 0, 0); _velFPrev.set(0, 0, 0);
  }
  _prevQ.copy(_curQ);
  inertiaPrev = true;
  // ГЛУБОКОЕ сглаживание всего: сила меняется непрерывно — нарастает, спадает,
  // в покое = 0. Дискретный гейт (вкл/выкл каждый кадр) раньше дёргал ткань.
  const k = Math.min(1, delta * 4);
  _accSmooth.lerp(_acc, k);
  _omegaSmV.lerp(_omega, k);
  _alphaSmV.lerp(_alpha, k);
  // мягкие мёртвые зоны: ниже порога вклад = 0, выше плавно растёт
  const ramp = (v, lo, hi) => {
    const t = Math.min(1, Math.max(0, (Math.abs(v) - lo) / (hi - lo)));
    return t * t * (3 - 2 * t);   // smoothstep
  };
  // пороги под ЧИСТЫЙ сигнал (после EMA-фильтра): шумовой пол ~0.15,
  // реальный драг даёт 1–5 и выше
  const aRamp = ramp(_accSmooth.length(), 0.7, 1.6);
  const wRamp = ramp(_omegaSmV.length(), 1.0, 2.2);
  // v14.8.42: телеметрия ВСЕГДА (не только ?jellylog): активность — раз в 1 с,
  // покой — раз в 10 с. Это единственный способ видеть живой сценарий пользователя.
  tickInertia._logAcc = (tickInertia._logAcc || 0) + delta;
  const _active = _accSmooth.length() > 0.3 || _omegaSmV.length() > 0.5;
  if (tickInertia._logAcc > (_active ? 1 : 10)) {
    tickInertia._logAcc = 0;
    console.log('[nima] inertia: |acc|=', _accSmooth.length().toFixed(2),
      'aRamp=', aRamp.toFixed(2), '|w|=', _omegaSmV.length().toFixed(2),
      'wRamp=', wRamp.toFixed(2), '|a|=', _alphaSmV.length().toFixed(2),
      'rotY=', vrm.scene.rotation.y.toFixed(2),
      'spin=', ((tickInertia._testSpin || 0) % (2 * Math.PI)).toFixed(2),
      'wRaw=', _omega.length().toFixed(2), 'dlt=', delta.toFixed(3));
  }
  // α-гейт поднят (1.8,3.6 → 2.8,5.5): рывок мышью на развороте давал
  // α-шип, который хлестал юбку в сторону, а не наружу по центробежной.
  _alphaEff.copy(_alphaSmV).multiplyScalar(ramp(_alphaSmV.length(), 2.8, 5.5));
  // пивот вращения — точка (0,PIVOT_Y,0) модели в мире (та же, что выше)
  _pivot.copy(_pvt);
  for (const { joint, power, dir, cls } of inertiaJoints) {
    const scale = INERTIA_SCALE[cls], lim = INERTIA_LIMITS[cls];
    if (!scale) continue;
    // Во время процедурного виляния хвостом его пружины отданы жесту:
    // инерция перезаписала бы gravityDir каждый кадр (жест глох).
    if (cls === 'tail' && safeProceduralIdle?.tailGestureActive) continue;
    joint.bone.getWorldPosition(_inertiaPos);
    _r.subVectors(_inertiaPos, _pivot);
    // Кинематическое ускорение точки жёсткого тела:
    //   a = a_центра + α×r + ω×(ω×r)  (последнее — центростремительное, ВНУТРЬ)
    // Псевдосила в системе модели = −a: ткань отстаёт от разгона, на
    // вращении уносится наружу (центробежная) и против углового разгона.
    _aJoint.copy(_accSmooth).multiplyScalar(aRamp);
    _aJoint.add(_tmpV.crossVectors(_alphaEff, _r));
    // РАМПА ВРАЩЕНИЯ — только на центробежный член! Раньше wRamp умножал
    // всю силу: при перетаскивании без вращения (|ω|=0) гасла и линейная
    // инерция — «перестало работать всё, кроме груди».
    _aJoint.addScaledVector(_omegaSmV, _omegaSmV.dot(_r) * wRamp);
    _aJoint.addScaledVector(_r, -_omegaSmV.lengthSq() * wRamp);
    _gExtra.copy(_aJoint).multiplyScalar(-scale);
    if (cls === 'skirt' && wRamp < 1) {
      // v14.8.41: боковой размах подола при ЧИСТОМ драге прошивает ноги —
      // гасим горизонтальную составляющую на 35% по мере того, как вращения
      // нет (wRamp→0). Центробежный флейр вращения (wRamp→1, одобрен) и
      // вертикаль не трогаются.
      const f = 1 - 0.35 * (1 - wRamp);
      _gExtra.x *= f; _gExtra.z *= f;
    }
    if (_gExtra.length() > lim) _gExtra.setLength(lim);
    if (_gExtra.length() < 0.04) {
      // покой: вернуть родные настройки ровно (normalize шума не допускаем)
      joint.settings.gravityPower = power;
      joint.settings.gravityDir.copy(dir);
      continue;
    }
    _gTotal.copy(dir).multiplyScalar(power).add(_gExtra);
    const total = Math.max(_gTotal.length(), 0.05);
    joint.settings.gravityDir.copy(_gTotal).divideScalar(total);
    joint.settings.gravityPower = total;
  }
}

// --- живая idle-поза: дыхание, перенос веса, микродвижения ---
// Повороты костей ставятся АБСОЛЮТНО (не +=): VRMA-миксер оставляет кости в
// последнем кадре — после анимации idle за пару секунд плавно тянет их в
// нейтраль (idleCur сглаживает и этот возврат, и смену микро-поз).
const idleBones = {};
function setupIdleBones(v) {
  for (const name of ['hips', 'spine', 'chest', 'neck', 'head',
    'leftShoulder', 'rightShoulder', 'leftUpperArm', 'rightUpperArm',
    'leftLowerArm', 'rightLowerArm']) {
    idleBones[name] = v.humanoid ? v.humanoid.getNormalizedBoneNode(name) : null;
  }
}
const idleCur = { chestX: 0, chestY: 0, spineX: 0, spineY: 0, spineZ: 0,
  hipsZ: 0, hipsY: 0, hipsX: 0, headX: 0, headY: 0, headZ: 0, shiftX: 0,
  armL: 0, armR: 0, armSpread: 0, elbowL: 0, elbowR: 0, bodyYaw: 0 };
const idleMicro = { headX: 0, headY: 0, headZ: 0, chestY: 0, shiftX: 0,
  hipsZ: 0, next: 2 };
// «Событийные» жесты — раз в 9–22 с один из:
//   hips    — качает бёдрами вбок (hips.y + противовращение плеч);
//   stretch — потягивается: руки в стороны, грудь вверх, голова чуть назад;
//   gesture — жест рукой (локоть поднимает предплечье);
//   show    — показывает себя в кадр: корпус боком, бедро вперёд, взгляд
//             в камеру поверх плеча;
//   butt    — дополнительное круговое покачивание тазом;
//   jiggle  — короткое потряхивание грудью только безопасными поворотами.
const idleEvent = { type: '', until: 0, next: 2, side: 1, t0: 0 };
// Пулы жестов: в живом idle — только одобренное (ушки, виляние хвостом).
// Прочие процедурные позы убраны по решению пользователя (v14.8.15) — код
// offsetsForGesture оставлен, но в пулы и debug menu они не входят.
const STAND_GESTURES = ['ears', 'tail'];
const SIT_GESTURES = ['ears'];
// Сидячий idle: состояние (см. tickSafeProceduralIdle).
const sitState = { sitting: false, blend: 0, next: 240 + Math.random() * 300, until: 0 };
// ?event=hips|stretch|gesture|show|butt|jiggle — отладка: держать выбранный жест постоянно
// (скриншот-тесты поз; NIMA_DEBUG_QUERY в main.cjs пробрасывает параметры)
const queryParams = new URLSearchParams(location.search);
window.__NIMA_PROBE = queryParams.has('probe');
const FORCE_EVENT = queryParams.get('event');
// Мутабельный форс-жест: ?event= в запросе или action='proc_<жест>' из моста
// (песочница debug menu). 'idle' сбрасывает.
let forcedGesture = FORCE_EVENT;
const INVARIANT_MODE = queryParams.get('invariant') === '1';
const TEST_MODE = queryParams.get('testMode') === '1' || queryParams.has('testTime');
const parsedTestTime = Number(queryParams.get('testTime'));
const TEST_TIME = Number.isFinite(parsedTestTime) ? Math.max(0, parsedTestTime) : 0;
const parsedTestDuration = Number(queryParams.get('testDuration'));
const TEST_DURATION = Number.isFinite(parsedTestDuration) && parsedTestDuration > 0
  ? parsedTestDuration : 5.5;
const TEST_SIDE = queryParams.get('side') === 'left' || queryParams.get('side') === '-1' ? -1 : 1;
const INVARIANT_DURATION = 5.5;
let safeProceduralIdle = null;
let safeIdleStartedAt = 0;
let safeIdleDuration = 0;
let safeIdleWasAnimating = false;

function setupSafeProceduralIdle(model) {
  safeProceduralIdle?.restore();
  safeProceduralIdle = createProceduralIdleCandidate(model);
  safeIdleStartedAt = 0;
  safeIdleDuration = 0;
  safeIdleWasAnimating = false;
  installInvariantProbe();
}

function recaptureSafeIdleBase() {
  if (!safeProceduralIdle) return;
  safeProceduralIdle.restore();
  safeProceduralIdle.capture();
  safeIdleStartedAt = 0;
  safeIdleDuration = 0;
}

function tickSafeProceduralIdle(delta, t, micro = null) {
  if (!safeProceduralIdle) return;
  if (animating || (animMixer && animMixer._actions?.some((action) => action.isRunning()))) {
    // ВАЖНО: никакого restore() здесь! Этот вариант убивал ВСЕ VRMA: restore
    // каждый кадр возвращал нормализованные кости в захваченную стойку ПОСЛЕ
    // mixer.update — миксер «играл» (finished приходил), а модель стояла
    // («анимации не работают, только дёргает рукой»). Во время VRMA кости
    // полностью принадлежат миксеру; возврат в стойку делает stopVrma().
    safeIdleWasAnimating = true;
    return;
  }
  if (safeIdleWasAnimating) {
    safeIdleWasAnimating = false;
    recaptureSafeIdleBase();
  }

  // Сидячий режим убран (v14.8.15, «убери всё»): она больше не садится сама.
  // sitState.blend оставлен на 0 — сигнатура apply() и proc_sit (?event=sit)
  // продолжают работать для отладки.
  sitState.blend = forcedGesture === 'sit' ? 1 : 0;

  idleEvent.next -= delta;
  if (safeIdleDuration && t - safeIdleStartedAt >= safeIdleDuration) {
    safeProceduralIdle.restore();
    idleEvent.type = '';
    safeIdleDuration = 0;
  }
  if (!idleEvent.type && (forcedGesture || idleEvent.next <= 0)) {
    idleEvent.next = 6 + Math.random() * 10;
    // В сидячей позе жесты с ногами/разворотом невозможны — другой пул.
    const pool = forcedGesture ? [forcedGesture]
      : sitState.blend > 0.5 ? SIT_GESTURES : STAND_GESTURES;
    // Хвост виляет чаще ушей (взвешенный выбор), уши — чаще, чем раньше.
    idleEvent.type = forcedGesture || (pool.length > 1 && Math.random() < 0.65
      ? 'tail' : pool[Math.floor(Math.random() * pool.length)]);
    idleEvent.side = TEST_MODE ? TEST_SIDE : (Math.random() < 0.5 ? -1 : 1);
    safeIdleStartedAt = t;
    safeIdleDuration = TEST_MODE ? TEST_DURATION : (INVARIANT_MODE ? INVARIANT_DURATION
      : idleEvent.type === 'show' ? 8 + Math.random() * 2
      : idleEvent.type === 'jiggle' || idleEvent.type === 'ears' || idleEvent.type === 'tail' ? 2.4 + Math.random() * 1
      : idleEvent.type === 'fix' ? 5.5 + Math.random() * 1.5
      : idleEvent.type === 'bosom' ? 5 + Math.random() * 1.5
      : 4.5 + Math.random() * 2);
    console.log('[nima] safe idle event:', idleEvent.type, idleEvent.side > 0 ? 'R' : 'L');
  }
  if (idleEvent.type && safeIdleDuration) {
    const elapsed = TEST_MODE ? TEST_TIME : t - safeIdleStartedAt;
    safeProceduralIdle.apply(idleEvent.type, elapsed, safeIdleDuration, idleEvent.side,
      micro, sitState.blend);
    if (TEST_MODE) publishProceduralTestState(elapsed);
  } else {
    safeProceduralIdle.apply(null, 0, 0, 1, micro, sitState.blend);
  }
}

function finiteArray(values) {
  return values.every(Number.isFinite);
}

function publishProceduralTestState(elapsed) {
  let node = document.getElementById('procedural-test-state');
  if (!node) {
    node = document.createElement('output');
    node.id = 'procedural-test-state';
    node.hidden = true;
    document.body.appendChild(node);
  }
  const bones = {};
  for (const [name, bone] of safeProceduralIdle?.bones || []) {
    bones[name] = {
      quaternion: bone.quaternion.toArray(),
      position: bone.position.toArray(),
      scale: bone.scale.toArray(),
    };
  }
  const state = {
    ready: true,
    gesture: idleEvent.type,
    side: idleEvent.side,
    elapsed,
    duration: safeIdleDuration,
    phase: safeIdleDuration > 0 ? THREE.MathUtils.clamp(elapsed / safeIdleDuration, 0, 1) : 0,
    bones,
  };
  const serialized = JSON.stringify(state);
  node.textContent = serialized;
  node.dataset.ready = '1';
  document.documentElement.dataset.proceduralTestReady = '1';
  // Main-process capture harness can observe this without renderer JS polling.
  document.title = `NIMA_TEST_STATE:${btoa(unescape(encodeURIComponent(serialized)))}`;
}

function installInvariantProbe() {
  if (!INVARIANT_MODE || !vrm || !safeProceduralIdle) return;
  window.__nimaInvariantProbe = {
    sample() {
      // Keep executeJavaScript CPU-only: skinned bounds can synchronously touch GPU state.
      vrm.scene.updateMatrixWorld(true);
      let bonesFinite = true;
      for (const node of safeProceduralIdle.bones.values()) {
        bonesFinite = bonesFinite && finiteArray(node.quaternion.toArray()) &&
          finiteArray(node.position.toArray()) && finiteArray(node.scale.toArray()) &&
          finiteArray(node.matrix.elements) && finiteArray(node.matrixWorld.elements);
      }
      return { ready: true, bonesFinite };
    },
    restored() {
      safeProceduralIdle.restore();
      vrm.scene.updateMatrixWorld(true);
      return safeProceduralIdle.isRestored();
    },
  };
}

const _headPos = new THREE.Vector3();
// Непрерывная микро-жизнь (дыхание, покачивания, микро-позы головы, взгляд,
// микродвижение рук). НЕ пишет кости сама: возвращает дельты-офсеты, которые
// накладывает на базовую стойку тот же безопасный писатель, что и жесты
// (tickSafeProceduralIdle → procedural-idle-candidate.apply). Так между
// жестами тело живёт, и нет двух писателей, дерущихся за одни кости.
// (Раньше эта логика жила в tickIdlePose и писала кости напрямую, но после
// перехода на процедурные жесты её вызов потерялся — модель стояла статуей.)
function tickIdleMicro(delta, t, amp) {
  idleMicro.next -= delta;
  if (idleMicro.next <= 0) {
    idleMicro.next = 3.5 + Math.random() * 6;
    idleMicro.headX = (Math.random() - 0.5) * 0.14;
    idleMicro.headY = (Math.random() - 0.5) * 0.5;   // живые повороты головы до ~14°
    idleMicro.headZ = (Math.random() - 0.5) * 0.16;
    idleMicro.chestY = (Math.random() - 0.5) * 0.09;
    idleMicro.shiftX = (Math.random() - 0.5) * 0.026;
    idleMicro.hipsZ = (Math.random() - 0.5) * 0.05;
  }
  const breath = Math.sin(t * 1.5);
  let gazeYaw = 0, gazePitch = 0;
  if (lookAtTarget && idleBones.head) {
    idleBones.head.getWorldPosition(_headPos);
    const dx = lookAtTarget.position.x - _headPos.x;
    const dy = lookAtTarget.position.y - _headPos.y;
    const dz = lookAtTarget.position.z - _headPos.z;
    gazeYaw = Math.max(-0.5, Math.min(0.5, Math.atan2(dx, dz))) * 0.6;
    gazePitch = Math.max(-0.25, Math.min(0.25, -Math.atan2(dy, Math.hypot(dx, dz)))) * 0.5;
  }
  const targets = {
    chestX: breath * 0.055 * amp,
    chestY: Math.sin(t * 0.17) * 0.045 + idleMicro.chestY,
    spineX: breath * 0.026 * amp,
    spineY: 0,
    spineZ: Math.sin(t * 0.13) * 0.06,
    hipsX: 0,
    hipsY: Math.sin(t * 0.5) * 0.035,       // непрерывное лёгкое покачивание бёдрами
    hipsZ: Math.sin(t * 0.13 + 0.6) * 0.035 + idleMicro.hipsZ,
    headX: breath * 0.012 * amp + idleMicro.headX + gazePitch,
    headY: Math.sin(t * 0.31) * 0.08 + idleMicro.headY + gazeYaw,
    headZ: Math.sin(t * 0.23 + 1.7) * 0.05 + idleMicro.headZ,
    shiftX: Math.sin(t * 0.11) * 0.012 + idleMicro.shiftX,
    armL: Math.sin(t * 0.9) * 0.02 * amp,
    armR: Math.sin(t * 0.9 + 2.1) * 0.02 * amp,
    armSpread: 0,
    bodyYaw: 0,
    elbowL: 0,
    elbowR: 0,
  };
  const ease = Math.min(1, delta * 1.3);
  for (const k of Object.keys(idleCur)) idleCur[k] += (targets[k] - idleCur[k]) * ease;
  // Дельты поверх базовой стойки (REST_* уже захвачены в base при capture()).
  return {
    chest: [idleCur.chestX, idleCur.chestY, 0],
    spine: [idleCur.spineX, idleCur.spineY, idleCur.spineZ],
    hips: [idleCur.hipsX, idleCur.hipsY, idleCur.hipsZ],
    head: [idleCur.headX, idleCur.headY, idleCur.headZ],
    leftShoulder: [0, 0, idleCur.armL * 0.6],
    rightShoulder: [0, 0, -idleCur.armR * 0.6],
    leftUpperArm: [0, 0, idleCur.armL],
    rightUpperArm: [0, 0, -idleCur.armR],
    // Локоть: вперёд = left y<0 / right y>0 (та же ось, что REST_ELBOW_BEND).
    leftLowerArm: [0, -idleCur.elbowL - idleCur.armL * 0.7, 0],
    rightLowerArm: [0, idleCur.elbowR + idleCur.armR * 0.7, 0],
  };
}

// --- моргание: случайные интервалы, закрытие быстрее открытия, иногда двойное ---
let blinkValue = 0;
let blinkDir = 0;          // -1 закрываем, +1 открываем, 0 ждём следующего
let blinkWait = 1.5 + Math.random() * 3;
let blinkQueueDouble = false;
function tickBlink(delta) {
  if (!vrm || !vrm.expressionManager) return;
  if (blinkDir === 0) {
    blinkWait -= delta;
    if (blinkWait <= 0) blinkDir = -1;
  } else if (blinkDir < 0) {
    blinkValue += delta / 0.07;
    if (blinkValue >= 1) { blinkValue = 1; blinkDir = 1; }
  } else {
    blinkValue -= delta / 0.14;
    if (blinkValue <= 0) {
      blinkValue = 0; blinkDir = 0;
      if (blinkQueueDouble) {               // вторая фаза двойного моргания
        blinkQueueDouble = false;
        blinkWait = 0.1 + Math.random() * 0.15;
      } else if (Math.random() < 0.18) {    // начать двойное
        blinkQueueDouble = true;
        blinkWait = 0.05;
      } else {
        blinkWait = 2.2 + Math.random() * 4;
      }
    }
  }
  vrm.expressionManager.setValue('blink', blinkValue);
}

// --- живой взгляд (зрачки): в основном в камеру, иногда коротко «отвлеклась» ---
// Цель lookAt плавно дёргается по сторонам (саккады) — у VRM-моделей со
// скелетными глазами или экспрешн-аплайером зрачки едут сами.
const gazeTargetPos = new THREE.Vector3(0, 1.05, 1.0);
let gazeTimer = 0;
function tickGaze(delta) {
  gazeTimer -= delta;
  if (gazeTimer <= 0) {
    if (Math.random() < 0.55) {
      // контакт глазами: вокруг камеры с лёгким дрейфом
      gazeTargetPos.set((Math.random() - 0.5) * 0.22,
        1.02 + Math.random() * 0.12, 1.0);
      gazeTimer = 2 + Math.random() * 3.5;
    } else {
      // короткий взгляд в сторону
      const side = Math.random() < 0.5 ? -1 : 1;
      gazeTargetPos.set(side * (0.5 + Math.random() * 0.8),
        0.9 + Math.random() * 0.5, 1.0);
      gazeTimer = 0.8 + Math.random() * 1.6;
    }
  }
  if (lookAtTarget) lookAtTarget.position.lerp(gazeTargetPos, Math.min(1, delta * 9));
}

// --- эмоции (mood → выражения лица) ---
const MOOD_EXPR = {
  normal:      {},
  joy:         { happy: 0.8 },
  excitement:  { happy: 0.6, surprised: 0.5 },
  interest:    { relaxed: 0.4 },
  thinking:    { relaxed: 0.3, angry: 0.15 },
  sadness:     { sad: 0.8 },
  anger:       { angry: 0.85 },
  fear:        { surprised: 0.6, sad: 0.3 },
  shyness:     { relaxed: 0.55, happy: 0.15, sad: 0.15 },
  arousal:     { happy: 0.45, relaxed: 0.4 },
  indifference:{ relaxed: 0.2 },
  boredom:     { sad: 0.25, relaxed: 0.3 },
  sleeping:    { relaxed: 0.5 },
};
const exprCurrent = {};
const exprTarget = {};

function applyMood(mood) {
  moodTarget = mood || 'normal';
  for (const k of Object.keys(exprTarget)) exprTarget[k] = 0;
  const map = MOOD_EXPR[moodTarget] || {};
  for (const [name, v] of Object.entries(map)) exprTarget[name] = v;
}

function tickExpressions(delta) {
  if (!vrm || !vrm.expressionManager) return;
  const names = new Set([...Object.keys(exprCurrent), ...Object.keys(exprTarget)]);
  for (const name of names) {
    const target = exprTarget[name] || 0;
    const cur = exprCurrent[name] || 0;
    const next = cur + (target - cur) * Math.min(1, delta * 5);
    exprCurrent[name] = next;
    vrm.expressionManager.setValue(name, next);
  }
}

// --- жизненный цикл персонажа ---
let baseRotationY = 0;  // базовый поворот модели (rotateVRM0) — покачивание поверх него
// пользовательское вращение мышью (зажатая СКМ): Y — поворот вокруг вертикали
// (влево/вправо, во все стороны на 360°), X — наклон вперёд/назад. Поверх него
// идут покачивание/VRMA. Переживает перезапуск (localStorage).
let userRotY = 0, userRotX = 0;
// наклон ограничен строго ±90° (±π/2): вверху упор — вид ровно НАД головой,
// внизу упор — вид ровно ПОД ногами. Дальше не пускаем, иначе модель
// перевернётся «через себя».
// высота точки, вокруг которой идёт наклон — уровень взгляда камеры
// (camera.lookAt(0, 0.95, 0)), т.е. пояс/грудь. origin VRM в НОГАХ (y≈0),
// поэтому наклон вокруг него уводил голову из кадра — компенсируем в animate().
const PIVOT_Y = 0.95;
const storedRot = readStoredObject('nima_char_rot');
const storedRotY = readFiniteNumber(storedRot?.y);
const storedRotX = readFiniteNumber(storedRot?.x);
if (storedRotY !== null && storedRotX !== null
    && storedRotY >= -Math.PI && storedRotY <= Math.PI
    && storedRotX >= USER_ROT_X_MIN && storedRotX <= USER_ROT_X_MAX) {
  userRotY = storedRotY; userRotX = storedRotX;
} else {
  try { localStorage.setItem('nima_char_rot', JSON.stringify(ROT_DEFAULT)); } catch (_) {}
}
// ?tilt=0.4 — отладка: фиксированный наклон модели (как наклон мышью),
// для скрин-тестов инерции под углом.
if (queryParams.has('tilt')) {
  const t = Number(queryParams.get('tilt'));
  if (Number.isFinite(t)) userRotX = Math.max(USER_ROT_X_MIN, Math.min(USER_ROT_X_MAX, t));
}
const clock = new THREE.Clock();
const rendererDiagnostics = installRendererDiagnostics({
  THREE, getVRM: () => vrm, camera, renderer, clock,
});
function animate() {
  requestAnimationFrame(animate);
  const delta = clock.getDelta();
  const t = clock.elapsedTime;

  if (vrm) {
    // зум: камера плавно едет к цели. ?cam=<deg|front|side|back> — отладочный
    // азимут камеры вокруг модели (кадры жестов с лица и с боку).
    camDist += (camDistTarget - camDist) * Math.min(1, delta * 8);
    const camAzim = CAM_AZIM * Math.PI / 180;
    camera.position.set(Math.sin(camAzim) * camDist, 1.05, Math.cos(camAzim) * camDist);
    camera.lookAt(0, CAM_FOCUS_Y, 0);
    // дыхание + лёгкое покачивание; в речи — живее; во время VRMA заморожено.
    // Поверх всего — пользовательское вращение мышью (СКМ): Y во все стороны,
    // X — наклон. Во время VRMA миксер сам крутит rotation.y корня, поэтому
    // пользовательский поворот там не навязываем (иначе дёргает анимацию).
    const amp = speaking ? 1.6 : 1.0;

    // Миксер VRMA должен обновляться КАЖДЫЙ кадр — без него клип стоит на
    // первом кадре (модель «замирает» в позе), 'finished' не приходит,
    // animating навсегда true и idle-пластика никогда не включается.
    if (animMixer && !rendererDiagnostics?.flags.noVrma) animMixer.update(delta);
    if (!rendererDiagnostics?.flags.noIdle) {
      // Непрерывная микро-жизнь + событийные жесты — один писатель костей.
      const micro = tickIdleMicro(delta, t, amp);
      tickSafeProceduralIdle(delta, t, micro);
    } else {
      safeProceduralIdle?.restore();
    }
    if (!rendererDiagnostics?.flags.noRestore) {
      // Корневое живое движение работает всегда, в том числе во время greeting/
      // dance/jump VRMA. Кости action-анимации не трогаем: idleCur.bodyYaw
      // добавляется только в базовом idle, когда !animating.
      // Синусоидальные «дыхательные» сдвиги корня (position.x/y, rotation.y)
      // УБРАНЫ: вся модель при них летала целиком по кругу — живость теперь
      // только в костях (tickIdleMicro).
      vrm.scene.rotation.y = baseRotationY + userRotY
        + (!animating ? idleCur.bodyYaw : 0)
        + (queryParams.has('spin') ? (tickInertia._testSpin = (tickInertia._testSpin || 0) + delta * 9) : 0);
      vrm.scene.rotation.x = userRotX;
      // Наклон должен идти вокруг УРОВНЯ ВЗГЛЯДА (пояс/грудь, PIVOT_Y), а не
      // вокруг origin модели (он в НОГАХ, y≈0). Иначе при наклоне голова
      // уезжает по большой дуге вниз/из кадра и «упирается». Компенсируем:
      // поворот вокруг origin + сдвиг позиции так, чтобы точка (0,PIVOT_Y,0)
      // осталась на месте → визуально модель кренится вокруг пояса.
      const cx = Math.cos(userRotX), sx = Math.sin(userRotX);
      const pivotDY = PIVOT_Y * (1 - cx);   // вернуть Y пивота
      const pivotDZ = -PIVOT_Y * sx;        // вернуть Z пивота
      vrm.scene.position.x = charOffset.x + idleCur.shiftX
        + (queryParams.has('dragtest') ? Math.sin(clock.elapsedTime * 2.5) * 0.22 : 0);
      vrm.scene.position.y = charOffset.y + pivotDY;
      vrm.scene.position.z = pivotDZ;
    }

    // липсинк: 'aa' тянется к амплитуде звука
    if (vrm.expressionManager) {
      const cur = vrm.expressionManager.getValue('aa') || 0;
      vrm.expressionManager.setValue('aa', cur + (mouthValue - cur) * Math.min(1, delta * 18));
    }
    // моргание и взгляд живут всегда — в том числе поверх VRMA
    tickBlink(delta);
    tickGaze(delta);
    // взгляд: цель дёргается саккадами вокруг камеры (tickGaze)
    if (vrm.lookAt) vrm.lookAt.target = lookAtTarget;
  }

  tickExpressions(delta);
    // инерция волос/хвоста/одежды (до vrm.update — пружины читают настройки)
    tickInertia(delta);
    if (vrm) {
      if (rendererDiagnostics?.flags.noSpring && vrm.springBoneManager) {
      const springUpdate = vrm.springBoneManager.update;
      vrm.springBoneManager.update = () => {};
      vrm.update(delta);
      vrm.springBoneManager.update = springUpdate;
    } else {
      vrm.update(delta);
    }
  }
  updateJelly(delta);
  renderer.render(scene, camera);
  rendererDiagnostics?.sample();
  if (DEBUG_BONES && vrm && clock.elapsedTime > (animate._dbgT || 0)) {
    animate._dbgT = clock.elapsedTime + 2;
    const h = vrm.humanoid.getNormalizedBoneNode('hips');
    const hd = vrm.humanoid.getNormalizedBoneNode('head');
    // RAW-кости: меш скиннут именно к ним. Если raw разошёлся с normalized —
    // скелет-диагностика по normalized будет «нормальной» при скомканном меше
    // (баг «пол модельки пропало» после VRMA).
    const rh = vrm.humanoid.getRawBoneNode('hips');
    const rhd = vrm.humanoid.getRawBoneNode('head');
    const rlf = vrm.humanoid.getRawBoneNode('leftFoot');
    const rrf = vrm.humanoid.getRawBoneNode('rightFoot');
    if (h && hd && rh && rhd) {
      h.getWorldPosition(_dbgV1); hd.getWorldPosition(_dbgV2);
      const _r = new THREE.Vector3(), _r2 = new THREE.Vector3(),
            _fl = new THREE.Vector3(), _fr = new THREE.Vector3();
      rh.getWorldPosition(_r); rhd.getWorldPosition(_r2);
      if (rlf) rlf.getWorldPosition(_fl);
      if (rrf) rrf.getWorldPosition(_fr);
      console.log('[nima] dbg t=', clock.elapsedTime.toFixed(1),
        'animating=', animating,
        'hipsPos=', _dbgV1.x.toFixed(3), _dbgV1.y.toFixed(3), _dbgV1.z.toFixed(3),
        'headPos=', _dbgV2.x.toFixed(3), _dbgV2.y.toFixed(3), _dbgV2.z.toFixed(3),
        '| RAW hips=', _r.x.toFixed(3), _r.y.toFixed(3), _r.z.toFixed(3),
        'head=', _r2.x.toFixed(3), _r2.y.toFixed(3), _r2.z.toFixed(3),
        'footL=', _fl.x.toFixed(3), _fl.y.toFixed(3), _fl.z.toFixed(3),
        'footR=', _fr.x.toFixed(3), _fr.y.toFixed(3), _fr.z.toFixed(3),
        'scenePos=', vrm.scene.position.x.toFixed(3), vrm.scene.position.y.toFixed(3),
        'sceneScale=', vrm.scene.scale.x.toFixed(3), vrm.scene.scale.y.toFixed(3));
      // Полная цепочка RAW-костей — по ней видно, какая часть тела «сворачивается»
      const _p = new THREE.Vector3();
      const chain = ['spine', 'chest', 'upperChest', 'neck', 'leftShoulder',
        'leftUpperArm', 'leftLowerArm', 'leftHand', 'leftUpperLeg', 'leftLowerLeg',
        'leftFoot', 'leftToes'];
      const parts = [];
      for (const bn of chain) {
        const n = vrm.humanoid.getRawBoneNode(bn);
        if (!n) continue;
        n.getWorldPosition(_p);
        parts.push(bn + '=' + _p.x.toFixed(2) + ',' + _p.y.toFixed(2) + ',' + _p.z.toFixed(2));
      }
      console.log('[nima] chainL', parts.join(' '));
      // CPU-скиннинг: фактический bbox вершин. Если кости в норме, а bbox
      // сжат — ломается сам меш (скиннинг/шейдер), не скелет.
      animate._skinDbgT = animate._skinDbgT || 0;
      if (clock.elapsedTime > animate._skinDbgT) {
        animate._skinDbgT = clock.elapsedTime + 2;
        const _sv = new THREE.Vector3();
        let mi = 0;
        try {
        vrm.scene.traverse((o) => {
          if (!o.isSkinnedMesh || mi >= 4) return;
          mi++;
          const pos = o.geometry.attributes.position;
          const bb = { x0: 1e9, y0: 1e9, z0: 1e9, x1: -1e9, y1: -1e9, z1: -1e9 };
          let nanVerts = 0, ranVerts = 0;
          const step = Math.max(1, Math.floor(pos.count / 600));
          for (let i = 0; i < pos.count; i += step) {
            ranVerts++;
            _sv.fromBufferAttribute(pos, i);
            o.applyBoneTransform(i, _sv);
            o.localToWorld(_sv);
            if (!Number.isFinite(_sv.x)) { nanVerts++; continue; }
            if (_sv.x < bb.x0) bb.x0 = _sv.x; if (_sv.x > bb.x1) bb.x1 = _sv.x;
            if (_sv.y < bb.y0) bb.y0 = _sv.y; if (_sv.y > bb.y1) bb.y1 = _sv.y;
            if (_sv.z < bb.z0) bb.z0 = _sv.z; if (_sv.z > bb.z1) bb.z1 = _sv.z;
          }
          if (nanVerts > 0 || ranVerts === 0 || bb.y1 - bb.y0 < 0.05) {
            // диагностика NaN-скиннинга: матрицы меша и скелета
            const bad = (m) => m.elements.some((e) => !Number.isFinite(e));
            const bm = o.bindMatrix, bmi = o.bindMatrixInverse;
            const mw = o.matrixWorld;
            console.log('[nima] skinNaN', o.name || mi,
              'verts=', pos.count, 'ran=', ranVerts, 'nan=', nanVerts,
              'bindMatrix bad=', bad(bm),
              'bindMatrixInverse bad=', bad(bmi),
              'mesh.matrixWorld bad=', bad(mw),
              'bindMode=', o.bindMode,
              'bones=', o.skeleton ? o.skeleton.bones.length : -1,
              'boneInv0 bad=', o.skeleton && o.skeleton.boneInverses[0]
                ? bad(o.skeleton.boneInverses[0]) : 'n/a',
              'boneM0 bad=', o.skeleton && o.skeleton.bones[0]
                ? bad(o.skeleton.bones[0].matrixWorld) : 'n/a',
              'parentChain=', (() => {
                let n = o, out = [];
                while (n && out.length < 8) {
                  out.push((n.type || '?') + ':' + (n.name || '?') +
                    (bad(n.matrixWorld) ? '!NaN' : ''));
                  n = n.parent;
                }
                return out.join(' < ');
              })());
          } else {
            console.log('[nima] skinBBox', o.name || mi,
              'x', bb.x0.toFixed(2), '..', bb.x1.toFixed(2),
              'y', bb.y0.toFixed(2), '..', bb.y1.toFixed(2),
              'z', bb.z0.toFixed(2), '..', bb.z1.toFixed(2));
          }
        });
        } catch (err) {
          console.log('[nima] skinERR', err && err.message);
        }
      }
    }
  }
  if (!animate._logged && clock.elapsedTime > 1) {
    animate._logged = true;
    console.log('[nima] frame: calls=', renderer.info.render.calls,
      'canvas=', renderer.domElement.width, 'x', renderer.domElement.height,
      'vrm=', !!vrm, 'opaque=', OPAQUE_DEBUG);
  }
}

// цель взгляда: живёт в сцене всегда, позицию дёргает tickGaze (саккады)
let lookAtTarget = new THREE.Object3D();
lookAtTarget.position.set(0, 1.05, 1.0);
scene.add(lookAtTarget);

// --- точки управления ---
function makeDragHandler(el, onMove, onDblClick) {
  let dragging = false, lastX = 0, lastY = 0;
  el.addEventListener('mousedown', (e) => {
    if (locked) return;
    e.preventDefault();
    dragging = true; lastX = e.screenX; lastY = e.screenY;
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    onMove(e.screenX - lastX, e.screenY - lastY);
    lastX = e.screenX; lastY = e.screenY;
  });
  window.addEventListener('mouseup', () => { dragging = false; });
  if (onDblClick) el.addEventListener('dblclick', (e) => { e.preventDefault(); onDblClick(); });
}

// центральная точка: перетаскивание окна + блокировка двойным щелчком
makeDragHandler(dragDot, (dx, dy) => window.nima.moveBy(dx, dy), () => {
  locked = !locked;
  document.body.classList.toggle('locked', locked);
  window.nima.setLock(locked);
  flashHint(locked ? 'Окно заблокировано' : 'Окно разблокировано');
});

// правая точка: ресайз. Протокол АБСОЛЮТНЫЙ: в mousedown запоминаем стартовый
// размер окна и позицию курсора, дальше каждая move шлёт целевой размер
// (старт + 2×дельта курсора). Дельты событие-к-событию нельзя — setBounds
// применяется асинхронно, и окно «съезжает» посреди перетаскивания.
// Привязку делает main: по горизонтали растёт от центра (точка следует за
// курсором), по вертикали — вниз от неподвижного верхнего края (иначе точка
// уезжает от курсора навстречу перетаскиванию).
{
  let resizing = false, startX = 0, startY = 0, startW = 0, startH = 0;
  resizeDot.addEventListener('mousedown', (e) => {
    if (locked) return;
    e.preventDefault();
    resizing = true;
    startX = e.screenX; startY = e.screenY;
    startW = window.innerWidth; startH = window.innerHeight;  // frameless: это и есть размер окна
  });
  window.addEventListener('mousemove', (e) => {
    if (!resizing) return;
    const w = startW + 2 * (e.screenX - startX);
    const h = startH + 2 * (e.screenY - startY);
    window.nima.resizeTo(w, h);
  });
  window.addEventListener('mouseup', () => { resizing = false; });
}

// зум колёсиком мыши: приближение/отдаление камеры (при блокировке — отключён)
let camDist = CAM_DIST_OVERRIDE ?? CAM_DEFAULT,
  camDistTarget = CAM_DIST_OVERRIDE ?? CAM_DEFAULT;
try {
  const storedCamDist = readFiniteNumber(localStorage.getItem('nima_cam_dist'));
  if (storedCamDist !== null && storedCamDist >= CAM_MIN && storedCamDist <= CAM_MAX) {
    camDist = camDistTarget = storedCamDist;
  } else {
    localStorage.setItem('nima_cam_dist', String(CAM_DEFAULT));
  }
} catch (_) {}
renderer.domElement.addEventListener('wheel', (e) => {
  if (locked) return;
  e.preventDefault();
  camDistTarget = Math.max(CAM_MIN, Math.min(CAM_MAX,
    camDistTarget + Math.sign(e.deltaY) * 0.15));
  localStorage.setItem('nima_cam_dist', String(camDistTarget));
}, { passive: false });

// перетаскивание самой Нимфы по полю окна — ТОЛЬКО левой кнопкой (e.button===0),
// чтобы средняя кнопка была свободна под вращение (см. ниже).
{
  let dragging = false, lastX = 0, lastY = 0;
  renderer.domElement.addEventListener('mousedown', (e) => {
    if (locked || !vrm || e.button !== 0) return;
    dragging = true; lastX = e.clientX; lastY = e.clientY;
  });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dx = (e.clientX - lastX) / window.innerHeight * 2.2;
    const dy = -(e.clientY - lastY) / window.innerHeight * 2.2;
    lastX = e.clientX; lastY = e.clientY;
    charOffset.x = Math.max(OFFSET_X_MIN, Math.min(OFFSET_X_MAX, charOffset.x + dx));
    charOffset.y = Math.max(OFFSET_Y_MIN, Math.min(OFFSET_Y_MAX, charOffset.y + dy));
    localStorage.setItem('nima_char_offset', JSON.stringify(charOffset));
  });
  window.addEventListener('mouseup', () => { dragging = false; });
}

// вращение Нимфы зажатой СРЕДНЕЙ кнопкой мыши (СКМ, e.button===1):
//   • по горизонтали — поворот вокруг вертикали (userRotY), во все стороны;
//   • по вертикали   — наклон вперёд/назад (userRotX, ограничен ±0.8 рад).
// Двойной клик СКМ — сброс вращения в исходное. Значение сохраняется
// (localStorage → nima_char_rot) и переживает перезапуск окна.
{
  let rotating = false, lastX = 0, lastY = 0;
  const ROT_SPEED = 0.011;   // радиан на пиксель курсора
  const TWO_PI = Math.PI * 2;
  renderer.domElement.addEventListener('mousedown', (e) => {
    if (locked || !vrm || e.button !== 1) return;
    e.preventDefault();       // средняя кнопка иначе включает автоскролл
    rotating = true; lastX = e.screenX; lastY = e.screenY;
  });
  window.addEventListener('mousemove', (e) => {
    if (!rotating) return;
    userRotY += (e.screenX - lastX) * ROT_SPEED;
    userRotX += (e.screenY - lastY) * ROT_SPEED;
    // Y — полный оборот (нормализуем в -π..π чтобы число не росло бесконечно);
    // X — наклон в пределах, чтобы модель не «легла» и не ушла за камеру.
    userRotY = ((userRotY + Math.PI) % TWO_PI + TWO_PI) % TWO_PI - Math.PI;
    userRotX = Math.max(USER_ROT_X_MIN, Math.min(USER_ROT_X_MAX, userRotX));
    lastX = e.screenX; lastY = e.screenY;
    localStorage.setItem('nima_char_rot', JSON.stringify({ y: userRotY, x: userRotX }));
  });
  window.addEventListener('mouseup', (e) => { if (e.button === 1) rotating = false; });
  // двойной клик средней кнопкой — сброс поворота
  renderer.domElement.addEventListener('auxclick', (e) => {
    if (e.button !== 1 || locked) return;
    const now = Date.now();
    if (now - (renderer.domElement._lastAux || 0) < 350) {
      userRotY = 0; userRotX = 0;
      localStorage.setItem('nima_char_rot', JSON.stringify({ y: 0, x: 0 }));
      flashHint('Поворот сброшен');
    }
    renderer.domElement._lastAux = now;
  });
}

// --- resize окна ---
function onResize() {
  renderer.setSize(window.innerWidth, window.innerHeight);
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', onResize);

// --- мост с main-процессом ---
// Метка сборки физики: видно в title окна и в консоли DevTools. Если вживую
// поведение не поменялось — первым делом сверить, что окно перезапущено и
// здесь стоит текущая версия.
        const NIMA_BUILD = 'физика v14.8.43';
document.title = 'Neyronya — ' + NIMA_BUILD;
console.log('[nima] build:', NIMA_BUILD);

window.nima.onInit(() => {
  // последняя одежда переживает перезапуск (дублируется питон-мостом через
  // cache/last_outfit.txt — окно может жить и без рантайма)
  let saved = null;
  try { saved = localStorage.getItem('nima_last_outfit'); } catch (_) {}
  // ?outfit=Nima_drees.vrm — принудительная модель (скриншот-тесты).
  const outfitParam = queryParams.get('outfit');
  loadOutfit(outfitParam || saved || 'Nima_standart.vrm');
});

// --- субтитры (v14.6): караоке — появление слева-направо, пауза, стирание
// слева-направо. Обводка = цвет говорящего (красный/синий/случайный из
// pipeline._subtitle_style), свечение чёрное. Приходит через bridge
// (avatar_state.json → main → state.subtitle {text, color, name, seq}).
let lastSubSeq = null;
let subTimers = [];

function showSubtitle(sub) {
  const box = document.getElementById('subtitle');
  if (!box) return;
  subTimers.forEach(clearTimeout);
  subTimers = [];
  const text = (sub && sub.text) ? String(sub.text) : '';
  if (!text) { box.classList.remove('sub-visible'); box.style.display = 'none'; return; }

  box.style.setProperty('--sub-color', sub.color || '#ffffff');
  if (sub.name) { box.textContent = sub.name + ': ' + text; }
  else { box.textContent = text; }
  const bright = document.createElement('span');
  bright.className = 'sub-bright';
  bright.textContent = box.textContent;
  box.appendChild(bright);
  box.style.display = 'block';
  // плавное появление всей строки (opacity-переход в CSS); класс снимается на
  // reflow-кадре, чтобы transition сработал даже когда строка сменилась
  box.classList.remove('sub-visible');
  void box.offsetWidth;
  box.classList.add('sub-visible');

  // Караоке-заливка идёт СИНХРОННО с голосом (v14.7.2): субтитр приходит ровно
  // в момент старта озвучки чанка (tts on_sentence), поэтому скорость заливки
  // подгоняем под темп речи (~55 мс/символ), а не под скорость чтения. hold —
  // короткий запас, чтобы залитый текст постоял, пока голос договаривает хвост.
  const reveal = Math.max(650, Math.round(text.length * 55));
  const hold = 350 + text.length * 18;

  // фаза 1 — заливка слева-направо (градиент «белый|прозрачный», позиция 100→0)
  bright.style.transition = 'none';
  bright.style.backgroundImage = 'linear-gradient(90deg, #fff 50%, transparent 50%)';
  bright.style.backgroundPosition = '100% 0';
  void bright.offsetWidth;                       // фиксируем стартовое состояние
  bright.style.transition = `background-position ${reveal}ms linear`;
  bright.style.backgroundPosition = '0% 0';

  // фаза 2 — стирание слева-направо (градиент «прозрачный|белый», позиция 100→0)
  subTimers.push(setTimeout(() => {
    bright.style.transition = 'none';
    bright.style.backgroundImage = 'linear-gradient(90deg, transparent 50%, #fff 50%)';
    bright.style.backgroundPosition = '100% 0';  // визуально всё ещё залито
    void bright.offsetWidth;
    bright.style.transition = `background-position ${reveal}ms linear`;
    bright.style.backgroundPosition = '0% 0';
    // плавное исчезание всей строки к концу стирания + скрытие после fade
    subTimers.push(setTimeout(() => { box.classList.remove('sub-visible'); },
                              Math.max(0, reveal - 280)));
    subTimers.push(setTimeout(() => { box.style.display = 'none'; }, reveal + 120));
  }, reveal + hold));
}

let lastStateOutfit = null;       // last seen current_outfit в state-потоке
let pendingOutfitSwitch = null;   // наряд, отложенный до конца VRMA
window.nima.onState((state) => {
  if (!state) return;
  if (state.subtitle && state.subtitle.seq !== lastSubSeq) {
    lastSubSeq = state.subtitle.seq;
    showSubtitle(state.subtitle);
  }
  // Наряд: меняем ТОЛЬКО если значение в state реально сменилось с прошлого
  // сообщения, и НЕ во время проигрывания VRMA — reload модели убивает миксер
  // (анимация «пропадала», модель застывала в стойке). Откладываем до финиша.
  if (state.current_outfit && state.current_outfit !== lastStateOutfit) {
    lastStateOutfit = state.current_outfit;
    if (animating) pendingOutfitSwitch = state.current_outfit;
    else loadOutfit(state.current_outfit);
  }
  if (state.mood) applyMood(state.mood);
  if (state.mouth !== undefined) mouthValue = state.mouth;
  if (state.speaking !== undefined) speaking = state.speaking;
  // действия: семантические — через карту, vrma_* — напрямую по имени файла,
  // idle закрывает текущую one-shot анимацию. Повтор той же команды (seq
  // изменился) перезапускает анимацию — иначе клик по той же строке песочницы
  // во время проигрывания выглядит как «не работает».
  const seq = state.seq !== undefined ? state.seq : null;
  const retrigger = seq !== null && seq !== lastActionSeq;
  if (state.action !== undefined &&
      (retrigger || state.action !== currentActionName)) {
    lastActionSeq = seq;
    currentActionName = state.action;
    const file = resolveVrmaFile(state.action);
    if (state.action.startsWith('proc_')) {
      // Процедурный жест/поза из песочницы debug menu.
      forcedGesture = state.action.slice(5);
      stopVrma();
    } else if (file) playVrma(file);
    else if (state.action === 'idle') { stopVrma(); if (forcedGesture && !FORCE_EVENT) forcedGesture = null; }
  }
});

// любая непойманная ошибка рендерера — в видимый блок (и в renderer_error.log)
window.addEventListener('error', (e) => showError('JS: ' + e.message));
window.addEventListener('unhandledrejection', (e) => showError('Promise: ' + e.reason));

onResize();
animate();
