# training — дообучение Нимфеи

## БЫСТРЫЙ ПУТЬ v14.4: стилевой датасет + Colab (без курации памяти)

> **v14.8.19**: датасет пересобран под подтверждённый характер — мат
> разрешён и на стриме, ЖЁСТКОЕ табу на ругательства через болезни/синдромы,
> коронная «Ну ты и далбаёб, конечно…», НОВЫЕ сцены «разгона тем» (короткий
> заход → развивает деталью/историей/встречным вопросом), анимационные
> сцены с тегом [АНИМАЦИЯ] временно удалены. После обучения персонаж
> перечитывать в промпте не нужно — он в весах (см. «Резка промпта» ниже).

1) Стилевой датасет 1000+ пар — все примеры руками/из ручных пулов, без живой памяти:
```
myenv\Scripts\python.exe training\make_style_dataset.py
   → training\dataset_style.jsonl        (1184 примера: манера, анти-ассистент,
                                          Кизилл нетранзферный, сабы/чатерсы,
                                          2-3 собеседника, присутствие, теги,
                                          разгон тем)
   → training\nima_system_prompt.txt     (сжатый system для обучения)
```
Ручные эталоны из manual_pairs.jsonl входят с весом ×5 — дописывай туда живые
примеры, перезапусти скрипт, датасет пересоберётся.

2) Обучение — готовый ноутбук Google Colab (нужен только браузер, GPU T4 бесплатная):
   **training/nima_lora_colab.ipynb** → открыть в Colab → выбрать T4 →
   пройти ячейки сверху вниз → скачать GGUF.
3) Дома: `ollama create nimfea -f Modelfile` (инструкция в конце ноутбука).
   С v14.8.19 `LLM_MODEL` по умолчанию уже `nimfea` — env не нужен;
   откат на базу: `set NIMA_LLM_MODEL=gemma3:4b`.

4) **Резка промпта после обучения**: когда новый nimfea проверен в живом
   диалоге (характер, теги, имена держатся сами), из `prompts/persona.py`
   → `PERSONA_COMPACT` можно убрать блоки, что теперь в весах: описания
   характера/создателя (оставить только протоколы тегов, правило имён,
   режим одежды). Каждый снятый блок ≈ минус prompt-eval на каждый ответ
   (главный тормоз — см. docs/LATENCY_OPTIMIZATION.md). Откат всегда:
   `NIMA_PERSONA_COMPACT=0` (полная персона) / `NIMA_LLM_MODEL=gemma3:4b`.

Куратив из живой памяти (ниже) остаётся «второй партией» датасета — смешай
dataset_v2.jsonl и dataset_style.jsonl после курации.

## Датасет v2 — С РУЧНОЙ КУРАЦИЕЙ (важно!)

Урок v14.0.1: датасет, собранный целиком из живой памяти, обучил модель бреду —
в память попадают донаты, обрывки чужой речи и её же неудачные ответы.
Теперь сборка трёхшаговая, человек в цикле:

```
1) myenv\Scripts\python.exe training\build_dataset.py
   → training\dataset_candidates.jsonl   (черновик из памяти)

2) myenv\Scripts\python.exe training\curate.py
   → бракуем тупые ответы [n], правим не в характере [e], одобряем [y]

3) training\manual_pairs.jsonl — пиши руками эталонные пары
   (как Нима ДОЛЖНА отвечать; каждая съедается с весом ×5)

4) myenv\Scripts\python.exe training\build_dataset.py --final
   → training\dataset_v2.jsonl (verdict=ok + manual×5)
```

Дубликаты и служебные реплики (initiative/threads) отбрасываются автоматически.
Правило: качество LoRA = качество курации. 200 отборных пар лучше 2000 сырых.

## LoRA-обучение (gemma-3-4b-it — v14.3: мозг сменился с qwen2.5:3b)

Почему gemma3: сообщество сходится, что на 4B Gemma заметно лучше в русском и
в живом разговорном/ролевом тоне, Qwen «safetyslopped» (зажат цензурой —
цундере из него получается ватная). Ровно те проблемы, из-за которых Нимфея
звучала тупо. База LoRA — характер, НО промпт остаётся лёгким: в system
держим только протоколы (теги, режим стрима, память), а манера речи
закрепляется файнтюном.

Подход (воспроизводимый минимум):

1. Окружение (не в myenv — там рантайм): `pip install unsloth`.
2. Обучение:
   ```python
   from unsloth import FastLanguageModel
   model, tokenizer = FastLanguageModel.from_pretrained(
       "unsloth/gemma-3-4b-it", max_seq_length=2048, load_in_4bit=True)
   model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=32,
       target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"])
   # датасет: tokenizer.apply_chat_template из dataset_v2.jsonl
   # 2-3 эпохи, lr 2e-4, batch 4+grad_accum 4 — хватает 6 ГБ VRAM
   ```
3. Экспорт GGUF: `model.save_pretrained_gguf("out", tokenizer, quantization_method="q4_k_m")`.
4. Регистрация: скопировать в `models/`, затем
   `cd models && ollama create nimfea -f Modelfile.nimfea` и
   `set NIMA_LLM_MODEL=nimfea`.

## Голос (требование 12) — v14.3: Chatterbox Multilingual

Основной движок теперь Chatterbox Multilingual v3 (Resemble AI, MIT): 0.5B —
в разы легче XTTS (освобождает VRAM для LLM), клон по ОДНОМУ референсу без
файнтюна, русский официально, интонация живее. Эмоции через exaggeration
(EMOTION_EXAGGERATION в core/config.py). Если не установлен — tts_module
автоматически откатывается на XTTS v2. Переключение: NIMA_TTS_BACKEND=xtts.

Запись 5–10 минут чистого голоса по-прежнему улучшит клон (референс чище —
клон точнее), но файнтюн голоса больше НЕ ТРЕБУЕТСЯ. GPT-SoVITS v2 — только
отдельным окружением (ронял CUDA на этой машине).

Кандидат — F5-TTS против XTTS v2 (см. `docs/SKELETON.md` §4.2). Запись
5–10 минут целевого голоса → файнтюн F5-TTS даёт стабильный клон.
GPT-SoVITS v2 — только отдельным окружением (ронял CUDA на этой машине).
