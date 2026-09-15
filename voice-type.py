#!/usr/bin/env python3
"""
Голосовой ввод и команды для opencode / любого приложения.
Использует faster-whisper + xdotool + xclip.
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
import queue

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel


# ═══════════════════════════════════════════════════════
#  Голосовые команды: фраза → горячие клавиши
# ═══════════════════════════════════════════════════════
COMMANDS = {
    "сохрани":       "ctrl+s",
    "сохранить":     "ctrl+s",
    "запусти":       "F5",
    "запустить":     "F5",
    "старт":         "F5",
    "отмени":        "ctrl+z",
    "отмена":        "ctrl+z",
    "верни":         "ctrl+z",
    "копируй":       "ctrl+c",
    "скопируй":      "ctrl+c",
    "копировать":    "ctrl+c",
    "вырежи":        "ctrl+x",
    "вырезать":      "ctrl+x",
    "вставь":        "ctrl+v",
    "вставить":      "ctrl+v",
    "найди":         "ctrl+f",
    "поиск":         "ctrl+f",
    "найти":         "ctrl+f",
    "выдели все":    "ctrl+a",
    "выделить все":  "ctrl+a",
    "выдели всё":    "ctrl+a",
    "выделить всё":  "ctrl+a",
}


def clean(text):
    text = text.lower()
    text = re.sub(r"[^\w\s]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def rms(block):
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))


def press_keys(keys):
    subprocess.run(["xdotool", "key", "--clearmodifiers", keys], check=True)


def paste_text(text, paste_keys):
    subprocess.run(["xclip", "-selection", "clipboard"],
                   input=text.encode("utf-8"), check=True)
    time.sleep(0.15)
    press_keys(paste_keys)


def record_until_silence(threshold=0.010, silence_after=1.2,
                         max_seconds=20.0, no_sound_timeout=8.0,
                         start_delay=0.25, debug=False):
    q = queue.Queue()
    frames = []

    def callback(indata, frames_count, time_info, status):
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    with sd.InputStream(samplerate=16000, channels=1,
                        dtype="float32", callback=callback):
        if start_delay > 0:
            time.sleep(start_delay)
            while not q.empty():
                q.get_nowait()

        start = time.time()
        last_sound = start
        got_sound = False

        if debug:
            print("🎤 Говорите...")

        while True:
            now = time.time()
            try:
                block = q.get(timeout=0.1)
            except queue.Empty:
                if now - start > max_seconds:
                    break
                if got_sound and now - last_sound > silence_after:
                    break
                if not got_sound and now - start > no_sound_timeout:
                    break
                continue

            frames.append(block)
            level = rms(block)

            if debug:
                bar = "█" * int(level * 300)
                print(f"  level={level:.4f} {bar}")

            if level >= threshold:
                got_sound = True
                last_sound = time.time()

            now = time.time()
            if now - start > max_seconds:
                break
            if got_sound and now - last_sound > silence_after:
                break

    if not frames:
        return None
    return np.concatenate(frames, axis=0).astype(np.float32).flatten()


def main():
    p = argparse.ArgumentParser(
        description="Голосовой ввод для opencode и любых приложений.")
    p.add_argument("--model", default="small",
                   help="Модель: tiny, base, small, medium (по умолчанию: small)")
    p.add_argument("--device", default="cpu",
                   help="cpu или cuda (по умолчанию: cpu)")
    p.add_argument("--compute-type", default="int8",
                   help="Тип вычислений (по умолчанию: int8)")
    p.add_argument("--language", default="ru")
    p.add_argument("--threshold", type=float, default=0.010,
                   help="Порог громкости (по умолчанию: 0.010)")
    p.add_argument("--silence", type=float, default=1.2,
                   help="Секунды тишины для завершения фразы")
    p.add_argument("--max", dest="max_seconds", type=float, default=20.0)
    p.add_argument("--no-sound-timeout", type=float, default=8.0)
    p.add_argument("--start-delay", type=float, default=0.25)
    p.add_argument("--paste-keys", default="ctrl+v",
                   help="Клавиши вставки (для терминала: ctrl+shift+v)")
    p.add_argument("--loop", action="store_true",
                   help="Непрерывный режим: слушать фразу за фразой")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()

    for tool in ("xdotool", "xclip"):
        if shutil.which(tool) is None:
            print(f"❌ Не найден: {tool}\n   sudo apt install {tool}")
            sys.exit(1)

    if args.debug:
        print(f"⏳ Загрузка модели '{args.model}'...")
        print("   (при первом запуске модель скачивается из интернета)")

    model = WhisperModel(args.model, device=args.device,
                         compute_type=args.compute_type)

    if args.debug:
        print("✅ Модель загружена.\n")

    while True:
        audio = record_until_silence(
            threshold=args.threshold,
            silence_after=args.silence,
            max_seconds=args.max_seconds,
            no_sound_timeout=args.no_sound_timeout,
            start_delay=args.start_delay,
            debug=args.debug,
        )

        if audio is None or audio.size < int(16000 * 0.3):
            if args.debug:
                print("⚠️  Слишком короткая запись или тишина.")
            if not args.loop:
                return
            continue

        if args.debug:
            print("⏳ Распознавание...")

        segments, _ = model.transcribe(audio, language=args.language,
                                       beam_size=1)
        text = " ".join(s.text.strip() for s in segments).strip()

        if not text:
            if args.debug:
                print("⚠️  Текст не распознан.")
            if not args.loop:
                return
            continue

        print(f"📝 Распознано: {text}")
        cleaned = clean(text)

        if cleaned in COMMANDS:
            keys = COMMANDS[cleaned]
            press_keys(keys)
            print(f"⚡ Команда: {keys}")
        else:
            paste_text(text, args.paste_keys)
            print("✅ Текст вставлен.")

        if not args.loop:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Остановлено.")
        sys.exit(0)
