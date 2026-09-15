#!/usr/bin/env python3
"""
Голосовой ввод и команды для opencode / любого приложения.
Использует faster-whisper + xdotool + xclip.

Режимы:
  --daemon   держать модель в памяти, ждать сигнал на FIFO /tmp/voice-type-trigger
  --trigger  мгновенно «разбудить» демон (пишет байт в FIFO) — привязывается к клавише
  (без флага) разовое распознавание по-старому, удобно для отладки с --debug
"""
import argparse
import errno
import logging
import os
import re
import select
import shutil
import subprocess
import sys
import time


FIFO = "/tmp/voice-type-trigger"
LOG = "/tmp/voice-type.log"
PID_FILE = "/tmp/voice-type.pid"
SAMPLE_RATE = 16000


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
    import numpy as np
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))


def trim_silence(audio, threshold, sr=SAMPLE_RATE,
                 window=0.02, pad=0.12):
    """Обрезает тишину на краях записи (окна по 20 мс)."""
    import numpy as np
    win = max(1, int(sr * window))
    n = len(audio)
    if n < win:
        return audio
    rms_win = np.array([rms(audio[i:i + win]) for i in range(0, n - win + 1, win)])
    idx = np.where(rms_win >= threshold)[0]
    if idx.size == 0:
        return audio
    first = max(0, int(idx[0] * win - sr * pad))
    last = min(n, int((idx[-1] + 1) * win + sr * pad))
    return audio[first:last]


def press_keys(keys):
    subprocess.run(["xdotool", "key", "--clearmodifiers", keys], check=True)


def paste_text(text, paste_keys):
    subprocess.run(["xclip", "-selection", "clipboard"],
                   input=text.encode("utf-8"), check=True)
    time.sleep(0.15)
    press_keys(paste_keys)


# ═══════════════════════════════════════════════════════
#  Запись с микрофона до тишины
# ═══════════════════════════════════════════════════════
def record_until_silence(threshold=0.010, silence_after=1.2,
                         max_seconds=20.0, no_sound_timeout=8.0,
                         start_delay=0.25, debug=False):
    import numpy as np
    import sounddevice as sd
    import queue
    q = queue.Queue()
    frames = []

    def callback(indata, frames_count, time_info, status):
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
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


# ═══════════════════════════════════════════════════════
#  Распознавание
# ═══════════════════════════════════════════════════════
INITIAL_PROMPT_RU = (
    "Привет! Это образец русской речи для распознавания."
    "Сохрани этот документ, запусти программу и вставь текст."
)


