# Установка «daemon + trigger» голосового ввода

Новая архитектура: модель Whisper держится в памяти **постоянно** (демон),
клавиша F9 лишь мгновенно «будит» её (триггер). Задержка при нажатии устраняется,
а распознавание стало точнее: модель **medium**, `beam_size=5`, VAD-фильтр,
подрезка тишины, `initial_prompt` на русском.

---

## 1. Куда копировать файлы

| Файл (здесь, в `./voice-type`)        | Куда копировать                          |
|---------------------------------------|------------------------------------------|
| `voice-type.py`                       | `~/bin/voice-type.py`                    |
| `voice-type` (обёртка)                | `~/bin/voice-type` (chmod +x)            |
| `voice-daemon.desktop`                | `~/.config/autostart/voice-daemon.desktop` |

```bash
cp voice-type.py ~/bin/voice-type.py
cp voice-type     ~/bin/voice-type
chmod +x ~/bin/voice-type
mkdir -p ~/.config/autostart
cp voice-daemon.desktop ~/.config/autostart/voice-daemon.desktop
```

Старая версия скрипта будет затёрта — бэкап при желании:
`cp ~/bin/voice-type.py ~/bin/voice-type.py.bak`

> Обёртка `voice-type` теперь ищет `voice-type.py` рядом с собой
> (`$DIR`), поэтому её можно класть в любое место, не трогая содержимое.

---

## 2. Проверка вручную (до автозапуска)

В **одном** терминале запустить демон (первый раз модель `medium` скачается
из интернета, ~0.5 ГБ, потом кешируется):

```bash
~/bin/voice-type --daemon
```

В **другом** терминале — триггер:

```bash
~/bin/voice-type --trigger
```

После триггера говорите вслух — демон распознает и вставит текст
(по умолчанию `ctrl+v`; в терминале нужен `ctrl+shift+v`, см. п. 5).
Остановить демон — `Ctrl+C` в его терминале.

Если захотите перезапустить демон после правок:

```bash
pkill -f "voice-type.py --daemon"; ~/bin/voice-type --daemon
```

---

## 3. Переназначить клавишу F9 на триггер

Текущая привязка (Cinnamon, `custom1`) запускает полный процесс с загрузкой
модели. Заменить её на мгновенный триггер:

```bash
gsettings set org.cinnamon.desktop.keybindings.custom-keybindings.custom1 \
    command "/home/nimda/bin/voice-type --trigger"
gsettings set org.cinnamon.desktop.keybindings.custom-keybindings.custom1 \
    name "Голосовой ввод"
```

Изменение подхватывается сразу (или выйти из сессии и зайти заново).
Проверить:

```bash
gsettings get org.cinnamon.desktop.keybindings.custom-keybindings.custom1 command
```

---

## 4. Автозапуск демона при входе

Файл `~/.config/autostart/voice-daemon.desktop` уже скопирован (п. 1).
Он запускает `~/bin/voice-type --daemon` поверхностно при входе.
Проверить после перезахода в систему:

```bash
pgrep -af "voice-type.py --daemon"      # должен показать процесс
```

---

## 5. Флаг вставки для терминала

Если вставлять нужно не `ctrl+v`, а `ctrl+shift+v` (как было в прежней
привязке F9) — добавьте флаг в автозапуск. Отредактировать
`~/.config/autostart/voice-daemon.desktop`, строка `Exec`:

```
Exec=/home/nimda/bin/voice-type --daemon --paste-keys ctrl+shift+v
```

Или передать флаги при ручном запуске: `~/bin/voice-type --daemon --paste-keys ctrl+shift+v`.

---

## 6. Настройка порога громкости (если «срезает» начало/фразы)

Порог по умолчанию `--threshold 0.010`. Если демоны режут слова или ловят шум:

1. Замерьте уровни (быстрая модель), говорите в микрофон и наблюдайте `level=`:
   ```bash
   ~/bin/voice-type --debug --model small --beam 5
   ```
2. Поставьте `--threshold` чуть выше фонового шума (как правило 0.005–0.020),
   добавив его в `Exec` автозапуска:
   ```
   Exec=/home/nimda/bin/voice-type --daemon --threshold 0.015
   ```
3. Перезапустить демон (команда из п. 2), проверить на фразе.

---

## 7. Диагностика

- Лог демона:  `tail -f /tmp/voice-type.log`
- PID демона:  `cat /tmp/voice-type.pid`
- FIFO-канал:  `/tmp/voice-type-trigger`
- F9 молчит → `pgrep -af "voice-type.py --daemon"`; если демона нет — `~/bin/voice-type --daemon`.
- F9 «висит» → FIFO остался без читателя: `rm -f /tmp/voice-type-trigger` и запустить демон.

---

## 8. Полезные сочетания в `voice-type.py`

| Флаг                      | По умолчанию | Зачем                                |
|---------------------------|--------------|--------------------------------------|
| `--model`                 | `medium`     | Точность русского. Медленно но верно |
| `--beam`                  | `5`          | Точнее, чем 1 (было раньше)          |
| `--no-vad`                | VAD вкл      | Выключить фильтр тишины              |
| `--language`              | `ru`         | Язык распознавания                   |
| `--threshold`             | `0.010`      | Порог «звука» при записи             |
| `--silence`               | `1.2`        | Тишина = конец фразы (сек)           |
| `--paste-keys`            | `ctrl+v`     | Терминал: `ctrl+shift+v`             |

Голосовые команды (`сохрани`, `запусти`, `копируй`, …) работают как раньше —
уже в демон-режиме.