def transcribe(model, audio, language, beam_size, vad, log):
    try:
        vad_params = None
        if vad:
            vad_params = {"speech_pad_ms": 200, "min_silence_duration_ms": 500}
        segments, info = model.transcribe(
            audio,
            language=language,
            beam_size=beam_size,
            initial_prompt=INITIAL_PROMPT_RU,
            vad_filter=vad,
            vad_parameters=vad_params,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if log:
            log.info("Результат (%.1f сек аудио): %r",
                     float(len(audio)) / SAMPLE_RATE, text)
        return text
    except Exception as exc:
        if log:
            log.exception("Ошибка распознавания")
        return None


# ═══════════════════════════════════════════════════════
#  FIFO (канал «демон ↔ триггер»)
# ═══════════════════════════════════════════════════════
def ensure_fifo():
    if not os.path.exists(FIFO):
        os.mkfifo(FIFO)
    return FIFO


def fifo_trigger():
    """Триггер: мгновенно пишет байт в FIFO, чтобы разбудить демон."""
    fd = -1
    try:
        # O_NONBLOCK: если демон (читатель) не запущен, open сразу вернёт ошибку,
        # иначе — заблокируется навсегда на пустом FIFO.
        fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        print(f"❌ Демон не запущен. Запусти: {sys.argv[0]} --daemon",
              file=sys.stderr)
        return 1
    except OSError as exc:
        if exc.errno in (errno.ENXIO, errno.ENODEV) or "no reader" in str(exc):
            print(f"❌ Демон не запущен. Запусти: {sys.argv[0]} --daemon",
                  file=sys.stderr)
            return 1
        print(f"❌ Не удалось разбудить демон: {exc}", file=sys.stderr)
        return 1
    try:
        os.write(fd, b"x")
        return 0
    except OSError as exc:
        print(f"❌ Не удалось разбудить демон: {exc}", file=sys.stderr)
        return 1
    finally:
        os.close(fd)


class Daemon:
    def __init__(self, args, log):
        self.args = args
        self.log = log
        self.model = None

    def load_model(self):
        from faster_whisper import WhisperModel
        t0 = time.time()
        self.log.info("Загрузка модели '%s' (device=%s, compute=%s)...",
                      self.args.model, self.args.device, self.args.compute_type)
        self.model = WhisperModel(self.args.model, device=self.args.device,
                                  compute_type=self.args.compute_type)
        self.log.info("✅ Модель загружена за %.1f сек.", time.time() - t0)

    def handle_trigger(self):
        self.log.info("⚡ Запрос на запись")
        audio = record_until_silence(
            threshold=self.args.threshold,
            silence_after=self.args.silence,
            max_seconds=self.args.max_seconds,
            no_sound_timeout=self.args.no_sound_timeout,
            start_delay=self.args.start_delay,
            debug=False,
        )
        if audio is None or audio.size < int(SAMPLE_RATE * 0.3):
            self.log.warning("⚠️ Слишком короткая запись или тишина.")
            return

        audio = trim_silence(audio, self.args.threshold)
        self.log.info("⏳ Распознавание...")
        text = transcribe(self.model, audio, self.args.language,
                          self.args.beam, self.args.vad, self.log)
        if not text:
            self.log.warning("⚠️ Текст не распознан.")
            return

        cleaned = clean(text)
        if cleaned in COMMANDS:
            keys = COMMANDS[cleaned]
            self.log.info("⚡ Команда: %s", keys)
            press_keys(keys)
        else:
            self.log.info("✅ Текст вставлен: %r", text)
            paste_text(text, self.args.paste_keys)

    def write_pid(self):
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

    @staticmethod
    def pid_running():
        if not os.path.exists(PID_FILE):
            return False
        try:
            pid = int(open(PID_FILE).read().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            return False

    @staticmethod
    def remove_pid():
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass

    def run(self):
        if self.pid_running():
            self.log.error("Демон уже запущен (см. %s)", PID_FILE)
            return 1
        self.write_pid()
        try:
            self.load_model()
            ensure_fifo()
            # O_RDWR: открытие не блокируется, чтение ждёт триггер
            fd = os.open(FIFO, os.O_RDWR)
            self.log.info("🟢 Демон готов. Жду сигнал (%s).", FIFO)
            while True:
                try:
                    data = os.read(fd, 16)
                except InterruptedError:
                    continue
                if not data:
                    # FIFO опустел — переоткрыть канал
                    os.close(fd)
                    fd = os.open(FIFO, os.O_RDWR)
                    continue
                self._drain(fd)
                self.handle_trigger()
        except KeyboardInterrupt:
            self.log.info("🛑 Демон остановлен.")
            return 0
        except Exception:
            self.log.exception("Сбой демона")
            return 1
        finally:
            self.remove_pid()

    @staticmethod
    def _drain(fd):
        """Сброс повторных сигналов во время записи («занято»)."""
        try:
            while True:
                r, _, _ = select.select([fd], [], [], 0)
                if not r:
                    break
                os.read(fd, 16)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════
def build_parser():
    p = argparse.ArgumentParser(
        description="Голосовой ввод для opencode и любых приложений.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true",
                      help="Держать модель в памяти и ждать сигнал (запускать при входе)")
    mode.add_argument("--trigger", action="store_true",
                      help="Разбудить демон (мгновенно; привязать к горячей клавише)")

    p.add_argument("--model", default="medium",
                   help="Модель: tiny, base, small, medium (по умолчанию: medium)")
    p.add_argument("--device", default="cpu",
                   help="cpu или cuda (по умолчанию: cpu)")
    p.add_argument("--compute-type", default="int8",
                   help="Тип вычислений (по умолчанию: int8)")
    p.add_argument("--language", default="ru")
    p.add_argument("--beam", type=int, default=5,
                   help="Beam size (по умолчанию: 5; точнее, чем 1)")
    p.add_argument("--vad", action="store_true", default=True,
                   help="Фильтр VAD: убирает тишину/шум (по умолчанию: вкл)")
    p.add_argument("--no-vad", dest="vad", action="store_false")
    p.add_argument("--threshold", type=float, default=0.010,
                   help="Порог громкости (по умолчанию: 0.010)")
    p.add_argument("--silence", type=float, default=1.2,
                   help="Секунды тишины для завершения фразы")
    p.add_argument("--max", dest="max_seconds", type=float, default=20.0)
    p.add_argument("--no-sound-timeout", type=float, default=8.0)
    p.add_argument("--start-delay", type=float, default=0.25)
    p.add_argument("--paste-keys", default="ctrl+v",
                   help="Клавиши вставки (для терминала: ctrl+shift+v)")
    p.add_argument("--debug", action="store_true")
    return p


def main():
    args = build_parser().parse_args()

    if args.trigger:
        sys.exit(fifo_trigger())

    if not args.daemon:
        # Разовый режим (старое поведение) — удобно для --debug / настройки порога
        for tool in ("xdotool", "xclip"):
            if shutil.which(tool) is None:
                print(f"❌ Не найден: {tool}\n   sudo apt install {tool}")
                sys.exit(1)

        if args.debug:
            print(f"⏳ Загрузка модели '{args.model}'...")
            print("   (в демон-режиме модель грузится один раз при входе)")

        from faster_whisper import WhisperModel
        model = WhisperModel(args.model, device=args.device,
                             compute_type=args.compute_type)

        if args.debug:
            print("✅ Модель загружена.\n")

        audio = record_until_silence(
            threshold=args.threshold, silence_after=args.silence,
            max_seconds=args.max_seconds, no_sound_timeout=args.no_sound_timeout,
            start_delay=args.start_delay, debug=args.debug)
        if audio is None or audio.size < int(SAMPLE_RATE * 0.3):
            if args.debug:
                print("⚠️  Слишком короткая запись или тишина.")
            return

        audio = trim_silence(audio, args.threshold)
        if args.debug:
            print("⏳ Распознавание...")
        text = transcribe(model, audio, args.language, args.beam, args.vad, None)
        if not text:
            if args.debug:
                print("⚠️  Текст не распознан.")
            return

        print(f"📝 Распознано: {text}")
        cleaned = clean(text)
        if cleaned in COMMANDS:
            press_keys(COMMANDS[cleaned])
            print(f"⚡ Команда: {COMMANDS[cleaned]}")
        else:
            paste_text(text, args.paste_keys)
            print("✅ Текст вставлен.")
        return

    # Демон
    logging.basicConfig(
        filename=LOG, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Не засорять лог HTTP-запросами huggingface_hub
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    log = logging.getLogger("voice-type")
    log.info("=== Демон запущен (pid=%s) ===", os.getpid())
    daemon = Daemon(args, log)
    sys.exit(daemon.run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Остановлено.")
        sys.exit(0)